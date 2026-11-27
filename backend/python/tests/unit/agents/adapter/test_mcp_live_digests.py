"""MCPLIVE: ``MCPToolProvider.live_digests``, what the assistant's run definition pins while the MCP hub is off.

Every attached server is discovered at once through the legacy loop's own discovery; each tool is named by its
server-side name with its descriptor digest (the one hub approvals use); a server that fails or does not answer in
time is left out and the others are kept.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import pytest

from app.agents.agent_loop import mcp_tool_loader as mcp_tool_loader_module
from app.agents.agent_loop.mcp_session import MCPSessionManager
from app.agents.agent_loop.mcp_tool_loader import MCPToolProvider
from app.agents.mcp.models import MCPToolInfo
from app.harness.mcp_hub.manifests import descriptor_of
from harness_core.testing.virtual_time import run_on_virtual_time
from tests.unit.agents.adapter.conftest import make_context

if TYPE_CHECKING:
    from app.agents.agent_loop.context import AgentContext


def _info(server: str, name: str = "lookup_note") -> MCPToolInfo:
    return MCPToolInfo(
        name=name,
        namespaced_name=f"mcp_{server}_{name}",
        description=f"{name} on {server}",
        input_schema={"type": "object", "properties": {"q": {"type": "string"}}},
    )


def _context(*servers: str) -> AgentContext:
    return make_context(
        mcp_servers=[{"instanceId": s, "name": s, "displayName": s, "typeId": s} for s in servers],
        mcp_server_configs={
            s: {
                "instance": {
                    "_id": s, "orgId": "org-1", "createdBy": "user-1", "name": s, "typeId": s,
                    "transport": "sse", "authMode": "none", "createdAt": 0, "updatedAt": 0,
                },
                "auth": {},
                "ownerId": "user-1",
            }
            for s in servers
        },
    )


def _discovery(behaviour: dict[str, str]) -> Any:  # noqa: ANN401
    """The turn's tool load (`MCPSessionManager.tools`), scripted per server."""

    async def tools(self: MCPSessionManager, server: Any, namespace: str) -> tuple[list[MCPToolInfo], None]:  # noqa: ANN401
        what = behaviour[server.name]
        if what == "fail":
            raise RuntimeError("connection refused")
        if what == "hang":
            await asyncio.sleep(30)
        return [_info(server.name)], None

    return tools


@pytest.fixture(autouse=True)
def _hub_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mcp_tool_loader_module, "hub_enabled", lambda: False)


async def test_every_server_names_its_live_tools_with_their_descriptor_digests(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(MCPSessionManager, "tools", _discovery({"notes": "ok", "wiki": "ok"}))

    live = await MCPToolProvider().live_digests(_context("notes", "wiki"))

    assert live == {
        "notes": {"lookup_note": descriptor_of(_info("notes")).digest()},
        "wiki": {"lookup_note": descriptor_of(_info("wiki")).digest()},
    }


async def test_a_failing_server_is_left_out_and_the_others_kept(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(MCPSessionManager, "tools", _discovery({"notes": "ok", "wiki": "fail"}))
    context = _context("notes", "wiki")

    live = await MCPToolProvider().live_digests(context)

    assert set(live) == {"notes"}
    assert [f["instanceId"] for f in context.mcp_tool_load_failures] == ["wiki"]


def test_discovery_is_bounded_by_the_timeout_and_keeps_what_answered(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(MCPSessionManager, "tools", _discovery({"notes": "ok", "wiki": "hang"}))

    async def main() -> tuple[dict[str, Any], float]:
        loop = asyncio.get_running_loop()
        started = loop.time()
        live = await MCPToolProvider().live_digests(_context("notes", "wiki"), timeout_seconds=0.2)
        return live, loop.time() - started

    live, elapsed = run_on_virtual_time(main)

    assert elapsed <= 0.2 + 1e-6
    assert set(live) == {"notes"}
