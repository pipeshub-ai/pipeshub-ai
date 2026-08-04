"""Unit tests for `ops.time_range.parse_time_range` — ISO 8601 date parsing
for time-aware retrieval search."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from app.agents.actions.knowledge_graph.ops.time_range import parse_time_range


def _epoch_ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


class TestParseTimeRange:
    def test_date_only_after_resolves_to_start_of_day(self) -> None:
        time_range, error = parse_time_range(created_after="2026-03-15")
        assert error is None
        expected = _epoch_ms(datetime(2026, 3, 15, 0, 0, 0, tzinfo=timezone.utc))
        assert time_range == {"source_created_after_ms": expected}

    def test_date_only_before_resolves_to_end_of_day(self) -> None:
        time_range, error = parse_time_range(created_before="2026-03-15")
        assert error is None
        expected = _epoch_ms(
            datetime(2026, 3, 15, 23, 59, 59, 999000, tzinfo=timezone.utc)
        )
        assert time_range == {"source_created_before_ms": expected}

    def test_full_iso_datetime_used_as_is(self) -> None:
        time_range, error = parse_time_range(created_after="2026-03-15T08:00:00-07:00")
        assert error is None
        expected = _epoch_ms(
            datetime(2026, 3, 15, 8, 0, 0, tzinfo=timezone(timedelta(hours=-7)))
        )
        assert time_range == {"source_created_after_ms": expected}

    def test_z_suffixed_datetime_treated_as_utc(self) -> None:
        time_range, error = parse_time_range(modified_after="2026-03-15T08:00:00Z")
        assert error is None
        expected = _epoch_ms(datetime(2026, 3, 15, 8, 0, 0, tzinfo=timezone.utc))
        assert time_range == {"source_updated_after_ms": expected}

    def test_naive_datetime_rejected(self) -> None:
        time_range, error = parse_time_range(created_after="2026-03-15T08:00:00")
        assert time_range is None
        assert error is not None
        parsed = json.loads(error)
        assert parsed["status"] == "error"
        assert "timezone" in parsed["message"].lower()

    def test_malformed_string_rejected(self) -> None:
        time_range, error = parse_time_range(created_after="not-a-date")
        assert time_range is None
        assert error is not None
        parsed = json.loads(error)
        assert parsed["status"] == "error"

    def test_invalid_calendar_date_rejected(self) -> None:
        time_range, error = parse_time_range(created_after="2026-02-30")
        assert time_range is None
        assert error is not None

    def test_inverted_created_range_rejected(self) -> None:
        time_range, error = parse_time_range(
            created_after="2026-12-31", created_before="2026-01-01"
        )
        assert time_range is None
        assert error is not None
        parsed = json.loads(error)
        assert "inverted" in parsed["message"].lower()

    def test_inverted_modified_range_rejected(self) -> None:
        time_range, error = parse_time_range(
            modified_after="2026-12-31", modified_before="2026-01-01"
        )
        assert time_range is None
        assert error is not None
        parsed = json.loads(error)
        assert "inverted" in parsed["message"].lower()

    def test_future_created_after_rejected(self) -> None:
        future = (datetime.now(timezone.utc) + timedelta(days=365)).strftime("%Y-%m-%d")
        time_range, error = parse_time_range(created_after=future)
        assert time_range is None
        assert error is not None
        parsed = json.loads(error)
        assert "future" in parsed["message"].lower()

    def test_future_created_before_allowed(self) -> None:
        future = (datetime.now(timezone.utc) + timedelta(days=365)).strftime("%Y-%m-%d")
        time_range, error = parse_time_range(created_before=future)
        assert error is None
        assert time_range is not None
        assert "source_created_before_ms" in time_range

    def test_future_modified_after_rejected(self) -> None:
        future = (datetime.now(timezone.utc) + timedelta(days=365)).strftime("%Y-%m-%d")
        time_range, error = parse_time_range(modified_after=future)
        assert time_range is None
        assert error is not None
        parsed = json.loads(error)
        assert "future" in parsed["message"].lower()

    def test_clock_skew_grace_window_allows_near_future(self) -> None:
        near_future = datetime.now(timezone.utc) + timedelta(minutes=2)
        iso = near_future.isoformat().replace("+00:00", "Z")
        time_range, error = parse_time_range(created_after=iso)
        assert error is None
        assert time_range is not None

    def test_open_ended_after_only(self) -> None:
        time_range, error = parse_time_range(created_after="2026-01-01")
        assert error is None
        assert list(time_range.keys()) == ["source_created_after_ms"]

    def test_open_ended_before_only(self) -> None:
        time_range, error = parse_time_range(modified_before="2026-06-30")
        assert error is None
        assert list(time_range.keys()) == ["source_updated_before_ms"]

    def test_combined_created_and_modified(self) -> None:
        time_range, error = parse_time_range(
            created_after="2026-01-01",
            created_before="2026-03-31",
            modified_after="2026-02-01",
            modified_before="2026-02-28",
        )
        assert error is None
        assert set(time_range.keys()) == {
            "source_created_after_ms",
            "source_created_before_ms",
            "source_updated_after_ms",
            "source_updated_before_ms",
        }

    def test_all_none_returns_none(self) -> None:
        time_range, error = parse_time_range()
        assert time_range is None
        assert error is None

    def test_empty_strings_treated_as_none(self) -> None:
        time_range, error = parse_time_range(
            created_after="", created_before="", modified_after="", modified_before=""
        )
        assert time_range is None
        assert error is None

    def test_whitespace_only_treated_as_none(self) -> None:
        time_range, error = parse_time_range(created_after="   ")
        assert time_range is None
        assert error is None

    def test_valid_range_boundary_equal_dates_allowed(self) -> None:
        """after == before is a valid (single-day) window, not inverted."""
        time_range, error = parse_time_range(
            created_after="2026-01-01", created_before="2026-01-01"
        )
        assert error is None
        assert time_range is not None
        assert (
            time_range["source_created_after_ms"]
            <= time_range["source_created_before_ms"]
        )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("created_after", "2026-13-01"),
        ("created_before", "2026-01-32"),
        ("modified_after", "26-01-01"),
        ("modified_before", "2026/01/01"),
    ],
)
def test_various_malformed_inputs_rejected(field: str, value: str) -> None:
    time_range, error = parse_time_range(**{field: value})
    assert time_range is None
    assert error is not None
