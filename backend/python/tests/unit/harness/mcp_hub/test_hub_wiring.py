"""The hub on the agent's tool path (P2.8): NV-6, SC-5..SC-7, SP-8, SP-9, SG-5, TS-1."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import pytest

from app.agents.mcp.discovery import build_namespaced_tool_name
from harness_core.mcp_hub import export_name, export_namespace
from harness_core.testing.clock import ManualClock
from tests.support.mcp_tool_admission import mcp_approvals_open  # noqa: F401 - the fixture, used by name
from tests.unit.harness.credentials.support import DictConfig
from tests.unit.harness.mcp_hub.support import (
    INSTANCE_ID,
    URL,
    FakeServer,
    context,
    http_instance,
    hub_on,
    load,
    no_requests_leave,
    serve,
    teardown,
)

# D-MCP-5: the hub's sessions, cache and checks under this build's admission, with no admin approval of tools.
pytestmark = pytest.mark.usefixtures("mcp_approvals_open")

if TYPE_CHECKING:
    from collections.abc import Callable

    from app.agent_loop_lib.tools.registry import ToolRegistry

DOTTED = export_name("fake", "dotted.tool")


@pytest.fixture
def server() -> FakeServer:
    return FakeServer()


@pytest.fixture
def config() -> DictConfig:
    return DictConfig()


@pytest.fixture
def clock() -> ManualClock:
    return ManualClock()


@pytest.fixture
def hub(monkeypatch: pytest.MonkeyPatch, server: FakeServer, clock: ManualClock):  # noqa: ANN201 - fixture
    _ = serve(monkeypatch, {URL: server})
    return hub_on(monkeypatch, clock)


def tool_names(registry: ToolRegistry) -> set[str]:
    return {name for name in registry.names() if name.startswith("mcp_")}


def test_nv2_a_name_that_already_fits_is_todays_name(monkeypatch: pytest.MonkeyPatch) -> None:
    pairs = (("github", "getUser"), ("Jira-Cloud", "search_issues"), ("slack team", "post-message"))
    legacy = [build_namespaced_tool_name(label, raw) for label, raw in pairs]
    monkeypatch.setenv("MCP_HUB", "true")
    assert [export_name(export_namespace(label), raw) for label, raw in pairs] == legacy
    assert [build_namespaced_tool_name(label, raw) for label, raw in pairs] == legacy


async def test_nv6_names_are_checked_and_exported(
    hub: object, server: FakeServer, config: DictConfig, caplog: pytest.LogCaptureFixture
) -> None:
    server.mcp.add_tool(_bad, name="bad tool", description="a name with a space")
    caplog.set_level(logging.WARNING)
    ctx = context(config)
    registry = await load(ctx)
    assert tool_names(registry) == {"mcp_fake_search", "mcp_fake_fetch", DOTTED, "mcp_fake_announce"}
    result = await registry.resolve_by_name(DOTTED).execute()
    assert result.success and server.calls == ["dotted.tool"]
    again = context(config)
    _ = await load(again)
    refused = [r.getMessage() for r in caplog.records if "mcp_hub.tool_refused" in r.getMessage()]
    assert len(refused) == 1 and "mcp_tool_name_invalid" in refused[0]
    await teardown(ctx, again)


def _bad() -> str:
    return "bad"


async def test_sc5_a_second_request_is_served_from_the_cache_and_an_edit_misses(
    hub: object, server: FakeServer, config: DictConfig
) -> None:
    first, second = context(config), context(config)
    _ = await load(first)
    _ = await load(second)
    assert server.lists == 1
    edited = context(config, instance=http_instance(updatedAt=5))
    _ = await load(edited)
    assert server.lists == 2
    await teardown(first, second, edited)


async def test_sc6_list_changed_drops_the_cached_list(hub: object, server: FakeServer, config: DictConfig) -> None:
    first = context(config)
    registry = await load(first)
    server.reveal("extra")
    stale = context(config)
    assert "mcp_fake_extra" not in tool_names(await load(stale)), "cached until the server says otherwise"
    assert (await registry.resolve_by_name("mcp_fake_announce").execute()).success
    fresh = context(config)
    assert "mcp_fake_extra" in tool_names(await load(fresh))
    assert server.lists == 2
    await teardown(first, stale, fresh)


async def test_sc7_and_sp8_another_owner_lists_and_connects_on_its_own(
    hub: object, server: FakeServer, config: DictConfig
) -> None:
    alice_1 = context(config, owner="alice", token="tok-alice")
    _ = await load(alice_1)
    await teardown(alice_1)
    alice_2 = context(config, owner="alice", token="tok-alice")
    registry = await load(alice_2)
    assert (await registry.resolve_by_name("mcp_fake_search").execute(q="x")).success
    await teardown(alice_2)
    assert (server.initializes, server.lists) == (1, 1), "one session and one list across alice's requests"
    bob = context(config, owner="bob", token="tok-bob")
    registry = await load(bob)
    assert (await registry.resolve_by_name("mcp_fake_search").execute(q="y")).success
    await teardown(bob)
    assert (server.initializes, server.lists) == (2, 2)
    owners = sorted(info.key.owner for info in hub.pool.sessions())  # type: ignore[attr-defined]
    assert owners == ["alice", "bob"]


async def test_sp8_without_the_hub_every_request_connects(
    monkeypatch: pytest.MonkeyPatch, server: FakeServer, config: DictConfig
) -> None:
    _ = serve(monkeypatch, {URL: server})
    for _ in range(2):
        ctx = context(config)
        registry = await load(ctx)
        assert (await registry.resolve_by_name("mcp_fake_search").execute(q="x")).success
        await teardown(ctx)
    assert server.initializes == 2, "one session per request (it lists and calls), never kept across requests"
    assert "mcp_fake_bad tool" not in tool_names(registry)


async def test_sp10_a_stdio_child_of_this_process_is_never_pooled(
    monkeypatch: pytest.MonkeyPatch, server: FakeServer, config: DictConfig, clock: ManualClock
) -> None:
    stdio = http_instance(_id="mcp-local", transport="stdio", command="fake-server", url=None, authMode="none")
    _ = serve(monkeypatch, {"mcp-local": server})
    hub = hub_on(monkeypatch, clock)
    first = context(config, instance=stdio)
    _ = await load(first)
    contexts = [first]
    for text in ("a", "b"):
        ctx = context(config, instance=stdio)
        registry = await load(ctx)
        assert (await registry.resolve_by_name("mcp_fake_search").execute(q=text)).success
        await teardown(ctx)
        contexts.append(ctx)
    assert hub.pool.sessions() == ()
    assert server.initializes == 3, "one listing session (then cached), and one session per request's calls, closed"
    await teardown(*contexts)


async def test_sp9_a_broken_pooled_session_is_discarded_and_the_next_call_reconnects(
    hub: object, server: FakeServer, config: DictConfig
) -> None:
    ctx = context(config)
    registry = await load(ctx)
    (info,) = hub.pool.sessions()  # type: ignore[attr-defined]
    lease = ctx.tool_state["_mcp_hub_leases"].get(INSTANCE_ID)
    assert lease is None, "discovery returns its lease to the pool"
    manager = hub.pool._entries[info.key.digest()].session  # type: ignore[attr-defined]
    await manager._session.runner.stop()  # the connection is gone under the pooled session
    failed = await registry.resolve_by_name("mcp_fake_search").execute(q="x")
    assert not failed.success
    retried = await registry.resolve_by_name("mcp_fake_search").execute(q="x")
    assert retried.success
    assert server.initializes == 2
    await teardown(ctx)


async def test_sg5_a_metadata_url_is_refused_before_any_request(
    hub: object, config: DictConfig
) -> None:
    instance = http_instance(url="http://169.254.169.254/latest/mcp")
    ctx = context(config, instance=instance)
    with no_requests_leave() as sent:
        assert tool_names(await load(ctx)) == set()
    assert sent == []
    # Main's URL guard refuses it (``MCPUrlBlockedError``), reported as main's code: an administrator's fix.
    assert ctx.mcp_tool_load_failures[0]["reason"] == "blocked"
    await teardown(ctx)


async def test_sg5_without_the_hub_a_private_url_is_connected(
    monkeypatch: pytest.MonkeyPatch, config: DictConfig
) -> None:
    built = serve(monkeypatch, {})
    # A private address: with the hub off it is allowed (main's guard resolves it once and pins the connection; a
    # metadata address is refused in both modes, test_mcpfix_addresses.py).
    instance = http_instance(url="http://10.0.0.5/latest/mcp")
    ctx = context(config, instance=instance)
    with no_requests_leave() as sent:
        _ = await load(ctx)
    assert [b["url"] for b in built] == [instance["url"]]
    assert sent == [instance["url"]]
    assert ctx.mcp_tool_load_failures[0]["reason"] == "unreachable"


async def test_ts1_many_mcp_tools_get_tool_search_by_default(
    hub: object, server: FakeServer, config: DictConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.agents.agent_loop.lazy_tools_wiring import (
        group_connector_toolsets,
        should_apply_lazy_tools,
    )

    for name in ("PIPESHUB_ENABLE_LAZY_TOOLS", "PIPESHUB_LAZY_TOOLS_THRESHOLD"):
        monkeypatch.delenv(name, raising=False)

    def make(n: int) -> Callable[[], str]:
        def tool() -> str:
            return str(n)

        return tool

    for n in range(21):
        server.mcp.add_tool(make(n), name=f"bulk_{n}", description=f"Bulk tool {n}")
    ctx = context(config)
    registry = await load(ctx)
    mcp_tools = sorted(tool_names(registry))
    assert len(mcp_tools) == 25
    assert should_apply_lazy_tools(len(mcp_tools)), "above the default threshold: tool search, not every schema"
    assert group_connector_toolsets(registry, mcp_tools), "an MCP-only agent goes lazy"
    assert not should_apply_lazy_tools(10)
    await teardown(ctx)
