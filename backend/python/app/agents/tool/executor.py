import json
from typing import Any, Dict, Optional

from app.agents.tool.registry import ToolRegistry, _global_tools_registry


class ToolExecutor:
    """Executor for running tools based on LLM requests"""

    def __init__(self, registry: Optional[ToolRegistry] = None):
        self.registry = registry or _global_tools_registry

    def execute(self, tool_name: str, arguments: Dict[str, Any]) -> Any:
        """
        Execute a tool with the given arguments
        Args:
            tool_name: Name of the tool to execute
            arguments: Arguments to pass to the tool
        Returns:
            The result of the tool execution
        """
        tool = self.registry.get_tool(tool_name)
        if not tool:
            raise ValueError(f"Tool '{tool_name}' not found")

        try:
            # Validate required parameters
            for param in tool.parameters:
                if param.required and param.name not in arguments:
                    raise ValueError(f"Required parameter '{param.name}' not provided")

            # Execute the tool
            result = tool.function(arguments)
            return result
        except Exception as e:
            raise RuntimeError(f"Error executing tool '{tool_name}': {str(e)}")

    def execute_from_llm_response(self, llm_response: Dict) -> Any:
        """
        Execute a tool from an LLM response format
        Args:
            llm_response: LLM response containing tool_name and arguments
        Returns:
            The result of the tool execution
        """
        tool_name = llm_response.get("name") or llm_response.get("function", {}).get("name")
        arguments = llm_response.get("arguments") or llm_response.get("function", {}).get("arguments")

        if isinstance(arguments, str):
            arguments = json.loads(arguments)

        return self.execute(tool_name, arguments)
