from typing import Any, Dict, List, Optional

from app.agents.tools.models import Tool


class ToolRegistry:
    """Registry for managing tools available to LLMs"""

    def __init__(self) -> None:
        """Initialize the tool registry"""
        """
        Args:
            None
        Returns:
            None
        """
        self._tools: Dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        """Register a tool in the registry"""
        """
        Args:
            tool: The tool to register
        Returns:
            None
        """
        if f"{tool.app_name}.{tool.tool_name}" in self._tools:
            raise ValueError(f"Tool '{tool.tool_name}' is already registered")
        self._tools[f"{tool.app_name}.{tool.tool_name}"] = tool

    def get_tool(self, app_name: str, tool_name: str) -> Optional[Tool]:
        """Get a tool by name"""
        """
        Args:
            app_name: The name of the app
            tool_name: The name of the tool
        Returns:
            The tool
        """
        return self._tools.get(f"{app_name}.{tool_name}")

    def list_tools(self) -> List[str]:
        """List all registered tool names"""
        """
        Args:
            None
        Returns:
            A list of all registered tool names
        """
        return list(self._tools.keys())

    def get_all_tools(self) -> Dict[str, Tool]:
        """Get all registered tools"""
        """
        Args:
            None
        Returns:
            A dictionary of all registered tools
        """
        return self._tools.copy()

    def generate_openai_schema(self) -> List[Dict]:
        """Generate OpenAI-compatible function schemas"""
        """
        Args:
            None
        Returns:
            A list of OpenAI-compatible function schemas
        """
        schemas = []
        for tool in self._tools.values():
            schema = {
                "type": "function",
                "function": {
                    "name": f"{tool.app_name}.{tool.tool_name}",
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
                "name": f"{tool.app_name}.{tool.tool_name}",
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
