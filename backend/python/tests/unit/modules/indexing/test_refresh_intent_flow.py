"""A Force reindex request, scenario by scenario: what is stored, who reads it, what clears it, what it must not do.

The world is ``tests/support/refresh_intent_world.py`` (one record, a clock, a broker that can lose a message, the real handler,
walk and sweeps).
"""
from __future__ import annotations

import contextlib
from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app import indexing_main
from app.config.constants.arangodb import EventTypes, ProgressStatus
from app.connectors.api.router import mark_user_forced_reindex
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.connectors.services import event_service
from app.connectors.services.event_service import EventService
from app.connectors.services.reindex_force import forced_by_user, user_forced_intent_id
from app.modules.indexing import refresh_intent as ri
from app.modules.indexing.record_republish import is_parked_duplicate, record_event
from app.modules.indexing.recovery_budget import recovery_tracking_id
from app.services.paid_cache.locks import DELETED_TTL_MS
from tests.support.refresh_intent_world import KEY, MINUTE_MS, ORG, World, patched

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

HOUR_MS = 60 * MINUTE_MS
QUEUED = ProgressStatus.QUEUED.value
COMPLETED = ProgressStatus.COMPLETED.value
IN_PROGRESS = ProgressStatus.IN_PROGRESS.value
FAILED = ProgressStatus.FAILED.value


@pytest.fixture
async def w() -> AsyncIterator[World]:
    """A world where nothing is lost, handed back or crashed unless a test says so."""
    world = World(1, twins=False)
    world.R.random = lambda: 0.99          # type: ignore[method-assign]
    with patched(world):
        yield world


def _events(world: World) -> list[dict]:
    return [m["payload"] for m in world.msgs]


async def _run_next(world: World) -> None:
    await world.consume()
    await world.finish(allow_crash=False)


# ---- the helpers

def test_the_life_of_a_request_is_the_paid_result_markers() -> None:
    assert ri.INTENT_LIFE_MS == DELETED_TTL_MS


def test_a_request_lives_up_to_24_hours_from_its_time() -> None:
    record = {ri.ID_FIELD: "i", ri.AT_FIELD: 1_000}
    assert ri.live_intent(record, 1_000 + DELETED_TTL_MS - 1) == "i"
    assert ri.live_intent(record, 1_000 + DELETED_TTL_MS) is None
    assert ri.expired_intent(record, 1_000 + DELETED_TTL_MS)
    assert ri.live_intent({ri.ID_FIELD: "i"}, 5) is None              # no time: nothing could ever bound it
    assert ri.live_intent({}, 5) is None and not ri.expired_intent({}, 5)


def test_a_request_stamped_ahead_of_this_clock_stays_live() -> None:
    """The walk and the sweep run on different hosts: a time a little ahead of this process's clock is not an expired request."""
    record = {ri.ID_FIELD: "i", ri.AT_FIELD: 10_000_000}
    assert ri.live_intent(record, 10_000_000 - 5 * 60 * 1000) == "i"
    assert not ri.expired_intent(record, 10_000_000 - 5 * 60 * 1000)
    assert ri.live_intent(record, 10_000_000 + ri.INTENT_LIFE_MS) is None          # only age expires it


def test_a_row_that_holds_a_request_is_never_a_parked_duplicate_and_its_event_is_the_refresh() -> None:
    row = {"md5Checksum": "m", "virtualRecordId": "v", "version": 1, "recordName": "r", "origin": "CONNECTOR"}
    assert is_parked_duplicate(row)
    held = {**row, **ri.intent_fields("i1", 1_000)}
    assert not is_parked_duplicate(held)
    with patch("app.modules.indexing.record_republish.get_epoch_timestamp_in_ms", lambda: 2_000):
        _, plain = record_event(row, record_key="k", connector_id="c")
        _, refresh = record_event(held, record_key="k", connector_id="c")
    assert "refreshPaidOutputs" not in plain and "forceReindex" not in plain
    assert (refresh["forceReindex"], refresh["refreshPaidOutputs"], refresh["refreshIntentId"], refresh["refreshFromIntent"]) == (True, True, "i1", True)


def test_a_request_has_a_budget_of_its_own_in_stale_recovery() -> None:
    assert recovery_tracking_id("r", 3) == "stale-recovery:r:3"
    assert recovery_tracking_id("r", 3, "i1") != recovery_tracking_id("r", 3)
    assert recovery_tracking_id("r", 3, "i1") != recovery_tracking_id("r", 3, "i2")


# ---- 4, 6: a lost hand-back, then the sweep, then the re-run

async def test_a_lost_handback_is_re_sent_by_the_intent_sweep_as_the_same_refresh(w: World) -> None:
    await w.user_force()
    stamped = _events(w)[0][ri.PAYLOAD_ID_KEY]
    assert w.row[ri.ID_FIELD] == stamped and w.row["indexingStatus"] == QUEUED          # stored with the status, before the publish
    w.msgs.clear()                                                                       # the hand-back never came back
    w.now_ms += 2 * HOUR_MS
    await w.intent_sweep()
    [resent] = _events(w)
    assert (resent["forceReindex"], resent["refreshPaidOutputs"], resent[ri.PAYLOAD_ID_KEY]) == (True, True, stamped)
    await w.consume()
    run = w.active["run"]
    assert run.refresh and run.intent_id == stamped and run.seen == stamped              # the same refresh: its marker is found
    await w.finish(allow_crash=False)
    assert w.row.get(ri.ID_FIELD) is None and (ORG, KEY, stamped) in w.cache.done


async def test_a_plain_update_that_runs_first_runs_as_the_refresh(w: World) -> None:
    await w.user_force()
    w.msgs.clear()
    await w.plain_update()
    await w.consume()
    run = w.active["run"]
    assert run.refresh and w.active["payload"][ri.FROM_INTENT_KEY] is True and w.active["payload"]["refreshPaidOutputs"] is True
    await w.finish(allow_crash=False)
    assert w.row.get(ri.ID_FIELD) is None and w.sat == {1}


async def test_a_publish_that_fails_leaves_the_request_on_the_record_and_the_sweep_sends_the_refresh(w: World) -> None:
    w.R.random = lambda: 0.0                                                             # type: ignore[method-assign]  # the publish fails
    await w.user_force()
    w.R.random = lambda: 0.99                                                            # type: ignore[method-assign]
    assert not w.msgs and w.row[ri.ID_FIELD] and w.row["indexingStatus"] == ProgressStatus.NOT_STARTED.value
    w.now_ms += 2 * HOUR_MS
    await w.intent_sweep()
    assert _events(w)[0]["refreshPaidOutputs"] is True


# ---- 22: a request made while the record runs

async def test_a_request_made_while_a_plain_run_is_in_progress_is_handed_over_when_the_run_ends(w: World) -> None:
    await w.plain_update()
    await w.consume()
    assert w.active is not None and not w.active["run"].refresh and w.row["indexingStatus"] == IN_PROGRESS
    started = w.row.get("processingStartedAt")
    await w.user_force()                                                                 # the walk meets a running record
    assert w.row["indexingStatus"] == IN_PROGRESS and w.row.get("processingStartedAt") == started
    assert not w.msgs, "a running record is not published: that would start a second pipeline"
    asked = w.row[ri.ID_FIELD]
    assert asked
    await w.finish(allow_crash=False)
    [handed] = _events(w)                                                                # no sweep needed
    assert handed[ri.PAYLOAD_ID_KEY] == asked and handed["refreshPaidOutputs"] is True
    assert w.row[ri.ID_FIELD] == asked                                                   # the plain run did not clear what it never saw
    await _run_next(w)
    assert w.row.get(ri.ID_FIELD) is None and len(w.sat) == 1 and w.reqs.keys() == w.sat


async def test_the_walk_that_meets_a_run_that_just_ended_publishes_the_ordinary_way(w: World) -> None:
    """The intent write precedes the status read; the run's terminal write precedes its intent read: one of them sees the other."""
    await w.plain_update()
    await w.consume()
    real = w.graph.update_record_fields_for_ids

    async def ends_in_between(ids: list[str], fields: dict) -> int:
        n = await real(ids, fields)
        w.row["indexingStatus"] = COMPLETED                                              # the run ends right after the write
        w.active = None
        return n

    w.graph.update_record_fields_for_ids = ends_in_between                               # type: ignore[method-assign]
    await w.user_force()
    [event] = _events(w)
    assert event["refreshPaidOutputs"] is True and w.row["indexingStatus"] == QUEUED


async def test_two_events_of_one_refresh_run_once(w: World) -> None:
    """The double send of the race above (the walk's publish and the run's hand-over): the second is a no-op."""
    await w.plain_update()
    await w.consume()
    await w.user_force()
    await w.finish(allow_crash=False)
    [handover] = _events(w)
    await _run_next(w)                                                                   # the first event runs
    # the walk's own stamped event of the same request (not built from the record, so it is not demoted): a no-op now
    again = {"recordId": KEY, "orgId": ORG, "forceReindex": True, "refreshPaidOutputs": True, ri.PAYLOAD_ID_KEY: handover[ri.PAYLOAD_ID_KEY]}
    run = await w.handler._settle_refresh_intent(EventTypes.REINDEX_RECORD.value, again, dict(w.row), KEY, parse_now=False)
    assert run is not None and await w.handler._refresh_state(run, dict(w.row), again) == "done"


# ---- the life of a request that waited, and the expiry

async def test_a_hand_over_restarts_the_life_and_the_resend_count_so_the_first_resend_is_an_hour_away(w: World) -> None:
    w.row.update(republishCount=5, lastRepublishedAt=w.now_ms - 3 * HOUR_MS)             # the record waited through five stranded re-sends
    await w.plain_update()
    await w.consume()
    await w.user_force()
    w.now_ms += 20 * HOUR_MS                                                             # a long run: the request is 20 h old at its end
    await w.finish(allow_crash=False)
    assert w.row[ri.AT_FIELD] == w.now_ms and w.row.get("republishCount") is None        # 24 h count from the hand-over
    w.msgs.clear()                                                                       # the hand-over message is lost
    w.now_ms += 61 * MINUTE_MS
    await w.intent_sweep()
    assert len(_events(w)) == 1, "the first re-send must not wait out the stranded counter"


async def test_an_intent_write_resets_the_resend_count(w: World) -> None:
    w.row.update(republishCount=5, lastRepublishedAt=1)
    await w.user_force()
    assert w.row.get("republishCount") is None


class _Counted:
    """Records the labels of every ``inc`` (the metrics backend has no reader)."""

    def __init__(self) -> None:
        self.events: list[str] = []

    def inc(self, *labels: str, value: float = 1.0) -> None:
        self.events.extend(labels)


async def test_an_expired_request_on_a_completed_row_is_cleared_logged_and_counted(w: World, caplog: pytest.LogCaptureFixture) -> None:
    w.row.update(ri.intent_fields("old", w.now_ms - 25 * HOUR_MS))
    counted = _Counted()
    with patch.object(ri, "REFRESH_INTENT_EVENTS", counted), caplog.at_level("WARNING"):
        await w.intent_sweep()
    assert ri.ID_FIELD not in w.row and ri.AT_FIELD not in w.row and not w.msgs
    assert "was not served within its life" in caplog.text
    assert w.row.get("reason") is None                                                   # a completed row keeps its text
    assert counted.events == ["expired"]


async def test_a_request_made_during_a_run_longer_than_the_life_is_still_served(w: World) -> None:
    """A record a run holds does not age out under it: the sweep leaves its request, and the run's end hands it over."""
    await w.plain_update()
    await w.consume()
    await w.user_force()                                                                 # intent-only write on the running row
    assert w.row.get(ri.ID_FIELD)
    for _ in range(25):
        w.now_ms += HOUR_MS
        await w.intent_sweep()                                                           # nothing dropped, nothing sent
    assert w.row.get(ri.ID_FIELD) and w.row.get("reason") != ri.REFRESH_EXPIRED_REASON and not w.msgs
    await w.finish(allow_crash=False)
    assert any(m["payload"].get(ri.PAYLOAD_ID_KEY) for m in w.msgs), w.msgs


async def test_a_request_stamped_ahead_of_the_sweeps_clock_is_neither_cleared_nor_sent_early(w: World) -> None:
    w.row.update(ri.intent_fields("ahead", w.now_ms + 5 * MINUTE_MS))                   # the walk's host runs 5 minutes ahead
    await w.intent_sweep()
    assert w.row[ri.ID_FIELD] == "ahead" and w.row.get("reason") is None and not w.msgs
    w.now_ms += HOUR_MS + 6 * MINUTE_MS                                                # its time is past: the first send follows
    await w.intent_sweep()
    assert len(w.msgs) == 1


async def test_an_expired_request_on_a_waiting_row_says_so_in_its_reason(w: World) -> None:
    w.row.update(ri.intent_fields("old", w.now_ms - 25 * HOUR_MS), indexingStatus=FAILED)
    await w.intent_sweep()
    assert w.row.get(ri.ID_FIELD) is None and w.row["reason"] == ri.REFRESH_EXPIRED_REASON


# ---- 3, 7: what clears a request, and what must not

async def test_an_older_refresh_does_not_clear_a_newer_request(w: World) -> None:
    await w.user_force()                                                                 # request 1, its message in the broker
    await w.consume()
    first = w.active["run"].seen
    await w.user_force()                                                                 # request 2 arrives while it runs
    newer = w.row[ri.ID_FIELD]
    assert newer != first
    await w.finish(allow_crash=False)
    assert w.row[ri.ID_FIELD] == newer
    assert _events(w)[-1][ri.PAYLOAD_ID_KEY] == newer                                    # and it is handed over


async def test_a_satisfied_event_built_from_a_request_runs_plain(w: World) -> None:
    stale = {"recordId": KEY, "orgId": ORG, **ri.intent_flags("gone", from_intent=True)}
    run = await w.handler._settle_refresh_intent(EventTypes.REINDEX_RECORD.value, stale, dict(w.row), KEY, parse_now=False)
    assert run is not None and not run.refresh and "refreshPaidOutputs" not in stale and ri.PAYLOAD_ID_KEY not in stale


async def test_the_users_own_stamped_message_always_runs_as_the_refresh(w: World) -> None:
    own = {"recordId": KEY, "orgId": ORG, "forceReindex": True, "refreshPaidOutputs": True, ri.PAYLOAD_ID_KEY: "i9"}
    run = await w.handler._settle_refresh_intent(EventTypes.REINDEX_RECORD.value, own, dict(w.row), KEY, parse_now=False)
    assert run is not None and run.refresh and run.intent_id == "i9" and run.seen is None


async def test_the_upgrade_of_a_plain_event_keeps_its_origin_through_a_requeue(w: World) -> None:
    w.row.update(ri.intent_fields("i1", w.now_ms))
    plain = {"recordId": KEY, "orgId": ORG}
    first = await w.handler._settle_refresh_intent(EventTypes.UPDATE_RECORD.value, plain, dict(w.row), KEY, parse_now=False)
    assert first is not None and first.refresh and plain[ri.FROM_INTENT_KEY] is True
    requeued = dict(plain)                                                               # the consumer re-queues the mutated payload
    w.row.update(dict.fromkeys(ri.CLEARED_FIELDS))                                                      # the request has been served meanwhile
    again = await w.handler._settle_refresh_intent(EventTypes.UPDATE_RECORD.value, requeued, dict(w.row), KEY, parse_now=False)
    assert again is not None and not again.refresh and "refreshPaidOutputs" not in requeued


# ---- the backstop writes only for an event that names no request

async def test_the_backstop_stores_the_request_of_an_unstamped_refresh_and_never_restores_a_cleared_one(w: World) -> None:
    unstamped = {"recordId": KEY, "orgId": ORG, "forceReindex": True, "refreshPaidOutputs": True, "_delivery_id": "d-1"}
    run = await w.handler._settle_refresh_intent(EventTypes.NEW_RECORD.value, unstamped, dict(w.row), KEY, parse_now=False)
    assert run is not None and run.refresh and run.intent_id == "d-1" and w.row[ri.ID_FIELD] == "d-1"
    assert unstamped[ri.PAYLOAD_ID_KEY] == "d-1"
    w.row.update(dict.fromkeys(ri.CLEARED_FIELDS))
    stamped = {"recordId": KEY, "orgId": ORG, "forceReindex": True, "refreshPaidOutputs": True, ri.PAYLOAD_ID_KEY: "d-1"}
    await w.handler._settle_refresh_intent(EventTypes.NEW_RECORD.value, stamped, dict(w.row), KEY, parse_now=False)
    assert w.row.get(ri.ID_FIELD) is None, "a cleared request must not be written back"


async def test_an_unstamped_refresh_whose_write_loses_the_race_runs_under_the_id_on_the_record(w: World) -> None:
    """The compare-and-set loses to a different live request: this delivery refreshes under the winner's id (the one that is cleared),
    never under an id that was not stored (which would leave the winner to be served, and paid for, again)."""
    real = w.graph.update_node_fields_if_match
    calls = 0

    async def loses_once(key: str, collection: str, updates: dict, expected: dict, *a: object, **kw: object) -> bool:
        nonlocal calls
        calls += 1
        if calls == 1:
            w.row.update(ri.intent_fields("winner", w.now_ms))                          # another delivery stored its request first
            return False
        return await real(key, collection, updates, expected, *a, **kw)

    w.graph.update_node_fields_if_match = loses_once                                     # type: ignore[method-assign]
    unstamped = {"recordId": KEY, "orgId": ORG, "forceReindex": True, "refreshPaidOutputs": True, "_delivery_id": "mine"}
    run = await w.handler._settle_refresh_intent(EventTypes.NEW_RECORD.value, unstamped, dict(w.row), KEY, parse_now=False)
    assert run is not None and run.refresh and run.intent_id == "winner" and run.seen == "winner"
    assert unstamped[ri.PAYLOAD_ID_KEY] == "winner" and w.row[ri.ID_FIELD] == "winner"


async def test_an_unstamped_refresh_for_a_record_that_is_gone_keeps_its_own_id(w: World) -> None:
    w.graph.docs.pop(KEY)
    unstamped = {"recordId": KEY, "orgId": ORG, "forceReindex": True, "refreshPaidOutputs": True, "_delivery_id": "mine"}
    run = await w.handler._settle_refresh_intent(
        EventTypes.NEW_RECORD.value, unstamped, {"_key": KEY, "orgId": ORG}, KEY, parse_now=False,
    )
    assert run is not None and run.intent_id == "mine" and run.seen is None


async def test_nothing_is_written_while_the_flag_is_off(w: World, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("REFRESH_INTENT_WRITE")
    unstamped = {"recordId": KEY, "orgId": ORG, "forceReindex": True, "refreshPaidOutputs": True, "_delivery_id": "d-1"}
    run = await w.handler._settle_refresh_intent(EventTypes.NEW_RECORD.value, unstamped, dict(w.row), KEY, parse_now=False)
    assert run is not None and run.refresh and w.row.get(ri.ID_FIELD) is None
    assert not ri.write_enabled()


@pytest.mark.parametrize("payload", [
    {"parseNow": True}, {"vectorDbOnly": True}, {"firstUse": True}, {"stages": ["classify"]},
])
async def test_a_request_applies_to_a_full_record_event_only(w: World, payload: dict) -> None:
    """a parse-now, a vector rebuild or a stage re-run stays plain; the request waits for a full run."""
    w.row.update(ri.intent_fields("i1", w.now_ms))
    event = {"recordId": KEY, "orgId": ORG, **payload}
    parse_now = payload.get("parseNow") is True
    run = await w.handler._settle_refresh_intent(EventTypes.REINDEX_RECORD.value, event, dict(w.row), KEY, parse_now=parse_now)
    assert run is None and "refreshPaidOutputs" not in event and w.row[ri.ID_FIELD] == "i1"


# ---- stale recovery gives up on one request, not on the next

async def test_recovery_that_gives_up_clears_only_the_request_it_served(w: World) -> None:
    w.row.update(ri.intent_fields("newer", w.now_ms), indexingStatus=IN_PROGRESS)
    mangled = "_RecordEventHandler__update_document_status"
    with patch.object(type(w.handler), mangled, AsyncMock(return_value={"virtualRecordId": "v1"})), \
            patch.object(type(w.handler), "_trigger_next_queued_duplicate", AsyncMock()):
        await w.handler.on_recovery_exhausted(KEY, resume_enrichment=False, intent_id="older")
        assert w.row[ri.ID_FIELD] == "newer"
        await w.handler.on_recovery_exhausted(KEY, resume_enrichment=False, intent_id="newer")
    assert w.row.get(ri.ID_FIELD) is None


# ---- the sweep leaves what is not its to send, and sends once

async def test_the_intent_sweep_leaves_a_deferred_a_trashed_and_a_leased_record_alone(w: World) -> None:
    w.row.update(ri.intent_fields("i1", w.now_ms - 2 * HOUR_MS))
    w.row["deferredUntilMs"] = w.now_ms + HOUR_MS                                        # handed back for a provider's wait
    await w.intent_sweep()
    assert not w.msgs
    w.row.pop("deferredUntilMs")
    w.active = {"run": None, "payload": {}, "start": 1, "derived": False}                # a run holds the lease
    await w.intent_sweep()
    assert not w.msgs
    w.active = None
    with patch.object(indexing_main, "is_live_record", lambda _r: False):                # trashed since the scan read it
        await w.intent_sweep()
    assert not w.msgs
    await w.intent_sweep()
    assert len(w.msgs) == 1


async def test_two_sweeps_send_one_event(w: World) -> None:
    import asyncio

    w.row.update(ri.intent_fields("i1", w.now_ms - 2 * HOUR_MS))
    await asyncio.gather(w.intent_sweep(), w.intent_sweep())
    assert len(w.msgs) == 1


async def test_a_sweep_that_lost_the_claim_to_another_sends_nothing(w: World) -> None:
    """the claim matches the re-send time that was read, so a sweep that read it before another claimed it does not send."""
    w.row.update(ri.intent_fields("i1", w.now_ms - 2 * HOUR_MS))
    real = w.graph.get_document
    reads = 0

    async def another_sweep_claims_after_the_read(key: str, collection: str, **kw: object) -> dict | None:
        nonlocal reads
        doc = await real(key, collection, **kw)
        if collection != "records":
            return doc
        reads += 1
        if reads == 1:                                         # the re-read under the lease
            w.row["lastRepublishedAt"] = w.now_ms               # the other sweep's claim lands right after it
        return doc

    w.graph.get_document = another_sweep_claims_after_the_read  # type: ignore[method-assign]
    await w.intent_sweep()
    assert not w.msgs


async def test_a_send_that_fails_puts_the_claim_back(w: World) -> None:
    w.row.update(ri.intent_fields("i1", w.now_ms - 2 * HOUR_MS))
    boom = AsyncMock(side_effect=RuntimeError("broker down"))
    with patch("tests.support.refresh_intent_world._Producer.send_event", boom):
        await w.intent_sweep()
    assert w.row.get("lastRepublishedAt") is None and not w.msgs
    await w.intent_sweep()
    assert len(w.msgs) == 1


# ---- the walk

async def test_the_walk_status_statement_carries_the_request_only_for_a_forced_walk(w: World) -> None:
    records = [SimpleNamespace(id=KEY, indexing_status=COMPLETED, mime_type="text/html")]
    await w.service._clear_status_before_publish(records, None)
    assert w.row.get(ri.ID_FIELD) is None and w.row["indexingStatus"] == ProgressStatus.NOT_STARTED.value
    await w.service._clear_status_before_publish([SimpleNamespace(id=KEY, indexing_status=COMPLETED, mime_type="text/html")], "i7")
    assert w.row[ri.ID_FIELD] == "i7" and w.row[ri.AT_FIELD] == w.now_ms


def test_a_forced_walk_mints_one_id_and_stamps_it_on_each_record_event() -> None:
    assert user_forced_intent_id() is None
    with forced_by_user(forced=True):
        minted = user_forced_intent_id()
        assert minted
        record = MagicMock(virtual_record_id=None)
        record.to_kafka_record.return_value = {"recordId": "r"}
        payload = DataSourceEntitiesProcessor._reindex_event_payload(record, vector_db_only=False)
    assert payload["refreshIntentId"] == minted and payload["refreshPaidOutputs"] is True
    with forced_by_user(forced=False):
        assert user_forced_intent_id() is None
        record = MagicMock(virtual_record_id=None)
        record.to_kafka_record.return_value = {}
        assert "refreshIntentId" not in DataSourceEntitiesProcessor._reindex_event_payload(record, vector_db_only=False)
    assert user_forced_intent_id() is None


def test_a_single_records_force_event_is_stamped_and_a_sync_event_is_not() -> None:
    record_events = {"topic": "record-events", "payload": {}}
    mark_user_forced_reindex(record_events)
    assert record_events["payload"]["refreshPaidOutputs"] is True and record_events["payload"]["refreshIntentId"]
    sync_events = {"topic": "sync-events", "payload": {}}
    mark_user_forced_reindex(sync_events)
    assert sync_events["payload"] == {"force": True}


def _walk_service(records: list[SimpleNamespace], graph_status: str) -> tuple[EventService, MagicMock, MagicMock]:
    graph = MagicMock()
    graph.get_records_by_status = AsyncMock(side_effect=[records, []])
    graph.update_indexing_status_for_record_ids = AsyncMock()
    graph.update_record_fields_for_ids = AsyncMock(return_value=1)
    graph.get_document = AsyncMock(return_value={"indexingStatus": graph_status})
    graph.get_typed_records_batch = AsyncMock(return_value={})
    connector = MagicMock()
    connector.reindex_records = AsyncMock()
    service = EventService.__new__(EventService)
    service.graph_provider = graph
    service.logger = MagicMock()
    return service, graph, connector


@contextlib.asynccontextmanager
async def _no_stamp(*_a: object, **_k: object) -> AsyncIterator[SimpleNamespace]:
    yield SimpleNamespace(tripped=False)


async def _walk(service: EventService, connector: MagicMock) -> None:
    with patch.object(event_service, "unless_synced_since", _no_stamp):
        await service._reindex_batches(connector, "web", "c1", ORG, None, None, 0, None, None)


def _records() -> list[SimpleNamespace]:
    return [
        SimpleNamespace(id="running", is_placeholder=False, indexing_status=IN_PROGRESS, mime_type="text/html"),
        SimpleNamespace(id="idle", is_placeholder=False, indexing_status=COMPLETED, mime_type="text/html"),
    ]


async def test_a_forced_walk_keeps_the_request_of_a_running_record_and_publishes_the_others(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REFRESH_INTENT_WRITE", "true")
    service, graph, connector = _walk_service(_records(), IN_PROGRESS)
    with forced_by_user(forced=True):
        await _walk(service, connector)
    assert graph.get_records_by_status.await_args_list[0].kwargs["exclude_statuses"] == []
    [(ids, fields), _] = graph.update_record_fields_for_ids.await_args_list[0].args, None
    assert ids == ["running"] and fields[ri.ID_FIELD]
    status_call = graph.update_indexing_status_for_record_ids.await_args_list[0]
    assert status_call.args == (["idle"], ProgressStatus.NOT_STARTED.value) and status_call.kwargs["fields"][ri.ID_FIELD] == fields[ri.ID_FIELD]
    assert [r.id for r in connector.reindex_records.await_args.args[0]] == ["idle"]


async def test_a_forced_walk_publishes_a_record_whose_run_ended_in_between(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REFRESH_INTENT_WRITE", "true")
    service, _graph, connector = _walk_service(_records(), COMPLETED)
    with forced_by_user(forced=True):
        await _walk(service, connector)
    assert sorted(r.id for r in connector.reindex_records.await_args.args[0]) == ["idle", "running"]


async def test_a_forced_walk_does_not_publish_a_batch_whose_request_did_not_land(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REFRESH_INTENT_WRITE", "true")
    service, graph, connector = _walk_service([_records()[1]], COMPLETED)
    graph.update_indexing_status_for_record_ids = AsyncMock(side_effect=RuntimeError("store down"))
    with forced_by_user(forced=True), pytest.raises(RuntimeError, match="store down"):
        await _walk(service, connector)
    connector.reindex_records.assert_not_awaited()


async def test_the_walk_is_today_s_while_the_flag_is_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("REFRESH_INTENT_WRITE", raising=False)
    service, graph, connector = _walk_service([_records()[1]], IN_PROGRESS)
    with forced_by_user(forced=True):
        await _walk(service, connector)
    assert graph.get_records_by_status.await_args_list[0].kwargs["exclude_statuses"] == [IN_PROGRESS]
    graph.update_record_fields_for_ids.assert_not_awaited()
    assert "fields" not in graph.update_indexing_status_for_record_ids.await_args_list[0].kwargs




async def test_an_event_upgraded_by_a_request_reaches_the_pipeline_as_a_forced_run(w: World) -> None:
    """The swap decides a forced run from the two flags the pipeline reads off the event; the handler sets them in place."""
    from app.events.events import refresh_requested

    w.row.update(ri.intent_fields("i1", w.now_ms))
    plain = {"recordId": KEY, "orgId": ORG}
    await w.handler._settle_refresh_intent(EventTypes.UPDATE_RECORD.value, plain, dict(w.row), KEY, parse_now=False)
    assert refresh_requested(plain) and plain["forceReindex"] is True


# ---- review round: what the sweep and the settle do beyond the first design

async def test_a_restored_upload_that_holds_a_request_is_sent_as_the_full_refresh_not_a_vector_rebuild() -> None:
    row = {
        "md5Checksum": "m", "virtualRecordId": "v", "version": 1, "recordName": "r", "origin": "UPLOAD",
        "indexingStatus": COMPLETED, "hasIndexedCopy": True, "externalRevisionId": "1000", "lastIndexTimestamp": 1000,
    }
    with patch("app.modules.indexing.record_republish.get_epoch_timestamp_in_ms", lambda: 2_000):
        _, plain = record_event(row, record_key="k", connector_id="c", restored_upload=True)
        _, held = record_event({**row, **ri.intent_fields("i1", 1_500)}, record_key="k", connector_id="c", restored_upload=True)
    assert plain.get("vectorDbOnly") is True and "refreshPaidOutputs" not in plain
    assert "vectorDbOnly" not in held and held["refreshPaidOutputs"] is True


async def test_the_sweep_ends_the_request_of_a_record_whose_connector_is_off_or_gone_and_leaves_one_it_cannot_read(w: World) -> None:
    for app in ({"_key": "c1", "isActive": False}, {"_key": "c1", "isActive": True, "status": "DELETING"}, None):
        w.msgs.clear()
        w.graph.docs.pop("c1", None)
        if app is not None:
            w.graph.docs["c1"] = app
        w.row.update(ri.intent_fields("i1", w.now_ms - 2 * HOUR_MS))
        await w.intent_sweep()
        assert not w.msgs and ri.ID_FIELD not in w.row, app
    # a connector that cannot be read this pass: nothing is sent and the request stays for the next pass
    w.row.update(ri.intent_fields("i2", w.now_ms - 2 * HOUR_MS))
    real = w.graph.get_document

    async def unreadable(key: str, collection: str, **kw: object) -> dict | None:
        if collection == "apps":
            raise RuntimeError("graph down")
        return await real(key, collection, **kw)

    w.graph.get_document = unreadable                                                    # type: ignore[method-assign]
    await w.intent_sweep()
    assert not w.msgs and w.row[ri.ID_FIELD] == "i2"


async def test_a_retry_of_an_older_refresh_that_finds_a_newer_request_runs_as_the_newer_one(w: World) -> None:
    w.row.update(ri.intent_fields("B", w.now_ms))
    older = {"recordId": KEY, "orgId": ORG, "forceReindex": True, "refreshPaidOutputs": True, ri.PAYLOAD_ID_KEY: "A"}
    run = await w.handler._settle_refresh_intent(EventTypes.REINDEX_RECORD.value, older, dict(w.row), KEY, parse_now=False)
    assert run is not None and run.intent_id == "B" and run.seen == "B" and run.superseded == ("A",)
    assert older[ri.PAYLOAD_ID_KEY] == "B"                                                # B's token keys the purge and the paid results
    w.active = {"run": run, "payload": older, "start": 1, "derived": False}
    await w.finish(allow_crash=False)
    assert ri.ID_FIELD not in w.row
    assert (ORG, KEY, "B") in w.cache.done


async def test_the_completion_is_written_on_the_record_with_the_clear_and_needs_no_marker_store(w: World) -> None:
    """A worker whose marker store is its own (Redis unreachable) still leaves the completion where every replica reads it."""
    w.row.update(ri.intent_fields("A", w.now_ms))
    at = w.row[ri.AT_FIELD]
    run = await w.handler._settle_refresh_intent(
        EventTypes.UPDATE_RECORD.value, {"recordId": KEY, "orgId": ORG}, dict(w.row), KEY, parse_now=False,
    )
    assert run.requested_at == at

    async def refused(*_a: object, **_k: object) -> bool:
        return False

    w.cache.mark_refresh_done = refused                                                   # type: ignore[method-assign]
    await w.handler._end_refresh_run(KEY, run, {"recordId": KEY, "orgId": ORG})
    assert ri.ID_FIELD not in w.row and w.row[ri.DONE_FIELD] == at
    # a second worker, with an empty marker store, refuses the late event of that request (and of an older one) on the record alone
    for requested_at in (at, at - 5):
        late = {"recordId": KEY, "orgId": ORG, "forceReindex": True, "refreshPaidOutputs": True, ri.PAYLOAD_ID_KEY: "A",
                ri.REQUESTED_AT_KEY: requested_at}
        again = await w.handler._settle_refresh_intent(EventTypes.REINDEX_RECORD.value, late, dict(w.row), KEY, parse_now=False)
        assert await w.handler._refresh_state(again, dict(w.row), late) == "done"
    newer = {"recordId": KEY, "orgId": ORG, "forceReindex": True, "refreshPaidOutputs": True, ri.PAYLOAD_ID_KEY: "N",
             ri.REQUESTED_AT_KEY: at + 5}
    run = await w.handler._settle_refresh_intent(EventTypes.REINDEX_RECORD.value, newer, dict(w.row), KEY, parse_now=False)
    assert await w.handler._refresh_state(run, dict(w.row), newer) == "open"


async def test_a_leftover_request_that_a_completed_refresh_covers_is_cleared_not_sent(w: World) -> None:
    w.row.update(ri.intent_fields("A", w.now_ms - 2 * HOUR_MS))
    w.row[ri.DONE_FIELD] = w.row[ri.AT_FIELD]
    await w.intent_sweep()
    assert not w.msgs and ri.ID_FIELD not in w.row and w.row[ri.DONE_FIELD]


# ---- review round 3: the sweep under the lease, the connector confirmed at the clear, the order of requests

async def _stale_snapshot(w: World, snapshot: dict) -> None:
    """The sweep reads ``snapshot`` and the row has changed by the time it takes the lease."""
    async def listing(org_id: str, *, after_key: str | None = None, limit: int = 100) -> list[dict]:
        return [dict(snapshot, _key=KEY)] if after_key is None else []

    w.graph.get_records_with_refresh_intent = listing                                      # type: ignore[method-assign]
    await w.intent_sweep()


async def test_an_expired_request_is_rechecked_under_the_lease_before_it_is_cleared(w: World) -> None:
    expired = ri.intent_fields("A", w.now_ms - 25 * HOUR_MS)
    w.row.update(ri.intent_fields("B", w.now_ms))                                          # a newer request since the scan
    await _stale_snapshot(w, {**w.row, **expired})
    assert w.row[ri.ID_FIELD] == "B" and w.row.get("reason") is None
    w.row.update(ri.intent_fields("A", w.now_ms))                                          # the same request, handed over since the scan
    await _stale_snapshot(w, {**w.row, **expired})
    assert w.row[ri.ID_FIELD] == "A" and w.row[ri.AT_FIELD] == w.now_ms
    w.row.update(expired, extractionStatus=IN_PROGRESS)                                    # still expired, but enrichment is running
    await _stale_snapshot(w, dict(w.row))
    assert w.row[ri.ID_FIELD] == "A"
    w.row.update(extractionStatus=COMPLETED)
    w.active = {"run": None, "payload": {}, "start": 1, "derived": False}                  # a run holds the lease
    await w.intent_sweep()
    assert w.row[ri.ID_FIELD] == "A"
    w.active = None
    counted = _Counted()
    with patch.object(ri, "REFRESH_INTENT_EVENTS", counted):
        await w.intent_sweep()                                                             # idle and still expired: cleared, counted
    assert ri.ID_FIELD not in w.row and counted.events == ["expired"]


async def test_a_handed_over_request_is_not_sent_again_at_once_and_a_moved_claim_stops_a_stale_sender(w: World) -> None:
    due = ri.intent_fields("B", w.now_ms - 3 * HOUR_MS)
    w.row.update(due)
    stale = dict(w.row)
    w.row.update(ri.intent_fields("B", w.now_ms))                                          # handed over right after the scan: not due now
    await _stale_snapshot(w, stale)
    assert not w.msgs
    w.row.update(due, lastRepublishedAt=None)
    real = w.graph.update_node_fields_if_match

    async def another_hand_over_first(key: str, collection: str, updates: dict, expected: dict, *a: object, **kw: object) -> bool:
        w.row[ri.AT_FIELD] = w.now_ms - 1                                                   # the time moves between the read and the claim
        return await real(key, collection, updates, expected, *a, **kw)

    w.graph.update_node_fields_if_match = another_hand_over_first                          # type: ignore[method-assign]
    await w.intent_sweep()
    assert not w.msgs


async def test_a_connector_that_came_back_since_it_was_cached_keeps_the_request(w: World) -> None:
    w.graph.docs["c1"] = {"_key": "c1", "isActive": False}
    w.row.update(ri.intent_fields("A", w.now_ms - 2 * HOUR_MS))
    real = w.graph.get_document
    reads = 0

    async def connector_comes_back_at_the_clear(key: str, collection: str, **kw: object) -> dict | None:
        nonlocal reads
        if collection == "apps":
            reads += 1
            if reads >= 2:
                w.graph.docs["c1"] = {"_key": "c1", "isActive": True}
        return await real(key, collection, **kw)

    w.graph.get_document = connector_comes_back_at_the_clear                               # type: ignore[method-assign]
    await w.intent_sweep()
    assert w.row[ri.ID_FIELD] == "A", "the connector state is confirmed where the request is cleared"
    assert not w.msgs                                                                       # and nothing is sent in the same pass


async def test_a_late_event_of_an_older_request_does_not_refresh_after_a_newer_one_ran(w: World) -> None:
    w.cache.last[(ORG, KEY)] = 200                                                         # request B (time 200) has run and cleared
    late = {"recordId": KEY, "orgId": ORG, "forceReindex": True, "refreshPaidOutputs": True, ri.PAYLOAD_ID_KEY: "A",
            ri.REQUESTED_AT_KEY: 100}
    run = await w.handler._settle_refresh_intent(EventTypes.REINDEX_RECORD.value, late, dict(w.row), KEY, parse_now=False)
    assert run is not None and run.requested_at == 100
    assert await w.handler._refresh_state(run, dict(w.row), late) == "done"
    newer = {**late, ri.PAYLOAD_ID_KEY: "C", ri.REQUESTED_AT_KEY: 300}
    run = await w.handler._settle_refresh_intent(EventTypes.REINDEX_RECORD.value, newer, dict(w.row), KEY, parse_now=False)
    assert await w.handler._refresh_state(run, dict(w.row), newer) == "open"


async def test_a_long_runs_request_survives_a_sweep_that_read_the_row_before_its_hand_over(w: World) -> None:
    """Stored during a run older than the life, the record already COMPLETED: the sweep that observed the pre-hand-over row does not
    drop it, and the run's end hands it over once."""
    await w.plain_update()
    await w.consume()
    await w.user_force()                                                                   # stored mid-run
    pre = dict(w.row)
    w.now_ms += 25 * HOUR_MS                                                               # the run outlasts the life
    w.row["indexingStatus"] = COMPLETED                                                    # indexing wrote COMPLETED before the run's end
    await _stale_snapshot(w, {**pre, "indexingStatus": COMPLETED})
    assert w.row.get(ri.ID_FIELD), "the sweep must not drop it before the hand-over"
    await w.finish(allow_crash=False)
    handed = [m for m in w.msgs if m["payload"].get(ri.PAYLOAD_ID_KEY)]
    assert len(handed) == 1 and w.row[ri.AT_FIELD] == w.now_ms
