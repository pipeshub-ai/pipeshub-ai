"""Unit tests for app.utils.time_conversion."""

import time
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from app.utils.time_conversion import (
    _strip_or_none,
    _utc_offset_hh_mm,
    build_llm_time_context,
    datetime_to_epoch_ms,
    epoch_ms_to_iso,
    format_user_timezone_prompt_line,
    get_epoch_timestamp_in_ms,
    parse_timestamp,
    prepare_iso_timestamps,
    string_to_datetime,
)


class TestGetEpochTimestampInMs:
    """Tests for get_epoch_timestamp_in_ms()."""

    def test_returns_int(self) -> None:
        result = get_epoch_timestamp_in_ms()
        assert isinstance(result, int)

    def test_reasonable_range(self) -> None:
        """Timestamp should be close to current time in milliseconds."""
        before = int(time.time() * 1000)
        result = get_epoch_timestamp_in_ms()
        after = int(time.time() * 1000)
        assert before <= result <= after

    def test_is_milliseconds_not_seconds(self) -> None:
        """Value should be 13 digits (milliseconds), not 10 digits (seconds)."""
        result = get_epoch_timestamp_in_ms()
        assert len(str(result)) >= 13

    def test_deterministic_with_mocked_time(self) -> None:
        fixed_dt = datetime(2024, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
        with patch("app.utils.time_conversion.datetime") as mock_dt:
            mock_dt.now.return_value = fixed_dt
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            result = get_epoch_timestamp_in_ms()
        expected = int(fixed_dt.timestamp() * 1000)
        assert result == expected


class TestParseTimestamp:
    """Tests for parse_timestamp()."""

    def test_utc_z_suffix(self) -> None:
        result = parse_timestamp("2024-01-01T00:00:00Z")
        # 2024-01-01T00:00:00 UTC in seconds = 1704067200, in ms = 1704067200000
        assert result == 1704067200000

    def test_lowercase_z_suffix(self) -> None:
        result = parse_timestamp("2024-01-01T00:00:00z")
        assert result == 1704067200000

    def test_with_timezone_offset(self) -> None:
        result = parse_timestamp("2024-01-01T00:00:00+00:00")
        assert result == 1704067200000

    def test_returns_milliseconds(self) -> None:
        result = parse_timestamp("2024-06-15T12:30:00Z")
        assert isinstance(result, int)
        assert len(str(result)) >= 13

    def test_already_millisecond_timestamp_not_doubled(self) -> None:
        """If the parsed timestamp already has 13+ digits, it should not be multiplied by 1000."""
        # A far-future date whose epoch seconds would be 13+ digits would be extreme,
        # but the code checks len(str(timestamp)) >= 13.
        # In practice, Unix timestamps won't reach 13 digits until ~year 33658.
        # So normal dates always get multiplied. Just verify a normal date works.
        result = parse_timestamp("2025-03-22T10:00:00Z")
        assert isinstance(result, int)
        assert result > 0

    def test_specific_known_date(self) -> None:
        # 2024-07-04T18:30:00Z
        result = parse_timestamp("2024-07-04T18:30:00Z")
        expected_dt = datetime(2024, 7, 4, 18, 30, 0, tzinfo=timezone.utc)
        expected_ms = int(expected_dt.timestamp()) * 1000
        assert result == expected_ms

    def test_timestamp_already_in_milliseconds_not_doubled(self) -> None:
        """When parsed timestamp is already 13+ digits, it should be returned as-is."""
        huge_ts = 10000000000000  # 13-digit number (already in millisecond range)
        mock_dt = MagicMock()
        mock_dt.timestamp.return_value = float(huge_ts)

        with patch("app.utils.time_conversion.datetime") as mock_datetime:
            mock_datetime.fromisoformat.return_value = mock_dt
            # We still need the real endswith check to work on the string
            result = parse_timestamp("2024-01-01T00:00:00+00:00")
        # Should return the timestamp as-is since len(str(10000000000000)) == 13
        assert result == huge_ts

    def test_no_z_suffix_passes_through(self) -> None:
        """Timestamp string without Z suffix should parse directly."""
        result = parse_timestamp("2024-06-15T12:30:00+05:30")
        assert isinstance(result, int)
        assert result > 0


class TestPrepareIsoTimestamps:
    """Tests for prepare_iso_timestamps()."""

    def test_returns_iso_format_strings(self) -> None:
        start, end = prepare_iso_timestamps("2024-01-01T00:00:00Z", "2024-12-31T23:59:59Z")
        # Should be ISO 8601 format
        assert "2024-01-01" in start
        assert "2024-12-31" in end

    def test_contains_t_separator(self) -> None:
        start, end = prepare_iso_timestamps("2024-06-01T10:00:00Z", "2024-06-01T20:00:00Z")
        assert "T" in start
        assert "T" in end

    def test_contains_timezone_info(self) -> None:
        start, end = prepare_iso_timestamps("2024-01-01T00:00:00Z", "2024-01-02T00:00:00Z")
        # datetime.isoformat() with timezone includes +00:00
        assert "+00:00" in start
        assert "+00:00" in end

    def test_round_trip_preserves_time(self) -> None:
        """Converting to ISO and back should preserve the original time."""
        start, end = prepare_iso_timestamps("2024-06-15T12:30:00Z", "2024-06-15T18:45:00Z")
        start_dt = datetime.fromisoformat(start)
        end_dt = datetime.fromisoformat(end)
        assert start_dt.hour == 12
        assert start_dt.minute == 30
        assert end_dt.hour == 18
        assert end_dt.minute == 45

    def test_start_before_end(self) -> None:
        start, end = prepare_iso_timestamps("2024-01-01T00:00:00Z", "2024-12-31T23:59:59Z")
        start_dt = datetime.fromisoformat(start)
        end_dt = datetime.fromisoformat(end)
        assert start_dt < end_dt

class TestStringToDatetime:
    """Tests String to datetime."""
    def test_string_to_datetime(self) -> None:
        assert string_to_datetime("1970-01-01T00:00:00Z") == datetime(
            1970, 1, 1, 0, 0, 0, tzinfo=timezone.utc
        )


class TestStripOrNone:
    """Tests for _strip_or_none()."""

    def test_none_input(self) -> None:
        assert _strip_or_none(None) is None

    def test_whitespace_only(self) -> None:
        assert _strip_or_none("   ") is None

    def test_empty_string(self) -> None:
        assert _strip_or_none("") is None

    def test_trims_value(self) -> None:
        assert _strip_or_none("  hello  ") == "hello"

    def test_passthrough(self) -> None:
        assert _strip_or_none("abc") == "abc"


class TestUtcOffsetHhMm:
    """Tests for _utc_offset_hh_mm()."""

    def test_naive_datetime_returns_zero_offset(self) -> None:
        # datetime without tzinfo → utcoffset() is None → "+00:00" branch
        dt = datetime(2024, 1, 1, 12, 0, 0)
        assert _utc_offset_hh_mm(dt) == "+00:00"

    def test_utc_aware(self) -> None:
        dt = datetime(2024, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
        assert _utc_offset_hh_mm(dt) == "+00:00"

    def test_positive_offset(self) -> None:
        tz = timezone(timedelta(hours=5, minutes=30))
        dt = datetime(2024, 1, 1, 12, 0, 0, tzinfo=tz)
        assert _utc_offset_hh_mm(dt) == "+05:30"

    def test_negative_offset(self) -> None:
        tz = timezone(timedelta(hours=-8))
        dt = datetime(2024, 1, 1, 12, 0, 0, tzinfo=tz)
        assert _utc_offset_hh_mm(dt) == "-08:00"


class TestFormatUserTimezonePromptLine:
    """Tests for format_user_timezone_prompt_line()."""

    def test_empty_name_returns_empty_string(self) -> None:
        assert format_user_timezone_prompt_line("") == ""
        assert format_user_timezone_prompt_line(None) == ""
        assert format_user_timezone_prompt_line("   ") == ""

    def test_invalid_iana_returns_raw_label(self) -> None:
        result = format_user_timezone_prompt_line("Not/A_Real_Zone")
        assert result == "**Time zone**: Not/A_Real_Zone"

    def test_valid_iana_without_moment(self) -> None:
        result = format_user_timezone_prompt_line("UTC")
        assert "**Time zone**: UTC" in result
        assert "UTC+00:00" in result

    def test_valid_iana_with_naive_moment(self) -> None:
        moment = datetime(2024, 6, 15, 12, 0, 0)
        result = format_user_timezone_prompt_line("America/New_York", moment=moment)
        assert "America/New_York" in result
        # Offset should be present in the formatted output
        assert "UTC-" in result or "UTC+" in result

    def test_valid_iana_with_aware_moment(self) -> None:
        moment = datetime(2024, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
        result = format_user_timezone_prompt_line("Asia/Kolkata", moment=moment)
        assert "Asia/Kolkata" in result
        assert "UTC+05:30" in result


class TestBuildLlmTimeContext:
    """Tests for build_llm_time_context()."""

    def test_both_empty_returns_empty_string(self) -> None:
        assert build_llm_time_context() == ""
        assert build_llm_time_context(current_time=None, time_zone=None) == ""
        assert build_llm_time_context(current_time="  ", time_zone="  ") == ""

    def test_only_current_time(self) -> None:
        result = build_llm_time_context(current_time="2024-06-15T12:00:00Z")
        assert "## Time context" in result
        assert "**Current time**: 2024-06-15T12:00:00Z" in result
        # No `**Time zone**:` line (the subline mentions the phrase but without a colon)
        assert "**Time zone**:" not in result

    def test_only_time_zone_adds_utc_fallback(self) -> None:
        result = build_llm_time_context(time_zone="UTC")
        assert "## Time context" in result
        # When current_time is not provided, the default reference ends with (UTC)
        assert "**Current time**:" in result
        assert "(UTC)" in result
        assert "**Time zone**: UTC" in result

    def test_both_set(self) -> None:
        result = build_llm_time_context(
            current_time="2024-06-15T12:00:00Z",
            time_zone="Asia/Kolkata",
        )
        assert "**Current time**: 2024-06-15T12:00:00Z" in result
        assert "Asia/Kolkata" in result
        assert "(UTC)" not in result  # current_time was explicit

    def test_invalid_timezone_still_emits_line(self) -> None:
        result = build_llm_time_context(
            current_time="2024-06-15T12:00:00Z",
            time_zone="Bad/Zone",
        )
        assert "**Time zone**: Bad/Zone" in result


class TestEpochMsToIso:
    """Tests for epoch_ms_to_iso()."""

    def test_epoch_zero(self) -> None:
        assert epoch_ms_to_iso(0).startswith("1970-01-01T00:00:00")

    def test_known_ms(self) -> None:
        # 1704067200000 ms = 2024-01-01T00:00:00 UTC
        result = epoch_ms_to_iso(1704067200000)
        assert result.startswith("2024-01-01T00:00:00")
        assert "+00:00" in result


class TestDatetimeToEpochMs:
    """Tests for datetime_to_epoch_ms()."""

    def test_none_returns_none(self) -> None:
        assert datetime_to_epoch_ms(None) is None

    def test_empty_string_returns_none(self) -> None:
        assert datetime_to_epoch_ms("") is None

    def test_aware_datetime(self) -> None:
        dt = datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        assert datetime_to_epoch_ms(dt) == 1704067200000

    def test_naive_datetime_treated_as_utc(self) -> None:
        dt = datetime(2024, 1, 1, 0, 0, 0)
        # Naive datetime path (line 163: dt.tzinfo is None → replace with UTC)
        assert datetime_to_epoch_ms(dt) == 1704067200000

    def test_iso_string_fallback(self) -> None:
        # No strptime_format → falls to parse_timestamp
        result = datetime_to_epoch_ms("2024-01-01T00:00:00Z")
        assert result == 1704067200000

    def test_strptime_format_happy_path(self) -> None:
        # Typical ServiceNow-style format
        result = datetime_to_epoch_ms(
            "2024-01-01 00:00:00",
            strptime_format="%Y-%m-%d %H:%M:%S",
        )
        assert result == 1704067200000

    def test_strptime_format_failure_falls_back_to_parse(self) -> None:
        # Provide a strptime_format that won't match but the string is a valid ISO
        # timestamp, so the fallback parse_timestamp path should return an int.
        result = datetime_to_epoch_ms(
            "2024-01-01T00:00:00Z",
            strptime_format="%Y-%m-%d %H:%M:%S",
        )
        assert result == 1704067200000

    def test_invalid_string_returns_none(self) -> None:
        # Neither strptime nor fromisoformat can parse this → exception → None
        assert datetime_to_epoch_ms("not-a-date") is None

    def test_strptime_aware_datetime_preserved(self) -> None:
        # strptime parses as naive; code replaces tzinfo with UTC
        result = datetime_to_epoch_ms(
            "2024-06-15 00:00:00",
            strptime_format="%Y-%m-%d %H:%M:%S",
        )
        expected = int(
            datetime(2024, 6, 15, 0, 0, 0, tzinfo=timezone.utc).timestamp() * 1000
        )
        assert result == expected

