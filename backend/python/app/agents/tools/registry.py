"""
Enhanced tool registry with metadata support and advanced search capabilities.
"""

import logging
from typing import Any, Dict, List, Optional

from app.agents.tools.config import ToolCategory, ToolMetadata
from app.agents.tools.models import Tool


class ToolRegistry:
    """
    Enhanced registry for managing tools with metadata support.

    Features:
    - Tool registration with metadata
    - Category-based indexing
    - Advanced search capabilities
    - Schema generation for different LLM providers
    """

    def __init__(self) -> None:
        """Initialize the tool registry"""
        self._tools: Dict[str, Tool] = {}
        self._metadata: Dict[str, ToolMetadata] = {}
        self._categories: Dict[ToolCategory, List[str]] = {}
        self.logger = logging.getLogger(__name__)

    def register(self, tool: Tool, metadata: Optional[ToolMetadata] = None) -> None:
        """
        Register a tool with optional metadata.

        Args:
            tool: Tool object to register
            metadata: Optional metadata for the tool
        """
        full_name = f"{tool.app_name}.{tool.tool_name}"

        if full_name in self._tools:
            self.logger.warning(f"Tool '{full_name}' already registered, skipping")
            return

        self._tools[full_name] = tool

        # Store and index metadata
        if metadata:
            self._metadata[full_name] = metadata

            # Index by category
            if metadata.category not in self._categories:
                self._categories[metadata.category] = []
            self._categories[metadata.category].append(full_name)

        self.logger.debug(f"Registered tool: {full_name}")

    def get_tool(self, app_name: str, tool_name: str) -> Optional[Tool]:
        """
        Get a tool by app and tool name.

        Args:
            app_name: Name of the application
            tool_name: Name of the tool

        Returns:
            Tool if found, None otherwise
        """
        return self._tools.get(f"{app_name}.{tool_name}")

    def get_tools_by_category(self, category: ToolCategory) -> List[Tool]:
        """
        Get all tools in a specific category.

        Args:
            category: Category to filter by

        Returns:
            List of tools in the category
        """
        tool_names = self._categories.get(category, [])
        return [self._tools[name] for name in tool_names if name in self._tools]

    def get_tools_by_app(self, app_name: str) -> List[Tool]:
        """
        Get all tools for a specific app.

        Args:
            app_name: Name of the application

        Returns:
            List of tools for the app
        """
        return [
            tool for name, tool in self._tools.items()
            if name.startswith(f"{app_name}.")
        ]

    def list_tools(self) -> List[str]:
        """
        List all registered tool names.

        Returns:
            List of tool names in format "app_name.tool_name"
        """
        return list(self._tools.keys())

    def get_all_tools(self) -> Dict[str, Tool]:
        """
        Get all registered tools.

        Returns:
            Dictionary of all tools
        """
        return self._tools.copy()

    def get_metadata(self, tool_name: str) -> Optional[ToolMetadata]:
        """
        Get metadata for a tool.

        Args:
            tool_name: Full name of the tool

        Returns:
            ToolMetadata if found, None otherwise
        """
        return self._metadata.get(tool_name)

    def search_tools(
        self,
        query: Optional[str] = None,
        category: Optional[ToolCategory] = None,
        tags: Optional[List[str]] = None,
        essential_only: bool = False
    ) -> List[Tool]:
        """
        Search for tools based on multiple criteria.

        Args:
            query: Search query for name/description
            category: Filter by category
            tags: Filter by tags
            essential_only: Only return essential tools

        Returns:
            List of matching tools
        """
        results = []

        for name, tool in self._tools.items():
            metadata = self._metadata.get(name)

            # Filter by essential status
            if essential_only and (not metadata or not metadata.is_essential):
                continue

            # Filter by category
            if category and (not metadata or metadata.category != category):
                continue

            # Filter by tags
            if tags and metadata:
                if not any(tag in metadata.tags for tag in tags):
                    continue

            # Filter by query
            if query:
                query_lower = query.lower()
                if (query_lower not in name.lower() and
                    query_lower not in tool.description.lower()):
                    continue

            results.append(tool)

        return results

    def get_statistics(self) -> Dict[str, Any]:
        """
        Get statistics about registered tools.

        Returns:
            Dictionary with statistics
        """
        stats = {
            "total_tools": len(self._tools),
            "by_category": {},
            "by_app": {},
            "essential_count": 0,
            "requires_auth_count": 0
        }

        # Count by category
        for category, tool_names in self._categories.items():
            stats["by_category"][category.value] = len(tool_names)

        # Count by app
        for name in self._tools:
            app_name = name.split(".")[0]
            stats["by_app"][app_name] = stats["by_app"].get(app_name, 0) + 1

        # Count essential and auth-required tools
        for metadata in self._metadata.values():
            if metadata.is_essential:
                stats["essential_count"] += 1
            if metadata.requires_auth:
                stats["requires_auth_count"] += 1

        return stats

    def generate_openai_schema(self) -> List[Dict]:
        """
        Generate OpenAI-compatible function schemas.

        Returns:
            List of OpenAI function schemas
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
        """
        Generate Anthropic Claude-compatible tool schemas.

        Returns:
            List of Anthropic tool schemas
        """
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
