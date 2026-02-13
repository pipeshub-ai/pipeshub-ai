from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Type

from pydantic import BaseModel

from app.agents.tools.enums import ParameterType


class ToolIntent(str, Enum):
    """What the tool is primarily used for"""
    QUESTION = "question"     # Answering questions
    ACTION = "action"         # Creating/modifying
    SEARCH = "search"         # Finding/listing
    ANALYSIS = "analysis"     # Computing/analyzing
    UTILITY = "utility"       # Utility/helper tools (validation, diagnostics, etc.)


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

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for ArangoDB storage"""
        return {
            "name": self.name,
            "type": self.type.value if hasattr(self.type, 'value') else str(self.type),
            "description": self.description,
            "required": self.required,
            "default": self.default,
            "enum": self.enum,
            "items": self.items,
            "properties": self.properties
        }

    def to_json_serializable_dict(self) -> Dict[str, Any]:
        """Convert to JSON-serializable dictionary for storage and transmission"""
        return {
            "name": self.name,
            "type": self.type.value if hasattr(self.type, 'value') else str(self.type),
            "description": self.description,
            "required": self.required,
            "default": self.default,
            "enum": self.enum,
            "items": self.items,
            "properties": self.properties
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'ToolParameter':
        """Create ToolParameter from dictionary"""
        return cls(
            name=data.get("name", ""),
            type=data.get("type", ""),
            description=data.get("description", ""),
            required=data.get("required", True),
            default=data.get("default", None),
            enum=data.get("enum", None),
            items=data.get("items", None),
            properties=data.get("properties", None)
        )

@dataclass
class Tool:
    """Represents a tool that can be called by an LLM"""
    app_name: str
    tool_name: str
    description: str  # User-friendly description for frontend (default)
    function: Callable[[Any], Any]
    parameters: List[ToolParameter] = field(default_factory=list)
    args_schema: Optional[Type[BaseModel]] = None  # NEW: Pydantic schema for validation
    returns: Optional[str] = None
    examples: List[Dict] = field(default_factory=list)
    tags: List[str] = field(default_factory=list)
    llm_description: Optional[str] = None  # NEW: Detailed description for LLM planner

    # Enhanced metadata for intelligent tool selection
    when_to_use: List[str] = field(default_factory=list)  # Explicit scenarios when to use
    when_not_to_use: List[str] = field(default_factory=list)  # Anti-patterns
    primary_intent: ToolIntent = ToolIntent.ACTION  # Main use case
    typical_queries: List[str] = field(default_factory=list)  # Example queries for few-shot

    @property
    def name(self) -> str:
        """Full tool name: app_name.tool_name"""
        return f"{self.app_name}.{self.tool_name}"
