"""
Tool loading and filtering logic.
"""

from typing import List, Optional, Set

from app.agents.tools.config import ToolDiscoveryConfig
from app.agents.tools.registry import _global_tools_registry
from app.agents.tools.wrapper import RegistryToolWrapper
from app.modules.agents.qna.chat_state import ChatState


class ToolLoader:
    """
    Handles loading and filtering of tools based on user configuration.
    Features:
    - Flexible filtering (by app, tool name, or full name)
    - Essential tools always included
    - Proper state initialization
    """

    def __init__(self, state: 'ChatState') -> None:
        """
        Initialize tool loader.

        Args:
            state: Chat state containing configuration
        """
        self.state = state
        self.logger = state.get("logger")
        self.registry = _global_tools_registry

    def load_tools(
        self,
        user_filter: Optional[List[str]] = None
    ) -> List[RegistryToolWrapper]:
        """
        Load tools based on user filter.

        Args:
            user_filter: Optional list of tool names/patterns to load.
                        None or empty list loads all tools.

        Returns:
            List of wrapped tools ready for agent use
        """
        # Get all available tools
        all_tools = self.registry.get_all_tools()

        if self.logger:
            self.logger.info(
                f"Loading tools from registry ({len(all_tools)} available)"
            )

        # Normalize filter
        normalized_filter = self._normalize_filter(user_filter)

        # Load and wrap tools
        loaded_tools = []
        for full_name, registry_tool in all_tools.items():
            if self._should_load_tool(full_name, normalized_filter):
                wrapper = self._create_wrapper(full_name, registry_tool)
                if wrapper:
                    loaded_tools.append(wrapper)

        # Initialize tool state
        self._initialize_tool_state(loaded_tools)

        if self.logger:
            self.logger.info(f"Loaded {len(loaded_tools)} tools for agent")
            self._log_tool_breakdown(loaded_tools)

        return loaded_tools

    def _normalize_filter(
        self,
        user_filter: Optional[List[str]]
    ) -> Optional[Set[str]]:
        """Normalize user filter to a set or None"""
        # None or empty list means load all
        if user_filter is None or len(user_filter) == 0:
            if self.logger:
                self.logger.info("No filter specified - loading ALL tools")
            return None

        return set(user_filter)

    def _should_load_tool(
        self,
        full_name: str,
        normalized_filter: Optional[Set[str]]
    ) -> bool:
        """
        Determine if tool should be loaded based on filter.

        Args:
            full_name: Full name of tool (app_name.tool_name)
            normalized_filter: Normalized filter set or None

        Returns:
            True if tool should be loaded
        """
        # No filter - load all
        if normalized_filter is None:
            return True

        # Check if tool is essential
        if ToolDiscoveryConfig.is_essential_tool(full_name):
            return True

        # Parse tool name
        if "." in full_name:
            app_name, tool_name = full_name.split(".", 1)
        else:
            app_name, tool_name = "default", full_name

        # Check against filter
        return (
            full_name in normalized_filter or      # Exact match: "slack.send_message"
            tool_name in normalized_filter or      # Tool name match: "send_message"
            app_name in normalized_filter          # App name match: "slack"
        )

    def _create_wrapper(
        self,
        full_name: str,
        registry_tool
    ) -> Optional[RegistryToolWrapper]:
        """Create a wrapper for a registry tool"""
        try:
            if "." in full_name:
                app_name, tool_name = full_name.split(".", 1)
            else:
                app_name, tool_name = "default", full_name

            wrapper = RegistryToolWrapper(
                app_name,
                tool_name,
                registry_tool,
                self.state
            )

            if self.logger:
                self.logger.debug(f"✅ Created wrapper: {full_name}")

            return wrapper

        except Exception as e:
            if self.logger:
                self.logger.error(
                    f"❌ Failed to create wrapper for {full_name}: {e}"
                )
            return None

    def _initialize_tool_state(self, tools: List[RegistryToolWrapper]) -> None:
        """Initialize tool-related state variables"""
        self.state.setdefault("tool_results", [])
        self.state.setdefault("all_tool_results", [])
        self.state.setdefault("web_search_results", [])
        self.state.setdefault("web_search_template_context", {})
        self.state["available_tools"] = [tool.name for tool in tools]

    def _log_tool_breakdown(self, tools: List[RegistryToolWrapper]) -> None:
        """Log breakdown of loaded tools by app"""
        breakdown = {}
        for tool in tools:
            if "." in tool.name:
                app = tool.name.split(".")[0]
                breakdown[app] = breakdown.get(app, 0) + 1

        if self.logger:
            self.logger.info(f"Tool breakdown by app: {breakdown}")
