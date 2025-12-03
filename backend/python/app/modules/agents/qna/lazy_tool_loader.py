from typing import Any, Dict, List, Optional


class LazyToolLoader:
    """Intelligently loads tools only when needed"""

    def __init__(self) -> None:
        self.frequently_used_tools = {
            # Tools that are used >50% of the time
            "slack.fetch_channels",
            "slack.search_messages",
            "slack.fetch_users",
            "gmail.search_emails",
            "gmail.read_email",
            "jira.search_issues",
            "jira.get_issue",
            "confluence.search_pages",
            "github.search_code",
            "google_drive.search_files"
        }

        self.tool_usage_stats = {}
        self.load_times = {}

    def should_preload_tool(self, tool_name: str) -> bool:
        """
        Determine if tool should be preloaded

        Returns:
            True if tool should be eagerly loaded
        """
        # Preload frequently used tools
        if tool_name in self.frequently_used_tools:
            return True

        # Preload tools with high usage stats
        usage_count = self.tool_usage_stats.get(tool_name, 0)
        _MIN_USAGE_FOR_PRELOAD = 5  # Used more than 5 times
        if usage_count > _MIN_USAGE_FOR_PRELOAD:
            return True

        return False

    def get_priority_tools(
        self,
        all_tools: List[str],
        user_enabled_tools: Optional[List[str]] = None,
        query: Optional[str] = None
    ) -> tuple[List[str], List[str]]:
        """
        Split tools into priority (preload) and lazy (load on demand)

        Returns:
            (priority_tools, lazy_tools) tuple
        """

        priority = []
        lazy = []

        # If user specified tools, prioritize those
        if user_enabled_tools:
            user_tools_set = set(user_enabled_tools)
            for tool in all_tools:
                if tool in user_tools_set:
                    priority.append(tool)
                else:
                    lazy.append(tool)
            return (priority, lazy)

        # Otherwise, use smart detection
        for tool in all_tools:
            if self.should_preload_tool(tool):
                priority.append(tool)
            else:
                lazy.append(tool)

        # Query-based priority boost
        if query:
            query_lower = query.lower()

            # Boost relevant tools based on query keywords
            keyword_map = {
                'slack': ['slack.'],
                'email': ['gmail.', 'outlook.'],
                'mail': ['gmail.', 'outlook.'],
                'jira': ['jira.'],
                'issue': ['jira.', 'github.'],
                'github': ['github.'],
                'code': ['github.'],
                'confluence': ['confluence.'],
                'wiki': ['confluence.'],
                'drive': ['google_drive.'],
                'file': ['google_drive.', 's3.', 'dropbox.'],
                'calendar': ['google_calendar.', 'outlook_calendar.']
            }

            for keyword, tool_prefixes in keyword_map.items():
                if keyword in query_lower:
                    # Move matching tools from lazy to priority
                    for prefix in tool_prefixes:
                        tools_to_promote = [t for t in lazy if t.startswith(prefix)]
                        for tool in tools_to_promote:
                            lazy.remove(tool)
                            if tool not in priority:
                                priority.append(tool)

        return (priority, lazy)

    def track_usage(self, tool_name: str, execution_time_ms: float) -> None:
        """Track tool usage for future optimization"""
        if tool_name not in self.tool_usage_stats:
            self.tool_usage_stats[tool_name] = 0

        self.tool_usage_stats[tool_name] += 1

        # Track load time
        if tool_name not in self.load_times:
            self.load_times[tool_name] = []
        self.load_times[tool_name].append(execution_time_ms)

        # Update frequently used set if tool is used often
        _MIN_USAGE_FOR_FREQUENT = 10
        if self.tool_usage_stats[tool_name] > _MIN_USAGE_FOR_FREQUENT:
            self.frequently_used_tools.add(tool_name)

    def get_stats(self) -> Dict[str, Any]:
        """Get usage statistics"""
        return {
            "total_tools_tracked": len(self.tool_usage_stats),
            "frequently_used_count": len(self.frequently_used_tools),
            "frequently_used_tools": list(self.frequently_used_tools),
            "top_10_tools": sorted(
                self.tool_usage_stats.items(),
                key=lambda x: x[1],
                reverse=True
            )[:10]
        }


# Global instance
_global_lazy_loader = LazyToolLoader()


def get_lazy_loader() -> LazyToolLoader:
    """Get global lazy loader instance"""
    return _global_lazy_loader

