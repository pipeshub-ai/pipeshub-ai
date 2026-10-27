"""A push into a knowledge base that is being deleted: the pre-commit read and the create-only fallback."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import CollectionNames
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
    PushClaimRefused,
)
from app.connectors.sources.localKB.push.contract import PushRefusal
from app.connectors.sources.localKB.push.service import KbPushService
from app.services.graph_db.push_claim import PushClaim

KB, ORG, KEY = "kb1", "org1", "rec-key"


def service(kb: dict | None) -> KbPushService:
    graph = MagicMock()
    graph.get_document = AsyncMock(return_value=kb)
    graph.delete_nodes_and_edges = AsyncMock()
    return KbPushService(
        graph_provider=graph, kb_service=MagicMock(), processor_for_kb=AsyncMock(), schema_registry=MagicMock(),
        storage=MagicMock(), worker=MagicMock(), spool=MagicMock(), publish_event=AsyncMock(),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(("kb", "status", "reason"), [({"status": "DELETING"}, 409, "KB_BEING_DELETED"), (None, 404, "KB_NOT_FOUND")])
async def test_a_create_in_a_kb_that_is_deleting_or_gone_is_removed_with_its_document(kb, status, reason) -> None:
    svc = service(kb)

    with pytest.raises(PushRefusal) as e:
        await svc._undo_create_in_deleted_kb(KEY, KB, ORG, "doc-1")

    assert (e.value.status, e.value.reason) == (status, reason)
    svc.graph.delete_nodes_and_edges.assert_awaited_once_with([KEY], CollectionNames.RECORDS.value)
    events = [c.args[0] for c in svc.publish_event.await_args_list]
    assert [e["eventType"] for e in events] == ["deleteStoredDocuments"]
    assert events[0]["payload"]["documentIds"] == ["doc-1"]


@pytest.mark.asyncio
@pytest.mark.parametrize("kb", [{}, {"status": None}, {"status": "ACTIVE"}])
async def test_a_create_in_a_live_kb_is_left_alone(kb) -> None:
    svc = service(kb)

    await svc._undo_create_in_deleted_kb(KEY, KB, ORG, "doc-1")

    svc.graph.delete_nodes_and_edges.assert_not_awaited()
    svc.publish_event.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_failed_read_of_the_kb_is_not_taken_for_a_live_kb() -> None:
    svc = service({})
    svc.graph.get_document = AsyncMock(side_effect=RuntimeError("graph down"))

    with pytest.raises(RuntimeError):
        await svc._undo_create_in_deleted_kb(KEY, KB, ORG, "doc-1")

    svc.graph.delete_nodes_and_edges.assert_not_awaited()


def processor(kb: dict | None) -> DataSourceEntitiesProcessor:
    graph = MagicMock()
    graph.get_document = AsyncMock(return_value=kb)
    store = MagicMock()
    store.graph_provider = graph
    return DataSourceEntitiesProcessor(MagicMock(), store, AsyncMock())


@pytest.mark.asyncio
@pytest.mark.parametrize(("kb", "claim"), [({"status": "DELETING"}, PushClaim.KB_DELETING), (None, PushClaim.KB_MISSING)])
async def test_the_pre_commit_read_refuses_a_kb_that_is_deleting_or_gone(kb, claim) -> None:
    p = processor(kb)

    with pytest.raises(PushClaimRefused) as e:
        await p._refuse_if_kb_not_live(KB)

    assert e.value.claim is claim
    assert p.data_store_provider.graph_provider.get_document.await_args.kwargs == {"raise_on_error": True}


@pytest.mark.asyncio
async def test_the_pre_commit_read_lets_a_live_kb_through_and_fails_closed_on_a_failed_read() -> None:
    await processor({"status": None})._refuse_if_kb_not_live(KB)

    p = processor({})
    p.data_store_provider.graph_provider.get_document = AsyncMock(side_effect=RuntimeError("graph down"))
    with pytest.raises(RuntimeError):
        await p._refuse_if_kb_not_live(KB)
