import logging

from app.agents.tools.decorator import tool
from app.agents.tools.enums import ParameterType
from app.agents.tools.models import ToolParameter

logger = logging.getLogger(__name__)


class Calculator:
    """Calculator tool exposed to the agents"""
    def __init__(self) -> None:
        """Initialize the Calculator tool
        Args:
            None
        Returns:
            None
        """
        logger.info("🚀 Initializing Calculator tool")

    def get_supported_operations(self) -> list[str]:
        """Get the supported operations
        Args:
            None
        Returns:
            A list of supported operations
        """
        return ["add", "subtract", "multiply", "divide", "power", "square root", "cube root"]

    @tool(
        app_name="calculator",
        tool_name="calculate_single_operand",
        parameters=[
            ToolParameter(
                name="a",
                type=ParameterType.NUMBER,
                description="The first number",
                required=True
                ),
            ToolParameter(
                name="operation",
                type=ParameterType.STRING,
                description="Mathematical operation: 'sqrt' (square root), 'cbrt' (cube root)",
                required=True
                )
            ]
        )
    def calculate_single_operand(self, a: float, operation: str) -> float:
        """Calculate the result of a mathematical operation
        Args:
            a: The first number
            operation: The operation to use
        Returns:
            The result of the mathematical operation
        """

        if operation in ("square root", "square root of", "sqrt"):
            return self._square_root(a)
        elif operation in ("cube root", "cube root of", "cbrt"):
            return self._cube_root(a)
        else:
            raise ValueError(f"Invalid operation: {operation}")

    @tool(
        app_name="calculator",
        tool_name="calculate_two_operands",
        parameters=[
            ToolParameter(
                name="a",
                type=ParameterType.NUMBER,
                description="The first number",
                required=True
                ),
            ToolParameter(
                name="b",
                type=ParameterType.NUMBER,
                description="The second number",
                required=True
                ),
            ToolParameter(
                name="operation",
                type=ParameterType.STRING,
                description="Mathematical operation: 'add', 'subtract', 'multiply', 'divide', 'power'",
                required=True
                )
            ]
        )
    def calculate_two_operands(self, a: float, b: float, operation: str) -> float:
        """Calculate the result of a mathematical operation
        Args:
            a: The first number
            b: The second number
            operator: The operator to use
        Returns:
            The result of the mathematical operation
        """

        if operation in ("add", "addition", "plus", "sum", "+"):
            return self._add(a, b)
        elif operation in ("subtract", "subtraction", "minus", "difference", "-"):
            return self._subtract(a, b)
        elif operation in ("multiply", "multiplication", "times", "product", "*"):
            return self._multiply(a, b)
        elif operation in ("divide", "division", "over", "quotient", "/"):
            return self._divide(a, b)
        elif operation in ("power", "exponent", "raised to the power of", "^"):
            return self._power(a, b)
        else:
            raise ValueError(f"Invalid operation: {operation}")

    def _add(self, a: float, b: float) -> float:
        """Add two numbers
        Args:
            a: The first number
            b: The second number
        Returns:
            The result of the addition
        """
        return a + b

    def _subtract(self, a: float, b: float) -> float:
        """Subtract two numbers
        Args:
            a: The first number
            b: The second number
        Returns:
            The result of the subtraction
        """
        return a - b

    def _multiply(self, a: float, b: float) -> float:
        """Multiply two numbers
        Args:
            a: The first number
            b: The second number
        Returns:
            The result of the multiplication
        """
        return a * b

    def _divide(self, a: float, b: float) -> float:
        """Divide two numbers
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
        """Raise a number to the power of another number
        Args:
            a: The base number
            b: The exponent
        Returns:
            The result of the power operation
        """
        return a ** b

    def _square_root(self, a: float) -> float:
        """Calculate the square root of a number
        Args:
            a: The number to calculate the square root of
        Returns:
            The result of the square root operation
        """
        if a < 0:
            raise ValueError("Cannot calculate the square root of a negative number")
        return a ** 0.5

    def _cube_root(self, a: float) -> float:
        """Calculate the cube root of a number
        Args:
            a: The number to calculate the cube root of
        Returns:
            The result of the cube root operation
        """
        if a < 0:
            return -(-a) ** (1/3)
        return a ** (1/3)
