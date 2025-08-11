from typing import Any, Dict, List, Optional

from app.agents.tool.models import Tool


class ToolRegistry:
    """Registry for managing tools available to LLMs"""

    def __init__(self):
        self._tools: Dict[str, Tool] = {}

    def register(self, tool: Tool):
        """Register a tool in the registry"""
        if tool.name in self._tools:
            raise ValueError(f"Tool '{tool.name}' is already registered")
        self._tools[tool.name] = tool

    def get_tool(self, name: str) -> Optional[Tool]:
        """Get a tool by name"""
        return self._tools.get(name)

    def list_tools(self) -> List[str]:
        """List all registered tool names"""
        return list(self._tools.keys())

    def get_all_tools(self) -> Dict[str, Tool]:
        """Get all registered tools"""
        return self._tools.copy()

    def generate_openai_schema(self) -> List[Dict]:
        """Generate OpenAI-compatible function schemas"""
        schemas = []
        for tool in self._tools.values():
            schema = {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": {
                        "type": "object",
                        "properties": {},
                        "required": []
                    }
                }
            }

            for param in tool.parameters:
                prop: Dict[str, Any] = {"type": param.type.value}
                if param.description:
                    prop["description"] = param.description
                if param.enum:
                    prop["enum"] = param.enum
                if param.type.value == "array" and param.items:
                    prop["items"] = param.items["type"]
                if param.type.value == "object" and param.properties:
                    prop["properties"] = param.properties["type"]

                schema["function"]["parameters"]["properties"][param.name] = prop
                if param.required:
                    schema["function"]["parameters"]["required"].append(param.name)

            schemas.append(schema)

        return schemas

    def generate_anthropic_schema(self) -> List[Dict]:
        """Generate Anthropic Claude-compatible tool schemas"""
        schemas = []
        for tool in self._tools.values():
            schema = {
                "name": tool.name,
                "description": tool.description,
                "input_schema": {
                    "type": "object",
                    "properties": {},
                    "required": []
                }
            }

            for param in tool.parameters:
                prop: Dict[str, Any] = {"type": param.type.value}
                if param.description:
                    prop["description"] = param.description
                if param.enum:
                    prop["enum"] = param.enum

                schema["input_schema"]["properties"][param.name] = prop
                if param.required:
                    schema["input_schema"]["required"].append(param.name)

            schemas.append(schema)

        return schemas

# Global registry instance
_global_tools_registry = ToolRegistry()
