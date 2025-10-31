"""
Migrated wrapper.py - Now uses the new factory system for cleaner code
"""

import json
from typing import Callable, Dict, List, Optional, Union

from langchain.tools import BaseTool
from pydantic import ConfigDict, Field

from app.agents.tools.factories.registry import ClientFactoryRegistry
from app.agents.tools.registry import _global_tools_registry
from app.modules.agents.qna.chat_state import ChatState

# Constants
TOOL_RESULT_TUPLE_LENGTH = 2
RESULT_PREVIEW_MAX_LENGTH = 150

# Type aliases
ToolResult = Union[tuple, str, dict, list, int, float, bool]


class RegistryToolWrapper(BaseTool):
    """Wrapper to adapt registry tools to LangChain BaseTool format"""

    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        extra='allow',
        validate_assignment=True
    )

    app_name: str = Field(default="", description="Application name")
    tool_name: str = Field(default="", description="Tool name")
    registry_tool: object = Field(default=None, description="Registry tool instance")
    chat_state: object = Field(default=None, description="Chat state")

    def __init__(
        self,
        app_name: str,
        tool_name: str,
        registry_tool: object,
        state: ChatState,
        **kwargs: Union[str, int, bool, dict, list, None]
    ) -> None:
        """Initialize the registry tool wrapper.

        Args:
            app_name: Application name
            tool_name: Tool name
            registry_tool: Registry tool instance
            state: Chat state object
            **kwargs: Additional keyword arguments
        """
        base_description = getattr(
            registry_tool,
            'description',
            f"Tool: {app_name}.{tool_name}"
        )

        try:
            params = getattr(registry_tool, 'parameters', []) or []
            if params:
                formatted_params = self._format_parameters(params)
                params_doc = "\nParameters:\n- " + "\n- ".join(formatted_params)
                full_description = f"{base_description}{params_doc}"
            else:
                full_description = base_description
        except Exception:
            full_description = base_description

        init_data: Dict[str, Union[str, object]] = {
            'name': f"{app_name}.{tool_name}",
            'description': full_description,
            'app_name': app_name,
            'tool_name': tool_name,
            'registry_tool': registry_tool,
            'chat_state': state,
            **kwargs
        }

        super().__init__(**init_data)

    @staticmethod
    def _format_parameters(params: List[object]) -> List[str]:
        """Format parameters for description.

        Args:
            params: List of parameter objects

        Returns:
            List of formatted parameter strings
        """
        formatted_params = []
        for param in params:
            try:
                type_name = getattr(
                    param.type,
                    'name',
                    str(getattr(param, 'type', 'string'))
                )
            except Exception:
                type_name = 'string'

            required_marker = ' (required)' if getattr(param, 'required', False) else ''
            description = getattr(param, 'description', '')
            formatted_params.append(
                f"{param.name}{required_marker}: {description} [{type_name}]"
            )
        return formatted_params

    @property
    def state(self) -> ChatState:
        """Access the chat state.

        Returns:
            Chat state object
        """
        return self.chat_state

    def _run(self, **kwargs: Union[str, int, bool, dict, list, None]) -> str:
        """Execute the registry tool directly.

        Args:
            **kwargs: Tool arguments

        Returns:
            Formatted result string
        """
        try:
            result = self._execute_tool_directly(kwargs)
            return self._format_result(result)
        except Exception as e:
            return self._handle_execution_error(e, kwargs)

    def _handle_execution_error(
        self,
        error: Exception,
        arguments: Dict[str, Union[str, int, bool, dict, list, None]]
    ) -> str:
        """Handle tool execution error.
        Args:
            error: Exception that occurred
            arguments: Tool arguments

        Returns:
            Formatted error message
        """
        error_msg = f"Error executing tool {self.app_name}.{self.tool_name}: {str(error)}"

        logger = self.state.get("logger") if hasattr(self.state, 'get') else None
        if logger:
            logger.error(error_msg)

        return json.dumps({
            "status": "error",
            "message": error_msg,
            "tool": f"{self.app_name}.{self.tool_name}",
            "args": arguments
        }, indent=2)

    def _execute_tool_directly(
        self,
        arguments: Dict[str, Union[str, int, bool, dict, list, None]]
    ) -> ToolResult:
        """Execute the registry tool function directly.

        Args:
            arguments: Tool arguments

        Returns:
            Tool execution result
        """
        tool_function = self.registry_tool.function

        if hasattr(tool_function, '__qualname__') and '.' in tool_function.__qualname__:
            return self._execute_class_method(tool_function, arguments)
        else:
            return tool_function(**arguments)

    def _execute_class_method(
        self,
        tool_function: Callable,
        arguments: Dict[str, Union[str, int, bool, dict, list, None]]
    ) -> ToolResult:
        """Execute a class method tool.

        Args:
            tool_function: Tool function object
            arguments: Tool arguments

        Returns:
            Tool execution result

        Raises:
            RuntimeError: If instance creation fails
        """
        class_name = tool_function.__qualname__.split('.')[0]
        module_name = tool_function.__module__

        try:
            action_module = __import__(module_name, fromlist=[class_name])
            action_class = getattr(action_module, class_name)

            instance = self._create_tool_instance_with_factory(action_class)
            bound_method = getattr(instance, self.tool_name)
            return bound_method(**arguments)
        except Exception as e:
            raise RuntimeError(
                f"Failed to create instance for tool '{self.app_name}.{self.tool_name}': {str(e)}"
            ) from e

    def _create_tool_instance_with_factory(self, action_class: type) -> object:
        """Use factory pattern for client creation.

        Args:
            action_class: Action class to instantiate

        Returns:
            Tool instance

        Raises:
            ValueError: If factory not available
        """
        try:
            factory = ClientFactoryRegistry.get_factory(self.app_name)

            if factory:
                retrieval_service = self.state.get("retrieval_service")
                if retrieval_service and hasattr(retrieval_service, 'config_service'):
                    config_service = retrieval_service.config_service
                    logger = self.state.get("logger")

                    # Pass chat state into factory for auth/impersonation decisions
                    client = factory.create_client_sync(config_service, logger, self.state)
                    return action_class(client)

            raise ValueError("Not able to get the client from factory")

        except Exception as e:
            logger = self.state.get("logger")
            if logger:
                logger.warning(
                    f"Factory creation failed for {self.app_name}, using fallback: {e}"
                )
            raise

    def _format_result(self, result: ToolResult) -> str:
        """Format tool result for LLM consumption.

        Args:
            result: Tool execution result

        Returns:
            Formatted result string
        """
        if isinstance(result, (tuple, list)) and len(result) == TOOL_RESULT_TUPLE_LENGTH:
            success, result_data = result
            return str(result_data)
        return str(result)


def _get_recently_failed_tools(state: ChatState, logger) -> dict:
    """
    Identify tools that have recently failed multiple times and should be blocked.

    Args:
        state: Chat state
        logger: Logger instance

    Returns:
        Dict mapping tool_name to failure count for blocked tools
    """
    LOOKBACK_WINDOW = 7  # Check last N tool calls
    FAILURE_THRESHOLD = 2  # Block if failed N+ times

    all_results = state.get("all_tool_results", [])
    if not all_results or len(all_results) < FAILURE_THRESHOLD:
        return {}

    # Look at recent tool results
    recent_results = all_results[-LOOKBACK_WINDOW:]

    # Count failures per tool
    failure_counts = {}
    for result in recent_results:
        tool_name = result.get("tool_name", "unknown")
        status = result.get("status", "unknown")

        if status == "error":
            failure_counts[tool_name] = failure_counts.get(tool_name, 0) + 1

    # Block tools that exceeded failure threshold
    blocked_tools = {
        tool: count
        for tool, count in failure_counts.items()
        if count >= FAILURE_THRESHOLD
    }

    if blocked_tools and logger:
        logger.info(f"🚫 Identified {len(blocked_tools)} tools to block based on recent failures")

    return blocked_tools


def get_agent_tools(state: ChatState) -> List[RegistryToolWrapper]:
    """Get all available tools from the global registry.
    - Caches tools after first load for performance
    - Only re-computes when blocked tools change
    - Filters out tools that have recently failed to prevent infinite retry loops

    Args:
        state: Chat state object

    Returns:
        List of tool wrappers
    """
    logger = state.get("logger")

    # **PERFORMANCE OPTIMIZATION**: Check if tools are already cached
    cached_tools = state.get("_cached_agent_tools")
    cached_blocked_tools = state.get("_cached_blocked_tools", {})

    # Identify recently failed tools
    blocked_tools = _get_recently_failed_tools(state, logger)

    # If cache exists and blocked tools haven't changed, return cached tools
    if cached_tools is not None and blocked_tools == cached_blocked_tools:
        if logger:
            logger.debug(f"⚡ Using cached tools ({len(cached_tools)} tools) - significant performance boost")
        return cached_tools

    # Cache miss or blocked tools changed - rebuild tool list
    if logger:
        if cached_tools is not None:
            logger.info("🔄 Blocked tools changed - rebuilding tool cache")
        else:
            logger.info("📦 First tool load - building cache")

    tools: List[RegistryToolWrapper] = []
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
            app_name, tool_name = _parse_tool_name(full_tool_name)

            should_include = _should_include_tool(
                full_tool_name,
                tool_name,
                app_name,
                user_enabled_tools
            )

            # Exclude tools that have recently failed
            if should_include and full_tool_name in blocked_tools:
                if logger:
                    logger.warning(f"🚫 Blocking tool: {full_tool_name} (recently failed {blocked_tools[full_tool_name]} times)")
                should_include = False

            if should_include:
                wrapper_tool = RegistryToolWrapper(
                    app_name,
                    tool_name,
                    registry_tool,
                    state
                )
                tools.append(wrapper_tool)
                if logger:
                    logger.debug(f"✅ Added tool: {full_tool_name}")

        except Exception as e:
            if logger:
                logger.error(f"Failed to add tool {full_tool_name}: {e}")
            continue

    _initialize_tool_state(state)
    state["available_tools"] = [tool.name for tool in tools]

    # **PERFORMANCE**: Cache the tools and blocked tools list
    state["_cached_agent_tools"] = tools
    state["_cached_blocked_tools"] = blocked_tools.copy()

    if logger:
        logger.info(f"✅ Cached {len(tools)} tools for future iterations")
        if blocked_tools:
            logger.warning(f"⚠️ Blocked {len(blocked_tools)} tools due to recent failures: {list(blocked_tools.keys())}")

    return tools


def _parse_tool_name(full_tool_name: str) -> tuple[str, str]:
    """Parse full tool name into app name and tool name.

    Args:
        full_tool_name: Full tool name (e.g., "app.tool")

    Returns:
        Tuple of (app_name, tool_name)
    """
    if "." not in full_tool_name:
        return "default", full_tool_name
    return full_tool_name.split(".", 1)


def _should_include_tool(
    full_tool_name: str,
    tool_name: str,
    app_name: str,
    user_enabled_tools: Optional[List[str]]
) -> bool:
    """Determine if a tool should be included.

    Args:
        full_tool_name: Full tool name
        tool_name: Tool name
        app_name: Application name
        user_enabled_tools: List of user-enabled tools or None

    Returns:
        True if tool should be included
    """
    if user_enabled_tools is None:
        return True

    if (full_tool_name in user_enabled_tools or
        tool_name in user_enabled_tools or
        app_name in user_enabled_tools):
        return True

    return _is_essential_tool(full_tool_name)


def _is_essential_tool(full_tool_name: str) -> bool:
    """Check if a tool is essential.

    Args:
        full_tool_name: Full tool name

    Returns:
        True if tool is essential
    """
    essential_patterns = ["calculator.", "web_search", "get_current_datetime"]
    return any(pattern in full_tool_name for pattern in essential_patterns)


def _initialize_tool_state(state: ChatState) -> None:
    """Initialize tool-related state variables.

    Args:
        state: Chat state object
    """
    state.setdefault("tool_results", [])
    state.setdefault("all_tool_results", [])


def get_tool_by_name(tool_name: str, state: ChatState) -> Optional[RegistryToolWrapper]:
    """Get a specific tool by name from the registry.

    Args:
        tool_name: Name of the tool to retrieve
        state: Chat state object

    Returns:
        Tool wrapper or None if not found
    """
    registry_tools = _global_tools_registry.get_all_tools()

    # Direct match
    if tool_name in registry_tools:
        app_name, actual_tool_name = _parse_tool_name(tool_name)
        return RegistryToolWrapper(
            app_name,
            actual_tool_name,
            registry_tools[tool_name],
            state
        )

    # Search by tool name or suffix
    for full_name, registry_tool in registry_tools.items():
        if hasattr(registry_tool, 'tool_name') and (
            registry_tool.tool_name == tool_name or
            full_name.endswith(f".{tool_name}")
        ):
            app_name, actual_tool_name = _parse_tool_name(full_name)
            return RegistryToolWrapper(
                app_name,
                actual_tool_name,
                registry_tool,
                state
            )

    return None


def get_tool_results_summary(state: ChatState) -> str:
    """Get a summary of all tool results with enhanced progress tracking.
    **FULLY GENERIC** - dynamically groups tools by their prefix/category.
    """
    all_results = state.get("all_tool_results", [])
    if not all_results:
        return "No tools have been executed yet."

    summary = f"Tool Execution Summary (Total: {len(all_results)}):\n"
    tool_summary = _build_tool_summary(all_results)

    # **DYNAMIC** categorization - extract categories from tool names
    tool_categories = {}

    for tool_name, stats in tool_summary.items():
        # Extract category from tool name (e.g., "slack.send_message" → "slack")
        category = tool_name.split('.')[0] if '.' in tool_name else "utility"

        if category not in tool_categories:
            tool_categories[category] = []

        tool_categories[category].append((tool_name, stats))

    # Add progress insights
    for category, tools in sorted(tool_categories.items()):
        if tools:
            summary += f"\n## {category.title()} Tools:\n"
            for tool_name, stats in tools:
                summary += _format_tool_stats(tool_name, stats)

                # **GENERIC** guidance based on tool verb/action
                if stats["success"] > 0:
                    tool_action = tool_name.split('.')[-1] if '.' in tool_name else tool_name

                    # Generic guidance based on action type
                    if any(verb in tool_action.lower() for verb in ["fetch", "get", "list", "retrieve"]):
                        summary += "  - ✅ Data retrieved successfully - can be used for further actions\n"
                    elif any(verb in tool_action.lower() for verb in ["create", "send", "post", "add"]):
                        summary += "  - ✅ Action completed successfully\n"
                    elif any(verb in tool_action.lower() for verb in ["update", "modify", "edit"]):
                        summary += "  - ✅ Update completed successfully\n"

    return summary


def _build_tool_summary(all_results: List[Dict[str, object]]) -> Dict[str, Dict[str, object]]:
    """Build summary statistics for tools.

    Args:
        all_results: List of all tool results

    Returns:
        Dictionary of tool statistics
    """
    tool_summary: Dict[str, Dict[str, object]] = {}

    for result in all_results:
        tool_name = result.get("tool_name", "unknown")
        status = result.get("status", "unknown")

        if tool_name not in tool_summary:
            tool_summary[tool_name] = {
                "success": 0,
                "error": 0,
                "results": []
            }

        tool_summary[tool_name][status] += 1
        tool_summary[tool_name]["results"].append(result)

    return tool_summary


def _format_tool_stats(tool_name: str, stats: Dict[str, object]) -> str:
    """Format statistics for a single tool.

    Args:
        tool_name: Name of the tool
        stats: Statistics dictionary

    Returns:
        Formatted statistics string
    """
    summary = f"\n{tool_name}:\n"
    summary += f"  - Successful: {stats['success']}\n"
    summary += f"  - Failed: {stats['error']}\n"

    results = stats.get("results", [])
    if results and isinstance(results, list):
        last_result = results[-1]
        result_str = str(last_result.get("result", ""))
        result_preview = result_str[:RESULT_PREVIEW_MAX_LENGTH]

        if len(result_str) > RESULT_PREVIEW_MAX_LENGTH:
            result_preview += "..."

        summary += f"  - Last result: {result_preview}\n"

    return summary


def get_all_available_tool_names() -> Dict[str, Union[List[str], int]]:
    """Get list of all available tool names.

    Returns:
        Dictionary with tool names and count
    """
    registry_tools = list(_global_tools_registry.list_tools())
    return {
        "registry_tools": registry_tools,
        "total_count": len(registry_tools)
    }


def get_tool_usage_guidance() -> str:
    """Provide comprehensive guidance for tool usage.

    Returns:
        Tool usage guidance string
    """
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
