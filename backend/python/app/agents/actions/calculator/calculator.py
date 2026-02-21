import logging

from pydantic import BaseModel, Field

from app.agents.tools.config import ToolCategory
from app.agents.tools.decorator import tool
from app.agents.tools.models import ToolIntent
from app.connectors.core.registry.auth_builder import AuthBuilder
from app.connectors.core.registry.tool_builder import (
    ToolsetBuilder,
    ToolsetCategory,
)

logger = logging.getLogger(__name__)

class CalculatorSingleOperandInput(BaseModel):
    a: float = Field(description="The first number")
    operation: str = Field(description="Mathematical operation: 'sqrt' (square root), 'cbrt' (cube root)")

class CalculatorTwoOperandsInput(BaseModel):
    a: float = Field(description="The first number")
    b: float = Field(description="The second number")
    operation: str = Field(description="Mathematical operation: 'add', 'subtract', 'multiply', 'divide', 'power'")

# Register Calculator toolset (internal - always available, no auth required, backend-only)
@ToolsetBuilder("Calculator")\
    .in_group("Internal Tools")\
    .with_description("Mathematical calculator tool - always available, no authentication required")\
    .with_category(ToolsetCategory.UTILITY)\
    .with_auth([
        AuthBuilder.type("NONE").fields([])
    ])\
    .as_internal()\
    .configure(lambda builder: builder.with_icon("/assets/icons/toolsets/calculator.svg"))\
    .build_decorator()
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
        args_schema=CalculatorSingleOperandInput,
        llm_description="Calculate the result of a mathematical operation with a single operand (square root, cube root)",
        category=ToolCategory.UTILITY,
        is_essential=True,
        requires_auth=False,
        when_to_use=[
            "User wants to calculate the square root of a number",
            "User wants to calculate the cube root of a number",
        ],
        when_not_to_use=[
            "User wants to calculate the result of a mathematical operation with two operands",
        ],
        primary_intent=ToolIntent.QUESTION,
        typical_queries=[
            "Calculate the square root of 16",
            "Calculate the cube root of 27",
            "Calculate the result of 16 + 27",
            "Calculate the result of 16 - 27",
            "Calculate the result of 16 * 27",
            "Calculate the result of 16 / 27",
            "Calculate the result of 16 ^ 27",
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
        args_schema=CalculatorTwoOperandsInput,
        llm_description="Calculate the result of a mathematical operation with two operands (add, subtract, multiply, divide, power)",
        category=ToolCategory.UTILITY,
        is_essential=True,
        requires_auth=False,
        when_to_use=[
            "User wants to calculate the result of a mathematical operation with two operands",
        ],
        when_not_to_use=[
            "User wants to calculate the result of a mathematical operation with a single operand",
        ],
        primary_intent=ToolIntent.QUESTION,
        typical_queries=[
            "Calculate the result of 16 + 27",
            "Calculate the result of 16 - 27",
            "Calculate the result of 16 * 27",
            "Calculate the result of 16 / 27",
            "Calculate the result of 16 ^ 27",
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
