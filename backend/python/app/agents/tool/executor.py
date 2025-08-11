from typing import Dict, Optional

from app.agents.tool.registry import ToolRegistry, _global_tools_registry


class ToolExecutor:
    """Executor for running tools based on LLM requests"""

    def __init__(self, registry: Optional[ToolRegistry] = None) -> None:
        """Initialize the tool executor"""
        """
        Args:
            registry: The tool registry to use
        Returns:
            None
        """
        self.registry = registry or _global_tools_registry

    def execute(self, app_name: str, tool_name: str, arguments: Dict[str, object]) -> object:
        """
        Execute a tool with the given arguments
        Args:
            app_name: Name of the app
            tool_name: Name of the tool to execute
            arguments: Arguments to pass to the tool
        Returns:
            The result of the tool execution
        """
        tool = self.registry.get_tool(app_name, tool_name)
        if not tool:
            raise ValueError(f"Tool '{app_name}.{tool_name}' not found")

        try:
            # Validate required parameters
            for param in tool.parameters:
                if param.required and param.name not in arguments:
                    raise ValueError(f"Required parameter '{param.name}' not provided")

            # Execute the tool with unpacked arguments
            # Check if this is a class method (has 'self' parameter)
            if hasattr(tool.function, '__qualname__') and '.' in tool.function.__qualname__:
                # This is a class method, we need to create an instance
                # Extract the class name from the qualified name
                class_name = tool.function.__qualname__.split('.')[0]
                module_name = tool.function.__module__

                try:
                    # Import the module and get the class
                    action_module = __import__(module_name, fromlist=[class_name])
                    action_class = getattr(action_module, class_name)

                    # Create an instance (you might need to pass config here)
                    # For now, we'll try to create without arguments
                    try:
                        instance = action_class()
                    except TypeError:
                        # If the class requires arguments, try with empty config
                        try:
                            instance = action_class({})
                        except Exception:
                            # Last resort: try with None
                            instance = action_class(None)

                    # Get the bound method and call it
                    bound_method = getattr(instance, tool_name)
                    result = bound_method(**arguments)  # type: ignore

                except Exception as e:
                    raise RuntimeError(f"Failed to create instance for tool '{app_name}.{tool_name}': {str(e)}")
            else:
                # This is a standalone function
                result = tool.function(**arguments)  # type: ignore

            return result
        except Exception as e:
            raise RuntimeError(f"Error executing tool '{app_name}.{tool_name}': {str(e)}")

    def execute_from_llm_response(self, llm_response: Dict) -> object:
        """
        Execute a tool from an LLM response format
        Args:
            llm_response: LLM response containing tool_name and arguments
        Returns:
            The result of the tool execution
        """
        # Extract the full tool name (e.g., "calculator.add")
        full_tool_name = llm_response.get("name") or llm_response.get("function", {}).get("name")
        if not full_tool_name:
            raise ValueError("No tool name found in LLM response")

        # Split the full tool name into app_name and tool_name
        if "." in full_tool_name:
            app_name, tool_name = full_tool_name.split(".", 1)
        else:
            # Fallback: assume it's just a tool name without app prefix
            app_name = "default"
            tool_name = full_tool_name

        arguments = llm_response.get("arguments") or llm_response.get("function", {}).get("arguments") or {}

        return self.execute(app_name, tool_name, arguments)
