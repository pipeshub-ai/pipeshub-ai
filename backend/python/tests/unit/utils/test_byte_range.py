"""The strict ``Range`` rules of the record stream route."""

from __future__ import annotations

import pytest

from app.utils.byte_range import MAX_RANGE_SPAN, ByteRange, resolve_range

TOTAL = 100 * MAX_RANGE_SPAN


@pytest.mark.parametrize(
    ("header", "total", "expected"),
    [
        ("bytes=0-0", 10, ByteRange(0, 0)),
        ("bytes=2-5", 10, ByteRange(2, 5)),
        ("bytes=5-", 10, ByteRange(5, 9)),
        ("bytes=5-999", 10, ByteRange(5, 9)),
        (f"bytes=0-{MAX_RANGE_SPAN - 1}", TOTAL, ByteRange(0, MAX_RANGE_SPAN - 1)),
        ("  bytes=1-2  ", 10, ByteRange(1, 2)),
    ],
)
def test_one_strict_range_is_served(header: str, total: int, expected: ByteRange) -> None:
    assert resolve_range(header, total) == expected


@pytest.mark.parametrize("header", [None, "", "bytes=-5", "bytes=1-2,4-5", "bytes=a-b", "bytes=5-2", "bytes=1-2-3", "chars=0-1", "bytes=0-5;x", "bytes=+1-2", "bytes=1_0-2"])
def test_anything_else_is_ignored_and_the_whole_body_follows(header: str | None) -> None:
    assert resolve_range(header, 1000) is None


def test_a_span_over_8_mib_is_ignored_not_refused() -> None:
    assert resolve_range(f"bytes=0-{MAX_RANGE_SPAN}", TOTAL) is None
    assert resolve_range("bytes=0-", TOTAL) is None
    assert resolve_range("bytes=0-", MAX_RANGE_SPAN) == ByteRange(0, MAX_RANGE_SPAN - 1)


@pytest.mark.parametrize("header", ["bytes=10-", "bytes=10-20", "bytes=11-12", "bytes=999999999999-"])
def test_a_start_at_or_past_the_end_is_unsatisfiable(header: str) -> None:
    assert resolve_range(header, 10) == "unsatisfiable"


def test_an_empty_object_has_no_satisfiable_range() -> None:
    assert resolve_range("bytes=0-", 0) == "unsatisfiable"


def test_a_huge_number_is_not_parsed() -> None:
    assert resolve_range("bytes=" + "9" * 40 + "-", 10) is None
