"""A Force reindex request survives a lost hand-back, and nothing is bought twice, over the repository's own code.

The action alphabet is the reference model's (``tests/support/refresh_intent_model.py``): a Force reindex through the walk
(its publish can fail), one made while the record runs, a connector's plain update, message loss, a hand-back before and
after the refresh branch, a run's end and crash, the stale, stranded and intent sweeps, a twin. ``tests/support/
refresh_intent_world.py`` drives it against the real handler, walk and sweeps. After a drain: every request is followed by a
refresh run (no DROP) and no derived run starts when every request was served (no SPURIOUS).

Each named mutant below removes one rule of the design and must make the same drive fail.
"""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

from app import indexing_main
from app.connectors.services.event_service import EventService
from app.modules.indexing import refresh_intent as ri
from app.services.messaging.kafka.handlers.record import RecordEventHandler, RefreshRun
from app.services.paid_cache import run as paid_cache_run
from tests.support import refresh_intent_world as world

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

SEEDS = (913377, 5550001, 777000111)
WORLDS = 150

_settle = RecordEventHandler._settle_refresh_intent
_walk_write = EventService._clear_status_before_publish


def _tally(*, twins: bool, seed: int = SEEDS[0], worlds: int = WORLDS) -> tuple[int, int]:
    async def go() -> tuple[int, int]:
        dropped = spurious = 0
        for s in range(seed, seed + worlds):
            drop, notes = await world.run_world(s, twins=twins)
            dropped += bool(drop)
            spurious += notes["spurious_any"] > 0
        return dropped, spurious

    return asyncio.run(go())


@pytest.mark.parametrize("twins", [False, True])
@pytest.mark.parametrize("seed", SEEDS)
def test_no_request_is_lost_and_nothing_is_bought_twice(seed: int, twins: bool) -> None:
    assert _tally(twins=twins, seed=seed) == (0, 0)


# ---- the mutants: each removes one rule of the design

@contextmanager
def _no_handler_read() -> Iterator[None]:
    async def settle(self, event_type, payload, record, record_id, *, parse_now):  # noqa: ANN202
        requested = paid_cache_run.refresh_requested(payload)
        return RefreshRun(seen=None, intent_id=payload.get(ri.PAYLOAD_ID_KEY) if requested else None, refresh=requested)

    with patch.object(RecordEventHandler, "_settle_refresh_intent", settle):
        yield


@contextmanager
def _no_publish_time_write() -> Iterator[None]:
    async def write(self, records, intent_id):  # noqa: ANN202
        await _walk_write(self, records, None)

    with patch.object(EventService, "_clear_status_before_publish", write):
        yield


@contextmanager
def _walk_skips_running_records() -> Iterator[None]:
    async def keep(self, records, intent_id):  # noqa: ANN202
        return [], 0

    with patch.object(EventService, "_keep_request_of_running_records", keep):
        yield


@contextmanager
def _no_intent_sweep() -> Iterator[None]:
    async def nothing(**_k):  # noqa: ANN202
        return 0

    with patch.object(indexing_main, "_republish_refresh_intents", nothing):
        yield


@contextmanager
def _intent_sweep_gated_by_status() -> Iterator[None]:
    original = world.IntentGraph.get_records_with_refresh_intent

    async def waiting_only(self, org_id, *, after_key=None, limit=100):  # noqa: ANN202
        rows = await original(self, org_id, after_key=after_key, limit=limit)
        return [r for r in rows if r.get("indexingStatus") in ("QUEUED", "NOT_STARTED", "IN_PROGRESS")]

    with patch.object(world.IntentGraph, "get_records_with_refresh_intent", waiting_only):
        yield


@contextmanager
def _clear_own_id_not_the_one_seen() -> Iterator[None]:
    async def settle(self, *args, **kwargs):  # noqa: ANN202
        run = await _settle(self, *args, **kwargs)
        return RefreshRun(seen=run.intent_id, intent_id=run.intent_id, refresh=run.refresh) if run is not None else run

    with patch.object(RecordEventHandler, "_settle_refresh_intent", settle):
        yield


@contextmanager
def _no_origin_marker() -> Iterator[None]:
    original = ri.intent_flags

    async def never_done(self, *_a, **_k):  # noqa: ANN202
        return False                      # the done-marker also answers a satisfied id; without Redis only the marker rule is left

    def flags(intent_id: str, *, from_intent: bool) -> dict:
        return original(intent_id, from_intent=False)

    with patch.object(ri, "intent_flags", flags), patch.object(RecordEventHandler, "_refresh_already_ran", never_done):
        yield


@contextmanager
def _backstop_on_a_stamped_payload() -> Iterator[None]:
    async def settle(self, event_type, payload, record, record_id, *, parse_now):  # noqa: ANN202
        if payload.get(ri.PAYLOAD_ID_KEY) and not payload.get(ri.FROM_INTENT_KEY):
            payload.pop(ri.PAYLOAD_ID_KEY)                    # the stamp is ignored, so the event looks unstamped
        return await _settle(self, event_type, payload, record, record_id, parse_now=parse_now)

    with patch.object(RecordEventHandler, "_settle_refresh_intent", settle):
        yield


MUTANTS: dict[str, Callable[[], object]] = {
    "no handler read": _no_handler_read,
    "no write at publish time": _no_publish_time_write,
    "walk skips running records": _walk_skips_running_records,
    "no intent sweep": _no_intent_sweep,
    "intent sweep gated by status": _intent_sweep_gated_by_status,
    "clear the run's own id, not the one it saw": _clear_own_id_not_the_one_seen,
    "events do not say they came from a request (and no done-marker)": _no_origin_marker,
    "backstop writes for a stamped payload": _backstop_on_a_stamped_payload,
}


@pytest.mark.parametrize("name", list(MUTANTS))
def test_each_mutant_fails_the_drive(name: str) -> None:
    with MUTANTS[name]():  # type: ignore[attr-defined]
        dropped, spurious = _tally(twins=True)
        dropped2, spurious2 = _tally(twins=False)
    assert dropped + spurious + dropped2 + spurious2 > 0, f"the drive does not notice: {name}"
