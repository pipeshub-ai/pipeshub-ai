"""The record processing diagnostics: the run rule, the page-count writes, and parse-now.

Generated runs against an independent oracle, the page count writes, parse-now store-only runs,
and the shape of the generated statement fragments. The fragments against real stores are in
``tests/integration/graph_db/test_record_diagnostics_conformance.py``.
"""

from __future__ import annotations

import contextlib
import logging
import os
import random
import re
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import ProgressStatus
from app.events.events import EventProcessor
from app.modules.indexing import parse_now_marker
from app.modules.indexing.parse_now_marker import ParseNowRun, begin_run, end_run
from app.modules.indexing.record_diagnostics import (
    FIRST_QUEUED_AT,
    RUN_FIELDS,
    WriteKind,
    apply_reference,
    aql_parts,
    cypher_parts,
    is_number,
    is_open,
    page_count_of,
    reference,
    sql_patch,
)


@contextlib.contextmanager
def _run(*, store_only: bool):  # noqa: ANN202
    begin_run(ParseNowRun(store_only=store_only))
    try:
        yield
    finally:
        end_run()


NS, Q, IP = ProgressStatus.NOT_STARTED.value, ProgressStatus.QUEUED.value, ProgressStatus.IN_PROGRESS.value
OK, FAIL = ProgressStatus.COMPLETED.value, ProgressStatus.FAILED.value


class TestTheRule:
    def test_a_queue_write_on_a_closed_record_starts_a_run(self) -> None:
        assert reference({"indexingStatus": OK}, WriteKind.QUEUE, 100) == {"firstQueuedAt": 100, "indexingAttempts": 0}

    def test_a_queue_write_inside_a_run_keeps_the_start_and_the_attempts(self) -> None:
        stored = {"indexingStatus": IP, "firstQueuedAt": 40, "indexingAttempts": 2}
        assert reference(stored, WriteKind.QUEUE, 100) == {"firstQueuedAt": 40}

    def test_a_record_that_was_mid_run_before_the_fields_existed_continues_from_its_queue_time(self) -> None:
        assert reference({"indexingStatus": Q, "queuedAtTimestamp": 7}, WriteKind.QUEUE, 100) == {"firstQueuedAt": 7}
        assert reference({"indexingStatus": NS, "queuedAtTimestamp": 7}, WriteKind.QUEUE, 100) == {"firstQueuedAt": 100, "indexingAttempts": 0}

    def test_an_attempt_counts_in_the_statement(self) -> None:
        stored = {"indexingStatus": Q, "firstQueuedAt": 40, "indexingAttempts": 2}
        assert reference(stored, WriteKind.ATTEMPT_START, 100) == {"firstQueuedAt": 40, "indexingAttempts": 3}
        assert reference({"indexingStatus": OK}, WriteKind.ATTEMPT_START, 100) == {"firstQueuedAt": 100, "indexingAttempts": 1}

    @pytest.mark.parametrize("outcome", [100, 250])
    def test_a_new_run_starts_after_the_last_outcome_even_when_the_clock_is_not_later(self, outcome: int) -> None:
        """An outcome at or after a run's start ends that run, so a start at the same time (or before a later outcome) is already closed."""
        stored = {"indexingStatus": NS, "firstQueuedAt": 10, "lastIndexTimestamp": outcome}
        for kind, extra in ((WriteKind.QUEUE, {"indexingAttempts": 0}), (WriteKind.ATTEMPT_START, {"indexingAttempts": 1})):
            written = reference(stored, kind, 100)
            assert written == {"firstQueuedAt": max(100, outcome + 1) if outcome >= 100 else 100, **extra}, kind
            assert written["firstQueuedAt"] > outcome
            assert is_open({**stored, "indexingStatus": Q, "firstQueuedAt": written["firstQueuedAt"]})

    def test_an_attempt_on_other_content_drops_the_stored_page_count(self) -> None:
        stored = {"indexingStatus": Q, "firstQueuedAt": 1, "contentRev": "a", "pageCount": 9}
        assert reference(stored, WriteKind.ATTEMPT_START, 100, content_rev="b")["pageCount"] is None
        assert "pageCount" not in reference(stored, WriteKind.ATTEMPT_START, 100, content_rev="a")
        assert reference({**stored, "contentRev": 5}, WriteKind.ATTEMPT_START, 100, content_rev="a")["pageCount"] is None

    def test_a_reset_by_an_older_process_does_not_continue_the_closed_run(self) -> None:
        # the closed run's start stays on a NOT_STARTED record; its outcome stamp is newer than it
        stored = {"indexingStatus": NS, "firstQueuedAt": 10, "lastIndexTimestamp": 50}
        assert not is_open(stored)
        assert reference(stored, WriteKind.QUEUE, 100) == {"firstQueuedAt": 100, "indexingAttempts": 0}
        assert is_open({**stored, "lastIndexTimestamp": 10}) is False  # a tie is closed
        assert is_open({**stored, "lastIndexTimestamp": 9}) is True

    @pytest.mark.parametrize("junk", ["abc", True, None, "", [], {}])
    def test_a_value_that_is_not_a_number_reads_as_absent(self, junk: Any) -> None:  # noqa: ANN401
        assert not is_number(junk)
        stored = {"indexingStatus": Q, "firstQueuedAt": junk, "queuedAtTimestamp": junk, "indexingAttempts": junk}
        assert reference(stored, WriteKind.ATTEMPT_START, 100) == {"firstQueuedAt": 100, "indexingAttempts": 1}

    def test_status_var_picks_the_rule_from_the_status_written(self) -> None:
        assert reference({"indexingStatus": FAIL}, WriteKind.STATUS_VAR, 9, status=NS) == {"firstQueuedAt": 9, "indexingAttempts": 0}
        assert reference({}, WriteKind.STATUS_VAR, 9, status=FAIL) == {"failedAt": 9}
        assert reference({}, WriteKind.STATUS_VAR, 9, status=OK) == {}
        assert reference({}, WriteKind.STATUS_VAR, 9, status=IP) == {}

    def test_a_sink_only_end_clears_the_start_and_a_restore_always_starts(self) -> None:
        assert reference({"indexingStatus": Q, "firstQueuedAt": 5}, WriteKind.SINK_END, 9) == {"firstQueuedAt": None}
        assert reference({"indexingStatus": Q, "firstQueuedAt": 5}, WriteKind.START, 9) == {"firstQueuedAt": 9, "indexingAttempts": 0}

    def test_a_record_that_does_not_exist_yet_starts_a_run(self) -> None:
        assert reference(None, WriteKind.QUEUE, 3) == {"firstQueuedAt": 3, "indexingAttempts": 0}

    @pytest.mark.parametrize(("value", "want"), [(1, 1), (7.0, 7), (1_000_000, 1_000_000), (0, None), (1_000_001, None), (2.5, None), (True, None), ("3", None), (None, None)])
    def test_page_count_of(self, value: Any, want: int | None) -> None:  # noqa: ANN401
        assert page_count_of(value) == want


# ---- generated runs against an independent oracle ------------------------------------------------------

_EVENTS = ("queue", "attempt", "fail", "complete", "sink_end", "restore", "defer", "revert")


class _Oracle:
    """A run opens at the first queue or attempt after an outcome and ends at an outcome. Written from the
    description of a run, not from the rule's conditions."""

    def __init__(self) -> None:
        self.in_run = False
        self.start: int | None = None
        self.attempts = 0

    def event(self, kind: str, t: int) -> None:
        if kind in ("queue", "defer", "revert"):
            if not self.in_run:
                self.in_run, self.start, self.attempts = True, t, 0
        elif kind == "attempt":
            if not self.in_run:
                self.in_run, self.start, self.attempts = True, t, 0
            self.attempts += 1
        elif kind in ("fail", "complete", "sink_end"):
            self.in_run = False
        elif kind == "restore":
            self.in_run, self.start, self.attempts = True, t, 0


def _applies(kind: str, status: str | None) -> bool:
    """An event that cannot happen to a record in this status is skipped by both the store and the oracle."""
    if kind in ("defer", "revert", "fail", "complete", "sink_end"):
        return status == IP
    if kind in ("queue", "attempt"):
        return status != IP
    return True  # restore


def _step(stored: dict[str, Any], kind: str, t: int, *, sink_clears: bool) -> dict[str, Any]:
    if kind == "queue":
        return {**apply_reference(stored, WriteKind.QUEUE, t), "indexingStatus": Q, "queuedAtTimestamp": t}
    if kind == "attempt":
        return {**apply_reference(stored, WriteKind.ATTEMPT_START, t), "indexingStatus": IP}
    if kind in ("defer", "revert"):
        return {**apply_reference(stored, WriteKind.QUEUE, t), "indexingStatus": Q, "queuedAtTimestamp": t}
    if kind == "fail":
        return {**apply_reference(stored, WriteKind.FAILED, t), "indexingStatus": FAIL, "lastIndexTimestamp": t}
    if kind == "complete":
        return {**stored, "indexingStatus": OK, "lastIndexTimestamp": t}
    if kind == "sink_end":
        after = apply_reference(stored, WriteKind.SINK_END, t) if sink_clears else dict(stored)
        return {**after, "indexingStatus": NS}
    return {**apply_reference(stored, WriteKind.START, t), "indexingStatus": NS, "restoredAtTimestamp": t - 1}


def _world(events: list[str], *, sink_clears: bool = True) -> tuple[dict[str, Any], _Oracle]:
    stored: dict[str, Any] = {}
    oracle = _Oracle()
    for step, kind in enumerate(events, start=1):
        if not _applies(kind, stored.get("indexingStatus")):
            continue
        stored = _step(stored, kind, step * 10, sink_clears=sink_clears)
        oracle.event(kind, step * 10)
    return stored, oracle


def _compare(stored: dict[str, Any], oracle: _Oracle) -> bool:
    """Where the record is inside a run, it carries the oracle's start and attempt count."""
    if not oracle.in_run:
        return True
    return stored.get(FIRST_QUEUED_AT) == oracle.start and stored.get("indexingAttempts") == oracle.attempts


def _generated(seed: int, count: int, **kw: Any) -> list[tuple[list[str], dict[str, Any], _Oracle]]:  # noqa: ANN401
    rng = random.Random(seed)
    out = []
    for _ in range(count):
        events = [rng.choice(_EVENTS) for _ in range(rng.randint(2, 14))]
        stored, oracle = _world(events, **kw)
        out.append((events, stored, oracle))
    return out


@pytest.mark.parametrize("draw", [0, 1, 2])
def test_generated_runs(draw: int) -> None:
    """2,000 generated event sequences per seed; at the end of each, a record that is inside a run carries the
    oracle's start and attempt count. A reset by an older process is left out of this oracle (it is the F22 flow,
    in the real-store flows test)."""
    seed = _fresh_seed()
    wrong = [events for events, stored, oracle in _generated(seed, 2000) if not _compare(stored, oracle)]
    assert wrong == [], f"seed {seed}: {len(wrong)} sequences differ, e.g. {wrong[:3]}"


def _fresh_seed() -> int:
    """A new seed each run, printed so a failure can be repeated with ``RD_SEED``."""
    seed = int(os.environ.get("RD_SEED") or random.SystemRandom().randint(1, 10**9))
    print(f"generated runs seed={seed}")
    return seed


def test_the_oracle_sees_a_sink_only_end_that_does_not_clear_the_start() -> None:
    """The negative control: without the delete at the sink-only end, a later attempt continues a run that ended."""
    wrong = [events for events, stored, oracle in _generated(_fresh_seed(), 2000, sink_clears=False) if not _compare(stored, oracle)]
    assert wrong, "the oracle did not see the missing delete"


# ---- the generated fragments ---------------------------------------------------------------------------------------

_AQL_BIND = re.compile(r"@(\w+)")


@pytest.mark.parametrize("kind", list(WriteKind))
def test_every_fragment_binds_only_what_it_names(kind: WriteKind) -> None:
    """AQL refuses a declared bind variable the statement does not use, so the names a fragment needs are exactly the
    ones in its text."""
    lets, patch = aql_parts("doc", kind, rev="@rev")
    names = set(_AQL_BIND.findall(lets + patch))
    assert names <= {"now", "status", "rev"}
    cypher_with, cypher_patch = cypher_parts("n", kind, rev="$rev")
    assert set(re.findall(r"\$(\w+)", cypher_with + cypher_patch)) <= {"now", "status", "rev"}
    assert set(re.findall(r"\$(\d+)", sql_patch("doc", kind, now="$1::bigint", status="$2::text", rev="$3::text"))) <= {"1", "2", "3"}


def test_the_cypher_fragment_captures_before_it_writes() -> None:
    with_clause, _ = cypher_parts("n", WriteKind.ATTEMPT_START)
    assert with_clause.startswith("WITH n, n.indexingStatus AS _st")
    for stored in ("firstQueuedAt", "queuedAtTimestamp", "indexingAttempts", "failedAt", "lastIndexTimestamp", "restoredAtTimestamp", "contentRev"):
        assert f"n.{stored} AS" in with_clause


def test_every_comparison_is_guarded_on_both_operands() -> None:
    lets, _ = aql_parts("doc", WriteKind.QUEUE)
    assert "IS_NUMBER(doc.firstQueuedAt)" in lets and all(f"IS_NUMBER(doc.{n})" in lets for n in ("failedAt", "lastIndexTimestamp", "restoredAtTimestamp", "queuedAtTimestamp"))
    assert "doc->'firstQueuedAt' IS NULL" not in sql_patch("doc", WriteKind.QUEUE)
    assert "<> null" not in cypher_parts("n", WriteKind.QUEUE)[1]


def test_the_run_fields_are_the_three_the_rule_writes() -> None:
    assert RUN_FIELDS == ("firstQueuedAt", "indexingAttempts", "failedAt")


# ---- the page count writes -------------------------------------------------------------------------------


def _processor(**graph: Any) -> EventProcessor:  # noqa: ANN401
    processor = EventProcessor.__new__(EventProcessor)
    processor.logger = logging.getLogger("test")
    provider = MagicMock()
    provider.update_node = AsyncMock(return_value=True)
    provider.write_attempt_start = AsyncMock(return_value=True)
    provider.update_node_fields_if_match = AsyncMock(return_value=True)
    provider.update_node_if_unchanged = AsyncMock(return_value=True)
    for name, value in graph.items():
        setattr(provider, name, value)
    processor.graph_provider = provider
    return processor


class TestPageCountWrites:
    async def test_the_in_process_count_is_one_conditional_write_on_the_revision_and_the_attempt(self) -> None:
        processor = _processor()

        await processor._write_page_count("r1", "rev-a", 12)

        processor.graph_provider.update_node_fields_if_match.assert_awaited_once()
        args = processor.graph_provider.update_node_fields_if_match.await_args.args
        assert args[2] == {"pageCount": 12}
        assert args[3] == {"contentRev": "rev-a", "indexingStatus": IP}

    async def test_a_count_the_record_held_before_the_attempt_is_written_again(self) -> None:
        """The attempt's start write drops a count when the record moved to another revision after it was read, so what the record held
        when it was read says nothing about what it holds now."""
        processor = _processor()

        await processor._write_page_count("r1", "rev-a", 12)

        processor.graph_provider.update_node_fields_if_match.assert_awaited_once()

    @pytest.mark.parametrize("pages", [None, 0, "3", True, 1_000_001])
    async def test_no_usable_count_writes_nothing(self, pages: Any) -> None:  # noqa: ANN401
        processor = _processor()

        await processor._write_page_count("r1", "rev-a", pages)

        processor.graph_provider.update_node_fields_if_match.assert_not_awaited()

    async def test_a_store_only_run_is_matched_on_the_revision_alone(self) -> None:
        processor = _processor()
        with _run(store_only=True):
            await processor._write_page_count("r1", "rev-a", 4)

        assert processor.graph_provider.update_node_fields_if_match.await_args.args[3] == {"contentRev": "rev-a"}

    async def test_a_failed_count_write_never_fails_the_record(self) -> None:
        processor = _processor(update_node_fields_if_match=AsyncMock(side_effect=RuntimeError("down")))

        await processor._write_page_count("r1", "rev-a", 4)  # no raise

    async def test_a_late_write_of_one_revision_matches_nothing_on_another(self) -> None:
        """The write names its revision, so it cannot land on content that replaced it: the store answers False."""
        processor = _processor(update_node_fields_if_match=AsyncMock(return_value=False))

        await processor._write_page_count("r1", "rev-a", 4)

        assert processor.graph_provider.update_node_fields_if_match.await_args.args[3]["contentRev"] == "rev-a"

    async def test_the_service_count_rides_the_status_write_when_the_record_is_unchanged_since_it_was_read(self) -> None:
        processor = _processor(update_node_if_unchanged=AsyncMock(return_value=True))
        record = {"_key": "r1", "contentRev": "rev-a", "_rev": "_r1"}

        await processor._persist_parsing_completed(record, 30, "rev-a")

        call = processor.graph_provider.update_node_if_unchanged.await_args
        assert call.args[2] == {"parsingStatus": "COMPLETED", "pageCount": 30}
        assert call.args[3] is record
        assert call.args[4] == {"contentRev": "rev-a", "indexingStatus": IP}
        processor.graph_provider.update_node.assert_not_awaited()

    async def test_when_the_record_changed_the_plain_status_write_runs_without_a_count(self) -> None:
        processor = _processor(update_node_if_unchanged=AsyncMock(return_value=False))
        record = {"_key": "r1", "contentRev": "rev-a"}

        await processor._persist_parsing_completed(record, 30, "rev-a")

        processor.graph_provider.update_node.assert_awaited_once()
        assert processor.graph_provider.update_node.await_args.args[2] == {"parsingStatus": "COMPLETED"}

    async def test_the_count_is_tied_to_the_revision_that_was_parsed_not_the_one_read_afterwards(self) -> None:
        """Revision B replaced A while the service parsed A: the record read afterwards is on B, and A's count must not be written to it."""
        processor = _processor(update_node_if_unchanged=AsyncMock(return_value=False))
        record = {"_key": "r1", "contentRev": "rev-b"}

        await processor._persist_parsing_completed(record, 30, "rev-a")

        assert processor.graph_provider.update_node_if_unchanged.await_args.args[4]["contentRev"] == "rev-a"
        assert processor.graph_provider.update_node.await_args.args[2] == {"parsingStatus": "COMPLETED"}

    async def test_a_count_with_no_revision_to_tie_it_to_is_not_written(self) -> None:
        processor = _processor()

        await processor._persist_parsing_completed({"_key": "r1", "contentRev": "rev-a"}, 30)

        processor.graph_provider.update_node_if_unchanged.assert_not_awaited()
        assert processor.graph_provider.update_node.await_args.args[2] == {"parsingStatus": "COMPLETED"}

    @pytest.mark.parametrize("attempt_start", [True, False])
    async def test_a_store_only_write_that_sets_the_revision_drops_the_count_it_may_hold(self, attempt_start: bool) -> None:
        """A store-only run drops the status fields, so its attempt start is a plain write: it still must not leave the old revision's count."""
        processor = _processor(update_node_fields_if_match=AsyncMock(side_effect=[False, True]), get_document=AsyncMock(return_value={"contentRev": "rev-a"}))
        begin_run(ParseNowRun(store_only=True))
        try:
            await processor.update_record_fields({"_key": "r1"}, {"indexingStatus": IP, "contentRev": "rev-b"}, attempt_start=attempt_start)
        finally:
            end_run()

        processor.graph_provider.write_attempt_start.assert_not_awaited()
        written = processor.graph_provider.update_node_fields_if_match.await_args.args[2]
        assert written["contentRev"] == "rev-b" and written["pageCount"] is None and "indexingStatus" not in written

    async def test_an_attempt_start_leaves_the_count_to_its_own_statement(self) -> None:
        processor = _processor()

        await processor.update_record_fields({"_key": "r1"}, {"indexingStatus": IP, "contentRev": "rev-b"}, attempt_start=True)

        assert "pageCount" not in processor.graph_provider.write_attempt_start.await_args.args[1]

    async def test_a_write_of_the_revision_the_record_already_holds_keeps_the_count(self) -> None:
        """A finished-twin reuse writes the revision again without parsing: the count of that same content stays, as ``write_attempt_start`` keeps it."""
        processor = _processor()

        await processor.update_record_fields({"_key": "r1", "contentRev": "rev-a"}, {"contentRev": "rev-a", "reason": None})

        call = processor.graph_provider.update_node_fields_if_match.await_args
        assert call.args[3] == {"contentRev": "rev-a"} and "pageCount" not in call.args[2]
        processor.graph_provider.update_node.assert_not_awaited()

    async def test_a_record_that_moved_to_another_revision_since_it_was_read_is_written_with_the_count_dropped(self) -> None:
        processor = _processor(update_node_fields_if_match=AsyncMock(side_effect=[False, True]), get_document=AsyncMock(return_value={"contentRev": "rev-z"}))

        await processor.update_record_fields({"_key": "r1", "contentRev": "rev-a"}, {"contentRev": "rev-a", "reason": None})

        assert processor.graph_provider.update_node_fields_if_match.await_args.args[2]["pageCount"] is None

    async def test_the_decision_is_the_stores_not_the_snapshots(self) -> None:
        """The snapshot holds revision A, a concurrent writer set B and its count, and this write is of B: the store matches B and keeps the
        count. The other way round (the snapshot says B, the store holds A) matches nothing and drops it."""
        kept = _processor()
        await kept.update_record_fields({"_key": "r1", "contentRev": "rev-a"}, {"contentRev": "rev-b"})
        assert kept.graph_provider.update_node_fields_if_match.await_args.args[3] == {"contentRev": "rev-b"}
        kept.graph_provider.update_node.assert_not_awaited()

        dropped = _processor(update_node_fields_if_match=AsyncMock(side_effect=[False, True]), get_document=AsyncMock(return_value={"contentRev": "rev-a"}))
        await dropped.update_record_fields({"_key": "r1", "contentRev": "rev-b"}, {"contentRev": "rev-b"})
        assert dropped.graph_provider.update_node_fields_if_match.await_args.args[2] == {"contentRev": "rev-b", "pageCount": None}

    async def test_a_new_revision_drops_the_count_after_the_store_found_a_different_one(self) -> None:
        processor = _processor(update_node_fields_if_match=AsyncMock(side_effect=[False, True]), get_document=AsyncMock(return_value={"contentRev": "rev-a"}))

        await processor.update_record_fields({"_key": "r1", "contentRev": "rev-a"}, {"contentRev": "rev-b"})

        assert processor.graph_provider.update_node_fields_if_match.await_args.args[2] == {"contentRev": "rev-b", "pageCount": None}

    async def test_a_write_that_does_not_set_the_revision_leaves_the_count_alone(self) -> None:
        processor = _processor()

        await processor.update_record_fields({"_key": "r1"}, {"reason": "x"})

        assert "pageCount" not in processor.graph_provider.update_node.await_args.args[2]

    async def test_a_parse_without_a_count_is_the_unchanged_status_write(self) -> None:
        processor = _processor()

        await processor._persist_parsing_completed({"_key": "r1", "contentRev": "rev-a"}, None, "rev-a")

        processor.graph_provider.update_node_if_unchanged.assert_not_awaited()
        assert processor.graph_provider.update_node.await_args.args[2] == {"parsingStatus": "COMPLETED"}


# ---- parse-now ---------------------------------------------------------------------------------------------


class TestParseNow:
    def test_store_only_drops_run_fields(self) -> None:
        """A store-only run is not an attempt and ends no run: none of the run fields are written."""
        fields = {"indexingStatus": IP, "firstQueuedAt": 5, "indexingAttempts": 2, "failedAt": 9, "pageCount": 7, "recordName": "x"}
        with _run(store_only=True):
            shaped = parse_now_marker.shape_status_write(fields)

        assert not {"firstQueuedAt", "indexingAttempts", "failedAt"} & set(shaped)
        assert shaped["pageCount"] == 7 and shaped["recordName"] == "x"

    def test_a_normal_run_writes_them(self) -> None:
        fields = {"indexingStatus": IP, "firstQueuedAt": 5, "indexingAttempts": 2}
        assert {"firstQueuedAt", "indexingAttempts"} <= set(parse_now_marker.shape_status_write(fields))

    async def test_a_store_only_attempt_does_not_start_an_attempt(self) -> None:
        processor = _processor(write_attempt_start=AsyncMock(return_value=True))
        doc = {"_key": "r1"}
        with _run(store_only=True):
            await processor.update_record_fields(doc, {"indexingStatus": IP, "contentRev": "a"}, attempt_start=True)

        processor.graph_provider.write_attempt_start.assert_not_awaited()


# ---- build cost ---------------------------------------------------------------------------------------------

class _CountingStored(dict):
    """A stored record that counts every read, so the work ``reference`` does is a number, not a duration."""

    reads = 0

    def get(self, key: str, default: Any = None) -> Any:  # noqa: ANN401
        type(self).reads += 1
        return super().get(key, default)

    def __getitem__(self, key: str) -> Any:  # noqa: ANN401
        type(self).reads += 1
        return super().__getitem__(key)

    def __iter__(self) -> Any:  # noqa: ANN401
        type(self).reads += len(self)
        return super().__iter__()

    def items(self) -> Any:  # noqa: ANN401
        type(self).reads += len(self)
        return super().items()


def test_fragments_are_built_once_per_distinct_call_and_reused() -> None:
    """The statement fragments are cached: a repeat of the same call builds nothing, and the cache holds one entry per distinct call."""
    for build, call in (
        (aql_parts, lambda: aql_parts("doc", WriteKind.ATTEMPT_START, rev="@rev")),
        (cypher_parts, lambda: cypher_parts("n", WriteKind.ATTEMPT_START, rev="$rev")),
        (sql_patch, lambda: sql_patch("doc", WriteKind.ATTEMPT_START, rev="$5::text")),
    ):
        build.cache_clear()
        first = call()
        for _ in range(1000):
            assert call() is first
        info = build.cache_info()
        assert (info.misses, info.hits) == (1, 1000), build.__name__


def test_reference_reads_a_fixed_number_of_fields_whatever_the_record_holds() -> None:
    """The reference's cost does not grow with the stored record: it reads the same few fields of a 5-field record and a 5000-field one."""
    small = {"indexingStatus": "QUEUED", "firstQueuedAt": 5, "indexingAttempts": 2, "contentRev": "a"}
    reads = {}
    for name, extra in (("small", 0), ("large", 5000)):
        stored = _CountingStored(small, **{f"x{i}": i for i in range(extra)})
        _CountingStored.reads = 0
        reference(stored, WriteKind.ATTEMPT_START, 100, content_rev="a")
        reads[name] = _CountingStored.reads
    assert reads["small"] == reads["large"] and 0 < reads["large"] <= 20


class TestTheCountRuleOfEveryRevisionWriter:
    async def test_the_shared_write_keeps_the_count_only_when_the_store_holds_the_revision(self) -> None:
        from app.modules.indexing.record_diagnostics import write_with_revision_rule

        graph = MagicMock()
        graph.update_node = AsyncMock(return_value=True)
        graph.update_node_fields_if_match = AsyncMock(return_value=True)
        assert await write_with_revision_rule(graph, "r1", "records", {"contentRev": "a", "reason": None})
        graph.update_node.assert_not_awaited()
        assert graph.update_node_fields_if_match.await_args.args[3] == {"contentRev": "a"}

        # the revision differs: the clearing write is conditional on the revision that was read, never plain
        graph.update_node_fields_if_match = AsyncMock(side_effect=[False, True])
        graph.get_document = AsyncMock(return_value={"contentRev": "z"})
        assert await write_with_revision_rule(graph, "r1", "records", {"contentRev": "a"})
        graph.update_node.assert_not_awaited()
        assert graph.update_node_fields_if_match.await_args.args[2] == {"contentRev": "a", "pageCount": None}
        assert graph.update_node_fields_if_match.await_args.args[3] == {"contentRev": "z"}

    async def test_a_record_that_took_the_written_revision_in_between_keeps_its_count(self) -> None:
        from app.modules.indexing.record_diagnostics import write_with_revision_rule

        graph = MagicMock()
        graph.update_node = AsyncMock()
        graph.update_node_fields_if_match = AsyncMock(side_effect=[False, True])
        graph.get_document = AsyncMock(return_value={"contentRev": "a", "pageCount": 9})
        assert await write_with_revision_rule(graph, "r1", "records", {"contentRev": "a"})
        assert [c.args[2] for c in graph.update_node_fields_if_match.await_args_list] == [{"contentRev": "a"}, {"contentRev": "a"}]

    async def test_a_record_that_keeps_moving_or_is_gone_is_reported_not_written(self) -> None:
        from app.modules.indexing.record_diagnostics import write_with_revision_rule

        graph = MagicMock()
        graph.update_node = AsyncMock()
        graph.update_node_fields_if_match = AsyncMock(return_value=False)
        graph.get_document = AsyncMock(return_value=None)
        assert not await write_with_revision_rule(graph, "r1", "records", {"contentRev": "a"})
        graph.get_document = AsyncMock(side_effect=lambda *a: {"contentRev": object()})
        assert not await write_with_revision_rule(graph, "r1", "records", {"contentRev": "a"})
        graph.update_node.assert_not_awaited()

    async def test_fields_without_a_revision_are_one_plain_write(self) -> None:
        from app.modules.indexing.record_diagnostics import write_with_revision_rule

        graph = MagicMock()
        graph.update_node = AsyncMock(return_value=True)
        graph.update_node_fields_if_match = AsyncMock()
        assert await write_with_revision_rule(graph, "r1", "records", {"reason": "x"})
        graph.update_node_fields_if_match.assert_not_awaited()
        assert graph.update_node.await_args.args[2] == {"reason": "x"}

    def test_every_write_site_of_the_revision_in_app_is_classified_and_the_writers_use_the_rule(self) -> None:
        """Structural, per write site. An AST walk finds every site in ``app`` that names the key ``contentRev``: a dict literal with it as a
        key (a ``**{...}`` spread of one included), a call that passes it as a keyword (``dict(contentRev=rev)``, a constructor, ``update``), an
        item assignment, or the bare constant (a match condition, a field list). Reads (``.get("contentRev")``) are skipped. Each function (or
        module) is classified with the NUMBER of sites it holds, so a new write added to an already classified function changes the count and
        fails here, as does a new function. A known writer must also call ``write_with_revision_rule``.

        Limits: the site is found by the literal key or keyword; a key built at run time (``{name: rev}`` with ``name`` a variable), fields taken
        from another mapping (``{**other}``) or ``setattr`` are invisible; and one site added while another is removed in the same function keeps
        its count (the review sees that diff)."""
        from pathlib import Path

        app = Path(__file__).resolve().parents[4] / "app"
        writers = {
            ("events/events.py", "EventProcessor.update_record_fields"): 1,
            ("modules/pipeline/ingress.py", "StageIngress.on_indexed"): 1,
        }
        not_writers = {
            ("connectors/sources/localKB/push/service.py", "PushResult.to_json"): (1, "the push response"),
            ("connectors/sources/localKB/push/service.py", "KbPushService._store"): (1, "storage tags"),
            ("events/events.py", "EventProcessor._write_page_count"): (1, "a match condition"),
            ("events/events.py", "EventProcessor._persist_parsing_completed"): (1, "a match condition"),
            ("events/events.py", "EventProcessor.mark_record_status"): (1, "builds fields that update_record_fields writes"),
            ("events/events.py", "EventProcessor._dispatch_event"): (1, "the attempt start fields (write_attempt_start decides the count)"),
            ("events/events.py", "EventProcessor._attach_to_finished_twin"): (1, "a match condition"),
            ("events/events.py", "EventProcessor._skip_same_bytes"): (1, "the revision the skip is for, passed to the redrive; no record write"),
            ("services/messaging/kafka/handlers/record.py", "RecordEventHandler._clear_membership_pending"): (2, "match conditions of a reason-only write"),
            ("services/messaging/kafka/handlers/record.py", "RecordEventHandler._repair_membership_after_give_up"): (1, "a match condition of a reason-only write"),
            ("services/messaging/kafka/handlers/record.py", "RecordEventHandler._undo_pending_reason"): (1, "a match condition of a reason-only write"),
            ("models/record_metadata.py", "<module>"): (1, "a field list"),
            ("modules/artifacts/retention.py", "_held_revs"): (1, "a read"),
            ("modules/indexing/duplicate_reconcile.py", "DuplicateReconciler.reconcile"): (1, "a match condition"),
            ("modules/indexing/embed_resume.py", "<module>"): (1, "a point field name"),
            ("modules/indexing/page_limit.py", "apply_keep"): (1, "a match condition"),
            ("modules/indexing/page_limit.py", "apply_refuse"): (1, "a tombstone revision, written through the record handler's revision rule with the count cleared"),
            ("modules/indexing/pdf_page_limit_audit.py", "_note"): (1, "a match condition"),
            ("modules/indexing/parse_now_marker.py", "shape_status_write"): (1, "derives the marker from the fields"),
            ("modules/indexing/record_diagnostics.py", "<module>"): (1, "a field list"),
            ("modules/indexing/record_diagnostics.py", "write_with_revision_rule"): (2, "the rule itself"),
            ("modules/pipeline/parse_now.py", "parse_now_event"): (1, "an event payload"),
            ("modules/record_metadata/projection.py", "project_properties"): (1, "a read"),
            ("schema/arango/documents.py", "<module>"): (1, "the schema"),
        }
        found: dict[tuple[str, str], int] = {}
        uses_rule: set[tuple[str, str]] = set()
        for path in sorted(app.rglob("*.py")):
            text = path.read_text(encoding="utf-8-sig")
            if "contentRev" not in text:
                continue
            relative = path.relative_to(app).as_posix()
            sites, rule_users = _revision_sites(text)
            for owner, count in sites.items():
                found[(relative, owner)] = count
            uses_rule |= {(relative, owner) for owner in rule_users}
        expected = {**writers, **{key: count for key, (count, _) in not_writers.items()}}
        changed = sorted(f"{key}: {found.get(key, 0)} sites, classified {count}" for key, count in expected.items() if found.get(key, 0) != count)
        unclassified = sorted(f"{key}: {count} sites" for key, count in found.items() if key not in expected)
        assert changed == [], f"the number of sites that name contentRev changed (a new write, or a classification gone stale): {changed}"
        assert unclassified == [], f"these name contentRev and are neither a writer using the rule nor classified as non-writers: {unclassified}"
        assert set(writers) <= uses_rule, f"these write contentRev without write_with_revision_rule: {sorted(set(writers) - uses_rule)}"

    def test_the_site_scan_sees_keyword_spread_and_assignment_forms(self) -> None:
        """Mutants of the scan itself: each form of a write of the revision is one site, and a write added to a function is one more."""
        base = "def f(rev):\n    return {'contentRev': rev}\n"
        assert _revision_sites(base)[0] == {"f": 1}
        for mutant in (
            "def f(rev):\n    return dict(contentRev=rev)\n",
            "def f(rev):\n    return dict(**{'contentRev': rev})\n",
            "def f(rev, doc):\n    doc['contentRev'] = rev\n",
            "def f(rev, g):\n    g(contentRev=rev)\n",
            "class C:\n    def f(self, rev):\n        return {**{'contentRev': rev}}\n",
        ):
            sites = _revision_sites(mutant)[0]
            assert sum(sites.values()) == 1, mutant
        assert _revision_sites(base + "    # reads\n    x = {}.get('contentRev')\n")[0] == {"f": 1}
        twice = "def f(rev):\n    a = {'contentRev': rev}\n    b = dict(contentRev=rev)\n    return a, b\n"
        assert _revision_sites(twice)[0] == {"f": 2}

    def test_a_write_added_to_a_classified_function_changes_its_count(self) -> None:
        """Mutant on the real file: a second write in ``update_record_fields`` (a keyword form) raises the count the classification holds."""
        from pathlib import Path

        source = (Path(__file__).resolve().parents[4] / "app" / "events" / "events.py").read_text(encoding="utf-8-sig")
        before = _revision_sites(source)[0]["EventProcessor.update_record_fields"]
        marker = "    async def update_record_fields("
        head, _, rest = source.partition(marker)
        signature_end = rest.index(":\n", rest.index(")"))  # the end of the signature
        body_start = rest.index("\n", signature_end + 1) + 1
        mutated = head + marker + rest[:body_start] + "        _extra = dict(contentRev=None)\n" + rest[body_start:]
        assert _revision_sites(mutated)[0]["EventProcessor.update_record_fields"] == before + 1


def _revision_sites(source: str) -> tuple[dict[str, int], set[str]]:
    """``({owner: number of write sites naming contentRev}, owners that use write_with_revision_rule)`` for one module's source."""
    import ast

    tree = ast.parse(source)
    parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}

    def owner(node: ast.AST) -> str:
        names = []
        while node in parents:
            node = parents[node]
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                names.append(node.name)
        return ".".join(reversed(names)) or "<module>"

    sites: dict[str, set[ast.AST]] = {}
    rule_users: set[str] = set()

    def add(node: ast.AST, site: ast.AST) -> None:
        sites.setdefault(owner(node), set()).add(site)

    for node in ast.walk(tree):
        if isinstance(node, ast.keyword) and node.arg == "contentRev":
            add(node, node)
        elif isinstance(node, ast.Constant) and node.value == "contentRev":
            parent = parents.get(node)
            if isinstance(parent, ast.Call) and isinstance(parent.func, ast.Attribute) and parent.func.attr == "get":
                continue  # a read
            if isinstance(parent, ast.Dict):
                add(node, parent)  # one site per dict literal, however it reaches a call (``**{...}`` too)
            elif isinstance(parent, ast.Subscript):
                add(node, parent)
            else:
                add(node, node)
        if isinstance(node, ast.Name) and node.id == "write_with_revision_rule" or (
            isinstance(node, ast.Attribute) and node.attr == "write_with_revision_rule"
        ):
            rule_users.add(owner(node))
    return {name: len(found) for name, found in sites.items()}, rule_users
