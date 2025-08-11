import functools
import inspect
from typing import Callable, Dict, List, Optional

try:
    from typing import get_type_hints
except ImportError:
    from typing_extensions import get_type_hints

from app.agents.tool.enums import ParameterType
from app.agents.tool.models import Tool, ToolParameter
from app.agents.tool.registry import ToolRegistry, _global_tools_registry


def tool(
    app_name: str,
    tool_name: str,
    description: Optional[str] = None,
    parameters: Optional[List[ToolParameter]] = None,
    returns: Optional[str] = None,
    examples: Optional[List[Dict]] = None,
    tags: Optional[List[str]] = None,
    registry: Optional[ToolRegistry] = None
):
    """
    Decorator to register a function as a tool for LLM use
    Args:
        app_name: Tool app name
        tool_name: Tool name
        description: Tool description (defaults to docstring)
        parameters: List of ToolParameter objects defining the function parameters
        returns: Description of what the tool returns
        examples: List of example invocations
        tags: Tags for categorizing the tool
        registry: ToolRegistry to register with (defaults to global registry)
    """
    def decorator(func: Callable) -> Callable:
        # Extract metadata
        if not app_name:
            raise ValueError("app_name must be provided")
        if not tool_name:
            raise ValueError("tool_name must be provided")
        tool_description = description or (func.__doc__ or "").strip()

        # Auto-generate parameters from function signature if not provided
        tool_parameters = parameters
        if tool_parameters is None:
            tool_parameters = _extract_parameters(func)

        # Create and register the tool
        tool_obj = Tool(
            app_name=app_name,
            tool_name=tool_name,
            description=tool_description,
            function=func,
            parameters=tool_parameters or [],
            returns=returns,
            examples=examples or [],
            tags=tags or []
        )

        # Register with the appropriate registry
        target_registry = registry or _global_tools_registry
        target_registry.register(tool_obj)

        # Add metadata to the function using setattr to avoid type checker issues
        setattr(func, '_tool_metadata', tool_obj)

        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            return func(*args, **kwargs)

        # Also add metadata to the wrapper
        setattr(wrapper, '_tool_metadata', tool_obj)

        return wrapper

    return decorator

def _extract_parameters(func: Callable) -> List[ToolParameter]:
    """Extract parameters from function signature"""
    sig = inspect.signature(func)
    type_hints = get_type_hints(func)
    parameters = []

    for param_name, param in sig.parameters.items():
        if param_name == 'self' or param_name == 'cls':
            continue

        # Determine type
        param_type = ParameterType.STRING  # default
        if param_name in type_hints:
            type_hint = type_hints[param_name]
            if isinstance(type_hint, type) and type_hint is int:
                param_type = ParameterType.INTEGER
            elif isinstance(type_hint, type) and type_hint is float:
                param_type = ParameterType.NUMBER
            elif isinstance(type_hint, type) and type_hint is bool:
                param_type = ParameterType.BOOLEAN
            elif isinstance(type_hint, type) and type_hint is list or str(type_hint).startswith('typing.List'):
                param_type = ParameterType.ARRAY
            elif isinstance(type_hint, type) and type_hint is dict or str(type_hint).startswith('typing.Dict'):
                param_type = ParameterType.OBJECT

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
