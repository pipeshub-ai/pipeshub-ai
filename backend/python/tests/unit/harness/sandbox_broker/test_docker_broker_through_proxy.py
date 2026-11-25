"""R38IDENT-FU-1: the Docker broker with no socket of its own, every call through the policy proxy's broker profile.

The whole broker contract (SB-1..SB-30, egress cases included) runs against a broker whose ``DOCKER_HOST`` is an
in-process ``app.sandbox.docker_proxy`` holding ``BrokerApiPolicy``, with the broker's own credential, so every call
the broker makes on a real daemon is one the profile admits. Then the proxy is asked, with that credential, for what a
foothold in the broker would ask for, and the daemon is checked to have made none of it.
"""

from __future__ import annotations

import asyncio
import secrets
import threading
import uuid
from typing import TYPE_CHECKING

import pytest

from app.harness.sandbox.docker_broker import DockerSandboxBroker, probe
from app.harness.sandbox.docker_client import DockerClientProvider
from app.sandbox.docker_proxy import ENV_BROKER_CALLER_TOKEN, DockerSocketProxy
from app.sandbox.docker_proxy_broker import BrokerApiPolicy, BrokerScope
from harness_core.ids import RandomIds
from harness_core.sandbox.types import RuntimeMode
from harness_core.testing.sandbox import SandboxBrokerContract
from tests.unit.agent_loop_lib.sandbox.contract.conftest import (
    DOCKER_TEST_IMAGE,
    skip_without_docker,
    skip_without_runsc,
)
from tests.unit.harness.sandbox_broker.conftest import (
    cleanup,
    cleanup_networks,
    egress_wiring,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from harness_core.sandbox.egress.client import RemoteEgressBindings
    from harness_core.testing.clock import ManualClock
    from tests.unit.harness.sandbox_broker.egress_stack import (
        DockerEgressContractEnv,
        EgressStack,
    )

pytestmark = [pytest.mark.docker, pytest.mark.timeout(300, method="thread")]


@pytest.fixture
def install() -> str:
    return f"pxh-r43sock-{uuid.uuid4().hex[:8]}"


def _serve(scope: BrokerScope, token: str) -> tuple[str, asyncio.AbstractEventLoop, asyncio.Server, threading.Thread]:
    """The proxy on its own loop and thread: the broker's blocking docker-py calls and its asyncio exec streams both
    reach it while the test's loop waits on them."""
    loop = asyncio.new_event_loop()
    proxy = DockerSocketProxy(None, "/var/run/docker.sock", callers={token: BrokerApiPolicy(scope)})
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    # Started on its own loop: the fixture runs inside the test's.
    server = asyncio.run_coroutine_threadsafe(asyncio.start_server(proxy.handle, "127.0.0.1", 0), loop).result(10)
    return f"tcp://127.0.0.1:{server.sockets[0].getsockname()[1]}", loop, server, thread


def _proxied_provider(monkeypatch: pytest.MonkeyPatch, scope: BrokerScope) -> tuple[DockerClientProvider, object]:
    token = secrets.token_hex(32)
    host, loop, server, thread = _serve(scope, token)
    monkeypatch.setenv("DOCKER_HOST", host)
    monkeypatch.setenv(ENV_BROKER_CALLER_TOKEN, token)

    def stop() -> None:
        loop.call_soon_threadsafe(server.close)
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=5)

    return DockerClientProvider(max_workers=16, caller_token_env=ENV_BROKER_CALLER_TOKEN), stop


@pytest.fixture
async def raw_provider() -> AsyncIterator[DockerClientProvider]:
    """The test's own client on the socket, for teardown and for checking what the daemon holds."""
    skip_without_docker()
    provider = DockerClientProvider(max_workers=4)
    _ = provider.client
    yield provider
    provider.close()


@pytest.fixture
async def docker_broker(
    raw_provider: DockerClientProvider,
    monkeypatch: pytest.MonkeyPatch,
    clock: ManualClock,
    namespace: str,
    volume_prefix: str,
    runtime_mode: RuntimeMode,
    install: str,
) -> AsyncIterator[DockerSandboxBroker]:
    if runtime_mode is RuntimeMode.GVISOR and RuntimeMode.GVISOR not in (await probe(raw_provider)).modes():
        skip_without_runsc()
    provider, stop = _proxied_provider(
        monkeypatch, BrokerScope(install=install, namespace=namespace, volume_prefix=volume_prefix)
    )
    broker = await DockerSandboxBroker.create(
        provider=provider, clock=clock, ids=RandomIds(), allowed_images=frozenset({DOCKER_TEST_IMAGE}),
        label_namespace=namespace, volume_prefix=volume_prefix, install_id=install,
    )
    assert broker.capabilities.exec_transport == "tcp"
    yield broker
    provider.close()
    stop()  # type: ignore[operator]
    await raw_provider.run_blocking(cleanup, raw_provider, namespace, volume_prefix)


@pytest.fixture
async def egress_docker_broker(
    egress_stack: EgressStack,
    egress_bindings: RemoteEgressBindings,
    raw_provider: DockerClientProvider,
    monkeypatch: pytest.MonkeyPatch,
    clock: ManualClock,
    egress_namespace: str,
    egress_prefix: str,
    runtime_mode: RuntimeMode,
    install: str,
) -> AsyncIterator[DockerSandboxBroker]:
    if runtime_mode is RuntimeMode.GVISOR and RuntimeMode.GVISOR not in (await probe(raw_provider)).modes():
        skip_without_runsc()
    scope = BrokerScope(
        install=install, namespace=egress_namespace, volume_prefix=egress_prefix, proxy_container=egress_stack.proxy
    )
    provider, stop = _proxied_provider(monkeypatch, scope)
    broker = await DockerSandboxBroker.create(
        provider=provider, clock=clock, ids=RandomIds(), allowed_images=frozenset({DOCKER_TEST_IMAGE}),
        label_namespace=egress_namespace, volume_prefix=egress_prefix, install_id=install,
        egress=egress_wiring(provider, clock, egress_stack, egress_bindings, egress_prefix),
    )
    yield broker
    provider.close()
    stop()  # type: ignore[operator]
    await raw_provider.run_blocking(cleanup, raw_provider, egress_namespace, egress_prefix)
    await raw_provider.run_blocking(cleanup_networks, raw_provider, egress_namespace, egress_stack.proxy)


@pytest.fixture
def broker(docker_broker: DockerSandboxBroker) -> DockerSandboxBroker:
    return docker_broker


class TestDockerBrokerThroughTheProxy(SandboxBrokerContract):
    @pytest.fixture
    def egress_broker(self, egress_docker_broker: DockerSandboxBroker) -> DockerSandboxBroker:
        return egress_docker_broker

    @pytest.fixture
    def egress_env(self, docker_egress_env: DockerEgressContractEnv) -> DockerEgressContractEnv:
        return docker_egress_env


@pytest.mark.parametrize("runtime_mode", [RuntimeMode.DOCKER_HARDENED], ids=["runc"])
async def test_a_foothold_in_the_broker_gets_nothing_of_the_host(
    docker_broker: DockerSandboxBroker, raw_provider: DockerClientProvider, namespace: str, install: str
) -> None:
    """With the broker's own credential, the asks of an attacker who runs code in the broker: every one refused by
    the proxy (403), and the daemon holds no container of them afterwards."""
    import docker

    api = docker_broker._provider.client.api
    own = {f"{namespace}.managed": "1", f"{namespace}.install": install}
    floor = {"read_only": True, "cap_drop": ["ALL"], "security_opt": ["no-new-privileges:true"], "pids_limit": 16,
             "mem_limit": 2**26, "memswap_limit": 2**26, "nano_cpus": 10**8, "network_mode": "none"}
    attempts = {
        "privileged": {**floor, "privileged": True},
        "bind of /": {**floor, "binds": ["/:/host:ro"]},
        "host network": {**floor, "network_mode": "host"},
        "host pid": {**floor, "pid_mode": "host"},
        "SYS_ADMIN": {**floor, "cap_add": ["SYS_ADMIN"]},
        "docker socket": {**floor, "mounts": [docker.types.Mount("/var/run/docker.sock", "/var/run/docker.sock",
                                                                    type="bind")]},
    }
    for what, host in attempts.items():
        with pytest.raises(docker.errors.APIError) as refused:
            api.create_container(DOCKER_TEST_IMAGE, ["true"], user="1000:1000", labels={**own, "attempt": what},
                                 host_config=api.create_host_config(**host))
        assert refused.value.response.status_code == 403, what
    with pytest.raises(docker.errors.APIError) as refused:
        api.create_volume(name=f"{docker_broker._volume_prefix}-hostbind", driver="local",
                          driver_opts={"type": "none", "device": "/", "o": "bind"}, labels=own)
    assert refused.value.response.status_code == 403
    # A container on the daemon that is not the install's sandbox (as the app or a store would be): no exec, no
    # archive, no removal through the broker's credential.
    other = raw_provider.client.api.create_container(
        DOCKER_TEST_IMAGE, ["true"], name=f"{install}-not-a-sandbox", labels={f"{namespace}.managed": "1"})["Id"]
    try:
        for call in (lambda: api.exec_create(other, ["id"], user="1000:1000"),
                     lambda: api.put_archive(other, "/tmp", b""), lambda: api.remove_container(other, force=True)):
            with pytest.raises(docker.errors.APIError) as refused:
                call()
            assert refused.value.response.status_code == 403
    finally:
        raw_provider.client.api.remove_container(other, force=True)
    made = raw_provider.client.api.containers(all=True, filters={"label": ["attempt"]})
    assert [c for c in made if (c.get("Labels") or {}).get(f"{namespace}.install") == install] == []
