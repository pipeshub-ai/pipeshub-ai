"""`FilteredSearch` toolset: one native-query search tool per filter-capable
connector type (`search_jira_issues`, `search_confluence_content`,
`search_slack_messages`) plus `describe_filter_schema`.

Each search tool takes the connector's OWN query language — JQL, CQL, or
Slack search operators — instead of a normalized filter spec (see the
design doc's "Why the fix is a language change, not a new field"). This
keeps the per-connector cost at one adapter (`filtered_search/adapters/`)
plus one thin tool method here (~30-40 lines): the tool method resolves
the connector's client, confirms it is actually the expected connector
type, and delegates to the shared `execution.run_filtered_search` for
validation, execution, and permission gating.

This toolset is intentionally connector-count independent in the sense
that matters — it holds no per-connector client itself (unlike `Jira`/
`Confluence`/`Slack`, which each own one authenticated client for their
app). Instead, given a `connector_id` argument, it resolves whichever
client `PipesHubToolLoader` already built for that connector this request
(see `connector_context.py`) — so multiple instances of the same
connector type all work, unlike the existing native `Jira`/`Confluence`/
`Slack` toolsets, which bind a single client at construction time.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from app.agent_loop_lib.tools.base import ParameterType, Tag, ToolParameter
from app.agent_loop_lib.tools.decorators import tool
from app.agents.actions.filtered_search.adapters.confluence import ConfluenceFilterAdapter
from app.agents.actions.filtered_search.adapters.jira import JiraFilterAdapter
from app.agents.actions.filtered_search.adapters.slack import SlackFilterAdapter
from app.agents.actions.filtered_search.connector_context import (
    resolve_client_for_connector,
)
from app.agents.actions.filtered_search.execution import run_filtered_search
from app.agents.actions.filtered_search.registry import FilterAdapterRegistry
from app.connectors.core.registry.auth_builder import AuthBuilder
from app.connectors.core.registry.tool_builder import ToolsetBuilder, ToolsetCategory

if TYPE_CHECKING:
    from app.modules.agents.qna.chat_state import ChatState

logger = logging.getLogger(__name__)

# Single source of truth for these tools' paths, shared with the PRE/POST
# hooks that scope themselves to exactly these tool paths
# (`agent_loop/hooks/filter_value_resolution.py`,
# `agent_loop/hooks/filtered_retrieval.py`) and with
# `factory.py::_build_hooks`, which wires them.
SEARCH_JIRA_ISSUES_PATH = "/tools/filtered_search/search_jira_issues"
SEARCH_CONFLUENCE_CONTENT_PATH = "/tools/filtered_search/search_confluence_content"
SEARCH_SLACK_MESSAGES_PATH = "/tools/filtered_search/search_slack_messages"

FILTERED_SEARCH_TOOL_PATHS = (
    SEARCH_JIRA_ISSUES_PATH,
    SEARCH_CONFLUENCE_CONTENT_PATH,
    SEARCH_SLACK_MESSAGES_PATH,
)

# The tool_input key holding the native query string for each path — the
# PRE_TOOL_USE `filter_value_resolution` hook uses this to know which
# argument to identity-substitute without needing per-tool branching of
# its own (it still derives the adapter class from `connector_id`, same
# as always; this map only tells it WHICH argument is the query).
NATIVE_QUERY_PARAM_BY_PATH: dict[str, str] = {
    SEARCH_JIRA_ISSUES_PATH: "jql",
    SEARCH_CONFLUENCE_CONTENT_PATH: "cql",
    SEARCH_SLACK_MESSAGES_PATH: "query",
}

# Which tool renders a given adapter's connector type — used only by
# `prompt_preload.py` to point the model at the right tool name per
# connector without hardcoding a connector-type match there too.
TOOL_PATH_BY_ADAPTER: dict[type, str] = {
    JiraFilterAdapter: SEARCH_JIRA_ISSUES_PATH,
    ConfluenceFilterAdapter: SEARCH_CONFLUENCE_CONTENT_PATH,
    SlackFilterAdapter: SEARCH_SLACK_MESSAGES_PATH,
}

_NOT_FOUND_ERROR = (
    "No active, authenticated connector found for connector_id={connector_id!r}. "
    "Use the Knowledge Sources section of the system prompt, or the knowledge-graph "
    "navigation tools, to find a valid connector_id for an enabled connector."
)

_CONTENT_QUERY_PARAM = ToolParameter(
    name="content_query",
    type=ParameterType.STRING,
    description="Optional: search the TEXT of the matching records via PipesHub retrieval, "
    "permission-checked, scoped to exactly the records the native query matched. Omit for a "
    "plain filtered listing. Never put a keyword/topic in the native query itself.",
    required=False,
)
_LIMIT_PARAM = ToolParameter(
    name="limit", type=ParameterType.INTEGER, description="Max records to consider (default 50).",
    required=False, default=50,
)
_CONNECTOR_ID_PARAM = ToolParameter(
    name="connector_id", type=ParameterType.STRING,
    description="Connector ID (from the Knowledge Sources section of the system prompt).",
    required=True,
)


@ToolsetBuilder("FilteredSearch") \
    .in_group("Internal Tools") \
    .with_description(
        "Native filter search against connected apps using each app's own query language "
        "(Jira JQL, Confluence CQL, Slack search operators), with optional PipesHub content "
        "search over the matched records."
    ) \
    .with_category(ToolsetCategory.UTILITY) \
    .with_auth([AuthBuilder.type("NONE").fields([])]) \
    .as_internal() \
    .as_essential() \
    .configure(lambda builder: builder.with_icon("/assets/icons/toolsets/knowledge_hub.svg")) \
    .build_decorator()
class FilteredSearch:
    """One native-query search tool per filter-capable connector type — see
    module docstring."""

    def __init__(self, state: ChatState | None = None) -> None:
        self.state: ChatState | None = state

    async def _search(
        self,
        connector_id: str,
        expected_adapter: type,
        other_tools_hint: str,
        query: str,
        limit: int,
    ) -> tuple[bool, str]:
        if not self.state:
            return False, json.dumps({"error": "Filtered search tool state not initialized"})

        resolved = await resolve_client_for_connector(self.state, connector_id)
        if resolved is None:
            return False, json.dumps({"error": _NOT_FOUND_ERROR.format(connector_id=connector_id)})
        connector_type, client = resolved

        adapter_cls = FilterAdapterRegistry.get(connector_type)
        if adapter_cls is None:
            return False, json.dumps({
                "error": f"Connector type {connector_type!r} does not support filter search.",
            })
        if adapter_cls is not expected_adapter:
            return False, json.dumps({
                "error": f"connector_id={connector_id!r} is a {connector_type} connector, not "
                f"{expected_adapter.capabilities().connector_type} — use {other_tools_hint} instead.",
            })

        return await run_filtered_search(
            state=self.state,
            connector_id=connector_id,
            connector_type=connector_type,
            client=client,
            query=query,
            limit=limit,
        )

    @tool(
        path=SEARCH_JIRA_ISSUES_PATH,
        short_description="Search Jira issues with JQL — optionally content-searches the hits",
        description=(
            "Find Jira issues using JQL (Jira Query Language) filters ONLY — never put a "
            "keyword/topic in `jql`. This does NOT search issue content; pass `content_query` "
            "to search the TEXT of the matching issues via PipesHub's own indexed content "
            "search, permission-checked, scoped to exactly the issues `jql` matched.\n\n"
            "Use `currentUser()` for the asking user's own identity (e.g. "
            '`assignee = currentUser()`) — it is deterministically substituted with their real '
            "Jira account id, never the connector's service-account identity. Text-match "
            "operators (`~`/`!~` on text/summary/description/comment) are rejected — move that "
            "term into `content_query` instead.\n\n"
            "Call list_filter_values(connector_id, dimension='record_groups') for exact project "
            "keys and people_search for account ids, unless already known. Call "
            "describe_filter_schema(connector_id) for custom field ids (`customfield_*`).\n\n"
            "Examples:\n"
            '  jql=\'project = "ES" AND status = "In Progress" AND priority = "High"\'\n'
            "  jql='assignee = currentUser() AND priority in (\"Highest\", \"High\") AND "
            "statusCategory != Done'"
        ),
        parameters=[
            _CONNECTOR_ID_PARAM,
            ToolParameter(
                name="jql", type=ParameterType.STRING,
                description="Filter-only JQL, e.g. 'project = \"ES\" AND status = \"Open\"'. No text-match operators.",
                required=True,
            ),
            _CONTENT_QUERY_PARAM,
            _LIMIT_PARAM,
        ],
        tags=[Tag(key="category", value="filtered_search"), Tag(key="type", value="read")],
    )
    async def search_jira_issues(
        self,
        connector_id: str,
        jql: str,
        content_query: str | None = None,
        limit: int = 50,
    ) -> tuple[bool, str]:
        return await self._search(
            connector_id, JiraFilterAdapter,
            "search_confluence_content or search_slack_messages", jql, limit,
        )

    @tool(
        path=SEARCH_CONFLUENCE_CONTENT_PATH,
        short_description="Search Confluence pages/blogposts with CQL — optionally content-searches the hits",
        description=(
            "Find Confluence content using CQL (Confluence Query Language) filters ONLY — never "
            "put a keyword/topic in `cql`. This does NOT search page content; pass `content_query` "
            "to search the TEXT of the matching pages via PipesHub's own indexed content search, "
            "permission-checked, scoped to exactly the pages `cql` matched.\n\n"
            "Use `currentUser()` for the asking user's own identity (e.g. "
            '`contributor = currentUser()`) — it is deterministically substituted with their real '
            "account id, never the connector's service-account identity. Text-match operators "
            "(`~`/`!~` on siteSearch/text/title/content/comment/body) are rejected — move that "
            "term into `content_query` instead. Personal space keys (`~<accountId>`) are filter "
            "values, not text operators, and are fine to use as-is.\n\n"
            "Call list_filter_values(connector_id, dimension='record_groups') for exact space "
            "keys and people_search for account ids, unless already known. Call "
            "describe_filter_schema(connector_id) for the current label catalog.\n\n"
            "Examples:\n"
            '  cql=\'space = "ENG" AND type = "page" AND label = "runbook"\'\n'
            "  cql='contributor = currentUser() AND lastmodified >= now(\"-30d\")'"
        ),
        parameters=[
            _CONNECTOR_ID_PARAM,
            ToolParameter(
                name="cql", type=ParameterType.STRING,
                description="Filter-only CQL, e.g. 'space = \"ENG\" AND type = \"page\"'. No text-match operators.",
                required=True,
            ),
            _CONTENT_QUERY_PARAM,
            _LIMIT_PARAM,
        ],
        tags=[Tag(key="category", value="filtered_search"), Tag(key="type", value="read")],
    )
    async def search_confluence_content(
        self,
        connector_id: str,
        cql: str,
        content_query: str | None = None,
        limit: int = 50,
    ) -> tuple[bool, str]:
        return await self._search(
            connector_id, ConfluenceFilterAdapter,
            "search_jira_issues or search_slack_messages", cql, limit,
        )

    @tool(
        path=SEARCH_SLACK_MESSAGES_PATH,
        short_description="Search Slack messages with search operators — optionally content-searches the hits",
        description=(
            "Find Slack messages using search operators ONLY (`in:`, `from:`, `to:`, `before:`, "
            "`after:`, `on:`, `during:`, `has:`, `is:`) — never put free text or a quoted phrase "
            "in `query`. This does NOT search message content; pass `content_query` to search "
            "the TEXT of the matching messages via PipesHub's own indexed content search, "
            "permission-checked, scoped to exactly the messages `query` matched.\n\n"
            "Use `from:me` / `to:me` for the asking user's own identity — deterministically "
            "substituted with their real Slack member id, never the connector's own identity. "
            "Reference channels and people by ID (`in:<#C0123>`, `from:<@U0123>`), not by display "
            "name — get exact IDs from list_filter_values(dimension='record_groups') and "
            "people_search.\n\n"
            "Examples:\n"
            "  query='in:<#C0123ABCD> after:2026-07-01'\n"
            "  query='from:me has:link'"
        ),
        parameters=[
            _CONNECTOR_ID_PARAM,
            ToolParameter(
                name="query", type=ParameterType.STRING,
                description="Filter-only Slack search operators, e.g. 'in:<#C0123> after:2026-07-01'. No free text.",
                required=True,
            ),
            _CONTENT_QUERY_PARAM,
            _LIMIT_PARAM,
        ],
        tags=[Tag(key="category", value="filtered_search"), Tag(key="type", value="read")],
    )
    async def search_slack_messages(
        self,
        connector_id: str,
        query: str,
        content_query: str | None = None,
        limit: int = 50,
    ) -> tuple[bool, str]:
        return await self._search(
            connector_id, SlackFilterAdapter,
            "search_jira_issues or search_confluence_content", query, limit,
        )

    @tool(
        path="/tools/filtered_search/describe_filter_schema",
        short_description="Discover a connector's custom/discoverable fields for use directly in its native query",
        description=(
            "List custom or connector-specific filterable fields (Jira customfield_* ids and "
            "their JQL clauseNames, the Confluence label catalog) not obvious from the query "
            "language alone. Call this only when the user references a field you're not already "
            "certain about — most requests never need it."
        ),
        parameters=[_CONNECTOR_ID_PARAM],
        tags=[Tag(key="category", value="filtered_search"), Tag(key="type", value="read")],
    )
    async def describe_filter_schema(self, connector_id: str) -> tuple[bool, str]:
        if not self.state:
            return False, json.dumps({"error": "Filtered search tool state not initialized"})

        resolved = await resolve_client_for_connector(self.state, connector_id)
        if resolved is None:
            return False, json.dumps({"error": _NOT_FOUND_ERROR.format(connector_id=connector_id)})
        connector_type, client = resolved

        adapter_cls = FilterAdapterRegistry.get(connector_type)
        if adapter_cls is None:
            return False, json.dumps({"error": f"No filter adapter registered for connector type {connector_type!r}"})

        caps = adapter_cls.capabilities()
        if not caps.supports_custom_fields:
            return True, json.dumps({
                "fields": [],
                "message": f"{connector_type} has no custom fields beyond its standard query language.",
            })

        try:
            fields = await adapter_cls().discover_custom_fields(client)
        except NotImplementedError:
            return True, json.dumps({"fields": []})
        except Exception as e:
            logger.exception("describe_filter_schema failed for connector %s", connector_id)
            return False, json.dumps({"error": str(e)})

        return True, json.dumps({"fields": [f.model_dump() for f in fields]})


__all__ = [
    "FilteredSearch",
    "SEARCH_JIRA_ISSUES_PATH",
    "SEARCH_CONFLUENCE_CONTENT_PATH",
    "SEARCH_SLACK_MESSAGES_PATH",
    "FILTERED_SEARCH_TOOL_PATHS",
    "NATIVE_QUERY_PARAM_BY_PATH",
    "TOOL_PATH_BY_ADAPTER",
]
