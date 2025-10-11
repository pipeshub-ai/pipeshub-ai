"""
Enhanced tool decorator with automatic parameter extraction and metadata support.
"""

import functools
import inspect
from typing import Callable, Dict, List, Optional, get_origin

try:
    from typing import get_type_hints
except ImportError:
    from typing_extensions import get_type_hints

from app.agents.tools.config import ToolCategory, ToolMetadata
from app.agents.tools.enums import ParameterType
from app.agents.tools.models import Tool, ToolParameter
from app.agents.tools.registry import _global_tools_registry


def tool(
    app_name: str,
    tool_name: str,
    description: Optional[str] = None,
    parameters: Optional[List[ToolParameter]] = None,
    returns: Optional[str] = None,
    examples: Optional[List[Dict]] = None,
    tags: Optional[List[str]] = None,
    category: ToolCategory = ToolCategory.UTILITY,
    is_essential: bool = False,
    requires_auth: bool = True,
) -> Callable:
    """
    Enhanced decorator to register a function as a tool.

    Args:
        app_name: Tool app name (required)
        tool_name: Tool name (required)
        description: Tool description (defaults to docstring)
        parameters: List of ToolParameter objects (auto-generated if not provided)
        returns: Description of return value
        examples: List of example invocations
        tags: Tags for categorization
        category: Tool category
        is_essential: Whether tool is essential (always loaded)
        requires_auth: Whether tool requires authentication

    Returns:
        Decorated function

    Example:
        ```python
        @tool(
            app_name="myapp",
            tool_name="process_data",
            description="Process data and return result",
            category=ToolCategory.UTILITY,
            is_essential=False,
            tags=["data", "processing"]
        )
        def process_data(input_text: str, count: int = 1) -> str:
            return input_text * count
        ```
    """
    def decorator(func: Callable) -> Callable:
        # Validate required fields
        if not app_name:
            raise ValueError("app_name must be provided")
        if not tool_name:
            raise ValueError("tool_name must be provided")

        # Extract metadata
        tool_description = description or (func.__doc__ or "").strip()

        # Auto-generate parameters if not provided
        tool_parameters = parameters or _extract_parameters(func)

        # Create tool object
        tool_obj = Tool(
            app_name=app_name,
            tool_name=tool_name,
            description=tool_description,
            function=func,
            parameters=tool_parameters,
            returns=returns,
            examples=examples or [],
            tags=tags or []
        )

        # Create metadata
        metadata = ToolMetadata(
            app_name=app_name,
            tool_name=tool_name,
            description=tool_description,
            category=category,
            is_essential=is_essential,
            requires_auth=requires_auth,
            tags=tags or []
        )

        # Register tool with metadata
        _global_tools_registry.register(tool_obj, metadata)

        # Add metadata to function
        setattr(func, '_tool_metadata', tool_obj)

        @functools.wraps(func)
        def wrapper(*args, **kwargs) -> object:
            return func(*args, **kwargs)

        setattr(wrapper, '_tool_metadata', tool_obj)
        return wrapper

    return decorator


def _extract_parameters(func: Callable) -> List[ToolParameter]:
    """
    Extract parameters from function signature with type hints.

    Args:
        func: Function to extract parameters from

    Returns:
        List of ToolParameter objects
    """
    sig = inspect.signature(func)
    type_hints = get_type_hints(func)
    parameters = []

    for param_name, param in sig.parameters.items():
        # Skip self and cls
        if param_name in ('self', 'cls'):
            continue

        # Determine parameter type
        param_type = ParameterType.STRING  # default
        if param_name in type_hints:
            type_hint = type_hints[param_name]
            param_type = _infer_parameter_type(type_hint)

        # Check if required
        required = param.default == inspect.Parameter.empty

        parameters.append(ToolParameter(
            name=param_name,
            type=param_type,
            description=f"Parameter {param_name}",
            required=required,
            default=param.default if not required else None
        ))

    return parameters


def _infer_parameter_type(type_hint) -> ParameterType:
    """
    Infer ParameterType from Python type hint.
    Args:
        type_hint: Python type hint

    Returns:
        Corresponding ParameterType
    """
    if isinstance(type_hint, type):
        if type_hint is int:
            return ParameterType.INTEGER
        elif type_hint is float:
            return ParameterType.NUMBER
        elif type_hint is bool:
            return ParameterType.BOOLEAN

    origin = get_origin(type_hint)
    if origin is list:
        return ParameterType.ARRAY
    elif origin is dict:
        return ParameterType.OBJECT

    return ParameterType.STRING
