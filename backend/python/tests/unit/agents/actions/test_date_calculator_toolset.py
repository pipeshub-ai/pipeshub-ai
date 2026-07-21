"""
Unit tests for app.agents.actions.calculator.date_calculator

Tests the DateCalculator tool for computing exclusion dates,
weekend enumeration, and holiday text parsing.
"""

import json

import pytest

from app.agents.actions.calculator.date_calculator import (
    DateCalculator,
    GetExclusionDatesInput,
    ListWeekendDatesInput,
    ParseHolidayDatesInput,
)


# ============================================================================
# GetExclusionDatesInput
# ============================================================================

class TestGetExclusionDatesInput:
    def test_defaults(self):
        inp = GetExclusionDatesInput(start_date="2026-01-01", end_date="2026-01-31")
        assert inp.include_saturdays is True
        assert inp.include_sundays is True
        assert inp.holiday_dates is None

    def test_custom_values(self):
        inp = GetExclusionDatesInput(
            start_date="2026-01-01",
            end_date="2026-01-31",
            holiday_dates=["2026-01-01", "2026-01-26"],
            include_saturdays=False,
            include_sundays=True,
        )
        assert inp.include_saturdays is False
        assert len(inp.holiday_dates) == 2


# ============================================================================
# ListWeekendDatesInput
# ============================================================================

class TestListWeekendDatesInput:
    def test_required_fields(self):
        inp = ListWeekendDatesInput(start_date="2026-01-01", end_date="2026-01-31")
        assert inp.start_date == "2026-01-01"
        assert inp.end_date == "2026-01-31"


# ============================================================================
# ParseHolidayDatesInput
# ============================================================================

class TestParseHolidayDatesInput:
    def test_defaults(self):
        inp = ParseHolidayDatesInput(text="Some text")
        assert inp.text == "Some text"
        assert inp.year is None

    def test_with_year_filter(self):
        inp = ParseHolidayDatesInput(text="Dates here", year=2026)
        assert inp.year == 2026


# ============================================================================
# DateCalculator.get_exclusion_dates
# ============================================================================

class TestGetExclusionDates:
    def test_weekends_and_weekday_holidays_deduplicated(self):
        """Weekends and weekday holidays are combined and deduplicated."""
        calc = DateCalculator()
        # January 2026: 3rd is Sat, 4th is Sun, 10th is Sat
        # Add a holiday on Jan 6 (Tue - weekday)
        result = calc.get_exclusion_dates(
            start_date="2026-01-01",
            end_date="2026-01-10",
            holiday_dates=["2026-01-06"],
            include_saturdays=True,
            include_sundays=True,
        )
        data = json.loads(result)
        assert "exclusion_dates" in data
        # Should have Sat 3rd, Sun 4th, Sat 10th, and Tue 6th (holiday)
        assert "2026-01-03" in data["exclusion_dates"]
        assert "2026-01-04" in data["exclusion_dates"]
        assert "2026-01-06" in data["exclusion_dates"]
        assert "2026-01-10" in data["exclusion_dates"]
        assert data["breakdown"]["holidays_on_weekdays"] == 1

    def test_holiday_on_weekend_does_not_double_count(self):
        """Holiday falling on a weekend does not inflate count."""
        calc = DateCalculator()
        # Jan 3, 2026 is Saturday
        result = calc.get_exclusion_dates(
            start_date="2026-01-01",
            end_date="2026-01-10",
            holiday_dates=["2026-01-03"],  # Saturday
            include_saturdays=True,
            include_sundays=True,
        )
        data = json.loads(result)
        # Should still only count once
        assert data["exclusion_dates"].count("2026-01-03") == 1
        assert data["breakdown"]["holidays_on_weekends_deduplicated"] == 1
        assert data["breakdown"]["holidays_on_weekdays"] == 0

    def test_include_saturdays_flag_respected(self):
        """When include_saturdays=False, Saturdays are excluded."""
        calc = DateCalculator()
        result = calc.get_exclusion_dates(
            start_date="2026-01-01",
            end_date="2026-01-10",
            include_saturdays=False,
            include_sundays=True,
        )
        data = json.loads(result)
        # Should not have Saturday Jan 3 or 10
        assert "2026-01-03" not in data["exclusion_dates"]
        assert "2026-01-10" not in data["exclusion_dates"]
        # Should have Sunday Jan 4
        assert "2026-01-04" in data["exclusion_dates"]
        assert data["breakdown"]["saturdays"] == 0
        assert data["breakdown"]["sundays"] > 0

    def test_include_sundays_flag_respected(self):
        """When include_sundays=False, Sundays are excluded."""
        calc = DateCalculator()
        result = calc.get_exclusion_dates(
            start_date="2026-01-01",
            end_date="2026-01-10",
            include_saturdays=True,
            include_sundays=False,
        )
        data = json.loads(result)
        # Should have Saturday Jan 3 and 10
        assert "2026-01-03" in data["exclusion_dates"]
        assert "2026-01-10" in data["exclusion_dates"]
        # Should not have Sunday Jan 4
        assert "2026-01-04" not in data["exclusion_dates"]
        assert data["breakdown"]["saturdays"] > 0
        assert data["breakdown"]["sundays"] == 0

    def test_start_date_after_end_date_returns_error(self):
        """Inverted date range returns error."""
        calc = DateCalculator()
        result = calc.get_exclusion_dates(
            start_date="2026-12-31",
            end_date="2026-01-01",
        )
        data = json.loads(result)
        assert "error" in data
        assert "after end_date" in data["error"]

    def test_invalid_holiday_string_skipped(self):
        """Invalid holiday dates are logged and skipped."""
        calc = DateCalculator()
        result = calc.get_exclusion_dates(
            start_date="2026-01-01",
            end_date="2026-01-10",
            holiday_dates=["2026-01-06", "invalid-date", "2026-99-99"],
        )
        data = json.loads(result)
        # Should still succeed, only valid date included
        assert "2026-01-06" in data["exclusion_dates"]
        assert data["breakdown"]["holidays_in_range"] == 1

    def test_holiday_outside_range_excluded(self):
        """Holidays outside the date range are not included."""
        calc = DateCalculator()
        result = calc.get_exclusion_dates(
            start_date="2026-01-01",
            end_date="2026-01-10",
            holiday_dates=["2026-12-25"],  # Outside range
        )
        data = json.loads(result)
        assert "2026-12-25" not in data["exclusion_dates"]
        assert data["breakdown"]["holidays_in_range"] == 0

    def test_invalid_start_date_format_returns_error(self):
        """Malformed start date returns error."""
        calc = DateCalculator()
        result = calc.get_exclusion_dates(
            start_date="not-a-date",
            end_date="2026-01-10",
        )
        data = json.loads(result)
        assert "error" in data
        assert "Invalid date format" in data["error"]

    def test_exclusion_dates_sorted(self):
        """Exclusion dates are returned in sorted order."""
        calc = DateCalculator()
        result = calc.get_exclusion_dates(
            start_date="2026-01-01",
            end_date="2026-01-31",
            holiday_dates=["2026-01-26", "2026-01-01"],
        )
        data = json.loads(result)
        dates = data["exclusion_dates"]
        assert dates == sorted(dates)

    def test_no_holidays_only_weekends(self):
        """When no holidays provided, only weekends are returned."""
        calc = DateCalculator()
        result = calc.get_exclusion_dates(
            start_date="2026-01-01",
            end_date="2026-01-10",
        )
        data = json.loads(result)
        assert data["breakdown"]["holidays_in_range"] == 0
        assert data["breakdown"]["saturdays"] > 0
        assert data["breakdown"]["sundays"] > 0


# ============================================================================
# DateCalculator.list_weekend_dates
# ============================================================================

class TestListWeekendDates:
    def test_returns_saturdays_and_sundays(self):
        """Returns all Saturday and Sunday dates in range."""
        calc = DateCalculator()
        # Jan 2026: 3rd-4th is Sat-Sun, 10th-11th is Sat-Sun
        result = calc.list_weekend_dates(
            start_date="2026-01-01",
            end_date="2026-01-15",
        )
        data = json.loads(result)
        assert "2026-01-03" in data["weekend_dates"]  # Saturday
        assert "2026-01-04" in data["weekend_dates"]  # Sunday
        assert "2026-01-10" in data["weekend_dates"]  # Saturday
        assert "2026-01-11" in data["weekend_dates"]  # Sunday

    def test_weekend_dates_sorted(self):
        """Weekend dates are returned in sorted order."""
        calc = DateCalculator()
        result = calc.list_weekend_dates(
            start_date="2026-01-01",
            end_date="2026-01-31",
        )
        data = json.loads(result)
        dates = data["weekend_dates"]
        assert dates == sorted(dates)

    def test_saturday_and_sunday_counts_correct(self):
        """Breakdown counts for Saturday and Sunday are accurate."""
        calc = DateCalculator()
        result = calc.list_weekend_dates(
            start_date="2026-01-01",
            end_date="2026-01-31",
        )
        data = json.loads(result)
        # January 2026 has 4 Saturdays and 4 Sundays (except maybe partial)
        assert data["saturdays"] == data["sundays"] or abs(data["saturdays"] - data["sundays"]) == 1
        assert data["total_count"] == data["saturdays"] + data["sundays"]

    def test_start_after_end_returns_error(self):
        """Inverted range returns error."""
        calc = DateCalculator()
        result = calc.list_weekend_dates(
            start_date="2026-12-31",
            end_date="2026-01-01",
        )
        data = json.loads(result)
        assert "error" in data

    def test_invalid_date_format_returns_error(self):
        """Malformed date returns error."""
        calc = DateCalculator()
        result = calc.list_weekend_dates(
            start_date="bad-format",
            end_date="2026-01-31",
        )
        data = json.loads(result)
        assert "error" in data
        assert "Invalid date format" in data["error"]

    def test_single_day_range(self):
        """Single day range (start == end) works correctly."""
        calc = DateCalculator()
        # Jan 4, 2026 is Saturday
        result = calc.list_weekend_dates(
            start_date="2026-01-04",
            end_date="2026-01-04",
        )
        data = json.loads(result)
        assert data["weekend_dates"] == ["2026-01-04"]
        assert data["total_count"] == 1


# ============================================================================
# DateCalculator.parse_holiday_dates
# ============================================================================

class TestParseHolidayDates:
    def test_extracts_iso_format(self):
        """Parses ISO format YYYY-MM-DD."""
        calc = DateCalculator()
        text = "Holidays are 2026-01-26 and 2026-03-15"
        result = calc.parse_holiday_dates(text)
        data = json.loads(result)
        assert "2026-01-26" in data["holidays"]
        assert "2026-03-15" in data["holidays"]

    def test_extracts_dmy_slash_format(self):
        """Parses DD/MM/YYYY format."""
        calc = DateCalculator()
        text = "Holiday on 26/01/2026"
        result = calc.parse_holiday_dates(text)
        data = json.loads(result)
        assert "2026-01-26" in data["holidays"]

    def test_extracts_named_month_formats(self):
        """Parses various named month formats."""
        calc = DateCalculator()
        text = """
        January 26, 2026
        15 March 2026
        26-Jan-2026
        """
        result = calc.parse_holiday_dates(text)
        data = json.loads(result)
        assert "2026-01-26" in data["holidays"]
        assert "2026-03-15" in data["holidays"]

    def test_deduplicates_same_date(self):
        """Same date found multiple times appears only once."""
        calc = DateCalculator()
        text = "2026-01-26 and January 26, 2026 and 26/01/2026"
        result = calc.parse_holiday_dates(text)
        data = json.loads(result)
        assert data["holidays"].count("2026-01-26") == 1
        assert data["total_count"] == 1

    def test_year_filter_applied(self):
        """When year filter provided, only matching year returned."""
        calc = DateCalculator()
        text = "2026-01-26 and 2027-01-26"
        result = calc.parse_holiday_dates(text, year=2026)
        data = json.loads(result)
        assert "2026-01-26" in data["holidays"]
        assert "2027-01-26" not in data["holidays"]
        assert data["year_filter"] == 2026

    def test_no_dates_found_returns_empty(self):
        """Text with no dates returns empty list."""
        calc = DateCalculator()
        text = "No dates in this text at all"
        result = calc.parse_holiday_dates(text)
        data = json.loads(result)
        assert data["holidays"] == []
        assert data["total_count"] == 0

    def test_dates_sorted_chronologically(self):
        """Dates are returned in chronological order."""
        calc = DateCalculator()
        text = "2026-12-25, 2026-01-01, 2026-07-04"
        result = calc.parse_holiday_dates(text)
        data = json.loads(result)
        assert data["holidays"] == ["2026-01-01", "2026-07-04", "2026-12-25"]

    def test_invalid_dates_skipped(self):
        """Invalid dates in text are silently skipped."""
        calc = DateCalculator()
        text = "2026-13-99 is invalid but 2026-01-26 is valid"
        result = calc.parse_holiday_dates(text)
        data = json.loads(result)
        assert "2026-01-26" in data["holidays"]
        assert data["total_count"] == 1

    def test_abbreviated_month_names(self):
        """Abbreviated month names are recognized."""
        calc = DateCalculator()
        text = "26-Jan-2026, 15-Feb-2026, 20-Mar-2026"
        result = calc.parse_holiday_dates(text)
        data = json.loads(result)
        assert "2026-01-26" in data["holidays"]
        assert "2026-02-15" in data["holidays"]
        assert "2026-03-20" in data["holidays"]

    def test_case_insensitive_month_names(self):
        """Month names are case insensitive."""
        calc = DateCalculator()
        text = "JANUARY 26, 2026 and february 15, 2026"
        result = calc.parse_holiday_dates(text)
        data = json.loads(result)
        assert "2026-01-26" in data["holidays"]
        assert "2026-02-15" in data["holidays"]

    def test_details_include_raw_match(self):
        """Details include the raw matched text."""
        calc = DateCalculator()
        text = "Holiday on January 26, 2026"
        result = calc.parse_holiday_dates(text)
        data = json.loads(result)
        assert len(data["details"]) == 1
        assert data["details"][0]["date"] == "2026-01-26"
        assert "raw" in data["details"][0]
