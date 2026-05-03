"""Unit tests for app.utils.datetime_utils."""

import re
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

from app.utils.datetime_utils import (
    get_current_date,
    get_current_datetime,
    get_current_datetime_with_timezone,
    get_current_time,
)


class TestGetCurrentDate:
    """Tests for get_current_date()."""

    def test_format_month_day_year(self):
        result = get_current_date()
        # Should match "Month DD, YYYY" e.g. "March 22, 2026"
        pattern = r"^[A-Z][a-z]+ \d{2}, \d{4}$"
        assert re.match(pattern, result), f"'{result}' does not match 'Month DD, YYYY'"

    def test_deterministic_with_mock(self):
        fixed = datetime(2024, 7, 4, 10, 30, 0)
        with patch("app.utils.datetime_utils.datetime") as mock_dt:
            mock_dt.now.return_value = fixed
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            result = get_current_date()
        assert result == "July 04, 2024"

    def test_contains_current_year(self):
        result = get_current_date()
        current_year = str(datetime.now().year)
        assert current_year in result


class TestGetCurrentTime:
    """Tests for get_current_time()."""

    def test_format_hh_mm_ss(self):
        result = get_current_time()
        pattern = r"^\d{2}:\d{2}:\d{2}$"
        assert re.match(pattern, result), f"'{result}' does not match 'HH:MM:SS'"

    def test_deterministic_with_mock(self):
        fixed = datetime(2024, 1, 1, 14, 30, 45)
        with patch("app.utils.datetime_utils.datetime") as mock_dt:
            mock_dt.now.return_value = fixed
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            result = get_current_time()
        assert result == "14:30:45"

    def test_hours_in_24h_format(self):
        result = get_current_time()
        hours = int(result.split(":")[0])
        assert 0 <= hours <= 23

    def test_minutes_valid(self):
        result = get_current_time()
        minutes = int(result.split(":")[1])
        assert 0 <= minutes <= 59

    def test_seconds_valid(self):
        result = get_current_time()
        seconds = int(result.split(":")[2])
        assert 0 <= seconds <= 59


class TestGetCurrentDatetime:
    """Tests for get_current_datetime()."""

    def test_contains_date_and_time(self):
        result = get_current_datetime()
        # Should contain month name and time with colons
        assert re.search(r"[A-Z][a-z]+", result), "Should contain month name"
        assert re.search(r"\d{2}:\d{2}:\d{2}", result), "Should contain HH:MM:SS"

    def test_format_month_dd_yyyy_hh_mm_ss(self):
        result = get_current_datetime()
        pattern = r"^[A-Z][a-z]+ \d{2}, \d{4} \d{2}:\d{2}:\d{2}$"
        assert re.match(pattern, result), f"'{result}' does not match expected format"

    def test_deterministic_with_mock(self):
        fixed = datetime(2025, 12, 25, 8, 15, 30)
        with patch("app.utils.datetime_utils.datetime") as mock_dt:
            mock_dt.now.return_value = fixed
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            result = get_current_datetime()
        assert result == "December 25, 2025 08:15:30"


class TestGetCurrentDatetimeWithTimezone:
    """Tests for get_current_datetime_with_timezone()."""

    def test_format_matches(self):
        result = get_current_datetime_with_timezone()
        # Uses strftime("%B %d, %Y %H:%M:%S") with UTC time
        pattern = r"^[A-Z][a-z]+ \d{2}, \d{4} \d{2}:\d{2}:\d{2}$"
        assert re.match(pattern, result), f"'{result}' does not match expected format"

    def test_uses_utc_time(self):
        """The function uses timezone.utc, so the time should match UTC."""
        fixed_utc = datetime(2024, 3, 15, 20, 0, 0, tzinfo=timezone.utc)
        with patch("app.utils.datetime_utils.datetime") as mock_dt:
            mock_dt.now.return_value = fixed_utc
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            result = get_current_datetime_with_timezone()
        assert result == "March 15, 2024 20:00:00"

    def test_returns_string(self):
        result = get_current_datetime_with_timezone()
        assert isinstance(result, str)

    def test_contains_year(self):
        result = get_current_datetime_with_timezone()
        # Year should be present (4 digits)
        assert re.search(r"\d{4}", result)
