"""QA probes for parse-now: wait budget, dead collaborators, gate accounting, run isolation.

Cases refer to notes/record-metadata-early/TEST_CASES_B.md.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.modules.indexing import parse_now_marker as marker
from app.modules.pipeline import parse_now as parse_now_module
from app.modules.pipeline.parse_now import ParseNow
from tests.unit.utils.test_fetch_record_resolution import (
    MODULE,
    _Docs,
    _fetch,
    _provider,
    _row,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

pytestmark = pytest.mark.asyncio


@pytest.fixture(autouse=True)
def _quick(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(f"{MODULE}._POLL_STEPS", (0.01,))
    monkeypatch.setattr(f"{MODULE}._POLL_THEN_EVERY", 0.01)
    yield
    patch.stopall()


def _asker(send, wait: float, redis=None) -> ParseNow:
    producer = MagicMock()
    producer.send_message = send
    config = MagicMock()
    config.get_config = AsyncMock(return_value={})
    patch(f"{MODULE}.parse_now_wait_seconds", AsyncMock(return_value=wait)).start()
    return ParseNow(config, logging.getLogger("qa"), producer, redis)


# ---- wait budget (task 2: "under 55 s with 3 records and slow/dead broker or Redis down") ----------


async def _hang(*_a, **_k) -> None:
    await asyncio.sleep(3600)


async def test_dead_broker_for_three_records_costs_one_producer_timeout_not_three(monkeypatch) -> None:
    monkeypatch.setattr(parse_now_module, "_PRODUCER_TIMEOUT_SECONDS", 0.2)
    docs = _Docs({f"r{i}": [_row("QUEUED")] for i in range(3)})
    asker = _asker(AsyncMock(side_effect=_hang), wait=5)

    t0 = time.monotonic()
    result = await _fetch([f"r{i}" for i in range(3)], graph_provider=_provider(docs), parse_now=asker)
    elapsed = time.monotonic() - t0

    assert elapsed < 1.0  # the requests ran together; nothing waited for a parse that was never asked for
    assert result["ok"] is False and "being prepared" not in result["error"]  # R12: today's reply


async def test_slow_broker_then_slow_graph_never_exceeds_the_one_budget(monkeypatch) -> None:
    monkeypatch.setattr(parse_now_module, "_PRODUCER_TIMEOUT_SECONDS", 0.2)

    async def slow_send(*_a, **_k) -> bool:
        await asyncio.sleep(0.15)  # just under the producer timeout
        return True

    docs = _Docs({f"r{i}": [_row("QUEUED")] for i in range(3)})
    asker = _asker(slow_send, wait=0.5)

    t0 = time.monotonic()
    await _fetch([f"r{i}" for i in range(3)], graph_provider=_provider(docs), parse_now=asker)

    assert time.monotonic() - t0 < 0.5 + 0.25  # budget is wait_seconds in total, publish time included


async def test_a_graph_read_that_hangs_during_the_wait_is_cut_at_the_deadline() -> None:
    entered, cancelled, finished = asyncio.Event(), asyncio.Event(), []

    class Hang(_Docs):
        async def __call__(self, document_key, collection="", **kw) -> object:
            if self.reads.get(document_key, 0) >= 1:
                entered.set()
                try:
                    await asyncio.Event().wait()  # never set: only the deadline can end this read
                except asyncio.CancelledError:
                    cancelled.set()
                    raise
                finished.append(document_key)
            return await super().__call__(document_key, collection, **kw)

    docs = Hang({"r0": [_row("QUEUED")]})
    asker = _asker(AsyncMock(return_value=True), wait=0.3)

    # no wall-clock bound: without the deadline this read never ends, so the outer timeout (far above any runner delay) fails the test
    result = await asyncio.wait_for(_fetch(["r0"], graph_provider=_provider(docs), parse_now=asker), 30)

    assert entered.is_set() and cancelled.is_set() and not finished  # the hung read was cut, and nothing completed past the deadline
    assert result["unavailable_reasons"]["r0"] == "content_being_prepared"


async def test_redis_down_does_not_block_or_prevent_the_send(monkeypatch) -> None:
    monkeypatch.setattr(parse_now_module, "_REDIS_TIMEOUT_SECONDS", 0.1)
    redis = MagicMock()
    redis.key_namespace = ""
    client = MagicMock()
    client.set = AsyncMock(side_effect=_hang)
    client.delete = AsyncMock()
    redis.get_client.return_value = client
    send = AsyncMock(return_value=True)
    asker = _asker(send, wait=0.2, redis=redis)

    ok = await asker.request("r0", {"contentRev": "rev-1"}, "org-1", "u1")

    assert ok is True and send.await_count == 1


async def test_a_claim_is_released_when_the_publish_fails() -> None:  # L12
    redis = MagicMock()
    redis.key_namespace = "ns"
    client = MagicMock()
    client.set = AsyncMock(return_value=True)
    client.delete = AsyncMock()
    redis.get_client.return_value = client
    asker = _asker(AsyncMock(side_effect=RuntimeError("broker")), wait=0.2, redis=redis)

    assert await asker.request("r0", {"contentRev": "rev-1"}, "org-1", "u1") is False
    client.delete.assert_awaited_once_with("ns:parse-now:org-1:r0")


async def test_a_claim_is_released_when_the_publish_is_cancelled() -> None:  # R21
    redis = MagicMock()
    redis.key_namespace = ""
    client = MagicMock()
    client.set = AsyncMock(return_value=True)
    client.delete = AsyncMock()
    redis.get_client.return_value = client
    asker = _asker(AsyncMock(side_effect=_hang), wait=0.2, redis=redis)

    task = asyncio.create_task(asker.request("r0", {"contentRev": "rev-1"}, "org-1", "u1"))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    client.delete.assert_awaited()  # else the record stays "in flight" for 90 s with nothing sent


# ---- findings -------------------------------------------------------------------------------------


@pytest.mark.parametrize("status", ["FILE_TYPE_NOT_SUPPORTED", "EMPTY"])
async def test_a_first_time_store_only_run_that_ends_for_good_is_known_to_the_reader(status) -> None:
    graph_row = {"indexingStatus": "AUTO_INDEX_OFF", "contentRev": None}  # never parsed
    marker.begin_run(marker.ParseNowRun(store_only=True, content_rev=graph_row["contentRev"]))
    try:
        written = marker.shape_status_write({"indexingStatus": status, "reason": "r"})
    finally:
        marker.end_run()
    graph_row.update(written)

    assert parse_now_module.parse_outcome(graph_row) == status


async def test_a_status_write_after_the_content_is_stored_keeps_the_stored_marker() -> None:
    marker.begin_run(marker.ParseNowRun(store_only=False, content_rev="rev-1", stored=True))
    try:
        written = marker.shape_status_write({"indexingStatus": "FAILED", "reason": "embedding"})
    finally:
        marker.end_run()
    assert marker.PARSE_NOW_STATUS not in written
