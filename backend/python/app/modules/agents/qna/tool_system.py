"""
Tool System

This module provides a clean, maintainable interface for tool loading,
caching, and execution. Designed for reliability, speed, and ease of understanding.

Key Features:
1. Simple tool loading with automatic caching
2. Clear error handling and retry logic
3. Instance caching for performance
4. Easy-to-understand API
"""

import logging
import re
from typing import Any, Callable, Dict, List, Optional

from langchain.tools import BaseTool
from pydantic import ConfigDict, Field

from app.agents.tools.config import ToolDiscoveryConfig
from app.agents.tools.registry import _global_tools_registry
from app.modules.agents.qna.chat_state import ChatState

logger = logging.getLogger(__name__)

# OpenAI's maximum tool limit
MAX_TOOLS_LIMIT = 128
MAX_RESULT_PREVIEW_LENGTH = 150

def sanitize_tool_name(tool_name: str) -> str:
    """
    Sanitize tool name to match the pattern: ^[a-zA-Z0-9_-]{1,128}$

    Replaces dots with underscores to comply with the pattern.

    Args:
        tool_name: Original tool name (e.g., "calendar.get_calendar_events")

    Returns:
        Sanitized tool name (e.g., "calendar_get_calendar_events")
    """
    # Replace dots with underscores
    sanitized = tool_name.replace(".", "_")

    # Ensure it matches the pattern (only alphanumeric, underscore, hyphen)
    # Remove any other invalid characters
    sanitized = re.sub(r'[^a-zA-Z0-9_-]', '_', sanitized)
    MAX_TOOL_NAME_LENGTH = 128

    # Ensure length is within limit (128 chars)
    if len(sanitized) > MAX_TOOL_NAME_LENGTH:
        sanitized = sanitized[:MAX_TOOL_NAME_LENGTH]

    return sanitized



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
    registry_tool: Any = Field(description="Original registry tool")  # noqa: ANN401
    # Store state as private attribute to avoid Pydantic validation of TypedDict
    _state: Optional[Dict[str, Any]] = None

    def __init__(self, app_name: str, tool_name: str, registry_tool: Any, state: ChatState, **kwargs) -> None:  # noqa: ANN401
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
    def _build_parameter_docs(parameters: List[Any]) -> str:  # noqa: ANN401
        """Build parameter documentation string."""
        docs = []
        for param in parameters:
            param_name = getattr(param, 'name', 'unknown')
            param_type = getattr(getattr(param, 'type', None), 'name', 'any')
            param_required = getattr(param, 'required', False)
            required_str = " (required)" if param_required else " (optional)"
            docs.append(f"  - {param_name}: {param_type}{required_str}")
        return "\n".join(docs)

    def _run(self, **kwargs) -> Any:  # noqa: ANN401
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

    async def _arun(self, **kwargs) -> Any:  # noqa: ANN401
        """Async execution (calls sync version)."""
        return self._run(**kwargs)

    @staticmethod
    def _is_class_method(func: Callable) -> bool:
        """Check if function is a class method."""
        return hasattr(func, '__qualname__') and '.' in func.__qualname__

    @staticmethod
    def _format_result(result: Any) -> str:  # noqa: ANN401
        """
        Format tool result for LLM consumption.

        Matches original implementation.
        """
        _TUPLE_RESULT_LENGTH = 2
        # Handle tuple results (success, data)
        if isinstance(result, (tuple, list)) and len(result) == _TUPLE_RESULT_LENGTH:
            success, result_data = result
            return str(result_data)

        return str(result)

    def _execute_class_method(self, func: Callable, arguments: Dict) -> Any:  # noqa: ANN401
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

    def _get_tool_instance(self, module_name: str, class_name: str, app_name: str) -> Any:  # noqa: ANN401
        """
        Get or create tool instance with caching.

        Instances are cached in state for performance.
        """
        cache_key = f"{module_name}.{class_name}_{app_name}"

        # Check cache
        instance_cache = self.state.setdefault("_tool_instance_cache", {})
        if cache_key in instance_cache:
            logger.debug(f"Using cached instance for {cache_key}")
            cached_instance = instance_cache[cache_key]
            # CRITICAL: Ensure cached instance has current state
            # This is especially important for tools like retrieval that need state
            if hasattr(cached_instance, 'set_state'):
                cached_instance.set_state(self.state)
            elif hasattr(cached_instance, 'state'):
                # Try to set state attribute directly if it exists
                try:
                    cached_instance.state = self.state
                except (AttributeError, TypeError):
                    pass
            return cached_instance

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

    def _create_instance_with_factory(self, action_class: type) -> Any:  # noqa: ANN401
        """
        Create tool instance using factory pattern, with fallback for tools that don't need factories.

        Some tools (like calculator) don't require a client/factory and can be instantiated directly.
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

                    # Find connector instance ID for this tool/app
                    connector_instance_id = self._get_connector_instance_id()

                    if connector_instance_id and state_logger:
                        state_logger.info(f"Using connector instance ID {connector_instance_id} for {self.app_name}")
                    elif state_logger:
                        state_logger.debug(f"No connector instance ID found for {self.app_name}")

                    # Prepare tool state with connector instance ID
                    if isinstance(self.state, dict):
                        tool_state = dict(self.state)
                    else:
                        tool_state = self.state

                    # Add connector instance ID to state
                    if isinstance(tool_state, dict):
                        tool_state["connector_instance_id"] = connector_instance_id

                    # Create client using factory - pass connector_instance_id explicitly
                    client = factory.create_client_sync(config_service, state_logger, tool_state, connector_instance_id)
                    return action_class(client)

            # No factory found - try fallback creation for tools that don't need clients
            # (e.g., calculator, simple utility tools)
            return self._fallback_creation(action_class)

        except Exception as e:
            state_logger = self.state.get("logger")
            if state_logger:
                state_logger.warning(f"Factory creation failed for {self.app_name}: {e}")
            # Try fallback even if factory creation failed
            try:
                return self._fallback_creation(action_class)
            except Exception:
                # If fallback also fails, raise the original error
                raise ValueError(f"Not able to get the client from factory for {self.app_name}") from e

    def _get_connector_instance_id(self) -> Optional[str]:
        """Get connector instance ID for this tool based on app_name.

        Looks up the connector instance from tool_to_connector_map in state,
        which maps app names to their connector instance IDs.

        Falls back to searching connector_instances directly if map lookup fails.

        Returns:
            Connector instance ID if found, None otherwise
        """
        state_logger = self.state.get("logger")
        app_name_normalized = self.app_name.replace(" ", "").lower()

        # First try: Use tool_to_connector_map
        tool_to_connector_map = self.state.get("tool_to_connector_map")

        if tool_to_connector_map:
            connector_id = (
                tool_to_connector_map.get(app_name_normalized) or
                tool_to_connector_map.get(self.app_name) or
                tool_to_connector_map.get(self.app_name.upper())
            )
            if connector_id:
                return connector_id

            if state_logger:
                state_logger.debug(
                    f"🔍 Map lookup failed for '{self.app_name}' (normalized: '{app_name_normalized}'). "
                    f"Available keys: {list(tool_to_connector_map.keys())}"
                )

        # Fallback: Search connector_instances directly
        connector_instances = self.state.get("connector_instances")
        if connector_instances:
            for instance in connector_instances:
                if isinstance(instance, dict):
                    connector_type = instance.get("type", "")
                    connector_type_normalized = connector_type.replace(" ", "").lower()

                    # Match by type (normalized or original)
                    if connector_type_normalized == app_name_normalized or connector_type.lower() == self.app_name.lower():
                        connector_id = instance.get("id")
                        if connector_id:
                            if state_logger:
                                state_logger.debug(f"✅ Found connector ID via fallback search: {connector_id} for {self.app_name}")
                            return connector_id

            if state_logger:
                available_types = [i.get("type", "?") for i in connector_instances if isinstance(i, dict)]
                state_logger.debug(f"🔍 Fallback search failed for '{self.app_name}'. Available types: {available_types}")
        else:
            if state_logger:
                state_logger.debug(f"🔍 No connector_instances in state for {self.app_name}")

        return None

    def _fallback_creation(self, action_class: type) -> Any:  # noqa: ANN401
        """Attempt to create instance without client (for tools that don't need factories).

        Args:
            action_class: Class to instantiate

        Returns:
            Instance of action_class
        """
        state_logger = self.state.get("logger")

        # First try: Pass state directly (required for tools like Retrieval)
        # Many tools need state to access services like retrieval_service, arango_service, etc.
        try:
            instance = action_class(state=self.state)
            if state_logger:
                state_logger.debug(f"Created {action_class.__name__} with state")
            return instance
        except TypeError:
            pass

        # Second try: no arguments (e.g., Calculator())
        try:
            instance = action_class()
            if state_logger:
                state_logger.debug(f"Created {action_class.__name__} with no args")
            return instance
        except TypeError:
            pass

        # Third try: dict argument
        try:
            instance = action_class({})
            if state_logger:
                state_logger.debug(f"Created {action_class.__name__} with empty dict")
            return instance
        except (TypeError, Exception):
            pass

        # Fourth try: None argument
        try:
            instance = action_class(None)
            if state_logger:
                state_logger.debug(f"Created {action_class.__name__} with None")
            return instance
        except Exception:
            pass

        # If all fail, raise informative error
        raise ValueError(
            f"Could not instantiate {action_class.__name__} - "
            f"tried: (state=...), (), ({{}}), (None). Tool may require a client/factory."
        )

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
        # NOTE: We return cached registry tools here, but fetch_full_record_tool
        # is added dynamically in get_agent_tools() since it depends on state
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
        use_essential_only = False

        # When tools list is empty, use only essential tools instead of all tools
        if user_enabled_tools is not None and len(user_enabled_tools) == 0:
            use_essential_only = True
            if state_logger:
                state_logger.info("Empty tools list detected - loading ONLY essential tools (not all tools)")

        # Helper function to check if tool is essential
        def _is_essential_tool(full_name: str) -> bool:
            """Check if a tool matches essential tool patterns."""
            for pattern in ToolDiscoveryConfig.ESSENTIAL_TOOL_PATTERNS:
                # Handle patterns with and without trailing dot
                pattern_clean = pattern.rstrip('.')
                if pattern_clean in full_name or full_name.startswith(pattern_clean + '.'):
                    return True
            return False

        # Load and filter tools
        essential_tools = []
        other_tools = []

        for full_name, registry_tool in registry_tools.items():
            try:
                # Parse name
                if '.' not in full_name:
                    app_name = "default"
                    tool_name = full_name
                else:
                    app_name, tool_name = full_name.split('.', 1)

                # Check if should include based on user configuration
                should_include = _should_include_tool(
                    full_name,
                    tool_name,
                    app_name,
                    user_enabled_tools if not use_essential_only else None
                )

                # If using essential only, filter to only essential tools
                if use_essential_only and should_include:
                    if not _is_essential_tool(full_name):
                        should_include = False

                # Block tools that have recently failed
                if should_include and full_name in blocked_tools:
                    if state_logger:
                        state_logger.warning(f"Blocking tool: {full_name} (recently failed {blocked_tools[full_name]} times)")
                    should_include = False

                if should_include:
                    wrapper = ToolWrapper(app_name, tool_name, registry_tool, state)
                    # Categorize as essential or other for prioritization
                    if _is_essential_tool(full_name):
                        essential_tools.append(wrapper)
                    else:
                        other_tools.append(wrapper)

            except Exception as e:
                if state_logger:
                    state_logger.error(f"Failed to add tool {full_name}: {e}")
                continue

        # Combine tools: essential first, then others (for prioritization when limiting)
        tools = essential_tools + other_tools

        # Apply OpenAI's tool limit (max 128 tools)
        if len(tools) > MAX_TOOLS_LIMIT:
            if state_logger:
                state_logger.warning(
                    f"⚠️ Tool limit exceeded: {len(tools)} tools found, limiting to {MAX_TOOLS_LIMIT} "
                    f"(keeping all {len(essential_tools)} essential tools + {MAX_TOOLS_LIMIT - len(essential_tools)} others)"
                )
            # Keep all essential tools, then limit others
            tools = essential_tools + other_tools[:MAX_TOOLS_LIMIT - len(essential_tools)]

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
    FAILURE_THRESHOLD = 3  # Block if failed N+ times (increased to give agent more chances to fix errors)

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
    essential_patterns = [
        "calculator.",
        "web_search",
        "get_current_datetime",
        "retrieval.",  # Match all retrieval tools
        "retrieval.search_internal_knowledge",  # Specific match
    ]
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
        result_preview = result_str[:MAX_RESULT_PREVIEW_LENGTH]  # Preview limit

        if len(result_str) > MAX_RESULT_PREVIEW_LENGTH:
            result_preview += "..."

        summary += f"  - Last result: {result_preview}\n"

    return summary


# Simple API functions for easy use

def get_agent_tools(state: ChatState) -> List[ToolWrapper]:
    """
    Get all agent tools (cached).

    This is the main function to call when you need tools.
    It's simple, fast, and handles all complexity internally.

    NOTE: Also adds dynamic tools like fetch_full_record_tool that aren't in the registry.

    Args:
        state: Chat state

    Returns:
        List of tools ready to use
    """
    # Get registry tools
    tools = ToolLoader.load_tools(state)

    # Add dynamic fetch_full_record tool (like chatbot does)
    # This tool is created dynamically based on virtual_record_id_to_result mapping
    virtual_record_id_to_result = state.get("virtual_record_id_to_result", {})
    if virtual_record_id_to_result:
        try:
            from app.utils.fetch_full_record import create_fetch_full_record_tool
            graph_provider = state.get("graph_provider")
            blob_store = state.get("blob_store")
            org_id = state.get("org_id")
            fetch_tool = create_fetch_full_record_tool(
                virtual_record_id_to_result, graph_provider, blob_store, org_id
            )
            tools.append(fetch_tool)
            state_logger = state.get("logger")
            if state_logger:
                state_logger.debug(f"✅ Added fetch_full_record_tool with {len(virtual_record_id_to_result)} records available")
        except Exception as e:
            state_logger = state.get("logger")
            if state_logger:
                state_logger.warning(f"Failed to add fetch_full_record_tool: {e}")

    # Add dynamic execute_sql_query tool for database queries
    config_service = state.get("config_service")
    if config_service:
        try:
            from app.utils.execute_query import create_execute_query_tool
            graph_provider = state.get("graph_provider")
            org_id = state.get("org_id")
            execute_query_tool = create_execute_query_tool(
                config_service=config_service,
                graph_provider=graph_provider,
                org_id=org_id,
            )
            tools.append(execute_query_tool)
            state_logger = state.get("logger")
            if state_logger:
                state_logger.debug("✅ Added execute_sql_query_tool for database queries")
        except Exception as e:
            state_logger = state.get("logger")
            if state_logger:
                state_logger.warning(f"Failed to add execute_sql_query_tool: {e}")

    return tools


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

