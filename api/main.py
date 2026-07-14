# License: placeholder — headers finalized in Phase 8 (see DECISIONS.md).
"""FastAPI app: ask → plan → approve → execute, fully audited.

Manual approval is the point, not a limitation: no plan executes without
a human clicking approve, and approval cannot override a policy deny
(design/label-model.md §5). Every boundary crossing lands in the
append-only audit log — question, plan, validation, policy decision,
approval identity, execution digest. Row data never enters the log.

V1 has no authentication: the approver identity is a self-declared form
field (documented limitation; see DECISIONS.md and Phase 7/8).
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import psycopg
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from boundary.audit import (
    EVENT_APPROVAL,
    EVENT_EXECUTION_DIGEST,
    EVENT_GUARD_DECISION,
    EVENT_PLAN_GENERATED,
    EVENT_POLICY_DECISION,
    EVENT_QUESTION_RECEIVED,
    EVENT_VALIDATION_RESULT,
    AuditLog,
)
from boundary.guards import (
    GUARD_K_THRESHOLD,
    GUARD_QUERY_BUDGET,
    BudgetExceeded,
    QueryBudget,
    config_from_env,
)
from boundary.policy import PolicyDecision, evaluate, step_labels
from executor.adapters.contract import Adapter, schema_labels_dict
from executor.runner import ExecutorError, RunResult, run_plan
from planner.client import PlannerClient, PlannerError, client_from_env

SESSION_COOKIE = "fondaco_session"


def _scripted_questions() -> list[str]:
    from planner.demo import SCRIPTED_QUESTIONS

    return list(SCRIPTED_QUESTIONS)


_UI_DIR = Path(__file__).parent / "ui"

PENDING = "pending"
DENIED = "denied"
EXECUTED = "executed"
REJECTED = "rejected"
FAILED = "failed"


@dataclass
class PlanRecord:
    plan: dict
    question: str
    status: str
    decision: PolicyDecision
    labels: dict[str, str]
    created: str = field(default_factory=lambda: datetime.now(UTC).isoformat(timespec="seconds"))
    result: RunResult | None = None
    error: str | None = None
    approver: str | None = None


def create_app(
    adapter: Adapter | None = None,
    planner: PlannerClient | None = None,
    audit_path: str | None = None,
    clearance: str | None = None,
) -> FastAPI:
    app = FastAPI(title="Fondaco", description="Data stays home. Plans cross.")
    templates = Jinja2Templates(directory=str(_UI_DIR / "templates"))
    app.mount("/static", StaticFiles(directory=str(_UI_DIR / "static")), name="static")

    clearance = clearance or os.environ.get("FONDACO_EGRESS_CLEARANCE", "internal")
    audit = AuditLog(audit_path or os.environ.get("FONDACO_AUDIT_LOG", "./audit.jsonl"))
    guard_config = config_from_env()
    budget = QueryBudget(guard_config.query_budget)
    plans: dict[str, PlanRecord] = {}

    def session_id(request: Request) -> str:
        return request.cookies.get(SESSION_COOKIE) or str(uuid.uuid4())

    def get_adapter() -> Adapter:
        nonlocal adapter
        if adapter is None:
            from executor.adapters.postgres import PostgresAdapter

            adapter = PostgresAdapter(os.environ["DATABASE_URL"])
        return adapter

    demo_mode = False
    if planner is None:
        if os.environ.get("FONDACO_PLANNER", "demo").strip().lower() == "demo":
            from planner.demo import DemoPlanner

            planner = DemoPlanner()
            demo_mode = True
        else:
            planner = client_from_env()
    else:
        from planner.demo import DemoPlanner

        demo_mode = isinstance(planner, DemoPlanner)

    def get_planner():
        return planner

    @app.get("/health")
    def health() -> dict[str, str]:
        dsn = os.environ.get("DATABASE_URL", "")
        db_status = "unconfigured"
        if dsn:
            try:
                with psycopg.connect(dsn, connect_timeout=3) as conn:
                    conn.execute("SELECT 1")
                db_status = "ok"
            except psycopg.Error:
                db_status = "unreachable"
        return {"status": "ok", "db": db_status}

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request, error: str = ""):
        records = sorted(plans.items(), key=lambda kv: kv[1].created, reverse=True)
        sid = session_id(request)
        response = templates.TemplateResponse(
            request,
            "index.html",
            {
                "plans": records,
                "clearance": clearance,
                "error": error,
                "budget": budget.state(sid),
                "k": guard_config.k,
                "demo_mode": demo_mode,
                "scripted": _scripted_questions() if demo_mode else [],
            },
        )
        response.set_cookie(SESSION_COOKIE, sid, httponly=True, samesite="lax")
        return response

    @app.post("/ask")
    async def ask(request: Request):
        if "json" in request.headers.get("content-type", ""):
            question = str((await request.json()).get("question", "")).strip()
        else:
            question = str((await request.form()).get("question", "")).strip()
        if not question:
            return RedirectResponse("/?error=empty+question", status_code=303)

        audit.append(EVENT_QUESTION_RECEIVED, {"question": question})
        working_adapter = get_adapter()
        schema = working_adapter.get_schema()
        labels_dict = schema_labels_dict(schema)

        try:
            plan, trace = get_planner().generate_plan(question, schema)
        except PlannerError as exc:
            audit.append(
                EVENT_PLAN_GENERATED,
                {"success": False, "error_code": exc.code, "question": question},
            )
            return RedirectResponse(f"/?error=planner+failed:+{exc.code}", status_code=303)

        plan_id = plan["plan_id"]
        audit.append(
            EVENT_PLAN_GENERATED,
            {
                "success": True,
                "plan_id": plan_id,
                "prompt_version": trace.prompt_version,
                "attempts": len(trace.attempts),
            },
        )
        for i, attempt in enumerate(trace.attempts):
            audit.append(
                EVENT_VALIDATION_RESULT,
                {
                    "plan_id": plan_id,
                    "attempt": i + 1,
                    "parse_error": attempt.parse_error,
                    "valid": bool(attempt.validation and attempt.validation.valid),
                    "error_codes": sorted(
                        {e.code for e in attempt.validation.errors} if attempt.validation else []
                    ),
                },
            )

        decision = evaluate(plan, labels_dict, clearance)
        audit.append(
            EVENT_POLICY_DECISION,
            {
                "plan_id": plan_id,
                "allow": decision.allow,
                "reason_code": decision.reason_code,
                "plan_label": decision.plan_label,
            },
        )
        try:
            labels = {k: v.name.lower() for k, v in step_labels(plan, labels_dict).items()}
        except KeyError:
            labels = {}
        plans[plan_id] = PlanRecord(
            plan=plan,
            question=question,
            status=PENDING if decision.allow else DENIED,
            decision=decision,
            labels=labels,
        )
        response = RedirectResponse(f"/plans/{plan_id}", status_code=303)
        response.set_cookie(SESSION_COOKIE, session_id(request), httponly=True, samesite="lax")
        return response

    @app.get("/plans/{plan_id}", response_class=HTMLResponse)
    def plan_detail(request: Request, plan_id: str):
        record = plans.get(plan_id)
        if record is None:
            return HTMLResponse("plan not found", status_code=404)
        return templates.TemplateResponse(
            request,
            "plan.html",
            {
                "r": record,
                "plan_id": plan_id,
                "clearance": clearance,
                "k": guard_config.k,
                "demo_mode": demo_mode,
            },
        )

    @app.post("/plans/{plan_id}/approve")
    async def approve(request: Request, plan_id: str):
        record = plans.get(plan_id)
        if record is None:
            return HTMLResponse("plan not found", status_code=404)
        if record.status != PENDING:
            # Denied, rejected, executed, failed: nothing to approve — and a
            # policy deny can never be overridden by a human (label-model §5).
            return HTMLResponse(f"plan is {record.status}, not approvable", status_code=409)
        form = await request.form()
        approver = str(form.get("approver", "")).strip() or "anonymous"
        sid = session_id(request)

        audit.append(
            EVENT_APPROVAL, {"plan_id": plan_id, "decision": "approve", "approver": approver}
        )
        record.approver = approver

        # Query budget is charged BEFORE execution: an over-budget plan must
        # never touch the database (fail closed, all-or-nothing).
        n_query_steps = sum(1 for s in record.plan["steps"] if s["type"] == "query")
        try:
            budget_state = budget.consume(sid, n_query_steps)
        except BudgetExceeded as exc:
            record.status = FAILED
            record.error = f"query budget exhausted ({exc.used}/{exc.limit})"
            audit.append(
                EVENT_GUARD_DECISION,
                {
                    "plan_id": plan_id,
                    "guard": GUARD_QUERY_BUDGET,
                    "triggered": True,
                    "used": exc.used,
                    "limit": exc.limit,
                },
            )
            response = HTMLResponse(record.error, status_code=429)
            response.set_cookie(SESSION_COOKIE, sid, httponly=True, samesite="lax")
            return response

        try:
            result = run_plan(record.plan, get_adapter(), k=guard_config.k)
        except ExecutorError as exc:
            record.status = FAILED
            record.error = f"{exc.code}: {exc.detail}"
            audit.append(
                EVENT_EXECUTION_DIGEST,
                {"plan_id": plan_id, "success": False, "error_code": exc.code},
            )
            return RedirectResponse(f"/plans/{plan_id}", status_code=303)
        record.status = EXECUTED
        record.result = result
        audit.append(
            EVENT_GUARD_DECISION,
            {
                "plan_id": plan_id,
                "guard": GUARD_K_THRESHOLD,
                "triggered": result.suppressed_groups > 0,
                "k": guard_config.k,
                "suppressed_groups": result.suppressed_groups,
                "budget_used": budget_state.used,
                "budget_limit": budget_state.limit,
            },
        )
        audit.append(
            EVENT_EXECUTION_DIGEST,
            {
                "plan_id": plan_id,
                "success": True,
                "digest": result.digest,
                "label": result.label,
                "row_count": len(result.rows),
            },
        )
        response = RedirectResponse(f"/plans/{plan_id}", status_code=303)
        response.set_cookie(SESSION_COOKIE, sid, httponly=True, samesite="lax")
        return response

    @app.post("/plans/{plan_id}/reject")
    async def reject(request: Request, plan_id: str):
        record = plans.get(plan_id)
        if record is None:
            return HTMLResponse("plan not found", status_code=404)
        if record.status != PENDING:
            return HTMLResponse(f"plan is {record.status}, not rejectable", status_code=409)
        form = await request.form()
        approver = str(form.get("approver", "")).strip() or "anonymous"
        record.status = REJECTED
        record.approver = approver
        audit.append(
            EVENT_APPROVAL, {"plan_id": plan_id, "decision": "reject", "approver": approver}
        )
        return RedirectResponse(f"/plans/{plan_id}", status_code=303)

    @app.get("/audit", response_class=HTMLResponse)
    def audit_view(request: Request, event: str = "", plan: str = ""):
        verification = audit.verify()
        entries = audit.entries()
        if event:
            entries = [e for e in entries if e["event"] == event]
        if plan:
            entries = [e for e in entries if e["payload"].get("plan_id") == plan]
        entries.reverse()
        return templates.TemplateResponse(
            request,
            "audit.html",
            {
                "entries": entries,
                "verification": verification,
                "event_filter": event,
                "plan_filter": plan,
                "demo_mode": demo_mode,
            },
        )

    return app


app = create_app()
