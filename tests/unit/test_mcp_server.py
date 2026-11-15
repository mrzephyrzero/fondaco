# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 the Fondaco contributors
"""MCP server: a thin proxy over the boundary's JSON API, no approve tool."""

import asyncio
import json

import httpx
import pytest

pytest.importorskip("mcp")

from mcp import Client  # noqa: E402

from api.main import create_app  # noqa: E402
from api.mcp_server import FondacoAPI, build_server, main  # noqa: E402
from boundary.approval_policy import ApprovalPolicy  # noqa: E402
from planner.client import PlannerClient  # noqa: E402
from tests.unit.test_api import CountingAdapter, _good_steps  # noqa: E402
from tests.unit.test_api_v1 import BIG_STEPS  # noqa: E402
from tests.unit.test_planner import SequencedLLM  # noqa: E402

TOOLS = {
    "fondaco_list_schemas",
    "fondaco_propose_plan",
    "fondaco_get_plan_status",
    "fondaco_execute_plan",
}


def _server(tmp_path, *llm, policy=None):
    adapter = CountingAdapter()
    planner = PlannerClient(
        base_url="https://llm.invalid/v1",
        model="mock",
        max_attempts=2,
        transport=SequencedLLM(*llm).transport(),
    )
    app = create_app(
        adapter=adapter,
        planner=planner,
        audit_path=str(tmp_path / "audit.jsonl"),
        clearance="internal",
        approval_policy=policy or ApprovalPolicy(),
    )
    http = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://fondaco")
    return build_server(FondacoAPI(http), public_url="https://fondaco.example"), adapter, http


async def _call(server, name, args=None):
    async with Client(server) as client:
        result = await client.call_tool(name, args or {})
    assert not result.is_error, result
    return json.loads(result.content[0].text)


def test_exactly_four_tools_and_no_approval(tmp_path):
    server, _, _ = _server(tmp_path)

    async def names():
        async with Client(server) as client:
            return {t.name for t in (await client.list_tools()).tools}

    assert asyncio.run(names()) == TOOLS
    assert not any("approve" in n for n in TOOLS)


def test_list_schemas(tmp_path):
    server, _, _ = _server(tmp_path)
    body = asyncio.run(_call(server, "fondaco_list_schemas"))
    assert body["clearance"] == "internal"
    assert {t["name"] for t in body["tables"]} >= {"orders"}


def test_pending_plan_gives_the_human_approval_url(tmp_path):
    server, adapter, _ = _server(tmp_path, _good_steps())
    body = asyncio.run(_call(server, "fondaco_propose_plan", {"question": "orders per region?"}))
    assert body["status"] == "pending_approval"
    assert body["approval_url"] == f"https://fondaco.example/plans/{body['plan_id']}"
    run = asyncio.run(_call(server, "fondaco_execute_plan", {"plan_id": body["plan_id"]}))
    assert run["http_status"] == 409 and run["result"] is None
    assert adapter.execute_calls == 0


def test_agent_written_plan_auto_approved_and_executed(tmp_path):
    server, adapter, _ = _server(tmp_path, policy=ApprovalPolicy(auto_approve_enabled=True))

    async def flow():
        async with Client(server) as client:
            proposed = await client.call_tool(
                "fondaco_propose_plan", {"question": "q", "steps": BIG_STEPS}
            )
            plan_id = json.loads(proposed.content[0].text)["plan_id"]
            ran = await client.call_tool("fondaco_execute_plan", {"plan_id": plan_id})
            status = await client.call_tool("fondaco_get_plan_status", {"plan_id": plan_id})
            return json.loads(ran.content[0].text), json.loads(status.content[0].text)

    ran, status = asyncio.run(flow())
    assert ran["status"] == "executed" and ran["result"]["row_count"] == 12
    assert status["status"] == "executed"
    assert adapter.execute_calls == 1


def test_invalid_agent_plan_returns_validation_codes(tmp_path):
    server, adapter, _ = _server(tmp_path)
    steps = [{"id": "s1", "type": "query", "template": "DROP TABLE orders", "params": {}}]
    body = asyncio.run(_call(server, "fondaco_propose_plan", {"question": "q", "steps": steps}))
    assert body["error"] == "invalid_plan"
    assert body["http_status"] == 422
    assert adapter.execute_calls == 0


@pytest.mark.parametrize("plan_id", ["../audit/anchor", "x", "", "a" * 36])
def test_plan_id_is_checked_before_any_request(tmp_path, plan_id):
    server, _, _ = _server(tmp_path)
    body = asyncio.run(_call(server, "fondaco_get_plan_status", {"plan_id": plan_id}))
    assert body == {"error": "invalid_plan_id"}


def test_unreachable_boundary_is_reported_not_raised():
    def refuse(request):
        raise httpx.ConnectError("refused")

    http = httpx.AsyncClient(transport=httpx.MockTransport(refuse), base_url="http://x")
    server = build_server(FondacoAPI(http))
    body = asyncio.run(_call(server, "fondaco_list_schemas"))
    assert body["error"] == "boundary_unreachable"


def test_cli_wires_the_transport(monkeypatch):
    seen = {}

    def fake_run(self, transport="stdio", **kwargs):
        seen["transport"], seen["kwargs"] = transport, kwargs

    from mcp.server.mcpserver import MCPServer

    monkeypatch.setattr(MCPServer, "run", fake_run)
    monkeypatch.setenv("FONDACO_API_TOKEN", "tok")
    assert main([]) == 0
    assert seen["transport"] == "stdio"
    assert main(["--transport", "streamable-http", "--port", "9000"]) == 0
    assert seen == {"transport": "streamable-http", "kwargs": {"host": "127.0.0.1", "port": 9000}}
