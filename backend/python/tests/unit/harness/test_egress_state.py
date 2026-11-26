"""ES on Redis, PR-3 (the proxy role's durable state) and PR-4 (P2.8's resolver for the proxy), P5.4b."""

from __future__ import annotations

import asyncio
import logging
import os
import socket
import time
import uuid
from typing import TYPE_CHECKING, Any

import fakeredis
import pytest
from fakeredis._socket import (
    _base as fakeredis_socket,  # pyright: ignore[reportPrivateUsage]
)

from app.egress_proxy_main import create_service
from app.harness.sandbox import egress_state
from app.harness.sandbox.egress_resolver import PublicHostResolver
from app.harness.sandbox.egress_state import (
    RedisEgressStateStore,
    redis_state_store,
    state_sealer,
)
from harness_core.ids import RandomIds
from harness_core.sandbox.egress import EgressBinding
from harness_core.sandbox.egress.errors import AddressRefused, HostUnresolvable
from harness_core.sandbox.egress.state import (
    DurableEgressBindings,
    InMemoryEgressStateStore,
    StateSealer,
)
from harness_core.sandbox.types import EgressDestination, EgressPolicy, TokenRoute
from harness_core.testing.clock import ManualClock
from harness_core.testing.sandbox.egress_contracts import (
    EgressStateStoreContract,
    HostResolverContract,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable


async def _elapse(seconds: float) -> None:
    await asyncio.sleep(seconds)


class _FakeRedisTime:
    """fakeredis stamps each command with ``time.time()`` read from its socket module, and expires keys against
    that stamp; standing in for that module's ``time`` lets a test move fakeredis's clock without sleeping."""

    def __init__(self) -> None:
        self._offset = 0.0

    def time(self) -> float:
        return time.time() + self._offset

    async def elapse(self, seconds: float) -> None:
        self._offset += seconds


class TestFakeRedisEgressStateStore(EgressStateStoreContract):
    @pytest.fixture
    def fake_redis_time(self, monkeypatch: pytest.MonkeyPatch) -> _FakeRedisTime:
        moved = _FakeRedisTime()
        monkeypatch.setattr(fakeredis_socket, "time", moved)
        return moved

    @pytest.fixture
    async def state_store(self, fake_redis_time: _FakeRedisTime) -> AsyncIterator[RedisEgressStateStore]:
        client = fakeredis.FakeAsyncRedis(decode_responses=True)
        yield RedisEgressStateStore(client, namespace=f"t-{uuid.uuid4().hex[:6]}:")
        await client.aclose()

    @pytest.fixture
    def state_elapse(self, fake_redis_time: _FakeRedisTime) -> Callable[[float], Awaitable[None]]:
        return fake_redis_time.elapse


@pytest.mark.integration
@pytest.mark.skipif(not os.environ.get("REDIS_IT_URL"), reason="needs a Redis (REDIS_IT_URL)")
class TestRealRedisEgressStateStore(EgressStateStoreContract):
    @pytest.fixture
    async def state_store(self) -> AsyncIterator[RedisEgressStateStore]:
        from redis.asyncio import Redis

        client = Redis.from_url(os.environ["REDIS_IT_URL"], decode_responses=True)  # pyright: ignore[reportUnknownMemberType]
        namespace = f"pxh-p54b-{uuid.uuid4().hex[:8]}:"
        yield RedisEgressStateStore(client, namespace=namespace)
        async for key in client.scan_iter(match=namespace + "*"):  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
            _ = await client.delete(key)  # pyright: ignore[reportUnknownArgumentType]
        await client.aclose()

    @pytest.fixture
    def state_elapse(self) -> Callable[[float], Awaitable[None]]:
        return _elapse


async def test_es_keys_share_one_hash_slot_under_the_namespace() -> None:
    client = fakeredis.FakeAsyncRedis(decode_responses=True)
    store = RedisEgressStateStore(client, namespace="ns:")
    await store.put("binding/a", "v", ttl_s=60)
    keys = [key async for key in client.scan_iter()]  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
    assert keys == ["ns:harness:egress:{default}:binding/a"]
    await client.aclose()


# --- PR-3 ------------------------------------------------------------------------------------------------

PH = "ph-main-0123456789abcdef"


class FakeConfig:
    async def get_config(self, key: str, *_: Any, **__: Any) -> Any:  # noqa: ANN401
        return {"/services/endpoints": {"modelGateway": {"endpoint": "http://gw:9"}}}.get(key)


async def test_pr3_the_role_restores_its_bindings_before_serving(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_HARNESS_STATE_URL", "http://127.0.0.1:1")
    clock = ManualClock()
    store, sealer = InMemoryEgressStateStore(clock), StateSealer(b"pr3-secret")
    first = await create_service(FakeConfig(), clock=clock, ids=RandomIds(), state=store, sealer=sealer)  # type: ignore[arg-type]
    assert isinstance(first.proxy.bindings, DurableEgressBindings)
    policy = EgressPolicy(
        destinations=(EgressDestination("model-gateway", (8097,)),),
        token_routes=(TokenRoute(PH, "model-gateway", "gateway:model"),),
    )
    await first.proxy.bindings.register(EgressBinding("sbx-1", "10.213.0.2", "10.213.0.3", policy, {"orgId": "o"}))
    second = await create_service(FakeConfig(), clock=clock, ids=RandomIds(), state=store, sealer=sealer)  # type: ignore[arg-type]
    assert await second.proxy.bindings.bound() == {"sbx-1"}
    assert second.instance == first.instance
    plain = await create_service(FakeConfig(), clock=clock, ids=RandomIds())  # type: ignore[arg-type]
    assert not isinstance(plain.proxy.bindings, DurableEgressBindings)


async def test_pr3_without_redis_or_a_key_state_stays_in_memory_and_says_so(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    async def unreachable(_: object) -> None:
        return None

    monkeypatch.setattr(egress_state, "harness_redis", unreachable)
    monkeypatch.setenv("SECRET_KEY", "8254c329a92850f6d539dd376f4816ee2764517da5e0235514af433164480d7a")
    with caplog.at_level(logging.WARNING, logger="app.harness"):
        assert await redis_state_store(FakeConfig()) is None  # type: ignore[arg-type]
        monkeypatch.delenv("SECRET_KEY")
        assert await redis_state_store(FakeConfig()) is None  # type: ignore[arg-type]
    reasons = [r.harness["reason"] for r in caplog.records if getattr(r, "harness_event", None) == "egress.state_unavailable"]  # type: ignore[attr-defined]
    assert reasons == ["redis_unreachable", "secret_key_missing"]
    assert state_sealer() is None


@pytest.fixture
def no_redis(monkeypatch: pytest.MonkeyPatch) -> None:
    async def unreachable(_: object) -> None:
        return None

    monkeypatch.setattr(egress_state, "harness_redis", unreachable)
    monkeypatch.delenv("EGRESS_STATE_REQUIRED", raising=False)
    monkeypatch.delenv("AGENT_HARNESS_STATE_URL", raising=False)
    monkeypatch.setenv("SECRET_KEY", "8254c329a92850f6d539dd376f4816ee2764517da5e0235514af433164480d7a")


def _required_logs(caplog: pytest.LogCaptureFixture) -> list[tuple[int, str]]:
    return [
        (r.levelno, r.harness["reason"])  # type: ignore[attr-defined]
        for r in caplog.records
        if getattr(r, "harness_event", None) in {"egress.state_required", "egress.state_unavailable"}
    ]


@pytest.mark.parametrize("missing", ["redis_unreachable", "secret_key_missing"])
async def test_h1_ep1_ep2_a_deployed_proxy_refuses_to_start_without_its_store(
    no_redis: None, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, missing: str
) -> None:
    monkeypatch.setenv("AGENT_HARNESS_STATE_URL", "http://harness-state:3000")
    if missing == "secret_key_missing":
        monkeypatch.delenv("SECRET_KEY")
    with caplog.at_level(logging.WARNING, logger="app.harness"), pytest.raises(egress_state.EgressStateUnavailable) as refused:
        await egress_state.durable_state(FakeConfig())  # type: ignore[arg-type]
    assert (refused.value.reason_code, refused.value.reason) == ("egress_state_unavailable", missing)
    assert _required_logs(caplog) == [(logging.ERROR, missing)]


async def test_h1_ep3_a_from_source_proxy_keeps_state_in_memory_as_before(
    no_redis: None, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING, logger="app.harness"):
        assert await egress_state.durable_state(FakeConfig()) == (None, None)  # type: ignore[arg-type]
    assert _required_logs(caplog) == [(logging.WARNING, "redis_unreachable")]


@pytest.mark.parametrize(
    ("url", "raw", "required"),
    [
        ("http://harness-state:3000", "false", False),
        (None, "true", True),
        (None, "", False),
        ("http://harness-state:3000", "", True),
        ("", "", False),
    ],
)
def test_h1_ep4_ep5_the_setting_overrides_what_the_address_says(
    monkeypatch: pytest.MonkeyPatch, url: str | None, raw: str, required: bool
) -> None:
    if url is None:
        monkeypatch.delenv("AGENT_HARNESS_STATE_URL", raising=False)
    else:
        monkeypatch.setenv("AGENT_HARNESS_STATE_URL", url)
    monkeypatch.setenv("EGRESS_STATE_REQUIRED", raw)
    assert egress_state.state_required() is required


async def test_h1_ep4_the_escape_hatch_keeps_a_deployed_proxy_in_memory(
    no_redis: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENT_HARNESS_STATE_URL", "http://harness-state:3000")
    monkeypatch.setenv("EGRESS_STATE_REQUIRED", "false")
    assert await egress_state.durable_state(FakeConfig()) == (None, None)  # type: ignore[arg-type]


async def test_h1_ep5_required_without_the_address_refuses_too(no_redis: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EGRESS_STATE_REQUIRED", "true")
    with pytest.raises(egress_state.EgressStateUnavailable):
        await egress_state.durable_state(FakeConfig())  # type: ignore[arg-type]


async def test_h1_ep6_with_redis_and_the_key_the_state_is_durable(monkeypatch: pytest.MonkeyPatch) -> None:
    client = fakeredis.FakeAsyncRedis(decode_responses=True)

    async def reachable(_: object) -> tuple[object, str]:
        return client, "ns:"

    monkeypatch.setattr(egress_state, "harness_redis", reachable)
    monkeypatch.setenv("SECRET_KEY", "8254c329a92850f6d539dd376f4816ee2764517da5e0235514af433164480d7a")
    monkeypatch.setenv("AGENT_HARNESS_STATE_URL", "http://harness-state:3000")
    store, sealer = await egress_state.durable_state(FakeConfig())  # type: ignore[arg-type]
    assert isinstance(store, RedisEgressStateStore) and isinstance(sealer, StateSealer)
    await client.aclose()


def test_h1_ep7_a_malformed_value_refuses_to_start(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.utils.env_flags import InvalidEnvFlag, validate_env_flags

    monkeypatch.setenv("EGRESS_STATE_REQUIRED", "always")
    with pytest.raises(InvalidEnvFlag, match="EGRESS_STATE_REQUIRED"):
        validate_env_flags()
    with pytest.raises(InvalidEnvFlag, match="EGRESS_STATE_REQUIRED"):
        egress_state.state_required()


async def test_pr3_with_redis_the_store_is_redis(monkeypatch: pytest.MonkeyPatch) -> None:
    client = fakeredis.FakeAsyncRedis(decode_responses=True)

    async def reachable(_: object) -> tuple[object, str]:
        return client, "ns:"

    monkeypatch.setattr(egress_state, "harness_redis", reachable)
    monkeypatch.setenv("SECRET_KEY", "8254c329a92850f6d539dd376f4816ee2764517da5e0235514af433164480d7a")
    store = await redis_state_store(FakeConfig())  # type: ignore[arg-type]
    assert isinstance(store, RedisEgressStateStore)
    assert isinstance(state_sealer(), StateSealer)
    await client.aclose()


# --- PR-4 ------------------------------------------------------------------------------------------------


class TestPublicHostResolver(HostResolverContract):
    @pytest.fixture
    def host_resolver(self) -> PublicHostResolver:
        return PublicHostResolver()

    @pytest.fixture
    def resolvable_host(self, dns: _FakeDns) -> str:
        dns.answers["api.example.test"] = "93.184.216.34"
        return "api.example.test"


class _FakeDns:
    """``socket.getaddrinfo`` for the names a test gives; NXDOMAIN for every other name, never the network."""

    def __init__(self) -> None:
        self.answers: dict[str, str] = {}
        self.asked: list[str] = []

    def getaddrinfo(self, host: str, *_: Any, **__: Any) -> Any:  # noqa: ANN401
        self.asked.append(host)
        if host not in self.answers:
            raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (self.answers[host], 0))]


@pytest.fixture
def dns(monkeypatch: pytest.MonkeyPatch) -> _FakeDns:
    fake = _FakeDns()
    monkeypatch.setattr(socket, "getaddrinfo", fake.getaddrinfo)
    return fake


@pytest.mark.parametrize(
    "host", ["localhost", "metadata.google.internal", "printer.local", "168.63.129.16", "100.100.100.200", "10.0.0.1",
             "169.254.169.254", "::1", "64:ff9b::a00:1"],
)
async def test_pr4_the_ssrf_policy_refuses_what_the_proxy_must_never_dial(host: str, dns: _FakeDns) -> None:
    dns.answers[host] = "93.184.216.34"
    with pytest.raises(AddressRefused):
        _ = await PublicHostResolver().resolve(host)
    assert dns.asked == [], "refused by policy before any lookup, whatever the name would answer"


async def test_pr4_a_name_answering_a_private_address_is_refused(dns: _FakeDns) -> None:
    dns.answers["rebind.example.test"] = "10.1.2.3"
    with pytest.raises(AddressRefused):
        _ = await PublicHostResolver().resolve("rebind.example.test")
    assert dns.asked == ["rebind.example.test"]


async def test_pr4_an_unknown_name_is_unresolvable_not_refused(dns: _FakeDns) -> None:
    with pytest.raises(HostUnresolvable) as caught:
        _ = await PublicHostResolver().resolve("no-such-host.invalid")
    assert not isinstance(caught.value, AddressRefused)
