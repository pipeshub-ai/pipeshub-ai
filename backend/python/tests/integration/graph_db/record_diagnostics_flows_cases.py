"""The test bodies of the record diagnostics flows, shared by the ArangoDB/Neo4j file and the PostgreSQL file.

Each of those files provides the ``store`` fixture and imports every ``test_*`` here, so one body runs on every store.
(This file is not collected by itself: its name does not start with ``test_``.)
"""

from __future__ import annotations

import asyncio
import functools
import os
import random
from typing import Any

import pytest

from app.modules.indexing.record_diagnostics import aql_parts, cypher_parts, sql_patch
from app.schema.node_validator import SchemaValidationError
from app.services.graph_db.arango import arango_http_provider as arango_module
from app.services.graph_db.neo4j import neo4j_provider as neo4j_module
from app.services.graph_db.postgres import codegraph as pg_codegraph
from app.services.graph_db.postgres import records as pg_records
from app.services.graph_db.postgres import sync_state as pg_sync_state
from tests.integration.graph_db import record_diagnostics_flows_support as flows
from tests.integration.graph_db.record_diagnostics_flows_support import (
    FAIL,
    FLOWS,
    IP,
    NS,
    OFF,
    OK,
    Flow,
    Q,
    check,
    within,
)
from tests.integration.graph_db.record_diagnostics_support import (
    RECORDS,
    Store,
    use_pre_diagnostics_validator,
)


def break_the_rule(monkeypatch: pytest.MonkeyPatch, **kw: Any) -> None:  # noqa: ANN401
    """Run the store's statements with a part of the rule left out."""
    monkeypatch.setattr(arango_module, "aql_parts", functools.partial(aql_parts, **kw))
    monkeypatch.setattr(neo4j_module, "cypher_parts", functools.partial(cypher_parts, **kw))
    for module in (pg_records, pg_sync_state, pg_codegraph):
        monkeypatch.setattr(module, "sql_patch", functools.partial(sql_patch, **kw))


@pytest.mark.parametrize("name", sorted(FLOWS))
async def test_flow(store: Store, name: str) -> None:
    steps, expect = FLOWS[name]
    flow = await Flow(store).run(*steps)
    assert check(flow, await flow.fields(), expect) == [], name


async def test_f18_a_failure_handed_to_a_queued_duplicate_stamps_it(store: Store) -> None:
    """The primary fails; the duplicate that was waiting behind it takes FAILED and `failedAt`."""
    primary, copy = store.key("primary"), store.key("copy")
    for key in (primary, copy):
        await store.put(key, indexingStatus="QUEUED" if key == copy else "IN_PROGRESS", md5Checksum="abc", sizeInBytes=10, extractionStatus="NOT_STARTED")
    before = flows.get_epoch_timestamp_in_ms()

    await store.p.update_queued_duplicates_status(primary, "FAILED", None, None, "no")

    doc = await store.doc(copy)
    assert doc["indexingStatus"] == "FAILED" and doc["failedAt"] >= before


async def test_f19_a_save_that_read_a_closed_record_while_the_consumer_started_it_stays_within_the_race(store: Store) -> None:
    """The save reads COMPLETED; the consumer's forceReindex starts a run; the save then writes. The error is bounded by
    the race (both writes are 'now'); reported, not zero."""
    flow = Flow(store)
    await flow.run("seed_complete")
    snapshot = await store.doc(flow.key)
    t0 = flows.get_epoch_timestamp_in_ms()
    await flow.op_attempt()
    await asyncio.sleep(0.004)
    stored = flows.Record.from_arango_base_record({**snapshot, "_key": flow.key})
    entity = flows.Record.from_arango_base_record(flows.base_record(flow.key, store.org, connectorId=flow.connector_id))
    entity.indexing_status = flows.NS
    flows.DataSourceEntitiesProcessor._stamp_queued_at(entity, stored)
    await flow._upsert_entity(entity)
    t1 = flows.get_epoch_timestamp_in_ms()

    fields = await flow.fields()
    assert t0 <= fields["firstQueuedAt"] <= t1
    print(f"F19 race width: {t1 - t0} ms")


async def test_f20_a_store_only_parse_now_run_writes_none_of_the_run_fields(store: Store) -> None:
    from app.modules.indexing.parse_now_marker import (
        ParseNowRun,
        begin_run,
        end_run,
        shape_status_write,
    )

    flow = Flow(store)
    await flow.run("save_new", "cas_queued", "attempt")
    before = await flow.fields()
    begin_run(ParseNowRun(store_only=True))
    try:
        shaped = shape_status_write({"indexingStatus": "FAILED", "failedAt": 5, "firstQueuedAt": 1, "indexingAttempts": 9, "pageCount": 4})
    finally:
        end_run()
    await store.p.update_node(flow.key, RECORDS, shaped)

    after = await flow.fields()
    assert (after["firstQueuedAt"], after["indexingAttempts"]) == (before["firstQueuedAt"], before["indexingAttempts"])
    assert "failedAt" not in after and after["pageCount"] == 4


async def test_a_record_from_before_the_fields_in_the_middle_of_a_run_continues_from_its_queue_time(store: Store) -> None:
    flow = Flow(store)
    await flow.run("seed_queued_before_the_fields", "attempt")
    stored = await store.doc(flow.key)
    assert stored["firstQueuedAt"] == stored["queuedAtTimestamp"] and stored["indexingAttempts"] == 1


async def test_f22_the_residual_of_a_failure_written_by_an_older_process_is_reported(store: Store) -> None:
    """An older process fails the record and then resets it without a stamp of its own: the closed run cannot be told
    from an open one, so it continues (reported, not asserted correct)."""
    flow = Flow(store)
    await flow.run("save_new", "cas_queued", "attempt", "fail_by_older_process", "reset_by_older_process", "cas_queued", "attempt")
    fields = await flow.fields()
    print(f"F22 residual: attempts={fields['indexingAttempts']} (a new run would read 1)")
    assert fields["indexingAttempts"] in (1, 2)


# ---- negative controls: each flow fails when its part of the rule is left out ----------------------------------------


@pytest.mark.parametrize("name", ["F4", "F5", "F6", "F7", "F8"])
async def test_a_rule_that_always_continues_fails_the_flow(store: Store, monkeypatch: pytest.MonkeyPatch, name: str) -> None:
    break_the_rule(monkeypatch, always_open=True)
    steps, expect = FLOWS[name]
    flow = await Flow(store).run(*steps)
    assert check(flow, await flow.fields(), expect) != [], f"{name} still passed with a rule that always continues"


async def test_a_rule_without_the_outcome_stamps_fails_f22(store: Store, monkeypatch: pytest.MonkeyPatch) -> None:
    break_the_rule(monkeypatch, outcome_stamps=())
    steps, expect = FLOWS["F22"]
    flow = await Flow(store).run(*steps)
    assert check(flow, await flow.fields(), expect) != []


async def test_a_sink_only_end_that_keeps_the_start_fails_f9(store: Store) -> None:
    steps, expect = FLOWS["F9"]
    flow = await Flow(store, sink_clears=False).run(*steps)
    assert check(flow, await flow.fields(), expect) != []


async def test_a_restore_that_continues_the_stored_run_fails_f21(store: Store) -> None:
    flow = await Flow(store).run("save_new", "cas_queued", "trash", "restore_stale", "attempt")
    assert check(flow, await flow.fields(), FLOWS["F21"][1]) != []


# ---- an older process's validator --------------------------------------------------------------------------


async def old_validator_body(store: Store, monkeypatch: pytest.MonkeyPatch) -> None:
    """A process whose validator does not list the fields still updates a record that carries them (a partial update
    validates only the keys it sends); a full replace is refused, which is the control that proves the case."""
    key = store.key("old")
    await store.put(key, firstQueuedAt=5, indexingAttempts=1, pageCount=3)
    use_pre_diagnostics_validator(monkeypatch)

    assert await store.p.update_node(key, RECORDS, {"indexingStatus": "QUEUED"})
    doc = await store.doc(key)
    assert doc["firstQueuedAt"] == 5 and doc["pageCount"] == 3
    with pytest.raises(SchemaValidationError):
        await store.p.update_node_if_match(key, RECORDS, {**doc, "id": key, "_key": key}, "updatedAtTimestamp", doc["updatedAtTimestamp"])


# ---- the attempt counter is incremented inside the statement ------------------------------------------------


async def test_attempt_increment_in_statement(store: Store) -> None:
    flow = Flow(store)
    await flow.run("save_new", "cas_queued")

    await asyncio.gather(*(flow.op_attempt() for _ in range(8)))

    assert (await flow.fields())["indexingAttempts"] == 8


async def test_a_reset_between_the_read_and_the_write_starts_a_new_run_not_old_plus_one(store: Store) -> None:
    flow = Flow(store)
    await flow.run("save_new", "cas_queued", "attempt", "attempt", "attempt", "complete")

    await flow.op_attempt()  # the record closed meanwhile: a start, attempts 1

    assert (await flow.fields())["indexingAttempts"] == 1


async def test_a_read_modify_write_loses_increments(store: Store) -> None:
    """The negative control: the same eight attempts done as a read in Python and a write of old + 1."""
    flow = Flow(store)
    await flow.run("save_new", "cas_queued")
    docs = await asyncio.gather(*(store.doc(flow.key) for _ in range(8)))

    await asyncio.gather(*(store.p.update_node(flow.key, RECORDS, {"indexingAttempts": (d.get("indexingAttempts") or 0) + 1}) for d in docs))

    assert (await flow.fields())["indexingAttempts"] == 1


async def test_a_write_conditional_on_the_snapshot_applies_to_an_unchanged_record(store: Store) -> None:
    """The conditional parse-complete write on every store (a revision guard on ArangoDB, the field-checked statement elsewhere)."""
    key = store.key("snap")
    await store.put(key, contentRev="rev-a", indexingStatus="IN_PROGRESS")
    snapshot = await store.doc(key)

    assert await store.p.update_node_if_unchanged(key, RECORDS, {"parsingStatus": "COMPLETED", "pageCount": 9}, snapshot, {"contentRev": "rev-a", "indexingStatus": "IN_PROGRESS"})
    assert (await store.doc(key))["pageCount"] == 9

    other = store.key("snap")
    await store.put(other, contentRev="rev-b", indexingStatus="IN_PROGRESS")
    assert not await store.p.update_node_if_unchanged(other, RECORDS, {"pageCount": 9}, await store.doc(other), {"contentRev": "rev-a"})
    assert (await store.doc(other)).get("pageCount") is None


SEQUENCES = int(os.environ.get("RD_SEQUENCES", "20"))

# op -> (statuses it may run from, kind for the oracle, status after)
RANDOM_OPS = {
    "cas_queued": ({NS}, "queue", Q),
    ("bulk", NS): ({NS, Q, IP, OK, FAIL}, "queue", NS),
    "reset_connector": ({FAIL}, "queue", NS),
    ("save_existing", NS): ({NS, OK, FAIL}, "queue", NS),
    ("save_existing", Q): ({NS, Q}, "queue", Q),
    "attempt": ({NS, Q, IP, OK, FAIL}, "attempt", IP),
    "revert": ({IP}, "queue", Q),
    "defer": ({IP}, "queue", Q),
    "cut": ({IP}, "queue", Q),
    "stale_recovery": ({IP}, "queue", Q),
    "complete": ({IP}, "end", OK),
    "fail": ({IP}, "end", FAIL),
    "sink_end": ({IP}, "end", NS),
    "connector_off": ({NS, Q, OK, FAIL}, "end", OFF),
    "connector_on": ({OFF}, "queue", Q),
    "trash_restore": ({NS, Q, IP, OK, FAIL}, "restore", NS),
}


async def test_random_flows_match_the_oracle(store: Store) -> None:
    seed = int(os.environ.get("RD_SEED") or random.SystemRandom().randint(1, 10**9))
    rng = random.Random(seed)
    wrong = []
    for _n in range(SEQUENCES):
        flow = Flow(store)
        await flow.run("save_new")
        status, in_run, start, attempts, names = NS, True, 1, 0, ["save_new"]
        for _ in range(rng.randint(2, 9)):
            choices = [op for op, (allowed, _, _) in RANDOM_OPS.items() if status in allowed]
            op = rng.choice(choices)
            if op == "trash_restore":
                await flow.run("trash", "restore")
                step = len(flow.marks)
                in_run, start, attempts = True, step, 0
            else:
                await flow.run(op)
                step = len(flow.marks)
                kind = RANDOM_OPS[op][1]
                if kind in ("queue", "attempt") and not in_run:
                    in_run, start, attempts = True, step, 0
                if kind == "attempt":
                    attempts += 1
                if kind == "end":
                    in_run = False
            status = RANDOM_OPS[op][2]
            names.append(op if isinstance(op, str) else f"{op[0]}:{op[1]}")
        if in_run:
            got = await flow.fields()
            if not within(got.get("firstQueuedAt"), flow.time_of(start)) or got.get("indexingAttempts") != attempts:
                wrong.append((names, {"want_start_step": start, "want_attempts": attempts}, got))
    print(f"{store.name} seed={seed}: {SEQUENCES} sequences, {len(wrong)} wrong")
    assert wrong == [], f"seed={seed}: {wrong[:3]}"


TEST_NAMES = ['test_random_flows_match_the_oracle', 'test_a_write_conditional_on_the_snapshot_applies_to_an_unchanged_record', 'test_flow', 'test_f18_a_failure_handed_to_a_queued_duplicate_stamps_it', 'test_f19_a_save_that_read_a_closed_record_while_the_consumer_started_it_stays_within_the_race', 'test_f20_a_store_only_parse_now_run_writes_none_of_the_run_fields', 'test_a_record_from_before_the_fields_in_the_middle_of_a_run_continues_from_its_queue_time', 'test_f22_the_residual_of_a_failure_written_by_an_older_process_is_reported', 'test_a_rule_that_always_continues_fails_the_flow', 'test_a_rule_without_the_outcome_stamps_fails_f22', 'test_a_sink_only_end_that_keeps_the_start_fails_f9', 'test_a_restore_that_continues_the_stored_run_fails_f21', 'test_attempt_increment_in_statement', 'test_a_reset_between_the_read_and_the_write_starts_a_new_run_not_old_plus_one', 'test_a_read_modify_write_loses_increments']
