# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 the Fondaco contributors
"""FastAPI app: ask → plan → approve → execute, fully audited.

Manual approval is the point, not a limitation: no plan executes without
a human clicking approve, and approval cannot override a policy deny
(design/label-model.md §5). Every boundary crossing lands in the
append-only audit log — question, plan, validation, policy decision,
approval identity, execution digest. Row data never enters the log.

The approval UI is authenticated (api/auth.py): every UI page needs an
approver session, every state-changing UI request also needs its CSRF token,
and the approver written to the audit log is the authenticated name. The
JSON API (/api/v1) is a separate surface with its own bearer token and no
approve endpoint, so the agent's credential can never approve.
"""

from __future__ import annotations

import hmac
import json
import os
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote

import psycopg
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from api.auth import (
    CSRF_FIELD,
    CSRF_HEADER,
    UI_SESSION_COOKIE,
    ApproverAuth,
    Session,
    cross_site,
    safe_next,
    secure_cookie_from_env,
)
from boundary.approval_policy import (
    AUTO_APPROVAL_ACTOR,
    ApprovalPolicy,
    decide,
    policy_from_env,
    release_allowed,
)
from boundary.audit import (
    EVENT_APPROVAL,
    EVENT_EXECUTION_DIGEST,
    EVENT_GUARD_DECISION,
    EVENT_PLAN_GENERATED,
    EVENT_POLICY_DECISION,
    EVENT_QUESTION_RECEIVED,
    EVENT_VALIDATION_RESULT,
    AuditError,
    AuditLog,
    anchor_sink_from_env,
    audit_log_from_env,
)
from boundary.guards import (
    GUARD_K_THRESHOLD,
    GUARD_QUERY_BUDGET,
    BudgetExceeded,
    QueryBudget,
    config_from_env,
)
from boundary.policy import PolicyDecision, UnknownRelation, evaluate, step_labels
from executor.adapters.annotations import annotations_from_env
from executor.adapters.contract import Adapter, schema_labels_dict
from executor.adapters.factory import adapter_from_env, adapter_kind, check_dialect
from executor.runner import ExecutorError, RunResult, run_plan
from planner.client import (
    PlannerClient,
    PlannerError,
    assemble_plan,
    client_from_env,
    schema_payload,
)

SESSION_COOKIE = "fondaco_session"


def _scripted_questions() -> list[str]:
    from planner.demo import SCRIPTED_QUESTIONS

    return list(SCRIPTED_QUESTIONS)


_UI_DIR = Path(__file__).parent / "ui"

PENDING = "pending"
DENIED = "denied"
EXECUTED = "executed"
APPROVED = "approved"  # auto-approved via the JSON API, not yet executed
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
    # Risk-based approval (boundary/approval_policy.py): "auto" when the
    # policy approved execution, and a result held back for a human because
    # it was smaller than auto_approve_min_rows.
    approval_mode: str = "human"
    held_result: RunResult | None = None


def create_app(
    adapter: Adapter | None = None,
    planner: PlannerClient | None = None,
    audit_path: str | None = None,
    clearance: str | None = None,
    audit_log: AuditLog | None = None,
    anchor_sink=None,
    approval_policy: ApprovalPolicy | None = None,
    approver_auth: ApproverAuth | None = None,
) -> FastAPI:
    app = FastAPI(title="Fondaco", description="Data stays home. Plans cross.")
    templates = Jinja2Templates(directory=str(_UI_DIR / "templates"))
    app.mount("/static", StaticFiles(directory=str(_UI_DIR / "static")), name="static")

    clearance = clearance or os.environ.get("FONDACO_EGRESS_CLEARANCE", "internal")
    # Fail closed at startup: no HMAC key, an unverifiable log, or a log that
    # no longer contains the latest external anchor → the app does not start.
    if audit_log is not None:
        audit = audit_log
    elif audit_path is not None:
        audit = AuditLog(audit_path)
    else:
        audit = audit_log_from_env()
    if anchor_sink is None:
        anchor_sink = anchor_sink_from_env()
    approval = approval_policy if approval_policy is not None else policy_from_env()
    # Optional bearer token for /api/v1 (the MCP server sends it). It never
    # opens the approval UI, which has its own approver credentials.
    api_token = (os.environ.get("FONDACO_API_TOKEN") or "").strip()
    # Fail closed at startup: malformed approver configuration, or an API
    # token that doubles as an approver token, stops the app here.
    if approver_auth is None:
        approver_auth = ApproverAuth.from_env(api_token)
    else:
        approver_auth.check_separate_from(api_token)
    ui_auth = approver_auth
    cookie_secure = secure_cookie_from_env()

    @app.middleware("http")
    async def _security_headers(request: Request, call_next):
        response = await call_next(request)
        # The approve button must not be clickable from inside another page.
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Content-Security-Policy"] = "frame-ancestors 'none'"
        response.headers["X-Content-Type-Options"] = "nosniff"
        # Not "no-referrer": that makes browsers send `Origin: null` on our own
        # form posts. same-origin keeps paths in-site and sends nothing out.
        response.headers["Referrer-Policy"] = "same-origin"
        if not request.url.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    def ui_session(request: Request) -> Session | None:
        return ui_auth.session(request.cookies.get(UI_SESSION_COOKIE))

    def login_redirect(request: Request) -> RedirectResponse:
        target = request.url.path + (f"?{request.url.query}" if request.url.query else "")
        return RedirectResponse(f"/login?next={quote(target, safe='')}", status_code=303)

    async def ui_post_guard(request: Request) -> tuple[Session | None, Response | None]:
        """Every state-changing UI request: session, same-site, CSRF token."""
        current = ui_session(request)
        if current is None:
            return None, HTMLResponse("sign in required", status_code=401)
        if cross_site(request.headers, request.headers.get("host", "")):
            return None, HTMLResponse("cross-site request refused", status_code=403)
        supplied = request.headers.get(CSRF_HEADER)
        if supplied is None and "json" not in request.headers.get("content-type", ""):
            supplied = (await request.form()).get(CSRF_FIELD)
        if not ui_auth.csrf_ok(current, supplied if isinstance(supplied, str) else None):
            return None, HTMLResponse("missing or invalid CSRF token", status_code=403)
        return current, None

    def budget_key(current: Session) -> str:
        # In the UI the query budget follows the authenticated approver, so
        # clearing cookies or opening a new browser no longer resets it.
        return f"approver:{current.approver}"

    def ui_context(current: Session, **values) -> dict:
        return {"ui_user": current.approver, "csrf_token": current.csrf, **values}

    @app.exception_handler(AuditError)
    async def _audit_failure(request: Request, exc: AuditError):
        # Any failure to record a crossing halts the crossing. The message is
        # the module's own text (no payload content, no key material).
        return HTMLResponse(f"boundary halted: audit log unavailable ({exc})", status_code=503)

    def require_intact_audit() -> None:
        """Every boundary operation starts by re-verifying the whole chain."""
        result = audit.verify_chain()
        if not result.ok:
            raise AuditError(f"chain broken at seq {result.bad_seq}: {result.reason}")

    guard_config = config_from_env()
    budget = QueryBudget(guard_config.query_budget)
    plans: dict[str, PlanRecord] = {}

    def session_id(request: Request) -> str:
        return request.cookies.get(SESSION_COOKIE) or str(uuid.uuid4())

    # Read the reviewed annotation overlay at startup, so a broken file stops
    # the app here instead of failing the first question.
    annotations = annotations_from_env() if adapter is None else {}
    if adapter is None:
        # Fail closed at startup: unknown adapter, or a boundary dialect that
        # differs from the grammar the selected database speaks.
        check_dialect(adapter_kind())

    def get_adapter() -> Adapter:
        nonlocal adapter
        if adapter is None:
            adapter = adapter_from_env(annotations)
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

    def execute(record: PlanRecord, plan_id: str, sid: str) -> HTMLResponse | None:
        """Charge the budget, run the plan, audit the outcome.

        Shared by human and automatic approval, so both cross exactly the same
        guards. Returns a response only when the budget blocked execution;
        otherwise the record's status/result/error carry the outcome.
        """
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
                plan=record.plan,
            )
            return HTMLResponse(record.error, status_code=429)

        try:
            result = run_plan(record.plan, get_adapter(), k=guard_config.k)
        except ExecutorError as exc:
            record.status = FAILED
            record.error = f"{exc.code}: {exc.detail}"
            audit.append(
                EVENT_EXECUTION_DIGEST,
                {"plan_id": plan_id, "success": False, "error_code": exc.code},
                plan=record.plan,
            )
            return None
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
            plan=record.plan,
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
            plan=record.plan,
        )
        return None

    def set_ui_cookie(response: Response, request: Request, sid: str) -> None:
        response.set_cookie(
            UI_SESSION_COOKIE,
            sid,
            max_age=ui_auth.session_ttl_s,
            httponly=True,
            samesite="strict",
            secure=cookie_secure or request.url.scheme == "https",
            path="/",
        )

    @app.get("/login", response_class=HTMLResponse)
    def login_form(request: Request, next: str = "/"):
        return templates.TemplateResponse(
            request,
            "login.html",
            {"next": safe_next(next), "error": "", "bootstrap": ui_auth.bootstrap},
        )

    @app.post("/login")
    async def login(request: Request):
        if cross_site(request.headers, request.headers.get("host", "")):
            return HTMLResponse("cross-site request refused", status_code=403)
        form = await request.form()
        token = form.get("token")
        target = safe_next(form.get("next") if isinstance(form.get("next"), str) else None)
        approver = ui_auth.authenticate(token.strip() if isinstance(token, str) else "")
        if approver is None:
            return templates.TemplateResponse(
                request,
                "login.html",
                {"next": target, "error": "Unknown token.", "bootstrap": ui_auth.bootstrap},
                status_code=401,
            )
        # A fresh session on every login; any previous one on this browser ends.
        ui_auth.logout(request.cookies.get(UI_SESSION_COOKIE))
        response = RedirectResponse(target, status_code=303)
        set_ui_cookie(response, request, ui_auth.login(approver))
        return response

    @app.post("/logout")
    async def logout(request: Request):
        _, refused = await ui_post_guard(request)
        if refused is not None:
            return refused
        ui_auth.logout(request.cookies.get(UI_SESSION_COOKIE))
        response = RedirectResponse("/login", status_code=303)
        response.delete_cookie(UI_SESSION_COOKIE, path="/")
        return response

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
        current = ui_session(request)
        if current is None:
            return login_redirect(request)
        records = sorted(plans.items(), key=lambda kv: kv[1].created, reverse=True)
        return templates.TemplateResponse(
            request,
            "index.html",
            ui_context(
                current,
                plans=records,
                clearance=clearance,
                error=error,
                budget=budget.state(budget_key(current)),
                k=guard_config.k,
                demo_mode=demo_mode,
                scripted=_scripted_questions() if demo_mode else [],
            ),
        )

    def hold_if_small(record: PlanRecord, plan_id: str) -> None:
        """An auto-approved result below min_rows waits for a human release."""
        if record.approval_mode != "auto" or record.status != EXECUTED:
            return
        if release_allowed(approval, len(record.result.rows)):
            return
        # Small results are where inference risk lives: hold it back and hand
        # the release decision to a human.
        record.held_result, record.result = record.result, None
        record.status = PENDING
        audit.append(
            EVENT_APPROVAL,
            {
                "plan_id": plan_id,
                "decision": "hold_for_human",
                "approver": AUTO_APPROVAL_ACTOR,
                "reasons": ["below_auto_approve_min_rows"],
                "min_rows": approval.auto_approve_min_rows,
            },
            actor=AUTO_APPROVAL_ACTOR,
            plan=record.plan,
        )

    def propose(
        question: str, sid: str, steps: object = None, run_auto: bool = True
    ) -> tuple[str | None, dict | None]:
        """question (+ optional client-written steps) → plan → validate → policy → approval.

        Shared by the UI (`/ask`, which runs an auto-approved plan at once) and
        the JSON API used by the MCP server (`run_auto=False`: an auto-approved
        plan waits in `approved` until the client asks to execute it). Client
        steps are hostile input exactly like LLM output: they cross the same
        envelope assembly and validator, and the boundary assigns the plan id.
        Returns (plan_id, None) or (None, machine-readable error).
        """
        require_intact_audit()
        audit.append(EVENT_QUESTION_RECEIVED, {"question": question})
        schema = get_adapter().get_schema()
        labels_dict = schema_labels_dict(schema)

        if steps is None:
            try:
                plan, trace = get_planner().generate_plan(question, schema)
            except PlannerError as exc:
                audit.append(
                    EVENT_PLAN_GENERATED,
                    {"success": False, "error_code": exc.code, "question": question},
                )
                return None, {"error": "planner_failed", "code": exc.code}
            attempts = trace.attempts
            generated = {"prompt_version": trace.prompt_version, "attempts": len(attempts)}
        else:
            plan, attempt = assemble_plan(question, steps)
            attempts = [attempt]
            generated = {"source": "client", "attempts": 1}
        plan_id = plan["plan_id"]
        invalid = steps is not None and not attempts[0].validation.valid
        audit.append(
            EVENT_PLAN_GENERATED,
            {"success": not invalid, "plan_id": plan_id, **generated},
            plan=plan,
        )
        for i, attempt in enumerate(attempts):
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
                plan=plan,
            )
        if invalid:
            return None, {
                "error": "invalid_plan",
                "validation_errors": [
                    {"code": e.code, "path": e.path, "detail": e.detail[:200]}
                    for e in attempts[0].validation.errors
                ],
            }

        decision = evaluate(plan, labels_dict, clearance)
        audit.append(
            EVENT_POLICY_DECISION,
            {
                "plan_id": plan_id,
                "allow": decision.allow,
                "reason_code": decision.reason_code,
                "plan_label": decision.plan_label,
            },
            plan=plan,
        )
        try:
            labels = {k: v.name.lower() for k, v in step_labels(plan, labels_dict).items()}
        except (KeyError, UnknownRelation):
            labels = {}
        record = PlanRecord(
            plan=plan,
            question=question,
            status=PENDING if decision.allow else DENIED,
            decision=decision,
            labels=labels,
        )
        plans[plan_id] = record
        if decision.allow and approval.auto_approve_enabled:
            verdict = decide(approval, plan, decision, labels_dict, budget.state(sid).remaining)
            audit.append(
                EVENT_APPROVAL,
                {
                    "plan_id": plan_id,
                    "decision": "auto_approve" if verdict.auto else "needs_human",
                    "approver": AUTO_APPROVAL_ACTOR,
                    "reasons": list(verdict.reasons),
                },
                actor=AUTO_APPROVAL_ACTOR,
                plan=plan,
            )
            if verdict.auto:
                record.approval_mode = "auto"
                record.approver = AUTO_APPROVAL_ACTOR
                if run_auto:
                    if execute(record, plan_id, sid) is None:
                        hold_if_small(record, plan_id)
                else:
                    record.status = APPROVED
        return plan_id, None

    @app.post("/ask")
    async def ask(request: Request):
        current, refused = await ui_post_guard(request)
        if refused is not None:
            return refused
        if "json" in request.headers.get("content-type", ""):
            question = str((await request.json()).get("question", "")).strip()
        else:
            question = str((await request.form()).get("question", "")).strip()
        if not question:
            return RedirectResponse("/?error=empty+question", status_code=303)
        plan_id, error = propose(question, budget_key(current))
        if error is not None:
            return RedirectResponse(f"/?error=planner+failed:+{error['code']}", status_code=303)
        return RedirectResponse(f"/plans/{plan_id}", status_code=303)

    # ── JSON API (/api/v1) — the surface the MCP server proxies ──────────
    # Deliberately no approve or reject endpoint: a client (an agent) can
    # propose, inspect and execute what is already approved; only a human
    # approves, in the UI. A policy deny is final here as everywhere.

    def api_authorized(request: Request) -> bool:
        if not api_token:
            return True
        supplied = request.headers.get("authorization", "")
        return hmac.compare_digest(supplied.encode(), f"Bearer {api_token}".encode())

    def api_response(body: dict, status: int, sid: str) -> JSONResponse:
        response = JSONResponse(body, status_code=status)
        response.set_cookie(SESSION_COOKIE, sid, httponly=True, samesite="lax")
        return response

    def plan_view(plan_id: str, record: PlanRecord) -> dict:
        status = {PENDING: "pending_approval", APPROVED: "auto_approved"}.get(
            record.status, record.status
        )
        view = {
            "plan_id": plan_id,
            "question": record.question,
            "status": status,
            "approval_mode": record.approval_mode,
            "policy": {
                "allow": record.decision.allow,
                "reason_code": record.decision.reason_code,
                "plan_label": record.decision.plan_label,
            },
            "step_labels": record.labels,
            "steps": record.plan["steps"],
            "approval_path": f"/plans/{plan_id}",
            "held_for_human": record.held_result is not None,
            "error": record.error,
            "result": None,
        }
        if record.status == EXECUTED and record.result is not None:
            result = record.result
            view["result"] = json.loads(
                json.dumps(
                    {
                        "title": result.title,
                        "columns": list(result.columns),
                        "rows": [list(r) for r in result.rows],
                        "label": result.label,
                        "row_count": len(result.rows),
                        "suppressed_groups": result.suppressed_groups,
                        "digest": result.digest,
                    },
                    default=str,
                )
            )
        return view

    @app.get("/api/v1/schema")
    def api_schema(request: Request):
        sid = session_id(request)
        if not api_authorized(request):
            return api_response({"error": "unauthorized"}, 401, sid)
        # Exactly what the planner sees: names, types, labels, comments, and
        # enum metadata only where the effective label allows it.
        payload = json.loads(schema_payload(get_adapter().get_schema()))
        return api_response({**payload, "clearance": clearance}, 200, sid)

    @app.post("/api/v1/plans")
    async def api_propose(request: Request):
        sid = session_id(request)
        if not api_authorized(request):
            return api_response({"error": "unauthorized"}, 401, sid)
        raw = await request.body()
        if len(raw) > 256 * 1024:
            return api_response({"error": "body larger than 256 KiB"}, 413, sid)
        try:
            body = json.loads(raw)
        except ValueError:
            return api_response({"error": "body must be JSON"}, 400, sid)
        if not isinstance(body, dict):
            return api_response({"error": "body must be a JSON object"}, 400, sid)
        question = body.get("question")
        if not isinstance(question, str) or not question.strip():
            return api_response({"error": "question is required"}, 400, sid)
        if len(question) > 2000:
            return api_response({"error": "question longer than 2000 characters"}, 400, sid)
        plan_id, error = propose(question.strip(), sid, steps=body.get("steps"), run_auto=False)
        if error is not None:
            return api_response(error, 422, sid)
        return api_response(plan_view(plan_id, plans[plan_id]), 201, sid)

    @app.get("/api/v1/plans/{plan_id}")
    def api_plan_status(request: Request, plan_id: str):
        sid = session_id(request)
        if not api_authorized(request):
            return api_response({"error": "unauthorized"}, 401, sid)
        record = plans.get(plan_id)
        if record is None:
            return api_response({"error": "plan not found"}, 404, sid)
        return api_response(plan_view(plan_id, record), 200, sid)

    @app.post("/api/v1/plans/{plan_id}/execute")
    def api_execute(request: Request, plan_id: str):
        sid = session_id(request)
        if not api_authorized(request):
            return api_response({"error": "unauthorized"}, 401, sid)
        record = plans.get(plan_id)
        if record is None:
            return api_response({"error": "plan not found"}, 404, sid)
        if record.status == APPROVED:
            require_intact_audit()
            blocked = execute(record, plan_id, sid)
            if blocked is not None:
                return api_response(plan_view(plan_id, record), 429, sid)
            hold_if_small(record, plan_id)
        elif record.status == PENDING:
            # Waiting for a human in the UI (or holding a small result).
            return api_response(plan_view(plan_id, record), 409, sid)
        elif record.status != EXECUTED:
            return api_response(plan_view(plan_id, record), 409, sid)
        status = 200 if record.status == EXECUTED else 409
        return api_response(plan_view(plan_id, record), status, sid)

    @app.get("/plans/{plan_id}", response_class=HTMLResponse)
    def plan_detail(request: Request, plan_id: str):
        current = ui_session(request)
        if current is None:
            return login_redirect(request)
        record = plans.get(plan_id)
        if record is None:
            return HTMLResponse("plan not found", status_code=404)
        return templates.TemplateResponse(
            request,
            "plan.html",
            ui_context(
                current,
                r=record,
                plan_id=plan_id,
                clearance=clearance,
                k=guard_config.k,
                demo_mode=demo_mode,
            ),
        )

    @app.post("/plans/{plan_id}/approve")
    async def approve(request: Request, plan_id: str):
        current, refused = await ui_post_guard(request)
        if refused is not None:
            return refused
        record = plans.get(plan_id)
        if record is None:
            return HTMLResponse("plan not found", status_code=404)
        if record.status != PENDING:
            # Denied, rejected, executed, failed: nothing to approve — and a
            # policy deny can never be overridden by a human (label-model §5).
            return HTMLResponse(f"plan is {record.status}, not approvable", status_code=409)
        # The authenticated approver, never a self-declared form field.
        approver = current.approver

        require_intact_audit()
        audit.append(
            EVENT_APPROVAL,
            {"plan_id": plan_id, "decision": "approve", "approver": approver},
            actor=approver,
            plan=record.plan,
        )
        record.approver = approver

        if record.held_result is not None:
            # Already executed under auto-approval; the human releases it.
            record.result, record.held_result = record.held_result, None
            record.status = EXECUTED
            return RedirectResponse(f"/plans/{plan_id}", status_code=303)

        blocked = execute(record, plan_id, budget_key(current))
        if blocked is not None:
            return blocked
        return RedirectResponse(f"/plans/{plan_id}", status_code=303)

    @app.post("/plans/{plan_id}/reject")
    async def reject(request: Request, plan_id: str):
        current, refused = await ui_post_guard(request)
        if refused is not None:
            return refused
        record = plans.get(plan_id)
        if record is None:
            return HTMLResponse("plan not found", status_code=404)
        if record.status != PENDING:
            return HTMLResponse(f"plan is {record.status}, not rejectable", status_code=409)
        approver = current.approver
        require_intact_audit()
        record.status = REJECTED
        record.approver = approver
        audit.append(
            EVENT_APPROVAL,
            {"plan_id": plan_id, "decision": "reject", "approver": approver},
            actor=approver,
            plan=record.plan,
        )
        return RedirectResponse(f"/plans/{plan_id}", status_code=303)

    @app.get("/audit", response_class=HTMLResponse)
    def audit_view(request: Request, event: str = "", plan: str = ""):
        current = ui_session(request)
        if current is None:
            return login_redirect(request)
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
            ui_context(
                current,
                entries=entries,
                verification=verification,
                event_filter=event,
                plan_filter=plan,
                demo_mode=demo_mode,
                key_id=audit.key_id,
            ),
        )

    @app.get("/audit/head")
    def audit_head():
        """The current verified head — what an external monitor should record."""
        verification = audit.verify_chain()
        head = audit.head() if verification.ok else None
        return JSONResponse(
            {
                "verified": verification.ok,
                "entries": verification.entries,
                "reason": verification.reason,
                "head": head.to_dict() if head else None,
            },
            status_code=200 if verification.ok else 503,
        )

    @app.post("/audit/anchor")
    async def audit_anchor(request: Request):
        """Export the verified head to the configured sink (call it from a scheduler).

        A scheduler authenticates with the API bearer token; a signed-in
        approver may also trigger it from a browser (session + CSRF).
        """
        if not (api_token and api_authorized(request)):
            _, refused = await ui_post_guard(request)
            if refused is not None:
                return refused
        if anchor_sink is None:
            return JSONResponse({"error": "no anchor sink configured"}, status_code=409)
        try:
            head = audit.anchor(anchor_sink)
        except AuditError as exc:
            status = 503 if audit.halted else 502
            return JSONResponse({"error": str(exc)}, status_code=status)
        return JSONResponse({"anchored": head.to_dict() if head else None})

    return app


app = create_app()
