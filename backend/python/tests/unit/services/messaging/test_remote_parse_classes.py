"""Remote parsing (``USE_PARSING_SERVICE=true``) with ``PARSE_ADMISSION=classes``: the remote places are ordered by class.

The index permit is class-ordered, then the parse step takes one place in the remote gate (one structure: the FIFO
slot pool is not entered with classes on, so nothing is held while waiting for the other).
"""
from __future__ import annotations

import asyncio
import logging
import time
from types import SimpleNamespace

import pytest

from app.modules.parse_engine import wiring
from app.services.messaging import consumer_concurrency as cc
from app.services.parsing import remote_concurrency as rc
from app.services.resource_governor.models import ParseTier, Pool
from tests.unit.services.messaging.governor_test_helpers import make_test_governor

MIB = 1 << 20


@pytest.fixture
def remote(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("USE_PARSING_SERVICE", "true")
    monkeypatch.setenv("PARSING_SERVICE_CONCURRENCY", "4")
    monkeypatch.setenv("PARSE_ADMISSION", "classes")
    monkeypatch.delenv("PARSE_LANES", raising=False)


def _host(index_limit: int = 16, lane: bool = False) -> SimpleNamespace:
    wiring.reset_for_tests()
    rc.reset_remote_parse_slots()
    governor = make_test_governor(logger_name="test.remote", cpu_quota=16.0)
    governor._registry.set(Pool.HEAVY_PARSE, 8)
    governor._registry.set(Pool.INDEX_HEAVY, index_limit)
    return SimpleNamespace(
        governor=governor, logger=logging.getLogger("test.remote"), priority_lane=SimpleNamespace() if lane else None,
        gate_waiters=cc.GateWaiters(),
    )


def _large(i: int) -> dict:
    return {"extension": "pdf", "mimeType": "application/pdf", "orgId": "o", "recordId": f"l{i}", "sizeInBytes": 150 * MIB}


def _small(i: int) -> dict:
    return {"extension": "xlsx", "mimeType": "", "orgId": "o", "recordId": f"s{i}", "sizeInBytes": 1 * MIB}


async def _record(host: SimpleNamespace, payload: dict, hold: float, out: list, kind: str, parse_host: SimpleNamespace | None = None) -> None:
    level = cc.dispatch_tier(host, SimpleNamespace(payload=payload))
    ctx = cc.class_context(host, payload)
    started = time.monotonic()
    index = await cc.acquire_index_slot(host, level, **cc.class_kwargs(ctx))
    cc.reclass_index_slot(index, ctx, payload["sizeInBytes"])
    try:
        parse = await cc.acquire_parsing_slot(
            parse_host or host, ParseTier.HEAVY, payload["sizeInBytes"], timeout=60.0, **cc.class_kwargs(ctx)
        )
        out.append((kind, time.monotonic() - started))
        await asyncio.sleep(hold)
        cc.release_admission(parse)
    finally:
        cc.release_admission(index)


async def _eight_large_then_twenty_small(host: SimpleNamespace, hold: float = 0.8) -> tuple[list[float], list[float]]:
    out: list = []
    tasks = [asyncio.create_task(_record(host, _large(i), hold, out, "large")) for i in range(8)]
    await asyncio.sleep(0.1)
    for i in range(20):
        tasks.append(asyncio.create_task(_record(host, _small(i), 0.02, out, "small")))
        await asyncio.sleep(0.02)
    await asyncio.wait_for(asyncio.gather(*tasks), 60)
    return sorted(w for k, w in out if k == "small"), sorted(w for k, w in out if k == "large")


def test_a_small_parse_does_not_wait_behind_large_ones_on_the_remote_places(remote: None) -> None:
    smalls, larges = asyncio.run(_eight_large_then_twenty_small(_host()))
    assert smalls[-1] < 0.3, smalls  # the FIFO pool made the median 1.76 s
    assert len(larges) == 8  # every large parse still runs


def test_without_classes_the_remote_pool_is_fifo_and_small_parses_queue(remote: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """The control (and the byte-identical off path): classes off keeps the FIFO pool."""
    monkeypatch.setenv("PARSE_ADMISSION", "legacy")
    smalls, _ = asyncio.run(_eight_large_then_twenty_small(_host()))
    assert smalls[len(smalls) // 2] > 0.5, smalls


def test_large_parses_keep_progressing_while_small_ones_flow(remote: None) -> None:
    """A large parse keeps its minimum share: with a steady stream of small ones all eight still finish."""

    async def run() -> int:
        host = _host()
        out: list = []
        tasks = [asyncio.create_task(_record(host, _large(i), 0.3, out, "large")) for i in range(8)]
        for i in range(80):
            tasks.append(asyncio.create_task(_record(host, _small(i), 0.05, out, "small")))
            await asyncio.sleep(0.02)
        await asyncio.wait_for(asyncio.gather(*tasks), 60)
        return sum(1 for kind, _ in out if kind == "large")

    assert asyncio.run(run()) == 8


def test_a_priority_lane_parse_is_not_starved_behind_queued_large_ones(remote: None) -> None:
    async def run() -> float:
        host = _host()
        out: list = []
        tasks = [asyncio.create_task(_record(host, _large(i), 0.8, out, "large")) for i in range(8)]
        await asyncio.sleep(0.1)
        lane_host = SimpleNamespace(governor=host.governor, logger=host.logger, priority_lane=SimpleNamespace(), gate_waiters=host.gate_waiters)
        tasks.append(asyncio.create_task(_record(host, _large(99), 0.02, out, "priority", parse_host=lane_host)))
        await asyncio.gather(*tasks)
        return next(w for kind, w in out if kind == "priority")

    # wall-clock-ok: about one large hold, against the queue of eight it must not wait behind
    assert asyncio.run(run()) < 2.0  # about one large hold, not the queue of eight


def test_many_mixed_records_with_few_remote_places_never_deadlock(remote: None, monkeypatch: pytest.MonkeyPatch) -> None:
    """No hold-and-wait between the index permit and the remote place: 60 mixed records on 2 places and an index pool of 4."""
    monkeypatch.setenv("PARSING_SERVICE_CONCURRENCY", "2")

    async def run() -> int:
        host = _host(index_limit=4)
        out: list = []
        tasks = [
            asyncio.create_task(_record(host, _large(i) if i % 3 == 0 else _small(i), 0.2 if i % 3 == 0 else 0.01, out, "x"))
            for i in range(60)
        ]
        await asyncio.wait_for(asyncio.gather(*tasks), 60)
        return len(out)

    assert asyncio.run(run()) == 60


def test_the_remote_place_is_the_parse_steps_only_place(remote: None) -> None:
    async def run() -> tuple[int, int]:
        host = _host()
        ctx = cc.class_context(host, _large(1))
        admission = await cc.acquire_parsing_slot(host, ParseTier.HEAVY, 150 * MIB, timeout=1, **cc.class_kwargs(ctx))
        fifo_free = rc.remote_parse_slots(4)._free
        gates = wiring.parse_engine_stats()["gates"]
        remote_running = sum(row["running"] for row in gates["remote_parse"].values())
        local_running = sum(row["running"] for row in gates.get("heavy_parse", {}).values())
        cc.release_admission(admission)
        return (remote_running, local_running + (4 - fifo_free))

    remote_running, elsewhere = asyncio.run(run())
    assert remote_running == 1 and elsewhere == 0  # one place in the remote gate, none in the FIFO pool or the local gate


def test_a_big_remote_parse_behind_a_full_gate_is_handed_back_after_the_big_wait_bound(
    remote: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The caller's own bound is far longer; PARSE_BIG_WAIT_SECONDS ends the wait, uncounted, with its first-wait time."""
    monkeypatch.setenv("PARSING_SERVICE_CONCURRENCY", "1")
    monkeypatch.setenv("PARSE_BIG_WAIT_SECONDS", "0.3")

    async def run() -> tuple[float, cc.ParseAdmissionTimeout]:
        host = _host()
        ctx = cc.class_context(host, _large(1))
        held = await cc.acquire_parsing_slot(host, ParseTier.HEAVY, 150 * MIB, timeout=5, ctx=ctx)
        started = time.monotonic()
        try:
            await cc.acquire_parsing_slot(host, ParseTier.HEAVY, 150 * MIB, timeout=60, ctx=ctx)
        except cc.ParseAdmissionTimeout as err:
            took = time.monotonic() - started
            cc.release_admission(held)
            return took, err
        raise AssertionError("the second large parse was admitted")

    took, err = asyncio.run(run())
    assert isinstance(err, cc.BigAdmissionTimeout)
    assert err.counts_toward_backstop is False and err.parse_enqueued_at_ms is not None
    # wall-clock-ok: 0.3 s plus a poll, against the caller's 60 s it must not wait out
    assert took < 5.0  # 0.3 s plus a poll, not the caller's 60 s
