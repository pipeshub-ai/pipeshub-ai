"""The resilience tests' recovery wait: when it may stop, and when it must keep going.

A wait that returns too early reads an outage's leftovers as its outcome: a
re-indexed record still showing its old FAILED, a record with no status yet,
or a status read that failed while the stack was still coming back.
"""

from __future__ import annotations

import asyncio
import itertools
from collections.abc import Iterable, Iterator

import pytest

from helper.indexing_progress import UNFINISHED, statuses, wait_until_finished

pytestmark = pytest.mark.unit


class _Reads:
    """A status reader that returns each scripted answer in turn (an exception is raised)."""

    def __init__(self, answers: Iterable[dict[str, str] | Exception]) -> None:
        self._answers: Iterator[dict[str, str] | Exception] = iter(answers)
        self.calls = 0

    def __call__(self, _kb_client: object, _record_ids: list[str]) -> dict[str, str]:
        self.calls += 1
        answer = next(self._answers)
        if isinstance(answer, Exception):
            raise answer
        return answer


def _wait(reads: _Reads, record_ids: list[str], **kwargs: object) -> dict[str, str]:
    return asyncio.run(wait_until_finished(object(), record_ids, poll=0, read=reads, **kwargs))  # type: ignore[arg-type]


def test_a_reindexed_record_still_showing_its_old_failure_is_not_finished() -> None:
    reads = _Reads([{"r1": "FAILED"}, {"r1": "FAILED"}, {"r1": "QUEUED"}, {"r1": "COMPLETED"}])
    assert _wait(reads, ["r1"], reindexed=["r1"]) == {"r1": "COMPLETED"}
    assert reads.calls == 4


def test_a_reindexed_record_that_fails_again_after_retrying_is_finished() -> None:
    reads = _Reads([{"r1": "FAILED"}, {"r1": "IN_PROGRESS"}, {"r1": "FAILED"}])
    assert _wait(reads, ["r1"], reindexed=["r1"]) == {"r1": "FAILED"}
    assert reads.calls == 3


def test_a_record_that_was_not_reindexed_may_finish_as_failed() -> None:
    reads = _Reads([{"r1": "FAILED", "r2": "COMPLETED"}])
    assert _wait(reads, ["r1", "r2"]) == {"r1": "FAILED", "r2": "COMPLETED"}


def test_a_reindex_that_never_leaves_failed_returns_at_the_deadline() -> None:
    reads = _Reads(itertools.repeat({"r1": "FAILED"}))
    assert _wait(reads, ["r1"], reindexed=["r1"], timeout=0.05) == {"r1": "FAILED"}
    assert reads.calls > 1


def test_a_record_with_no_status_yet_keeps_the_wait_going() -> None:
    assert "UNKNOWN" in UNFINISHED
    reads = _Reads([{"r1": "UNKNOWN"}, {"r1": "COMPLETED"}])
    assert _wait(reads, ["r1"]) == {"r1": "COMPLETED"}


def test_a_failed_status_read_is_retried() -> None:
    reads = _Reads([ConnectionError("gateway restarting"), {"r1": "COMPLETED"}])
    assert _wait(reads, ["r1"]) == {"r1": "COMPLETED"}


def test_status_reads_that_keep_failing_raise_the_last_error_at_the_deadline() -> None:
    reads = _Reads(itertools.repeat(ConnectionError("still down")))
    with pytest.raises(ConnectionError, match="still down"):
        _wait(reads, ["r1"], timeout=0.05)


def test_statuses_reads_a_missing_or_null_status_as_unknown() -> None:
    class _Client:
        def get_record(self, record_id: str) -> dict[str, object]:
            return {"record": {"indexingStatus": None}} if record_id == "null" else {"record": {}}

    assert statuses(_Client(), ["null", "missing"]) == {"null": "UNKNOWN", "missing": "UNKNOWN"}  # type: ignore[arg-type]
