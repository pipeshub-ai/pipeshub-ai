"""Exact arithmetic and calendar differences for the calculator tools.

A model doing multi-step arithmetic in its head drops digits (1954 read back
as 195) and converts day counts to months by eye (209 days as "7 months").
These are the deterministic versions it can call instead. Pure functions: no
I/O, no `eval`.
"""

from __future__ import annotations

import ast
import calendar
import math
import operator
from dataclasses import asdict, dataclass
from datetime import date
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

MAX_EXPRESSION_CHARS = 500
# Past this many digits a result is not an answer anyone asked for, and
# computing it can stall the event loop (`9**9**9`).
_MAX_RESULT_DIGITS = 400

Number = int | float

_BINARY: dict[type[ast.operator], Callable[[Number, Number], object]] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
}
_UNARY: dict[type[ast.unaryop], Callable[[Number], object]] = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}
_FUNCTIONS: dict[str, Callable[..., object]] = {
    "abs": abs,
    "round": round,
    "min": min,
    "max": max,
    "sqrt": math.sqrt,
    "floor": math.floor,
    "ceil": math.ceil,
    "exp": math.exp,
    "log": math.log,
    "log10": math.log10,
    "sin": math.sin,
    "cos": math.cos,
    "tan": math.tan,
    "asin": math.asin,
    "acos": math.acos,
    "atan": math.atan,
    "atan2": math.atan2,
    "radians": math.radians,
    "degrees": math.degrees,
}
_CONSTANTS: dict[str, float] = {"pi": math.pi, "e": math.e}


class ExpressionError(ValueError):
    """The expression is not plain arithmetic this evaluator accepts."""


def evaluate_expression(expression: str) -> Number:
    """The value of an arithmetic expression.

    Numbers, `pi`, `e`, `+ - * / // % **`, parentheses, and the functions in
    `_FUNCTIONS` (rounding, roots, logs, trigonometry in radians). Anything
    else -- other names, attributes, strings -- is refused.
    """
    text = expression.strip()
    if not text:
        raise ExpressionError("The expression is empty.")
    if len(text) > MAX_EXPRESSION_CHARS:
        raise ExpressionError(f"The expression is longer than {MAX_EXPRESSION_CHARS} characters.")
    try:
        tree = ast.parse(text.replace("^", "**"), mode="eval")
    except SyntaxError as exc:
        raise ExpressionError(f"Not a valid arithmetic expression: {exc.msg}.") from exc
    return _evaluate(tree.body)


def _number(value: object) -> Number:
    # `bool` is an `int` subclass; `True + 1` is not arithmetic anyone typed.
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value
    raise ExpressionError("The expression did not produce a real number.")


def _evaluate(node: ast.AST) -> Number:
    if isinstance(node, ast.Constant):
        return _number(node.value)
    if isinstance(node, ast.Name) and node.id in _CONSTANTS:
        return _CONSTANTS[node.id]
    if isinstance(node, ast.BinOp):
        left, right = _evaluate(node.left), _evaluate(node.right)
        if isinstance(node.op, ast.Pow):
            return _power(left, right)
        function = _BINARY.get(type(node.op))
        if function is None:
            raise ExpressionError(f"Unsupported operator: {type(node.op).__name__}.")
        try:
            return _number(function(left, right))
        except ZeroDivisionError as exc:
            raise ExpressionError("Division by zero.") from exc
    if isinstance(node, ast.UnaryOp):
        function = _UNARY.get(type(node.op))
        if function is None:
            raise ExpressionError(f"Unsupported operator: {type(node.op).__name__}.")
        return _number(function(_evaluate(node.operand)))
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id in _FUNCTIONS
        and not node.keywords
    ):
        args = [_evaluate(arg) for arg in node.args]
        try:
            return _number(_FUNCTIONS[node.func.id](*args))
        except (TypeError, ValueError, OverflowError) as exc:
            raise ExpressionError(f"{node.func.id}(): {exc}.") from exc
    raise ExpressionError(
        "Only numbers, pi, e, + - * / // % **, parentheses and "
        f"{', '.join(sorted(_FUNCTIONS))} are allowed.",
    )


def _power(base: Number, exponent: Number) -> Number:
    if base != 0 and abs(base) != 1:
        digits = abs(exponent) * math.log10(abs(base))
        if digits > _MAX_RESULT_DIGITS:
            raise ExpressionError("The result is too large.")
    try:
        result = base ** exponent
    except (OverflowError, ZeroDivisionError) as exc:
        raise ExpressionError(f"Cannot compute this power: {exc}.") from exc
    if isinstance(result, complex):
        raise ExpressionError("A negative number to a fractional power has no real value.")
    return _number(result)


@dataclass(frozen=True)
class DateDifference:
    """How far apart two dates are, counted the ways questions ask for it."""

    start: str
    end: str
    total_days: int
    weeks: int
    remaining_days: int
    # Completed years, then months, then days: "6 years, 11 months, 24 days".
    years: int
    months: int
    days: int
    # `end.year - start.year`, which is what "how old was it in 1776"
    # usually means; differs from `years` before the anniversary.
    calendar_years: int

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def date_difference(start: str, end: str) -> DateDifference:
    """The difference from `start` to `end`, both `YYYY-MM-DD`.

    Swapped arguments are put in order, so the result never goes negative.
    """
    first, second = _parse_date(start), _parse_date(end)
    if second < first:
        first, second = second, first
    years = second.year - first.year
    months = second.month - first.month
    days = second.day - first.day
    if days < 0:
        months -= 1
        borrow_year, borrow_month = (
            (second.year, second.month - 1) if second.month > 1 else (second.year - 1, 12)
        )
        days += calendar.monthrange(borrow_year, borrow_month)[1]
    if months < 0:
        years -= 1
        months += 12
    total = (second - first).days
    return DateDifference(
        start=first.isoformat(),
        end=second.isoformat(),
        total_days=total,
        weeks=total // 7,
        remaining_days=total % 7,
        years=years,
        months=months,
        days=days,
        calendar_years=second.year - first.year,
    )


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value.strip())
    except (AttributeError, ValueError) as exc:
        raise ValueError(f"Invalid date {value!r}: use YYYY-MM-DD.") from exc


__all__ = [
    "MAX_EXPRESSION_CHARS",
    "DateDifference",
    "ExpressionError",
    "date_difference",
    "evaluate_expression",
]
