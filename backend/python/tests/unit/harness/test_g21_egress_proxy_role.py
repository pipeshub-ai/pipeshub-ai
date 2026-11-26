"""SEC-EG-13 and SEC-ISO-09 in PipesHub's egress proxy role: the upstream egress settings reach the proxy, and the names of
PipesHub's own services (gateways, Node, broker, KV stores) and both sandbox pools are refused whatever is listed."""

from __future__ import annotations

import ipaddress
import time
from typing import Any

import pytest

from app.egress_proxy_main import create_service, refresh_upstreams
from app.harness.sandbox.egress_config import internal_hosts, pipeshub_internal_networks
from app.harness.sandbox.egress_resolver import PublicHostResolver
from harness_core.ids import RandomIds
from harness_core.sandbox.egress import StaticResolver
from harness_core.sandbox.egress.errors import HostUnresolvable
from harness_core.sandbox.egress.upstream_proxy import UpstreamEgressInvalid
from harness_core.testing.clock import ManualClock


class FakeConfig:
    def __init__(self, documents: dict[str, Any]) -> None:
        self.documents = documents

    async def get_config(self, key: str, *_: Any, **__: Any) -> Any:  # noqa: ANN401
        return self.documents.get(key)


def _config(model: str = "http://pipeshub-ai:8096/model") -> FakeConfig:
    return FakeConfig(
        {
            "/services/agentHarness/workerCredential": {
                "current": "cred-now",
                "previous": "cred-before",
                "previousValidUntil": time.time() * 1000 + 60_000,
            },
            "/services/endpoints": {
                "modelGateway": {"endpoint": model},
                "toolGateway": {"endpoint": "http://pipeshub-ai:8098/harness/tool-gateway"},
                "sandboxBroker": {"endpoint": "http://sandbox-broker:8095"},
                "egressProxy": {"controlEndpoint": "http://egress-proxy-control:8098"},
            },
        }
    )


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("EGRESS_UPSTREAM_PROXY", "EGRESS_UPSTREAM_PROXY_AUTH", "EGRESS_UPSTREAM_NO_PROXY", "EGRESS_PRIVATE_ALLOW"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AGENT_HARNESS_STATE_URL", "http://harness-state:3002")
    monkeypatch.setenv("REDIS_HOST", "redis")
    monkeypatch.setenv("ETCD_URL", "http://etcd:2379")


async def test_sec_iso_09_the_services_the_proxy_knows_by_name_are_internal() -> None:
    names = await internal_hosts(_config(), {"model-gateway": "http://pipeshub-ai:8096/model"})  # type: ignore[arg-type]
    assert names == (
        "http://pipeshub-ai:8096/model",
        "http://harness-state:3002",
        "http://sandbox-broker:8095",
        "http://egress-proxy-control:8098",
        "redis",
        "http://etcd:2379",
    )


async def test_sec_iso_09_their_addresses_and_both_sandbox_pools_are_refused_networks() -> None:
    resolver = StaticResolver(
        {"pipeshub-ai": ["10.20.0.7"], "harness-state": ["10.20.0.7"], "sandbox-broker": ["10.20.0.8"],
         "redis": ["10.21.0.2"], "etcd": ["10.21.0.3"]}
    )
    nets = await pipeshub_internal_networks(_config(), {"model-gateway": "http://pipeshub-ai:8096/model"}, resolver)  # type: ignore[arg-type]
    for expected in ("10.20.0.7/32", "10.20.0.8/32", "10.21.0.2/32", "10.21.0.3/32", "10.213.0.0/16", "10.212.0.0/16"):
        assert ipaddress.ip_network(expected) in nets, expected


async def test_sec_eg_13_the_role_reads_the_upstream_egress_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EGRESS_UPSTREAM_PROXY", "http://corp-proxy:3128")
    monkeypatch.setenv("EGRESS_UPSTREAM_PROXY_AUTH", "svc:secret-value-1")
    monkeypatch.setenv("EGRESS_PRIVATE_ALLOW", "git.corp:443")
    server = await create_service(_config(), clock=ManualClock(), ids=RandomIds())  # type: ignore[arg-type]
    upstream_egress = server.proxy.upstream_egress
    assert upstream_egress.upstream is not None and upstream_egress.upstream.url == "http://corp-proxy:3128"
    assert [str(e) for e in upstream_egress.private_allow] == ["git.corp:443"]
    assert "secret-value-1" not in repr(upstream_egress)


async def test_sec_eg_13_the_role_refuses_to_start_on_a_malformed_setting(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EGRESS_UPSTREAM_PROXY", "http://svc:secret-value-1@corp-proxy:3128")
    with pytest.raises(UpstreamEgressInvalid) as caught:
        _ = await create_service(_config(), clock=ManualClock(), ids=RandomIds())  # type: ignore[arg-type]
    assert "secret-value-1" not in str(caught.value)


async def test_sec_iso_09_a_refresh_re_reads_the_internal_addresses(monkeypatch: pytest.MonkeyPatch) -> None:
    server = await create_service(_config(), clock=ManualClock(), ids=RandomIds())  # type: ignore[arg-type]
    moved = (ipaddress.ip_network("10.99.0.1/32"),)

    async def internals(*_: object) -> tuple[ipaddress.IPv4Network, ...]:
        return moved  # type: ignore[return-value]

    monkeypatch.setattr("app.egress_proxy_main.pipeshub_internal_networks", internals)
    _ = await refresh_upstreams(server, _config())  # type: ignore[arg-type]
    assert moved[0] in server.proxy.refused_networks


async def test_sec_eg_13_the_private_resolver_answers_private_addresses_but_never_loopback_or_metadata() -> None:
    private = PublicHostResolver(allow_private=True)
    assert await private.resolve("10.1.2.3") == ("10.1.2.3",)
    assert await private.resolve("100.64.0.9") == ("100.64.0.9",)
    for refused in ("127.0.0.1", "169.254.169.254", "100.100.100.200", "localhost"):
        with pytest.raises(HostUnresolvable):  # AddressRefused is one
            _ = await private.resolve(refused)
    with pytest.raises(HostUnresolvable):
        _ = await PublicHostResolver().resolve("10.1.2.3")
