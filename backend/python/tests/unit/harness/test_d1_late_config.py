"""D1: the broker and the proxy start before Node writes ``/services/endpoints`` (FB-2, FB-3), and read what Node writes (EN-3).

In Compose both containers start next to the app; Node writes the sandbox-plane
addresses only once it is up. A value read once at start would pin the broker to
the ``localhost`` fallback (another container) and leave the proxy with no
gateway, for the life of the process.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

import aiohttp
import pytest
from aiohttp import web

from app.egress_proxy_main import create_service, refresh_upstreams
from app.harness.sandbox.client import broker_endpoint
from app.harness.sandbox.egress_config import (
    ConfiguredEgressBindings,
    control_endpoint,
    gateway_upstreams,
)
from harness_core.ids import RandomIds
from harness_core.sandbox.auth import StaticBearerAuth
from harness_core.sandbox.egress import (
    EgressBinding,
    EgressProxy,
    InMemoryEgressBindings,
    InMemoryTokenSource,
    StaticResolver,
)
from harness_core.sandbox.egress.server import EgressServer
from harness_core.sandbox.errors import BrokerUnavailable
from harness_core.sandbox.types import EgressDestination, EgressPolicy, TokenRoute
from harness_core.testing.clock import ManualClock
from harness_core.testing.sandbox.egress_contracts import (
    EgressBindingsContract,
    EgressRevocationsContract,
)
from tests.unit.harness.store_outage import outage_configuration_service

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

ENDPOINTS = "/services/endpoints"
CREDENTIAL = "/services/agentHarness/workerCredential"
PH = "ph-d1-0123456789abcdef"

# The document Node's ConfigService.getSandboxPlaneEndpoints writes (cm.service.test.ts EN-1).
NODE_WRITES = {
    "queryBackend": {"endpoint": "http://localhost:8000"},
    "sandboxBroker": {"endpoint": "http://sandbox-broker:8095"},
    "egressProxy": {"controlEndpoint": "http://egress-proxy:8098"},
    "modelGateway": {"endpoint": "http://model-gateway:8096"},
    "toolGateway": {"endpoint": "http://tool-gateway:8099"},
}


class FakeConfig:
    def __init__(self, documents: dict[str, Any]) -> None:
        self.documents = documents

    async def get_config(self, key: str, *_: Any, **__: Any) -> Any:  # noqa: ANN401
        return self.documents.get(key)


async def test_en3_python_reads_the_addresses_node_writes() -> None:
    config = FakeConfig({ENDPOINTS: NODE_WRITES})
    assert await broker_endpoint(config) == "http://sandbox-broker:8095"  # type: ignore[arg-type]
    assert await control_endpoint(config) == "http://egress-proxy:8098"  # type: ignore[arg-type]
    assert await gateway_upstreams(config) == {  # type: ignore[arg-type]
        "model-gateway": "http://model-gateway:8096",
        "tool-gateway": "http://tool-gateway:8099",
    }


async def _serve(handler: Callable[[web.Request], Any]) -> tuple[web.AppRunner, str]:
    app = web.Application()
    _ = app.router.add_route("*", "/{tail:.*}", handler)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    return runner, f"http://127.0.0.1:{runner.addresses[0][1]}"


@pytest.fixture
async def two_control_planes() -> AsyncIterator[tuple[list[str], str, str]]:
    calls: list[str] = []

    def plane(name: str) -> Callable[[web.Request], Any]:
        async def handle(request: web.Request) -> web.Response:
            calls.append(f"{name} {request.method} {request.path}")
            return web.json_response({"sandboxIds": [name]})

        return handle

    first, first_url = await _serve(plane("a"))
    second, second_url = await _serve(plane("b"))
    yield calls, first_url, second_url
    await first.cleanup()
    await second.cleanup()


async def test_lc1_the_broker_uses_the_control_endpoint_written_after_it_started(
    two_control_planes: tuple[list[str], str, str],
) -> None:
    calls, first, second = two_control_planes
    config = FakeConfig({ENDPOINTS: {}})
    bindings = ConfiguredEgressBindings(config, StaticBearerAuth("cred"))  # type: ignore[arg-type]
    try:
        # Nothing written yet: the fallback is this container's own localhost, where no proxy listens.
        config.documents[ENDPOINTS] = {"egressProxy": {"controlEndpoint": "http://127.0.0.1:9"}}
        with pytest.raises(BrokerUnavailable):
            _ = await bindings.bound()
        config.documents[ENDPOINTS] = {"egressProxy": {"controlEndpoint": first + "/"}}
        assert await bindings.bound() == frozenset({"a"})
        config.documents[ENDPOINTS] = {"egressProxy": {"controlEndpoint": second}}
        assert await bindings.bound() == frozenset({"b"})
        assert bindings.endpoint == second
    finally:
        await bindings.aclose()
    assert calls == ["a GET /v1/bindings", "b GET /v1/bindings"]


@pytest.fixture
async def gateway() -> AsyncIterator[tuple[list[str], str]]:
    seen: list[str] = []

    async def handle(request: web.Request) -> web.Response:
        if request.path.endswith("/gateway-token"):
            # Node's gateway-token route (E2), which the proxy's token source asks since E0.
            return web.json_response({"token": "gw-token-from-node", "expiresAt": int(time.time() * 1000) + 600_000})
        if request.path.endswith("/egress"):
            # Node's egress-decision route (OB-03): the proxy reports the destination it let through.
            return web.json_response({"outcome": "recorded"})
        if request.path.endswith("/refusals"):
            # Node's refusal route (SEC2 O-2): the proxy reports the refused calls below to the same Node.
            return web.json_response({"outcome": "recorded"})
        seen.append(request.headers.get("Authorization", ""))
        return web.json_response({"ok": True})

    runner, url = await _serve(handle)
    yield seen, url
    await runner.cleanup()


async def test_lc2_the_proxy_forwards_to_a_gateway_configured_after_it_started(
    gateway: tuple[list[str], str], monkeypatch: pytest.MonkeyPatch
) -> None:
    seen, url = gateway
    monkeypatch.setenv("AGENT_HARNESS_STATE_URL", url)
    config = FakeConfig(
        {
            CREDENTIAL: {"current": "cred-now", "previousValidUntil": time.time() * 1000 - 1},
            ENDPOINTS: {"egressProxy": {"controlEndpoint": "http://egress-proxy:8098"}},
        }
    )
    server = await create_service(config, clock=ManualClock(), ids=RandomIds())  # type: ignore[arg-type]
    data_port, _ = await server.start(host="127.0.0.2", data_port=0, control_port=0, control_host="127.0.0.1")
    policy = EgressPolicy(
        destinations=(EgressDestination("model-gateway", (8097,)),),
        token_routes=(TokenRoute(PH, "model-gateway", "gateway:model"),),
    )
    labels = {"orgId": "o1", "runId": "run-1", "attemptId": "run-1.a1"}
    await server.proxy.bindings.register(EgressBinding("sbx-1", "127.0.0.2", "127.0.0.1", policy, labels, attestation="v1.broker-proof"))

    async def call() -> tuple[int, str | None]:
        async with aiohttp.ClientSession() as session, session.get(
            f"http://127.0.0.2:{data_port}/v1/models",
            headers={"Host": "model-gateway:8097", "Authorization": f"Bearer {PH}"},
        ) as response:
            return response.status, response.headers.get("X-Egress-Refusal")

    try:
        assert await refresh_upstreams(server, config) is False  # type: ignore[arg-type]
        # Not configured: the name is resolved like any host and refused (fail closed), never forwarded.
        status, refusal = await call()
        assert status in (403, 502) and refusal is not None
        assert seen == []

        config.documents[ENDPOINTS] = {**config.documents[ENDPOINTS], "modelGateway": {"endpoint": url}}
        assert await refresh_upstreams(server, config) is True  # type: ignore[arg-type]
        assert await refresh_upstreams(server, config) is False  # type: ignore[arg-type]
        assert await call() == (200, None)
        assert seen == ["Bearer gw-token-from-node"]

        del config.documents[ENDPOINTS]["modelGateway"]
        assert await refresh_upstreams(server, config) is True  # type: ignore[arg-type]
        status, refusal = await call()
        assert refusal is not None
        assert len(seen) == 1
    finally:
        await server.stop()


async def test_lc2_an_unreadable_store_keeps_the_current_table(gateway: tuple[list[str], str]) -> None:
    _, url = gateway
    config = FakeConfig({ENDPOINTS: {"modelGateway": {"endpoint": url}}})
    server = await create_service(config, clock=ManualClock(), ids=RandomIds())  # type: ignore[arg-type]

    class Broken:
        async def get_config(self, *_: Any, **__: Any) -> Any:  # noqa: ANN401
            raise ConnectionError("kv down")

    assert await refresh_upstreams(server, Broken()) is False  # type: ignore[arg-type]
    assert server.proxy.replace_upstreams({"model-gateway": url}) is False


async def test_fm_f3_a_store_outage_through_the_real_configuration_service_keeps_the_table(
    gateway: tuple[list[str], str],
) -> None:
    _, url = gateway
    config = FakeConfig({ENDPOINTS: {"modelGateway": {"endpoint": url}}})
    server = await create_service(config, clock=ManualClock(), ids=RandomIds())  # type: ignore[arg-type]
    assert await refresh_upstreams(server, outage_configuration_service()) is False
    assert server.proxy.replace_upstreams({"model-gateway": url}) is False


async def test_fm_g2_a_store_outage_never_pins_the_fallback_broker_address() -> None:
    with pytest.raises(ConnectionError):
        _ = await broker_endpoint(outage_configuration_service())


class TestConfiguredEgressBindings(EgressBindingsContract, EgressRevocationsContract):
    """QA19-9: the broker's production ``EgressBindings`` against a real proxy control plane, found where
    ``/services/endpoints`` names it."""

    @pytest.fixture
    async def egress_bindings(self) -> AsyncIterator[ConfiguredEgressBindings]:
        clock = ManualClock()
        proxy = EgressProxy(
            bindings=InMemoryEgressBindings(),
            tokens=InMemoryTokenSource(b"k", clock),
            resolver=StaticResolver(),
            upstreams={},
            clock=clock,
            ids=RandomIds(),
        )
        server = EgressServer(proxy, StaticBearerAuth("c"), instance="t")
        _, control_port = await server.start(host="127.0.0.1", data_port=0, control_port=0)
        config = FakeConfig({ENDPOINTS: {"egressProxy": {"controlEndpoint": f"http://127.0.0.1:{control_port}"}}})
        client = ConfiguredEgressBindings(config, StaticBearerAuth("c"))  # type: ignore[arg-type]
        yield client
        await client.aclose()
        await server.stop()
