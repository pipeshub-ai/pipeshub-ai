"""RD-1..RD-3 (P2.8): a STDIO server's MCP session in its P5.10 runner stays open across requests (``-m docker``).

Same rig as ``test_mcp_runner_docker.py`` (its image, broker and runner fixtures; this module's own label
namespace): the agent's tool path with ``MCP_STDIO_RUNNERS`` and ``MCP_HUB`` on. Without the hub each
request's session is one server process that ends with the request (P5.10 D-1); with it the pooled
session, and so the server process, outlives the request.
"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING

import pytest

from app.agent_loop_lib.tools.registry import ToolRegistry
from app.agents.agent_loop.mcp_session import MCPSessionManager
from app.agents.agent_loop.mcp_tool_loader import MCPToolProvider
from harness_core.sandbox.mcp_runner.types import BRIDGE_PATH
from harness_core.testing.sandbox.mcp_runner import SECRET_NAME
from tests.unit.harness.mcp_hub.support import hub_on
from tests.unit.harness.mcp_runner.support import (
    ProcessStub,
    credential_record,
    resolved_instance,
    runners_on,
    stdio_context,
    stdio_instance,
)
from tests.unit.harness.sandbox_broker import test_mcp_runner_docker as rig
from tests.unit.harness.sandbox_broker.test_mcp_runner_docker import (  # the image-template override
    CONFIG,
    SERVER_PATH,
    echo_catalog_server,  # noqa: F401
)

if TYPE_CHECKING:
    from app.agents.agent_loop.context import AgentContext
    from app.harness.mcp_hub.hub import MCPHub
    from app.harness.sandbox.docker_client import DockerClientProvider
    from harness_core.sandbox.mcp_runner import SandboxMCPRunner
    from harness_core.testing.clock import ManualClock
    from tests.unit.harness.credentials.support import DictConfig

pytestmark = [pytest.mark.docker, pytest.mark.timeout(600, method="thread")]

# P5.10's rig, used as it is: its image, broker and runner fixtures.
runner_image = rig.runner_image
runner_namespace = rig.runner_namespace
runner_prefix = rig.runner_prefix
runner_mode = rig.runner_mode
runner_broker = rig.runner_broker
mcp_runner = rig.mcp_runner
_runner_container = rig._runner_container

TOKENS = {"alice": "token-alice-0123456789", "bob": "token-bob-0123456789"}


def _server_pids(provider: DockerClientProvider, container_id: str) -> list[str]:
    top = provider.client.api.top(container_id)
    pid = top["Titles"].index("PID")
    # The bridge's own argv names the server too; a session is a server process the bridge started.
    return [p[pid] for p in top["Processes"] if SERVER_PATH in p[-1] and BRIDGE_PATH not in p[-1]]


async def _until_no_server(provider: DockerClientProvider, container_id: str) -> list[str]:
    """A closed session's server process exits once its stdin closes: poll briefly (real time) for that."""
    for _ in range(100):
        pids = _server_pids(provider, container_id)
        if not pids:
            return pids
        await asyncio.sleep(0.1)
    return _server_pids(provider, container_id)


async def _request(owner: str) -> tuple[AgentContext, DictConfig, ToolRegistry]:
    instance = stdio_instance(command="python3", args=[SERVER_PATH])
    context, config = await stdio_context(instance, owner=owner, records={owner: credential_record(TOKENS[owner])})
    registry = ToolRegistry()
    with resolved_instance(instance):
        await MCPToolProvider().load_into(registry, context)
    return context, config, registry


async def _open_session(owner: str) -> None:
    """The first request opens the owner's session; D-MCP-5 (OSS) admits the listed tools without an approval."""
    context, _, registry = await _request(owner)
    assert context.mcp_tool_load_failures == []
    assert registry.has("mcp_echo_echo")
    await MCPSessionManager(context).aclose_all()


@pytest.fixture
def hub(
    monkeypatch: pytest.MonkeyPatch, mcp_runner: SandboxMCPRunner, runner_image: str, clock: ManualClock
) -> MCPHub:
    runners_on(monkeypatch, ProcessStub(mcp_runner))
    monkeypatch.setenv("MCP_RUNNER_IMAGE", runner_image)
    return hub_on(monkeypatch, clock)


async def _echo(registry: ToolRegistry, text: str) -> None:
    result = await registry.resolve_by_name("mcp_echo_echo").execute(text=text)
    assert result.success and text in json.dumps(result.data)


async def test_rd1_without_the_hub_a_requests_server_process_ends_with_it(
    monkeypatch: pytest.MonkeyPatch,
    mcp_runner: SandboxMCPRunner,
    runner_image: str,
    provider: DockerClientProvider,
    runner_namespace: str,
) -> None:
    runners_on(monkeypatch, ProcessStub(mcp_runner))
    monkeypatch.setenv("MCP_RUNNER_IMAGE", runner_image)
    context, _, registry = await _request("alice")
    await _echo(registry, "without the hub")
    (info,) = mcp_runner.runners()
    container = _runner_container(provider, info.key.digest(), runner_namespace)["Id"]
    assert len(_server_pids(provider, container)) == 1
    await MCPSessionManager(context).aclose_all()
    assert await _until_no_server(provider, container) == [], "P5.10 D-1: the session ends with the request"


async def test_rd1_with_the_hub_one_server_process_serves_every_request(
    hub: MCPHub, mcp_runner: SandboxMCPRunner, provider: DockerClientProvider, runner_namespace: str
) -> None:
    await _open_session("alice")
    (info,) = mcp_runner.runners()
    container = _runner_container(provider, info.key.digest(), runner_namespace)["Id"]
    (pid,) = _server_pids(provider, container)
    for text in ("first request", "second request"):
        context, _, registry = await _request("alice")
        assert context.mcp_tool_load_failures == []
        await _echo(registry, text)
        await MCPSessionManager(context).aclose_all()
        assert _server_pids(provider, container) == [pid], "the pooled session outlives the request"
    assert [i.key.owner for i in hub.pool.sessions()] == ["alice"]
    assert len(mcp_runner.runners()) == 1


async def test_rd2_another_owner_gets_its_own_runner_and_session(
    hub: MCPHub, mcp_runner: SandboxMCPRunner, provider: DockerClientProvider, runner_namespace: str
) -> None:
    await _open_session("alice")
    alice, _, alice_tools = await _request("alice")
    bob, _, bob_tools = await _request("bob")
    await _echo(alice_tools, "alice")
    await _echo(bob_tools, "bob")
    digest = await bob_tools.resolve_by_name("mcp_echo_env_digest").execute(name=SECRET_NAME)
    import hashlib

    # OWD-1 (WP-06): the server holds a placeholder, never either owner's token; MD-7 shows each owner's token
    # reaching the listed host through the proxy.
    held = json.dumps(digest.data)
    assert digest.success and not any(hashlib.sha256(t.encode()).hexdigest() in held for t in TOKENS.values())
    by_owner = {i.key.owner: i for i in mcp_runner.runners()}
    assert set(by_owner) == {"alice", "bob"} and by_owner["alice"].sandbox_id != by_owner["bob"].sandbox_id
    assert sorted(i.key.owner for i in hub.pool.sessions()) == ["alice", "bob"]
    for owner, info in by_owner.items():
        container = _runner_container(provider, info.key.digest(), runner_namespace)["Id"]
        assert len(_server_pids(provider, container)) == 1, owner
    await MCPSessionManager(alice).aclose_all()
    await MCPSessionManager(bob).aclose_all()


async def test_rd3_an_idle_pooled_session_closes_then_its_runner_idles_out(
    hub: MCPHub, mcp_runner: SandboxMCPRunner, provider: DockerClientProvider, runner_namespace: str, clock: ManualClock
) -> None:
    await _open_session("alice")
    (info,) = mcp_runner.runners()
    container = _runner_container(provider, info.key.digest(), runner_namespace)["Id"]
    _ = clock.advance(301)
    assert await mcp_runner.sweep() == 0, "an open pooled session keeps its runner"
    assert await hub.sweep() == 1
    assert await _until_no_server(provider, container) == []
    _ = clock.advance(CONFIG.idle_ttl_s + 1)
    assert await mcp_runner.sweep() == 1
    context, _, registry = await _request("alice")
    await _echo(registry, "after the sweep")
    (again,) = mcp_runner.runners()
    assert again.runner_id != info.runner_id
    await MCPSessionManager(context).aclose_all()
