"""MW-1..MW-6, MW-9: STDIO MCP servers for agents run through MCPRunner behind ``MCP_STDIO_RUNNERS`` (P5.10)."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest
from fastapi import HTTPException

from app.agent_loop_lib.tools.registry import ToolRegistry
from app.agents.agent_loop.mcp_session import MCPSessionManager
from app.agents.agent_loop.mcp_tool_loader import MCPToolProvider
from app.agents.mcp import client as client_module
from app.agents.mcp.client import MCPConnectionError, build_transport
from app.agents.mcp.service import instance_config_from_dict
from app.api.routes import mcp_servers
from app.harness.mcp_runner.routing import stdio_target
from app.harness.mcp_runner.transport import RunnerClientTransport, RunnerTarget
from harness_core.sandbox.mcp_runner import RunnerUnavailable
from harness_core.sandbox.types import RuntimeMode
from harness_core.testing.clock import ManualClock
from harness_core.testing.sandbox.egress import FakeEgressProxy
from harness_core.testing.sandbox.mcp_echo_server import SECRET_HEADER
from harness_core.testing.sandbox.mcp_runner import SECRET_NAME, digest
from tests.support.bound_org import bind_test_org
from tests.unit.harness.mcp_runner.support import (
    INSTANCE_ID,
    TOKEN,
    ProcessStub,
    credential_record,
    echo_declares,
    fake_runner,
    resolved_instance,
    runners_on,
    stdio_context,
    stdio_instance,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

    from harness_core.testing.sandbox.egress import EgressRequest

ECHO_TOOL = "mcp_echo_echo"


def _no_host_spawn(*_: Any, **__: Any) -> Any:  # noqa: ANN401
    """Fails a test that would start a STDIO server in this process."""
    raise AssertionError("a STDIO MCP server was about to start in the host process")


@pytest.fixture(autouse=True)
def _flag_off_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MCP_STDIO_RUNNERS", raising=False)


def test_mw1_flag_off_stdio_is_todays_host_transport(monkeypatch: pytest.MonkeyPatch) -> None:
    process = ProcessStub(error=AssertionError("no runner with the flag off"))
    monkeypatch.setattr("app.harness.mcp_runner.routing.process_mcp_runner", process)
    config = instance_config_from_dict(stdio_instance())
    assert not isinstance(build_transport(config, env={SECRET_NAME: "x"}).streams, RunnerClientTransport)


async def test_mw1_flag_off_the_tool_path_asks_for_no_runner(monkeypatch: pytest.MonkeyPatch) -> None:
    process = ProcessStub(error=AssertionError("no runner with the flag off"))
    monkeypatch.setattr("app.harness.mcp_runner.routing.process_mcp_runner", process)
    seen: list[object] = []

    class Manager:
        """Main's session manager builds one per server; this one records the runner and opens nothing."""

        def __init__(self, config: Any, **kwargs: Any) -> None:  # noqa: ANN401
            seen.append(kwargs.get("runner"))

        async def open(self) -> None:
            raise ConnectionError("not opened in this test")

    monkeypatch.setattr("app.agents.agent_loop.mcp_session.MCPClientManager", Manager)
    context, _ = await stdio_context()
    with resolved_instance(stdio_instance()):
        await MCPToolProvider().load_into(ToolRegistry(), context)
    assert seen and set(seen) == {None} and process.calls == 0


async def test_mw2_flag_on_stdio_runs_in_a_runner_and_http_is_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    runner, _ = fake_runner()
    runners_on(monkeypatch, ProcessStub(runner))
    monkeypatch.setattr(client_module, "stdio_client", _no_host_spawn)
    config = instance_config_from_dict(stdio_instance())
    target = await stdio_target(config, credential_record()["credentials"], tenant="org-1", owner="user-1")
    assert isinstance(target, RunnerTarget)
    assert isinstance(build_transport(config, runner=target).streams, RunnerClientTransport)
    with pytest.raises(MCPConnectionError, match="runs only in an MCP runner"):
        _ = build_transport(config, env={SECRET_NAME: "x"})
    # Custom instances: a catalog one keeps its template's transport (main #4003).
    sse = instance_config_from_dict(stdio_instance(typeId=None, transport="sse", url="https://mcp.example.com/sse"))
    http = instance_config_from_dict(
        stdio_instance(typeId=None, transport="streamable_http", url="https://mcp.example.com/mcp")
    )
    assert await stdio_target(sse, {}, tenant="org-1", owner="user-1") is None
    assert not isinstance(build_transport(sse).streams, RunnerClientTransport)
    assert not isinstance(build_transport(http).streams, RunnerClientTransport)


@pytest.mark.parametrize(
    ("setup", "reason"),
    [
        ("broker_down", "runner_unavailable"),
        ("no_proxy_for_hosts", "runner_unavailable"),
        ("floor_above_broker", "runner_isolation_unavailable"),
    ],
)
async def test_mw3_flag_on_and_no_runner_is_a_load_failure_never_a_host_process(
    monkeypatch: pytest.MonkeyPatch, setup: str, reason: str
) -> None:
    runner, broker = fake_runner()
    instance = stdio_instance()
    if setup == "broker_down":
        runners_on(monkeypatch, ProcessStub(error=RunnerUnavailable("broker gone", cause="broker_unreachable")))
    else:
        runners_on(monkeypatch, ProcessStub(runner))
    if setup == "floor_above_broker":
        monkeypatch.setenv("MCP_RUNNER_ISOLATION", RuntimeMode.GVISOR.value)
    monkeypatch.setattr(client_module, "stdio_client", _no_host_spawn)
    registry = ToolRegistry()
    with echo_declares(["api.listed.test"] if setup == "no_proxy_for_hosts" else []):
        context, _ = await stdio_context(instance)
        with resolved_instance(instance):
            await MCPToolProvider().load_into(registry, context)
    assert registry.names() == []
    assert [(f["instanceId"], f["reason"]) for f in context.mcp_tool_load_failures] == [(INSTANCE_ID, reason)]
    assert broker.sandboxes == {}


async def test_mw4_the_agent_tool_path_lists_and_calls_through_the_runner(monkeypatch: pytest.MonkeyPatch) -> None:
    """The server's key reaches its listed host (the egress proxy puts it back); the server holds a stand-in."""
    clock = ManualClock()

    async def whoami(request: EgressRequest) -> dict[str, str]:
        return {"keyDigest": digest(request.headers.get(SECRET_HEADER.lower(), ""))}

    runner, broker = fake_runner(clock, egress_proxy=FakeEgressProxy(clock=clock, gateways={"api.listed.test": whoami}))
    runners_on(monkeypatch, ProcessStub(runner))
    monkeypatch.setattr(client_module, "stdio_client", _no_host_spawn)
    listed = stdio_instance()
    registry = ToolRegistry()
    with echo_declares(["api.listed.test"]), resolved_instance(listed):
        context, _ = await stdio_context(listed)
        await MCPToolProvider().load_into(registry, context)
        assert context.mcp_tool_load_failures == []
        assert {ECHO_TOOL, "mcp_echo_env_digest", "mcp_echo_send_secret"} <= set(registry.names())
        output = await registry.resolve_by_name(ECHO_TOOL).execute(text="through the runner")
        assert output.success, output.error
        assert "through the runner" in json.dumps(output.data)
        held = await registry.resolve_by_name("mcp_echo_env_digest").execute(name=SECRET_NAME)
        assert digest(TOKEN) not in json.dumps(held.data)
        sent = await registry.resolve_by_name("mcp_echo_send_secret").execute(
            url="http://api.listed.test/whoami", name=SECRET_NAME
        )
        assert sent.success, sent.error
        assert digest(TOKEN) in json.dumps(sent.data)
    await MCPSessionManager(context).aclose_all()
    (info,) = runner.runners()
    assert info.key.owner == "user-1" and info.sessions == 0
    assert len(broker.sandboxes) == 1
    _ = await runner.stop_all()


async def test_mw5_a_credential_of_another_owner_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    runner, broker = fake_runner()
    runners_on(monkeypatch, ProcessStub(runner))
    context, _ = await stdio_context()
    # The reference names user-1's connection; a tampered owner field asks for user-2's runner with it.
    context.mcp_server_configs[INSTANCE_ID]["ownerId"] = "user-2"
    registry = ToolRegistry()
    with resolved_instance(stdio_instance()):
        await MCPToolProvider().load_into(registry, context)
    assert [f["reason"] for f in context.mcp_tool_load_failures] == ["runner_owner_mismatch"]
    assert broker.sandboxes == {} and runner.runners() == ()


async def test_mw6_the_token_resolved_by_handle_reaches_the_runner_only_as_a_placeholder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """OWD-1 (WP-06): the server holds a stand-in; the token travels sealed in its egress routes only."""
    runner, broker = fake_runner()
    runners_on(monkeypatch, ProcessStub(runner))
    context, _ = await stdio_context()
    assert TOKEN not in json.dumps(context.mcp_server_configs)
    with resolved_instance(stdio_instance()):
        await MCPToolProvider().load_into(ToolRegistry(), context)
    (state,) = broker.sandboxes.values()
    (bridge,) = state.execs
    assert bridge.env[SECRET_NAME].startswith("pxh-ph-") and TOKEN not in repr(bridge.env)
    assert TOKEN not in repr(state.spec) and TOKEN not in json.dumps(dict(state.spec.labels))
    assert context.tool_credentials().handles()
    _ = await runner.stop_all()


async def test_mw9_the_admin_discover_tools_route_runs_the_server_in_the_callers_runner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner, broker = fake_runner()
    runners_on(monkeypatch, ProcessStub(runner))
    monkeypatch.setattr(client_module, "stdio_client", _no_host_spawn)
    instance = stdio_instance()

    async def org_instance(*_: object) -> dict[str, Any]:
        return instance

    async def effective(*_: object) -> dict[str, Any]:
        return credential_record()

    monkeypatch.setattr(mcp_servers, "_get_org_instance", org_instance)
    monkeypatch.setattr(mcp_servers, "_resolve_effective_user_auth", effective)
    monkeypatch.setattr(mcp_servers, "_get_config_service", lambda _request: object())

    async def visible(*_: object) -> list[dict[str, Any]]:
        return [instance]

    monkeypatch.setattr(mcp_servers, "resolve_mcp_instances_with_inheritance", visible)
    # A Starlette request carries a scope: the route marks its failure there for the request line (main #5639).
    request = SimpleNamespace(state=SimpleNamespace(user={"userId": "admin-7", "orgId": "org-1"}), scope={})
    reply = await mcp_servers.get_instance_tools(request, INSTANCE_ID)  # type: ignore[arg-type]
    assert {"echo", "env_digest"} <= {tool["name"] for tool in reply["tools"]}
    (info,) = runner.runners()
    assert (info.key.tenant, info.key.owner) == ("org-1", "admin-7")
    assert len(broker.sandboxes) == 1

    monkeypatch.setenv("MCP_RUNNER_ISOLATION", RuntimeMode.GVISOR.value)
    with pytest.raises(HTTPException) as refused:
        _ = await mcp_servers.get_instance_tools(request, INSTANCE_ID)  # type: ignore[arg-type]
    assert refused.value.status_code == 502 and "runner_isolation_unavailable" in str(refused.value.detail)
    _ = await runner.stop_all()


@pytest.fixture(autouse=True)
def _bound_org() -> Iterator[str]:
    """The auth middleware binds the verified token's org; these tests call the route functions directly."""
    yield from bind_test_org("org-1")
