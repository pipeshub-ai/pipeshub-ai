"""MD-1..MD-7: MCP runners on the real Docker broker (P5.10, ``-m docker``; the gVisor lane skips without ``runsc``).

A server's secret never enters its runner (OWD-1, WP-06): the contract (MD-1) and MD-5 run the runner behind the
real egress proxy, which opens the sealed route and puts the secret on the server's request to its listed host.

The server is the stdlib echo server (``harness_core/testing/sandbox/mcp_echo_server.py``) in an image built
here ``FROM`` the sandbox test image (one small layer), so it is pinned in the image, as D23 requires, and
removed at teardown. Containers carry this module's own label namespace and are removed by it.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any, override

import docker
import psutil
import pytest

from app.agent_loop_lib.tools.registry import ToolRegistry
from app.agents.agent_loop.mcp_session import MCPSessionManager
from app.agents.agent_loop.mcp_tool_loader import MCPToolProvider
from app.agents.mcp import client as client_module
from app.agents.mcp.registry import get_mcp_registry
from app.harness.sandbox.docker_broker import DockerSandboxBroker, probe
from harness_core.ids import RandomIds
from harness_core.sandbox.egress.sealed import CredentialSealer
from harness_core.sandbox.mcp_runner import (
    RUNNER_LABEL,
    MCPRunnerSpec,
    RunnerConfig,
    RunnerEgressHost,
    RunnerOwnerMismatch,
    SandboxMCPRunner,
)
from harness_core.sandbox.mcp_runner.types import BRIDGE_DIR
from harness_core.sandbox.types import RuntimeMode
from harness_core.security.token_patterns import find_forbidden
from harness_core.testing.sandbox.mcp_runner import (
    SECRET_NAME,
    LineRpc,
    MCPRunnerContract,
    MCPRunnerContractEnv,
    digest,
    request,
    session,
)
from tests.support.docker_images import build_test_image
from tests.unit.agent_loop_lib.sandbox.contract.conftest import (
    DOCKER_TEST_IMAGE,
    skip_without_docker,
    skip_without_runsc,
)
from tests.unit.harness.mcp_runner.support import (
    ProcessStub,
    resolved_instance,
    runners_on,
    stdio_context,
    stdio_instance,
)
from tests.unit.harness.sandbox_broker.conftest import (
    cleanup,
    cleanup_networks,
    egress_wiring,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator

    from app.agents.mcp.models import MCPServerTemplate
    from app.harness.sandbox.docker_client import DockerClientProvider
    from harness_core.sandbox.egress.client import RemoteEgressBindings
    from harness_core.testing.clock import ManualClock
    from tests.unit.harness.sandbox_broker.egress_stack import EgressStack

pytestmark = [pytest.mark.docker, pytest.mark.timeout(600, method="thread")]

OWNER = {"pipeshub.harness.owner": "x-lead", "pipeshub.agent": "P5.10"}
SERVER_PATH = "/opt/pxh-echo/server.py"
ECHO_SOURCE = (
    Path(__file__).resolve().parents[4]
    / "harness_core/testing/sandbox/mcp_echo_server.py"
)
LISTED = "api.mcp-listed.test"
# Runners with no listed host still seal their secrets (they reach nowhere); the key is the deployment's.
TEST_SEALER = CredentialSealer(b"p510-test-deployment-key")
CONFIG = RunnerConfig(
    idle_ttl_s=60, max_age_s=300, reap_grace_s=60, ready_timeout_s=60, labels=OWNER
)


@pytest.fixture(autouse=True)
def echo_catalog_server(echo_catalog_server: MCPServerTemplate) -> Iterator[MCPServerTemplate]:
    """In a real runner the catalog template's program is the one the test image carries: main's
    STDIO policy (#3887) runs a catalog instance's template, never the instance's stored command."""
    image_template = echo_catalog_server.model_copy(update={"command": "python3", "args": [SERVER_PATH]})
    with pytest.MonkeyPatch.context() as patch:
        patch.setitem(get_mcp_registry()._templates, "echo", image_template)  # pyright: ignore[reportPrivateUsage]
        yield image_template



@pytest.fixture(scope="module")
def runner_image() -> Iterator[str]:
    """``FROM`` the sandbox test image plus the echo server: the pinned STDIO server of this lane."""
    skip_without_docker()
    client = docker.from_env()
    tag = f"pxh-mcp-runner:p510-{uuid.uuid4().hex[:8]}"
    dockerfile = f"FROM {DOCKER_TEST_IMAGE}\nCOPY server.py {SERVER_PATH}\n"
    build_test_image(client, tag, dockerfile, files={"server.py": ECHO_SOURCE.read_bytes()}, labels=OWNER)
    try:
        yield tag
    finally:
        with contextlib.suppress(Exception):
            client.images.remove(tag, force=True)
        client.close()


class DockerEnv(MCPRunnerContractEnv):
    config = CONFIG

    def __init__(
        self,
        clock: ManualClock,
        image: str,
        floor: RuntimeMode = RuntimeMode.DOCKER_HARDENED,
        listed_upstream: str | None = None,
    ) -> None:
        """``listed_upstream``: the server may reach ``LISTED`` (served there), and its secret goes only there."""
        self._clock = clock
        self.image = image
        self._floor = floor
        self.egress = (RunnerEgressHost(LISTED, upstream=listed_upstream),) if listed_upstream else ()

    @override
    async def received_secret(self, rpc: LineRpc) -> str:
        if not self.egress:
            return await super().received_secret(rpc)
        reply = await rpc.call("send_secret", {"url": f"http://{LISTED}/whoami", "name": SECRET_NAME})
        status, _, body = reply.partition(" ")
        assert status == "200", reply
        return str(json.loads(body)["keyDigest"])

    @override
    def spec(
        self,
        server_id: str = "srv-echo",
        *,
        env_names: frozenset[str] = frozenset({SECRET_NAME}),
        command: str = "python3",
        args: tuple[str, ...] = (SERVER_PATH,),
    ) -> MCPRunnerSpec:
        return MCPRunnerSpec(
            server_id=server_id,
            image=self.image,
            command=command,
            args=args,
            env_names=env_names,
            isolation_floor=self._floor,
            egress=self.egress,
        )

    @override
    def advance(self, seconds: float) -> None:
        _ = self._clock.advance(seconds)


@pytest.fixture
def runner_namespace() -> str:
    return f"io.pipeshub.p510test.{uuid.uuid4().hex[:8]}"


@pytest.fixture
def runner_prefix(runner_namespace: str) -> str:
    return "pxh-p510-" + runner_namespace.rsplit(".", 1)[1]


@pytest.fixture(
    params=[RuntimeMode.DOCKER_HARDENED, RuntimeMode.GVISOR], ids=["runc", "gvisor"]
)
def runner_mode(request: pytest.FixtureRequest) -> RuntimeMode:
    return request.param


@pytest.fixture
async def runner_broker(
    provider: DockerClientProvider,
    clock: ManualClock,
    runner_image: str,
    runner_namespace: str,
    runner_prefix: str,
    runner_mode: RuntimeMode,
) -> AsyncIterator[DockerSandboxBroker]:
    if (
        runner_mode is RuntimeMode.GVISOR
        and RuntimeMode.GVISOR not in (await probe(provider)).modes()
    ):
        skip_without_runsc()
    broker = await DockerSandboxBroker.create(
        provider=provider,
        clock=clock,
        ids=RandomIds(),
        allowed_images=frozenset({runner_image}),
        label_namespace=runner_namespace,
        volume_prefix=runner_prefix,
    )
    yield broker
    await provider.run_blocking(cleanup, provider, runner_namespace, runner_prefix)


@pytest.fixture
def mcp_env(
    clock: ManualClock, runner_image: str, runner_mode: RuntimeMode
) -> DockerEnv:
    return DockerEnv(clock, runner_image, runner_mode)


@pytest.fixture
async def mcp_runner(
    runner_broker: DockerSandboxBroker, clock: ManualClock
) -> AsyncIterator[SandboxMCPRunner]:
    runner = SandboxMCPRunner(
        runner_broker, clock=clock, ids=RandomIds(), config=CONFIG, instance="p510-test", sealer=TEST_SEALER
    )
    yield runner
    _ = await runner.stop_all()


class TestDockerRunner(MCPRunnerContract):
    """MD-1: the contract on the Docker broker, behind the real egress proxy (the secret reaches the listed host)."""

    @pytest.fixture
    def mcp_env(self, clock: ManualClock, runner_image: str, runner_mode: RuntimeMode, egress_stack: EgressStack) -> DockerEnv:
        return DockerEnv(clock, runner_image, runner_mode, listed_upstream=f"http://{egress_stack.gateway}:8080")

    @pytest.fixture
    async def mcp_runner(
        self,
        provider: DockerClientProvider,
        clock: ManualClock,
        egress_stack: EgressStack,
        egress_bindings: RemoteEgressBindings,
        runner_image: str,
        runner_namespace: str,
        runner_prefix: str,
        runner_mode: RuntimeMode,
    ) -> AsyncIterator[SandboxMCPRunner]:
        if runner_mode is RuntimeMode.GVISOR and RuntimeMode.GVISOR not in (await probe(provider)).modes():
            skip_without_runsc()
        async for runner in _egress_runner(
            provider, clock, egress_stack, egress_bindings, runner_image, runner_namespace, runner_prefix
        ):
            yield runner


def _containers(
    provider: DockerClientProvider, namespace: str | None = None, **labels: str
) -> list[dict[str, Any]]:
    filters = [f"{k}={v}" for k, v in {**OWNER, **labels}.items()]
    # Runner keys are deterministic, so other lanes on this daemon hold containers with the same key: keep our own.
    if namespace is not None:
        filters.append(f"{namespace}.managed=1")
    return provider.client.api.containers(all=True, filters={"label": filters})


def _runner_container(
    provider: DockerClientProvider, key_digest: str, namespace: str
) -> dict[str, Any]:
    (found,) = _containers(provider, namespace, mcpRunnerKey=key_digest)
    return provider.client.api.inspect_container(found["Id"])


def _exec(provider: DockerClientProvider, container: str, script: str) -> str:
    api = provider.client.api
    created = api.exec_create(container, ["/bin/sh", "-c", script], user="1000:1000")
    return api.exec_start(created["Id"]).decode("utf-8", "replace")


async def test_md2_the_agent_tool_path_lists_and_calls_through_a_docker_runner(
    mcp_runner: SandboxMCPRunner,
    provider: DockerClientProvider,
    runner_image: str,
    monkeypatch: pytest.MonkeyPatch,
    runner_namespace: str,
) -> None:
    runners_on(monkeypatch, ProcessStub(mcp_runner))
    monkeypatch.setenv("MCP_RUNNER_IMAGE", runner_image)

    def no_host_process(*_: object, **__: object) -> None:
        raise AssertionError(
            "a STDIO MCP server was about to start in the host process"
        )

    monkeypatch.setattr(client_module, "stdio_client", no_host_process)
    instance = stdio_instance(command="python3", args=[SERVER_PATH])
    context, _ = await stdio_context(instance)
    registry = ToolRegistry()
    with resolved_instance(instance):
        await MCPToolProvider().load_into(registry, context)
        assert context.mcp_tool_load_failures == []
        echoed = await registry.resolve_by_name("mcp_echo_echo").execute(
            text="from a container"
        )
        where = await registry.resolve_by_name("mcp_echo_where").execute()
    assert echoed.success and "from a container" in json.dumps(echoed.data)
    (info,) = mcp_runner.runners()
    container = _runner_container(provider, info.key.digest(), runner_namespace)
    assert "uid=1000" in json.dumps(where.data)
    top = provider.client.api.top(container["Id"])
    assert [p for p in top["Processes"] if SERVER_PATH in p[-1]], (
        "the server runs inside the runner container"
    )
    ours = [
        " ".join(p.cmdline())
        for p in psutil.Process(os.getpid()).children(recursive=True)
    ]
    assert not [c for c in ours if SERVER_PATH in c]
    await MCPSessionManager(context).aclose_all()


async def test_md3_a_runner_container_is_hardened_and_has_no_network(
    mcp_runner: SandboxMCPRunner,
    mcp_env: DockerEnv,
    provider: DockerClientProvider,
    runner_namespace: str,
) -> None:
    rpc = await session(mcp_runner, request(mcp_env))
    container = _runner_container(
        provider, request(mcp_env).key.digest(), runner_namespace
    )
    host = container["HostConfig"]
    assert host["NetworkMode"] == "none" and host["ReadonlyRootfs"] is True
    assert (
        host["CapDrop"] == ["ALL"] and "no-new-privileges:true" in host["SecurityOpt"]
    )
    assert (
        not host.get("Privileged")
        and not host.get("Binds")
        and not host.get("PortBindings")
    )
    assert container["Config"]["User"] == "1000:1000"
    assert container["Config"]["Labels"][RUNNER_LABEL] == "1"
    (bridge_mount,) = [m for m in container["Mounts"] if m["Destination"] == BRIDGE_DIR]
    assert bridge_mount["RW"] is False
    await rpc.aclose()


async def _egress_runner(
    provider: DockerClientProvider,
    clock: ManualClock,
    egress_stack: EgressStack,
    egress_bindings: RemoteEgressBindings,
    runner_image: str,
    runner_namespace: str,
    runner_prefix: str,
) -> AsyncIterator[SandboxMCPRunner]:
    broker = await DockerSandboxBroker.create(
        provider=provider,
        clock=clock,
        ids=RandomIds(),
        allowed_images=frozenset({runner_image}),
        label_namespace=runner_namespace,
        volume_prefix=runner_prefix,
        egress=egress_wiring(
            provider, clock, egress_stack, egress_bindings, runner_prefix
        ),
    )
    runner = SandboxMCPRunner(
        broker,
        clock=clock,
        ids=RandomIds(),
        config=CONFIG,
        instance="p510-egress",
        sealer=CredentialSealer(egress_stack.secret),
    )
    yield runner
    _ = await runner.stop_all()
    await provider.run_blocking(cleanup, provider, runner_namespace, runner_prefix)
    await provider.run_blocking(
        cleanup_networks, provider, runner_namespace, egress_stack.proxy
    )


@pytest.fixture
async def egress_runner(
    provider: DockerClientProvider,
    clock: ManualClock,
    egress_stack: EgressStack,
    egress_bindings: RemoteEgressBindings,
    runner_image: str,
    runner_namespace: str,
    runner_prefix: str,
) -> AsyncIterator[SandboxMCPRunner]:
    async for runner in _egress_runner(
        provider, clock, egress_stack, egress_bindings, runner_image, runner_namespace, runner_prefix
    ):
        yield runner


async def test_md4_egress_is_denied_except_a_listed_host_through_the_proxy(
    egress_runner: SandboxMCPRunner,
    egress_stack: EgressStack,
    clock: ManualClock,
    runner_image: str,
    provider: DockerClientProvider,
    runner_namespace: str,
) -> None:
    env = DockerEnv(clock, runner_image)
    upstream = f"http://{egress_stack.gateway}:8080"
    spec = MCPRunnerSpec(
        server_id="srv-egress",
        image=runner_image,
        command="python3",
        args=(SERVER_PATH,),
        env_names=frozenset({SECRET_NAME}),
        egress=(RunnerEgressHost(LISTED, upstream=upstream),),
    )
    req = request(env, server_id="srv-egress", spec=spec)
    rpc = await session(egress_runner, req)
    denied = await rpc.call("fetch", {"url": "http://api.unlisted.test/data"})
    assert denied.startswith("403 ") and "destination_not_allowed" in denied
    allowed = await rpc.call("fetch", {"url": f"http://{LISTED}/hello"})
    assert allowed.startswith("200 ") and '"/hello"' in allowed
    container = _runner_container(provider, req.key.digest(), runner_namespace)
    probe_script = "python3 -c \"import socket; socket.create_connection(('1.1.1.1', 80), 3)\" 2>&1 | tail -1"
    assert "Error" in _exec(provider, container["Id"], probe_script)
    await rpc.aclose()


async def test_md5_the_secret_is_in_no_runner_process_and_reaches_only_the_listed_host(
    egress_runner: SandboxMCPRunner,
    egress_stack: EgressStack,
    clock: ManualClock,
    runner_image: str,
    provider: DockerClientProvider,
    caplog: pytest.LogCaptureFixture,
    runner_namespace: str,
) -> None:
    """OWD-1 (WP-06): every process of the runner holds a placeholder; the proxy puts the secret on the request."""
    caplog.set_level(logging.DEBUG)
    canary = "ghp_" + "Md5DockerCanary0123456789abcdefABCDEF"
    env = DockerEnv(clock, runner_image, listed_upstream=f"http://{egress_stack.gateway}:8080")
    req = request(env, secret=canary)
    rpc = await session(egress_runner, req)
    assert await env.received_secret(rpc) == digest(canary)
    assert await rpc.call("env_digest", {"name": SECRET_NAME}) != digest(canary)
    container = _runner_container(provider, req.key.digest(), runner_namespace)
    holders = _exec(
        provider,
        container["Id"],
        'for p in /proc/[0-9]*; do c=$(tr "\\0" " " < $p/cmdline 2>/dev/null); '
        'tr "\\0" "\\n" < $p/environ 2>/dev/null | grep -q "^ECHO_API_TOKEN=pxh-ph-" && echo "$c"; done',
    )
    assert SERVER_PATH in holders
    files = _exec(provider, container["Id"], f"grep -rl {canary} /tmp /opt /etc /home /root /var 2>/dev/null | head -5; true")
    assert files.strip() == ""
    inspected = json.dumps(container)
    execs = [
        provider.client.api.exec_inspect(e) for e in container.get("ExecIDs") or []
    ]
    environments = _exec(provider, container["Id"], "cat /proc/[0-9]*/environ 2>/dev/null | tr '\\0' '\\n'")
    surfaces = [
        inspected,
        json.dumps(execs),
        environments,
        repr(req.spec),
        repr(egress_runner.runners()),
    ]
    surfaces += [r.getMessage() for r in caplog.records] + [
        repr(r.__dict__) for r in caplog.records
    ]
    assert not [s for s in surfaces if canary in s]
    assert not [s for s in surfaces if find_forbidden(s)]
    await rpc.aclose()


async def test_md6_an_idle_runner_is_reaped_and_the_next_call_restarts_it(
    mcp_runner: SandboxMCPRunner,
    mcp_env: DockerEnv,
    provider: DockerClientProvider,
    runner_namespace: str,
) -> None:
    req = request(mcp_env)
    rpc = await session(mcp_runner, req)
    first = _runner_container(provider, req.key.digest(), runner_namespace)["Id"]
    await rpc.aclose()
    mcp_env.advance(CONFIG.idle_ttl_s + 1)
    assert await mcp_runner.sweep() >= 1
    assert _containers(provider, runner_namespace, mcpRunnerKey=req.key.digest()) == []
    again = await session(mcp_runner, req)
    assert await again.call("echo", {"text": "restarted"}) == "restarted"
    assert (
        _runner_container(provider, req.key.digest(), runner_namespace)["Id"] != first
    )
    await again.aclose()


async def test_md7_users_get_their_own_runner_and_another_users_secret_is_refused(
    egress_runner: SandboxMCPRunner,
    egress_stack: EgressStack,
    clock: ManualClock,
    runner_image: str,
    provider: DockerClientProvider,
    runner_namespace: str,
) -> None:
    """Each user's runner carries that user's secret to the listed host, and only theirs (WP-06: through the proxy)."""
    mcp_runner = egress_runner
    mcp_env = DockerEnv(clock, runner_image, listed_upstream=f"http://{egress_stack.gateway}:8080")
    alice = await session(
        mcp_runner, request(mcp_env, "alice", secret="token-alice-0123456789")
    )
    bob = await session(
        mcp_runner, request(mcp_env, "bob", secret="token-bob-0123456789")
    )
    by_owner = {info.key.owner: info.sandbox_id for info in mcp_runner.runners()}
    assert set(by_owner) == {"alice", "bob"} and by_owner["alice"] != by_owner["bob"]
    assert await mcp_env.received_secret(bob) == digest("token-bob-0123456789")
    assert await mcp_env.received_secret(alice) == digest("token-alice-0123456789")
    with pytest.raises(RunnerOwnerMismatch):
        _ = await mcp_runner.open(
            request(mcp_env, "alice", principal="bob", secret="token-bob-0123456789")
        )
    assert len(_containers(provider, runner_namespace, mcpServer="srv-echo")) == 2
    await alice.aclose()
    await bob.aclose()
