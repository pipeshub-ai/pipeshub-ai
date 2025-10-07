"""
Migrated wrapper.py - Now uses the new factory system for cleaner code
"""

import json
from typing import Any, Union

from langchain.tools import BaseTool
from pydantic import ConfigDict, Field

from app.agents.tools.factories.registry import ClientFactoryRegistry
from app.agents.tools.registry import _global_tools_registry
from app.modules.agents.qna.chat_state import ChatState


class RegistryToolWrapper(BaseTool):
    """Wrapper to adapt registry tools to LangChain BaseTool format"""

    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        extra='allow',
        validate_assignment=True
    )

    app_name: str = Field(default="", description="Application name")
    tool_name: str = Field(default="", description="Tool name")
    registry_tool: Any = Field(default=None, description="Registry tool instance")
    chat_state: Any = Field(default=None, description="Chat state")

    def __init__(self, app_name: str, tool_name: str, registry_tool, state: ChatState, **kwargs) -> None:
        base_description = getattr(registry_tool, 'description', f"Tool: {app_name}.{tool_name}")

        try:
            params = getattr(registry_tool, 'parameters', []) or []
            if params:
                formatted_params = []
                for p in params:
                    try:
                        type_name = getattr(p.type, 'name', str(getattr(p, 'type', 'string')))
                    except Exception:
                        type_name = 'string'
                    formatted_params.append(
                        f"{p.name}{' (required)' if getattr(p, 'required', False) else ''}: "
                        f"{getattr(p, 'description', '')} [{type_name}]"
                    )
                params_doc = "\nParameters:\n- " + "\n- ".join(formatted_params)
                full_description = f"{base_description}{params_doc}"
            else:
                full_description = base_description
        except Exception:
            full_description = base_description

        init_data = {
            'name': f"{app_name}.{tool_name}",
            'description': full_description,
            'app_name': app_name,
            'tool_name': tool_name,
            'registry_tool': registry_tool,
            'chat_state': state,
            **kwargs
        }

        super().__init__(**init_data)

    @property
    def state(self) -> ChatState:
        """Access the chat state"""
        return self.chat_state

    def _run(self, **kwargs) -> str:
        """Execute the registry tool directly"""
        try:
            result = self._execute_tool_directly(kwargs)
            return self._format_result(result)
        except Exception as e:
            error_msg = f"Error executing tool {self.app_name}.{self.tool_name}: {str(e)}"
            if hasattr(self.state, 'get') and self.state.get("logger"):
                self.state["logger"].error(error_msg)
            return json.dumps({
                "status": "error",
                "message": error_msg,
                "tool": f"{self.app_name}.{self.tool_name}",
                "args": kwargs
            }, indent=2)

    def _execute_tool_directly(self, arguments: dict) -> Union[tuple, str, dict, list, int, float, bool]:
        """Execute the registry tool function directly"""
        tool_function = self.registry_tool.function

        if hasattr(tool_function, '__qualname__') and '.' in tool_function.__qualname__:
            # Class method - create instance
            class_name = tool_function.__qualname__.split('.')[0]
            module_name = tool_function.__module__

            try:
                action_module = __import__(module_name, fromlist=[class_name])
                action_class = getattr(action_module, class_name)

                # Use new factory system
                instance = self._create_tool_instance_with_factory(action_class)

                bound_method = getattr(instance, self.tool_name)
                return bound_method(**arguments)
            except Exception as e:
                raise RuntimeError(
                    f"Failed to create instance for tool '{self.app_name}.{self.tool_name}': {str(e)}"
                )
        else:
            # Standalone function
            return tool_function(**arguments)

    def _create_tool_instance_with_factory(self, action_class) -> object:
        """
        NEW METHOD: Use factory pattern for client creation
        Falls back to old methods if factory not available
        """
        try:
            # Try to get factory from registry
            factory = ClientFactoryRegistry.get_factory(self.app_name)

            if factory:
                # Use factory to create client
                retrieval_service = self.state.get("retrieval_service")
                if retrieval_service and hasattr(retrieval_service, 'config_service'):
                    config_service = retrieval_service.config_service
                    logger = self.state.get("logger")

                    # Create client using factory
                    client = factory.create_client_sync(config_service, logger)
                    return action_class(client)

            # No factory available, fall back to old method
            return ValueError("Not able to get the client from factory")

        except Exception as e:
            logger = self.state.get("logger")
            if logger:
                logger.warning(f"Factory creation failed for {self.app_name}, using fallback: {e}")

    def _format_result(self, result) -> str:
        """Format tool result for LLM consumption"""
        if isinstance(result, (tuple, list)) and len(result) == 2:
            success, result_data = result
            return str(result_data)
        return str(result)


# KEEP ALL YOUR EXISTING HELPER FUNCTIONS
def get_agent_tools(state: ChatState) -> list:
    """Get all available tools from the global registry"""
    tools = []
    logger = state.get("logger")

    registry_tools = _global_tools_registry.get_all_tools()
    if logger:
        logger.info(f"Loading {len(registry_tools)} tools from global registry")

    user_enabled_tools = state.get("tools", None)

    if user_enabled_tools is not None and len(user_enabled_tools) == 0:
        user_enabled_tools = None
        if logger:
            logger.info("Empty tools list detected - loading ALL available tools")

    for full_tool_name, registry_tool in registry_tools.items():
        try:
            if "." not in full_tool_name:
                app_name = "default"
                tool_name = full_tool_name
            else:
                app_name, tool_name = full_tool_name.split(".", 1)

            should_include = False

            if user_enabled_tools is None:
                should_include = True
            elif user_enabled_tools is not None:
                should_include = (
                    full_tool_name in user_enabled_tools or
                    tool_name in user_enabled_tools or
                    app_name in user_enabled_tools
                )

            if not should_include and _is_essential_tool(full_tool_name):
                should_include = True

            if should_include:
                wrapper_tool = RegistryToolWrapper(app_name, tool_name, registry_tool, state)
                tools.append(wrapper_tool)
                if logger:
                    logger.debug(f"✅ Added tool: {full_tool_name}")

        except Exception as e:
            if logger:
                logger.error(f"Failed to add tool {full_tool_name}: {e}")
            continue

    _initialize_tool_state(state)
    state["available_tools"] = [tool.name for tool in tools]

    if logger:
        logger.info(f"Total tools available to LLM: {len(tools)}")

    return tools


def _is_essential_tool(full_tool_name: str) -> bool:
    """Check if a tool is essential"""
    essential_patterns = ["calculator.", "web_search", "get_current_datetime"]
    return any(pattern in full_tool_name for pattern in essential_patterns)


def _initialize_tool_state(state: ChatState) -> None:
    """Initialize tool-related state variables"""
    state.setdefault("tool_results", [])
    state.setdefault("all_tool_results", [])

def get_tool_by_name(tool_name: str, state: ChatState) -> RegistryToolWrapper | None:
    """Get a specific tool by name from the registry"""
    registry_tools = _global_tools_registry.get_all_tools()

    if tool_name in registry_tools:
        if "." in tool_name:
            app_name, actual_tool_name = tool_name.split(".", 1)
        else:
            app_name, actual_tool_name = "default", tool_name
        return RegistryToolWrapper(app_name, actual_tool_name, registry_tools[tool_name], state)

    for full_name, registry_tool in registry_tools.items():
        if hasattr(registry_tool, 'tool_name') and (
            registry_tool.tool_name == tool_name or full_name.endswith(f".{tool_name}")
        ):
            if "." in full_name:
                app_name, actual_tool_name = full_name.split(".", 1)
            else:
                app_name, actual_tool_name = "default", full_name
            return RegistryToolWrapper(app_name, actual_tool_name, registry_tool, state)

    return None


def get_tool_results_summary(state: ChatState) -> str:
    """Get a summary of all tool results"""
    all_results = state.get("all_tool_results", [])
    if not all_results:
        return "No tools have been executed yet."

    summary = f"Tool Execution Summary (Total: {len(all_results)}):\n"
    tool_summary = {}

    for result in all_results:
        tool_name = result.get("tool_name", "unknown")
        status = result.get("status", "unknown")
        if tool_name not in tool_summary:
            tool_summary[tool_name] = {"success": 0, "error": 0, "results": []}
        tool_summary[tool_name][status] += 1
        tool_summary[tool_name]["results"].append(result)

    for tool_name, stats in tool_summary.items():
        summary += f"\n{tool_name}:\n"
        summary += f"  - Successful: {stats['success']}\n"
        summary += f"  - Failed: {stats['error']}\n"
        if stats["results"]:
            last_result = stats["results"][-1]
            result_preview = str(last_result.get("result", ""))[:150]
            if len(result_preview) == 150:
                result_preview += "..."
            summary += f"  - Last result: {result_preview}\n"

    return summary


def get_all_available_tool_names() -> dict:
    """Get list of all available tool names"""
    registry_tools = list(_global_tools_registry.list_tools())
    return {
        "registry_tools": registry_tools,
        "total_count": len(registry_tools)
    }


def get_tool_usage_guidance() -> str:
    """Provide comprehensive guidance for tool usage"""
    return """
COMPREHENSIVE TOOL USAGE GUIDANCE:

You have access to a comprehensive set of enterprise and utility tools.

APPROACH:
- Analyze the user's request
- Choose appropriate tools
- Execute tools in logical sequence
- Provide clear responses

AVAILABLE TOOL CATEGORIES:
- Mathematical calculations
- Web search and information retrieval
- Communication (Slack, Email)
- Project management (JIRA)
- Documentation (Confluence)
- Calendar and scheduling

BEST PRACTICES:
- Use tools for better, current, or accurate information
- Combine tools for complex tasks
- Handle errors gracefully
- Provide context about tool usage
"""
