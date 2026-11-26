"""Remote parse fan-out: the indexing side is sized by PARSING_SERVICE_CONCURRENCY, and the
parsing service answers 429 + Retry-After when saturated, with its load on /health and /metrics."""
from __future__ import annotations

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.api.middlewares.auth import authMiddleware
from app.api.routes.parsing import router as parsing_router
from app.models.blocks import BlocksContainer
from app.services.messaging import consumer_concurrency as concurrency
from app.services.parsing.client import ParsingClient
from app.services.parsing.interface import ParseResult, ParserProvider
from app.services.parsing.load import (
    ParseLoad,
    parse_load,
    parsing_max_concurrent,
    render_metrics,
)
from app.services.parsing.registry import ParserRegistry
from app.services.parsing.remote_concurrency import (
    lift_ceilings,
    remote_parse_concurrency,
    reset_remote_parse_slots,
)
from app.services.resource_governor.controller import ResourceGovernor
from app.services.resource_governor.models import ParseTier, ResourceSnapshot
from tests.support.settle import SETTLE_S


class _TwoCpuProbe:
    def snapshot(self) -> ResourceSnapshot:
        return ResourceSnapshot(
            cpu_quota=2.0, cpu_utilisation=0.1, cpu_throttled_ratio=0.0, cpu_pressure=0.0,
            mem_limit_bytes=16 * 1024**3, mem_working_set_bytes=1024**3, source="stub",
        )


def _governor(**kwargs: object) -> ResourceGovernor:
    return ResourceGovernor(logger=logging.getLogger("t"), probe=_TwoCpuProbe(), **kwargs)  # type: ignore[arg-type]


class _Host:
    """The slice of a consumer ``acquire_parsing_slot`` reads."""

    def __init__(self, governor: ResourceGovernor | None) -> None:
        self.governor = governor
        self.parsing_semaphore = asyncio.Semaphore(2)
        self.priority_lane = None


@pytest.fixture(autouse=True)
def _clean(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("USE_PARSING_SERVICE", "PARSING_SERVICE_CONCURRENCY", "PARSING_MAX_CONCURRENT"):
        monkeypatch.delenv(name, raising=False)
    reset_remote_parse_slots()
    yield
    reset_remote_parse_slots()


# --- config -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("use", "value", "expected"),
    [
        (None, "16", None),            # parsing is local: the value is ignored
        ("false", "16", None),
        ("true", None, None),          # default: today's behaviour
        ("true", "", None),
        ("true", "abc", None),
        ("true", "0", None),
        ("true", "-3", None),
        ("true", "16", 16),
    ],
)
def test_remote_concurrency_is_read_only_when_parsing_is_remote(monkeypatch, use, value, expected) -> None:
    if use is not None:
        monkeypatch.setenv("USE_PARSING_SERVICE", use)
    if value is not None:
        monkeypatch.setenv("PARSING_SERVICE_CONCURRENCY", value)
    assert remote_parse_concurrency() == expected


# --- indexing side ----------------------------------------------------------


async def test_remote_concurrency_is_not_capped_by_local_cpus(monkeypatch) -> None:
    monkeypatch.setenv("USE_PARSING_SERVICE", "true")
    monkeypatch.setenv("PARSING_SERVICE_CONCURRENCY", "16")
    host = _Host(_governor(remote_parse_slots=16))
    assert host.governor.ceilings.heavy >= 16  # type: ignore[union-attr]

    held = [await concurrency.acquire_parsing_slot(host, ParseTier.HEAVY, 1024, timeout=0.5) for _ in range(16)]
    assert len(held) == 16
    with pytest.raises(concurrency.ParseAdmissionTimeout):
        await concurrency.acquire_parsing_slot(host, ParseTier.HEAVY, 1024, timeout=0.05)
    held[0]._release()
    again = await concurrency.acquire_parsing_slot(host, ParseTier.LIGHT, 10, timeout=0.5)
    assert again.tier is ParseTier.LIGHT


async def test_without_the_setting_two_cpus_hold_far_fewer_than_sixteen(monkeypatch) -> None:
    monkeypatch.setenv("USE_PARSING_SERVICE", "true")
    host = _Host(_governor())
    admitted = 0
    for _ in range(16):
        try:
            await concurrency.acquire_parsing_slot(host, ParseTier.HEAVY, 1024, timeout=0.05)
        except concurrency.ParseAdmissionTimeout:
            break
        admitted += 1
    assert 1 <= admitted < 16


def test_index_permits_follow_the_remote_concurrency() -> None:
    plain = _governor()
    lifted = _governor(remote_parse_slots=16)
    assert lifted.ceilings.index_heavy >= 32 > plain.ceilings.index_heavy
    assert lifted.ceilings.index_light >= 32
    assert lifted.fleet_ceilings.heavy == lifted.heavy_parse_lease_limit >= 16


def test_an_explicit_index_cap_still_holds_after_the_lift() -> None:
    """MAX_CONCURRENT_INDEXING is the operator's total in-flight budget; remote capacity must not exceed it."""
    for cap in (1, 5, 24):
        c = _governor(remote_parse_slots=16, env_index=cap).ceilings
        assert c.index_heavy + c.index_light <= cap, (cap, c)
        assert c.heavy <= max(1, c.index_heavy)
    one = _governor(remote_parse_slots=16, env_index=1).ceilings
    assert (one.index_heavy, one.index_light) == (1, 0)


def test_default_governor_ceilings_are_unchanged() -> None:
    assert _governor(remote_parse_slots=None).ceilings == _governor().ceilings
    c = _governor().ceilings
    assert lift_ceilings(c, 1) == c  # never lowers


def test_client_pool_matches_the_concurrency(monkeypatch) -> None:
    assert ParsingClient(service_url="http://p:8092")._limits.max_connections == 10
    monkeypatch.setenv("USE_PARSING_SERVICE", "true")
    monkeypatch.setenv("PARSING_SERVICE_CONCURRENCY", "16")
    client = ParsingClient(service_url="http://p:8092")
    assert client._limits.max_connections == 16
    assert client._limits.max_keepalive_connections == 16
    monkeypatch.setenv("PARSING_SERVICE_CONCURRENCY", "4")
    assert ParsingClient(service_url="http://p:8092")._limits.max_connections == 10


async def test_client_waits_out_a_saturated_service_and_the_parse_succeeds(monkeypatch) -> None:
    """429 + Retry-After is backpressure: the client retries on its own backpressure budget."""
    calls = {"n": 0}
    ok = {"success": True, "block_container": {"blocks": [], "block_groups": []}, "provider_used": "default"}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] <= 3:
            return httpx.Response(429, headers={"Retry-After": "0"}, json={"success": False})
        return httpx.Response(200, json=ok)

    client = ParsingClient(service_url="http://p:8092", max_retries=1, retry_delay=0.0)
    client._make_client = lambda: httpx.AsyncClient(transport=httpx.MockTransport(handler))  # type: ignore[method-assign]
    with patch.object(client, "_auth_headers", AsyncMock(return_value={}), create=True):
        result = await client.parse(b"x", "a.txt", mime_type="text/plain", extension="txt")
    assert calls["n"] == 4, "three 429s were waited out, none counted as a failure"
    assert result.block_container is not None
    assert not client.circuit_open


# --- parsing service --------------------------------------------------------


async def _authed(request: Request) -> Request:
    request.state.user = {"token_type": "scoped", "scopes": ["document:parse"], "orgId": "org-1"}
    return request


def _service() -> tuple[TestClient, asyncio.Event]:
    gate_open = asyncio.Event()
    parser = MagicMock()
    parser.parse = AsyncMock(return_value=ParseResult(
        block_container=BlocksContainer(blocks=[], block_groups=[]),
        provider_used=ParserProvider.DEFAULT, metadata={},
    ))
    registry = MagicMock(spec=ParserRegistry)
    registry.resolve = MagicMock(return_value=parser)
    app = FastAPI()
    app.state.parser_registry = registry
    app.state.governor = MagicMock()
    app.state.governor.gate.return_value = MagicMock(limit=5, in_use=0)
    app.include_router(parsing_router)
    app.dependency_overrides[authMiddleware] = _authed
    return TestClient(app), gate_open


def _post(client: TestClient) -> httpx.Response:
    return client.post(
        "/api/v1/parse",
        files={"file": ("a.py", b"x = 1\n", "text/x-python")},
        data={"record_name": "a.py", "extension": "py", "provider": "default"},
    )


def test_saturated_service_answers_429_with_retry_after_at_once(monkeypatch) -> None:
    client, _ = _service()
    monkeypatch.setenv("PARSING_MAX_CONCURRENT", "2")
    load = ParseLoad()
    assert load.try_enter(2) and load.try_enter(2)  # two parses already in flight
    acquire = AsyncMock(return_value=True)
    with patch("app.api.routes.parsing.parse_load", return_value=load), \
            patch("app.api.routes.parsing.acquire_gate_with_backpressure", acquire):
        response = _post(client)
    assert response.status_code == 429
    assert int(response.headers["Retry-After"]) >= 1
    assert response.json()["error"]["code"] == "PARSE_BACKPRESSURE"
    acquire.assert_not_awaited()  # shed before any queueing
    assert load.snapshot()["saturated_total"] == 1
    assert load.snapshot()["in_flight"] == 2, "the refused request left nothing in flight"


def test_below_the_limit_parses_and_leaves_nothing_in_flight(monkeypatch) -> None:
    client, _ = _service()
    monkeypatch.setenv("PARSING_MAX_CONCURRENT", "2")
    load = ParseLoad()
    with patch("app.api.routes.parsing.parse_load", return_value=load), \
            patch("app.api.routes.parsing.acquire_gate_with_backpressure", AsyncMock(return_value=True)):
        assert _post(client).status_code == 200
    snap = load.snapshot()
    assert (snap["in_flight"], snap["saturated_total"], snap["requests_total"]) == (0, 0, 1)


def test_unset_limit_never_sheds_and_a_gate_timeout_still_counts(monkeypatch) -> None:
    client, _ = _service()
    assert parsing_max_concurrent() is None
    load = ParseLoad()
    with patch("app.api.routes.parsing.parse_load", return_value=load), \
            patch("app.api.routes.parsing.acquire_gate_with_backpressure", AsyncMock(return_value=False)):
        response = _post(client)
    assert response.status_code == 429  # today's path, unchanged
    assert load.snapshot()["saturated_total"] == 1
    assert load.snapshot()["in_flight"] == 0


def test_in_flight_is_released_when_the_parse_raises() -> None:
    client, _ = _service()
    load = ParseLoad()
    with patch("app.api.routes.parsing.parse_load", return_value=load), \
            patch("app.api.routes.parsing.acquire_gate_with_backpressure", AsyncMock(side_effect=RuntimeError("x"))):
        with pytest.raises(RuntimeError):
            _post(client)
    assert load.snapshot()["in_flight"] == 0


def test_metrics_text_format(monkeypatch) -> None:
    monkeypatch.setenv("PARSING_MAX_CONCURRENT", "8")
    load = ParseLoad()
    load.try_enter(8)
    load.try_enter(1)  # refused
    text = render_metrics(load)
    assert text.endswith("\n")
    values = {}
    for line in text.splitlines():
        if line.startswith("# TYPE "):
            _, _, name, kind = line.split()
            assert kind in {"gauge", "counter"}
            values[name] = None
        elif not line.startswith("#"):
            name, value = line.split(" ")
            assert name in values, "every sample follows its TYPE line"
            values[name] = int(value)
    assert values == {
        "pipeshub_parsing_capacity": 8,
        "pipeshub_parsing_saturated_total": 1,
        "pipeshub_parsing_requests_total": 1,
        "pipeshub_parse_stuck_conversions": 0,
        "pipeshub_parse_stuck_oldest_seconds": 0,
    }


def test_metrics_text_exports_no_in_flight_gauge() -> None:
    """The in-flight count stays in the process (it drives the 429 and /health); it is not a metric."""
    load = ParseLoad()
    load.try_enter(8)
    assert "in_flight" not in render_metrics(load)
    assert load.snapshot()["in_flight"] == 1


def test_capacity_falls_back_to_the_governor_ceiling(monkeypatch) -> None:
    assert "pipeshub_parsing_capacity 6\n" in render_metrics(ParseLoad(), default_capacity=6)
    monkeypatch.setenv("PARSING_MAX_CONCURRENT", "3")
    assert "pipeshub_parsing_capacity 3\n" in render_metrics(ParseLoad(), default_capacity=6)
    monkeypatch.delenv("PARSING_MAX_CONCURRENT")
    assert "pipeshub_parsing_capacity 0\n" in render_metrics(ParseLoad())


def test_metrics_and_health_endpoints_on_the_parsing_app() -> None:
    from app import parsing_main

    client = TestClient(parsing_main.app)
    response = client.get("/metrics")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert "pipeshub_parsing_capacity " in response.text
    health_load = parse_load().snapshot()
    assert set(health_load) == {"in_flight", "max_concurrent", "saturated_total", "requests_total"}


# --- priority lane on the process's remote slots -----------------------------


async def test_a_priority_parse_is_served_ahead_of_queued_bulk_parses() -> None:
    from app.services.parsing.remote_concurrency import RemoteParseSlots

    slots = RemoteParseSlots(1)
    assert await slots.acquire(1)                       # one bulk parse holds the only slot
    order: list[str] = []

    async def take(name: str, priority: bool) -> None:
        assert await slots.acquire(5, priority=priority)
        order.append(name)
        slots.release()

    bulk = [asyncio.create_task(take(f"bulk{i}", False)) for i in range(2)]
    await asyncio.sleep(0)
    interactive = asyncio.create_task(take("interactive", True))
    await asyncio.sleep(0)
    slots.release()
    await asyncio.gather(*bulk, interactive)
    assert order[0] == "interactive"
    assert order[1:] == ["bulk0", "bulk1"]


async def test_the_remote_slots_never_exceed_their_size_with_priority_waiters() -> None:
    from app.services.parsing.remote_concurrency import RemoteParseSlots

    slots = RemoteParseSlots(2)
    assert await slots.acquire(1) and await slots.acquire(1)
    assert not await slots.acquire(0.05, priority=True)  # full: a priority waiter times out, it does not overshoot
    slots.release()
    assert await slots.acquire(0.5, priority=True)
    assert not await slots.acquire(0.05)
    assert slots._free == 0


async def test_a_waiter_that_timed_out_leaves_no_permit_behind() -> None:
    from app.services.parsing.remote_concurrency import RemoteParseSlots

    slots = RemoteParseSlots(1)
    assert await slots.acquire(1)
    assert not await slots.acquire(0.02)
    slots.release()
    assert slots._free == 1 and not slots._waiters


async def test_acquire_parsing_slot_gives_the_priority_lane_the_queue_jump(monkeypatch) -> None:
    monkeypatch.setenv("USE_PARSING_SERVICE", "true")
    monkeypatch.setenv("PARSING_SERVICE_CONCURRENCY", "1")
    bulk_host = _Host(None)
    lane_host = _Host(None)
    lane_host.priority_lane = concurrency.PriorityLane(heavy_limit=1, light_limit=3)
    first = await concurrency.acquire_parsing_slot(bulk_host, ParseTier.HEAVY, 1, timeout=1)
    queued_bulk = asyncio.create_task(concurrency.acquire_parsing_slot(bulk_host, ParseTier.HEAVY, 1, timeout=2))
    await asyncio.sleep(0.01)
    interactive = asyncio.create_task(concurrency.acquire_parsing_slot(lane_host, ParseTier.LIGHT, 1, timeout=2))
    await asyncio.sleep(0.01)
    first._release()
    got = await asyncio.wait_for(interactive, SETTLE_S)
    assert not queued_bulk.done()
    got._release()
    (await asyncio.wait_for(queued_bulk, SETTLE_S))._release()
