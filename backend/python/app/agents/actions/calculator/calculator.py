import logging

from app.agents.tool.decorator import tool

logger = logging.getLogger(__name__)


class Calculator:
    """Calculator tool exposed to the agents"""
    def __init__(self) -> None:
        """Initialize the Calculator tool"""
        """
        Args:
            None
        Returns:
            None
        """
        logger.info("🚀 Initializing Calculator tool")

    def get_supported_operations(self) -> list[str]:
        """Get the supported operations"""
        """
        Args:
            None
        Returns:
            A list of supported operations
            """
        return ["add", "subtract", "multiply", "divide", "power", "square root", "cube root"]

    @tool(app_name="calculator", tool_name="calculate_single_operand")
    def calculate_single_operand(self, a: float, operation: str) -> float:
        """Calculate the result of a mathematical operation"""
        """
        Args:
            a: The first number
            operation: The operation to use
        Returns:
            The result of the mathematical operation
        """
        if operation == "square root" or operation == "square root of" or operation == "sqrt":
            return self._square_root(a)
        elif operation == "cube root" or operation == "cube root of" or operation == "cbrt":
            return self._cube_root(a)
        else:
            raise ValueError(f"Invalid operation: {operation}")

    @tool(app_name="calculator", tool_name="calculate_two_operands")
    def calculate_two_operands(self, a: float, b: float, operation: str) -> float:
        """Calculate the result of a mathematical operation"""
        """
        Args:
            a: The first number
            b: The second number
            operator: The operator to use
        Returns:
            The result of the mathematical operation
        """
        if operation == "add" or operation == "addition" or operation == "plus" or operation == "sum" or operation == "+":
            return self._add(a, b)
        elif operation == "subtract" or operation == "subtraction" or operation == "minus" or operation == "difference" or operation == "-":
            return self._subtract(a, b)
        elif operation == "multiply" or operation == "multiplication" or operation == "times" or operation == "product" or operation == "*":
            return self._multiply(a, b)
        elif operation == "divide" or operation == "division" or operation == "over" or operation == "quotient" or operation == "/":
            return self._divide(a, b)
        elif operation == "power" or operation == "exponent" or operation == "raised to the power of" or operation == "raised to the power of" or operation == "^":
            return self._power(a, b)
        else:
            raise ValueError(f"Invalid operation: {operation}")

    def _add(self, a: float, b: float) -> float:
        """Add two numbers"""
        """
        Args:
            a: The first number
            b: The second number
        Returns:
            The result of the addition
        """
        return a + b

    def _subtract(self, a: float, b: float) -> float:
        """Subtract two numbers"""
        """
        Args:
            a: The first number
            b: The second number
        Returns:
            The result of the subtraction
        """
        return a - b

    def _multiply(self, a: float, b: float) -> float:
        """Multiply two numbers"""
        """
        Args:
            a: The first number
            b: The second number
        Returns:
            The result of the multiplication
        """
        return a * b

    def _divide(self, a: float, b: float) -> float:
        """Divide two numbers"""
        """
        Args:
            a: The first number
            b: The second number
        Returns:
            The result of the division
        """
        if b == 0:
            raise ValueError("Cannot divide by zero")
        return a / b

    def _power(self, a: float, b: float) -> float:
        """Raise a number to the power of another number"""
        """
        Args:
            a: The base number
            b: The exponent
        Returns:
            The result of the power operation
        """
        return a ** b

    def _square_root(self, a: float) -> float:
        """Calculate the square root of a number"""
        """
        Args:
            a: The number to calculate the square root of
        Returns:
            The result of the square root operation
        """
        return a ** 0.5

    def _cube_root(self, a: float) -> float:
        """Calculate the cube root of a number"""
        """
        Args:
            a: The number to calculate the cube root of
        Returns:
            The result of the cube root operation
        """
        return a ** (1/3)
