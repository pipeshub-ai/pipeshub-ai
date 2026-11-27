"""A connected app limited to folders or files (below app level) and the grep
route: the scan runs on the apps the limit reaches and the answer keeps only
what the limit, and the request's own selection, admit. The scan finds every
record of every app it is told to run on, so what the answer keeps is the
route's own bound."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from tests.support.connection_limit_world import RECORDS, VRID_TO_RECORD, make_graph
from tests.unit.api.test_connection_limit_routes import _request


def L(apps=(), groups=(), records=()) -> dict:
    return {"apps": list(apps), "recordGroups": list(groups), "records": list(records)}


async def grep(limit, filters=None, graph=None) -> tuple:
    from app.api.routes import knowledge_search as ks
    from app.core.org_context import OrgContext, bind
    from app.modules.retrieval import selection_scope
    from app.utils.pattern_match import resolve_connector_ids_for_search

    selection_scope._memo.clear()
    graph = graph or make_graph()
    ran: list[list[str]] = []

    async def scan(**kwargs) -> list:
        apps = await resolve_connector_ids_for_search(graph, "org-1", kwargs["filters"])
        ran.append(sorted(apps))
        for v, r in VRID_TO_RECORD.items():
            if RECORDS[r][0] in apps:
                kwargs["found"][v] = {"virtual_record_id": v, "match_count": "1", "match_preview": "m"}
        return list(kwargs["found"].values())

    retrieval = MagicMock()
    retrieval.blob_store.get_record_from_storage = AsyncMock(return_value={"block_containers": {"blocks": [
        {"index": 0, "data": "marker", "code_metadata": None}]}})
    retrieval.blob_store.get_document_ids_by_virtual_record_ids = AsyncMock(return_value={})
    body = {"pattern": "marker"}
    if filters is not None:
        body["filters"] = filters
    with bind(OrgContext("org-1", "user-1", "user", "jwt", "req-1")), \
         patch.object(ks, "run_pattern_match_with_llm_grep", scan), \
         patch.object(ks, "check_pattern_match_eligible", AsyncMock(return_value=True)), \
         patch.object(ks, "resolve_frontend_url", AsyncMock(return_value=None)):
        response = await ks.grep(_request(limit), ks.GrepQuery(**body), retrieval, graph)
    scanned = {a for apps in ran for a in apps}
    return {r["recordId"] for r in json.loads(response.body)["records"]}, scanned


CASES = [
    # id, limit, request filters, expected records, apps that must never be scanned
    ("folder_limit_empty_request", L(groups=["gC"]), None, {"rC1", "rC2"}, {"app-B", "kb-1"}),
    ("folder_limit_strict_flag", L(groups=["gC"]), {"strictScope": True}, {"rC1", "rC2"}, {"app-B", "kb-1"}),
    ("sibling_of_parent_named", L(groups=["gC"]), {"recordGroups": ["gS"]}, set(), {"app-B", "kb-1"}),
    ("limit_child_request_parent", L(groups=["gC"]), {"recordGroups": ["gP"]}, {"rC1", "rC2"}, {"app-B", "kb-1"}),
    ("limit_parent_request_child", L(groups=["gP"]), {"recordGroups": ["gC"]}, {"rC1", "rC2"}, {"app-B", "kb-1"}),
    ("limit_parent_request_parent_file", L(groups=["gP"]), {"records": ["rP"]}, {"rP"}, {"app-B", "kb-1"}),
    ("file_limit_empty_request", L(records=["rC1"]), None, {"rC1"}, {"app-B", "kb-1"}),
    ("file_limit_sibling_file_named", L(records=["rC1"]), {"records": ["rC2"]}, set(), {"app-B", "kb-1"}),
    ("file_limit_sibling_exact_named", L(records=["rC1"]), {"recordsExact": ["rC2"]}, set(), {"app-B", "kb-1"}),
    ("file_limit_parent_folder_named", L(records=["rC1"]), {"recordGroups": ["gC"]}, {"rC1"}, {"app-B", "kb-1"}),
    ("folder_limit_other_app_folder_named", L(groups=["gC"]), {"recordGroups": ["gB"]}, set(), {"app-B", "kb-1"}),
    ("folder_limit_other_app_named", L(groups=["gC"]), {"apps": ["app-B"]}, set(), {"app-B", "kb-1"}),
    ("folder_limit_other_app_and_its_folder", L(groups=["gC"]), {"apps": ["app-B"], "recordGroups": ["gB"]}, set(), {"app-B", "kb-1"}),
    ("folder_limit_kb_named", L(groups=["gC"]), {"kb": ["kb-1"]}, set(), {"app-B", "kb-1"}),
    ("folder_limit_own_app_named", L(groups=["gC"]), {"apps": ["app-A"]}, {"rC1", "rC2"}, {"app-B", "kb-1"}),
    ("folder_limit_own_app_plus_other_app_folder", L(groups=["gC"]), {"apps": ["app-A"], "recordGroups": ["gB"]}, {"rC1", "rC2"}, {"app-B", "kb-1"}),
    ("kb_folder_limit_empty_request", L(records=["kF"]), None, {"kR1"}, {"app-A", "app-B"}),
    ("kb_folder_limit_kb_named", L(records=["kF"]), {"kb": ["kb-1"]}, {"kR1"}, {"app-A", "app-B"}),
    ("kb_folder_limit_kb_root_file_named", L(records=["kF"]), {"records": ["kR2"]}, set(), {"app-A", "app-B"}),
    ("kb_folder_limit_connector_folder_named", L(records=["kF"]), {"recordGroups": ["gC"]}, set(), {"app-A", "app-B"}),
    ("mixed_limit_empty_request", L(apps=["app-B"], groups=["gC"]), None, {"rB", "rC1", "rC2"}, {"kb-1"}),
    ("mixed_limit_sibling_named", L(apps=["app-B"], groups=["gC"]), {"recordGroups": ["gS"]}, set(), {"kb-1"}),
    ("mixed_limit_app_A_named", L(apps=["app-B"], groups=["gC"]), {"apps": ["app-A"]}, {"rC1", "rC2"}, {"kb-1"}),
    # A client cannot inject the server-set keys.
    ("inject_selectionApps", L(groups=["gC"]), {"recordGroups": ["gC"], "selectionApps": ["app-B", "kb-1"]}, {"rC1", "rC2"}, {"app-B", "kb-1"}),
    ("inject_selectionApps_only", L(groups=["gC"]), {"selectionApps": ["app-B"]}, {"rC1", "rC2"}, {"app-B", "kb-1"}),
    ("inject_connection_keys", L(groups=["gC"]), {"connectionApps": ["app-B"], "connectionRecordGroups": ["gB"], "connectionRecords": []}, {"rC1", "rC2"}, {"app-B", "kb-1"}),
    ("inject_project_and_agent_keys", L(groups=["gC"]), {"allowedApps": ["app-B"], "projectApps": ["app-B"]}, {"rC1", "rC2"}, {"app-B", "kb-1"}),
    ("stale_limit_folder", L(groups=["g-gone"]), None, set(), {"app-A", "app-B", "kb-1"}),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(("name", "limit", "filters", "expected", "never"), CASES, ids=[c[0] for c in CASES])
async def test_limited_grep_keeps_to_the_limit(name, limit, filters, expected, never) -> None:
    found, scanned = await grep(limit, filters)
    assert found == expected, (name, found, scanned)
    assert not (scanned & never), (name, scanned)


UNLIMITED = [
    ("unlimited_empty", None, {"rP", "rC1", "rC2", "rS", "rB", "kR1", "kR2"}),
    ("unlimited_app_A", {"apps": ["app-A"]}, {"rP", "rC1", "rC2", "rS"}),
    ("unlimited_strict_no_app", {"strictScope": True}, set()),
    # Out of scope here: a folder alone sets no selectionApps for an
    # unlimited token, so the scan runs on nothing (pre-existing, fails closed).
    ("unlimited_folder_only", {"recordGroups": ["gC"]}, set()),
    ("unlimited_folder_injected_apps", {"recordGroups": ["gC"], "selectionApps": ["app-A"]}, set()),
    ("unlimited_app_and_folder", {"apps": ["app-A"], "recordGroups": ["gC"]}, {"rP", "rC1", "rC2", "rS"}),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(("name", "filters", "expected"), UNLIMITED, ids=[c[0] for c in UNLIMITED])
async def test_unlimited_grep_is_unchanged(name, filters, expected) -> None:
    found, _ = await grep(None, filters)
    assert found == expected, (name, found)


@pytest.mark.asyncio
async def test_a_limit_folder_the_user_cannot_read_finds_nothing() -> None:
    found, scanned = await grep(L(groups=["gC"]), None, graph=make_graph(unreadable=frozenset({"gC"})))
    assert found == set()


def _truncating(graph: AsyncMock, group: str) -> AsyncMock:
    """A graph whose hierarchy walk reports a selection naming ``group`` as too large."""
    real = graph.get_selection_nodes.side_effect

    async def nodes(org_id, *, group_ids=(), record_ids=(), exact_record_ids=(), limit=0) -> dict:
        out = await real(org_id, group_ids=group_ids, record_ids=record_ids, exact_record_ids=exact_record_ids, limit=limit)
        if group in group_ids:
            out["truncated"] = True
        return out

    graph.get_selection_nodes = AsyncMock(side_effect=nodes)
    return graph


@pytest.mark.asyncio
@pytest.mark.parametrize("state", [
    {"vectorMembershipBackfilled": False},
    {"vectorMembershipBackfilled": False, "vectorMembershipBackfillExhausted": True},
])
async def test_grep_does_not_wait_for_the_vector_backfill(state: dict) -> None:
    """Grep reads files, not the vector index: an app whose index is not
    backfilled (or never will be) is searched all the same."""
    graph = make_graph()
    graph.get_nodes_by_field_in = AsyncMock(side_effect=lambda collection, field, ids, *a, **k: [
        {"id": i, **state} for i in ids])
    found, _ = await grep(L(groups=["gC"]), None, graph=graph)
    assert found == {"rC1", "rC2"}


@pytest.mark.asyncio
async def test_a_request_selection_too_large_says_so_and_does_not_blame_the_limit() -> None:
    with pytest.raises(HTTPException) as exc:
        await grep(L(groups=["gC"]), {"recordGroups": ["gP"]}, graph=_truncating(make_graph(), "gP"))
    assert exc.value.status_code == 400
    assert "request" in exc.value.detail and "limit" not in exc.value.detail


@pytest.mark.asyncio
async def test_a_limit_too_large_says_so_as_the_limit() -> None:
    from app.modules.retrieval.selection_scope import CONNECTION_LIMIT_TOO_LARGE_MESSAGE

    with pytest.raises(HTTPException) as exc:
        await grep(L(groups=["gC"]), None, graph=_truncating(make_graph(), "gC"))
    assert exc.value.status_code == 422 and exc.value.detail == CONNECTION_LIMIT_TOO_LARGE_MESSAGE


@pytest.mark.asyncio
async def test_a_graph_that_cannot_answer_is_a_503_not_a_widening() -> None:
    from app.exceptions.graph_db_exceptions import (
        PermissionVerificationUnavailableError,
    )

    graph = make_graph()
    graph.check_access = AsyncMock(side_effect=PermissionVerificationUnavailableError("down"))
    with pytest.raises(HTTPException) as exc:
        await grep(L(groups=["gC"]), None, graph=graph)
    assert exc.value.status_code == 503


class TestTheAppsTheScanMayRunOn:
    """``_reachable_scope`` cuts the apps a selection touched to those the caller
    and the limit reach, whatever built the filters."""

    @staticmethod
    async def _cut(filters: dict, limit_reach: frozenset[str] | None, graph: AsyncMock | None = None) -> dict | None:
        from app.api.routes.knowledge_search import _reachable_scope

        return await _reachable_scope(graph or make_graph(), "org-1", "user-1", filters, limit_reach)

    @pytest.mark.asyncio
    async def test_a_recorded_app_outside_the_limit_is_cut(self) -> None:
        got = await self._cut({"strictScope": True, "selectionApps": ["app-A", "app-B"]}, frozenset({"app-A"}))
        assert got["selectionApps"] == ["app-A"]

    @pytest.mark.asyncio
    async def test_when_every_recorded_app_is_outside_the_limit_nothing_is_scanned(self) -> None:
        assert await self._cut({"strictScope": True, "selectionApps": ["app-B"]}, frozenset({"app-A"})) is None

    @pytest.mark.asyncio
    async def test_a_recorded_app_the_caller_cannot_reach_is_cut(self) -> None:
        graph = make_graph()
        graph.get_org_apps = AsyncMock(return_value=[{"_key": "app-A"}])
        got = await self._cut({"strictScope": True, "selectionApps": ["app-A", "app-B"]}, None, graph)
        assert got["selectionApps"] == ["app-A"]
