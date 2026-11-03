"""Entity routes (app.api.routes.knowledge_search).

The access decisions are the shared ones (``entity_permissions``); these tests
check the routes hand them every result, return nothing the check refuses, and
take the caller from the session, never from the body.
"""
import json
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import jsonschema
import pytest
import yaml
from fastapi import HTTPException
from fastapi.responses import JSONResponse

import app.edition_config  # noqa: F401  (the route module loads cleanly only after this)
from app.api.routes import knowledge_search as ks
from app.core.org_context import OrgContext, bind
from app.modules.retrieval.entity_permissions import (
    EntityAccessContext,
    EntityRecordPage,
)

ORG, USER, RID = "org-1", "user-1", "rec-1"


@pytest.fixture(autouse=True)
def _signed_in(request: pytest.FixtureRequest) -> Iterator[None]:
    """The auth middleware binds the org context before a route runs; a test that asks for ``no_org_context`` runs without it."""
    if "no_org_context" in request.fixturenames:
        yield
        return
    with bind(OrgContext(ORG, USER, "user", "jwt", "req-1")):
        yield


@pytest.fixture
def no_org_context() -> None:
    """Marks a test that runs with no org context bound (see ``_signed_in``)."""


def _request(user: dict | None = None) -> MagicMock:
    request = MagicMock()
    request.state.user = {"orgId": ORG, "userId": USER} if user is None else user
    container = request.app.container
    container.logger.return_value = MagicMock()
    container.config_service.return_value = MagicMock()
    return request


# ---- entities ---------------------------------------------------------------
def _context() -> EntityAccessContext:
    return EntityAccessContext(
        org_id=ORG, user_key="user-key", app_level_app_ids=frozenset({"c1"}),
        record_level_app_ids=frozenset(), record_group_ids=frozenset(), app_names={"c1": "Drive"},
    )


def _patch_entity_state() -> object:
    return patch.object(ks, "_entity_state", AsyncMock(return_value={
        "org_id": ORG, "user_id": USER, "config_service": MagicMock(), "filters": {},
    }))


async def test_neighbors_lists_only_accessible_records_and_their_shared_entities() -> None:
    graph = MagicMock()
    graph.get_record_taxonomy_links = AsyncMock(return_value=[
        {"recordId": RID, "collection": "topics", "entityId": "t1", "name": "Orders"},
        {"recordId": RID, "collection": "topics", "entityId": "self", "name": "Self"},
    ])
    page = EntityRecordPage(records=[{"_key": RID, "recordName": "Doc", "recordType": "FILE", "connectorId": "c1"}], next_cursor=None)
    with _patch_entity_state(), patch.object(ks, "_named_entities_enabled", AsyncMock(return_value=False)), \
         patch.object(ks, "load_entity_access_context", AsyncMock(return_value=_context())), \
         patch.object(ks, "list_accessible_entity_records", AsyncMock(return_value=page)), \
         patch.object(ks, "resolve_frontend_url", AsyncMock(return_value=None)):
        response = await ks.entity_neighbors(
            _request(), ks.EntityNeighborsQuery(entityId="self", entityType="topic"), graph,
        )
    out = json.loads(response.body)
    assert [r["recordId"] for r in out["records"]] == [RID] and out["records"][0]["app"] == "Drive"
    assert out["relatedEntities"] == [{
        "entityId": "t1", "entityType": "topic", "name": "Orders", "relation": "belongsToTopic", "sharedRecords": 1,
    }]
    assert out["relationTypes"] == ["belongsToTopic"]


async def test_neighbors_without_readable_records_is_empty_and_reads_no_labels() -> None:
    graph = MagicMock()
    graph.get_record_taxonomy_links = AsyncMock()
    with _patch_entity_state(), patch.object(ks, "_named_entities_enabled", AsyncMock(return_value=True)), \
         patch.object(ks, "load_entity_access_context", AsyncMock(return_value=_context())), \
         patch.object(ks, "list_accessible_entity_records", AsyncMock(return_value=EntityRecordPage([], None))), \
         patch.object(ks, "resolve_frontend_url", AsyncMock(return_value=None)):
        response = await ks.entity_neighbors(
            _request(), ks.EntityNeighborsQuery(entityId="secret", entityType="named_entity"), graph,
        )
    out = json.loads(response.body)
    assert out["records"] == [] and out["relatedEntities"] == []
    graph.get_record_taxonomy_links.assert_not_called()


async def test_search_entities_returns_nothing_when_the_access_check_keeps_nothing() -> None:
    state = {"org_id": ORG, "user_id": USER, "config_service": MagicMock(), "filters": {},
             "entity_vector_store": MagicMock(), "graph_provider": MagicMock()}
    with patch.object(ks, "_entity_state", AsyncMock(return_value=state)), \
         patch("app.agents.actions.knowledge_graph.ops.entity_discovery.load_entity_access_context", AsyncMock(return_value=_context())), \
         patch("app.agents.actions.knowledge_graph.ops.entity_discovery.search_entities_for_user", AsyncMock(return_value=[])), \
         patch("app.agents.actions.knowledge_graph.ops.entity_discovery._named_entities_enabled", AsyncMock(return_value=False)):
        response = await ks.search_entities(_request(), ks.EntitySearchQuery(query="payments"), MagicMock())
    assert json.loads(response.body)["results"] == []


async def test_search_entities_without_a_store_is_a_503_that_says_so() -> None:
    state = {"org_id": ORG, "user_id": USER, "config_service": MagicMock(), "filters": {}, "graph_provider": MagicMock()}
    with patch.object(ks, "_entity_state", AsyncMock(return_value=state)):
        with pytest.raises(HTTPException) as err:
            await ks.search_entities(_request(), ks.EntitySearchQuery(query="payments"), MagicMock())
    assert err.value.status_code == 503



def test_the_bodies_have_no_place_for_an_organisation_or_a_user() -> None:
    for model in (ks.EntitySearchQuery, ks.EntityNeighborsQuery):
        assert not {"orgId", "userId", "org_id", "user_id"} & set(model.model_fields)
    # a client that sends them anyway is not obeyed: they are dropped, not read
    body = ks.EntityNeighborsQuery.model_validate(
        {"entityId": "e", "entityType": "topic", "orgId": "someone-else", "userId": "someone-else"},
    )
    assert not hasattr(body, "orgId") and not hasattr(body, "userId")


async def test_no_organisation_context_is_a_401_and_reads_nothing_whatever_the_request_claims(no_org_context) -> None:
    graph = MagicMock()
    # the claims on the request are not read for the org: without a bound context there is no caller
    request = _request({"orgId": "someone-else", "userId": USER})
    with pytest.raises(HTTPException) as err:
        await ks.search_entities(request, ks.EntitySearchQuery(query="payments"), graph)
    assert err.value.status_code == 401
    with pytest.raises(HTTPException) as err:
        await ks.entity_neighbors(request, ks.EntityNeighborsQuery(entityId="e", entityType="topic"), graph)
    assert err.value.status_code == 401
    assert graph.method_calls == []


async def test_a_bound_context_without_a_user_is_a_401() -> None:
    with bind(OrgContext(ORG, None, "user", "jwt", "req-2")):
        with pytest.raises(HTTPException) as err:
            await ks.search_entities(_request({}), ks.EntitySearchQuery(query="payments"), MagicMock())
    assert err.value.status_code == 401


async def test_the_organisation_is_the_bound_one_not_the_one_in_the_claims() -> None:
    assert ks._user(_request({"orgId": "someone-else", "userId": "ignored"})) == (ORG, USER)


async def test_the_access_check_is_built_for_the_session_user() -> None:
    graph = MagicMock()
    seen = {}

    async def context(state: dict) -> EntityAccessContext:
        seen.update(org=state["org_id"], user=state["user_id"])
        return _context()

    state = {"org_id": ORG, "user_id": USER, "config_service": MagicMock(), "filters": {}}
    with patch.object(ks, "_entity_state", AsyncMock(return_value=state)),          patch.object(ks, "_named_entities_enabled", AsyncMock(return_value=False)),          patch.object(ks, "load_entity_access_context", context),          patch.object(ks, "list_accessible_entity_records", AsyncMock(return_value=EntityRecordPage([], None))),          patch.object(ks, "resolve_frontend_url", AsyncMock(return_value=None)):
        await ks.entity_neighbors(_request(), ks.EntityNeighborsQuery(entityId="e", entityType="topic"), graph)
    assert seen == {"org": ORG, "user": USER}


async def test_an_entity_type_the_search_does_not_cover_is_a_400() -> None:
    state = {"org_id": ORG, "user_id": USER, "config_service": MagicMock(), "filters": {}}
    with patch.object(ks, "_entity_state", AsyncMock(return_value=state)),          patch.object(ks, "_named_entities_enabled", AsyncMock(return_value=True)),          patch.object(ks, "SEARCHABLE_ENTITY_TYPES", frozenset({"topic"})):
        with pytest.raises(HTTPException) as err:
            await ks.entity_neighbors(_request(), ks.EntityNeighborsQuery(entityId="e", entityType="department"), MagicMock())
    assert err.value.status_code == 400


@pytest.mark.parametrize("provider", ["neo4j", "arango"])
def test_both_graph_providers_answer_the_calls_the_routes_make(provider) -> None:
    """The routes call only these two provider methods besides the access check."""
    from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
    from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider

    cls = Neo4jProvider if provider == "neo4j" else ArangoHTTPProvider
    for name in ("get_record_taxonomy_links", "get_named_entities_for_record"):
        method = getattr(cls, name)
        assert callable(method) and not getattr(method, "__isabstractmethod__", False), (provider, name)
    assert not getattr(cls, "__abstractmethods__", frozenset())


_SPEC = Path(__file__).resolve().parents[4].parent / "nodejs" / "apps" / "src" / "modules" / "api-docs" / "pipeshub-openapi.yaml"


def _validate(schema_name: str, payload: dict) -> None:
    """Check a payload against the published schema (resolved against the spec's own components)."""
    components = yaml.safe_load(_SPEC.read_text(encoding="utf-8"))["components"]
    jsonschema.validate(payload, {"$ref": f"#/components/schemas/{schema_name}", "components": components})


def _neighbors_graph(record: dict, **overrides) -> tuple[MagicMock, EntityRecordPage]:
    graph = MagicMock()
    graph.get_record_taxonomy_links = AsyncMock(return_value=[
        {"recordId": RID, "collection": "topics", "entityId": "t1", "name": "Orders"},
    ])
    graph.get_named_entities_for_record = AsyncMock(return_value=[])
    for name, value in overrides.items():
        setattr(graph, name, value)
    return graph, EntityRecordPage(records=[record], next_cursor="c1")


async def _neighbors(graph: MagicMock, page: EntityRecordPage, *, named: bool = False) -> JSONResponse:
    with _patch_entity_state(), patch.object(ks, "_named_entities_enabled", AsyncMock(return_value=named)), \
         patch.object(ks, "load_entity_access_context", AsyncMock(return_value=_context())), \
         patch.object(ks, "list_accessible_entity_records", AsyncMock(return_value=page)), \
         patch.object(ks, "resolve_frontend_url", AsyncMock(return_value=None)):
        return await ks.entity_neighbors(
            _request(), ks.EntityNeighborsQuery(entityId="self", entityType="topic"), graph,
        )


async def test_the_neighbors_answer_matches_the_published_schema_and_dates_a_record_like_the_search_does() -> None:
    record = {"_key": RID, "recordName": "Doc", "recordType": "FILE", "connectorId": "c1",
              "sourceLastModifiedTimestamp": 1_777_500_000_000, "webUrl": "https://x/y"}
    graph, page = _neighbors_graph(record)
    out = json.loads((await _neighbors(graph, page)).body)
    _validate("EntityNeighborsResponse", out)
    row = out["records"][0]
    dates = {k: v for k, v in row.items() if k in {"modified", "dated", "fileModified", "uploaded"}}
    assert dates == {"modified": "2026-04-29"}
    # the same row, previewed by the search, carries the same key and text
    from app.agents.actions.knowledge_graph.ops.entity_discovery import _preview_row
    preview = _preview_row(record, _context(), None)
    assert {k: v for k, v in preview.items() if k in dates} == dates


async def test_the_search_answer_matches_the_published_schema_with_its_notes() -> None:
    payload = {
        "status": "success", "results": [], "ignoredEntityTypes": ["galaxy"], "kindNotes": ["x is not searched"],
        "scopeNote": "limited",
    }
    _validate("EntitySearchResponse", payload)
    with pytest.raises(jsonschema.ValidationError):
        _validate("EntitySearchResponse", {"results": [{"entityId": 1}]})


@pytest.mark.parametrize("failing", ["get_record_taxonomy_links", "get_named_entities_for_record"])
async def test_a_graph_database_error_while_labelling_is_the_documented_503_not_a_500(failing) -> None:
    graph, page = _neighbors_graph({"_key": RID, "recordName": "Doc", "connectorId": "c1"})
    setattr(graph, failing, AsyncMock(side_effect=ConnectionError("graph database is not connected")))
    with pytest.raises(HTTPException) as err:
        await _neighbors(graph, page, named=True)
    assert err.value.status_code == 503 and "try again" in err.value.detail
    assert "not connected" not in err.value.detail


@pytest.mark.parametrize(("error", "status"), [
    ({"status": "error", "message": "Entity search timed out", "retryable": True}, 503),
    ({"status": "error", "message": "Entity search is not available", "retryable": True}, 503),
    ({"status": "error", "message": "Unsupported entity_types ['x']; failed to match"}, 400),
    ({"status": "error", "message": "Named-entity search is not available on this plan"}, 400),
])
async def test_the_status_follows_what_the_search_reports_not_the_wording_of_its_message(error, status) -> None:
    state = {"org_id": ORG, "user_id": USER, "config_service": MagicMock(), "filters": {}}
    with patch.object(ks, "_entity_state", AsyncMock(return_value=state)), \
         patch.object(ks, "execute_search_entities", AsyncMock(return_value=(False, json.dumps(error)))):
        with pytest.raises(HTTPException) as err:
            await ks.search_entities(_request(), ks.EntitySearchQuery(query="payments"), MagicMock())
    assert err.value.status_code == status


async def test_the_search_marks_the_failures_that_are_retryable() -> None:
    from app.agents.actions.knowledge_graph.ops import entity_discovery as ed

    ok, text = await ed.execute_search_entities({"graph_provider": None}, "x")
    assert not ok and json.loads(text)["retryable"] is True
    ok, text = await ed.execute_search_entities(
        {"graph_provider": MagicMock(), "entity_vector_store": MagicMock(), "config_service": MagicMock(), "filters": {}},
        "x", ["galaxy"],
    )
    assert not ok and "retryable" not in json.loads(text)
