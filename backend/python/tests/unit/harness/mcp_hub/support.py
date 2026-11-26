"""Shared set-up for the P2.8 hub tests: an in-process MCP server (the mcp SDK's, in memory) that counts sessions and
lists, can rewrite a tool's description or add a tool (the rug pull), and announces ``tools/list_changed``; and
agent contexts whose streamable-HTTP instance reaches it."""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING, Any

from mcp.client._memory import InMemoryTransport
from mcp.server.mcpserver import Context, MCPServer

from app.agent_loop_lib.tools.registry import ToolRegistry
from app.agents.agent_loop.mcp_session import MCPSessionManager
from app.agents.agent_loop.mcp_tool_loader import MCPToolProvider
from app.agents.mcp import client as client_module
from app.agents.mcp.client import _Transport
from app.harness.mcp_hub.hub import MCPHub, ProcessMCPHub
from app.harness.mcp_hub.settings import HubSettings
from harness_core.testing.clock import ManualClock
from tests.unit.agents.adapter.conftest import make_context

if TYPE_CHECKING:
    from collections.abc import Iterator

    import pytest

    from app.agents.agent_loop.context import AgentContext
    from tests.unit.harness.credentials.support import DictConfig

INSTANCE_ID = "mcp-fake"
URL = "http://93.184.216.34/mcp"


class FakeServer:
    """``search``, ``fetch``, ``dotted.tool``, ``announce``; ``extra`` hidden until ``reveal``."""

    def __init__(self) -> None:
        self.initializes = 0
        self.lists = 0
        self.calls: list[str] = []
        self.mcp = MCPServer("fake", middleware=[self._count])

        def search(q: str) -> str:
            self.calls.append("search")
            return f"found {q}"

        def fetch(issue: str) -> str:
            self.calls.append("fetch")
            return f"issue {issue}"

        def dotted() -> str:
            self.calls.append("dotted.tool")
            return "dotted"

        def extra() -> str:
            return "extra"

        async def announce(ctx: Context) -> str:
            # 2026-07-28 listeners hear the bus; an older session hears the notification sent on it.
            await ctx.notify_tools_changed()
            await ctx.session.send_tool_list_changed()
            return "announced"

        # name -> (function, description); a rewrite or a reveal re-registers the tool.
        self._tools: dict[str, tuple[Any, str]] = {
            "search": (search, "Search issues"),
            "fetch": (fetch, "Fetch an issue"),
            "dotted.tool": (dotted, "A dotted name"),
            "extra": (extra, "Hidden until revealed"),
            "announce": (announce, "Tells the client the tool list changed"),
        }
        for name in self._tools:
            if name != "extra":
                self._add(name)

    async def _count(self, ctx: Any, call_next: Any) -> Any:  # noqa: ANN401 - the SDK's middleware shape
        # A session opens with the version probe (2026-07-28) or with initialize (older servers), never both.
        if ctx.method in ("initialize", "server/discover"):
            self.initializes += 1
        elif ctx.method == "tools/list":
            self.lists += 1
        return await call_next(ctx)

    def _add(self, name: str, description: str | None = None) -> None:
        fn, default = self._tools[name]
        self.mcp.add_tool(fn, name=name, description=description or default)

    def rewrite(self, tool: str, description: str) -> None:
        self.mcp.remove_tool(tool)
        self._add(tool, description)

    def reveal(self, tool: str) -> None:
        self._add(tool)

    def hide(self, tool: str) -> None:
        self.mcp.remove_tool(tool)

    def transport(self) -> _Transport:
        return _Transport(InMemoryTransport(self.mcp))


def http_instance(**changes: Any) -> dict[str, Any]:  # noqa: ANN401
    instance: dict[str, Any] = {
        "_id": INSTANCE_ID,
        "orgId": "org-1",
        "createdBy": "admin-1",
        "name": "Fake",
        "typeId": "fake",
        "transport": "streamable_http",
        "authMode": "api_token",
        "url": URL,
        "createdAt": 0,
        "updatedAt": 0,
    }
    instance.update(changes)
    return instance


def serve(monkeypatch: pytest.MonkeyPatch, servers: dict[str, FakeServer]) -> list[dict[str, Any]]:
    """Every client for a URL in ``servers`` reaches that server in memory; the transports built are recorded."""
    built: list[dict[str, Any]] = []
    real = client_module.build_transport

    def build(config: Any, *args: Any, **kwargs: Any) -> Any:  # noqa: ANN401 - build_transport's own signature
        built.append({"url": config.url, "headers": dict(kwargs.get("headers") or {})})
        server = servers.get(config.url or config.id)
        return server.transport() if server is not None else real(config, *args, **kwargs)

    monkeypatch.setattr(client_module, "build_transport", build)
    return built


def hub_on(monkeypatch: pytest.MonkeyPatch, clock: ManualClock | None = None, *, by_default: bool = False) -> MCPHub:
    """``MCP_HUB`` on, with a fresh process hub on ``clock``; ``by_default``: unset, on because the harness is switched
    on (ADR-OPS-06: set explicitly, whatever the switch's default)."""
    if by_default:
        monkeypatch.delenv("MCP_HUB", raising=False)
        monkeypatch.setenv("AGENT_HARNESS_WORKER_ENABLED", "true")
    else:
        monkeypatch.setenv("MCP_HUB", "true")
    hub = MCPHub(HubSettings(session_idle_ttl_s=300, session_max_age_s=3600, schema_ttl_s=600), clock=clock or ManualClock())
    process = ProcessMCPHub()
    process._hub = hub
    monkeypatch.setattr("app.harness.mcp_hub.hub.process_mcp_hub", process)
    monkeypatch.setattr("app.harness.mcp_hub.discovery.process_mcp_hub", process)
    monkeypatch.setattr("app.agents.agent_loop.mcp_session.process_mcp_hub", process)
    return hub


def context(
    config: DictConfig,
    *,
    owner: str = "user-1",
    token: str = "tok-user-1",
    instance: dict[str, Any] | None = None,
    tools: list[dict[str, Any]] | None = None,
) -> AgentContext:
    instance = instance or http_instance()
    return make_context(
        config_service=config,
        user_id=owner,
        mcp_servers=[
            {"instanceId": instance["_id"], "name": "Fake", "displayName": "Fake", "typeId": "fake", "tools": tools}
        ],
        mcp_server_configs={
            instance["_id"]: {"instance": instance, "auth": {"credentials": {"apiToken": token}}, "ownerId": owner}
        },
        run_id="run-1",
    )


async def load(ctx: AgentContext) -> ToolRegistry:
    registry = ToolRegistry()
    await MCPToolProvider().load_into(registry, ctx)
    return registry


@contextlib.contextmanager
def no_requests_leave() -> Iterator[list[str]]:
    """Fails a test whose code sends an HTTP request through httpx's or httpx2's (mcp 2.x) default transport."""
    import httpx
    import httpx2

    sent: list[str] = []
    transports = (httpx.AsyncHTTPTransport, httpx2.AsyncHTTPTransport)
    originals = [t.handle_async_request for t in transports]

    async def refuse(self: Any, request: Any) -> Any:  # noqa: ANN401 - either library's request
        sent.append(str(request.url))
        raise AssertionError(f"a request left the process: {request.url}")

    for transport in transports:
        transport.handle_async_request = refuse  # type: ignore[method-assign]
    try:
        yield sent
    finally:
        for transport, original in zip(transports, originals, strict=True):
            transport.handle_async_request = original  # type: ignore[method-assign]


async def teardown(*contexts: AgentContext) -> None:
    for ctx in contexts:
        await MCPSessionManager(ctx).aclose_all()
