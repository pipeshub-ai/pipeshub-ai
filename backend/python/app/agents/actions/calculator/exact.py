"""Exact arithmetic, date differences and text counts for the calculator tools.

A model doing multi-step arithmetic in its head drops digits (1954 read back
as 195), converts day counts to months by eye (209 days as "7 months"),
subtracts minutes from an h:mm:ss time as if they were seconds, and miscounts
the letters of a word. These are the deterministic versions it can call
instead. Pure functions: no I/O, no `eval`.

Rational arithmetic is exact (`Fraction`), so `0.1 + 0.2` is `0.3` and
`1/3*3` is `1`; only the transcendental functions (logs, trigonometry, a root
that is not exact) go through floating point, and their results say so.
"""

from __future__ import annotations

import ast
import calendar
import functools
import math
import re
import sys
import unicodedata
from dataclasses import asdict, dataclass
from datetime import date
from decimal import Context, Decimal, localcontext
from decimal import Overflow as DecimalOverflow
from fractions import Fraction
from typing import TYPE_CHECKING

import regex

if TYPE_CHECKING:
    from collections.abc import Callable

MAX_EXPRESSION_CHARS = 10_000
MAX_TEXT_CHARS = 2_000
# Every exact value stays under this many digits (numerator and denominator),
# which bounds the CPU one call can spend on the event loop and keeps clear of
# Python's 4,300-digit int-to-str limit. No answer anyone asked for is longer.
MAX_DIGITS = 500
_MAX_BITS = math.ceil(MAX_DIGITS / math.log10(2))
_MAX_NODES = 5_000
# A root is tried exactly only for small indexes (square, cube, ...).
_MAX_EXACT_ROOT = 64
# What a double carries reliably; approximate results are cut here so
# `sqrt(2)**2` reads 2, not 2.0000000000000004.
_SIGNIFICANT_DIGITS = 15
# Inexact decimals inside this range are returned as plain JSON numbers.
_PLAIN_RANGE = (Fraction(1, 10**6), Fraction(10**15))

# Exact or approximate: a `Fraction` came only from exact steps.
Value = Fraction | float
# A value and its power of time: 0 a plain number, 1 seconds, -1 per second.
Quantity = tuple[Value, int]

_TYPOGRAPHIC_OPERATORS = str.maketrans({"×": "*", "÷": "/", "−": "-"})
_CONSTANTS: dict[str, float] = {"pi": math.pi, "e": math.e}
_SECONDS_PER: dict[str, int] = {"seconds": 1, "minutes": 60, "hours": 3600, "days": 86_400}
_HMS = "hms"
# ASCII digits with bounded field lengths: an unbounded field could exceed
# int()'s digit limit and escape as a plain ValueError.
_CLOCK = re.compile(r"([0-9]{1,9}):([0-9]{1,2})(?::([0-9]{1,2}(?:\.[0-9]{1,9})?))?")
_UNITS = {0: None, 1: "seconds", -1: "per second"}
# Digits carried beyond an argument's own when taking its log.
_LOG_GUARD_DIGITS = 30
# Largest rounding error (radians) an approximate trig angle may carry.
_ANGLE_TOLERANCE = 1e-6
_IMPRECISE_ANGLE = (
    "The angle is too large for its precision to give a meaningful sine, cosine or "
    "tangent: pi and function results are approximate. Supply the angle already "
    "reduced to a few radians, or write it exactly with integers and fractions."
)
_TRIG = frozenset({"sin", "cos", "tan"})
_TOO_SMALL = "The result is too small for floating-point functions (below about 1e-308)."

# name: (fewest arguments, most arguments or None for any number)
_ARITY: dict[str, tuple[int, int | None]] = {
    "abs": (1, 1), "round": (1, 2), "min": (1, None), "max": (1, None),
    "floor": (1, 1), "ceil": (1, 1), "sqrt": (1, 1),
    "exp": (1, 1), "log": (1, 2), "log10": (1, 1),
    "sin": (1, 1), "cos": (1, 1), "tan": (1, 1),
    "asin": (1, 1), "acos": (1, 1), "atan": (1, 1), "atan2": (2, 2),
    "radians": (1, 1), "degrees": (1, 1), "hypot": (1, None),
}
_FLOAT_FUNCTIONS: dict[str, Callable[..., float]] = {
    "exp": math.exp, "log": math.log, "log10": math.log10,
    "sin": math.sin, "cos": math.cos, "tan": math.tan,
    "asin": math.asin, "acos": math.acos, "atan": math.atan, "atan2": math.atan2,
    "radians": math.radians, "degrees": math.degrees, "hypot": math.hypot,
}
# Their result is in the unit of their (alike) arguments, so
# `max(hms("1:02:03"), minutes(50))` stays a duration.
_UNIT_PRESERVING = frozenset({"abs", "min", "max", "floor", "ceil"})

_ALLOWED = (
    "Only numbers, pi, e, + - * / // % ** (or ^), parentheses, "
    f"{', '.join(sorted(_ARITY))} and the duration helpers "
    f"{_HMS}, {', '.join(_SECONDS_PER)} are allowed."
)


class CalculationError(ValueError):
    """The input is not something these tools compute; the message says why."""


class _OutsideFloatRange(CalculationError):
    """A value a double cannot hold (it would overflow or underflow)."""

    def __init__(self, message: str, *, too_large: bool = False) -> None:
        super().__init__(message)
        self.too_large = too_large


@dataclass(frozen=True)
class Evaluation:
    """An expression's value and unit (`"seconds"`, `"per second"` or None)."""

    value: Value
    unit: str | None = None

    @property
    def approximate(self) -> bool:
        return isinstance(self.value, float)

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {}
        if isinstance(self.value, float):
            payload["result"] = _approximate_number(self.value)
            payload["approximate"] = True
        else:
            payload.update(_exact_number(self.value))
        if self.unit is not None:
            payload["unit"] = self.unit
        if self.unit == "seconds":
            payload["hms"] = format_hms(self.value)
        return payload


def evaluate(expression: str) -> Evaluation:
    """The value of an arithmetic expression, with its unit.

    Numbers, `pi`, `e`, `+ - * / // % **` (`^` reads as `**`; `×`, `÷` and
    `−` as their ASCII forms), parentheses, the functions in `_ARITY`, and
    the duration helpers `hms("h:mm:ss" | "mm:ss")`, `seconds(n)`,
    `minutes(n)`, `hours(n)`, `days(n)`, which all return seconds. Adding a
    duration to a plain number is refused rather than guessed at. `round`
    rounds halves away from zero (2.5 to 3), not to even.
    """
    if not isinstance(expression, str):
        raise CalculationError('The expression must be text, e.g. "2 * (3 + 4)".')
    if not expression.strip():
        raise CalculationError("The expression is empty.")
    if len(expression) > MAX_EXPRESSION_CHARS:
        raise CalculationError(f"The expression is longer than {MAX_EXPRESSION_CHARS} characters.")
    source = expression.strip().translate(_TYPOGRAPHIC_OPERATORS).replace("^", "**")
    try:
        tree = ast.parse(source, mode="eval")
    except (SyntaxError, ValueError) as exc:
        reason = exc.msg if isinstance(exc, SyntaxError) else str(exc)
        raise CalculationError(f"Not a valid arithmetic expression: {reason}.") from exc
    except (RecursionError, MemoryError) as exc:
        raise CalculationError("The expression is nested too deeply.") from exc
    if isinstance(tree.body, ast.Tuple):
        raise CalculationError(
            "Commas are not allowed: remove thousands separators (1234, not 1,234) "
            "and evaluate one expression at a time.",
        )
    if sum(1 for _ in ast.walk(tree)) > _MAX_NODES:
        raise CalculationError(f"The expression has too many terms (more than {_MAX_NODES}).")
    value, dimension = _evaluate(tree.body, source.encode().splitlines(keepends=True))
    if dimension not in _UNITS:
        raise CalculationError(f"The result is in seconds^{dimension}, which is not a usable unit.")
    return Evaluation(value, _UNITS[dimension])


def format_hms(seconds: Value) -> str:
    """`4955` -> `"1:22:35"`, to the millisecond. Hours are not folded into days."""
    sign = "-" if seconds < 0 else ""
    millis = round(abs(Fraction(seconds)) * 1000)
    hours, rest = divmod(millis, 3_600_000)
    minutes, rest = divmod(rest, 60_000)
    secs, millis = divmod(rest, 1000)
    tail = f".{millis:03d}".rstrip("0") if millis else ""
    return f"{sign}{hours}:{minutes:02d}:{secs:02d}{tail}"


def _exact_number(value: Fraction) -> dict[str, object]:
    if value.denominator == 1:
        return {"result": value.numerator}
    try:
        as_float: float | None = float(value)
    except OverflowError:
        as_float = None
    # A float that reads back as this exact decimal (0.3, 2.5) is the result
    # as it stands; otherwise (1/3, 1e-400, more digits than a double holds)
    # the decimal is computed without floating point and the fraction goes too.
    if as_float is not None and Fraction(repr(as_float)) == value:
        return {"result": as_float}
    return {"result": _decimal(value), "exact": f"{value.numerator}/{value.denominator}"}


def _decimal(value: Fraction) -> float | str:
    """`value` to 15 significant digits, computed in decimal: a number inside
    the plain range, scientific notation (`"1e-400"`) outside it, where a
    double would underflow, overflow or drop digits."""
    with localcontext(Context(prec=_SIGNIFICANT_DIGITS + 10)):
        quotient = Decimal(value.numerator) / Decimal(value.denominator)
    digits = format(quotient, f".{_SIGNIFICANT_DIGITS - 1}e")
    if _PLAIN_RANGE[0] <= abs(value) < _PLAIN_RANGE[1]:
        return float(digits)
    mantissa, exponent = digits.split("e")
    return f"{mantissa.rstrip('0').rstrip('.')}e{exponent}"


def _approximate_number(value: float) -> int | float:
    rounded = float(f"{value:.{_SIGNIFICANT_DIGITS}g}")
    return int(rounded) if rounded.is_integer() and abs(rounded) < 2**53 else rounded


def _evaluate(root: ast.expr, lines: list[bytes]) -> Quantity:
    """Post-order over the tree with an explicit stack: a sum of a few
    thousand terms is a chain deeper than Python's recursion limit."""
    done: dict[int, Quantity] = {}
    stack: list[tuple[ast.expr, bool]] = [(root, False)]
    while stack:
        node, expanded = stack.pop()
        operands = _operands(node)
        if operands and not expanded:
            stack.append((node, True))
            stack.extend((operand, False) for operand in reversed(operands))
            continue
        try:
            done[id(node)] = _apply(node, [done.pop(id(operand)) for operand in operands], lines)
        except _OutsideFloatRange as exc:
            # An approximate angle past a double's range has no meaningful
            # sine either: say that rather than "too large" (sin(10**400 * pi)).
            if exc.too_large and _inside_trig_argument(root, node):
                raise CalculationError(_IMPRECISE_ANGLE) from exc
            raise
    return done[id(root)]


def _inside_trig_argument(root: ast.expr, node: ast.expr) -> bool:
    """Whether `node` lies in the argument of a sin, cos or tan call. Only
    walked when an overflow occurs."""
    parents = {id(child): parent for parent in ast.walk(root) for child in ast.iter_child_nodes(parent)}
    current = parents.get(id(node))
    while current is not None:
        if isinstance(current, ast.Call) and isinstance(current.func, ast.Name) and current.func.id in _TRIG:
            return True
        current = parents.get(id(current))
    return False


def _operands(node: ast.expr) -> list[ast.expr]:
    if isinstance(node, ast.Constant) or (isinstance(node, ast.Name) and node.id in _CONSTANTS):
        return []
    if isinstance(node, ast.BinOp):
        return [node.left, node.right]
    if isinstance(node, ast.UnaryOp):
        return [node.operand]
    if (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and not node.keywords
        and not any(isinstance(arg, ast.Starred) for arg in node.args)
    ):
        name = node.func.id
        if name == _HMS:
            return []
        if name in _SECONDS_PER or name in _ARITY:
            return list(node.args)
    raise CalculationError(_ALLOWED)


def _apply(node: ast.expr, operands: list[Quantity], lines: list[bytes]) -> Quantity:
    if isinstance(node, ast.Constant):
        return _literal(node, lines), 0
    if isinstance(node, ast.Name):
        return _CONSTANTS[node.id], 0
    if isinstance(node, ast.BinOp):
        return _binary(node.op, *operands)
    if isinstance(node, ast.UnaryOp):
        (value, dimension), = operands
        if isinstance(node.op, ast.USub):
            return -value, dimension
        if isinstance(node.op, ast.UAdd):
            return value, dimension
        raise CalculationError(f"Unsupported operator: {type(node.op).__name__}.")
    assert isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    name = node.func.id
    if name == _HMS:
        return _parse_hms(node.args), 1
    if name in _SECONDS_PER:
        if len(operands) != 1:
            raise CalculationError(f"{name}() takes one number.")
        value, dimension = operands[0]
        _require_plain([dimension], f"take {name}() of")
        return _times(value, Fraction(_SECONDS_PER[name])), 1
    return _call(name, operands)


def _literal(node: ast.Constant, lines: list[bytes]) -> Value:
    value = node.value
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise CalculationError(f"{value!r} is not a real number.")
    if isinstance(value, int):
        return _bounded(Fraction(value))
    # The digits as typed, not the nearest double: 1e-400 is not zero. Read
    # from the source split once per expression (`ast.get_source_segment`
    # splits it again on every call). AST column offsets are UTF-8 bytes.
    text = repr(value)
    if node.end_lineno == node.lineno and node.end_col_offset is not None:
        text = lines[node.lineno - 1][node.col_offset:node.end_col_offset].decode()
    try:
        return _bounded(Fraction(text.replace("_", "")))
    except ValueError as exc:
        # Past int()'s digit limit; the cap is far lower.
        raise CalculationError(f"The result is too large (more than {MAX_DIGITS} digits).") from exc


def _bounded(value: Value, *, nonzero: bool = False) -> Value:
    """`nonzero`: the exact result cannot be 0, so a float 0 is an underflow."""
    if isinstance(value, float):
        if math.isnan(value):
            raise CalculationError("The expression did not produce a real number.")
        if math.isinf(value):
            raise _OutsideFloatRange("The result is too large.", too_large=True)
        # An approximate 0 (or a subnormal with a few digits left) in place of
        # a tiny non-zero value would be a wrong answer.
        if (nonzero and value == 0) or 0 < abs(value) < sys.float_info.min:
            raise _OutsideFloatRange(_TOO_SMALL)
        return value
    if value.numerator.bit_length() > _MAX_BITS or value.denominator.bit_length() > _MAX_BITS:
        raise CalculationError(f"The result is too large (more than {MAX_DIGITS} digits).")
    return value


def _as_float(value: Value, *, flush: bool = False) -> float:
    """`flush`: a non-zero value below a double's range becomes a zero of its
    sign instead of an error, for a function whose result does not depend on
    it (exp(1e-400)) and which refuses a result that comes out 0 when it
    cannot be."""
    try:
        result = float(value)
    except OverflowError as exc:
        raise _OutsideFloatRange("The result is too large for floating-point functions.", too_large=True) from exc
    if flush and value != 0 and abs(result) < sys.float_info.min:
        return -0.0 if value < 0 else 0.0
    return _bounded(result, nonzero=value != 0)


def _times(left: Value, right: Value) -> Value:
    if isinstance(left, float) or isinstance(right, float):
        return _as_float(Fraction(left) * Fraction(right))
    return _bounded(left * right)


def _require_alike(dimensions: list[int], action: str) -> int:
    if len(set(dimensions)) > 1:
        raise CalculationError(
            f"Cannot {action} a duration and a plain number: convert the plain "
            "number with seconds(), minutes(), hours() or days() first.",
        )
    return dimensions[0] if dimensions else 0


def _require_plain(dimensions: list[int], action: str) -> None:
    if any(dimensions):
        raise CalculationError(f"Cannot {action} a duration; divide it by seconds(1), minutes(1), ... first.")


def _binary(op: ast.operator, left_q: Quantity, right_q: Quantity) -> Quantity:
    (left, left_dim), (right, right_dim) = left_q, right_q
    if isinstance(op, ast.Pow):
        _require_plain([left_dim, right_dim], "take a power of")
        return _power(left, right), 0
    if isinstance(op, ast.Mult):
        return _times(left, right), left_dim + right_dim
    if isinstance(op, (ast.Div, ast.FloorDiv)):
        dimension = left_dim - right_dim
    elif isinstance(op, (ast.Add, ast.Sub, ast.Mod)):
        dimension = _require_alike([left_dim, right_dim], "add, subtract or take the remainder of")
    else:
        raise CalculationError(f"Unsupported operator: {type(op).__name__}.")
    # An approximate operand takes part with its exact binary value and the
    # result is rounded once: the range of the result decides, not of each
    # operand ((-1e-400) // pi is -1, pi / 10**400 is too small).
    a, b = Fraction(left), Fraction(right)
    try:
        if isinstance(op, ast.Add):
            result = a + b
        elif isinstance(op, ast.Sub):
            result = a - b
        elif isinstance(op, ast.Div):
            result = a / b
        elif isinstance(op, ast.FloorDiv):
            result = Fraction(a // b)
        else:
            result = a % b
    except ZeroDivisionError as exc:
        raise CalculationError("Division by zero.") from exc
    if isinstance(left, Fraction) and isinstance(right, Fraction):
        return _bounded(result), dimension
    return _as_float(result), dimension


def _power(base: Value, exponent: Value) -> Value:
    if isinstance(base, Fraction) and isinstance(exponent, Fraction):
        if exponent.denominator == 1:
            return _integer_power(base, exponent.numerator)
        if exponent.denominator <= _MAX_EXACT_ROOT:
            root = _exact_root(base, exponent.denominator)
            if root is not None:
                return _integer_power(root, exponent.numerator)
    if base < 0 and isinstance(exponent, Fraction) and exponent.denominator % 2 == 1:
        # An odd root of a negative number is real: (-b)**(p/q) = (-1)**p * b**(p/q).
        magnitude = _power(-base, exponent)
        return -magnitude if exponent.numerator % 2 else magnitude
    # Decided on the base and exponent as given, before either is rounded:
    # 0 ** 1e-400 is 0, not the 0 ** 0 of a flushed exponent, and 2 + 1e-400
    # is not an integer although a double rounds it to one.
    if exponent == 0:
        return 1.0
    if base == 0:
        if exponent < 0:
            raise CalculationError("Division by zero.")
        return 0.0
    whole = exponent.denominator == 1 if isinstance(exponent, Fraction) else exponent.is_integer()
    e = _as_float(exponent, flush=True)
    try:
        b = _as_float(base)
    except _OutsideFloatRange:
        if not isinstance(base, Fraction):
            raise
        if base > 0:
            return _decimal_power(base, exponent)
        if not whole:
            raise CalculationError("A negative number to a fractional power has no real value.") from None
        magnitude = _decimal_power(-base, exponent)
        return -magnitude if int(Fraction(exponent)) % 2 else magnitude
    if b < 0 and not whole:
        raise CalculationError("A negative number to a fractional power has no real value.")
    try:
        return _bounded(math.pow(b, e), nonzero=True)
    except OverflowError as exc:
        raise _OutsideFloatRange("The result is too large.", too_large=True) from exc


def _decimal_power(base: Fraction, exponent: Value) -> float:
    """A positive exact base outside a double's range to a fractional power,
    as exp(exponent * ln(base)) in decimal: (2e-400) ** 0.5 is 1.4e-200."""
    exact = Fraction(exponent)
    try:
        with localcontext(Context(prec=_LOG_GUARD_DIGITS)):
            result = (Decimal(exact.numerator) / Decimal(exact.denominator) * _decimal_log(base, base10=False)).exp()
    except DecimalOverflow as exc:
        # Past even the decimal context's exponent range.
        raise _OutsideFloatRange("The result is too large.", too_large=True) from exc
    return _bounded(float(result), nonzero=True)


def _integer_power(base: Fraction, exponent: int) -> Fraction:
    if base == 0 and exponent < 0:
        raise CalculationError("Division by zero.")
    if abs(base) not in (0, 1):
        bits = max(base.numerator.bit_length(), base.denominator.bit_length())
        if abs(exponent) * (bits - 1) > _MAX_BITS:
            raise CalculationError(f"The result is too large (more than {MAX_DIGITS} digits).")
    return _bounded(base**exponent)


def _exact_root(value: Fraction, index: int) -> Fraction | None:
    """The real `index`-th root when it is rational, else None."""
    if value < 0:
        if index % 2 == 0:
            raise CalculationError("A negative number to a fractional power has no real value.")
        root = _exact_root(-value, index)
        return -root if root is not None else None
    numerator = _integer_root(value.numerator, index)
    denominator = _integer_root(value.denominator, index)
    if numerator is None or denominator is None:
        return None
    return Fraction(numerator, denominator)


def _integer_root(n: int, index: int) -> int | None:
    if n < 2:
        return n
    # Newton's method from above; integers only, so exact for any size.
    x = 1 << -(-n.bit_length() // index)
    while True:
        y = ((index - 1) * x + n // x ** (index - 1)) // index
        if y >= x:
            break
        x = y
    return x if x**index == n else None


def _call(name: str, args: list[Quantity]) -> Quantity:
    fewest, most = _ARITY[name]
    if len(args) < fewest or (most is not None and len(args) > most):
        expected = f"{fewest}" if fewest == most else f"at least {fewest}" if most is None else f"{fewest} or {most}"
        raise CalculationError(f"{name}() takes {expected} argument{'s' if most != 1 else ''}.")
    values = [value for value, _ in args]
    dimensions = [dimension for _, dimension in args]
    if name in _UNIT_PRESERVING:
        dimension = _require_alike(dimensions, f"mix in {name}()")
        return _unit_preserving(name, values), dimension
    if name == "round":
        _require_plain(dimensions[1:], "round to")
        return _round(values[0], values[1] if len(values) > 1 else Fraction(0)), dimensions[0]
    _require_plain(dimensions, f"take {name}() of")
    exact = _exact_point(name, values)
    if exact is not None:
        return _bounded(exact), 0
    if name == "sqrt":
        if isinstance(values[0], Fraction):
            if values[0] < 0:
                raise CalculationError("sqrt(): math domain error.")
            root = _exact_root(values[0], 2)
            if root is not None:
                return root, 0
        try:
            return _float_function("sqrt", math.sqrt, values), 0
        except _OutsideFloatRange:
            if not isinstance(values[0], Fraction):
                raise
            return _scaled_sqrt(values[0]), 0
    if name in ("log", "log10"):
        return _logarithm(name, values), 0
    if name in ("asin", "acos"):
        return _arc(name, values[0]), 0
    if name in _TRIG:
        return _trigonometric(name, values[0]), 0
    if name in ("radians", "degrees") and isinstance(values[0], Fraction):
        # Scaled on the exact value, so only the result has to fit a double.
        scale = Fraction(math.pi) / 180 if name == "radians" else 180 / Fraction(math.pi)
        return _as_float(values[0] * scale), 0
    if name == "atan" and isinstance(values[0], Fraction) and abs(values[0]) > 1:
        # atan(x) = ±pi/2 - atan(1/x): fits for any x, 10**400 included.
        inverse = math.atan(_as_float(1 / values[0], flush=True))
        return _bounded((math.pi / 2 if values[0] > 0 else -math.pi / 2) - inverse), 0
    if name == "atan2":
        return _atan2(values[0], values[1]), 0
    return _float_function(name, _FLOAT_FUNCTIONS[name], values), 0


def _scaled_sqrt(value: Fraction) -> float:
    """The square root of an exact value too large or small for a double:
    its root always fits, since the value is under `MAX_DIGITS` digits."""
    shift = (value.numerator.bit_length() - value.denominator.bit_length()) // 2
    return _bounded(math.ldexp(math.sqrt(value / Fraction(4) ** shift), shift))


def _logarithm(name: str, values: list[Value]) -> float:
    if any(value <= 0 for value in values):
        raise CalculationError(f"{name}(): math domain error.")
    logs = [_decimal_log(value, base10=name == "log10") for value in values]
    if len(logs) == 1:
        result = logs[0]
    elif logs[1] == 0:
        raise CalculationError(f"{name}(): division by zero.")
    else:
        # The quotient is formed before converting: log(1 + 1e-300, 1 + 1e-400)
        # is 1e100 although neither log fits in a double.
        with localcontext(Context(prec=_LOG_GUARD_DIGITS)):
            result = logs[0] / logs[1]
    return _bounded(float(result), nonzero=result != 0)


def _decimal_log(value: Value, *, base10: bool) -> Decimal:
    """The log, correctly rounded in decimal at the precision the argument
    carries: a value a double rounds to 1 (1 + 1e-20) keeps its tiny log,
    and one outside a double's range keeps its large one. log10 of a power
    of ten is exact."""
    exact = Fraction(value)
    digits = max(len(str(exact.numerator)), len(str(exact.denominator)))
    with localcontext(Context(prec=digits + _LOG_GUARD_DIGITS)):
        argument = Decimal(exact.numerator) / Decimal(exact.denominator)
        return argument.log10() if base10 else argument.ln()


def _arc(name: str, value: Value) -> float:
    """asin or acos. An exact argument is checked against [-1, 1] before any
    rounding, and near ±1 the angle comes from its exact distance to the
    endpoint, which a double would round away: acos(1 - 1e-20) is 1.4e-10."""
    if isinstance(value, Fraction):
        if abs(value) > 1:
            raise CalculationError(f"{name}(): math domain error.")
        if abs(value) > Fraction(1, 2):
            gap = 1 - abs(value)
            # acos(|x|) = atan2(sqrt(1 - x²), |x|), with 1 - x² = gap * (2 - gap).
            angle = math.atan2(_sqrt_of_exact(gap * (2 - gap)), _as_float(abs(value)))
            if name == "acos":
                return _bounded(angle if value > 0 else math.pi - angle)
            return _bounded(math.pi / 2 - angle if value > 0 else angle - math.pi / 2)
    return _float_function(name, _FLOAT_FUNCTIONS[name], [value])


def _atan2(y: Value, x: Value) -> float:
    """atan2 depends only on the ratio, so both are scaled by the larger
    magnitude first: atan2(10**400, 10**400) is pi/4."""
    scale = max(abs(Fraction(y)), abs(Fraction(x)))
    if scale == 0:
        return 0.0
    return _float_function("atan2", math.atan2, [Fraction(y) / scale, Fraction(x) / scale])


def _trigonometric(name: str, value: Value) -> float:
    if isinstance(value, Fraction) and abs(value) > 1:
        angle = _reduced_angle(value)
        result = _bounded(_FLOAT_FUNCTIONS[name](angle), nonzero=name != "cos")
    else:
        # An approximate angle carries at least half an ulp of rounding, and
        # a sine or cosine passes it straight to the result (|sin'| <= 1):
        # past the tolerance the digits reported would be mostly noise
        # (sin(2**51 + sin(1)) is off by 0.06).
        if isinstance(value, float) and math.ulp(value) / 2 > _ANGLE_TOLERANCE:
            raise CalculationError(_IMPRECISE_ANGLE)
        angle = _as_float(value, flush=True)
        result = _float_function(name, _FLOAT_FUNCTIONS[name], [value])
    # tan amplifies the angle's rounding by its derivative, 1 + tan**2: near
    # a pole the neighbouring doubles of the angle straddle it.
    if name == "tan" and math.ulp(angle) / 2 * (1 + result * result) > _ANGLE_TOLERANCE * max(1.0, abs(result)):
        raise CalculationError(_IMPRECISE_ANGLE)
    return result


def _exact_point(name: str, values: list[Value]) -> Fraction | None:
    """The value of `name` where it is rational, for exact arguments. A
    transcendental function of a non-zero rational is irrational
    (Lindemann-Weierstrass), so these are the trivial points, plus exact
    powers for logs and exact roots for hypot; elsewhere None."""
    if not all(isinstance(value, Fraction) for value in values):
        return None
    x = values[0]
    if x == 0 and name in ("sin", "tan", "asin", "atan", "radians", "degrees"):
        return Fraction(0)
    if x == 0 and name in ("cos", "exp"):
        return Fraction(1)
    if x == 1 and name in ("acos", "log") and len(values) == 1:
        return Fraction(0)
    if name == "atan2" and values[0] == 0 and values[1] > 0:
        return Fraction(0)
    if name == "log10" or (name == "log" and len(values) == 2):
        return _exact_logarithm(x, Fraction(10) if name == "log10" else values[1])
    if name == "hypot":
        return _exact_root(sum(value * value for value in values), 2)
    return None


def _exact_logarithm(value: Fraction, base: Fraction) -> Fraction | None:
    """k when value is exactly base**k for an integer k."""
    if value <= 0 or base <= 0 or base == 1:
        return None
    with localcontext(Context(prec=_LOG_GUARD_DIGITS)):
        estimate = _decimal_log(value, base10=False) / _decimal_log(base, base10=False)
        power = int(estimate.to_integral_value())
    bits = max(base.numerator.bit_length(), base.denominator.bit_length())
    if abs(power) * (bits - 1) > 2 * _MAX_BITS:
        return None
    return Fraction(power) if base**power == value else None


def _reduced_angle(value: Fraction) -> float:
    """An exact angle reduced by a whole number of turns in decimal before it
    is rounded to a double: 10**300 as a double is off by about 10**284
    radians, and even 100000.1 by 7e-12, which a sine would carry."""
    integer_digits = len(str(abs(value.numerator) // value.denominator))
    with localcontext(Context(prec=integer_digits + _LOG_GUARD_DIGITS)):
        angle = Decimal(value.numerator) / Decimal(value.denominator)
        turn = 2 * _decimal_pi()
        return float(angle - turn * (angle / turn).to_integral_value())


@functools.cache
def _decimal_pi() -> Decimal:
    """pi to more digits than any exact value can have (the series from the
    decimal module's documentation), computed once."""
    with localcontext(Context(prec=MAX_DIGITS + 2 * _LOG_GUARD_DIGITS)):
        last, term, total, n, n_step, d, d_step = Decimal(0), Decimal(3), Decimal(3), 1, 0, 0, 24
        while total != last:
            last = total
            n, n_step = n + n_step, n_step + 8
            d, d_step = d + d_step, d_step + 32
            term = term * n / d
            total += term
        return total


def _sqrt_of_exact(value: Fraction) -> float:
    if value == 0:
        return 0.0
    try:
        return math.sqrt(_as_float(value))
    except _OutsideFloatRange:
        return _scaled_sqrt(value)


def _unit_preserving(name: str, values: list[Value]) -> Value:
    if name == "abs":
        return abs(values[0])
    if name == "min":
        return min(values)
    if name == "max":
        return max(values)
    rounding = math.floor if name == "floor" else math.ceil
    value = values[0]
    result = Fraction(rounding(_carried_digits(value) if isinstance(value, float) else value))
    return float(result) if isinstance(value, float) else result


def _round(value: Value, digits: Value) -> Value:
    if not isinstance(digits, Fraction) or digits.denominator != 1 or abs(digits) > MAX_DIGITS:
        raise CalculationError("round(): the number of digits must be a whole number.")
    exact = _carried_digits(value) if isinstance(value, float) else value
    scale = Fraction(10) ** digits.numerator
    magnitude = Fraction(math.floor(abs(exact) * scale + Fraction(1, 2))) / scale
    result = _bounded(magnitude if exact >= 0 else -magnitude)
    # Rounding an approximate value does not make it exact.
    return _as_float(result) if isinstance(value, float) else result


def _carried_digits(value: float) -> Fraction:
    """An approximate value at the precision it is reported to, so rounding
    sees log(1000, 10) as 3, not 2.9999999999999996."""
    return Fraction(f"{value:.{_SIGNIFICANT_DIGITS}g}")


def _float_function(name: str, function: Callable[..., float], values: list[Value]) -> float:
    # Converted outside the try: an argument past a double's range is
    # `_OutsideFloatRange`, which callers with an exact fallback catch. One
    # below it flushes to 0: exp(1e-400) is 1, and sin(1e-400) comes out 0,
    # which `_nonzero_result` then refuses.
    arguments = [_as_float(value, flush=True) for value in values]
    try:
        result = function(*arguments)
    except ZeroDivisionError as exc:
        raise CalculationError(f"{name}(): division by zero.") from exc
    except OverflowError as exc:
        raise _OutsideFloatRange(f"{name}(): the result is too large.", too_large=True) from exc
    except ValueError as exc:
        raise CalculationError(f"{name}(): {exc}.") from exc
    return _bounded(result, nonzero=_nonzero_result(name, values))


def _nonzero_result(name: str, values: list[Value]) -> bool:
    """Whether the exact result of `name` is non-zero, so a 0 is an underflow."""
    if name == "exp":
        return True
    if name in ("hypot",):
        return any(value != 0 for value in values)
    if name in ("sin", "tan", "asin", "atan", "atan2", "radians", "degrees", "sqrt"):
        return values[0] != 0
    return False


def _clip(text: str, limit: int = 40) -> str:
    return text if len(text) <= limit else f"{text[:limit]}..."


def _parse_hms(args: list[ast.expr]) -> Fraction:
    if len(args) != 1 or not isinstance(args[0], ast.Constant) or not isinstance(args[0].value, str):
        raise CalculationError('hms() takes one quoted time, e.g. hms("2:00:35") or hms("38:10").')
    text = args[0].value.strip()
    match = _CLOCK.fullmatch(text)
    if match is None:
        raise CalculationError(f'hms({_clip(text)!r}): write it as "h:mm:ss" or "mm:ss".')
    first, second, third = match.groups()
    if third is None:
        hours, minutes, seconds = 0, int(first), Fraction(second)
    else:
        hours, minutes, seconds = int(first), int(second), Fraction(third)
        if minutes >= 60:
            raise CalculationError(f"hms({text!r}): minutes must be under 60.")
    if seconds >= 60:
        raise CalculationError(f"hms({text!r}): seconds must be under 60.")
    return _bounded(hours * 3600 + minutes * 60 + seconds)


@dataclass(frozen=True)
class DateDifference:
    """How far apart two dates are, counted the ways questions ask for it."""

    start: str
    end: str
    end_before_start: bool
    # Negative when `end` is before `start`; every other count is the size of
    # the span either way round.
    total_days: int
    weeks: int
    remaining_days: int
    # Completed years, then months, then days: "6 years, 11 months, 24 days".
    years: int
    months: int
    days: int
    # `end.year - start.year`, which is what "how old was it in 1776" usually
    # means; it differs from `years` before the anniversary.
    calendar_years: int

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


_ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def date_difference(start: str, end: str) -> DateDifference:
    """The difference from `start` to `end`, both `YYYY-MM-DD`.

    An anniversary that does not exist in the end year falls on the last day
    of its month: Feb 29 to Feb 28 of the next year is one year, Jan 31 to
    Feb 28 one month.
    """
    first, second = _parse_date(start), _parse_date(end)
    reversed_order = second < first
    low, high = (second, first) if reversed_order else (first, second)
    total_months = (high.year - low.year) * 12 + high.month - low.month
    if total_months > 0 and _add_months(low, total_months) > high:
        total_months -= 1
    anchor = _add_months(low, total_months)
    span = (high - low).days
    years, months = divmod(total_months, 12)
    return DateDifference(
        start=first.isoformat(),
        end=second.isoformat(),
        end_before_start=reversed_order,
        total_days=-span if reversed_order else span,
        weeks=span // 7,
        remaining_days=span % 7,
        years=years,
        months=months,
        days=(high - anchor).days,
        calendar_years=second.year - first.year,
    )


def _add_months(day: date, months: int) -> date:
    year, month_index = divmod(day.month - 1 + months, 12)
    year += day.year
    month = month_index + 1
    return date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def _parse_date(value: object) -> date:
    text = value.strip() if isinstance(value, str) else None
    if text is None or not _ISO_DATE.fullmatch(text):
        raise CalculationError(f"Invalid date {value!r}: use YYYY-MM-DD, the calendar date alone.")
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise CalculationError(f"{value!r} is not a calendar date: use YYYY-MM-DD.") from exc


@dataclass(frozen=True)
class TextCount:
    """Counts over a short text, after NFC normalization so an accented
    letter typed as base + combining mark counts once."""

    # Alphabetic code points in any script (`str.isalpha`); digits, spaces,
    # punctuation, emoji and combining marks are not letters.
    letters: int
    # User-perceived characters (grapheme clusters): an emoji with a skin
    # tone, a family emoji or a flag is one character.
    characters: int
    characters_excluding_spaces: int
    # Whitespace-separated tokens holding at least one letter or digit, so
    # "well-known" is one word; text without spaces (e.g. Chinese) is one.
    words: int
    letter: str | None = None
    # Case-insensitive, accent-sensitive: "é" is not "e".
    letter_occurrences: int | None = None

    def to_dict(self) -> dict[str, object]:
        return {key: value for key, value in asdict(self).items() if value is not None}


def count_text(text: str, letter: str | None = None) -> TextCount:
    """Letters, characters and words in `text`, and how often `letter` occurs."""
    if not isinstance(text, str):
        raise CalculationError("The text must be a string.")
    if len(text) > MAX_TEXT_CHARS:
        raise CalculationError(f"The text is longer than {MAX_TEXT_CHARS} characters.")
    normalized = unicodedata.normalize("NFC", text)
    graphemes = regex.findall(r"\X", normalized)
    target = unicodedata.normalize("NFC", letter.strip()) if isinstance(letter, str) else ""
    if target and (len(target) != 1 or not target.isalpha()):
        raise CalculationError(f"{letter!r} is not a single letter.")
    return TextCount(
        letters=sum(ch.isalpha() for ch in normalized),
        characters=len(graphemes),
        characters_excluding_spaces=sum(not cluster.isspace() for cluster in graphemes),
        words=sum(any(ch.isalnum() for ch in token) for token in normalized.split()),
        letter=target or None,
        letter_occurrences=(
            sum(ch.casefold() == target.casefold() for ch in normalized) if target else None
        ),
    )


__all__ = [
    "MAX_DIGITS",
    "MAX_EXPRESSION_CHARS",
    "MAX_TEXT_CHARS",
    "CalculationError",
    "DateDifference",
    "Evaluation",
    "TextCount",
    "count_text",
    "date_difference",
    "evaluate",
    "format_hms",
]
