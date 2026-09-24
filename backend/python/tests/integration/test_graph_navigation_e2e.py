"""Graph Navigation E2E test.

Tests the complete navigation stack against a fully seeded in-memory fixture
that simulates a realistic Jira hierarchy:

    App(JIRA)
      └─ RecordGroup("Alpha Project")
           └─ Record(EPIC  "Build Payment Gateway")
                ├─ Record(STORY "Implement OAuth", has LINKED_TO a Confluence page)
                │    ├─ Record(TASK  "Write unit tests")
                │    └─ Record(TASK  "Review PR", attachment: Record(FILE "review.pdf"))
                └─ Record(STORY "Add retry logic")

Exercises:
- Root navigation (lists apps)
- App → RecordGroup listing
- RecordGroup → Records listing (Epic)
- Epic → Story listing (children)
- Story → Task listing with breadcrumbs carrying node IDs
- Pagination (limit is floored at 50; page 2 omits the path header)
- lookup_record by URL and by bare issue key
- Permission boundary: cross-org node produces same output as not-found
- Record ID round-trip: IDs surfaced by navigate() usable in further navigate()

Run with:
    pytest tests/integration/test_graph_navigation_e2e.py -v
"""

from __future__ import annotations

import re
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from app.agents.actions.knowledge_graph.knowledge_graph import KnowledgeGraph
from app.config.constants.arangodb import Connectors, OriginTypes
from app.connectors.sources.localKB.api.knowledge_hub_models import (
    KnowledgeHubNodesResponse,
    NodeItem,
    PaginationInfo as KHPaginationInfo,
)
from app.models.entities import ItemType, Record, RecordType, Status, TicketRecord

# Jira issue-type subTypes used by the seeded fixture below map to a real
# `TicketRecord` (RecordType.TICKET + ItemType), same as production Jira
# records — RecordType has no "EPIC"/"STORY"/"TASK" member of its own; those
# are `ItemType` values that live on a TICKET-typed record's `type` field.
_JIRA_ITEM_TYPES = {"EPIC": ItemType.EPIC, "STORY": ItemType.STORY, "TASK": ItemType.TASK}


def _node_to_record(node: dict) -> Record:
    """Build a real `Record`/`TicketRecord` from a seeded node dict.

    Provider lookups (`get_record_by_weburl`/`get_record_by_issue_key`/
    `get_record_by_external_id`) return this Pydantic model in production —
    a camelCase-attributed `MagicMock` here would mask the exact
    snake_case/camelCase field-mapping bug this fixture is the regression
    guard for (see `resolver.py::_record_to_match`).
    """
    sub_type = node.get("subType")
    connector_value = node.get("connector") or "JIRA"
    common = dict(
        id=node["id"],
        record_name=node.get("name", ""),
        external_record_id=node.get("externalRecordId", ""),
        version=1,
        origin=OriginTypes.CONNECTOR,
        connector_name=Connectors(connector_value),
        connector_id=f"{connector_value.lower()}-conn",
        weburl=node.get("webUrl"),
        indexing_status=node.get("indexingStatus", "COMPLETED"),
    )
    if sub_type in _JIRA_ITEM_TYPES:
        return TicketRecord(
            **common,
            record_type=RecordType.TICKET,
            type=_JIRA_ITEM_TYPES[sub_type],
            status=Status.IN_PROGRESS,
        )
    return Record(**common, record_type=RecordType(sub_type))


# ---------------------------------------------------------------------------
# Seeded in-memory graph provider
# ---------------------------------------------------------------------------


class SeededGraphProvider:
    """Minimal IGraphDBProvider-shaped stub backed by in-memory dicts.

    Only implements the methods actually called by navigator.py and resolver.py.
    """

    def __init__(self, user_key: str, org_id: str) -> None:
        self._user_key = user_key
        self._org_id = org_id
        self._nodes: dict[str, dict[str, Any]] = {}
        self._edges: list[tuple[str, str, str]] = []
        # id → list of ancestor dicts (breadcrumbs)
        self._breadcrumbs: dict[str, list[dict]] = {}

    def add_node(self, node: dict) -> "SeededGraphProvider":
        self._nodes[node["id"]] = node
        return self

    def add_edge(self, from_id: str, to_id: str, rel_type: str) -> "SeededGraphProvider":
        self._edges.append((from_id, to_id, rel_type))
        return self

    def set_breadcrumbs(self, node_id: str, crumbs: list[dict]) -> "SeededGraphProvider":
        self._breadcrumbs[node_id] = crumbs
        return self

    # --- IGraphDBProvider methods used by navigator / resolver ---

    async def get_knowledge_hub_node_access(
        self,
        node_id: str,
        user_key: str,
        org_id: str,
        folder_mime_types: list[str],
        transaction: str | None = None,
    ) -> dict[str, Any] | None:
        node = self._nodes.get(node_id)
        if not node:
            return None
        if node.get("orgId") != org_id:
            return None  # treat cross-org as missing (no info leak)
        if not node.get("_accessible", True):
            return None  # permission denied → same as missing
        return {k: v for k, v in node.items() if not k.startswith("_")}

    async def get_linked_records(
        self,
        record_id: str,
        org_id: str,
        user_key: str,
        relation_types: list[str],
        limit: int = 10,
        transaction: str | None = None,
    ) -> list[dict[str, Any]]:
        linked = []
        for from_id, to_id, rel_type in self._edges:
            if rel_type not in relation_types:
                continue
            if from_id != record_id and to_id != record_id:
                continue
            other_id = to_id if from_id == record_id else from_id
            other = self._nodes.get(other_id)
            if not other or other.get("orgId") != org_id:
                continue
            if not other.get("_accessible", True):
                continue
            linked.append({
                "id": other["id"],
                "name": other.get("name", ""),
                "recordType": other.get("subType"),
                "connectorName": other.get("connector"),
                "webUrl": other.get("webUrl"),
                "relationshipType": rel_type,
                "hasChildren": False,
                "indexingStatus": other.get("indexingStatus"),
                "userRole": "READER",
            })
            if len(linked) >= limit:
                break
        return linked

    async def get_knowledge_hub_breadcrumbs(
        self, node_id: str, transaction: str | None = None
    ) -> list[dict] | None:
        return self._breadcrumbs.get(node_id, [])

    async def get_record_by_weburl(
        self,
        weburl: str,
        org_id: str | None = None,
        transaction: str | None = None,
    ):
        for node in self._nodes.values():
            if node.get("webUrl") == weburl:
                if org_id and node.get("orgId") != org_id:
                    continue
                if not node.get("_accessible", True):
                    return None
                return _node_to_record(node)
        return None

    async def get_record_by_issue_key(
        self,
        connector_id: str,
        issue_key: str,
        transaction: str | None = None,
    ):
        for node in self._nodes.values():
            if node.get("externalRecordId", "").upper() == issue_key.upper():
                if node.get("orgId") != self._org_id:
                    continue
                if not node.get("_accessible", True):
                    return None
                return _node_to_record(node)
        return None

    async def get_user_by_user_id(self, user_id: str):
        return {"_key": self._user_key}

    async def get_record_by_id(self, record_id: str, transaction: str | None = None):
        """Typed record for the node the navigator just authorized — this is
        what carries the ticket status/type that the node-access projection
        does not."""
        node = self._nodes.get(record_id)
        if not node or node.get("orgId") != self._org_id:
            return None
        if node.get("nodeType") not in ("record", "folder"):
            return None
        return _node_to_record(node)

    async def get_record_by_external_id(self, connector_id: str, external_id: str, transaction=None):
        for node in self._nodes.values():
            if str(node.get("externalRecordId", "")) == str(external_id):
                if node.get("orgId") != self._org_id:
                    continue
                if not node.get("_accessible", True):
                    return None
                return _node_to_record(node)
        return None

    async def get_knowledge_hub_filter_options(self, user_key: str, org_id: str):
        """Return all app-type nodes as filter options."""
        apps = []
        for node in self._nodes.values():
            if node.get("nodeType") == "app" and node.get("orgId") == org_id:
                apps.append({
                    "id": node["id"],
                    "name": node.get("name", ""),
                    "type": node.get("subType", node.get("connector", "")),
                })
        return {"apps": apps}

    async def find_slack_burst_record_by_ts(self, *args, **kwargs):
        return None


# ---------------------------------------------------------------------------
# Seeded graph factory
# ---------------------------------------------------------------------------

_JIRA_CRUMB = {"id": "app-jira", "name": "Jira", "nodeType": "app", "subType": "JIRA"}
_ALPHA_CRUMB = {"id": "rg-alpha", "name": "Alpha Project", "nodeType": "recordGroup", "subType": "PROJECT"}
_EPIC_CRUMB = {"id": "rec-epic", "name": "Build Payment Gateway", "nodeType": "record", "subType": "EPIC"}
_OAUTH_CRUMB = {"id": "rec-story-oauth", "name": "Implement OAuth", "nodeType": "record", "subType": "STORY"}


def _build_seeded_graph(org_id: str) -> SeededGraphProvider:
    p = SeededGraphProvider(user_key="u1", org_id=org_id)

    p.add_node({"id": "app-jira", "name": "Jira", "nodeType": "app", "subType": "JIRA",
                "connector": "JIRA", "orgId": org_id, "_accessible": True})

    p.add_node({"id": "rg-alpha", "name": "Alpha Project", "nodeType": "recordGroup",
                "subType": "PROJECT", "connector": "JIRA", "orgId": org_id, "_accessible": True})

    p.add_node({"id": "rec-epic", "name": "Build Payment Gateway", "nodeType": "record",
                "subType": "EPIC", "connector": "JIRA", "orgId": org_id,
                "webUrl": "https://example.atlassian.net/browse/ALPHA-1",
                "externalRecordId": "ALPHA-1", "indexingStatus": "COMPLETED",
                "_accessible": True})

    p.add_node({"id": "rec-story-oauth", "name": "Implement OAuth", "nodeType": "record",
                "subType": "STORY", "connector": "JIRA", "orgId": org_id,
                "webUrl": "https://example.atlassian.net/browse/ALPHA-2",
                "externalRecordId": "ALPHA-2", "indexingStatus": "COMPLETED",
                "_accessible": True})

    p.add_node({"id": "rec-story-retry", "name": "Add retry logic", "nodeType": "record",
                "subType": "STORY", "connector": "JIRA", "orgId": org_id,
                "webUrl": "https://example.atlassian.net/browse/ALPHA-3",
                "externalRecordId": "ALPHA-3", "indexingStatus": "COMPLETED",
                "_accessible": True})

    p.add_node({"id": "rec-task-tests", "name": "Write unit tests", "nodeType": "record",
                "subType": "TASK", "connector": "JIRA", "orgId": org_id,
                "externalRecordId": "ALPHA-4", "indexingStatus": "COMPLETED",
                "_accessible": True})

    p.add_node({"id": "rec-task-review", "name": "Review PR", "nodeType": "record",
                "subType": "TASK", "connector": "JIRA", "orgId": org_id,
                "externalRecordId": "ALPHA-5", "indexingStatus": "COMPLETED",
                "_accessible": True})

    p.add_node({"id": "rec-file-review", "name": "review.pdf", "nodeType": "record",
                "subType": "FILE", "connector": "JIRA", "orgId": org_id,
                "indexingStatus": "COMPLETED", "_accessible": True})

    p.add_node({"id": "rec-conf-page", "name": "OAuth Design Doc", "nodeType": "record",
                "subType": "CONFLUENCE_PAGE", "connector": "CONFLUENCE", "orgId": org_id,
                "webUrl": "https://example.atlassian.net/wiki/spaces/TEAM/pages/123456",
                "externalRecordId": "123456", "indexingStatus": "COMPLETED",
                "_accessible": True})

    # Cross-org node (must not be reachable from this org)
    p.add_node({"id": "rec-secret", "name": "Secret Record", "nodeType": "record",
                "subType": "STORY", "connector": "JIRA", "orgId": "other-org",
                "externalRecordId": "SECRET-1", "_accessible": True})

    # Edges
    p.add_edge("rec-story-oauth", "rec-conf-page", "LINKED_TO")

    # Breadcrumbs (ancestor chain for each node, root excluded)
    p.set_breadcrumbs("rg-alpha", [_JIRA_CRUMB])
    p.set_breadcrumbs("rec-epic", [_JIRA_CRUMB, _ALPHA_CRUMB])
    p.set_breadcrumbs("rec-story-oauth", [_JIRA_CRUMB, _ALPHA_CRUMB, _EPIC_CRUMB])
    p.set_breadcrumbs("rec-story-retry", [_JIRA_CRUMB, _ALPHA_CRUMB, _EPIC_CRUMB])
    p.set_breadcrumbs("rec-task-tests", [_JIRA_CRUMB, _ALPHA_CRUMB, _EPIC_CRUMB, _OAUTH_CRUMB])
    p.set_breadcrumbs("rec-task-review", [_JIRA_CRUMB, _ALPHA_CRUMB, _EPIC_CRUMB, _OAUTH_CRUMB])

    return p


# ---------------------------------------------------------------------------
# KnowledgeHubService.get_nodes patch
# ---------------------------------------------------------------------------


def _make_node_item(n: dict) -> NodeItem:
    return NodeItem(
        id=n["id"],
        name=n["name"],
        nodeType=n.get("nodeType", "record"),
        parentId=n.get("parentId"),
        origin="CONNECTOR",
        connector=n.get("connector"),
        recordType=n.get("subType") if n.get("nodeType") == "record" else None,
        recordGroupType=n.get("subType") if n.get("nodeType") == "recordGroup" else None,
        indexingStatus=n.get("indexingStatus"),
        createdAt=n.get("createdAt", 0),
        updatedAt=n.get("updatedAt", 0),
        webUrl=n.get("webUrl"),
        hasChildren=n.get("hasChildren", False),
    )


# Static hierarchy used by _fake_get_nodes
_HIERARCHY: dict[str | None, list[dict]] = {
    None: [
        {"id": "app-jira", "name": "Jira", "nodeType": "app", "connector": "JIRA",
         "subType": "JIRA", "hasChildren": True},
    ],
    "app-jira": [
        {"id": "rg-alpha", "name": "Alpha Project", "nodeType": "recordGroup",
         "parentId": "app-jira", "connector": "JIRA", "subType": "PROJECT",
         "hasChildren": True},
    ],
    "rg-alpha": [
        {"id": "rec-epic", "name": "Build Payment Gateway", "nodeType": "record",
         "parentId": "rg-alpha", "connector": "JIRA", "subType": "EPIC",
         "hasChildren": True},
    ],
    "rec-epic": [
        {"id": "rec-story-oauth", "name": "Implement OAuth", "nodeType": "record",
         "parentId": "rec-epic", "connector": "JIRA", "subType": "STORY",
         "hasChildren": True,
         # 2026-06-01T00:00:00Z — older story, outside the July window
         # `TestNavigateTimeFiltering` filters for below.
         "createdAt": 1780272000000},
        {"id": "rec-story-retry", "name": "Add retry logic", "nodeType": "record",
         "parentId": "rec-epic", "connector": "JIRA", "subType": "STORY",
         "hasChildren": False,
         # 2026-07-15T00:00:00Z — inside the July window.
         "createdAt": 1784073600000},
    ],
    "rec-story-oauth": [
        {"id": "rec-task-tests", "name": "Write unit tests", "nodeType": "record",
         "parentId": "rec-story-oauth", "connector": "JIRA", "subType": "TASK",
         "hasChildren": False},
        {"id": "rec-task-review", "name": "Review PR", "nodeType": "record",
         "parentId": "rec-story-oauth", "connector": "JIRA", "subType": "TASK",
         "hasChildren": True},
    ],
    "rec-task-review": [
        {"id": "rec-file-review", "name": "review.pdf", "nodeType": "record",
         "parentId": "rec-task-review", "connector": "JIRA", "subType": "FILE",
         "hasChildren": False},
    ],
}


def _patch_kh_service():
    """Patch KnowledgeHubService.get_nodes to return from the seeded hierarchy."""

    async def _fake_get_nodes(
        self_svc,
        user_id: str,
        org_id: str,
        parent_id: str | None = None,
        parent_type: str | None = None,
        only_containers: bool = False,
        page: int = 1,
        limit: int = 50,
        sort_by: str = "updatedAt",
        sort_order: str = "desc",
        q: str | None = None,
        node_types: list[str] | None = None,
        record_types: list[str] | None = None,
        origins: list[str] | None = None,
        connector_ids: list[str] | None = None,
        indexing_status: list[str] | None = None,
        created_at: dict | None = None,
        updated_at: dict | None = None,
        size: dict | None = None,
        flattened: bool | None = None,
        include: list[str] | None = None,
        record_group_ids: list[str] | None = None,
        **kwargs,
    ) -> KnowledgeHubNodesResponse:
        children = list(_HIERARCHY.get(parent_id, []))
        if q:
            children = [c for c in children if q.lower() in c["name"].lower()]
        # Mimics the real AQL/Cypher `FILTER created_expr >= @gte AND
        # created_expr <= @lte` — see `IGraphDBProvider.get_knowledge_hub_search`
        # in both arango_http_provider.py and neo4j_provider.py.
        if created_at:
            gte, lte = created_at.get("gte"), created_at.get("lte")
            children = [
                c for c in children
                if (gte is None or c.get("createdAt", 0) >= gte)
                and (lte is None or c.get("createdAt", 0) <= lte)
            ]
        if updated_at:
            gte, lte = updated_at.get("gte"), updated_at.get("lte")
            children = [
                c for c in children
                if (gte is None or c.get("updatedAt", 0) >= gte)
                and (lte is None or c.get("updatedAt", 0) <= lte)
            ]
        total = len(children)
        offset = (page - 1) * limit
        page_items = children[offset: offset + limit]
        total_pages = max(1, (total + limit - 1) // limit) if total else 1
        has_next = (offset + limit) < total

        items = [_make_node_item(n) for n in page_items]
        pagination = KHPaginationInfo(
            page=page,
            limit=limit,
            totalItems=total,
            totalPages=total_pages,
            hasNext=has_next,
            hasPrev=(page > 1),
        )
        return KnowledgeHubNodesResponse(
            success=True,
            items=items,
            pagination=pagination,
        )

    return patch(
        "app.agents.actions.knowledge_graph.navigator.KnowledgeHubService.get_nodes",
        new=_fake_get_nodes,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

ORG = "org-e2e"
USER_ID = "user-e2e"


def _short_id_for(state: dict, full_id: str) -> str:
    """The short "R<n>" label `RecordIdShortener` assigned `full_id` in a
    prior navigate()/lookup_record() call on this `state` — TEMPORARY
    token-savings experiment (see `RecordIdShortener` in
    `utils/chat_helpers.py`). `get_or_create_short_id` is idempotent, so
    this returns the existing mapping rather than minting a new one, as
    long as `full_id` was already surfaced once via a tool call."""
    shortener = state.get("record_id_shortener")
    assert shortener is not None, "record_id_shortener not set on state — call a KG tool first"
    return shortener.get_or_create_short_id(full_id)


@pytest.fixture
def provider():
    return _build_seeded_graph(ORG)


@pytest.fixture
def state(provider):
    return {
        "org_id": ORG,
        "user_id": USER_ID,
        "graph_provider": provider,
        "logger": MagicMock(),
        "agent_knowledge": [
            {"connectorId": "app-jira", "type": "JIRA"},
        ],
        # Opt into the RecordIdShortener (disabled by default — see
        # `ChatQuery.enableRecordIdShortening`) so this suite continues to
        # exercise the shortened-id path it was written against.
        "enable_record_id_shortening": True,
    }


# ---------------------------------------------------------------------------
# Navigation walk
# ---------------------------------------------------------------------------


class TestNavigationWalk:
    @pytest.mark.asyncio
    async def test_root_lists_apps(self, state, provider):
        with _patch_kh_service():
            tool = KnowledgeGraph(state=state)
            success, text = await tool.navigate(node_id=None)

        assert success
        assert "Jira" in text
        assert _short_id_for(state, "app-jira") in text

    @pytest.mark.asyncio
    async def test_app_to_record_group(self, state, provider):
        with _patch_kh_service():
            tool = KnowledgeGraph(state=state)
            success, text = await tool.navigate(node_id="app-jira")

        assert success
        assert "Alpha Project" in text
        assert _short_id_for(state, "rg-alpha") in text

    @pytest.mark.asyncio
    async def test_record_group_to_epic(self, state, provider):
        with _patch_kh_service():
            tool = KnowledgeGraph(state=state)
            success, text = await tool.navigate(node_id="rg-alpha")

        assert success
        assert "Build Payment Gateway" in text
        assert _short_id_for(state, "rec-epic") in text

    @pytest.mark.asyncio
    async def test_epic_to_stories_with_breadcrumbs(self, state, provider):
        with _patch_kh_service():
            tool = KnowledgeGraph(state=state)
            success, text = await tool.navigate(node_id="rec-epic")

        assert success
        assert "Implement OAuth" in text
        assert _short_id_for(state, "rec-story-oauth") in text
        assert "Add retry logic" in text
        assert "Alpha Project" in text  # breadcrumb present (by name — id is shortened)

    @pytest.mark.asyncio
    async def test_story_has_linked_to_related(self, state, provider):
        with _patch_kh_service():
            tool = KnowledgeGraph(state=state)
            success, text = await tool.navigate(node_id="rec-story-oauth")

        assert success
        assert _short_id_for(state, "rec-task-tests") in text
        # LINKED_TO Confluence page in related section
        assert "OAuth Design Doc" in text

    @pytest.mark.asyncio
    async def test_breadcrumb_contains_all_ancestor_ids(self, state, provider):
        with _patch_kh_service():
            tool = KnowledgeGraph(state=state)
            success, text = await tool.navigate(node_id="rec-story-oauth")

        assert success
        # Must show path from root down to the epic
        assert "Build Payment Gateway" in text
        assert "Alpha Project" in text


class TestNavigateTimeFiltering:
    """navigate(created_after=..., ...) end-to-end: `rec-story-oauth` was
    seeded with a June creation date and `rec-story-retry` with a July one
    (see `_HIERARCHY["rec-epic"]`) — a July-only filter must surface only
    the latter, and its row must show the source creation date."""

    @pytest.mark.asyncio
    async def test_created_after_filters_out_older_sibling(self, state, provider):
        with _patch_kh_service():
            tool = KnowledgeGraph(state=state)
            success, text = await tool.navigate(node_id="rec-epic", created_after="2026-07-01")

        assert success
        assert "Add retry logic" in text
        assert "Implement OAuth" not in text

    @pytest.mark.asyncio
    async def test_created_before_filters_out_newer_sibling(self, state, provider):
        with _patch_kh_service():
            tool = KnowledgeGraph(state=state)
            success, text = await tool.navigate(node_id="rec-epic", created_before="2026-06-30")

        assert success
        assert "Implement OAuth" in text
        assert "Add retry logic" not in text

    @pytest.mark.asyncio
    async def test_output_includes_created_date_on_matching_row(self, state, provider):
        with _patch_kh_service():
            tool = KnowledgeGraph(state=state)
            success, text = await tool.navigate(node_id="rec-epic", created_after="2026-07-01")

        assert success
        assert "| created: 2026-07-15" in text

    @pytest.mark.asyncio
    async def test_no_time_filter_shows_both_siblings(self, state, provider):
        """Regression guard: an unfiltered call is unaffected by the seeded
        createdAt values — both stories still show up."""
        with _patch_kh_service():
            tool = KnowledgeGraph(state=state)
            success, text = await tool.navigate(node_id="rec-epic")

        assert success
        assert "Implement OAuth" in text
        assert "Add retry logic" in text

    @pytest.mark.asyncio
    async def test_sparse_time_filtered_result_carries_retry_hint(self, state, provider):
        """A window matching only one of two children (< 3 rows) should
        nudge the model to widen the range or drop the filter."""
        with _patch_kh_service():
            tool = KnowledgeGraph(state=state)
            success, text = await tool.navigate(node_id="rec-epic", created_after="2026-07-01")

        assert success
        assert "widen" in text.lower()
        assert "retry without time filters" in text.lower()

    @pytest.mark.asyncio
    async def test_window_matching_neither_sibling_is_empty_with_hint(self, state, provider):
        # A past-but-later date than both seeded siblings (not in the
        # future, so it doesn't hit `parse_time_range`'s not-future guard).
        with _patch_kh_service():
            tool = KnowledgeGraph(state=state)
            success, text = await tool.navigate(node_id="rec-epic", created_after="2026-07-20")

        assert success
        assert "Implement OAuth" not in text
        assert "Add retry logic" not in text
        assert "widen" in text.lower()

    @pytest.mark.asyncio
    async def test_invalid_date_returns_error_and_does_not_navigate(self, state, provider):
        with _patch_kh_service():
            tool = KnowledgeGraph(state=state)
            success, text = await tool.navigate(node_id="rec-epic", created_after="not-a-date")

        assert not success
        assert "ISO 8601" in text


class TestPagination:
    @pytest.mark.asyncio
    async def test_sub_floor_limit_still_returns_all_seeded_children(self, state, provider):
        """navigate() floors `limit` at 50, so a 2-child epic fits on page 1
        even when the caller asks for limit=1."""
        with _patch_kh_service():
            tool = KnowledgeGraph(state=state)
            success, text = await tool.navigate(node_id="rec-epic", page=1, limit=1)

        assert success
        assert "Implement OAuth" in text
        assert "Add retry logic" in text

    @pytest.mark.asyncio
    async def test_second_page_shows_remainder(self, state, provider):
        with _patch_kh_service():
            tool = KnowledgeGraph(state=state)
            _, p1 = await tool.navigate(node_id="rec-epic", page=1, limit=1)
            _, p2 = await tool.navigate(node_id="rec-epic", page=2, limit=1)

        combined = p1 + p2
        assert "Implement OAuth" in combined
        assert "Add retry logic" in combined

    @pytest.mark.asyncio
    async def test_delta_page_omits_path_header(self, state, provider):
        with _patch_kh_service():
            tool = KnowledgeGraph(state=state)
            _, p2 = await tool.navigate(node_id="rec-epic", page=2, limit=1)

        # Page 2 output must not repeat the full "Path:" breadcrumb header
        assert "Path:" not in p2


class TestLookup:
    @pytest.mark.asyncio
    async def test_lookup_by_jira_url(self, state, provider):
        tool = KnowledgeGraph(state=state)
        success, text = await tool.lookup_record(
            identifiers=["https://example.atlassian.net/browse/ALPHA-1"]
        )

        assert success
        assert _short_id_for(state, "rec-epic") in text
        assert "Build Payment Gateway" in text

    @pytest.mark.asyncio
    async def test_lookup_by_bare_key(self, state, provider):
        tool = KnowledgeGraph(state=state)
        success, text = await tool.lookup_record(identifiers=["ALPHA-2"])

        assert success
        assert _short_id_for(state, "rec-story-oauth") in text
        assert "Implement OAuth" in text

    @pytest.mark.asyncio
    async def test_lookup_not_found_returns_not_found_text(self, state, provider):
        tool = KnowledgeGraph(state=state)
        success, text = await tool.lookup_record(identifiers=["ALPHA-9999"])

        assert "Not found" in text or "no access" in text.lower()


class TestLookupEnrichedFields:
    @pytest.mark.asyncio
    async def test_lookup_epic_returns_external_id_and_status(self, state, provider):
        """`lookup_record("ALPHA-1")` must surface the epic's External ID
        and ticket Status — the enrichment `Record.to_llm_context()` (via
        `TicketRecord`) provides, not just a bare Record ID."""
        tool = KnowledgeGraph(state=state)
        success, text = await tool.lookup_record(identifiers=["ALPHA-1"])

        assert success
        assert _short_id_for(state, "rec-epic") in text
        assert "External ID" in text
        assert "ALPHA-1" in text
        assert "* Status" in text

    @pytest.mark.asyncio
    async def test_lookup_printed_record_id_accepted_verbatim_by_navigate(self, state, provider):
        """The exact Record ID value lookup_record prints — a short "R<n>"
        label, see `RecordIdShortener` — must resolve with navigate() with
        no further transformation: the tool-composition contract the
        `Next:` hint promises."""
        tool = KnowledgeGraph(state=state)
        _, lookup_text = await tool.lookup_record(identifiers=["ALPHA-1"])

        m = re.search(r"Record ID\s*:\s*(\S+)", lookup_text)
        assert m, f"No Record ID line found in: {lookup_text}"
        record_id = m.group(1)
        assert record_id == _short_id_for(state, "rec-epic")

        with _patch_kh_service():
            success, nav_text = await tool.navigate(node_id=record_id)

        assert success
        assert "Build Payment Gateway" in nav_text


class TestResolveEpicThenNavigateChildrenWalk:
    @pytest.mark.asyncio
    async def test_resolve_epic_then_navigate_reveals_stories_and_subtasks(self, state, provider):
        """The user-scenario walk this whole feature targets: resolve a
        Jira epic by key, then use navigate() on its Record ID to reach its
        stories, and one story's Record ID to reach its sub-tasks — all
        with usable Record IDs, no manual ID surgery required."""
        tool = KnowledgeGraph(state=state)

        _, lookup_text = await tool.lookup_record(identifiers=["ALPHA-1"])
        epic_short = _short_id_for(state, "rec-epic")
        assert epic_short in lookup_text
        assert f'Next: navigate(node_id="{epic_short}")' in lookup_text

        with _patch_kh_service():
            success, epic_children_text = await tool.navigate(node_id="rec-epic")
        assert success
        assert "Implement OAuth" in epic_children_text
        assert _short_id_for(state, "rec-story-oauth") in epic_children_text
        assert "Add retry logic" in epic_children_text

        with _patch_kh_service():
            success, story_children_text = await tool.navigate(node_id="rec-story-oauth")
        assert success
        assert "Write unit tests" in story_children_text
        assert _short_id_for(state, "rec-task-tests") in story_children_text
        assert "Review PR" in story_children_text
        assert _short_id_for(state, "rec-task-review") in story_children_text

    @pytest.mark.asyncio
    async def test_navigating_the_epic_shows_its_status_and_the_read_step(self, state, provider):
        """"What is the status of the SaaS launch epic" has to be answerable
        from the walk itself: the epic's own ticket fields are in the output,
        and the tool that reads its content is named with a callable id."""
        tool = KnowledgeGraph(state=state)

        with _patch_kh_service():
            success, text = await tool.navigate(node_id="rec-epic")

        assert success
        assert "* Status: IN_PROGRESS" in text
        assert "* Type: EPIC" in text
        # The read step names the epic AND the stories the walk just surfaced:
        # a listing hands back their ids and metadata and nothing they say, so
        # a hint covering only the epic left the walk with no way to read on.
        epic_short = _short_id_for(state, "rec-epic")
        story_short = _short_id_for(state, "rec-story-oauth")
        read_hint = text.split("Next: ")[-1]
        assert "knowledgegraph__fetch_record(record_ids=[" in read_hint
        assert f'"{epic_short}"' in read_hint
        assert f'"{story_short}"' in read_hint

    @pytest.mark.asyncio
    async def test_the_walk_unlocks_reading_every_record_it_surfaced(self, state, provider):
        """The walk is only useful if its Record IDs become readable —
        `hooks/citations.py` registers `dynamic_fetch_full_record` off this
        set, and nothing in a lookup/navigate path populates the retrieval
        map that used to be the sole trigger."""
        tool = KnowledgeGraph(state=state)

        await tool.lookup_record(identifiers=["ALPHA-1"])
        with _patch_kh_service():
            await tool.navigate(node_id="rec-epic")
            await tool.navigate(node_id="rec-story-oauth")

        known = state["known_record_ids"]
        assert {"rec-epic", "rec-story-oauth", "rec-task-tests", "rec-task-review"} <= known
        assert not state.get("virtual_record_id_to_result")


class TestPermissionBoundary:
    @pytest.mark.asyncio
    async def test_cross_org_lookup_same_as_not_found(self, state, provider):
        """Secret-1 is in a different org — must be indistinguishable from missing."""
        tool = KnowledgeGraph(state=state)
        _, text_denied = await tool.lookup_record(identifiers=["SECRET-1"])
        _, text_missing = await tool.lookup_record(identifiers=["NONEXISTENT-9999"])

        assert "Not found" in text_denied or "no access" in text_denied.lower()
        assert "Not found" in text_missing or "no access" in text_missing.lower()

    @pytest.mark.asyncio
    async def test_permission_denied_navigate_same_as_missing(self, state, provider):
        """navigate(node_id=<denied>) must produce empty/not-found output."""
        provider._nodes["rec-story-oauth"]["_accessible"] = False
        with _patch_kh_service():
            tool = KnowledgeGraph(state=state)
            success, text = await tool.navigate(node_id="rec-story-oauth")

        assert (not success) or (
            "not found" in text.lower()
            or "no access" in text.lower()
            or "no items" in text.lower()
            or not text.strip()
        )


class TestRecordIdRoundTrip:
    @pytest.mark.asyncio
    async def test_ids_from_navigate_are_bare_keys(self, state, provider):
        """IDs in navigate() output must not have 'records/' collection prefix."""
        with _patch_kh_service():
            tool = KnowledgeGraph(state=state)
            success, text = await tool.navigate(node_id="rec-epic")

        assert success
        assert "records/" not in text
        assert _short_id_for(state, "rec-story-oauth") in text  # bare key present

    @pytest.mark.asyncio
    async def test_lookup_id_can_be_fed_into_navigate(self, state, provider):
        """An ID returned by lookup_record() passes straight to navigate()."""
        tool = KnowledgeGraph(state=state)
        _, lookup_text = await tool.lookup_record(identifiers=["ALPHA-1"])
        assert _short_id_for(state, "rec-epic") in lookup_text

        with _patch_kh_service():
            success, nav_text = await tool.navigate(node_id="rec-epic")

        assert success
        assert "Build Payment Gateway" in nav_text


# ---------------------------------------------------------------------------
# Cross-connector resolution (RC2 fix)
# ---------------------------------------------------------------------------


@pytest.fixture
def cross_connector_state(provider):
    """Chatbot-mode state scoped to CONFLUENCE only — no agent_knowledge."""
    # Agent scope filtered to Confluence, but Jira records should still be
    # resolvable via lookup_record (lookup is org-wide, not agent-scoped).
    return {
        "org_id": ORG,
        "user_id": USER_ID,
        "graph_provider": provider,
        "has_knowledge": True,
        "available_connectors": [
            {"id": "app-conf", "name": "Confluence Connector", "type": "CONFLUENCE"},
            {"id": "app-jira", "name": "Jira Cloud", "type": "JIRA"},
        ],
        # No agent_knowledge — chat mode without per-agent config
        "enable_record_id_shortening": True,
    }


class TestCrossConnectorResolution:
    @pytest.mark.asyncio
    async def test_jira_key_resolves_from_confluence_scoped_session(self, cross_connector_state, provider):
        """A Jira issue key extracted from a Confluence page must resolve even
        when the session has no agent_knowledge (chatbot mode)."""
        tool = KnowledgeGraph(state=cross_connector_state)
        success, text = await tool.lookup_record(identifiers=["ALPHA-2"])

        assert success, f"Expected success but got: {text}"
        assert _short_id_for(cross_connector_state, "rec-story-oauth") in text
        assert "Implement OAuth" in text

    @pytest.mark.asyncio
    async def test_confluence_page_id_resolves_by_external_id(self, cross_connector_state, provider):
        """Confluence page URL → external id lookup → correct record."""
        tool = KnowledgeGraph(state=cross_connector_state)
        url = "https://example.atlassian.net/wiki/spaces/TEAM/pages/123456"
        success, text = await tool.lookup_record(identifiers=[url])

        assert success, f"Expected success but got: {text}"
        assert _short_id_for(cross_connector_state, "rec-conf-page") in text
        assert "OAuth Design Doc" in text

    @pytest.mark.asyncio
    async def test_batch_jira_and_confluence_in_one_call(self, cross_connector_state, provider):
        """Batch lookup with identifiers from different connector types."""
        tool = KnowledgeGraph(state=cross_connector_state)
        success, text = await tool.lookup_record(
            identifiers=["ALPHA-1", "https://example.atlassian.net/wiki/spaces/TEAM/pages/123456"]
        )

        assert success
        assert _short_id_for(cross_connector_state, "rec-epic") in text
        assert _short_id_for(cross_connector_state, "rec-conf-page") in text

    @pytest.mark.asyncio
    async def test_chatbot_mode_root_navigate_uses_catalog(self, cross_connector_state, provider):
        """navigate() with no agent_knowledge falls back to catalog scope."""
        with _patch_kh_service():
            tool = KnowledgeGraph(state=cross_connector_state)
            success, _text = await tool.navigate()

        # Should not error — catalog provides the scope
        assert success
