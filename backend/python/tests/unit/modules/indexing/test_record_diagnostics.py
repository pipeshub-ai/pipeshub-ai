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
import timeit
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
    provider.update_node_fields_if_match = AsyncMock(return_value=True)
    provider.update_node_if_unchanged = AsyncMock(return_value=True)
    for name, value in graph.items():
        setattr(provider, name, value)
    processor.graph_provider = provider
    return processor


class TestPageCountWrites:
    async def test_the_in_process_count_is_one_conditional_write_on_the_revision_and_the_attempt(self) -> None:
        processor = _processor()

        await processor._write_page_count("r1", "rev-a", 12, ("rev-0", None))

        processor.graph_provider.update_node_fields_if_match.assert_awaited_once()
        args = processor.graph_provider.update_node_fields_if_match.await_args.args
        assert args[2] == {"pageCount": 12}
        assert args[3] == {"contentRev": "rev-a", "indexingStatus": IP}

    async def test_a_count_the_record_already_holds_for_this_revision_is_not_written_again(self) -> None:
        processor = _processor()

        await processor._write_page_count("r1", "rev-a", 12, ("rev-a", 12))

        processor.graph_provider.update_node_fields_if_match.assert_not_awaited()

    async def test_the_same_count_for_other_content_is_written_again(self) -> None:
        processor = _processor()

        await processor._write_page_count("r1", "rev-b", 12, ("rev-a", 12))

        processor.graph_provider.update_node_fields_if_match.assert_awaited_once()

    @pytest.mark.parametrize("pages", [None, 0, "3", True, 1_000_001])
    async def test_no_usable_count_writes_nothing(self, pages: Any) -> None:  # noqa: ANN401
        processor = _processor()

        await processor._write_page_count("r1", "rev-a", pages, None)

        processor.graph_provider.update_node_fields_if_match.assert_not_awaited()

    async def test_a_store_only_run_is_matched_on_the_revision_alone(self) -> None:
        processor = _processor()
        with _run(store_only=True):
            await processor._write_page_count("r1", "rev-a", 4, None)

        assert processor.graph_provider.update_node_fields_if_match.await_args.args[3] == {"contentRev": "rev-a"}

    async def test_a_failed_count_write_never_fails_the_record(self) -> None:
        processor = _processor(update_node_fields_if_match=AsyncMock(side_effect=RuntimeError("down")))

        await processor._write_page_count("r1", "rev-a", 4, None)  # no raise

    async def test_a_late_write_of_one_revision_matches_nothing_on_another(self) -> None:
        """The write names its revision, so it cannot land on content that replaced it: the store answers False."""
        processor = _processor(update_node_fields_if_match=AsyncMock(return_value=False))

        await processor._write_page_count("r1", "rev-a", 4, None)

        assert processor.graph_provider.update_node_fields_if_match.await_args.args[3]["contentRev"] == "rev-a"

    async def test_the_service_count_rides_the_status_write_when_the_record_is_unchanged_since_it_was_read(self) -> None:
        processor = _processor(update_node_if_unchanged=AsyncMock(return_value=True))
        record = {"_key": "r1", "contentRev": "rev-a", "_rev": "_r1"}

        await processor._persist_parsing_completed(record, 30)

        call = processor.graph_provider.update_node_if_unchanged.await_args
        assert call.args[2] == {"parsingStatus": "COMPLETED", "pageCount": 30}
        assert call.args[3] is record
        assert call.args[4] == {"contentRev": "rev-a", "indexingStatus": IP}
        processor.graph_provider.update_node.assert_not_awaited()

    async def test_when_the_record_changed_the_plain_status_write_runs_without_a_count(self) -> None:
        processor = _processor(update_node_if_unchanged=AsyncMock(return_value=False))
        record = {"_key": "r1", "contentRev": "rev-a"}

        await processor._persist_parsing_completed(record, 30)

        processor.graph_provider.update_node.assert_awaited_once()
        assert processor.graph_provider.update_node.await_args.args[2] == {"parsingStatus": "COMPLETED"}

    async def test_a_parse_without_a_count_is_the_unchanged_status_write(self) -> None:
        processor = _processor()

        await processor._persist_parsing_completed({"_key": "r1", "contentRev": "rev-a"}, None)

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

def test_fragment_and_reference_build_cost() -> None:
    """The cached fragments cost under 1 us a use; the Python reference is reported (about 1 us)."""
    runs = 100_000
    best = {
        "aql": min(timeit.repeat(lambda: aql_parts("doc", WriteKind.ATTEMPT_START, rev="@rev"), number=runs, repeat=5)) / runs * 1e6,
        "cypher": min(timeit.repeat(lambda: cypher_parts("n", WriteKind.ATTEMPT_START, rev="$rev"), number=runs, repeat=5)) / runs * 1e6,
        "sql": min(timeit.repeat(lambda: sql_patch("doc", WriteKind.ATTEMPT_START, rev="$5::text"), number=runs, repeat=5)) / runs * 1e6,
    }
    stored = {"indexingStatus": "QUEUED", "firstQueuedAt": 5, "indexingAttempts": 2, "contentRev": "a"}
    best["reference"] = min(timeit.repeat(lambda: reference(stored, WriteKind.ATTEMPT_START, 100, content_rev="a"), number=runs, repeat=5)) / runs * 1e6
    print(f"fragment build us: {best}")
    assert best["aql"] <= 1.0 and best["cypher"] <= 1.0 and best["sql"] <= 1.0
    assert best["reference"] <= 5.0


