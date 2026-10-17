import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.agents.actions.knowledge_graph.ops import values
from app.agents.actions.knowledge_graph.ops.entity_records import LOOKUP_FAILED_MSG
from app.exceptions.fastapi_responses import Status
from app.exceptions.graph_db_exceptions import PermissionVerificationUnavailableError
from app.modules.retrieval.entity_filters import TOO_BROAD_MESSAGE
from app.modules.retrieval.entity_permissions import (
    EntityAccessContext,
    EntityAccessError,
)
from app.modules.retrieval.retrieval_service import (
    ACCESSIBLE_RECORDS_NOT_FOUND_MESSAGE,
    ENTITY_FILTER_NO_MATCH_MESSAGE,
    ENTITY_FILTER_UNAVAILABLE_MESSAGE,
    RetrievalService,
)


def _service() -> RetrievalService:
    svc = RetrievalService.__new__(RetrievalService)
    svc.logger = MagicMock()
    svc.graph_provider = AsyncMock()
    svc.vector_db_service = AsyncMock()
    return svc


def _hits(*rows: tuple[str, str, str], truncated: bool = False) -> dict:
    return {
        "hits": [{"recordId": r, "virtualRecordId": v, "connectorId": c} for r, v, c in rows],
        "truncated": truncated,
    }


async def test_entity_filters_narrow_the_record_id_search_without_widening_scope(monkeypatch):
    svc = _service()
    scope = AsyncMock(return_value=(None, {"v1": "r1", "v2": "r2"}, {"_key": "u1"}))
    monkeypatch.setattr(svc, "_resolve_search_scope", scope)
    monkeypatch.setattr(svc, "_execute_parallel_searches", AsyncMock(return_value=[]))
    svc.graph_provider.get_records_for_named_entities = AsyncMock(
        return_value=_hits(("r1", "v1", "a"), ("r-private", "v-private", "a")),
    )

    await svc.search_with_filters(
        ["q"], "u1", "org",
        filter_groups={"Departments": ["Legal"], "entityFilters": {"kinds": ["organization"], "name": "Acme"}},
    )

    assert scope.await_args.args == ("u1", "org", {"departments": ["Legal"]}, None)
    assert scope.await_args.kwargs == {}
    must = svc.vector_db_service.filter_collection.await_args.kwargs["must"]
    assert must == {"orgId": "org", "virtualRecordId": ["v1"]}


async def test_a_reused_filter_dict_keeps_its_entity_filter(monkeypatch):
    svc = _service()
    monkeypatch.setattr(svc, "_resolve_search_scope", AsyncMock(return_value=(None, {"v1": "r1"}, None)))
    monkeypatch.setattr(svc, "_execute_parallel_searches", AsyncMock(return_value=[]))
    svc.graph_provider.get_records_for_named_entities = AsyncMock(return_value=_hits(("r1", "v1", "a")))
    groups = {"entityFilters": {"entityIds": ["e1"]}, "Departments": ["Legal"]}

    for _ in range(2):
        await svc.search_with_filters(["q"], "u1", "org", filter_groups=groups)

    assert groups == {"entityFilters": {"entityIds": ["e1"]}, "Departments": ["Legal"]}
    assert svc.graph_provider.get_records_for_named_entities.await_count == 2


async def test_entity_filters_keep_container_scoping(monkeypatch):
    svc = _service()
    containers = MagicMock(is_empty=False)
    monkeypatch.setattr(svc, "_resolve_search_scope", AsyncMock(return_value=(containers, {}, {"_key": "u1"})))
    clauses = MagicMock(return_value=({"orgId": "org"}, {"connectorIds": ["a"]}))
    monkeypatch.setattr(svc, "_build_container_clauses", clauses)
    adjudicate = AsyncMock(return_value=([], {}, False))
    monkeypatch.setattr(svc, "_search_and_adjudicate", adjudicate)
    svc.graph_provider.get_records_for_named_entities = AsyncMock(
        return_value=_hits(("r1", "v1", "a"), ("r2", "v2", "b")),
    )

    await svc.search_with_filters(["q"], "u1", "org", filter_groups={"entityFilters": {"entityIds": ["e1"]}})

    assert clauses.call_args.args[2] == ["v1", "v2"]
    adjudicate.assert_awaited_once()


async def test_entity_filters_intersect_a_tools_record_ids(monkeypatch):
    svc = _service()
    monkeypatch.setattr(svc, "_resolve_search_scope", AsyncMock(return_value=(None, {"v1": "r1", "v2": "r2"}, None)))
    monkeypatch.setattr(svc, "_execute_parallel_searches", AsyncMock(return_value=[]))
    svc.graph_provider.get_records_for_named_entities = AsyncMock(
        return_value=_hits(("r1", "v1", "a"), ("r2", "v2", "a")),
    )

    await svc.search_with_filters(
        ["q"], "u1", "org", filter_groups={"entityFilters": {"entityIds": ["e1"]}},
        virtual_record_ids_from_tool=["v2", "v7"],
    )

    must = svc.vector_db_service.filter_collection.await_args.kwargs["must"]
    assert must["virtualRecordId"] == ["v2"]


def _legacy_scope(accessible: dict[str, str]) -> AsyncMock:
    return AsyncMock(return_value=(None, accessible, {"_key": "u1"}))


async def _search(svc, graph_reply: dict) -> dict:
    svc.graph_provider.get_records_for_named_entities = AsyncMock(**graph_reply)
    return await svc.search_with_filters(["q"], "u1", "org", filter_groups={"entityFilters": {"name": "acme"}})


async def test_a_match_only_in_unreadable_records_reads_as_no_match(monkeypatch):
    """The L8 oracle: a user must not tell "nothing mentions this" from "only records
    I cannot read mention this"."""
    responses = []
    for reply in ({"return_value": _hits()}, {"return_value": _hits(("r-secret", "v-secret", "hr"))}):
        svc = _service()
        monkeypatch.setattr(svc, "_resolve_search_scope", _legacy_scope({"v1": "r1"}))
        monkeypatch.setattr(svc, "_execute_parallel_searches", AsyncMock(return_value=[]))
        response = await _search(svc, reply)
        responses.append((response["message"], response["status"]))
        svc.vector_db_service.filter_collection.assert_not_called()
    assert responses[0] == responses[1] == (ENTITY_FILTER_NO_MATCH_MESSAGE, Status.EMPTY_RESPONSE.value)


async def test_a_user_who_reads_nothing_gets_the_same_answer_whatever_the_filter_matches(monkeypatch):
    responses = []
    for reply in ({"return_value": _hits()}, {"return_value": _hits(("r-secret", "v-secret", "hr"))}):
        svc = _service()
        monkeypatch.setattr(svc, "_resolve_search_scope", _legacy_scope({}))
        response = await _search(svc, reply)
        responses.append((response["message"], response["status"]))
    assert responses[0] == responses[1] == (ACCESSIBLE_RECORDS_NOT_FOUND_MESSAGE, Status.ACCESSIBLE_RECORDS_NOT_FOUND.value)


async def test_too_broad_across_the_org_is_counted_again_over_readable_records(monkeypatch):
    svc = _service()
    monkeypatch.setattr(svc, "_resolve_search_scope", _legacy_scope({"v1": "r1", "v2": "r2"}))
    monkeypatch.setattr(svc, "_execute_parallel_searches", AsyncMock(return_value=[]))
    graph_reply = {"side_effect": [_hits(truncated=True), _hits(("r1", "v1", "a"))]}
    await _search(svc, graph_reply)
    second = svc.graph_provider.get_records_for_named_entities.await_args_list[1]
    assert sorted(second.kwargs["within"]) == ["r1", "r2"]
    must = svc.vector_db_service.filter_collection.await_args.kwargs["must"]
    assert must["virtualRecordId"] == ["v1"]


async def test_too_broad_is_reported_only_when_readable_records_are_too_many(monkeypatch):
    svc = _service()
    monkeypatch.setattr(svc, "_resolve_search_scope", _legacy_scope({"v1": "r1"}))
    response = await _search(svc, {"side_effect": [_hits(truncated=True), _hits(truncated=True)]})
    assert (response["message"], response["status"], response["status_code"]) == (
        TOO_BROAD_MESSAGE, Status.INVALID_FILTER.value, 422,
    )
    svc.vector_db_service.filter_collection.assert_not_called()


async def test_a_failed_entity_lookup_is_an_error_never_no_match(monkeypatch):
    svc = _service()
    monkeypatch.setattr(svc, "_resolve_search_scope", _legacy_scope({"v1": "r1"}))
    response = await _search(svc, {"side_effect": RuntimeError("graph down")})
    assert (response["message"], response["status"]) == (ENTITY_FILTER_UNAVAILABLE_MESSAGE, Status.ERROR.value)
    svc.vector_db_service.filter_collection.assert_not_called()


def _context(*app_ids: str) -> EntityAccessContext:
    return EntityAccessContext(
        org_id="org", user_key="uk", app_level_app_ids=frozenset(app_ids),
        record_level_app_ids=frozenset(), record_group_ids=frozenset(), app_names={},
    )


def _state(graph) -> dict:
    return {"graph_provider": graph, "org_id": "org", "user_id": "u1", "config_service": object()}


@pytest.fixture
def tool_env(monkeypatch):
    monkeypatch.setattr(values, "_named_entities_enabled", AsyncMock(return_value=True))
    context = AsyncMock(return_value=_context("app-in-scope"))
    monkeypatch.setattr(values, "load_entity_access_context", context)
    return context


async def test_find_records_by_value_checks_only_in_scope_hits(tool_env):
    graph = AsyncMock()
    graph.get_records_for_named_entities = AsyncMock(return_value=_hits(
        ("r1", "v1", "app-in-scope"), ("r-denied", "v2", "app-in-scope"), ("r-other-app", "v3", "app-elsewhere"),
    ))
    graph.filter_accessible_record_ids = AsyncMock(return_value={"r1"})

    ok, body = await values.execute_find_records_by_value(_state(graph), kinds=["currency"], amount_min=10, currency="USD")

    assert ok
    assert json.loads(body)["records"] == [{"virtualRecordId": "v1", "recordId": "r1"}]
    assert graph.filter_accessible_record_ids.await_args.args == (["r1", "r-denied"], "u1", "org")
    graph.get_accessible_virtual_record_ids.assert_not_called()
    query = graph.get_records_for_named_entities.await_args.args[2]
    assert (query.currency, query.amount_min) == ("USD", 10)


async def test_find_records_by_value_filters_on_a_percentage_alone(tool_env):
    graph = AsyncMock()
    graph.get_records_for_named_entities = AsyncMock(return_value=_hits(("r1", "v1", "app-in-scope")))
    graph.filter_accessible_record_ids = AsyncMock(return_value={"r1"})

    ok, _ = await values.execute_find_records_by_value(_state(graph), percent_min=0.2)

    assert ok
    query = graph.get_records_for_named_entities.await_args.args[2]
    assert query.percent_min == 0.2 and query.percent_max is not None


async def test_records_found_by_value_can_be_fetched_next(tool_env):
    """fetch_record is offered only for Record IDs the model has been shown."""
    graph = AsyncMock()
    graph.get_records_for_named_entities = AsyncMock(return_value=_hits(
        ("r1", "v1", "app-in-scope"), ("r-denied", "v2", "app-in-scope"),
    ))
    graph.filter_accessible_record_ids = AsyncMock(return_value={"r1"})
    state = _state(graph)

    await values.execute_find_records_by_value(state, kinds=["organization"])

    assert state["known_record_ids"] == {"r1"}


async def test_a_value_too_broad_for_one_page_is_listed_further_and_checked_for_permission(tool_env):
    """The user's whole readable set is not loaded when the matches can be listed."""
    graph = AsyncMock()
    graph.get_records_for_named_entities = AsyncMock(side_effect=[
        _hits(truncated=True), _hits(("r1", "v1", "app-in-scope"), ("r2", "v2", "app-in-scope")),
    ])
    graph.filter_accessible_record_ids = AsyncMock(return_value={"r1"})

    ok, body = await values.execute_find_records_by_value(_state(graph), kinds=["date"])

    assert ok and json.loads(body)["records"] == [{"virtualRecordId": "v1", "recordId": "r1"}]
    assert graph.get_records_for_named_entities.await_args.kwargs == {"limit": values._SCAN_LIMIT}
    graph.get_accessible_virtual_record_ids.assert_not_awaited()


async def test_a_value_too_broad_to_list_is_counted_over_readable_records(tool_env):
    graph = AsyncMock()
    graph.get_records_for_named_entities = AsyncMock(side_effect=[
        _hits(truncated=True), _hits(truncated=True), _hits(("r1", "v1", "app-in-scope")),
    ])
    graph.get_accessible_virtual_record_ids = AsyncMock(return_value={"v1": "r1", "v2": "r2"})
    graph.filter_accessible_record_ids = AsyncMock(return_value={"r1"})

    ok, body = await values.execute_find_records_by_value(_state(graph), kinds=["date"])

    assert ok and json.loads(body)["records"] == [{"virtualRecordId": "v1", "recordId": "r1"}]
    assert graph.get_accessible_virtual_record_ids.await_args.args[2] == {"apps": ["app-in-scope"]}
    assert sorted(graph.get_records_for_named_entities.await_args.kwargs["within"]) == ["r1", "r2"]


async def test_a_value_too_broad_among_readable_records_asks_for_a_narrower_filter(tool_env):
    graph = AsyncMock()
    graph.get_records_for_named_entities = AsyncMock(side_effect=[_hits(truncated=True)] * 3)
    graph.get_accessible_virtual_record_ids = AsyncMock(return_value={"v1": "r1"})

    ok, body = await values.execute_find_records_by_value(_state(graph), kinds=["date"])

    assert (ok, body) == (False, values.TOO_MANY_RECORDS_MSG)


async def test_find_records_by_value_rejects_more_readable_records_than_it_returns(tool_env):
    graph = AsyncMock()
    rows = [(f"r{i}", f"v{i}", "app-in-scope") for i in range(400)]
    graph.get_records_for_named_entities = AsyncMock(return_value=_hits(*rows))
    graph.filter_accessible_record_ids = AsyncMock(side_effect=lambda ids, *_: set(ids))

    ok, message = await values.execute_find_records_by_value(_state(graph), kinds=["organization"])

    assert (ok, message) == (False, values.TOO_MANY_RECORDS_MSG)
    assert graph.filter_accessible_record_ids.await_count == 1


async def test_find_records_by_value_returns_exactly_a_full_page_after_checking_every_batch(tool_env):
    graph = AsyncMock()
    total = values._CHECK_BATCH + 200
    rows = [(f"r{i}", f"v{i}", "app-in-scope") for i in range(total)]
    graph.get_records_for_named_entities = AsyncMock(return_value=_hits(*rows))
    readable = {f"r{i}" for i in range(0, total, total // 50)}
    readable = set(sorted(readable, key=lambda r: int(r[1:]))[:50])
    graph.filter_accessible_record_ids = AsyncMock(side_effect=lambda ids, *_: set(ids) & readable)

    ok, body = await values.execute_find_records_by_value(_state(graph), kinds=["organization"])

    payload = json.loads(body)
    assert ok and len(payload["records"]) == 50 and "message" not in payload
    assert graph.filter_accessible_record_ids.await_count == 2


@pytest.mark.parametrize(
    "failure",
    [
        {"context": EntityAccessError("no user")},
        {"lookup": RuntimeError("graph down")},
        {"permissions": PermissionVerificationUnavailableError("graph down")},
    ],
)
async def test_find_records_by_value_fails_when_access_cannot_be_resolved(tool_env, failure):
    graph = AsyncMock()
    graph.get_records_for_named_entities = AsyncMock(
        side_effect=failure.get("lookup"), return_value=_hits(("r1", "v1", "app-in-scope")),
    )
    graph.filter_accessible_record_ids = AsyncMock(side_effect=failure.get("permissions"), return_value={"r1"})
    if "context" in failure:
        tool_env.side_effect = failure["context"]

    ok, message = await values.execute_find_records_by_value(_state(graph), kinds=["organization"])

    assert (ok, message) == (False, LOOKUP_FAILED_MSG)


async def test_find_records_by_value_is_off_with_the_labs_flag(monkeypatch):
    monkeypatch.setattr(values, "_named_entities_enabled", AsyncMock(return_value=False))
    graph = AsyncMock()

    ok, message = await values.execute_find_records_by_value(_state(graph), kinds=["organization"])

    assert not ok
    assert message == "Named-entity lookup is not enabled."
    graph.get_records_for_named_entities.assert_not_called()


async def test_a_model_that_sends_every_parameter_filters_only_on_the_ones_it_set(tool_env):
    """Strict tool schemas make models fill unused numbers with 0; 0 to 0 is not a range."""
    graph = AsyncMock()
    graph.get_records_for_named_entities = AsyncMock(return_value=_hits(("r1", "v1", "app-in-scope")))
    graph.filter_accessible_record_ids = AsyncMock(return_value={"r1"})

    ok, body = await values.execute_find_records_by_value(
        _state(graph), kinds=[], name="", date_from=0, date_to=0, amount_min=-800, amount_max=-700,
        currency="USD", quantity_min=0, quantity_max=0, dimension="", percent_min=0, percent_max=0,
    )

    assert ok and json.loads(body)["records"] == [{"virtualRecordId": "v1", "recordId": "r1"}]
    query = graph.get_records_for_named_entities.await_args.args[2]
    assert (query.amount_min, query.amount_max, query.currency) == (-800, -700, "USD")
    assert query.date_start_ms is None and query.percent_min is None and query.quantity_min is None


async def test_a_range_with_one_zero_end_is_still_a_range(tool_env):
    graph = AsyncMock()
    graph.get_records_for_named_entities = AsyncMock(return_value=_hits())
    await values.execute_find_records_by_value(_state(graph), amount_min=0, amount_max=100, currency="USD")
    query = graph.get_records_for_named_entities.await_args.args[2]
    assert (query.amount_min, query.amount_max) == (0, 100)
