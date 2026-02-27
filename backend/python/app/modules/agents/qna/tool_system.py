"""
Tool System - Clean and Maintainable

Clean, maintainable interface for tool loading and execution.
Uses RegistryToolWrapper for consistent tool execution.

Key features:
1. Clearer separation: internal tools (always) + user toolsets (configured)
2. Better caching with proper invalidation
3. Simplified tool loading logic
4. Better error handling and logging
5. SECURITY: Strictly respects filtered tools - no toolset-level matching
"""

import logging
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Optional, Set, Tuple, Union

if TYPE_CHECKING:
    from langchain_core.language_models.chat_models import BaseChatModel

    from app.agents.tools.models import Tool

from app.agents.tools.registry import _global_tools_registry
from app.agents.tools.wrapper import RegistryToolWrapper
from app.modules.agents.qna.chat_state import ChatState

logger = logging.getLogger(__name__)

# Constants
MAX_TOOLS_LIMIT = 128
MAX_RESULT_PREVIEW_LENGTH = 150
FAILURE_LOOKBACK_WINDOW = 7
FAILURE_THRESHOLD = 3


# ============================================================================
# LLM-Aware Tool Name Sanitization
# ============================================================================

def _requires_sanitized_tool_names(llm: Optional['BaseChatModel']) -> bool:
    """
    Check if the LLM requires sanitized tool names (dots replaced with underscores).
    Only Anthropic models require sanitized names due to their tool name format restrictions.
    Other LLMs (OpenAI, Gemini, etc.) support dots in tool names.

    Args:
        llm: The LLM instance to check (can be None)

    Returns:
        True if tool names should be sanitized, False otherwise
    """
    if not llm:
        return False

    try:
        from langchain_anthropic import ChatAnthropic
        return isinstance(llm, ChatAnthropic)
    except ImportError:
        # If langchain_anthropic is not available, assume no sanitization needed
        return False
    except Exception:
        # If any error occurs, default to no sanitization (safer for other LLMs)
        return False


def _sanitize_tool_name_if_needed(tool_name: str, llm: Optional['BaseChatModel']) -> str:
    """
    Sanitize tool name only if the LLM requires it.

    Args:
        tool_name: Original tool name (may contain dots)
        llm: LLM instance to check requirements (can be None)

    Returns:
        Sanitized name (dots replaced with underscores) if needed, original name otherwise
    """
    if _requires_sanitized_tool_names(llm):
        return tool_name.replace('.', '_')
    return tool_name


# ============================================================================
# Tool Loading - Clean and Simple
# ============================================================================

class ToolLoader:
    """Clean tool loader with smart caching"""

    @staticmethod
    def load_tools(state: ChatState) -> List[RegistryToolWrapper]:
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

        has_knowledge = bool(state.get("kb") or state.get("apps") or state.get("agent_knowledge"))

        # Check cache validity
        cached_tools = state.get("_cached_agent_tools")
        cached_blocked = state.get("_cached_blocked_tools", {})
        cached_has_knowledge = state.get("_cached_has_knowledge", None)
        blocked_tools = _get_blocked_tools(state)

        # Cache is valid only when BOTH blocked_tools AND has_knowledge are unchanged
        cache_valid = (
            cached_tools is not None
            and blocked_tools == cached_blocked
            and cached_has_knowledge == has_knowledge
        )

        # Return cached tools if valid
        if cache_valid:
            if state_logger:
                state_logger.debug(f"⚡ Using cached tools ({len(cached_tools)} tools)")
            return cached_tools

        # Cache miss or invalidated - rebuild
        if state_logger:
            if cached_tools:
                if cached_has_knowledge != has_knowledge:
                    state_logger.info(
                        f"🔄 has_knowledge changed ({cached_has_knowledge} → {has_knowledge}) "
                        "— rebuilding tool cache so retrieval tool is included/excluded correctly"
                    )
                else:
                    state_logger.info("🔄 Blocked tools changed - rebuilding cache")
            else:
                state_logger.info("📦 First tool load - building cache")

        # Load all tools
        all_tools = _load_all_tools(state, blocked_tools)

        # Initialize tool state
        _initialize_tool_state(state)

        # Cache results (now including has_knowledge so next call can detect staleness)
        state["_cached_agent_tools"] = all_tools
        state["_cached_blocked_tools"] = blocked_tools.copy()
        state["_cached_has_knowledge"] = has_knowledge
        state["available_tools"] = [t.name for t in all_tools]

        if state_logger:
            state_logger.info(f"✅ Cached {len(all_tools)} tools (has_knowledge={has_knowledge})")
            if blocked_tools:
                state_logger.warning(f"⚠️ Blocked {len(blocked_tools)} failed tools: {list(blocked_tools.keys())}")

        return all_tools

    @staticmethod
    def get_tool_by_name(tool_name: str, state: ChatState) -> Optional[RegistryToolWrapper]:
        """Get specific tool by name"""
        registry_tools = _global_tools_registry.get_all_tools()

        # Direct match
        if tool_name in registry_tools:
            app_name, name = tool_name.split('.', 1)
            return RegistryToolWrapper(app_name, name, registry_tools[tool_name], state)

        # Search by suffix
        for full_name, registry_tool in registry_tools.items():
            if full_name.endswith(f".{tool_name}"):
                app_name, name = full_name.split('.', 1)
                return RegistryToolWrapper(app_name, name, registry_tool, state)

        return None


# ============================================================================
# Helper Functions
# ============================================================================

def _load_all_tools(state: ChatState, blocked_tools: Dict[str, int]) -> List[RegistryToolWrapper]:
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

    # Check if knowledge is configured - retrieval tool is only loaded when knowledge exists
    agent_knowledge = state.get("agent_knowledge", [])
    has_knowledge = bool(agent_knowledge)

    for full_name, registry_tool in registry_tools.items():
        try:
            app_name, tool_name = _parse_tool_name(full_name)

            # Skip blocked tools
            if full_name in blocked_tools:
                if state_logger:
                    state_logger.warning(f"Blocking {full_name} (failed {blocked_tools[full_name]} times)")
                continue

            # Skip retrieval tools when no knowledge is configured
            is_retrieval = _is_retrieval_tool(full_name, registry_tool)
            if is_retrieval and not has_knowledge:
                if state_logger:
                    state_logger.debug(f"Skipping retrieval tool {full_name} - no knowledge configured")
                continue

            # Check if internal (always included)
            is_internal = _is_internal_tool(full_name, registry_tool)

            # Retrieval tools are internal when knowledge is available
            if is_retrieval and has_knowledge:
                is_internal = True

            # Check if user-enabled - ONLY exact matches, no toolset-level matching
            is_user_enabled = False
            if user_enabled_tools is not None:
                # SECURITY: Only exact full name match - no toolset-level matching
                if full_name in user_enabled_tools:
                    is_user_enabled = True

            # Load tool if internal OR user-enabled
            if is_internal:
                wrapper = RegistryToolWrapper(app_name, tool_name, registry_tool, state)
                internal_tools.append(wrapper)
                if state_logger:
                    state_logger.debug(f"Loaded internal: {full_name}")
            elif is_user_enabled:
                wrapper = RegistryToolWrapper(app_name, tool_name, registry_tool, state)
                user_tools.append(wrapper)
                if state_logger:
                    state_logger.debug(f"Loaded user tool: {full_name}")

        except Exception as e:
            if state_logger:
                state_logger.error(f"Failed to load {full_name}: {e}")

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


def _is_retrieval_tool(full_name: str, registry_tool: 'Tool') -> bool:
    """
    Check if a tool is a retrieval/RAG tool.

    Retrieval tools should only be included when knowledge is configured.
    """
    if hasattr(registry_tool, 'app_name'):
        app_name = str(registry_tool.app_name).lower()
        if app_name == 'retrieval':
            return True

    retrieval_patterns = ["retrieval."]
    return any(p in full_name.lower() for p in retrieval_patterns)


def _is_internal_tool(full_name: str, registry_tool: 'Tool') -> bool:
    """
    Check if tool is internal (always included).

    Internal tools are marked in registry with isInternal=True.
    Note: Retrieval tools are handled separately via _is_retrieval_tool.
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

    # Check app name (retrieval is NOT always internal - depends on knowledge config)
    if hasattr(registry_tool, 'app_name'):
        app_name = str(registry_tool.app_name).lower()
        if app_name in ['calculator', 'datetime', 'utility']:
            return True

    # Fallback patterns (retrieval excluded - handled separately based on knowledge)
    internal_patterns = [
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

def get_agent_tools(state: ChatState) -> List[RegistryToolWrapper]:
    """
    Get all agent tools (cached).

    Returns internal tools + user's configured toolset tools.
    Also adds dynamic tools like fetch_full_record_tool.
    """
    tools = ToolLoader.load_tools(state)

    # Add dynamic agent fetch_full_record tool
    virtual_record_map = state.get("virtual_record_id_to_result", {})
    if virtual_record_map:
        try:
            from app.utils.agent_fetch_full_record import (
                create_agent_fetch_full_record_tool,
            )
            record_label_to_uuid_map = state.get("record_label_to_uuid_map", {})
            fetch_tool = create_agent_fetch_full_record_tool(
                virtual_record_map,
                label_to_virtual_record_id=record_label_to_uuid_map if record_label_to_uuid_map else None,
            )
            tools.append(fetch_tool)

            state_logger = state.get("logger")
            if state_logger:
                state_logger.debug(f"Added agent fetch_full_record tool ({len(virtual_record_map)} records)")
        except Exception as e:
            state_logger = state.get("logger")
            if state_logger:
                state_logger.warning(f"Failed to add agent fetch_full_record tool: {e}")

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


def get_tool_by_name(tool_name: str, state: ChatState) -> Optional[RegistryToolWrapper]:
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


# ============================================================================
# Modern Tool System with Pydantic Schemas (for ReAct Agent)
# ============================================================================

def get_agent_tools_with_schemas(state: ChatState) -> List:
    """
    Convert registry tools to StructuredTools with Pydantic schemas.

    This function is used by the ReAct agent to get tools with proper
    schema validation for function calling.

    Tool names are sanitized (dots -> underscores) only for LLMs that require it
    (e.g., Anthropic). Other LLMs (OpenAI, Gemini, etc.) keep original names with dots.

    Args:
        state: Chat state containing tool configuration and LLM instance

    Returns:
        List of LangChain StructuredTool objects with Pydantic schemas
    """
    try:
        from langchain_core.tools import StructuredTool

        # Get tools from registry (RegistryToolWrapper objects)
        registry_tools = get_agent_tools(state)
        structured_tools = []

        # Get LLM from state to determine if sanitization is needed
        llm = state.get("llm")

        # Debug: Log tool count
        state_logger = state.get("logger")
        if state_logger:
            state_logger.debug(f"get_agent_tools_with_schemas: received {len(registry_tools)} tools from get_agent_tools")

        for tool_wrapper in registry_tools:
            try:
                registry_tool = tool_wrapper.registry_tool

                # Get Pydantic schema from Tool object (stored during registration from @tool decorator)
                args_schema = getattr(registry_tool, 'args_schema', None)

                # Sanitize tool name only if LLM requires it (e.g., Anthropic)
                original_tool_name = tool_wrapper.name
                sanitized_tool_name = _sanitize_tool_name_if_needed(original_tool_name, llm)

                # Create an async wrapper function that calls tool_wrapper.arun()
                # This ensures proper async execution in the same event loop as FastAPI
                def make_async_tool_func(wrapper: RegistryToolWrapper) -> Callable:
                    async def async_tool_func(**kwargs) -> Union[Tuple[bool, str], str, Dict[str, Any], List[Any]]:
                        """Async tool function that wraps RegistryToolWrapper.arun()"""
                        # Call arun with kwargs as a dict (arun handles both formats)
                        result = await wrapper.arun(kwargs)
                        # Return result as-is to preserve tuple format (bool, str) if present
                        # The tool executor in nodes.py will handle both tuple and string formats
                        return result
                    return async_tool_func

                async_tool_func = make_async_tool_func(tool_wrapper)

                # Create StructuredTool with schema if available
                # Explicitly mark as coroutine to ensure LangChain handles it correctly
                if args_schema:
                    # Use schema for validation
                    structured_tool = StructuredTool.from_function(
                        func=async_tool_func,
                        name=sanitized_tool_name,
                        description=tool_wrapper.description,
                        args_schema=args_schema,
                        coroutine=async_tool_func,  # Explicitly pass the coroutine
                    )
                else:
                    # Fallback: no schema (for legacy tools without Pydantic schemas)
                    structured_tool = StructuredTool.from_function(
                        func=async_tool_func,
                        name=sanitized_tool_name,
                        description=tool_wrapper.description,
                        coroutine=async_tool_func,  # Explicitly pass the coroutine
                    )

                # Store original name and wrapper reference for backward compatibility
                setattr(structured_tool, '_original_name', original_tool_name)
                setattr(structured_tool, '_tool_wrapper', tool_wrapper)
                structured_tools.append(structured_tool)
            except Exception as tool_error:
                # Log but continue processing other tools
                if state_logger:
                    state_logger.warning(f"Failed to create StructuredTool for {tool_wrapper.name}: {tool_error}")
                continue

        # Debug: Log final tool count
        if state_logger:
            state_logger.debug(f"get_agent_tools_with_schemas: returning {len(structured_tools)} structured tools")
            tool_names = [getattr(t, 'name', str(t)) for t in structured_tools]
            state_logger.debug(f"Structured tool names: {tool_names[:10]}")

        # Add dynamic agent fetch_full_record tool
        virtual_record_map = state.get("virtual_record_id_to_result", {})
        if virtual_record_map:
            try:
                from app.utils.agent_fetch_full_record import (
                    create_agent_fetch_full_record_tool,
                )
                record_label_to_uuid_map = state.get("record_label_to_uuid_map", {})
                fetch_tool = create_agent_fetch_full_record_tool(
                    virtual_record_map,
                    label_to_virtual_record_id=record_label_to_uuid_map if record_label_to_uuid_map else None,
                )
                structured_tools.append(fetch_tool)

                state_logger = state.get("logger")
                if state_logger:
                    state_logger.debug(f"Added agent fetch_full_record tool ({len(virtual_record_map)} records)")
            except Exception as e:
                state_logger = state.get("logger")
                if state_logger:
                    state_logger.warning(f"Failed to add agent fetch_full_record tool: {e}")

        return structured_tools
    except ImportError:
        # Fallback if langchain_core not available
        logger.warning("langchain_core.tools not available, returning regular tools")
        return get_agent_tools(state)
    except Exception as e:
        logger.error(f"Error converting tools to StructuredTools: {e}", exc_info=True)
        # Fallback to regular tools
        return get_agent_tools(state)
