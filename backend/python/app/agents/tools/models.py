from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from app.agents.tools.enums import ParameterType


@dataclass
class ToolParameter:
    """Represents a parameter for a tool function"""

    name: str
    type: ParameterType
    description: str
    required: bool = True
    default: Any = None
    enum: list[Any] | None = None
    items: dict | None = None  # For array types
    properties: dict | None = None  # For object types

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for ArangoDB storage"""
        return {
            "name": self.name,
            "type": self.type.value if hasattr(self.type, "value") else str(self.type),
            "description": self.description,
            "required": self.required,
            "default": self.default,
            "enum": self.enum,
            "items": self.items,
            "properties": self.properties,
        }

    def to_json_serializable_dict(self) -> dict[str, Any]:
        """Convert to JSON-serializable dictionary for storage and transmission"""
        return {
            "name": self.name,
            "type": self.type.value if hasattr(self.type, "value") else str(self.type),
            "description": self.description,
            "required": self.required,
            "default": self.default,
            "enum": self.enum,
            "items": self.items,
            "properties": self.properties,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ToolParameter":
        """Create ToolParameter from dictionary"""
        return cls(
            name=data.get("name", ""),
            type=data.get("type", ""),
            description=data.get("description", ""),
            required=data.get("required", True),
            default=data.get("default"),
            enum=data.get("enum"),
            items=data.get("items"),
            properties=data.get("properties"),
        )


@dataclass
class Tool:
    """Represents a tool that can be called by an LLM"""

    app_name: str
    tool_name: str
    description: str
    function: Callable[[Any], Any]
    parameters: list[ToolParameter] = field(default_factory=list)
    returns: str | None = None
    examples: list[dict] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)

    @property
    def name(self) -> str:
        """Full tool name: app_name.tool_name"""
        return f"{self.app_name}.{self.tool_name}"
