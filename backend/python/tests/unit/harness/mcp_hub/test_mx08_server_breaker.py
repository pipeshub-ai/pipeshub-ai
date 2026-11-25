"""MX-08: a server that is down or slow is bounded by its own timeouts and a circuit breaker; other servers are not."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import pytest

from app.agents.mcp.client import MCPCallTooLongError
from app.agents.mcp.errors import (
    MCPConnectionError,
    MCPHttpStatusError,
    MCPRequestNotSentError,
    MCPServerCircuitOpenError,
)
from app.harness.mcp_hub.breaker import ServerBreakers, server_down
from app.harness.mcp_hub.settings import hub_settings
from harness_core.mcp_hub.types import SessionKey
from harness_core.testing.clock import ManualClock
from tests.unit.agents.adapter.conftest import make_context
from tests.unit.harness.credentials.support import DictConfig
from tests.unit.harness.mcp_hub.support import (
    URL,
    FakeServer,
    http_instance,
    hub_on,
    load,
    serve,
    teardown,
)

if TYPE_CHECKING:
    from app.agents.agent_loop.context import AgentContext
    from app.harness.mcp_hub.hub import MCPHub

KEY = SessionKey("org-1", "mcp-slow", "user-1")
SLOW_URL = "http://93.184.216.35/mcp"
SLOW = http_instance(_id="mcp-slow", name="Slow", typeId="slow", url=SLOW_URL, callTimeoutSeconds=1)


def _ok() -> asyncio.Future[str]:
    done: asyncio.Future[str] = asyncio.get_running_loop().create_future()
    done.set_result("ok")
    return done


def _raises(exc: BaseException) -> Any:  # noqa: ANN401 - a request factory
    async def request() -> str:
        raise exc

    return request


@pytest.mark.parametrize(
    ("exc", "down"),
    [
        (TimeoutError(), True),
        (MCPRequestNotSentError("no connection"), True),
        (MCPConnectionError("refused"), True),
        (ConnectionRefusedError(), True),
        (MCPHttpStatusError(401), False),
        (MCPCallTooLongError("still working", limit_seconds=1800), False),
        (ValueError("bad argument"), False),
    ],
)
def test_mx08_only_a_dead_or_silent_server_counts(exc: BaseException, down: bool) -> None:
    assert server_down(exc) is down


async def test_mx08_the_circuit_opens_after_failures_in_a_row_and_one_probe_closes_it() -> None:
    breakers = ServerBreakers(failures=2, cooldown_s=0.05)
    for _ in range(2):
        with pytest.raises(TimeoutError):
            await breakers.run(KEY, "Slow", _raises(TimeoutError()))
    assert breakers.state(KEY) == "open"
    sent: list[str] = []

    async def counted() -> str:
        sent.append("x")
        return "ok"

    with pytest.raises(MCPServerCircuitOpenError) as refused:
        await breakers.run(KEY, "Slow", counted)
    assert sent == [] and "Slow" in str(refused.value) and refused.value.reason_code == "mcp_server_circuit_open"
    await asyncio.sleep(0.06)
    gate = asyncio.Event()

    async def probe() -> str:
        await gate.wait()
        return "ok"

    first = asyncio.ensure_future(breakers.run(KEY, "Slow", probe))
    await asyncio.sleep(0)
    with pytest.raises(MCPServerCircuitOpenError):
        await breakers.run(KEY, "Slow", counted)
    gate.set()
    assert await first == "ok"
    assert breakers.state(KEY) == "closed"
    assert await breakers.run(KEY, "Slow", counted) == "ok" and sent == ["x"]


async def test_mx08_a_failed_probe_opens_it_again() -> None:
    breakers = ServerBreakers(failures=1, cooldown_s=0.05)
    with pytest.raises(TimeoutError):
        await breakers.run(KEY, "Slow", _raises(TimeoutError()))
    await asyncio.sleep(0.06)
    with pytest.raises(MCPConnectionError):
        await breakers.run(KEY, "Slow", _raises(MCPConnectionError("down")))
    with pytest.raises(MCPServerCircuitOpenError):
        await breakers.run(KEY, "Slow", _raises(AssertionError("not sent")))


async def test_mx08_a_server_that_answers_resets_the_count_and_owners_are_apart() -> None:
    breakers = ServerBreakers(failures=2, cooldown_s=60)
    with pytest.raises(TimeoutError):
        await breakers.run(KEY, "Slow", _raises(TimeoutError()))
    with pytest.raises(MCPHttpStatusError):
        await breakers.run(KEY, "Slow", _raises(MCPHttpStatusError(401)))
    with pytest.raises(TimeoutError):
        await breakers.run(KEY, "Slow", _raises(TimeoutError()))
    assert breakers.state(KEY) == "closed"
    with pytest.raises(TimeoutError):
        await breakers.run(KEY, "Slow", _raises(TimeoutError()))
    assert breakers.state(KEY) == "open"
    other_owner = SessionKey("org-1", "mcp-slow", "user-2")
    assert await breakers.run(other_owner, "Slow", lambda: _ok()) == "ok"


async def test_mx08_zero_failures_turns_the_breaker_off() -> None:
    breakers = ServerBreakers(failures=0, cooldown_s=60)
    for _ in range(5):
        with pytest.raises(TimeoutError):
            await breakers.run(KEY, "Slow", _raises(TimeoutError()))
    assert breakers.state(KEY) == "closed"


def test_mx08_the_settings_read_the_deployment(monkeypatch: pytest.MonkeyPatch) -> None:
    assert (hub_settings().breaker_failures, hub_settings().breaker_cooldown_s) == (5, 30)
    monkeypatch.setenv("MCP_HUB_BREAKER_FAILURES", "0")
    monkeypatch.setenv("MCP_HUB_BREAKER_COOLDOWN_S", "120")
    assert (hub_settings().breaker_failures, hub_settings().breaker_cooldown_s) == (0, 120)
    monkeypatch.setenv("MCP_HUB_BREAKER_COOLDOWN_S", "0")
    with pytest.raises(ValueError, match="MCP_HUB_BREAKER_COOLDOWN_S"):
        _ = hub_settings()


class SlowServer(FakeServer):
    """``FakeServer`` plus ``hang``, which never answers."""

    def __init__(self) -> None:
        super().__init__()
        self.answered = asyncio.Event()

        async def hang() -> str:
            await asyncio.Event().wait()
            self.answered.set()
            return "late"

        self.mcp.add_tool(hang, name="hang", description="Waits")


def _two_servers(config: DictConfig) -> AgentContext:
    fast = http_instance()
    return make_context(
        config_service=config,
        mcp_servers=[
            {"instanceId": i["_id"], "name": i["name"], "displayName": i["name"], "typeId": i["typeId"], "tools": None}
            for i in (SLOW, fast)
        ],
        mcp_server_configs={
            i["_id"]: {"instance": i, "auth": {"credentials": {"apiToken": "tok"}}, "ownerId": "user-1"} for i in (SLOW, fast)
        },
        run_id="run-1",
    )


async def test_mx08_a_slow_server_times_out_then_fails_fast_and_the_other_server_still_answers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    slow, fast = SlowServer(), FakeServer()
    _ = serve(monkeypatch, {SLOW_URL: slow, URL: fast})
    hub: MCPHub = hub_on(monkeypatch, ManualClock())
    hub._breakers = ServerBreakers(failures=2, cooldown_s=60)
    config = DictConfig()
    ctx = _two_servers(config)
    registry = await load(ctx)
    hang, search, fetch = (registry.resolve_by_name(n) for n in ("mcp_slow_hang", "mcp_slow_search", "mcp_fake_fetch"))
    for _ in range(2):
        timed_out = await hang.execute()
        assert not timed_out.success and timed_out.error_code == "timeout"
        assert "within 1 seconds" in (timed_out.error or ""), "the server's own 1 s call timeout bounds the call"
    assert not slow.answered.is_set(), "the hang never answered: the timeout ended the calls"
    refused = await search.execute(q="x")
    assert not refused.success and refused.error_code == "unavailable"
    assert "failed several times in a row" in (refused.error or "")
    assert "search" not in slow.calls, "nothing was sent to the open server"
    answered = await fetch.execute(issue="7")
    assert answered.success and fast.calls == ["fetch"]
    await teardown(ctx)


async def test_mx08_an_open_circuit_skips_discovery_and_a_cached_list_still_loads(monkeypatch: pytest.MonkeyPatch) -> None:
    slow, fast = SlowServer(), FakeServer()
    _ = serve(monkeypatch, {SLOW_URL: slow, URL: fast})
    hub: MCPHub = hub_on(monkeypatch, ManualClock())
    hub._breakers = ServerBreakers(failures=1, cooldown_s=60)
    config = DictConfig()
    first = _two_servers(config)
    registry = await load(first)
    assert not (await registry.resolve_by_name("mcp_slow_hang").execute()).success
    assert hub.breakers.state(SessionKey("org-1", "mcp-slow", "user-1")) == "open"
    lists = slow.lists
    second = _two_servers(config)
    names = set((await load(second)).names())
    assert {"mcp_slow_search", "mcp_fake_fetch"} <= names, "the cached lists load; no server is asked"
    await hub.tools_changed("org-1", "mcp-slow")
    third = _two_servers(config)
    names = set((await load(third)).names())
    assert "mcp_slow_search" not in names and "mcp_fake_fetch" in names
    assert slow.lists == lists
    assert [f["reason"] for f in third.mcp_tool_load_failures] == ["mcp_server_circuit_open"]
    await teardown(first, second, third)
