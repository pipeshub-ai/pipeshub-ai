"""`app/agents/actions/calculator/exact.py` -- the arithmetic and calendar
differences the model calls instead of computing in its head."""

from __future__ import annotations

import random
from datetime import date, timedelta

import pytest

from app.agents.actions.calculator.exact import (
    MAX_EXPRESSION_CHARS,
    MAX_TEXT_CHARS,
    ExpressionError,
    count_text,
    date_difference,
    evaluate,
    evaluate_expression,
)


class TestEvaluateExpression:
    @pytest.mark.parametrize(("expression", "expected"), [
        ("(90 - 16) * 1954", 144_596),
        ("51176 - 7658", 43_518),
        ("25.0 - 14.7", pytest.approx(10.3)),
        ("2 ** 10", 1024),
        ("2 ^ 10", 1024),
        ("-3 + +5", 2),
        ("17 // 5", 3),
        ("17 % 5", 2),
        ("round(2 / 3, 2)", 0.67),
        ("max(3, 9, 4) - min(3, 9, 4)", 6),
        ("sqrt(144) + abs(-2)", 14),
        ("floor(2.7) + ceil(2.1)", 5),
        ("round(degrees(pi), 6)", 180.0),
        ("round(log10(1000) + log(e), 9)", 4.0),
    ])
    def test_arithmetic(self, expression: str, expected: object) -> None:
        assert evaluate_expression(expression) == expected

    @pytest.mark.parametrize("expression", [
        "__import__('os').system('true')",
        "open('/etc/passwd')",
        "(1).__class__",
        "x + 1",
        "'a' * 3",
        "[1, 2]",
        "True + 1",
        "lambda: 1",
        "round(1.5, ndigits=0)",
        "1 if 2 else 3",
    ])
    def test_anything_but_arithmetic_is_refused(self, expression: str) -> None:
        with pytest.raises(ExpressionError):
            evaluate_expression(expression)

    @pytest.mark.parametrize("expression", ["9 ** 9 ** 9", "10 ** 1000", "(-8) ** 0.5"])
    def test_powers_that_cannot_be_answered_are_refused(self, expression: str) -> None:
        with pytest.raises(ExpressionError):
            evaluate_expression(expression)

    def test_a_great_circle_distance(self) -> None:
        """Haversine between two points: the kind of multi-step formula a
        model gets wrong by a few percent in its head."""
        lat1, lon1, lat2, lon2 = 40.7128, -74.0060, 51.5074, -0.1278
        expression = (
            f"2 * 3958.8 * asin(sqrt(sin(radians({lat2} - {lat1}) / 2) ** 2 + "
            f"cos(radians({lat1})) * cos(radians({lat2})) * "
            f"sin(radians({lon2} - {lon1}) / 2) ** 2))"
        )

        assert evaluate_expression(expression) == pytest.approx(3461, abs=5)

    def test_a_domain_error_is_an_expression_error(self) -> None:
        with pytest.raises(ExpressionError):
            evaluate_expression("log(0)")
        with pytest.raises(ExpressionError):
            evaluate_expression("asin(2)")

    def test_division_by_zero_is_an_expression_error(self) -> None:
        with pytest.raises(ExpressionError, match="zero"):
            evaluate_expression("1 / (2 - 2)")

    def test_empty_and_oversized_input_is_refused(self) -> None:
        with pytest.raises(ExpressionError):
            evaluate_expression("   ")
        with pytest.raises(ExpressionError):
            evaluate_expression("1+" * MAX_EXPRESSION_CHARS + "1")


class TestDateDifference:
    def test_a_gap_under_a_year_in_months_and_days(self) -> None:
        """Elliot Handler (9 Apr 1916) to Ruth Handler (4 Nov 1916): 209 days
        is 6 months 26 days, not 7."""
        diff = date_difference("1916-04-09", "1916-11-04")

        assert diff.total_days == 209
        assert (diff.years, diff.months, diff.days) == (0, 6, 26)

    def test_an_age_just_before_a_birthday(self) -> None:
        diff = date_difference("1918-07-13", "1926-07-07")

        assert (diff.years, diff.months, diff.days) == (7, 11, 24)
        assert diff.calendar_years == 8

    def test_month_end_and_leap_day(self) -> None:
        diff = date_difference("2024-01-31", "2024-02-29")

        assert diff.total_days == 29
        assert (diff.years, diff.months, diff.days) == (0, 0, 29)

    def test_borrowing_across_a_year_boundary(self) -> None:
        diff = date_difference("2019-12-20", "2020-01-05")

        assert (diff.years, diff.months, diff.days) == (0, 0, 16)

    def test_order_does_not_matter(self) -> None:
        assert date_difference("2000-05-01", "1990-01-01") == date_difference("1990-01-01", "2000-05-01")

    def test_weeks_and_days(self) -> None:
        diff = date_difference("2026-01-01", "2026-01-20")

        assert (diff.weeks, diff.remaining_days) == (2, 5)

    def test_invalid_dates_name_the_format(self) -> None:
        with pytest.raises(ValueError, match="YYYY-MM-DD"):
            date_difference("July 4, 1776", "1776-07-05")

    def test_the_breakdown_always_adds_back_up(self) -> None:
        """Adding the years, months and days back onto the start lands on the
        end, for any pair of dates."""
        rng = random.Random(3)
        for _ in range(2_000):
            start = date(1600, 1, 1) + timedelta(days=rng.randint(0, 200_000))
            end = start + timedelta(days=rng.randint(0, 40_000))

            diff = date_difference(start.isoformat(), end.isoformat())

            assert 0 <= diff.months < 12
            assert diff.days >= 0
            month_index = start.month - 1 + diff.months
            year = start.year + diff.years + month_index // 12
            month = month_index % 12 + 1
            anchor = date(year, month, 1) + timedelta(days=start.day - 1)
            assert anchor + timedelta(days=diff.days) == end or _clamped(start, year, month, diff.days, end)
            assert diff.total_days == (end - start).days


def _clamped(start: date, year: int, month: int, days: int, end: date) -> bool:
    """A start on the 29th-31st lands past a shorter month's end; the
    breakdown then counts from that month's last day instead."""
    import calendar

    last = calendar.monthrange(year, month)[1]
    if start.day <= last:
        return False
    return date(year, month, last) + timedelta(days=days + start.day - last) == end


class TestDurations:
    def test_minutes_off_a_clock_time(self) -> None:
        """A race time minus 38 minutes: minutes must not be read as seconds."""
        result = evaluate('hms("2:00:35") - minutes(38)')

        assert result.to_dict() == {"result": 4955, "unit": "seconds", "hms": "1:22:35"}

    @pytest.mark.parametrize(("expression", "expected"), [
        ('hms("38:10")', 2290),
        ('hms("1:02:03.5")', 3723.5),
        ("hours(1.5) + minutes(30) + seconds(15)", 7215),
        ("days(2)", 172_800),
        ('max(hms("1:02:03"), minutes(50))', 3723),
        ('round(hms("0:00:01.26"), 1)', 1.3),
    ])
    def test_every_duration_is_in_seconds(self, expression: str, expected: object) -> None:
        result = evaluate(expression)

        assert result.result == pytest.approx(expected)
        assert result.unit == "seconds"

    def test_dividing_by_a_unit_converts_to_it(self) -> None:
        result = evaluate('(hms("2:00:35") - minutes(38)) / minutes(1)')

        assert result.result == pytest.approx(82.5833, abs=1e-4)
        assert result.unit is None

    def test_a_rate_is_per_second(self) -> None:
        assert evaluate("42 / hours(2)").unit == "per second"

    @pytest.mark.parametrize("expression", [
        'hms("2:00:35") - 38',
        "minutes(38) + 1",
        'max(hms("1:00"), 5)',
        "sqrt(hours(1))",
        "hours(1) * hours(1)",
        'hms("1:00") ** 2',
        'hms("1:75:00")',
        'hms("0:61")',
        'hms("two hours")',
        "hms(120)",
        'hms("1:00", "2:00")',
        "minutes()",
        "'1:00' + 1",
    ])
    def test_mixed_units_and_bad_durations_are_refused(self, expression: str) -> None:
        with pytest.raises(ExpressionError):
            evaluate(expression)

    def test_plain_arithmetic_has_no_unit(self) -> None:
        assert evaluate("(90 - 16) * 1954").to_dict() == {"result": 144_596}


class TestCountText:
    def test_a_ten_letter_word(self) -> None:
        count = count_text("Lumberjack")

        assert (count.letters, count.characters, count.words) == (10, 10, 1)

    @pytest.mark.parametrize("text", ["Kraftwerké", "Kraftwerké"])
    def test_an_accented_letter_counts_once_however_it_is_encoded(self, text: str) -> None:
        assert count_text(text).letters == 10

    def test_other_scripts_are_letters(self) -> None:
        assert count_text("Москва 東京").letters == 8

    def test_spaces_digits_and_punctuation_are_not_letters(self) -> None:
        count = count_text("Saint-Étienne, 42!")

        assert count.letters == 12
        assert count.characters == 18
        assert count.characters_excluding_spaces == 17
        assert count.words == 2

    def test_a_lone_dash_is_not_a_word(self) -> None:
        assert count_text("before — after").words == 2

    def test_one_letter_ignores_case_but_not_accents(self) -> None:
        assert count_text("Eleven élites", letter="e").letter_occurrences == 4

    @pytest.mark.parametrize("letter", ["ab", "3", "!"])
    def test_a_letter_must_be_one_letter(self, letter: str) -> None:
        with pytest.raises(ValueError):
            count_text("anything", letter=letter)

    def test_oversized_text_is_refused(self) -> None:
        with pytest.raises(ValueError, match=str(MAX_TEXT_CHARS)):
            count_text("a" * (MAX_TEXT_CHARS + 1))
