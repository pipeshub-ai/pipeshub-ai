"""Exact arithmetic, date differences and text counts behind the calculator tools."""

from __future__ import annotations

import contextlib
import json
import math
import random
import time

import pytest

from app.agents.actions.calculator.exact import (
    MAX_EXPRESSION_CHARS,
    MAX_TEXT_CHARS,
    CalculationError,
    count_text,
    date_difference,
    evaluate,
)


def _result(expression: str) -> dict[str, object]:
    return evaluate(expression).to_dict()


class TestExactArithmetic:
    @pytest.mark.parametrize(
        ("expression", "expected"),
        [
            ("0.1 + 0.2", 0.3),
            ("1/3*3", 1),
            ("(90 - 16) * 1954", 144596),
            ("2^10", 1024),
            ("10 / 4", 2.5),
            ("2 ** -1", 0.5),
            ("7 // 2", 3),
            ("-7 // 2", -4),
            ("7 % 3", 1),
            ("-7 % 3", 2),
            ("1e-400 * 1e400", 1),
            ("1_000 + 1", 1001),
            ("  2 + 2 \n", 4),
            ("6 × 7", 42),
            ("84 ÷ 2", 42),
            ("5 − 3", 2),
            ("-(-3)", 3),
            ("+4", 4),
            ("1e308 * 10 / 1e309", 1),
        ],
    )
    def test_rational_results_are_exact(self, expression: str, expected: object) -> None:
        payload = _result(expression)
        assert payload["result"] == expected
        assert type(payload["result"]) is type(expected)
        assert "approximate" not in payload
        assert "exact" not in payload

    def test_a_repeating_decimal_carries_its_exact_fraction(self) -> None:
        payload = _result("1/3")
        assert payload["result"] == pytest.approx(1 / 3)
        assert payload["exact"] == "1/3"

    @pytest.mark.parametrize(
        ("expression", "expected"),
        [
            # Out of a double's range or precision: a decimal string, never 0.0.
            ("1e-400", "1e-400"),
            ("-1e-400", "-1e-400"),
            ("(1/7)**499", "1.97732488871112e-422"),
            ("2/3*1e-320", "6.66666666666667e-321"),
            ("10**450/3", "3.33333333333333e+449"),
            # Either side of the plain-notation range [1e-6, 1e15).
            ("1/3*1e-6", "3.33333333333333e-7"),
            ("1/3*1e-5", 3.33333333333333e-06),
            ("10**15/3", 333333333333333.0),
            ("10**16/3", "3.33333333333333e+15"),
            ("1/3", 0.333333333333333),
            # round() stays exact too.
            ("round(2/3*1e-320, 330)", "6.666666667e-321"),
        ],
    )
    def test_an_inexact_decimal_is_computed_without_floating_point(
        self, expression: str, expected: object,
    ) -> None:
        payload = _result(expression)
        assert payload["result"] == expected
        assert "exact" in payload
        assert "approximate" not in payload

    @pytest.mark.parametrize(
        ("expression", "expected"),
        [("0.3", 0.3), ("3703703.673", 3703703.673), ("1.5e-7", 1.5e-7), ("2.5", 2.5)],
    )
    def test_a_decimal_a_float_holds_exactly_stays_a_plain_number(
        self, expression: str, expected: float,
    ) -> None:
        assert _result(expression) == {"result": expected}

    def test_a_decimal_with_more_digits_than_a_float_keeps_them(self) -> None:
        payload = _result("0.12345678901234567891")
        assert payload["exact"] == "12345678901234567891/100000000000000000000"

    @pytest.mark.parametrize(
        ("expression", "expected"),
        [
            ("sqrt(16)", 4),
            ("sqrt(9/4)", 1.5),
            ("8 ** (1/3)", 2),
            ("27 ** (2/3)", 9),
            ("(-8) ** (1/3)", -2),
            ("-8 ** (1/3)", -2),
            ("4 ** 0.5", 2),
            ("sqrt(10**400) / 10**199", 10),
            ("(1e-400) ** 0.3", 1e-120),
        ],
    )
    def test_exact_roots_stay_exact(self, expression: str, expected: object) -> None:
        payload = _result(expression)
        assert payload["result"] == expected
        assert "approximate" not in payload

    @pytest.mark.parametrize(
        ("expression", "expected"),
        [
            ("round(2.5)", 3),
            ("round(-2.5)", -3),
            ("round(0.5)", 1),
            ("round(2.675, 2)", 2.68),
            ("round(1234, -2)", 1200),
            ("round(1/3, 4)", 0.3333),
            ("floor(-2.5)", -3),
            ("ceil(2.1)", 3),
            ("abs(-3)", 3),
            ("max(3, 9, 4)", 9),
            ("min(5)", 5),
            ("max(1/3, 0.3)", 1 / 3),
        ],
    )
    def test_rounding_and_selection(self, expression: str, expected: object) -> None:
        assert _result(expression)["result"] == pytest.approx(expected)

    @pytest.mark.parametrize(
        ("expression", "expected"),
        [
            ("sqrt(2)", 1.4142135623731),
            ("2*pi", 6.28318530717959),
            ("log(100) / log(10)", 2),
            ("log10(1000.5)", 3.00021709297223),
            ("exp(1)", 2.71828182845905),
            ("sin(pi/2)", 1),
            ("degrees(atan2(1, 1))", 45),
            ("sqrt(2)**2", 2),
            ("hypot(2, 3)", 3.60555127546399),
        ],
    )
    def test_floating_point_functions_are_marked_approximate(self, expression: str, expected: float) -> None:
        payload = _result(expression)
        assert payload["result"] == pytest.approx(expected, rel=1e-14)
        assert payload["approximate"] is True

    @pytest.mark.parametrize(
        ("expression", "expected"),
        [
            ("round(2*pi, 10)", 6.2831853072),
            ("round(sin(1), 20)", 0.841470984807897),
            # Cut to the 15 digits an approximate value carries before
            # rounding: log(1000, 10) is 2.9999999999999996 in binary.
            ("floor(log(1000) / log(10))", 3),
            ("ceil(sqrt(2)**2)", 2),
            ("round(log(1000) / log(10))", 3),
        ],
    )
    def test_rounding_an_approximate_value_stays_approximate(self, expression: str, expected: float) -> None:
        payload = _result(expression)
        assert payload["result"] == expected
        assert payload["approximate"] is True
        assert "exact" not in payload

    def test_float_literals_are_read_from_one_split_of_the_source(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # get_source_segment re-splits the whole source on every call: with
        # thousands of literals that is quadratic.
        def _refuse(*_args: object, **_kwargs: object) -> str:
            raise AssertionError("get_source_segment called")

        monkeypatch.setattr("ast.get_source_segment", _refuse)
        assert _result(" + ".join(["0.1"] * 10)) == {"result": 1}
        assert _result("(1.5\n + 2.25)") == {"result": 3.75}
        assert _result("(1.5\r\n+ 2.25\r+ 0.125\n+ 1e-400*1e400)") == {"result": 4.875}

    @pytest.mark.parametrize(
        ("expression", "expected"),
        [
            # An odd root of a negative number is real: (-b)**(p/q) = (-1)**p * b**(p/q).
            ("(-10)**(1/3)", -2.15443469003188),
            ("(-2)**(1/3)", -1.25992104989487),
            ("(-2)**(2/3)", 1.5874010519682),
            ("(-16)**(1/5)", -1.74110112659225),
            ("(-sqrt(2))**(1/3)", -1.12246204830937),
            # Out of a double's range, computed without converting the input.
            ("sqrt(2e-400)", 1.4142135623731e-200),
            ("sqrt(2*10**400)", 1.4142135623731e200),
            ("log(10**400) / log(10)", 400),
            # Values a double rounds to 1: the log comes from the exact gap.
            ("log(1 + 1e-20)", 1e-20),
            ("log10(1 + 1e-16)", 4.34294481903252e-17),
            ("log(1 + 1e-300)", 1e-300),
            ("log(1 - 1e-20)", -1e-20),
            ("log(10, 1 + 1e-18)", 2.30258509299405e18),
            ("log(10**400, 1 + 1e-20)", 9.21034037197618e22),
            ("log10(1.5)", 0.176091259055681),
            # A two-argument log divides before converting, so tiny gaps cancel.
            ("log(1 + 1e-300, 1 + 1e-400)", 1e100),
            ("log(1 + 1e-400, 1 + 2e-400)", 0.5),
            ("log(2, 1 + 1e-308)", 6.93147180559945e307),
            # Near ±1 a double rounds the argument onto the endpoint.
            ("acos(1 - 1e-20)", 1.4142135623731e-10),
            ("acos(1 - 5e-17)", 1e-8),
            ("acos(1 - 1e-16)", 1.4142135623731e-8),
            ("acos(1 - 1e-400)", 1.4142135623731e-200),
            ("asin(1 - 1e-20)", 1.57079632665348),
            ("acos(-1 + 1e-20)", 3.14159265344837),
            ("asin(-1 + 1e-20)", -1.57079632665348),
            ("acos(0.75)", 0.722734247813416),
            ("asin(0.75)", 0.848062078981481),
            ("acos(-1)", 3.14159265358979),
            # An input below a double's range that the result does not depend on.
            ("pi + 1e-400", 3.14159265358979),
            ("sqrt(2) - 1e-400", 1.4142135623731),
            ("exp(1e-400)", 1),
            ("cos(1e-400)", 1),
            ("acos(1e-400)", 1.5707963267949),
            ("hypot(1, 1e-400)", 1),
            ("atan2(1, 1e-400)", 1.5707963267949),
            ("2 ** 1e-400", 1),
            # An input past a double's range whose result fits.
            ("atan(10**400)", 1.5707963267949),
            ("atan(-10**400)", -1.5707963267949),
            ("atan2(10**400, 10**400)", 0.785398163397448),
            ("(2e-400) ** 0.5", 1.4142135623731e-200),
            ("(2e-400) ** 0.25", 1.18920711500272e-100),
            ("(2*10**400) ** (1/3)", 2.71441761659491e133),
            # A flushed input keeps its sign.
            ("atan2(-1e-400, -1)", -3.14159265358979),
            ("degrees(atan2(-1e-400, -1))", -180),
            # Arithmetic with an approximate operand is exact, then rounded once.
            ("(-1e-400) // pi", -1),
            ("(-1e-400) % pi", 3.14159265358979),
            ("pi // 10**400", 0),
            ("(-pi) // 10**400", -1),
            ("sqrt(2) % 10**400", 1.4142135623731),
            # Powers decided by the exponent and base as given, not as rounded.
            ("0 ** 1e-400", 0),
            ("sin(0) ** 1e-400", 0),
            ("0 ** 1e-20", 0),
            ("(-10**400) ** (log(e) - 1)", 1),
            ("(-1e-400) ** (log(e) - 1)", 1),
            ("(10**400) ** (log(e) - 1)", 1),
            # An exact trig argument is reduced by 2*pi in decimal, not after
            # rounding it to a double: 10**300 as a double is off by about
            # 10**284 radians, and 100000.1 by 7e-12.
            ("sin(10**300)", -0.985750425160377),
            ("cos(10**300)", -0.168214444374245),
            ("tan(10**300)", 5.8600819259449),
            ("sin(-10**300)", 0.985750425160377),
            ("sin(100000.1)", -0.0641994009836506),
            ("cos(10**300 / 12.5)", -0.296129482721992),
            ("sin(10**499 + 1/3)", -0.0124529466817837),
            ("tan(10**22)", -1.6287782256069),
            # Near a zero or pole the residual after reducing by pi/2 is tiny;
            # it is computed in decimal, not rounded onto pi first.
            ("sin(3.14159265358979323846)", 2.64338327950288e-21),
            ("cos(1.570796326794896619)", 2.31321691639751e-19),
            ("tan(3.14159265358979323846)", -2.64338327950288e-21),
            ("cos(3.14159265358979323846)", -1),
            ("sin(4.712388980384689857)", -1),
            ("tan(1.570796326794896619)", 4.3229841218581e18),
            # The residual cancels more digits than the angle has: precision
            # grows until enough of it survives.
            ("tan(1647533557310758242795542778250)", 3.78133285112983e30),
            ("cos(1647533557310758242795542778250)", -2.64457015388426e-31),
            ("sin(1647533557310758242795542778250)", -1),
            # Integers nearest a multiple of pi or pi/2: the residual is far
            # below the angle's own digits.
            ("sin(2646693125139304345)", 1.18848857958684e-20),
            ("sin(23317502333402457908402376300016)", -7.08381527389479e-32),
            ("sin(41629395862109680461101929914153)", -8.26174040105404e-33),
            ("tan(11658751166701228954201188150008)", -2.82333731565585e31),
            ("cos(11658751166701228954201188150008)", 3.54190763694739e-32),
            # A continued-fraction convergent of pi/2: as close to a multiple
            # of pi/2 as an integer of its size can be.
            ("cos(2085083560376813133713475929088424186954)", -3.5275763910637e-40),
            ("tan(2085083560376813133713475929088424186954)", -2.83480749710557e39),
            ("sin(2085083560376813133713475929088424186954)", 1),
            # Scaled on the exact value: the angle need not fit, the result does.
            ("radians(10**309)", 1.74532925199433e307),
            ("radians(-10**309)", -1.74532925199433e307),
            ("radians(10**310)", 1.74532925199433e308),
            ("degrees(1e-309)", 5.72957795130823e-308),
            # sin(0) and cos(0) are exact, so these sums and products stay exact.
            ("radians(10**309 + sin(0))", 1.74532925199433e307),
            ("degrees(1e-309 * cos(0))", 5.72957795130823e-308),
            # Within its error bound: the angles are uncertain by about 2e-10
            # and 5e-7 radians.
            ("sin(10**6 * pi)", -2.23191218136087e-10),
            ("sin(2**32 + sin(1))", -0.969136722624749),
        ],
    )
    def test_real_results_outside_the_plain_float_path(self, expression: str, expected: float) -> None:
        payload = _result(expression)
        assert payload["result"] == pytest.approx(expected, rel=1e-13, abs=0)
        assert payload["approximate"] is True

    @pytest.mark.parametrize("expression", ["log(1)", "log10(1)", "log(1, 10)"])
    def test_the_log_of_one_is_zero(self, expression: str) -> None:
        assert _result(expression)["result"] == 0

    @pytest.mark.parametrize(
        ("expression", "expected"),
        [
            # A transcendental function of a non-zero rational is irrational;
            # at the trivial points the value is exact and stays exact.
            ("sin(0)", 0),
            ("tan(0)", 0),
            ("atan(0)", 0),
            ("asin(0)", 0),
            ("cos(0)", 1),
            ("exp(0)", 1),
            ("acos(1)", 0),
            ("log(1)", 0),
            ("log10(1000)", 3),
            ("log10(1e-400)", -400),
            ("log10(10**400)", 400),
            ("log(100, 10)", 2),
            ("log(1e-400, 10)", -400),
            ("log(1/8, 2)", -3),
            ("hypot(3, 4)", 5),
            ("hypot(0.5, 1.2)", 1.3),
            ("atan2(0, 5)", 0),
            ("radians(0)", 0),
            ("(-10**400) ** log(1)", 1),
            ("(-1e-400) ** sin(0)", 1),
        ],
    )
    def test_functions_at_their_exact_points_stay_exact(self, expression: str, expected: object) -> None:
        payload = _result(expression)
        assert payload["result"] == expected
        assert "approximate" not in payload

    def test_floating_point_results_lose_binary_noise(self) -> None:
        # 15 significant digits: sqrt(2)**2 is 2.0000000000000004 in binary.
        assert _result("sqrt(2)**2")["result"] == 2


class TestRefusedExpressions:
    # The time bounds are on this process's CPU time: on a shared runner, wall
    # time also counts the time other jobs held the CPU.
    @pytest.mark.parametrize(
        ("expression", "hint"),
        [
            ("", "empty"),
            ("   ", "empty"),
            ("1/0", "Division by zero"),
            ("5 // 0", "Division by zero"),
            ("5 % 0", "Division by zero"),
            ("0 ** -1", "Division by zero"),
            ("1,234", "thousands separators"),
            ("(1, 2)", "thousands separators"),
            ("True + 1", "number"),
            ("None", "number"),
            ("'a'", "number"),
            ("[1, 2]", "Only numbers"),
            ("x + 1", "Only numbers"),
            ("abs.__class__", "Only numbers"),
            ("(lambda: 1)()", "Only numbers"),
            ("[i for i in range(3)]", "Only numbers"),
            ("max(*[1, 2])", "Only numbers"),
            ("round(2.5, ndigits=1)", "Only numbers"),
            ("(y := 2)", "Only numbers"),
            ("1 if 1 else 2", "Only numbers"),
            ("1 < 2", "Only numbers"),
            ("1 and 2", "Only numbers"),
            ("~1", "Unsupported operator"),
            ("1 << 2", "Unsupported operator"),
            ("1 & 2", "Unsupported operator"),
            ("1j", "real number"),
            ("__import__('os')", "Only numbers"),
            ("open('x')", "Only numbers"),
            ("max()", "max()"),
            ("sqrt(1, 2)", "sqrt()"),
            ("round(1, 2, 3)", "round()"),
            ("round(1.5, 0.5)", "round()"),
            ("sqrt(-1)", "sqrt()"),
            ("log(0)", "log()"),
            ("log(-1)", "log()"),
            ("log(10, 1)", "log()"),
            ("asin(2)", "asin()"),
            ("exp(1000)", "too large"),
            ("(-8) ** 0.5", "no real value"),
            ("(-2) ** 1.5", "no real value"),
            ("(-16) ** (1/4)", "no real value"),
            ("(-2) ** sqrt(2)", "no real value"),
            # Below the smallest double: an approximate 0 would be a wrong answer.
            ("sin(1e-400)", "too small"),
            ("exp(-800)", "too small"),
            ("hypot(1e-400, 1e-400)", "too small"),
            ("1e-400 * pi", "too small"),
            ("sin(1) * 1e-310", "too small"),
            ("0.5 ** 2000.5", "too small"),
            ("radians(1e-307)", "too small"),
            ("log(1 + 1e-400)", "too small"),
            ("log(10, 1 + 1e-400)", "too large"),
            ("0 * pi + 1e-400", "too small"),
            ("pi / 1e-400", "too large"),
            ("pi / 10**400", "too small"),
            ("1e-400 % pi", "too small"),
            ("(-2) ** 1e-400", "no real value"),
            ("(-pi) ** 1e-400", "no real value"),
            ("(-8) ** (2 + 1e-400)", "no real value"),
            ("(-8) ** (1 + 1e-20)", "no real value"),
            ("(-pi) ** (2 + 1e-400)", "no real value"),
            ("0 ** (-1e-400)", "Division by zero"),
            ("sin(0) ** (-1e-400)", "Division by zero"),
            ("(-10**400) ** (-log(e))", "too small"),
            ("(-1e-400) ** (-log(e))", "too large"),
            ("(-10**400) ** floor(exp(10))", "too large"),
            ("(2e-400) ** -floor(exp(10))", "too large"),
            ("(2e-400) ** floor(exp(10))", "too small"),
            ("(2*10**400) ** exp(10)", "too large"),
            ("radians(10**311)", "too large"),
            # An approximate angle this large cannot tell whole radians apart.
            # An approximate angle whose uncertainty (one ulp) reaches a radian.
            ("sin(10**22 + sin(1))", "meaningful"),
            ("sin(10**22 * pi)", "meaningful"),
            ("cos((10**22 + 1/2) * pi)", "meaningful"),
            ("tan(2**53 * pi)", "meaningful"),
            ("sin(2**52 + sin(1))", "meaningful"),
            ("sin(2**51 + sin(1))", "meaningful"),
            ("sin(10**15 + sin(1))", "meaningful"),
            ("sin(2**34 + sin(1))", "meaningful"),
            # tan amplifies the angle's error by 1 + tan**2: near a pole the
            # neighbouring doubles straddle it.
            ("tan((10**6 + 1/2) * pi)", "meaningful"),
            ("tan((2**32 + 1/2) * pi)", "meaningful"),
            ("tan(pi / 2)", "meaningful"),
            ("sin(10**400 * pi)", "meaningful"),
            ("cos(exp(1000))", "meaningful"),
            ("atan2(1e-400, 1)", "too small"),
            ("asin(1e-400)", "too small"),
            ("(2e-400) ** 2.5", "too small"),
            ("log(1, 1)", "division by zero"),
            ("acos(1 + 1e-20)", "domain"),
            ("asin(-1 - 1e-20)", "domain"),
            ("١٢ + 1", "Not a valid arithmetic expression"),
            ("2 +", "Not a valid arithmetic expression"),
            ("15% * 200", "Not a valid arithmetic expression"),
            ("0." + "1" * 5000, "too large"),
        ],
    )
    def test_refused_with_a_message_the_model_can_act_on(self, expression: str, hint: str) -> None:
        with pytest.raises(CalculationError, match=hint):
            evaluate(expression)

    def test_overlong_expression_is_refused(self) -> None:
        with pytest.raises(CalculationError, match=str(MAX_EXPRESSION_CHARS)):
            evaluate("1+" * MAX_EXPRESSION_CHARS + "1")

    @pytest.mark.parametrize(
        "expression",
        [
            "9**9**9", "2**10**6", "10**1001", "(10**300)*(10**300)", "1/10**1001",
            "sqrt(2*10**800)", "2 ** 0.5 ** 10000",
        ],
    )
    def test_huge_results_are_refused_before_they_are_computed(self, expression: str) -> None:
        started = time.process_time()
        with pytest.raises(CalculationError, match="too large"):
            evaluate(expression)
        assert time.process_time() - started < 1.0

    @pytest.mark.parametrize("expression", ["(" * 250 + "1" + ")" * 250, "~" * 3000 + "1"])
    def test_deep_nesting_is_refused_without_recursion_error(self, expression: str) -> None:
        with pytest.raises(CalculationError):
            evaluate(expression)

    def test_a_deep_chain_of_signs_evaluates_or_is_refused_cleanly(self) -> None:
        try:
            payload = _result("-" * 3000 + "1")
        except CalculationError:
            return
        assert payload["result"] == 1

    def test_the_closest_large_angle_to_a_quarter_turn_stays_fast(self) -> None:
        # A 494-digit convergent of pi/2: its reduction cancels about 494
        # more digits. Its sine (1.3e-495) is below a double's range.
        convergent = "17839370513524201499748668616592542108626995409288572013029872004471324123607627527454223464272772054176082126556885213677277954876867504992212681236462708790448353242926198280850747392707199373021378606222557604413387879486933906869398442149234697972962966389238328329808193081488550161312884326085994415166742276475551449836263931234426093372624990964550490295016812656093335087914326890380938534417693168481758783330959477883741143485211273518438907375097830567310289121244081038348220434485"
        started = time.process_time()
        assert _result(f"cos({convergent})")["result"] == 1
        with pytest.raises(CalculationError, match="too small"):
            evaluate(f"sin({convergent})")
        assert time.process_time() - started < 1.0

    def test_worst_case_operands_stay_fast(self) -> None:
        # Every intermediate sits just under the digit cap: the cap bounds
        # the CPU a single call can spend on the event loop.
        terms = [f"(10**499 - {k})/(10**498 + {k})" for k in range(1, 120)]
        started = time.process_time()
        with contextlib.suppress(CalculationError):
            evaluate(" + ".join(terms))
        assert time.process_time() - started < 1.0

    def test_a_long_sum_of_line_items_is_accepted(self) -> None:
        # A left-deep chain of 900 additions: deeper than Python's recursion
        # limit if evaluated recursively.
        expression = " + ".join(["1.25"] * 900)
        assert len(expression) < MAX_EXPRESSION_CHARS
        assert _result(expression)["result"] == 1125

    def test_too_many_terms_are_refused(self) -> None:
        with pytest.raises(CalculationError, match="too many"):
            evaluate("+".join(["1"] * 3000))


class TestNoUnexpectedErrors:
    """Every expression either computes or is refused with a CalculationError
    the tool turns into JSON: never another exception, never NaN or inf."""

    _VALUES = (
        "0", "1", "-1", "2", "0.5", "1e-400", "-1e-400", "2e-400", "1e-310", "1e-300", "1e300",
        "10**400", "-10**400", "1 + 1e-20", "1 - 1e-20", "1/3", "pi", "e", "sqrt(2)", "7**-300",
        "(10**499 - 1)/(10**498 + 7)", 'hms("1:02:03.5")', "-pi", "-8", "1/7", "2 + 1e-400",
        "floor(exp(10))", "-floor(exp(10))",
    )
    _UNARY = (
        "abs", "floor", "ceil", "round", "sqrt", "exp", "log", "log10", "sin", "cos", "tan",
        "asin", "acos", "atan", "radians", "degrees", "-", "+", "minutes", "seconds",
    )
    _BINARY = ("+", "-", "*", "/", "//", "%", "**", "atan2", "hypot", "log", "min", "max", "round")

    @staticmethod
    def _expressions(count: int) -> list[str]:
        rng = random.Random(4125)
        cls = TestNoUnexpectedErrors
        expressions = []
        for _ in range(count):
            a, b = rng.choice(cls._VALUES), rng.choice(cls._VALUES)
            if rng.random() < 0.5:
                expressions.append(f"{rng.choice(cls._UNARY)}({a})")
            else:
                op = rng.choice(cls._BINARY)
                expressions.append(f"{op}({a}, {b})" if op[0].isalpha() else f"({a}) {op} ({b})")
        return expressions

    def test_random_combinations_compute_or_refuse_cleanly(self) -> None:
        for expression in self._expressions(6000):
            try:
                payload = evaluate(expression).to_dict()
            except CalculationError:
                continue
            json.dumps(payload)
            result = payload["result"]
            if isinstance(result, float):
                assert math.isfinite(result), expression


class TestDurations:
    def test_minutes_subtracted_from_a_clock_time(self) -> None:
        payload = _result('hms("2:00:35") - minutes(38)')
        assert payload == {"result": 4955, "unit": "seconds", "hms": "1:22:35"}

    @pytest.mark.parametrize(
        ("expression", "seconds", "hms"),
        [
            ('hms("38:10")', 2290, "0:38:10"),
            ('hms("75:30")', 4530, "1:15:30"),
            ('hms("1:02:03.5")', 3723.5, "1:02:03.5"),
            ('hms("1:00:00") * 2', 7200, "2:00:00"),
            ('-hms("0:30")', -30, "-0:00:30"),
            ('round(hms("1:00:00.6"))', 3601, "1:00:01"),
            ('max(hms("1:00"), minutes(2))', 120, "0:02:00"),
            ("days(1) + hours(2)", 93600, "26:00:00"),
            ("seconds(90)", 90, "0:01:30"),
        ],
    )
    def test_duration_results_carry_seconds_and_hms(self, expression: str, seconds: object, hms: str) -> None:
        payload = _result(expression)
        assert payload["result"] == seconds
        assert payload["unit"] == "seconds"
        assert payload["hms"] == hms

    def test_a_fraction_of_a_second_stays_exact(self) -> None:
        assert _result('hms("0:00:01") / 3') == {
            "result": 0.333333333333333, "exact": "1/3", "unit": "seconds", "hms": "0:00:00.333",
        }

    def test_a_ratio_of_durations_is_a_plain_number(self) -> None:
        payload = _result('hms("1:00:00") / minutes(1)')
        assert payload == {"result": 60}

    def test_a_rate_is_per_second(self) -> None:
        assert _result('100 / hms("0:01:40")') == {"result": 1, "unit": "per second"}

    @pytest.mark.parametrize(
        ("expression", "hint"),
        [
            ('hms("2:00") + 5', "duration and a plain number"),
            ('max(hms("1:00"), 5)', "duration and a plain number"),
            ('hms("1:00:00") * hms("1:00:00")', "not a usable unit"),
            ('hms("1:00") ** 2', "duration"),
            ('sqrt(hms("1:00"))', "duration"),
            ('minutes(hms("1:00"))', "duration"),
            ('hms("1:75:00")', "minutes must be under 60"),
            ('hms("1:00:60")', "seconds must be under 60"),
            ('hms("abc")', "h:mm:ss"),
            ('hms("-1:00")', "h:mm:ss"),
            ('hms("' + "1" * 5000 + ':00:00")', "h:mm:ss"),
            ('hms("1:00:00.' + "1" * 5000 + '")', "h:mm:ss"),
            ('hms("\u0661:00")', "h:mm:ss"),
            ("hms(5)", "quoted time"),
            ('hms("1:00", "2:00")', "quoted time"),
        ],
    )
    def test_unit_mistakes_are_refused(self, expression: str, hint: str) -> None:
        with pytest.raises(CalculationError, match=hint):
            evaluate(expression)


class TestDateDifference:
    def test_a_gap_in_completed_months_and_days(self) -> None:
        gap = date_difference("2024-01-15", "2024-08-11").to_dict()
        assert gap == {
            "start": "2024-01-15",
            "end": "2024-08-11",
            "end_before_start": False,
            "total_days": 209,
            "weeks": 29,
            "remaining_days": 6,
            "years": 0,
            "months": 6,
            "days": 27,
            "calendar_years": 0,
        }

    def test_same_day(self) -> None:
        gap = date_difference("2024-05-05", "2024-05-05")
        assert (gap.total_days, gap.years, gap.months, gap.days) == (0, 0, 0, 0)

    def test_reversed_dates_are_signed_and_flagged(self) -> None:
        gap = date_difference("2024-08-11", "2024-01-15")
        assert gap.end_before_start is True
        assert gap.total_days == -209
        assert gap.calendar_years == 0
        # The span itself reads the same either way round.
        assert (gap.months, gap.days, gap.weeks, gap.remaining_days) == (6, 27, 29, 6)

    @pytest.mark.parametrize(
        ("start", "end", "years", "months", "days"),
        [
            # An anniversary that does not exist falls on the month's last day.
            ("2020-02-29", "2021-02-28", 1, 0, 0),
            ("2020-02-29", "2021-03-01", 1, 0, 1),
            ("2020-02-29", "2024-02-29", 4, 0, 0),
            ("2020-02-29", "2021-02-27", 0, 11, 29),
            ("2023-01-31", "2023-02-28", 0, 1, 0),
            ("2023-01-31", "2023-03-01", 0, 1, 1),
            ("2023-01-30", "2023-02-28", 0, 1, 0),
            ("2023-12-31", "2024-01-01", 0, 0, 1),
        ],
    )
    def test_month_ends_and_leap_days(self, start: str, end: str, years: int, months: int, days: int) -> None:
        gap = date_difference(start, end)
        assert (gap.years, gap.months, gap.days) == (years, months, days)

    def test_calendar_years_differ_before_the_anniversary(self) -> None:
        gap = date_difference("1776-07-04", "2024-01-01")
        assert (gap.years, gap.calendar_years) == (247, 248)

    def test_the_whole_calendar_range(self) -> None:
        gap = date_difference("0001-01-01", "9999-12-31")
        assert gap.years == 9998 and gap.months == 11 and gap.days == 30

    @pytest.mark.parametrize(
        "value",
        ["2023-02-29", "2024/01/01", "20240101", "2024-W10-1", "2024-01-01T10:00:00", "", "2024-1-5", None, 20240101],
    )
    def test_anything_but_a_calendar_date_is_refused(self, value: object) -> None:
        with pytest.raises(CalculationError, match="YYYY-MM-DD"):
            date_difference(value, "2024-01-01")  # type: ignore[arg-type]


class TestCountText:
    def test_letters_characters_and_words(self) -> None:
        assert count_text("hello world").to_dict() == {
            "letters": 10,
            "characters": 11,
            "characters_excluding_spaces": 10,
            "words": 2,
        }

    def test_an_accent_typed_as_a_combining_mark_counts_once(self) -> None:
        counts = count_text("Brontë")
        assert (counts.letters, counts.characters) == (6, 6)

    def test_emoji_sequences_are_one_character_each(self) -> None:
        counts = count_text("\U0001f44d\U0001f3fd \U0001f468‍\U0001f469‍\U0001f467 \U0001f1e9\U0001f1ea")
        assert counts.characters == 5
        assert counts.letters == 0
        assert counts.words == 0

    def test_letters_in_other_scripts(self) -> None:
        assert count_text("हिन्दी").letters == 3

    @pytest.mark.parametrize(
        ("text", "words"),
        [("well-known don't  e-mail", 3), ("Hello — world", 2), ("", 0), ("one\ntwo\tthree", 3)],
    )
    def test_words(self, text: str, words: int) -> None:
        assert count_text(text).words == words

    @pytest.mark.parametrize(
        ("text", "letter", "occurrences"),
        [
            ("Hello World", "l", 3),
            ("Hello World", "L", 3),
            ("résumé", "e", 0),
            ("résumé", "é", 2),
            ("résumé", "é", 2),
            ("Straße", "s", 1),
        ],
    )
    def test_one_letter_ignores_case_but_not_accents(self, text: str, letter: str, occurrences: int) -> None:
        counts = count_text(text, letter)
        assert counts.letter_occurrences == occurrences

    @pytest.mark.parametrize("letter", ["", "  ", None])
    def test_no_letter_means_no_letter_count(self, letter: str | None) -> None:
        assert "letter_occurrences" not in count_text("abc", letter).to_dict()

    @pytest.mark.parametrize("letter", ["ab", "1", "!"])
    def test_letter_must_be_one_letter(self, letter: str) -> None:
        with pytest.raises(CalculationError, match="single letter"):
            count_text("abc", letter)

    def test_overlong_text_is_refused(self) -> None:
        with pytest.raises(CalculationError, match=str(MAX_TEXT_CHARS)):
            count_text("a" * (MAX_TEXT_CHARS + 1))

    def test_text_must_be_a_string(self) -> None:
        with pytest.raises(CalculationError, match="string"):
            count_text(None)  # type: ignore[arg-type]
