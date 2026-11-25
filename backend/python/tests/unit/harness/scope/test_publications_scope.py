"""SYNC4 S9 and S11 (MAINSTUDY test 8): on a scoped turn the scope wins over publications.

A record outside the turn's selection is neither searched nor read by id, even when a publication would disclose it
to the reader. On an unscoped turn publications apply as before. ``ENABLE_KNOWLEDGE_PUBLICATIONS`` on throughout.

Written test-first, before ``origin/main`` was merged: red until S9 (the publications search arm builds its filter from
the enumerated record ids and skips the per-hit check and every scope).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.actions.knowledge_graph.ops.scope import ids_outside_scope
from app.harness.knowledge.read_gate import RecordReadGate
from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
from harness_core.knowledge.publications import ReadGrant
from tests.support import scope_world as sw

PUBLISHED = {sw.VRID[sw.REC_B]: sw.REC_B}


@pytest.fixture(autouse=True)
def _fresh_memo() -> None:
    from app.modules.retrieval import selection_scope

    selection_scope._memo.clear()


def _graph() -> tuple[Any, sw.ScopeWorld]:
    world = sw.ScopeWorld.build()
    world.revoke(sw.APP_B)  # the reader's own access stops at app-a: app-b's record reaches them only published
    graph = MagicMock(spec=IGraphDBProvider)
    return world.install(graph), world


class Published:
    """The reader's publications: app-b's record, disclosed in full."""

    async def enabled(self) -> bool:
        return True

    async def read_grants(self, user_id: str | None, org_id: str, record_ids: list[str], *, own: Any) -> dict[str, ReadGrant]:  # noqa: ANN401
        return {
            rid: ReadGrant("own") if rid in own else ReadGrant("published_full" if rid == sw.REC_B else "denied")
            for rid in record_ids
        }


async def test_s11_unscoped_a_publication_still_opens_a_record_by_id() -> None:
    graph, _ = _graph()
    gate = RecordReadGate(graph, sw.ORG, sw.USER, publications=Published())  # type: ignore[arg-type]
    assert await gate.decide_many([sw.REC_B]) == {sw.REC_B: "published_full"}


async def test_s11_scoped_a_record_outside_the_selection_is_refused_even_when_published() -> None:
    graph, _ = _graph()
    state = {"filters": {"apps": [], "kb": [], "records": [sw.FOLDER], "strictScope": True}}
    assert await ids_outside_scope(state, graph, sw.USER, sw.ORG, [sw.REC_IN, sw.REC_B]) == [sw.REC_B]


# S9: the search's publications arm.


def _service(graph: Any) -> Any:  # noqa: ANN401
    with patch("app.modules.retrieval.retrieval_service.SparseEmbedder"):
        from app.modules.retrieval.retrieval_service import RetrievalService

        registry = MagicMock()
        registry.strategy_name = "single"
        vector = MagicMock()
        vector.filter_collection = AsyncMock(return_value=MagicMock())
        config = MagicMock()
        config.get_config = AsyncMock(return_value={})
        service = RetrievalService(
            logger=MagicMock(), config_service=config, collection_registry=registry,
            vector_db_service=vector, graph_provider=graph, blob_store=MagicMock(),
        )
    pubs = service.publications
    pubs.enabled = AsyncMock(return_value=True)
    pubs.principal = AsyncMock(return_value=MagicMock(org_id=sw.ORG, user_id=sw.USER))
    pubs.published_map = AsyncMock(return_value={
        vrid: (rid, MagicMock(id="pub-1", mode="full")) for vrid, rid in PUBLISHED.items()
    })
    hits = [sw.VRID[r] for r in (sw.REC_IN, sw.REC_OUT, sw.REC_B)]
    service._execute_parallel_searches = AsyncMock(return_value=[
        {"score": 0.9, "content": "c", "citationType": "vectordb|document", "metadata": {"virtualRecordId": v, "orgId": sw.ORG}}
        for v in hits
    ])
    return service


async def _returned(filters: dict[str, Any]) -> set[str]:
    graph, _ = _graph()
    graph.get_records_by_record_ids = AsyncMock(side_effect=lambda ids, *_a, **_k: [
        {"_key": i, "id": i, "virtualRecordId": sw.VRID.get(i), "origin": "CONNECTOR", "recordName": i,
         "mimeType": "text/plain", "connectorName": "X", "connectorId": sw.APP_A, "webUrl": "http://x/" + i} for i in ids
    ])
    service = _service(graph)
    with patch("app.agents.actions.knowledge_graph.location.resolve_ancestor_locations", AsyncMock(return_value={})):
        result = await service.search_with_filters(
            queries=["q"], user_id=sw.USER, org_id=sw.ORG, filter_groups=filters,
        )
    return {r["metadata"]["virtualRecordId"] for r in result.get("searchResults", [])}


async def test_s9_a_scoped_search_never_returns_a_published_record_outside_the_selection() -> None:
    returned = await _returned({"apps": [], "kb": [], "records": [sw.FOLDER], "strictScope": True})
    assert sw.VRID[sw.REC_B] not in returned
    assert sw.VRID[sw.REC_OUT] not in returned


async def test_s9_a_scoped_search_still_returns_a_published_record_inside_the_scope() -> None:
    """MAINSTUDY B4: a publication is admitted where every scope admits its record; an agent whose sources hold the
    published record finds it (the engine matrix's KN cell)."""
    returned = await _returned({"apps": [sw.APP_B], "kb": [], "allowedApps": [sw.APP_B]})
    assert sw.VRID[sw.REC_B] in returned
    returned = await _returned({"apps": [sw.APP_A], "kb": [], "allowedApps": [sw.APP_A]})
    assert sw.VRID[sw.REC_B] not in returned


async def test_s9_an_unscoped_search_still_returns_what_is_published_to_the_reader() -> None:
    returned = await _returned({})
    assert sw.VRID[sw.REC_B] in returned


# S11 group membership: a record published under a selected record group lies inside the selection by place.


def _published_world(shape: str) -> Any:  # noqa: ANN401
    world = sw.ScopeWorld.build()
    world.revoke(sw.REC_B)  # the reader reaches grp-b but not rec-b on their own: rec-b reaches them only published
    return world.install(MagicMock(spec=IGraphDBProvider), shape=shape)


async def _published_inside(shape: str, filters: dict[str, Any], ids: list[str]) -> set[str]:
    from app.harness.knowledge.scope_overlay import published_within_scope

    graph = _published_world(shape)
    with patch("app.harness.knowledge.service.KnowledgePublications.enabled", AsyncMock(return_value=True)):
        return await published_within_scope({"filters": filters}, graph, sw.USER_KEY, sw.ORG, ids)


@pytest.mark.parametrize("shape", ["neo4j", "arango"])
async def test_s11_a_record_published_under_a_selected_group_passes_on_to_the_gate(shape: str) -> None:
    filters = {"apps": [], "kb": [], "recordGroups": [sw.GRP_B], "strictScope": True}
    assert await _published_inside(shape, filters, [sw.REC_B]) == {sw.REC_B}


@pytest.mark.parametrize("shape", ["neo4j", "arango"])
async def test_s11_a_record_under_another_group_stays_outside_the_selection(shape: str) -> None:
    """Scope wins: the app edge a record also carries and a group the turn did not select admit nothing."""
    filters = {"apps": [], "kb": [], "recordGroups": [sw.GRP_1], "strictScope": True}
    assert await _published_inside(shape, filters, [sw.REC_B, sw.REC_G2]) == set()


@pytest.mark.parametrize("shape", ["neo4j", "arango"])
async def test_s11_a_nested_record_under_a_selected_group_is_inside_by_place(shape: str) -> None:
    """rec-in sits in a folder under grp-1: its group comes from its own BELONGS_TO edge, not the folder."""
    filters = {"apps": [], "kb": [], "recordGroups": [sw.GRP_1], "strictScope": True}
    assert await _published_inside(shape, filters, [sw.REC_IN, sw.REC_OUT, sw.REC_G2]) == {sw.REC_IN, sw.REC_OUT}
