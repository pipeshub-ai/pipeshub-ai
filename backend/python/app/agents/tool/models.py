from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from app.agents.tool.enums import ParameterType


@dataclass
class ToolParameter:
    """Represents a parameter for a tool function"""
    name: str
    type: ParameterType
    description: str
    required: bool = True
    default: Any = None
    enum: Optional[List[Any]] = None
    items: Optional[Dict] = None  # For array types
    properties: Optional[Dict] = None  # For object types

@dataclass
class Tool:
    """Represents a tool that can be called by an LLM"""
    name: str
    description: str
    function: Callable[[Any], Any]
    parameters: List[ToolParameter] = field(default_factory=list)
    returns: Optional[str] = None
    examples: List[Dict] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)
