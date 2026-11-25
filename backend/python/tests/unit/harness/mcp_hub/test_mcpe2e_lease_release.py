"""MCPE2E-F1: a request's pooled MCP sessions go back to the hub when the request ends.

With ``MCP_HUB`` on, a call to an HTTP server (or a server in a runner) leases the process's pooled session. Both
exits of a request (the durable attempt's ``close_environment`` and today's loop's stream teardown) asked only
whether the request had opened sessions of its own, which a pooled call never does, so the lease was never
released: the pool never saw the session idle, never closed it, and a STDIO server's runner sandbox was held for
the life of the process.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from app.agents.agent_loop.mcp_session import close_request_sessions
from app.harness.assembly.teardown import close_environment
from harness_core.logs import HarnessLog
from harness_core.testing.clock import ManualClock
from tests.unit.harness.credentials.support import DictConfig
from tests.unit.harness.mcp_hub.support import (
    URL,
    FakeServer,
    context,
    hub_on,
    load,
    serve,
)

if TYPE_CHECKING:
    from app.agents.agent_loop.context import AgentContext
    from app.harness.mcp_hub.hub import MCPHub

IDLE_TTL_S = 300


@pytest.fixture
def clock() -> ManualClock:
    return ManualClock()


@pytest.fixture
def hub(monkeypatch: pytest.MonkeyPatch, clock: ManualClock) -> MCPHub:
    return hub_on(monkeypatch, clock)


async def _called(monkeypatch: pytest.MonkeyPatch) -> AgentContext:
    """A request that listed the server's tools and called one: it holds one lease of the pooled session."""
    server, config = FakeServer(), DictConfig()
    _ = serve(monkeypatch, {URL: server})
    ctx = context(config)
    registry = await load(ctx)
    result = await registry.resolve_by_name("mcp_fake_search").execute(q="q")
    assert result.success, result
    return ctx


def _leases(hub: MCPHub) -> list[int]:
    return [session.leases for session in hub.pool.sessions()]


async def test_f1_a_durable_attempts_teardown_releases_its_pooled_session(
    monkeypatch: pytest.MonkeyPatch, hub: MCPHub, clock: ManualClock
) -> None:
    ctx = await _called(monkeypatch)
    assert _leases(hub) == [1]
    await close_environment(ctx, HarnessLog.named("test"))
    assert _leases(hub) == [0]
    # Released, the session is idle: the pool closes it after its idle time, as P2.8 says.
    clock.advance(IDLE_TTL_S + 1)
    assert await hub.sweep() == 1
    assert hub.pool.sessions() == ()


async def test_f1_todays_loops_teardown_releases_its_pooled_session(
    monkeypatch: pytest.MonkeyPatch, hub: MCPHub
) -> None:
    ctx = await _called(monkeypatch)
    assert await close_request_sessions(ctx) == 1
    assert _leases(hub) == [0]


async def test_f1_a_request_that_opened_nothing_has_nothing_to_close(
    monkeypatch: pytest.MonkeyPatch, hub: MCPHub
) -> None:
    server, config = FakeServer(), DictConfig()
    _ = serve(monkeypatch, {URL: server})
    assert await close_request_sessions(context(config)) == 0
    assert hub.pool.sessions() == ()
