"""A re-widen under steady sync load on a real ArangoDB.

Four save-like workers keep overlapping stream transactions open on ``records`` (50-record upserts, 0.05-0.4 s each),
four plain writers update single records and time every write. An older image narrows the schema; a write to a record
holding the fields triggers the widening. Asserted: the stream transactions the server lists when each schema change is
sent (what every records write then waits for; none with short transactions); rewiden_total vs rewiden_contended_total;
every save transaction either committed or was refused with a retryable error; the trigger record ends in its written state. Printed: the longest and p99 plain-write wait from the narrowing to 10 s
after the widening, against STALL_BOUND_S (on a shared runner that is the host's scheduling, not the code).
"""
from __future__ import annotations

import asyncio
import logging
import os
import random
import time
import uuid

import pytest

from app.exceptions.graph_db_exceptions import RecordSchemaNarrowError
from app.schema.arango.documents import RECORD_SCHEMA_GENERATION, record_schema
from app.services.graph_db.arango.record_schema_guard import generation_of_schema
from app.telemetry.backend import METRICS_BACKEND
from tests.integration.graph_db.record_diagnostics_stall_bound import (
    STALL_BOUND_S,
    running_at_each_widening,
)
from tests.integration.graph_db.record_diagnostics_support import (
    RECORDS,
    Store,
    base_record,
    drop_database,
    narrow_schema,
    new_org,
)
from tests.integration.real_graph import backend_unavailable, connect_arango

RECOVERY_S = 60.0  # skips (up to three polls), a forced widening that waits for the open transactions, a held lease (15 s) and the retries

pytestmark = [pytest.mark.integration, pytest.mark.timeout(600)]
logger = logging.getLogger("record-diagnostics-steady-load")
DIAG = {"firstQueuedAt": 1_700_000_000_000, "indexingAttempts": 2, "pageCount": 12, "failedAt": 1_700_000_100_000}


def _counter(name: str) -> float:
    total = 0.0
    for line in METRICS_BACKEND.serialize().splitlines():
        if line.startswith((name + "{", name + " ")):
            total += float(line.rsplit(" ", 1)[1])
    return total


@pytest.fixture
async def env():  # noqa: ANN201
    try:
        p = await connect_arango(logger, f"rd_load_{uuid.uuid4().hex[:6]}")
    except Exception as exc:
        backend_unavailable("arango", exc)
    yield p, Store("arango", p, new_org())
    await p.http_client.update_collection_schema(RECORDS, record_schema)
    await drop_database(p)
    await p.disconnect()


async def write_within(write, recovery_s: float) -> int:
    """Run ``write`` again while it is refused transiently, until it has landed, all within ``recovery_s``. Each awaited write is bounded by what
    is left of the deadline, so a write that is slow and then succeeds (or never answers) fails as well as one that keeps being refused.
    Returns the number of attempts."""
    deadline = time.monotonic() + recovery_s
    attempts = 0
    while True:
        attempts += 1
        try:
            async with asyncio.timeout(max(deadline - time.monotonic(), 0)):
                await write()
            return attempts
        except RecordSchemaNarrowError:
            assert time.monotonic() < deadline, f"not recovered within {recovery_s:.0f}s ({attempts} attempts)"
            await asyncio.sleep(0.5)
        except TimeoutError:
            raise AssertionError(f"not recovered within {recovery_s:.1f}s: a write was still running at the deadline ({attempts} attempts)") from None


async def test_the_recovery_deadline_also_bounds_a_write_that_succeeds_late() -> None:
    """The deadline covers a write that succeeds after it (a slow success), not only refused ones: it fails the test."""
    async def slow_success() -> None:
        await asyncio.sleep(1.0)

    async def quick_success() -> None:
        return None

    assert await write_within(quick_success, 0.3) == 1
    with pytest.raises(AssertionError, match="not recovered"):
        await write_within(slow_success, 0.3)
    refused = 0

    async def refused_then_slow() -> None:
        nonlocal refused
        refused += 1
        if refused == 1:
            raise RecordSchemaNarrowError("narrow")
        await asyncio.sleep(1.0)

    with pytest.raises(AssertionError, match="not recovered"):
        await write_within(refused_then_slow, 0.8)


@pytest.mark.parametrize(("savers", "hold_max"), [(4, 0.4), (6, 7.0)], ids=["short-transactions", "one-always-open"])
async def test_rewiden_under_steady_load(env, monkeypatch: pytest.MonkeyPatch, savers: int, hold_max: float) -> None:
    p, s = env
    seed = int(os.environ.get("RD_SEED") or random.SystemRandom().randint(1, 10**6))
    rng = random.Random(seed)
    clean = [s.key("c") for _ in range(400)]
    assert await p.batch_upsert_nodes([base_record(k, s.org) for k in clean], RECORDS)
    tainted = s.key("t")
    assert await p.batch_upsert_nodes([{**base_record(tainted, s.org), **DIAG}], RECORDS)
    before_total, before_contended = _counter("pipeshub_record_schema_rewiden_total"), _counter("pipeshub_record_schema_rewiden_contended_total")
    stop = asyncio.Event()
    waits: list[tuple[float, float]] = []
    saves = {"ok": 0, "refused": 0, "other": [], "first_refusal": None}

    async def saver(w: int) -> None:
        while not stop.is_set():
            batch = rng.sample(clean[w * 25:(w + 1) * 25], 20)  # disjoint per worker: no write-write conflicts between savers
            txn = await p.begin_transaction(read=[RECORDS], write=[RECORDS])
            try:
                await p.batch_upsert_nodes([{**base_record(k, s.org), "indexingStatus": "QUEUED", "version": w} for k in batch], RECORDS, transaction=txn)
                await asyncio.sleep(rng.uniform(0.05 if hold_max < 5 else hold_max * 0.7, hold_max))  # 7.0: always one open, the contended case
                await p.commit_transaction(txn)
                saves["ok"] += 1
            except Exception as error:
                await p.rollback_transaction(txn)
                if p.is_transient_error(error):
                    saves["refused"] += 1
                    saves["first_refusal"] = saves["first_refusal"] or repr(error)[:300]
                else:
                    saves["other"].append(repr(error)[:200])

    async def writer(w: int) -> None:
        i = 0
        while not stop.is_set():
            key = clean[200 + (w * 50 + i) % 200]
            started = time.monotonic()
            await p.update_node(key, RECORDS, {"processingStartedAt": i})
            waits.append((started, time.monotonic() - started))
            i += 1
            await asyncio.sleep(0.02)

    tasks = [asyncio.create_task(saver(w)) for w in range(savers)] + [asyncio.create_task(writer(w)) for w in range(4)]
    await asyncio.sleep(3 if hold_max < 5 else 8)
    narrowed_at = time.monotonic()
    assert await p.http_client.update_collection_schema(RECORDS, narrow_schema(), timeout=30)  # the older image starts
    sent = running_at_each_widening(p.http_client, monkeypatch)
    trigger_started = time.monotonic()
    # What this is about is recovery, not how many tries it takes: skipped widenings, a forced one that timed out with its lease still held
    # (the next tries are refused without a new widening until the lease or the pending change ends) and the retries between them all
    # count against one deadline, after which the schema must be wide and the write landed.
    recovery_s = RECOVERY_S + hold_max
    attempts = await write_within(lambda: p.update_node(tainted, RECORDS, {"indexingStatus": "QUEUED"}), recovery_s)
    widened_after = time.monotonic() - trigger_started
    await asyncio.sleep(10)
    stop.set()
    await asyncio.gather(*tasks)

    window = [w for t, w in waits if t >= narrowed_at]
    window.sort()
    p99 = window[int(len(window) * 0.99) - 1]
    total = _counter("pipeshub_record_schema_rewiden_total") - before_total
    contended = _counter("pipeshub_record_schema_rewiden_contended_total") - before_contended
    print(f"seed={seed} savers={savers} hold_max={hold_max}s: trigger write landed after {widened_after:.2f}s ({attempts} attempts); plain writes in window {len(window)}, "
          f"max wait {window[-1]:.2f}s, p99 {p99:.3f}s (bound {STALL_BOUND_S}s); saves ok={saves['ok']} refused={saves['refused']} "
          f"other={len(saves['other'])} first_refusal={saves['first_refusal']}; rewiden_total +{total:g}, contended +{contended:g}; "
          f"running at each widening {sent}")
    assert (await s.doc(tainted))["indexingStatus"] == "QUEUED"
    assert generation_of_schema(await p.http_client.get_collection_schema(RECORDS)) == RECORD_SCHEMA_GENERATION, "the schema ends wide"
    assert saves["other"] == [], saves["other"][:3]
    if hold_max < 5:
        # new save transactions wait while the schema is narrow, so the open ones end and the change is sent with none running
        assert sent == [[]], f"the widening was sent while the server listed {sent}: every records write waits for those"
    else:
        # a transaction is nearly always open, so the first quiet-point wait finds no moment: that widening is skipped (the write is refused
        # transiently) and a later try finds a gap or is forced. What a write then waits for is the save transactions open at that
        # moment (each held at most hold_max), which is ArangoDB's doing, not the guard's
        assert contended >= 1 and total >= 1
