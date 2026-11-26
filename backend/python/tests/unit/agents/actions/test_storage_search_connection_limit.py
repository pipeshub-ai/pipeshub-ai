"""At the scan layer, the storage tools keep to a connected app's limit
themselves, so a caller that builds its own filters cannot reach past it."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.modules.retrieval.selection_scope import SelectionScope
from tests.support.connection_limit_world import RECORDS, VRID_TO_RECORD, make_graph
from tests.unit.agents.actions.test_storage_search import _make_tool

SELECTION = "app.modules.retrieval.selection_scope"


def _limited(**extra: object) -> dict:
    """The filters of a turn limited to app-in whose request also names app-out."""
    return {
        "strictScope": True, "apps": ["app-in", "app-out"], "connectionApps": ["app-in"],
        "connectionRecordGroups": [], "connectionRecords": [], **extra,
    }


def test_an_app_outside_the_limit_is_not_searchable_even_when_the_request_names_it() -> None:
    tool = _make_tool(filters=_limited())
    assert tool._scope_connector_ids() == frozenset({"app-in"})
    assert tool._out_of_scope_error("app-out") is not None
    assert tool._out_of_scope_error("app-in") is None


def test_a_limit_with_no_whole_app_searches_nothing() -> None:
    tool = _make_tool(filters=_limited(connectionApps=[]))
    assert tool._scope_connector_ids() == frozenset()


def test_a_limit_naming_folders_leaves_the_apps_to_the_record_check() -> None:
    tool = _make_tool(filters=_limited(connectionApps=[], connectionRecordGroups=["g-in"]))
    assert tool._scope_connector_ids() == frozenset({"app-in", "app-out"})


def test_a_turn_without_a_limit_is_unchanged() -> None:
    tool = _make_tool(filters={"strictScope": True, "apps": ["app-in", "app-out"]})
    assert tool._scope_connector_ids() == frozenset({"app-in", "app-out"})


@pytest.mark.asyncio
async def test_the_record_check_is_bounded_by_the_limit() -> None:
    tool = _make_tool(filters=_limited())
    scope = SelectionScope(app_ids=frozenset({"app-in"}))
    with patch(f"{SELECTION}.resolve_user_key", AsyncMock(return_value="ukey")), \
         patch(f"{SELECTION}.resolve_request_scopes", AsyncMock(return_value=(None, [scope]))):
        await tool._fetch_accessible_vrids(["v1"])
    kwargs = tool.state["graph_provider"].filter_accessible_virtual_record_ids.await_args.kwargs
    assert kwargs["scopes"] == [scope]


@pytest.mark.asyncio
async def test_a_limit_that_cannot_be_resolved_fails_closed() -> None:
    tool = _make_tool(filters=_limited())
    with patch(f"{SELECTION}.resolve_user_key", AsyncMock(return_value=None)):
        assert await tool._fetch_accessible_vrids(["v1"]) is None
    tool.state["graph_provider"].filter_accessible_virtual_record_ids.assert_not_awaited()


@pytest.mark.asyncio
async def test_without_a_limit_the_record_check_gets_no_scopes() -> None:
    tool = _make_tool(filters={"strictScope": True, "apps": ["c"]})
    tool.state["graph_provider"].filter_accessible_virtual_record_ids = AsyncMock(return_value={})
    await tool._fetch_accessible_vrids(["v1"])
    assert "scopes" not in tool.state["graph_provider"].filter_accessible_virtual_record_ids.await_args.kwargs


def test_two_limits_do_not_share_a_permission_answer() -> None:
    a = _make_tool(filters=_limited())._row_limit_key()
    b = _make_tool(filters=_limited(connectionApps=["app-out"]))._row_limit_key()
    c = _make_tool(filters=_limited(recordGroups=["g-x"]))._row_limit_key()
    assert len({a, b, c}) == 3 and _make_tool(filters={})._row_limit_key() == ()


def _folder_only(**extra: object) -> dict:
    """A turn limited to a folder of app-in, as ``prepare_turn_scope`` writes it:
    no app named, the apps the folder sits in recorded."""
    return {
        "strictScope": True, "apps": [], "recordGroups": ["g-in"], "selectionApps": ["app-in"],
        "connectionApps": [], "connectionRecordGroups": ["g-in"], "connectionRecords": [], **extra,
    }


def test_a_folder_only_limit_searches_the_app_the_folder_sits_in() -> None:
    tool = _make_tool(filters=_folder_only())
    assert tool._scope_connector_ids() == frozenset({"app-in"})
    assert tool._out_of_scope_error("app-in") is None
    assert tool._out_of_scope_error("app-out") is not None


def test_a_folder_only_limit_with_no_app_recorded_searches_nothing() -> None:
    tool = _make_tool(filters=_folder_only(selectionApps=[]))
    assert tool._scope_connector_ids() == frozenset()


def test_an_unlimited_turn_does_not_read_the_apps_a_selection_touches() -> None:
    """Only a connected app's limit widens the scan to the apps a folder sits in."""
    tool = _make_tool(filters={"strictScope": True, "apps": [], "recordGroups": ["g"], "selectionApps": ["app-in"]})
    assert tool._scope_connector_ids() == frozenset()


# --- the record check holds each hit to the whole turn ---------------------------
# A world with nested and sibling folders, files, a second app and a collection;
# the filters are built by the real ``prepare_turn_scope``, as a chat turn builds them.
FOLDER_GC = {"apps": [], "recordGroups": ["gC"], "records": []}
FOLDER_GP = {"apps": [], "recordGroups": ["gP"], "records": []}
FILE_RC1 = {"apps": [], "recordGroups": [], "records": ["rC1"]}


async def _reach(filters: dict, **sources: object) -> tuple[set[str], set[str]]:
    """The records a storage tool can return on the turn, and the apps it scans."""
    from app.modules.retrieval import selection_scope
    from app.modules.retrieval.selection_scope import prepare_turn_scope

    selection_scope._memo.clear()
    graph = make_graph()
    turn = await prepare_turn_scope(graph, "user-1", "org-1", filters, **sources)
    tool = _make_tool(filters=turn, graph_provider=graph, user_id="user-1", org_id="org-1")
    scope = tool._scope_connector_ids()
    scanned = {r[0] for r in RECORDS.values()} if scope is None else set(scope)
    accessible = await tool._fetch_accessible_vrids(list(VRID_TO_RECORD))
    assert accessible is not None
    return {r for r in accessible.values() if RECORDS[r][0] in scanned}, scanned


CASES = [
    ("limit_folder_request_nothing", {}, {"connection_sources": FOLDER_GC}, {"rC1", "rC2"}),
    ("limit_file_request_nothing", {}, {"connection_sources": FILE_RC1}, {"rC1"}),
    ("limit_kb_folder_request_nothing", {}, {"connection_sources": {"apps": [], "recordGroups": [], "records": ["kF"]}}, {"kR1"}),
    ("limit_folder_request_other_app_folder", {"recordGroups": ["gB"]}, {"connection_sources": FOLDER_GC}, set()),
    ("limit_folder_request_other_app", {"apps": ["app-B"]}, {"connection_sources": FOLDER_GC}, set()),
    # The turn's own selection narrows inside the limit: hits stay in it.
    ("limit_parent_selection_child", {"recordGroups": ["gC"]}, {"connection_sources": FOLDER_GP}, {"rC1", "rC2"}),
    ("limit_folder_selection_sibling", {"recordGroups": ["gS"]}, {"connection_sources": FOLDER_GC}, set()),
    ("limit_file_selection_sibling_file", {"records": ["rC2"]}, {"connection_sources": FILE_RC1}, set()),
    # Server-set bounds besides the limit.
    ("limit_parent_agent_child", {}, {"connection_sources": FOLDER_GP, "agent_sources": FOLDER_GC}, {"rC1", "rC2"}),
    ("limit_parent_project_child", {}, {"connection_sources": FOLDER_GP, "project_sources": FOLDER_GC}, {"rC1", "rC2"}),
]
#: What a turn may return, exactly (not only at most): the narrowed turns must still find their records.
FINDS = {"limit_folder_request_nothing", "limit_file_request_nothing", "limit_kb_folder_request_nothing",
         "limit_parent_selection_child", "limit_parent_agent_child", "limit_parent_project_child"}


@pytest.mark.asyncio
@pytest.mark.parametrize(("name", "filters", "sources", "expected"), CASES, ids=[c[0] for c in CASES])
async def test_storage_tools_keep_to_the_whole_turn(name: str, filters: dict, sources: dict, expected: set[str]) -> None:
    got, scanned = await _reach(filters, **sources)
    assert got <= expected, (name, sorted(got - expected), sorted(scanned))
    if name in FINDS:
        assert got == expected, (name, sorted(got), sorted(scanned))


@pytest.mark.asyncio
async def test_an_unlimited_strict_selection_still_scans_nothing() -> None:
    got, scanned = await _reach({"recordGroups": ["gC"]})
    assert scanned == set() and got == set()


@pytest.mark.asyncio
async def test_a_saved_agents_folder_sources_hold_an_unlimited_turn_too() -> None:
    """The record check carries every bound, so a turn with no connected-app
    limit is held to the folder of its saved agent as well."""
    got, _ = await _reach({}, agent_sources=FOLDER_GC)
    assert got <= {"rC1", "rC2"}


@pytest.mark.asyncio
async def test_a_turn_with_no_selection_and_no_bound_gets_no_scopes() -> None:
    from app.modules.retrieval.selection_scope import prepare_turn_scope

    graph = make_graph()
    turn = await prepare_turn_scope(graph, "user-1", "org-1", {"apps": ["app-A"]})
    tool = _make_tool(filters=turn, graph_provider=graph, user_id="user-1", org_id="org-1")
    assert await tool._turn_scopes(graph, "user-1", "org-1") == []


_FOLDER_C = {"apps": [], "recordGroups": ["gC"], "records": []}
_FOLDER_P = {"apps": [], "recordGroups": ["gP"], "records": []}
_FOLDER_S = {"apps": [], "recordGroups": ["gS"], "records": []}


async def _prepared_turn(filters: dict, graph: MagicMock, **sources: object) -> dict:
    from app.modules.retrieval import selection_scope
    from app.modules.retrieval.selection_scope import prepare_turn_scope

    selection_scope._memo.clear()
    return await prepare_turn_scope(graph, "user-1", "org-1", filters, **sources)


async def _turn_reach(filters: dict, graph: MagicMock) -> set[str]:
    tool = _make_tool(filters=filters, graph_provider=graph, user_id="user-1", org_id="org-1")
    scope = tool._scope_connector_ids()
    scanned = {RECORDS[r][0] for r in RECORDS} if scope is None else set(scope)
    accessible = await tool._fetch_accessible_vrids_for_turn(list(VRID_TO_RECORD))
    assert accessible is not None
    return {r for r in accessible.values() if RECORDS[r][0] in scanned}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("first", "second", "allowed"),
    [
        (({"recordGroups": ["gC"]}, {"connection_sources": _FOLDER_P}),
         ({"recordGroups": ["gS"]}, {"connection_sources": _FOLDER_P}), set()),
        (({}, {}), ({}, {"agent_sources": _FOLDER_C}), {"rC1", "rC2"}),
        (({}, {"agent_sources": _FOLDER_C}), ({}, {"agent_sources": _FOLDER_S}), {"rS"}),
    ],
    ids=["same-limit-other-selection", "no-scope-then-agent-folder", "agent-folder-then-other-folder"],
)
async def test_two_tool_instances_in_one_turn_do_not_share_a_permission_answer(first, second, allowed) -> None:
    """One chat turn builds several storage tool instances and memoizes their
    permission answers for the turn; an instance under a different selection or
    bound must not reuse another's answer."""
    from app.utils.turn_memo import begin_turn, end_turn

    graph = make_graph()
    turn_a = await _prepared_turn(first[0], graph, **first[1])
    turn_b = await _prepared_turn(second[0], graph, **second[1])
    tasks = begin_turn()
    try:
        await _turn_reach(turn_a, graph)
        got = await _turn_reach(turn_b, graph)
    finally:
        await end_turn(tasks)
    assert got <= allowed, sorted(got - allowed)
