"""
Unit tests for app.agents.actions.calculator.calculator

Tests the Calculator tool for basic arithmetic operations.
"""

import pytest

from app.agents.actions.calculator.calculator import (
    Calculator,
    CalculatorSingleOperandInput,
    CalculatorTwoOperandsInput,
)


# ============================================================================
# CalculatorSingleOperandInput
# ============================================================================

class TestCalculatorSingleOperandInput:
    def test_valid_input(self):
        inp = CalculatorSingleOperandInput(a=16.0, operation="sqrt")
        assert inp.a == 16.0
        assert inp.operation == "sqrt"


# ============================================================================
# CalculatorTwoOperandsInput
# ============================================================================

class TestCalculatorTwoOperandsInput:
    def test_valid_input(self):
        inp = CalculatorTwoOperandsInput(a=10.0, b=5.0, operation="add")
        assert inp.a == 10.0
        assert inp.b == 5.0
        assert inp.operation == "add"


# ============================================================================
# Calculator.get_supported_operations
# ============================================================================

class TestGetSupportedOperations:
    def test_returns_list_of_operations(self):
        calc = Calculator()
        ops = calc.get_supported_operations()
        assert isinstance(ops, list)
        assert len(ops) > 0
        assert "add" in ops
        assert "multiply" in ops
        assert "square root" in ops


# ============================================================================
# Calculator.calculate_two_operands
# ============================================================================

class TestCalculateTwoOperands:
    def test_add_operation(self):
        calc = Calculator()
        result = calc.calculate_two_operands(10.0, 5.0, "add")
        assert result == 15.0

    def test_addition_alias(self):
        calc = Calculator()
        result = calc.calculate_two_operands(10.0, 5.0, "addition")
        assert result == 15.0

    def test_plus_alias(self):
        calc = Calculator()
        result = calc.calculate_two_operands(10.0, 5.0, "plus")
        assert result == 15.0

    def test_subtract_operation(self):
        calc = Calculator()
        result = calc.calculate_two_operands(10.0, 5.0, "subtract")
        assert result == 5.0

    def test_subtraction_alias(self):
        calc = Calculator()
        result = calc.calculate_two_operands(10.0, 5.0, "subtraction")
        assert result == 5.0

    def test_multiply_operation(self):
        calc = Calculator()
        result = calc.calculate_two_operands(10.0, 5.0, "multiply")
        assert result == 50.0

    def test_multiplication_alias(self):
        calc = Calculator()
        result = calc.calculate_two_operands(10.0, 5.0, "multiplication")
        assert result == 50.0

    def test_divide_operation(self):
        calc = Calculator()
        result = calc.calculate_two_operands(10.0, 5.0, "divide")
        assert result == 2.0

    def test_division_alias(self):
        calc = Calculator()
        result = calc.calculate_two_operands(10.0, 5.0, "division")
        assert result == 2.0

    def test_divide_by_zero_raises_error(self):
        calc = Calculator()
        with pytest.raises(ValueError) as exc_info:
            calc.calculate_two_operands(10.0, 0.0, "divide")
        assert "Cannot divide by zero" in str(exc_info.value)

    def test_power_operation(self):
        calc = Calculator()
        result = calc.calculate_two_operands(2.0, 3.0, "power")
        assert result == 8.0

    def test_exponent_alias(self):
        calc = Calculator()
        result = calc.calculate_two_operands(2.0, 3.0, "exponent")
        assert result == 8.0

    def test_unsupported_operation_raises_error(self):
        calc = Calculator()
        with pytest.raises(ValueError) as exc_info:
            calc.calculate_two_operands(10.0, 5.0, "modulo")
        assert "Invalid operation" in str(exc_info.value)

    def test_negative_numbers(self):
        calc = Calculator()
        result = calc.calculate_two_operands(-10.0, 5.0, "add")
        assert result == -5.0

    def test_decimal_numbers(self):
        calc = Calculator()
        result = calc.calculate_two_operands(10.5, 2.5, "add")
        assert result == 13.0


# ============================================================================
# Calculator.calculate_single_operand
# ============================================================================

class TestCalculateSingleOperand:
    def test_sqrt_operation(self):
        calc = Calculator()
        result = calc.calculate_single_operand(16.0, "sqrt")
        assert result == 4.0

    def test_square_root_alias(self):
        calc = Calculator()
        result = calc.calculate_single_operand(25.0, "square root")
        assert result == 5.0

    def test_square_root_of_alias(self):
        calc = Calculator()
        result = calc.calculate_single_operand(9.0, "square root of")
        assert result == 3.0

    def test_sqrt_negative_raises_error(self):
        calc = Calculator()
        with pytest.raises(ValueError) as exc_info:
            calc.calculate_single_operand(-16.0, "sqrt")
        assert "Cannot calculate the square root of a negative number" in str(exc_info.value)

    def test_cbrt_operation(self):
        calc = Calculator()
        result = calc.calculate_single_operand(27.0, "cbrt")
        assert abs(result - 3.0) < 0.001

    def test_cube_root_alias(self):
        calc = Calculator()
        result = calc.calculate_single_operand(8.0, "cube root")
        assert abs(result - 2.0) < 0.001

    def test_cube_root_of_alias(self):
        calc = Calculator()
        result = calc.calculate_single_operand(64.0, "cube root of")
        assert abs(result - 4.0) < 0.001

    def test_cbrt_negative_number(self):
        calc = Calculator()
        result = calc.calculate_single_operand(-27.0, "cbrt")
        assert abs(result - (-3.0)) < 0.001

    def test_unsupported_single_operand_raises_error(self):
        calc = Calculator()
        with pytest.raises(ValueError) as exc_info:
            calc.calculate_single_operand(16.0, "log")
        assert "Invalid operation" in str(exc_info.value)

    def test_sqrt_of_zero(self):
        calc = Calculator()
        result = calc.calculate_single_operand(0.0, "sqrt")
        assert result == 0.0

    def test_sqrt_decimal(self):
        calc = Calculator()
        result = calc.calculate_single_operand(2.25, "sqrt")
        assert result == 1.5


# ============================================================================
# Internal methods
# ============================================================================

class TestInternalMethods:
    def test_add_internal(self):
        calc = Calculator()
        assert calc._add(3.0, 2.0) == 5.0

    def test_subtract_internal(self):
        calc = Calculator()
        assert calc._subtract(10.0, 3.0) == 7.0

    def test_multiply_internal(self):
        calc = Calculator()
        assert calc._multiply(4.0, 5.0) == 20.0

    def test_divide_internal(self):
        calc = Calculator()
        assert calc._divide(20.0, 4.0) == 5.0

    def test_divide_internal_zero_raises(self):
        calc = Calculator()
        with pytest.raises(ValueError):
            calc._divide(10.0, 0.0)

    def test_power_internal(self):
        calc = Calculator()
        assert calc._power(2.0, 10.0) == 1024.0

    def test_square_root_internal(self):
        calc = Calculator()
        assert calc._square_root(100.0) == 10.0

    def test_square_root_internal_negative_raises(self):
        calc = Calculator()
        with pytest.raises(ValueError):
            calc._square_root(-1.0)

    def test_cube_root_internal_positive(self):
        calc = Calculator()
        assert abs(calc._cube_root(125.0) - 5.0) < 0.001

    def test_cube_root_internal_negative(self):
        calc = Calculator()
        result = calc._cube_root(-8.0)
        assert abs(result - (-2.0)) < 0.001
