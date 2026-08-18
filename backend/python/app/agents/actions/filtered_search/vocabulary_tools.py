"""`FilterVocabulary` toolset: `list_filter_values` and `people_search`.

Both tools are internal (no external auth, no per-connector client) and
connector-count independent by construction — they read PipesHub's OWN
graph via `FilterVocabularyService`, so adding connector N+1 needs zero
changes here as long as its connector/data-source-entities processor
populates `RecordGroup`/`AppUserGroup`/`AppRole`/`userAppRelation` the way
every existing connector already does. This is the direct answer to "how
does the agent know projects/spaces/channels/users/groups/roles for every
app" — see the design doc's "Verified Ground Truth" section.

`describe_filter_schema` and the native-query search tools
(`search_jira_issues`, `search_confluence_content`, `search_slack_messages`)
are intentionally NOT here — they need a per-connector authenticated client
this toolset doesn't hold, so they live in the connector-count-independent
`FilteredSearch` toolset (`filtered_search/tools.py`) instead, which
resolves the right client per call via `connector_id`.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from app.agent_loop_lib.tools.base import ParameterType, Tag, ToolParameter
from app.agent_loop_lib.tools.decorators import tool
from app.agents.actions.filtered_search.connector_context import (
    get_user_key,
    resolve_connector_type,
)
from app.agents.actions.filtered_search.registry import FilterAdapterRegistry
from app.agents.actions.filtered_search.vocabulary import FilterVocabularyService
from app.connectors.core.registry.auth_builder import AuthBuilder
from app.connectors.core.registry.tool_builder import ToolsetBuilder, ToolsetCategory

if TYPE_CHECKING:
    from app.models.entities import RecordGroupType
    from app.modules.agents.qna.chat_state import ChatState

logger = logging.getLogger(__name__)

_VALID_DIMENSIONS = {"record_groups", "user_groups", "roles"}


@ToolsetBuilder("FilterVocabulary") \
    .in_group("Internal Tools") \
    .with_description(
        "Uniform vocabulary for native-app filter search: legal values for projects/spaces/"
        "channels, roles, and cross-app people lookup — the values search_jira_issues/"
        "search_confluence_content/search_slack_messages expect in their native query."
    ) \
    .with_category(ToolsetCategory.UTILITY) \
    .with_auth([AuthBuilder.type("NONE").fields([])]) \
    .as_internal() \
    .as_essential() \
    .configure(lambda builder: builder.with_icon("/assets/icons/toolsets/knowledge_hub.svg")) \
    .build_decorator()
class FilterVocabulary:
    """Cross-connector filter-vocabulary tools, backed by PipesHub's own graph."""

    def __init__(self, state: ChatState | None = None) -> None:
        self.state: ChatState | None = state

    @tool(
        path="/tools/filter_vocabulary/list_filter_values",
        short_description="List legal filter values for one connector (projects, spaces, channels, roles)",
        description=(
            "Look up the exact keys to use in search_by_filters' record_groups/roles for one "
            "connector — projects (Jira), spaces (Confluence), channels (Slack), or roles. "
            "Returns each value's display name AND the `key` token search_by_filters expects "
            "(a project/space key, a channel ID, etc — the native token differs by connector, "
            "this tool resolves that for you). Call this before search_by_filters whenever you "
            "are not already certain of the exact key from the system prompt's preloaded list.\n\n"
            "dimension='roles' may return {\"tracked\": false} for connectors that don't sync "
            "roles (e.g. Confluence Cloud) — that means the dimension does not exist there, not "
            "that it is empty."
        ),
        parameters=[
            ToolParameter(name="connector_id", type=ParameterType.STRING, description="Connector ID (from the Knowledge Sources section of the system prompt).", required=True),
            ToolParameter(name="dimension", type=ParameterType.STRING, description="One of: 'record_groups', 'user_groups', 'roles'.", required=True),
            ToolParameter(name="query", type=ParameterType.STRING, description="Optional substring filter on name/key.", required=False),
            ToolParameter(name="limit", type=ParameterType.INTEGER, description="Max results (default 50).", required=False, default=50),
        ],
        tags=[Tag(key="category", value="filtered_search"), Tag(key="type", value="read")],
    )
    async def list_filter_values(
        self,
        connector_id: str,
        dimension: str,
        query: str | None = None,
        limit: int = 50,
    ) -> tuple[bool, str]:
        if not self.state:
            return False, json.dumps({"error": "Tool state not initialized"})
        if dimension not in _VALID_DIMENSIONS:
            return False, json.dumps({"error": f"Invalid dimension {dimension!r}. Use one of {sorted(_VALID_DIMENSIONS)}."})

        graph_provider = self.state.get("graph_provider")
        org_id = self.state.get("org_id", "")
        if not graph_provider:
            return False, json.dumps({"error": "Graph provider not available"})

        connector_type = await resolve_connector_type(self.state, connector_id)
        vocab = FilterVocabularyService(graph_provider)

        try:
            if dimension == "record_groups":
                adapter_cls = FilterAdapterRegistry.get(connector_type) if connector_type else None
                group_types: list[RecordGroupType] | None = None
                if adapter_cls is not None:
                    caps = adapter_cls.capabilities()
                    group_types = caps.container_group_types or None
                entries = await vocab.record_groups(org_id, connector_id, group_types=group_types, query=query, limit=limit)
                values = [
                    {"name": e.name, "key": e.key, "external_id": e.external_id, "type": e.group_type, "is_stub": e.is_stub}
                    for e in entries
                ]
                return True, json.dumps({"dimension": dimension, "values": values, "count": len(values)})

            if dimension == "user_groups":
                values = await vocab.user_groups(org_id, connector_id)
                if query:
                    needle = query.lower()
                    values = [v for v in values if needle in (v.get("name") or "").lower()]
                return True, json.dumps({"dimension": dimension, "values": values[:limit], "count": len(values[:limit])})

            # roles
            roles = await vocab.roles(org_id, connector_id, connector_type or "")
            if roles is None:
                return True, json.dumps({"dimension": dimension, "tracked": False, "values": [], "count": 0})
            if query:
                needle = query.lower()
                roles = [r for r in roles if needle in (r.get("name") or "").lower()]
            return True, json.dumps({"dimension": dimension, "tracked": True, "values": roles[:limit], "count": len(roles[:limit])})
        except Exception as e:
            logger.exception("list_filter_values failed")
            return False, json.dumps({"error": str(e)})

    @tool(
        path="/tools/filter_vocabulary/people_search",
        short_description="Find a person's per-connector identity for use in filters",
        description=(
            "Resolve a name or email to the sourceUserId(s) needed to reference a specific "
            "person in a native query (e.g. Jira assignee/reporter, Confluence contributor, "
            "Slack from:/to:) — built on PipesHub's own "
            "User <-> App graph, not a native API call, so it works identically for every "
            "connector.\n\n"
            "Coverage is partial by design: a miss does not always mean the person doesn't "
            "exist in the app — see the connector-specific coverage note in the response "
            "(e.g. Slack Individual-scope connectors only resolve the authenticated user; "
            "Atlassian connectors skip users whose email could not be resolved)."
        ),
        parameters=[
            ToolParameter(name="query", type=ParameterType.STRING, description="Name or email to search for.", required=True),
            ToolParameter(name="connector_ids", type=ParameterType.ARRAY, description="Restrict to these connector IDs; omit to search all filter-capable connectors.", required=False, items={"type": "string"}),
        ],
        tags=[Tag(key="category", value="filtered_search"), Tag(key="type", value="read")],
    )
    async def people_search(
        self,
        query: str,
        connector_ids: list[str] | None = None,
    ) -> tuple[bool, str]:
        if not self.state:
            return False, json.dumps({"error": "Tool state not initialized"})

        graph_provider = self.state.get("graph_provider")
        org_id = self.state.get("org_id", "")
        if not graph_provider:
            return False, json.dumps({"error": "Graph provider not available"})

        from app.agents.actions.knowledge_graph.catalog import ConnectorCatalog

        user_key = await get_user_key(graph_provider, self.state.get("user_id", ""))
        if not user_key:
            return False, json.dumps({"error": "User not found"})

        catalog = await ConnectorCatalog.build(self.state, graph_provider=graph_provider, user_key=user_key, org_id=org_id)
        targets = [
            c for c in catalog.connectors
            if FilterAdapterRegistry.is_registered(c.type) and (not connector_ids or c.id in connector_ids)
        ]

        vocab = FilterVocabularyService(graph_provider)
        results: dict[str, Any] = {}
        for connector in targets:
            people = await vocab.people(org_id, connector.id, query=query)
            adapter_cls = FilterAdapterRegistry.get(connector.type)
            coverage_note = adapter_cls.capabilities().people_coverage_note if adapter_cls else None
            results[connector.id] = {
                "connector_name": connector.name,
                "connector_type": connector.type,
                "matches": [
                    {"display_name": p.display_name, "email": p.email, "source_user_id": p.source_user_id}
                    for p in people
                ],
                "coverage_note": coverage_note,
            }

        return True, json.dumps({"query": query, "results": results})


__all__ = ["FilterVocabulary"]
