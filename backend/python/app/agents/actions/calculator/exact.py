"""Exact arithmetic and calendar differences for the calculator tools.

A model doing multi-step arithmetic in its head drops digits (1954 read back
as 195), converts day counts to months by eye (209 days as "7 months"),
subtracts minutes from an h:mm:ss time as if they were seconds, and miscounts
the letters of a word. These are the deterministic versions it can call
instead. Pure functions: no I/O, no `eval`.
"""

from __future__ import annotations

import ast
import calendar
import math
import operator
import re
import unicodedata
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
# Their result is in the unit of their (alike) arguments, so
# `max(hms("1:02:03"), minutes(50))` stays a duration.
_UNIT_PRESERVING = frozenset({"abs", "min", "max", "floor", "ceil"})
_SECONDS_PER: dict[str, int] = {"seconds": 1, "minutes": 60, "hours": 3600, "days": 86_400}
_HMS = "hms"
_CLOCK = re.compile(r"(\d+):(\d{1,2})(?::(\d{1,2}(?:\.\d+)?))?")

MAX_TEXT_CHARS = 2_000


class ExpressionError(ValueError):
    """The expression is not plain arithmetic this evaluator accepts."""


@dataclass(frozen=True)
class Evaluation:
    """An expression's value; `unit` is `"seconds"` (or `"per second"`) when
    it was built from the duration helpers, `None` for a plain number."""

    result: Number
    unit: str | None = None

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {"result": self.result}
        if self.unit is not None:
            payload["unit"] = self.unit
        if self.unit == "seconds":
            payload["hms"] = format_hms(self.result)
        return payload


def evaluate_expression(expression: str) -> Number:
    """The value of an arithmetic expression; see `evaluate`."""
    return evaluate(expression).result


def evaluate(expression: str) -> Evaluation:
    """The value of an arithmetic expression, with its unit.

    Numbers, `pi`, `e`, `+ - * / // % **`, parentheses, the functions in
    `_FUNCTIONS` (rounding, roots, logs, trigonometry in radians), and the
    duration helpers `hms("h:mm:ss" | "mm:ss")`, `seconds(n)`, `minutes(n)`,
    `hours(n)`, `days(n)`, which all return seconds. Adding a duration to a
    plain number is refused rather than guessed at. Anything else -- other
    names, attributes, strings outside `hms` -- is refused.
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
    value, dimension = _evaluate(tree.body)
    units = {0: None, 1: "seconds", -1: "per second"}
    if dimension not in units:
        raise ExpressionError(f"The result is in seconds^{dimension}, which is not a usable unit.")
    return Evaluation(value, units[dimension])


def format_hms(seconds: Number) -> str:
    """`4955` -> `"1:22:35"`. Hours are not folded into days."""
    sign = "-" if seconds < 0 else ""
    whole, fraction = divmod(abs(seconds), 1)
    hours, rest = divmod(int(whole), 3600)
    minutes, secs = divmod(rest, 60)
    tail = f"{fraction:.3f}".rstrip("0")[1:] if fraction else ""
    return f"{sign}{hours}:{minutes:02d}:{secs:02d}{tail}"


def _number(value: object) -> Number:
    # `bool` is an `int` subclass; `True + 1` is not arithmetic anyone typed.
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value
    raise ExpressionError("The expression did not produce a real number.")


# A value and its power of time: 0 a plain number, 1 seconds, -1 per second.
Quantity = tuple[Number, int]


def _require_alike(dimensions: list[int], action: str) -> int:
    if len(set(dimensions)) > 1:
        raise ExpressionError(
            f"Cannot {action} a duration and a plain number: convert the plain "
            "number with seconds(), minutes(), hours() or days() first.",
        )
    return dimensions[0] if dimensions else 0


def _require_plain(dimensions: list[int], action: str) -> None:
    if any(dimensions):
        raise ExpressionError(f"Cannot {action} a duration; divide it by seconds(1), minutes(1), ... first.")


def _evaluate(node: ast.AST) -> Quantity:
    if isinstance(node, ast.Constant):
        return _number(node.value), 0
    if isinstance(node, ast.Name) and node.id in _CONSTANTS:
        return _CONSTANTS[node.id], 0
    if isinstance(node, ast.BinOp):
        return _binary(node)
    if isinstance(node, ast.UnaryOp):
        function = _UNARY.get(type(node.op))
        if function is None:
            raise ExpressionError(f"Unsupported operator: {type(node.op).__name__}.")
        value, dimension = _evaluate(node.operand)
        return _number(function(value)), dimension
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and not node.keywords:
        name = node.func.id
        if name == _HMS:
            return _parse_hms(node.args), 1
        if name in _SECONDS_PER and len(node.args) == 1:
            value, dimension = _evaluate(node.args[0])
            _require_plain([dimension], f"take {name}() of")
            return _number(value * _SECONDS_PER[name]), 1
        if name in _FUNCTIONS:
            return _call(name, [_evaluate(arg) for arg in node.args])
    raise ExpressionError(
        "Only numbers, pi, e, + - * / // % **, parentheses, "
        f"{', '.join(sorted(_FUNCTIONS))} and the duration helpers "
        f"{_HMS}, {', '.join(_SECONDS_PER)} are allowed.",
    )


def _binary(node: ast.BinOp) -> Quantity:
    (left, left_dim), (right, right_dim) = _evaluate(node.left), _evaluate(node.right)
    op = type(node.op)
    if op is ast.Pow:
        _require_plain([left_dim, right_dim], "take a power of")
        return _power(left, right), 0
    if op is ast.Mult:
        dimension = left_dim + right_dim
    elif op in (ast.Div, ast.FloorDiv):
        dimension = left_dim - right_dim
    else:
        dimension = _require_alike([left_dim, right_dim], "add, subtract or take the remainder of")
    function = _BINARY.get(op)
    if function is None:
        raise ExpressionError(f"Unsupported operator: {op.__name__}.")
    try:
        return _number(function(left, right)), dimension
    except ZeroDivisionError as exc:
        raise ExpressionError("Division by zero.") from exc


def _call(name: str, args: list[Quantity]) -> Quantity:
    dimensions = [dimension for _, dimension in args]
    if name in _UNIT_PRESERVING:
        dimension = _require_alike(dimensions, f"mix in {name}()")
    elif name == "round" and args:
        _require_plain(dimensions[1:], "round to")
        dimension = dimensions[0]
    else:
        _require_plain(dimensions, f"take {name}() of")
        dimension = 0
    try:
        return _number(_FUNCTIONS[name](*(value for value, _ in args))), dimension
    except (TypeError, ValueError, OverflowError) as exc:
        raise ExpressionError(f"{name}(): {exc}.") from exc


def _parse_hms(args: list[ast.expr]) -> Number:
    if len(args) != 1 or not isinstance(args[0], ast.Constant) or not isinstance(args[0].value, str):
        raise ExpressionError('hms() takes one quoted time, e.g. hms("2:00:35") or hms("38:10").')
    text = args[0].value.strip()
    match = _CLOCK.fullmatch(text)
    if match is None:
        raise ExpressionError(f'hms({text!r}): write it as "h:mm:ss" or "mm:ss".')
    first, second, third = match.groups()
    if third is None:
        hours, minutes, seconds = 0, int(first), float(second)
    else:
        hours, minutes, seconds = int(first), int(second), float(third)
        if minutes >= 60:
            raise ExpressionError(f"hms({text!r}): minutes must be under 60.")
    if seconds >= 60:
        raise ExpressionError(f"hms({text!r}): seconds must be under 60.")
    total = hours * 3600 + minutes * 60 + seconds
    return int(total) if total.is_integer() else total


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


@dataclass(frozen=True)
class TextCount:
    """Counts over a short text, after NFC normalization so an accented
    letter typed as base + combining mark counts once."""

    # Alphabetic characters in any script (`str.isalpha`); digits, spaces,
    # punctuation and combining marks are not letters.
    letters: int
    characters: int
    characters_excluding_spaces: int
    # Whitespace-separated tokens holding at least one letter or digit.
    words: int
    letter: str | None = None
    # Case-insensitive, accent-sensitive: "é" is not "e".
    letter_occurrences: int | None = None

    def to_dict(self) -> dict[str, object]:
        return {key: value for key, value in asdict(self).items() if value is not None}


def count_text(text: str, letter: str | None = None) -> TextCount:
    """Letters, characters and words in `text`, and how often `letter` occurs."""
    if not isinstance(text, str):
        raise ValueError("The text must be a string.")
    if len(text) > MAX_TEXT_CHARS:
        raise ValueError(f"The text is longer than {MAX_TEXT_CHARS} characters.")
    normalized = unicodedata.normalize("NFC", text)
    target = unicodedata.normalize("NFC", letter.strip()) if letter else ""
    if letter and (len(target) != 1 or not target.isalpha()):
        raise ValueError(f"{letter!r} is not a single letter.")
    return TextCount(
        letters=sum(ch.isalpha() for ch in normalized),
        characters=len(normalized),
        characters_excluding_spaces=sum(not ch.isspace() for ch in normalized),
        words=sum(any(ch.isalnum() for ch in token) for token in normalized.split()),
        letter=target or None,
        letter_occurrences=(
            sum(ch.casefold() == target.casefold() for ch in normalized) if target else None
        ),
    )


__all__ = [
    "MAX_EXPRESSION_CHARS",
    "MAX_TEXT_CHARS",
    "DateDifference",
    "Evaluation",
    "ExpressionError",
    "TextCount",
    "count_text",
    "date_difference",
    "evaluate",
    "evaluate_expression",
    "format_hms",
]
