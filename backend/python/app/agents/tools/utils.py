"""
Utility functions for tool management and querying.
"""

from typing import Any, Dict, List, Optional

from app.agents.tools.config import ToolCategory
from app.agents.tools.loader import ToolLoader
from app.agents.tools.models import Tool
from app.agents.tools.registry import _global_tools_registry
from app.agents.tools.wrapper import RegistryToolWrapper
from app.modules.agents.qna.chat_state import ChatState

RESULT_PREVIEW_MAX_LENGTH = 150
MAX_TOOLS_PER_CATEGORY_DISPLAY = 5

def get_agent_tools(state: 'ChatState') -> List[RegistryToolWrapper]:
    """
    Get tools for the agent based on state configuration.

    This is the main entry point for loading tools in agents.

    Args:
        state: Chat state containing tool configuration.
               state["tools"] can be:
               - None or []: Load all tools
               - ["app_name"]: Load all tools from app
               - ["app.tool_name"]: Load specific tool
               - ["tool_name"]: Load tool by name

    Returns:
        List of wrapped tools ready for agent use

    Example:
        ```python
        # Load all tools
        state = {"tools": None}
        tools = get_agent_tools(state)

        # Load specific apps
        state = {"tools": ["slack", "jira"]}
        tools = get_agent_tools(state)

        # Load specific tools
        state = {"tools": ["slack.send_message", "jira.create_issue"]}
        tools = get_agent_tools(state)
        ```
    """
    user_filter = state.get("tools", None)
    loader = ToolLoader(state)
    return loader.load_tools(user_filter)


def get_tool_by_name(
    tool_name: str,
    state: 'ChatState'
) -> Optional[RegistryToolWrapper]:
    """
    Get a specific tool by name.

    Args:
        tool_name: Name of the tool. Can be:
                  - Full name: "app_name.tool_name"
                  - Short name: "tool_name"
        state: Chat state

    Returns:
        Tool wrapper if found, None otherwise
    """
    registry = _global_tools_registry
    all_tools = registry.get_all_tools()

    # Try exact match first
    if tool_name in all_tools:
        if "." in tool_name:
            app_name, actual_tool_name = tool_name.split(".", 1)
        else:
            app_name, actual_tool_name = "default", tool_name
        return RegistryToolWrapper(
            app_name,
            actual_tool_name,
            all_tools[tool_name],
            state
        )

    # Try partial match
    for full_name, registry_tool in all_tools.items():
        if (hasattr(registry_tool, 'tool_name') and
            (registry_tool.tool_name == tool_name or
             full_name.endswith(f".{tool_name}"))):
            if "." in full_name:
                app_name, actual_tool_name = full_name.split(".", 1)
            else:
                app_name, actual_tool_name = "default", full_name
            return RegistryToolWrapper(
                app_name,
                actual_tool_name,
                registry_tool,
                state
            )

    return None


def get_all_available_tool_names() -> Dict[str, Any]:
    """
    Get list of all available tool names from registry.

    Returns:
        Dictionary with tool names and counts organized by category
    """
    registry = _global_tools_registry
    tool_names = registry.list_tools()

    return {
        "registry_tools": tool_names,
        "total_count": len(tool_names),
        "by_category": _group_tools_by_category(),
        "statistics": registry.get_statistics()
    }


def _group_tools_by_category() -> Dict[str, List[str]]:
    """Group tools by category"""
    registry = _global_tools_registry
    by_category = {}

    for tool_name in registry.list_tools():
        metadata = registry.get_metadata(tool_name)
        if metadata:
            category = metadata.category.value
            if category not in by_category:
                by_category[category] = []
            by_category[category].append(tool_name)

    return by_category


def get_tool_results_summary(state: 'ChatState') -> str:
    """
    Get a summary of all tool execution results.

    Args:
        state: Chat state containing tool results

    Returns:
        Formatted summary string
    """
    all_results = state.get("all_tool_results", [])

    if not all_results:
        return "No tools have been executed yet."

    summary = f"Tool Execution Summary (Total: {len(all_results)}):\n"

    # Group by tool and status
    tool_summary = {}
    for result in all_results:
        tool_name = result.get("tool_name", "unknown")
        status = result.get("status", "unknown")

        if tool_name not in tool_summary:
            tool_summary[tool_name] = {
                "success": 0,
                "error": 0,
                "results": []
            }

        tool_summary[tool_name][status] = (
            tool_summary[tool_name].get(status, 0) + 1
        )
        tool_summary[tool_name]["results"].append(result)

    # Format summary
    for tool_name, stats in tool_summary.items():
        summary += f"\n{tool_name}:\n"
        summary += f"  - Successful: {stats.get('success', 0)}\n"
        summary += f"  - Failed: {stats.get('error', 0)}\n"

        # Show last result preview
        if stats["results"]:
            last_result = stats["results"][-1]
            result_preview = str(last_result.get("result", ""))[:RESULT_PREVIEW_MAX_LENGTH]
            if len(result_preview) == RESULT_PREVIEW_MAX_LENGTH:
                result_preview += "..."
            summary += f"  - Last result: {result_preview}\n"

    return summary


def search_tools(
    query: Optional[str] = None,
    category: Optional[ToolCategory] = None,
    tags: Optional[List[str]] = None,
    essential_only: bool = False
) -> List[Tool]:
    """
    Search for tools based on criteria.

    Args:
        query: Search query for name/description
        category: Filter by category
        tags: Filter by tags
        essential_only: Only return essential tools

    Returns:
        List of matching tools

    Example:
        ```python
        # Search by query
        tools = search_tools(query="email")

        # Search by category
        from app.agents.tools.config import ToolCategory
        tools = search_tools(category=ToolCategory.COMMUNICATION)

        # Combined search
        tools = search_tools(
            query="create",
            category=ToolCategory.DOCUMENTATION,
            tags=["collaborative"]
        )
        ```
    """
    return _global_tools_registry.search_tools(
        query=query,
        category=category,
        tags=tags,
        essential_only=essential_only
    )


def get_tool_usage_guidance() -> str:
    """
    Get comprehensive guidance for tool usage.

    Returns:
        Formatted guidance string with categories and examples
    """
    registry = _global_tools_registry
    all_tools = registry.list_tools()
    by_category = _group_tools_by_category()
    stats = registry.get_statistics()

    guidance = f"""
COMPREHENSIVE TOOL USAGE GUIDANCE

Total Available Tools: {len(all_tools)}
Essential Tools: {stats.get('essential_count', 0)}

TOOL CATEGORIES:
"""

    for category, tools in sorted(by_category.items()):
        guidance += f"\n{category.upper()} ({len(tools)} tools):\n"
        # Show first 5 tools of each category
        for tool in sorted(tools)[:MAX_TOOLS_PER_CATEGORY_DISPLAY]:
            guidance += f"  - {tool}\n"
        if len(tools) > MAX_TOOLS_PER_CATEGORY_DISPLAY:
            guidance += f"  ... and {len(tools) - MAX_TOOLS_PER_CATEGORY_DISPLAY} more\n"

    guidance += """

USAGE APPROACH:
1. Analyze the user's request to understand their needs
2. Choose appropriate tools from the available categories
3. Execute tools in logical sequence for multi-step tasks
4. Provide clear responses based on tool results

BEST PRACTICES:
- Use tools when they provide better or more current information
- Combine multiple tools for complex tasks
- Handle errors gracefully with alternative approaches
- Provide context about tool usage when helpful
- Essential tools are always available

FILTERING TOOLS:
You can filter which tools to use by setting state["tools"]:
- None or []: Load all tools
- ["app_name"]: Load all tools from an app
- ["app.tool_name"]: Load specific tools
- ["tool_name"]: Load tools by name

You have complete freedom to select and use tools as needed.
"""

    return guidance


def get_tool_metadata(tool_name: str) -> Optional[Dict[str, Any]]:
    """
    Get metadata for a specific tool.

    Args:
        tool_name: Full name of the tool (app_name.tool_name)

    Returns:
        Dictionary with metadata or None if not found
    """
    registry = _global_tools_registry
    metadata = registry.get_metadata(tool_name)

    if not metadata:
        return None

    return {
        "app_name": metadata.app_name,
        "tool_name": metadata.tool_name,
        "description": metadata.description,
        "category": metadata.category.value,
        "is_essential": metadata.is_essential,
        "requires_auth": metadata.requires_auth,
        "dependencies": metadata.dependencies,
        "tags": metadata.tags
    }


def list_tools_by_category(category: ToolCategory) -> List[str]:
    """
    List all tools in a specific category.

    Args:
        category: Category to filter by

    Returns:
        List of tool names in the category
    """
    registry = _global_tools_registry
    tools = registry.get_tools_by_category(category)
    return [f"{tool.app_name}.{tool.tool_name}" for tool in tools]


def list_tools_by_app(app_name: str) -> List[str]:
    """
    List all tools for a specific app.

    Args:
        app_name: Name of the application

    Returns:
        List of tool names for the app
    """
    registry = _global_tools_registry
    tools = registry.get_tools_by_app(app_name)
    return [f"{tool.app_name}.{tool.tool_name}" for tool in tools]
