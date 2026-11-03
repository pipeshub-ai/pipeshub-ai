"""Entity routes (app.api.routes.knowledge_search).

The access decisions are the shared ones (``entity_permissions``); these tests
check the routes hand them every result, return nothing the check refuses, and
take the caller from the session, never from the body.
"""
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

import app.edition_config  # noqa: F401  (the route module loads cleanly only after this)
from app.api.routes import knowledge_search as ks
from app.modules.retrieval.entity_permissions import (
    EntityAccessContext,
    EntityRecordPage,
)

ORG, USER, RID = "org-1", "user-1", "rec-1"


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


@pytest.mark.parametrize("user", [{}, {"orgId": ORG}, {"userId": USER}])
async def test_a_session_without_organisation_or_user_is_a_401_and_reads_nothing(user) -> None:
    graph = MagicMock()
    with pytest.raises(HTTPException) as err:
        await ks.search_entities(_request(user), ks.EntitySearchQuery(query="payments"), graph)
    assert err.value.status_code == 401
    with pytest.raises(HTTPException) as err:
        await ks.entity_neighbors(_request(user), ks.EntityNeighborsQuery(entityId="e", entityType="topic"), graph)
    assert err.value.status_code == 401
    assert graph.method_calls == []


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
