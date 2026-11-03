"""`ToolsetAwareToolIndex` (`app/agent_loop_lib/tools/index.py`), the ranking behind the model's own
`search_tools` call, checked against real Linear and ClickUp tool names and first lines.

Live test: "compare my open Linear issues with my ClickUp tasks" searched
"list open Linear issues and search ClickUp tasks"; plain keyword overlap ranked Linear
`list_issue_labels` and ClickUp `search_reminders` first, left `filter_tasks` out of the top 10, and
the answer was about reminders."""

from __future__ import annotations

from typing import Any

import pytest

from app.agent_loop_lib.tools.base import Tool, ToolOutput, ToolParameter
from app.agent_loop_lib.tools.index import KeywordToolIndex, ToolsetAwareToolIndex
from app.agent_loop_lib.tools.registry import ToolRegistry

_LINEAR = [
    ("list_issues", "List issues in the user's Linear workspace, including active Triage Intelligence suggestions for issues in triage. For issues assigned to me, use \"me\" as the..."),
    ("get_issue", "Retrieve detailed information about an issue by ID, including attachments, git branch name, and active Triage Intelligence suggestions when the issue is in t..."),
    ("save_issue", "Create or update a Linear issue. If `id` is provided, updates the existing issue; otherwise creates a new one. When creating, `team` is required, and `title`..."),
    ("list_issue_labels", "List available issue labels in a Linear workspace or team. A retired label carries `retiredAt` and cannot be applied to new issues."),
    ("list_issue_statuses", "List available issue statuses in a Linear team"),
    ("list_comments", "List comments on a Linear issue, project, initiative, document, project milestone, or project/initiative status update. Provide exactly one of `issueId`, `pr..."),
    ("save_comment", "Create or update a comment on a Linear issue, project, initiative, document, project milestone, or project/initiative status update. If `id` is provided, upd..."),
    ("list_teams", "List teams in the user's Linear workspace, including their parent IDs and hierarchy names"),
    ("list_projects", "List projects in the user's Linear workspace"),
    ("list_cycles", "Retrieve cycles for a specific Linear team"),
    ("list_custom_views", "Find saved issue, project, and initiative views accessible to the user, including personal, workspace, and team views. Excludes archived views and views scop..."),
    ("list_releases", "List releases in the workspace, with optional filtering by pipeline, stage, version, and text."),
    ("list_templates", "List the Linear issue, project, and document templates available to the authenticated user. Use this to find the template a team expects, then call get_templ..."),
    ("list_users", "Retrieve users in the Linear workspace"),
    ("search_documentation", "Search Linear's documentation to learn about features and usage"),
    ("list_documents", "List documents in the user's Linear workspace"),
]
_CLICKUP = [
    ("clickup_filter_tasks", "Retrieve tasks with combined filters (tags, lists, folders, spaces, statuses, assignees, due date range, completion date range, custom field values). Multipl..."),
    ("clickup_search", "Search across all workspace content (tasks, docs, dashboards, attachments, whiteboards, chats, forms). Best for keyword/text matching across all content type..."),
    ("clickup_get_task", "Retrieve a ClickUp task by ID (supports custom IDs like 'DEV-1234'). Returns a compact summary by default — core fields are always included, large sections a..."),
    ("clickup_create_task", "Create a task in a ClickUp list. Requires name and list_id — always ask the user which list. Supports assignees (user IDs, emails, usernames, or \"me\") and ta..."),
    ("clickup_update_task", "Update task properties. Requires task_id and at least one field to change. Supports assignees (user IDs, emails, usernames, or \"me\"), custom fields as [{id, ..."),
    ("clickup_search_reminders", "Search and list your reminders. Supports filtering by type, status, completion, and since date. Date filters use YYYY-MM-DD or YYYY-MM-DD HH:MM format (e.g.,..."),
    ("clickup_create_reminder", "Create a personal reminder in your ClickUp workspace. Requires title and due_date (YYYY-MM-DD or YYYY-MM-DD HH:MM format, uses your timezone)."),
    ("clickup_get_task_comments", "Get task comments with reply_count per comment. Use clickup_get_threaded_comments for replies when reply_count > 0. Supports pagination via start/start_id."),
    ("clickup_get_workspace_hierarchy", "Get workspace hierarchy (spaces, folders, lists) with pagination and depth control. Use only when you need the workspace structure — most tools resolve names..."),
    ("clickup_get_list", "Get list details by list_id or list_name. Returns id, name, content, space info, and configured statuses. Use to resolve list names to IDs."),
    ("clickup_create_list", "Create a list in a ClickUp space. Requires name and space_name or space_id. For lists in folders, use clickup_create_list_in_folder."),
    ("clickup_get_workspace_members", "List all members in the workspace. Most tools resolve assignees automatically — use only when you need the full member list."),
    ("clickup_get_time_entries", "Get time entries with optional filtering by task, date range, assignee, and billable status. Pass task_id to scope to a single task, or omit for workspace-wi..."),
    ("clickup_get_document_pages", "Get the full content of specific pages by page ID. Use list_document_pages first to discover available page IDs."),
    ("clickup_send_chat_message", "Send a message or threaded reply to a chat channel. Provide parent_message_id for threaded replies. Supports markdown and post types."),
    ("clickup_get_chat_channels", "List chat channels in the workspace with pagination support."),
]


class _Tool(Tool):
    def __init__(self, name: str, description: str) -> None:
        self._name = name
        self._description = description

    @property
    def name(self) -> str:
        return self._name

    @property
    def short_description(self) -> str:
        return self._description

    @property
    def description(self) -> str:
        return self._description

    @property
    def path(self) -> str:
        return f"/mcp/test/{self._name}"

    @property
    def parameters(self) -> list[ToolParameter]:
        return []

    async def execute(self, **kwargs: Any) -> ToolOutput:  # noqa: ANN401
        return ToolOutput(success=True, data=self._name)


@pytest.fixture(scope="module")
def registry() -> ToolRegistry:
    """Laid out as `MCPToolProvider` registers two servers."""
    registry = ToolRegistry()
    registry.register_toolset("mcp", "Attached MCP (Model Context Protocol) servers. Call list_toolsets(toolset) ...", [])
    for prefix, group, display, tools in (("mcp_linear_", "mcp_linear", "Linear", _LINEAR), ("mcp_clickup_", "mcp_clickup", "ClickUp", _CLICKUP)):
        names = []
        for name, description in tools:
            registry.register_tool(_Tool(prefix + name, description))
            names.append(prefix + name)
        registry.register_toolset(group, f"{display} — attached MCP server tools.", names, parent="mcp")
    registry.register_tool(_Tool("web_search", "Search the web for current information"))
    registry.register_toolset("web", "Search the web and fetch pages.", ["web_search"])
    return registry


async def _names(registry: ToolRegistry, query: str, limit: int = 10, index: Any = None) -> list[str]:  # noqa: ANN401
    matches = await (index or ToolsetAwareToolIndex()).search(registry, query, limit=limit)
    return [m.summary.name for m in matches]


async def test_the_live_query_finds_issues_and_tasks_not_labels_and_reminders(registry: ToolRegistry) -> None:
    query = "list open Linear issues and search ClickUp tasks"
    before = await _names(registry, query, index=KeywordToolIndex())
    assert set(before[:2]) == {"mcp_linear_list_issue_labels", "mcp_clickup_clickup_search_reminders"}
    assert "mcp_clickup_clickup_filter_tasks" not in before

    after = await _names(registry, query)

    assert after[:2] == ["mcp_linear_list_issues", "mcp_clickup_clickup_search"]
    assert "mcp_clickup_clickup_filter_tasks" in after


async def test_named_apps_take_turns_in_the_order_the_query_names_them(registry: ToolRegistry) -> None:
    names = await _names(registry, "linear issues clickup tasks", limit=4)
    assert [name.split("_")[1] for name in names] == ["linear", "clickup", "linear", "clickup"]
    assert {"mcp_linear_list_issues", "mcp_clickup_clickup_filter_tasks"} <= set(await _names(registry, "linear issues clickup tasks"))


async def test_plurals_match_singular_names(registry: ToolRegistry) -> None:
    assert (await _names(registry, "clickup time entries", limit=1)) == ["mcp_clickup_clickup_get_time_entries"]
    assert (await _names(registry, "create clickup tasks", limit=1)) == ["mcp_clickup_clickup_create_task"]


async def test_the_app_name_alone_lists_its_tools(registry: ToolRegistry) -> None:
    names = await _names(registry, "linear", limit=50)
    assert names and all(name.startswith("mcp_linear_") for name in names)
    assert len(names) == len(_LINEAR)


async def test_an_app_name_that_matches_nothing_else_favours_nothing(registry: ToolRegistry) -> None:
    """"web" names a toolset but is also an ordinary word: only its matching tools are favoured."""
    names = await _names(registry, "search the web", limit=3)
    assert names[0] == "web_search"


async def test_without_an_app_name_it_ranks_by_the_words(registry: ToolRegistry) -> None:
    names = await _names(registry, "send chat message", limit=1)
    assert names == ["mcp_clickup_clickup_send_chat_message"]


async def test_only_stopwords_find_nothing(registry: ToolRegistry) -> None:
    assert await _names(registry, "the and of my") == []
