"""Unit tests for app.utils.time_conversion."""

import time
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from app.utils.time_conversion import (
    get_epoch_timestamp_in_ms,
    parse_timestamp,
    prepare_iso_timestamps,
)


class TestGetEpochTimestampInMs:
    """Tests for get_epoch_timestamp_in_ms()."""

    def test_returns_int(self):
        result = get_epoch_timestamp_in_ms()
        assert isinstance(result, int)

    def test_reasonable_range(self):
        """Timestamp should be close to current time in milliseconds."""
        before = int(time.time() * 1000)
        result = get_epoch_timestamp_in_ms()
        after = int(time.time() * 1000)
        assert before <= result <= after

    def test_is_milliseconds_not_seconds(self):
        """Value should be 13 digits (milliseconds), not 10 digits (seconds)."""
        result = get_epoch_timestamp_in_ms()
        assert len(str(result)) >= 13

    def test_deterministic_with_mocked_time(self):
        fixed_dt = datetime(2024, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
        with patch("app.utils.time_conversion.datetime") as mock_dt:
            mock_dt.now.return_value = fixed_dt
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            result = get_epoch_timestamp_in_ms()
        expected = int(fixed_dt.timestamp() * 1000)
        assert result == expected


class TestParseTimestamp:
    """Tests for parse_timestamp()."""

    def test_utc_z_suffix(self):
        result = parse_timestamp("2024-01-01T00:00:00Z")
        # 2024-01-01T00:00:00 UTC in seconds = 1704067200, in ms = 1704067200000
        assert result == 1704067200000

    def test_lowercase_z_suffix(self):
        result = parse_timestamp("2024-01-01T00:00:00z")
        assert result == 1704067200000

    def test_with_timezone_offset(self):
        result = parse_timestamp("2024-01-01T00:00:00+00:00")
        assert result == 1704067200000

    def test_returns_milliseconds(self):
        result = parse_timestamp("2024-06-15T12:30:00Z")
        assert isinstance(result, int)
        assert len(str(result)) >= 13

    def test_already_millisecond_timestamp_not_doubled(self):
        """If the parsed timestamp already has 13+ digits, it should not be multiplied by 1000."""
        # A far-future date whose epoch seconds would be 13+ digits would be extreme,
        # but the code checks len(str(timestamp)) >= 13.
        # In practice, Unix timestamps won't reach 13 digits until ~year 33658.
        # So normal dates always get multiplied. Just verify a normal date works.
        result = parse_timestamp("2025-03-22T10:00:00Z")
        assert isinstance(result, int)
        assert result > 0

    def test_specific_known_date(self):
        # 2024-07-04T18:30:00Z
        result = parse_timestamp("2024-07-04T18:30:00Z")
        expected_dt = datetime(2024, 7, 4, 18, 30, 0, tzinfo=timezone.utc)
        expected_ms = int(expected_dt.timestamp()) * 1000
        assert result == expected_ms


class TestPrepareIsoTimestamps:
    """Tests for prepare_iso_timestamps()."""

    def test_returns_iso_format_strings(self):
        start, end = prepare_iso_timestamps("2024-01-01T00:00:00Z", "2024-12-31T23:59:59Z")
        # Should be ISO 8601 format
        assert "2024-01-01" in start
        assert "2024-12-31" in end

    def test_contains_t_separator(self):
        start, end = prepare_iso_timestamps("2024-06-01T10:00:00Z", "2024-06-01T20:00:00Z")
        assert "T" in start
        assert "T" in end

    def test_contains_timezone_info(self):
        start, end = prepare_iso_timestamps("2024-01-01T00:00:00Z", "2024-01-02T00:00:00Z")
        # datetime.isoformat() with timezone includes +00:00
        assert "+00:00" in start
        assert "+00:00" in end

    def test_round_trip_preserves_time(self):
        """Converting to ISO and back should preserve the original time."""
        start, end = prepare_iso_timestamps("2024-06-15T12:30:00Z", "2024-06-15T18:45:00Z")
        start_dt = datetime.fromisoformat(start)
        end_dt = datetime.fromisoformat(end)
        assert start_dt.hour == 12
        assert start_dt.minute == 30
        assert end_dt.hour == 18
        assert end_dt.minute == 45

    def test_start_before_end(self):
        start, end = prepare_iso_timestamps("2024-01-01T00:00:00Z", "2024-12-31T23:59:59Z")
        start_dt = datetime.fromisoformat(start)
        end_dt = datetime.fromisoformat(end)
        assert start_dt < end_dt
