import json
import logging

from pydantic import BaseModel, Field

from app.agent_loop_lib.tools.base import ParameterType, Tag, ToolParameter
from app.agent_loop_lib.tools.decorators import tool
from app.agents.actions.calculator.exact import (
    MAX_TEXT_CHARS,
    ExpressionError,
    count_text,
    date_difference,
    evaluate,
)
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

# Register Calculator toolset (internal, no auth, backend-only). Essential, so
# a lazily disclosed agent sees it from the first turn instead of doing
# arithmetic in its head because the tool was one discovery call away.
@ToolsetBuilder("Calculator")\
    .in_group("Internal Tools")\
    .with_description("Mathematical calculator tool - always available, no authentication required")\
    .with_category(ToolsetCategory.UTILITY)\
    .with_auth([
        AuthBuilder.type("NONE").fields([])
    ])\
    .as_internal()\
    .as_essential()\
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
        logger.debug("🚀 Initializing Calculator tool")

    def get_supported_operations(self) -> list[str]:
        """Get the supported operations
        Args:
            None
        Returns:
            A list of supported operations
        """
        return ["add", "subtract", "multiply", "divide", "power", "square root", "cube root"]

    @tool(
        path="/tools/calculator/calculate_single_operand",
        short_description="Single-operand math (square root, cube root)",
        description="Calculate the result of a mathematical operation with a single operand (square root, cube root).",
        parameters=[
            ToolParameter(name="a", type=ParameterType.FLOAT, description="The number to operate on", required=True),
            ToolParameter(name="operation", type=ParameterType.STRING, description="Mathematical operation: 'sqrt' (square root), 'cbrt' (cube root)", required=True),
        ],
        tags=[Tag(key="category", value="utility"), Tag(key="type", value="utility")],
    )
    async def calculate_single_operand(self, a: float, operation: str) -> float:
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
        path="/tools/calculator/calculate_two_operands",
        short_description="Two-operand math (add, subtract, multiply, divide, power)",
        description="Calculate the result of a mathematical operation with two operands (add, subtract, multiply, divide, power).",
        parameters=[
            ToolParameter(name="a", type=ParameterType.FLOAT, description="The first number", required=True),
            ToolParameter(name="b", type=ParameterType.FLOAT, description="The second number", required=True),
            ToolParameter(name="operation", type=ParameterType.STRING, description="Mathematical operation: 'add', 'subtract', 'multiply', 'divide', 'power'", required=True),
        ],
        tags=[Tag(key="category", value="utility"), Tag(key="type", value="utility")],
    )
    async def calculate_two_operands(self, a: float, b: float, operation: str) -> float:
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

    @tool(
        path="/tools/calculator/evaluate_expression",
        short_description="Evaluate an arithmetic expression exactly",
        description=(
            "Evaluate an arithmetic expression, e.g. '(90 - 16) * 1954'. Supports "
            "numbers, pi, e, + - * / // % ** and parentheses; abs, round, min, max, "
            "sqrt, floor, ceil, exp, log, log10; and sin, cos, tan, asin, acos, atan, "
            "atan2, radians, degrees (angles in radians). Use it for any calculation "
            "with more than one step, copying the numbers exactly from their source. "
            "Durations: hms(\"h:mm:ss\") or hms(\"mm:ss\"), seconds(n), minutes(n), "
            "hours(n), days(n) all return seconds, e.g. hms(\"2:00:35\") - minutes(38); "
            "the result then carries unit and h:mm:ss. Mixing a duration with a bare "
            "number in + or - is refused. Divide by minutes(1) etc. to get another unit."
        ),
        parameters=[
            ToolParameter(
                name="expression", type=ParameterType.STRING,
                description="The arithmetic expression to evaluate", required=True,
            ),
        ],
        tags=[Tag(key="category", value="utility"), Tag(key="type", value="utility")],
    )
    async def evaluate_expression(self, expression: str) -> str:
        try:
            return json.dumps({"expression": expression, **evaluate(expression).to_dict()})
        except ExpressionError as exc:
            return json.dumps({"expression": expression, "error": str(exc)})

    @tool(
        path="/tools/calculator/count_text",
        short_description="Count letters, characters and words in a text",
        description=(
            "Count the letters, characters and words in a short text (up to "
            f"{MAX_TEXT_CHARS} characters), and optionally how often one letter occurs. "
            "Letters are alphabetic characters in any script, accents included "
            "('Brontë' has 6); spaces, digits and punctuation are not letters. "
            "Words are whitespace-separated tokens containing a letter or digit. "
            "The single-letter count ignores case but not accents. Use it instead "
            "of counting by hand."
        ),
        parameters=[
            ToolParameter(
                name="text", type=ParameterType.STRING,
                description="The text to count, copied exactly", required=True,
            ),
            ToolParameter(
                name="letter", type=ParameterType.STRING,
                description="Optional single letter to count occurrences of", required=False,
            ),
        ],
        tags=[Tag(key="category", value="utility"), Tag(key="type", value="utility")],
    )
    async def count_text(self, text: str, letter: str | None = None) -> str:
        try:
            return json.dumps(count_text(text, letter).to_dict())
        except ValueError as exc:
            return json.dumps({"error": str(exc)})

    @tool(
        path="/tools/calculator/date_difference",
        short_description="Exact difference between two dates",
        description=(
            "The difference between two dates (YYYY-MM-DD): total days, weeks, "
            "completed years/months/days (an age), and calendar_years (end year "
            "minus start year). Use it for every age or elapsed-time question."
        ),
        parameters=[
            ToolParameter(
                name="start_date", type=ParameterType.STRING,
                description="The earlier date, YYYY-MM-DD", required=True,
            ),
            ToolParameter(
                name="end_date", type=ParameterType.STRING,
                description="The later date, YYYY-MM-DD", required=True,
            ),
        ],
        tags=[Tag(key="category", value="utility"), Tag(key="type", value="utility")],
    )
    async def date_difference(self, start_date: str, end_date: str) -> str:
        try:
            return json.dumps(date_difference(start_date, end_date).to_dict())
        except ValueError as exc:
            return json.dumps({"error": str(exc)})

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
