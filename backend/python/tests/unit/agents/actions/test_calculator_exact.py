"""`app/agents/actions/calculator/exact.py` -- the arithmetic and calendar
differences the model calls instead of computing in its head."""

from __future__ import annotations

import random
from datetime import date, timedelta

import pytest

from app.agents.actions.calculator.exact import (
    MAX_EXPRESSION_CHARS,
    ExpressionError,
    date_difference,
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
