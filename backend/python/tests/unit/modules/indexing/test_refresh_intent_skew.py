"""Hosts' clocks differ by seconds or minutes: a request is served by identity, never by what two clocks say.

The walk host stamps a request with its own clock and the indexing host runs and completes it on another. These drive the repository's
world (real handler, sweep, hand-over) with the walk host ahead of and behind the indexing host, and with runs shorter than the skew.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from app import indexing_main
from app.config.constants.arangodb import EventTypes, ProgressStatus
from app.modules.indexing import refresh_intent as ri
from tests.support import refresh_intent_world as world_mod
from tests.support.refresh_intent_world import KEY, MINUTE_MS, ORG, World, patched

HOUR_MS = 60 * MINUTE_MS


@pytest.fixture
async def w() -> World:  # type: ignore[misc]
    world = World(1, twins=False)
    world.R.random = lambda: 0.99          # type: ignore[method-assign]
    with patched(world):
        yield world


async def _run_b_stamped_by_a_host_ahead(w: World, skew_ms: int) -> None:
    """Request B stamped by a host whose clock is ``skew_ms`` ahead; its event runs."""
    w.row.update(ri.intent_fields("B", w.now_ms + skew_ms))
    w.msgs.append({"event_type": EventTypes.REINDEX_RECORD.value, "payload": {
        "recordId": KEY, "orgId": ORG, "version": 1, "virtualRecordId": "v1", "forceReindex": True, "refreshPaidOutputs": True,
        ri.PAYLOAD_ID_KEY: "B", "_event_id": "eB", "_delivery_id": "dB"}})
    await w.consume(allow_loss=False)
    assert w.active is not None and w.active["run"].refresh


@pytest.mark.parametrize(("skew_ms", "step_ms"), [(0, MINUTE_MS), (10 * MINUTE_MS, MINUTE_MS), (5_000, 1_000), (-5_000, 1_000)])
async def test_a_request_made_mid_run_is_served_whatever_the_running_requests_clock_was(w: World, skew_ms: int, step_ms: int) -> None:
    await _run_b_stamped_by_a_host_ahead(w, skew_ms)
    w.now_ms += step_ms
    await w.user_force()                               # A, while B runs: stored, not published
    a_id = w.row.get(ri.ID_FIELD)
    assert a_id and a_id != "B"
    w.now_ms += step_ms
    await w.finish(allow_crash=False)                  # B ends: hand-over of A
    for _ in range(6):
        await w.consume(allow_loss=False)
        if w.active is not None:
            break
        w.now_ms += 90 * MINUTE_MS
        await w.intent_sweep()
    assert w.active is not None and w.active["run"].refresh and w.active["run"].intent_id == a_id, (skew_ms, w.row, w.notes)


@pytest.mark.parametrize("skew_ms", [0, 5_000, -65_000])
async def test_a_second_request_after_the_first_ran_is_served_when_the_walk_clocks_differ(w: World, skew_ms: int) -> None:
    await _run_b_stamped_by_a_host_ahead(w, skew_ms + 60_000)
    w.now_ms += 1_000
    await w.finish(allow_crash=False)                  # B ran and completed
    w.now_ms += 60_000
    await w.user_force()                               # a minute later, on another walk replica
    a_id = f"i{w.t}"
    for _ in range(6):
        await w.consume(allow_loss=False)
        if w.active is not None:
            break
        w.now_ms += 90 * MINUTE_MS
        await w.intent_sweep()
    assert w.active is not None and w.active["run"].refresh and w.active["run"].intent_id == a_id, (skew_ms, w.row, w.notes)


async def test_expiry_racing_a_run_ends_hand_over_keeps_the_handed_over_request(w: World) -> None:
    """The sweep re-reads an expired request and, before its clear lands, a run's end hands it over (renews its time and sends it).
    The clear matches the time it read, so it does not undo the hand-over."""
    w.row.update(ri.intent_fields("A", w.now_ms - 25 * HOUR_MS))
    w.row["indexingStatus"] = ProgressStatus.COMPLETED.value
    real_clear = ri.clear_intent
    raced = {"done": False}

    async def clear_after_hand_over(graph, key, intent_id, extra=None, **kw):  # noqa: ANN202
        if not raced["done"]:
            raced["done"] = True
            await w.handler._hand_over_refresh(KEY, dict(w.row), "A", w.now_ms)      # the run's end, in between
        return await real_clear(graph, key, intent_id, extra, **kw)

    with patch.object(indexing_main.refresh_intent, "clear_intent", clear_after_hand_over):
        await w.intent_sweep()
    assert raced["done"] and [m for m in w.msgs if (m["payload"] or {}).get(ri.PAYLOAD_ID_KEY) == "A"]
    assert w.row.get(ri.ID_FIELD) == "A", "the handed-over request was cleared by the stale expiry"


async def test_a_served_clear_rechecks_under_the_lease_and_matches_the_time_it_read(w: World) -> None:
    """A leftover request the completion covers is cleared under the lease on the row as it is then: a new request, or a renewed time,
    since the scan keeps it."""
    w.row.update(ri.intent_fields("A", w.now_ms - 2 * HOUR_MS))
    w.row[ri.DONE_ID_FIELD] = "A"
    stale = dict(w.row)
    w.row.update(ri.intent_fields("B", w.now_ms - 2 * HOUR_MS))                            # a new request since the scan
    snapshot = {**stale, "_key": KEY}

    async def listing(org_id: str, *, after_key: str | None = None, limit: int = 100) -> list[dict]:
        return [snapshot] if after_key is None else []

    w.graph.get_records_with_refresh_intent = listing                                      # type: ignore[method-assign]
    await w.intent_sweep()
    assert w.row[ri.ID_FIELD] == "B"
    w.row.update(ri.intent_fields("A", w.now_ms - 2 * HOUR_MS))                            # A again, but renewed after the scan
    w.row[ri.AT_FIELD] = w.now_ms - 60_000
    w.row[ri.DONE_ID_FIELD] = "A"
    real = w.graph.update_node_fields_if_match
    moved = {"done": False}

    async def hand_over_before_the_clear(key: str, collection: str, updates: dict, expected: dict, *a: object, **kw: object) -> bool:
        if not moved["done"] and ri.ID_FIELD in expected and ri.AT_FIELD in expected:
            moved["done"] = True
            w.row[ri.AT_FIELD] = w.now_ms                                                   # renewed between the re-read and the clear
        return await real(key, collection, updates, expected, *a, **kw)

    w.graph.update_node_fields_if_match = hand_over_before_the_clear                       # type: ignore[method-assign]
    await w.intent_sweep()
    assert moved["done"] and w.row.get(ri.ID_FIELD) == "A"


async def test_a_lease_lost_mid_send_does_not_buy_twice(w: World) -> None:
    """A run serves the request between the sweep's re-read and its send: the sweep's event is then not a second refresh."""
    w.row.update(ri.intent_fields("A", w.now_ms - 2 * HOUR_MS))
    w.row["indexingStatus"] = ProgressStatus.COMPLETED.value
    real_send = world_mod._Producer.send_event

    async def send_after_a_run_served_it(self, *, topic, event_type, payload, key):  # noqa: ANN202
        run = await w.handler._settle_refresh_intent(
            EventTypes.UPDATE_RECORD.value, {"recordId": KEY, "orgId": ORG, "version": 1}, dict(w.row), KEY, parse_now=False,
        )
        w.row["indexingStatus"] = ProgressStatus.COMPLETED.value
        await w.handler._end_refresh_run(KEY, run, {"recordId": KEY})
        await real_send(self, topic=topic, event_type=event_type, payload=payload, key=key)

    with patch.object(world_mod._Producer, "send_event", send_after_a_run_served_it):
        await w.intent_sweep()
    assert w.msgs
    await w.consume(allow_loss=False)
    assert w.active is None or not w.active["run"].refresh, (w.notes, w.row)


async def test_a_served_clear_leaves_a_request_that_the_row_no_longer_says_is_served(w: World) -> None:
    """The scan saw request A served; by the time the lease is held the completion on the row is another request's."""
    w.row.update(ri.intent_fields("A", w.now_ms - 2 * HOUR_MS))
    w.row[ri.DONE_ID_FIELD] = "A"
    snapshot = {**w.row, "_key": KEY}
    w.row[ri.DONE_ID_FIELD] = "Z"                                                          # not A's completion any more

    async def listing(org_id: str, *, after_key: str | None = None, limit: int = 100) -> list[dict]:
        return [snapshot] if after_key is None else []

    w.graph.get_records_with_refresh_intent = listing                                      # type: ignore[method-assign]
    await w.intent_sweep()
    assert w.row[ri.ID_FIELD] == "A", "a request is cleared as served only while the row, under the lease, still says so"


async def test_a_connector_clear_names_the_time_it_read(w: World) -> None:
    """The connector looked off; a hand-over between the sweep's re-read and its clear renews the request's time: the clear does not undo it."""
    w.graph.docs["c1"] = {"_key": "c1", "isActive": False}
    w.row.update(ri.intent_fields("A", w.now_ms - 2 * HOUR_MS))
    real = w.graph.update_node_fields_if_match
    moved = {"done": False}

    async def hand_over_before_the_clear(key: str, collection: str, updates: dict, expected: dict, *a: object, **kw: object) -> bool:
        if not moved["done"] and ri.ID_FIELD in expected and ri.AT_FIELD in expected:
            moved["done"] = True
            w.row[ri.AT_FIELD] = w.now_ms
        return await real(key, collection, updates, expected, *a, **kw)

    w.graph.update_node_fields_if_match = hand_over_before_the_clear                       # type: ignore[method-assign]
    await w.intent_sweep()
    assert moved["done"] and w.row.get(ri.ID_FIELD) == "A"


@pytest.mark.parametrize("skew_ms", [0, -5_000, -65_000, -300_000, 5_000])
async def test_a_request_made_during_a_run_over_the_life_is_handed_over_whatever_the_walks_clock(w: World, skew_ms: int) -> None:
    """No request at the run's start; one is made during a run of more than 24 h, stamped by a walk host whose clock is ``skew_ms`` off
    (behind, it reads as older than the run's start). It is a request the run had not seen, so it is handed over, never dropped."""
    await w.plain_update()
    await w.consume()
    assert w.active is not None and w.active["run"].id_at_start is None
    started = w.now_ms
    w.now_ms += 25 * HOUR_MS                                                               # the run outlasts the life
    w.row.update(ri.intent_fields("mid", started + skew_ms - 1_000))                       # stamped before the run's start on a slow host
    await w.finish(allow_crash=False)
    handed = [m for m in w.msgs if (m["payload"] or {}).get(ri.PAYLOAD_ID_KEY) == "mid"]
    assert len(handed) == 1 and w.row[ri.ID_FIELD] == "mid", (skew_ms, w.row, w.msgs)


async def test_a_request_that_was_already_there_expired_at_the_runs_start_is_not_handed_over(w: World) -> None:
    w.row.update(ri.intent_fields("old", w.now_ms - 26 * HOUR_MS))
    w.msgs.append({"event_type": EventTypes.UPDATE_RECORD.value, "payload": {"recordId": KEY, "orgId": ORG, "version": 1}})
    await w.consume(allow_loss=False)
    assert w.active is not None and w.active["run"].id_at_start == "old"
    await w.finish(allow_crash=False)
    assert not [m for m in w.msgs if (m["payload"] or {}).get(ri.PAYLOAD_ID_KEY) == "old"]
