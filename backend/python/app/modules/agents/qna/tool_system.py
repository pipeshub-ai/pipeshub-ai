"""
Tool System - Simplified and Professional Tool Management
This module provides a clean, maintainable interface for tool loading,
caching, and execution. Designed for reliability, speed, and ease of understanding.
Key Features:
1. Simple tool loading with automatic caching
2. Clear error handling and retry logic
3. Instance caching for performance
4. Easy-to-understand API
"""

import logging
from typing import Any, Callable, Dict, List, Optional

from langchain.tools import BaseTool
from pydantic import ConfigDict, Field

from app.agents.tools.registry import _global_tools_registry
from app.modules.agents.qna.chat_state import ChatState

logger = logging.getLogger(__name__)


class ToolWrapper(BaseTool):
    """
    Simple, clean wrapper for registry tools.

    Wraps registry tools to work with LangChain's BaseTool interface
    while maintaining simplicity and clarity.
    """

    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        extra='allow',
        validate_assignment=False  # Don't validate on assignment
    )

    app_name: str = Field(description="Application name (e.g., 'slack', 'jira')")
    tool_name: str = Field(description="Tool name (e.g., 'send_message')")
    registry_tool: Any = Field(description="Original registry tool")
    # Store state as private attribute to avoid Pydantic validation of TypedDict
    _state: Optional[Dict[str, Any]] = None

    def __init__(self, app_name: str, tool_name: str, registry_tool: Any, state: ChatState, **kwargs):
        """
        Initialize tool wrapper.

        Args:
            app_name: Application name
            tool_name: Tool function name
            registry_tool: Original tool from registry
            state: Chat state for context
            **kwargs: Additional arguments
        """
        # Build description
        description = getattr(registry_tool, 'description', f"{app_name}.{tool_name}")

        # Add parameters to description if available
        if hasattr(registry_tool, 'parameters') and registry_tool.parameters:
            param_docs = self._build_parameter_docs(registry_tool.parameters)
            if param_docs:
                description = f"{description}\n\nParameters:\n{param_docs}"

        # Initialize parent WITHOUT state field
        super().__init__(
            name=f"{app_name}.{tool_name}",
            description=description,
            app_name=app_name,
            tool_name=tool_name,
            registry_tool=registry_tool,
            **kwargs
        )

        # Store state as private attribute after initialization
        self._state = state

    @property
    def state(self) -> ChatState:
        """Get chat state."""
        return self._state

    @staticmethod
    def _build_parameter_docs(parameters: List[Any]) -> str:
        """Build parameter documentation string."""
        docs = []
        for param in parameters:
            param_name = getattr(param, 'name', 'unknown')
            param_type = getattr(getattr(param, 'type', None), 'name', 'any')
            param_required = getattr(param, 'required', False)
            required_str = " (required)" if param_required else " (optional)"
            docs.append(f"  - {param_name}: {param_type}{required_str}")
        return "\n".join(docs)

    def _run(self, **kwargs) -> Any:
        """
        Execute the tool.

        Args:
            **kwargs: Tool arguments

        Returns:
            Tool execution result
        """
        try:
            # Get tool function - matches original implementation
            tool_function = self.registry_tool.function

            # Execute based on tool type
            if self._is_class_method(tool_function):
                result = self._execute_class_method(tool_function, kwargs)
            else:
                result = tool_function(**kwargs)

            # Format result
            return self._format_result(result)

        except Exception as e:
            logger.error(f"Error executing tool {self.app_name}.{self.tool_name}: {e}", exc_info=True)
            import json
            return json.dumps({
                "status": "error",
                "message": str(e),
                "tool": f"{self.app_name}.{self.tool_name}",
                "args": kwargs
            }, indent=2)

    async def _arun(self, **kwargs) -> Any:
        """Async execution (calls sync version)."""
        return self._run(**kwargs)

    @staticmethod
    def _is_class_method(func: Callable) -> bool:
        """Check if function is a class method."""
        return hasattr(func, '__qualname__') and '.' in func.__qualname__

    @staticmethod
    def _format_result(result: Any) -> str:
        """
        Format tool result for LLM consumption.

        Matches original implementation.
        """
        # Handle tuple results (success, data)
        if isinstance(result, (tuple, list)) and len(result) == 2:
            success, result_data = result
            return str(result_data)

        return str(result)

    def _execute_class_method(self, func: Callable, arguments: Dict) -> Any:
        """Execute class method with instance caching."""
        try:
            # Parse class info
            class_name = func.__qualname__.split('.')[0]
            module_name = func.__module__

            # Get or create instance
            instance = self._get_tool_instance(module_name, class_name, self.app_name)

            # Execute method
            method = getattr(instance, self.tool_name)
            return method(**arguments)

        except Exception as e:
            raise RuntimeError(f"Failed to execute {self.name}: {str(e)}") from e

    def _get_tool_instance(self, module_name: str, class_name: str, app_name: str) -> Any:
        """
        Get or create tool instance with caching.

        Instances are cached in state for performance.
        """
        cache_key = f"{module_name}.{class_name}_{app_name}"

        # Check cache
        instance_cache = self.state.setdefault("_tool_instance_cache", {})
        if cache_key in instance_cache:
            logger.debug(f"Using cached instance for {cache_key}")
            return instance_cache[cache_key]

        # Create new instance
        logger.debug(f"Creating new instance for {cache_key}")

        try:
            # Import class
            action_module = __import__(module_name, fromlist=[class_name])
            action_class = getattr(action_module, class_name)

            # Create instance with factory
            instance = self._create_instance_with_factory(action_class)

            # Cache it
            instance_cache[cache_key] = instance
            logger.info(f"Cached instance for {cache_key} (total cached: {len(instance_cache)})")

            return instance

        except Exception as e:
            raise RuntimeError(f"Failed to create instance for {cache_key}: {str(e)}") from e

    def _create_instance_with_factory(self, action_class: type) -> Any:
        """
        Create tool instance using factory pattern.

        This matches the original implementation exactly for compatibility.
        """
        from app.agents.tools.factories.registry import ClientFactoryRegistry

        try:
            # Get factory
            factory = ClientFactoryRegistry.get_factory(self.app_name)

            if factory:
                # Get retrieval service and config service
                retrieval_service = self.state.get("retrieval_service")
                if retrieval_service and hasattr(retrieval_service, 'config_service'):
                    config_service = retrieval_service.config_service
                    state_logger = self.state.get("logger")

                    # Get connector instance ID
                    connector_instance_id = self._get_connector_instance_id()

                    if connector_instance_id and state_logger:
                        state_logger.info(f"Using connector instance ID {connector_instance_id} for {self.app_name}")
                    elif state_logger:
                        state_logger.debug(f"No connector instance ID found for {self.app_name}, using default config")

                    # Prepare tool state with connector instance ID
                    if isinstance(self.state, dict):
                        tool_state = dict(self.state)
                    else:
                        tool_state = self.state

                    if not isinstance(tool_state, dict):
                        tool_state = {"connector_instance_id": connector_instance_id}
                    else:
                        tool_state["connector_instance_id"] = connector_instance_id

                    # Create client using factory
                    client = factory.create_client_sync(config_service, state_logger, tool_state, connector_instance_id)
                    return action_class(client)

            raise ValueError(f"Not able to get the client from factory for {self.app_name}")

        except Exception as e:
            state_logger = self.state.get("logger")
            if state_logger:
                state_logger.warning(f"Factory creation failed for {self.app_name}: {e}")
            raise

    def _get_connector_instance_id(self) -> Optional[str]:
        """
        Get connector instance ID for this tool based on app_name.

        Uses tool_to_connector_map from state.
        """
        tool_to_connector_map = self.state.get("tool_to_connector_map")
        if not tool_to_connector_map:
            return None

        # Normalize app_name and search in map
        app_name_normalized = self.app_name.replace(" ", "").lower()
        connector_id = tool_to_connector_map.get(app_name_normalized) or tool_to_connector_map.get(self.app_name)

        return connector_id


class ToolLoader:
    """
    Simple tool loader with caching.

    Handles loading tools from registry, filtering, and caching
    for optimal performance.
    """

    @staticmethod
    def load_tools(state: ChatState) -> List[ToolWrapper]:
        """
        Load all available tools with caching.

        This is the main entry point for getting tools. It handles:
        1. Caching for performance
        2. Tool filtering based on user configuration
        3. Blocking failed tools to prevent loops

        Args:
            state: Chat state

        Returns:
            List of tool wrappers ready to use
        """
        state_logger = state.get("logger")

        # Check cache and blocked tools
        cached_tools = state.get("_cached_agent_tools")
        cached_blocked_tools = state.get("_cached_blocked_tools", {})

        # Get recently failed tools
        blocked_tools = _get_recently_failed_tools(state, state_logger)

        # If cache exists and blocked tools haven't changed, return cached
        if cached_tools is not None and blocked_tools == cached_blocked_tools:
            if state_logger:
                state_logger.debug(f"Using cached tools ({len(cached_tools)} tools)")
            return cached_tools

        # Cache miss or blocked tools changed - rebuild
        if state_logger:
            if cached_tools is not None:
                state_logger.warning("Blocked tools changed - rebuilding tool cache")
            else:
                state_logger.info("First tool load - building cache")

        # Get all registry tools
        registry_tools = _global_tools_registry.get_all_tools()

        if state_logger:
            state_logger.info(f"Loading {len(registry_tools)} tools from global registry")

        # Get user configuration
        user_enabled_tools = state.get("tools", None)

        if user_enabled_tools is not None and len(user_enabled_tools) == 0:
            user_enabled_tools = None
            if state_logger:
                state_logger.info("Empty tools list detected - loading ALL available tools")

        # Load and filter tools
        tools = []
        for full_name, registry_tool in registry_tools.items():
            try:
                # Parse name
                if '.' not in full_name:
                    app_name = "default"
                    tool_name = full_name
                else:
                    app_name, tool_name = full_name.split('.', 1)

                # Check if should include
                should_include = _should_include_tool(
                    full_name,
                    tool_name,
                    app_name,
                    user_enabled_tools
                )

                # Block tools that have recently failed
                if should_include and full_name in blocked_tools:
                    if state_logger:
                        state_logger.warning(f"Blocking tool: {full_name} (recently failed {blocked_tools[full_name]} times)")
                    should_include = False

                if should_include:
                    wrapper = ToolWrapper(app_name, tool_name, registry_tool, state)
                    tools.append(wrapper)

            except Exception as e:
                if state_logger:
                    state_logger.error(f"Failed to add tool {full_name}: {e}")
                continue

        # Initialize tool state
        _initialize_tool_state(state)

        # Cache tools and blocked list
        state["_cached_agent_tools"] = tools
        state["_cached_blocked_tools"] = blocked_tools.copy()
        state["available_tools"] = [t.name for t in tools]

        if state_logger:
            state_logger.info(f"Cached {len(tools)} tools for future iterations")
            if blocked_tools:
                state_logger.warning(f"Blocked {len(blocked_tools)} tools due to recent failures: {list(blocked_tools.keys())}")

        return tools

    @staticmethod
    def get_tool_by_name(tool_name: str, state: ChatState) -> Optional[ToolWrapper]:
        """
        Get specific tool by name.

        Args:
            tool_name: Full tool name (e.g., 'slack.send_message')
            state: Chat state

        Returns:
            Tool wrapper or None if not found
        """
        registry_tools = _global_tools_registry.get_all_tools()

        # Direct match
        if tool_name in registry_tools:
            app_name, name = tool_name.split('.', 1)
            return ToolWrapper(app_name, name, registry_tools[tool_name], state)

        # Search by suffix
        for full_name, registry_tool in registry_tools.items():
            if full_name.endswith(f".{tool_name}"):
                app_name, name = full_name.split('.', 1)
                return ToolWrapper(app_name, name, registry_tool, state)

        return None


# Helper functions

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
        logger.info(f"Identified {len(blocked_tools)} tools to block based on recent failures")

    return blocked_tools


def _should_include_tool(
    full_tool_name: str,
    tool_name: str,
    app_name: str,
    user_enabled_tools: Optional[List[str]]
) -> bool:
    """
    Determine if a tool should be included.

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
    """
    Check if a tool is essential.

    Args:
        full_tool_name: Full tool name

    Returns:
        True if tool is essential
    """
    essential_patterns = ["calculator.", "web_search", "get_current_datetime"]
    return any(pattern in full_tool_name for pattern in essential_patterns)


def _initialize_tool_state(state: ChatState) -> None:
    """
    Initialize tool-related state variables.

    Args:
        state: Chat state object
    """
    state.setdefault("tool_results", [])
    state.setdefault("all_tool_results", [])


def get_tool_results_summary(state: ChatState) -> str:
    """
    Get a summary of all tool results with enhanced progress tracking.
    Fully generic - dynamically groups tools by their prefix/category.

    Args:
        state: Chat state

    Returns:
        Summary string of all tool executions
    """
    all_results = state.get("all_tool_results", [])
    if not all_results:
        return "No tools have been executed yet."

    summary = f"Tool Execution Summary (Total: {len(all_results)}):\n"
    tool_summary = _build_tool_summary(all_results)

    # Dynamic categorization - extract categories from tool names
    tool_categories = {}

    for tool_name, stats in tool_summary.items():
        # Extract category from tool name (e.g., "slack.send_message" -> "slack")
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

                # Generic guidance based on tool verb/action
                if stats["success"] > 0:
                    tool_action = tool_name.split('.')[-1] if '.' in tool_name else tool_name

                    # Generic guidance based on action type
                    if any(verb in tool_action.lower() for verb in ["fetch", "get", "list", "retrieve"]):
                        summary += "  - Data retrieved successfully - can be used for further actions\n"
                    elif any(verb in tool_action.lower() for verb in ["create", "send", "post", "add"]):
                        summary += "  - Action completed successfully\n"
                    elif any(verb in tool_action.lower() for verb in ["update", "modify", "edit"]):
                        summary += "  - Update completed successfully\n"

    return summary


def _build_tool_summary(all_results: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """
    Build summary statistics for tools.

    Args:
        all_results: List of all tool results

    Returns:
        Dictionary of tool statistics
    """
    tool_summary: Dict[str, Dict[str, Any]] = {}

    for result in all_results:
        tool_name = result.get("tool_name", "unknown")
        status = result.get("status", "unknown")

        if tool_name not in tool_summary:
            tool_summary[tool_name] = {
                "success": 0,
                "error": 0,
                "results": []
            }

        if status in tool_summary[tool_name]:
            tool_summary[tool_name][status] += 1
        tool_summary[tool_name]["results"].append(result)

    return tool_summary


def _format_tool_stats(tool_name: str, stats: Dict[str, Any]) -> str:
    """
    Format statistics for a single tool.

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
        result_preview = result_str[:150]  # Preview limit

        if len(result_str) > 150:
            result_preview += "..."

        summary += f"  - Last result: {result_preview}\n"

    return summary


# Simple API functions for easy use

def get_agent_tools(state: ChatState) -> List[ToolWrapper]:
    """
    Get all agent tools (cached).

    This is the main function to call when you need tools.
    It's simple, fast, and handles all complexity internally.

    Args:
        state: Chat state
    Returns:
        List of tools ready to use
    """
    return ToolLoader.load_tools(state)


def get_tool_by_name(tool_name: str, state: ChatState) -> Optional[ToolWrapper]:
    """
    Get a specific tool by name.

    Args:
        tool_name: Tool name (e.g., 'slack.send_message')
        state: Chat state

    Returns:
        Tool or None if not found
    """
    return ToolLoader.get_tool_by_name(tool_name, state)


def get_recently_failed_tools(state: ChatState, logger_instance=None) -> dict:
    """
    Get tools that have recently failed multiple times.

    Public API for external use.
    Args:
        state: Chat state
        logger_instance: Optional logger instance

    Returns:
        Dict mapping tool_name to failure count
    """
    return _get_recently_failed_tools(state, logger_instance)


def clear_tool_cache(state: ChatState) -> None:
    """
    Clear tool cache.
    Call this if you need to reload tools (rare).
    Args:
        state: Chat state
    """
    state.pop("_cached_agent_tools", None)
    state.pop("_tool_instance_cache", None)
    logger.info("Tool cache cleared")

