"""
Tool System - Corrected and Improved

Clean, maintainable interface for tool loading and execution.
Maintains all working logic while improving code quality.

Key improvements:
1. Clearer separation: internal tools (always) + user toolsets (configured)
2. Better caching with proper invalidation
3. Simplified instance creation logic
4. Better error handling and logging
5. SECURITY FIX: Strictly respects filtered tools - no toolset-level matching
"""

import logging
from typing import Any, Dict, List, Optional, Set

from langchain.tools import BaseTool
from pydantic import ConfigDict, Field

from app.agents.tools.registry import _global_tools_registry
from app.modules.agents.qna.chat_state import ChatState

logger = logging.getLogger(__name__)

# Constants
MAX_TOOLS_LIMIT = 128
MAX_RESULT_PREVIEW_LENGTH = 150
FAILURE_LOOKBACK_WINDOW = 7
FAILURE_THRESHOLD = 3


# ============================================================================
# Tool Wrapper - Maintains Working Logic
# ============================================================================

class ToolWrapper(BaseTool):
    """Wrapper for registry tools with proper instance caching"""

    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        extra='allow',
        validate_assignment=False
    )

    app_name: str = Field(description="Application name (e.g., 'slack', 'jira')")
    tool_name: str = Field(description="Tool name (e.g., 'send_message')")
    registry_tool: Any = Field(description="Original registry tool")
    _state: Optional[Dict[str, Any]] = None

    def __init__(self, app_name: str, tool_name: str, registry_tool: Any, state: ChatState, **kwargs) -> None:
        """Initialize tool wrapper"""
        description = self._build_description(registry_tool, app_name, tool_name)

        super().__init__(
            name=f"{app_name}.{tool_name}",
            description=description,
            app_name=app_name,
            tool_name=tool_name,
            registry_tool=registry_tool,
            **kwargs
        )

        self._state = state

    @staticmethod
    def _build_description(registry_tool: Any, app_name: str, tool_name: str) -> str:
        """Build tool description with parameters"""
        base_desc = getattr(registry_tool, 'description', f"{app_name}.{tool_name}")

        parameters = getattr(registry_tool, 'parameters', None)
        if not parameters:
            return base_desc

        param_docs = []
        for param in parameters:
            param_name = getattr(param, 'name', 'unknown')
            param_type = getattr(getattr(param, 'type', None), 'name', 'any')
            required = " (required)" if getattr(param, 'required', False) else " (optional)"
            param_docs.append(f"  - {param_name}: {param_type}{required}")

        return f"{base_desc}\n\nParameters:\n" + "\n".join(param_docs) if param_docs else base_desc

    @property
    def state(self) -> ChatState:
        """Get chat state"""
        return self._state

    def _run(self, **kwargs) -> Any:
        """Execute the tool"""
        try:
            tool_function = self.registry_tool.function

            if self._is_class_method(tool_function):
                result = self._execute_class_method(tool_function, kwargs)
            else:
                result = tool_function(**kwargs)

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
        """Async execution"""
        return self._run(**kwargs)

    @staticmethod
    def _is_class_method(func) -> bool:
        """Check if function is a class method"""
        return hasattr(func, '__qualname__') and '.' in func.__qualname__

    def _execute_class_method(self, func, arguments: Dict) -> Any:
        """Execute class method with instance caching"""
        class_name = func.__qualname__.split('.')[0]
        module_name = func.__module__
        cache_key = f"{module_name}.{class_name}_{self.app_name}"

        instance = self._get_cached_instance(module_name, class_name, cache_key)
        method = getattr(instance, self.tool_name)
        return method(**arguments)

    def _get_cached_instance(self, module_name: str, class_name: str, cache_key: str) -> Any:
        """Get or create cached tool instance"""
        instance_cache = self.state.setdefault("_tool_instance_cache", {})

        if cache_key in instance_cache:
            logger.debug(f"Using cached instance for {cache_key}")
            instance = instance_cache[cache_key]
            self._update_instance_state(instance)
            return instance

        logger.debug(f"Creating new instance for {cache_key}")
        instance = self._create_instance(module_name, class_name)
        instance_cache[cache_key] = instance

        logger.info(f"Cached instance for {cache_key} (total: {len(instance_cache)})")
        return instance

    def _update_instance_state(self, instance: Any) -> None:
        """Update instance with current state"""
        if hasattr(instance, 'set_state'):
            instance.set_state(self.state)
        elif hasattr(instance, 'state'):
            try:
                instance.state = self.state
            except (AttributeError, TypeError):
                pass

    def _create_instance(self, module_name: str, class_name: str) -> Any:
        """Create tool instance using factory or fallback"""
        action_module = __import__(module_name, fromlist=[class_name])
        action_class = getattr(action_module, class_name)

        # Try factory first
        instance = self._try_factory_creation(action_class)
        if instance:
            return instance

        # Fallback to direct creation
        return self._try_direct_creation(action_class)

    def _try_factory_creation(self, action_class: type) -> Optional[Any]:
        """Try to create instance using factory pattern"""
        from app.agents.tools.factories.registry import ClientFactoryRegistry

        factory = ClientFactoryRegistry.get_factory(self.app_name)
        if not factory:
            return None

        try:
            retrieval_service = self.state.get("retrieval_service")
            if not retrieval_service or not hasattr(retrieval_service, 'config_service'):
                return None

            config_service = retrieval_service.config_service
            state_logger = self.state.get("logger")

            # Get toolset configuration
            toolset_id = self._get_toolset_id()
            toolset_config = self._get_toolset_config(toolset_id)

            if not toolset_config:
                if state_logger:
                    if toolset_id:
                        state_logger.warning(
                            f"No toolset config found for {self.app_name} "
                            f"(toolset ID: {toolset_id}). Tool may not be authenticated. "
                            f"Falling back to direct creation."
                        )
                    else:
                        state_logger.debug(
                            f"No toolset ID found for {self.app_name}. "
                            f"This may be an internal tool."
                        )
                return None

            # Validate toolset config has required fields
            if not toolset_config.get("isAuthenticated", False):
                if state_logger:
                    state_logger.warning(
                        f"Toolset config for {self.app_name} (ID: {toolset_id}) "
                        f"is not authenticated. Tool may fail."
                    )

            if state_logger:
                state_logger.info(f"Using toolset config for {self.app_name} (ID: {toolset_id})")

            tool_state = dict(self.state) if isinstance(self.state, dict) else self.state
            client = factory.create_client_sync(
                config_service=config_service,
                logger=state_logger,
                toolset_config=toolset_config,
                state=tool_state
            )

            return action_class(client)

        except Exception as e:
            state_logger = self.state.get("logger")
            if state_logger:
                state_logger.warning(f"Factory creation failed for {self.app_name}: {e}")
            return None

    def _try_direct_creation(self, action_class: type) -> Any:
        """Try direct instance creation for tools that don't need factories"""
        state_logger = self.state.get("logger")

        # Try with state first (most tools need this)
        try:
            instance = action_class(state=self.state)
            if state_logger:
                state_logger.debug(f"Created {action_class.__name__} with state")
            return instance
        except TypeError:
            pass

        # Try no arguments
        try:
            instance = action_class()
            if state_logger:
                state_logger.debug(f"Created {action_class.__name__} with no args")
            return instance
        except TypeError:
            pass

        # Try empty dict
        try:
            instance = action_class({})
            if state_logger:
                state_logger.debug(f"Created {action_class.__name__} with empty dict")
            return instance
        except Exception:
            pass

        # Try None
        try:
            instance = action_class(None)
            if state_logger:
                state_logger.debug(f"Created {action_class.__name__} with None")
            return instance
        except Exception:
            pass

        raise ValueError(
            f"Could not instantiate {action_class.__name__} - "
            f"tried: (state=...), (), ({{}}), (None)"
        )

    def _get_toolset_id(self) -> Optional[str]:
        """Get toolset ID for this tool"""
        tool_to_toolset_map = self.state.get("tool_to_toolset_map", {})
        toolset_id = tool_to_toolset_map.get(self.name)

        if toolset_id:
            state_logger = self.state.get("logger")
            if state_logger:
                state_logger.debug(f"Found toolset ID {toolset_id} for {self.name}")

        return toolset_id

    def _get_toolset_config(self, toolset_id: Optional[str]) -> Optional[Dict[str, Any]]:
        """Get toolset configuration from state"""
        if not toolset_id:
            return None

        toolset_configs = self.state.get("toolset_configs", {})
        return toolset_configs.get(toolset_id)

    @staticmethod
    def _format_result(result: Any) -> str:
        """Format tool result for LLM"""
        if isinstance(result, (tuple, list)) and len(result) == 2:
            success, data = result
            return str(data)
        return str(result)


# ============================================================================
# Tool Loading - Corrected Logic
# ============================================================================

class ToolLoader:
    """Clean tool loader with smart caching"""

    @staticmethod
    def load_tools(state: ChatState) -> List[ToolWrapper]:
        """
        Load tools with intelligent caching.

        Logic:
        1. Check cache validity
        2. Get internal tools (always included, marked isInternal=True in registry)
        3. Get agent's configured toolsets from state (agent_toolsets)
        4. Extract tool names from those toolsets
        5. Load tools: internal (always) + user toolsets (configured)
        6. Block recently failed tools
        7. Apply OpenAI's 128 tool limit
        8. Cache results
        """
        state_logger = state.get("logger")

        # Check cache validity
        cached_tools = state.get("_cached_agent_tools")
        cached_blocked = state.get("_cached_blocked_tools", {})
        blocked_tools = _get_blocked_tools(state)

        # Return cached tools if valid
        if cached_tools is not None and blocked_tools == cached_blocked:
            if state_logger:
                state_logger.debug(f"⚡ Using cached tools ({len(cached_tools)} tools)")
            return cached_tools

        # Cache miss or invalidated - rebuild
        if state_logger:
            if cached_tools:
                state_logger.info("🔄 Blocked tools changed - rebuilding cache")
            else:
                state_logger.info("📦 First tool load - building cache")

        # Load all tools
        all_tools = _load_all_tools(state, blocked_tools)

        # Initialize tool state
        _initialize_tool_state(state)

        # Cache results
        state["_cached_agent_tools"] = all_tools
        state["_cached_blocked_tools"] = blocked_tools.copy()
        state["available_tools"] = [t.name for t in all_tools]

        if state_logger:
            state_logger.info(f"✅ Cached {len(all_tools)} tools")
            if blocked_tools:
                state_logger.warning(f"⚠️ Blocked {len(blocked_tools)} failed tools: {list(blocked_tools.keys())}")

        return all_tools

    @staticmethod
    def get_tool_by_name(tool_name: str, state: ChatState) -> Optional[ToolWrapper]:
        """Get specific tool by name"""
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


# ============================================================================
# Helper Functions
# ============================================================================

def _load_all_tools(state: ChatState, blocked_tools: Dict[str, int]) -> List[ToolWrapper]:
    """
    Load all tools (internal + user toolsets).

    This is the core tool loading logic that:
    1. Gets agent's configured toolsets from state
    2. Extracts tool names from those toolsets
    3. Loads internal tools (always)
    4. Loads user tools (from agent's toolsets)
    5. Blocks recently failed tools
    6. Applies tool limit

    SECURITY: Strictly respects filtered tools - only loads explicitly listed tools,
    never entire toolsets.
    """
    state_logger = state.get("logger")
    registry_tools = _global_tools_registry.get_all_tools()

    # Get agent's configured toolsets
    agent_toolsets = state.get("agent_toolsets", [])

    # Extract tool names from toolsets - ONLY explicit tool names, no toolset-level matching
    user_enabled_tools = _extract_tool_names_from_toolsets(agent_toolsets)

    if state_logger:
        state_logger.info(f"Loading from {len(registry_tools)} registry tools")
        if agent_toolsets:
            state_logger.info(f"Agent has {len(agent_toolsets)} configured toolsets")
            if user_enabled_tools:
                state_logger.info(f"Extracted {len(user_enabled_tools)} tool names")
                state_logger.debug(f"Enabled tools: {sorted(user_enabled_tools)}")
            else:
                state_logger.warning("No tools extracted from toolsets - this may be a configuration issue")
        else:
            state_logger.info("No agent toolsets - loading only internal tools")

    internal_tools = []
    user_tools = []

    for full_name, registry_tool in registry_tools.items():
        try:
            app_name, tool_name = _parse_tool_name(full_name)

            # Skip blocked tools
            if full_name in blocked_tools:
                if state_logger:
                    state_logger.warning(f"Blocking {full_name} (failed {blocked_tools[full_name]} times)")
                continue

            # Check if internal (always included)
            is_internal = _is_internal_tool(full_name, registry_tool)

            # Check if user-enabled - ONLY exact matches, no toolset-level matching
            is_user_enabled = False
            if user_enabled_tools is not None:
                # SECURITY: Only exact full name match - no toolset-level matching
                if full_name in user_enabled_tools:
                    is_user_enabled = True

            # Load tool if internal OR user-enabled
            if is_internal:
                wrapper = ToolWrapper(app_name, tool_name, registry_tool, state)
                internal_tools.append(wrapper)
                if state_logger:
                    state_logger.debug(f"Loaded internal: {full_name}")
            elif is_user_enabled:
                wrapper = ToolWrapper(app_name, tool_name, registry_tool, state)
                user_tools.append(wrapper)
                if state_logger:
                    state_logger.debug(f"Loaded user tool: {full_name}")

        except Exception as e:
            if state_logger:
                state_logger.error(f"Failed to load {full_name}: {e}")

    # Combine: internal first (priority)
    tools = internal_tools + user_tools

    # Apply tool limit
    if len(tools) > MAX_TOOLS_LIMIT:
        if state_logger:
            state_logger.warning(
                f"Tool limit: {len(tools)} → {MAX_TOOLS_LIMIT} "
                f"({len(internal_tools)} internal + {MAX_TOOLS_LIMIT - len(internal_tools)} user)"
            )
        tools = internal_tools + user_tools[:MAX_TOOLS_LIMIT - len(internal_tools)]

    if state_logger:
        state_logger.info(f"✅ {len(internal_tools)} internal + {len(user_tools)} user = {len(tools)} total")

    return tools


def _extract_tool_names_from_toolsets(agent_toolsets: List[Dict]) -> Optional[Set[str]]:
    """
    Extract tool names from agent's configured toolsets.

    Returns a set of full tool names ONLY: {"googledrive.get_files_list", "slack.send_message"}

    SECURITY: Does NOT include toolset names to prevent loading all tools in a toolset
    when only specific tools are enabled. This ensures filtered tools are respected.

    Returns None if no toolsets configured
    """
    if not agent_toolsets:
        return None

    tool_names = set()

    for toolset in agent_toolsets:
        toolset_name = toolset.get("name", "").lower()
        if not toolset_name:
            continue

        # Add individual tools ONLY - no toolset-level matching for security
        tools = toolset.get("tools", [])
        for tool in tools:
            if isinstance(tool, dict):
                # Try fullName first (already has toolset.tool format)
                # Then construct from toolName or name field
                full_name = tool.get("fullName") or f"{toolset_name}.{tool.get('toolName') or tool.get('name', '')}"
                if full_name and full_name != f"{toolset_name}.":
                    tool_names.add(full_name)
            elif isinstance(tool, str):
                # If tool is just a string, it might be the full name already
                if "." in tool:
                    tool_names.add(tool)
                else:
                    tool_names.add(f"{toolset_name}.{tool}")

    return tool_names if tool_names else None


def _is_internal_tool(full_name: str, registry_tool: Any) -> bool:
    """
    Check if tool is internal (always included).

    Internal tools are marked in registry with isInternal=True.
    """
    # Check registry metadata
    if hasattr(registry_tool, 'metadata'):
        metadata = registry_tool.metadata
        if hasattr(metadata, 'category'):
            category = str(metadata.category).lower()
            if 'internal' in category:
                return True
        if hasattr(metadata, 'is_internal') and metadata.is_internal:
            return True

    # Check app name
    if hasattr(registry_tool, 'app_name'):
        app_name = str(registry_tool.app_name).lower()
        if app_name in ['retrieval', 'calculator', 'datetime', 'utility']:
            return True

    # Fallback patterns
    internal_patterns = [
        "retrieval.",
        "calculator.",
        "web_search",
        "get_current_datetime",
    ]

    return any(p in full_name.lower() for p in internal_patterns)


def _get_blocked_tools(state: ChatState) -> Dict[str, int]:
    """Get tools that recently failed multiple times"""
    all_results = state.get("all_tool_results", [])

    if not all_results or len(all_results) < FAILURE_THRESHOLD:
        return {}

    recent_results = all_results[-FAILURE_LOOKBACK_WINDOW:]

    failure_counts = {}
    for result in recent_results:
        if result.get("status") == "error":
            tool_name = result.get("tool_name", "unknown")
            failure_counts[tool_name] = failure_counts.get(tool_name, 0) + 1

    return {
        tool: count
        for tool, count in failure_counts.items()
        if count >= FAILURE_THRESHOLD
    }


def _parse_tool_name(full_name: str) -> tuple:
    """Parse tool name into (app_name, tool_name)"""
    if '.' not in full_name:
        return "default", full_name
    return full_name.split('.', 1)


def _initialize_tool_state(state: ChatState) -> None:
    """Initialize tool state"""
    state.setdefault("tool_results", [])
    state.setdefault("all_tool_results", [])


# ============================================================================
# Public API
# ============================================================================

def get_agent_tools(state: ChatState) -> List[ToolWrapper]:
    """
    Get all agent tools (cached).

    Returns internal tools + user's configured toolset tools.
    Also adds dynamic tools like fetch_full_record_tool.
    """
    tools = ToolLoader.load_tools(state)

    # Add dynamic fetch_full_record tool
    virtual_record_map = state.get("virtual_record_id_to_result", {})
    if virtual_record_map:
        try:
            from app.utils.fetch_full_record import create_fetch_full_record_tool
            fetch_tool = create_fetch_full_record_tool(virtual_record_map)
            tools.append(fetch_tool)

            state_logger = state.get("logger")
            if state_logger:
                state_logger.debug(f"Added fetch_full_record_tool ({len(virtual_record_map)} records)")
        except Exception as e:
            state_logger = state.get("logger")
            if state_logger:
                state_logger.warning(f"Failed to add fetch_full_record_tool: {e}")

    return tools


def get_tool_by_name(tool_name: str, state: ChatState) -> Optional[ToolWrapper]:
    """Get specific tool by name"""
    return ToolLoader.get_tool_by_name(tool_name, state)


def clear_tool_cache(state: ChatState) -> None:
    """Clear tool cache"""
    state.pop("_cached_agent_tools", None)
    state.pop("_tool_instance_cache", None)
    state.pop("_cached_blocked_tools", None)
    logger.info("Tool cache cleared")


def get_tool_results_summary(state: ChatState) -> str:
    """Get summary of tool execution results"""
    all_results = state.get("all_tool_results", [])
    if not all_results:
        return "No tools executed yet."

    # Group by category
    categories = {}
    for result in all_results:
        tool_name = result.get("tool_name", "unknown")
        category = tool_name.split('.')[0] if '.' in tool_name else "utility"

        if category not in categories:
            categories[category] = {"success": 0, "error": 0, "tools": {}}

        status = result.get("status", "unknown")
        if status in ("success", "error"):
            categories[category][status] += 1

        if tool_name not in categories[category]["tools"]:
            categories[category]["tools"][tool_name] = {"success": 0, "error": 0}

        if status in ("success", "error"):
            categories[category]["tools"][tool_name][status] += 1

    # Build summary
    lines = [f"Tool Execution Summary (Total: {len(all_results)}):"]

    for category, stats in sorted(categories.items()):
        lines.append(f"\n## {category.title()} Tools:")
        lines.append(f"  Success: {stats['success']}, Failed: {stats['error']}")

        for tool_name, tool_stats in stats["tools"].items():
            lines.append(f"  - {tool_name}: {tool_stats['success']} ✓, {tool_stats['error']} ✗")

    return "\n".join(lines)
