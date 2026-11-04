"""Every vector delete and update names its org.

Two orgs hold points under the same virtual record id (it can happen on an upgraded deployment, and the
stores must not depend on it not happening). For each caller family a call made as org A removes or rewrites
only A's points, and a call with no org bound raises ``OrgRequired`` and touches nothing. Runs on the
in-memory service, which enforces the same filter rules as the providers.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from app.core import org_context
from app.services.vector_db.collection_locator import StaticCollectionLocator
from app.services.vector_db.errors import OrgRequired
from app.services.vector_db.filters import build_filter_expression
from app.services.vector_db.membership import (
    rewrite_or_delete_virtual_record,
    sync_vector_membership,
    write_membership_batch_locked,
)
from app.services.vector_db.models import CollectionConfig, FilterMode, VectorPoint
from app.services.vector_db.qdrant.utils import QdrantUtils
from app.services.vector_db.strategy import DeleteAction, DeleteContext, DeleteScope
from tests.support.in_memory_vector_db import InMemoryVectorDBService
from tests.support.vector_db import make_collection_registry

if TYPE_CHECKING:
    from collections.abc import Iterator
    from contextlib import AbstractContextManager

    from app.modules.indexing.run import IndexingPipeline
    from app.modules.transformers.vectorstore import VectorStore

A, B = "a" * 24, "b" * 24
VRID = "vr-1"
COLL = "records"


def _as(org: str) -> AbstractContextManager[org_context.OrgContext]:
    return org_context.for_org(org, job="test")


def _point(org: str, block: str, connectors: tuple[str, ...] = ("c1",)) -> VectorPoint:
    return VectorPoint(
        id=f"{org[:1]}-{block}",
        dense_vector=[1.0],
        payload={
            "page_content": block,
            "metadata": {"orgId": org, "virtualRecordId": VRID, "blockId": block},
            "connectorIds": list(connectors),
            "recordGroupIds": [],
        },
    )


@pytest_asyncio.fixture
async def db() -> InMemoryVectorDBService:
    service = InMemoryVectorDBService()
    await service.create_collection(COLL, CollectionConfig(embedding_size=1))
    await service.upsert_points(COLL, [_point(o, b) for o in (A, B) for b in ("b1", "b2")])
    return service


def _ids(db: InMemoryVectorDBService) -> set[str]:
    return set(db.collections[COLL])


def _payload(db: InMemoryVectorDBService, org: str) -> dict:
    return db.collections[COLL][f"{org[:1]}-b1"].payload


def _graph(keys: list[str], org: str = A) -> AsyncMock:
    graph = AsyncMock()
    graph.get_records_by_virtual_record_id = AsyncMock(return_value=keys)
    graph.get_document = AsyncMock(return_value={"connectorId": "c9", "recordGroupId": None, "orgId": org})
    graph.get_edges_from_node = AsyncMock(return_value=[])
    return graph


@pytest.fixture(autouse=True)
def _no_confirm_delay() -> Iterator[None]:
    with patch("app.services.vector_db.membership.EMPTY_CONFIRM_DELAY_SECONDS", 0), patch(
        "app.modules.indexing.run.EMPTY_CONFIRM_DELAY_SECONDS", 0
    ):
        yield


def _pipeline(db: InMemoryVectorDBService) -> IndexingPipeline:
    from app.modules.indexing.run import IndexingPipeline

    pipeline = IndexingPipeline(
        logger=MagicMock(), config_service=AsyncMock(), graph_provider=_graph([]),
        collection_registry=make_collection_registry(), vector_db_service=db,
    )
    pipeline.collection_locator = StaticCollectionLocator([COLL])
    pipeline._forget_virtual_record_mappings = AsyncMock(return_value=[])
    return pipeline


def _vector_store(db: InMemoryVectorDBService) -> VectorStore:
    from app.modules.transformers.vectorstore import VectorStore
    from app.services.vector_db.models import VectorDBCapabilities

    db.get_capabilities = MagicMock(return_value=VectorDBCapabilities())
    return VectorStore(
        logger=MagicMock(), config_service=AsyncMock(), graph_provider=_graph([]),
        collection_registry=make_collection_registry(), vector_db_service=db,
    )


# the in-memory service itself: org A cannot touch org B's points through any write
async def test_org_a_cannot_delete_or_rewrite_org_bs_points(db) -> None:
    filt_a = await db.filter_collection(must={"orgId": A, "virtualRecordId": VRID})
    await db.set_payload(COLL, {"connectorIds": ["c7"]}, filt_a)
    await db.overwrite_payload(COLL, {"page_content": "x", "metadata": {"orgId": A, "virtualRecordId": VRID}}, filt_a)
    await db.update_payload_by_ids(COLL, ["a-b1", "b-b1"], {"connectorIds": ["c8"]}, org_id=A)
    assert _payload(db, B)["connectorIds"] == ["c1"] and _payload(db, B)["page_content"] == "b1"
    await db.delete_points(COLL, filt_a)
    assert _ids(db) == {"b-b1", "b-b2"}


async def test_a_write_filter_without_an_org_touches_nothing(db) -> None:
    no_org = await db.filter_collection(must={"virtualRecordId": VRID})
    for call in (
        db.delete_points(COLL, no_org),
        db.set_payload(COLL, {"connectorIds": ["c7"]}, no_org),
        db.overwrite_payload(COLL, {"x": 1}, no_org),
        db.update_payload_by_ids(COLL, ["a-b1"], {"connectorIds": ["c8"]}, org_id=""),
    ):
        with pytest.raises(OrgRequired):
            await call
    assert len(_ids(db)) == 4
    assert _payload(db, A)["connectorIds"] == ["c1"]


async def test_the_whole_org_removal_stays_allowed(db) -> None:
    await db.delete_points(COLL, await db.filter_collection(must={"metadata.orgId": A}))
    assert _ids(db) == {"b-b1", "b-b2"}


@pytest.mark.parametrize("org", [None, "", "  ", []])
def test_a_blank_org_never_widens_a_filter(org) -> None:
    with pytest.raises(OrgRequired):
        build_filter_expression(
            FilterMode.MUST, must={"orgId": org, "virtualRecordId": VRID},
            build_conditions=QdrantUtils.build_conditions_generic,
        )


# a block reconciliation delete
async def test_block_delete_is_scoped_to_its_org(db) -> None:
    with _as(A):
        await _vector_store(db).delete_blocks_by_ids({"b1"}, VRID, COLL)
    assert _ids(db) == {"a-b2", "b-b1", "b-b2"}


async def test_block_delete_without_an_org_raises(db) -> None:
    with pytest.raises(OrgRequired):
        await _vector_store(db).delete_blocks_by_ids({"b1"}, VRID, COLL)
    assert len(_ids(db)) == 4


# a full replace of a record's points
async def test_embedding_delete_is_scoped_to_its_org(db) -> None:
    with _as(B):
        await _vector_store(db).delete_embeddings(VRID, COLL)
    assert _ids(db) == {"a-b1", "a-b2"}


async def test_embedding_delete_without_an_org_raises(db) -> None:
    with pytest.raises(OrgRequired):
        await _vector_store(db).delete_embeddings(VRID, COLL)
    assert len(_ids(db)) == 4


# a bulk delete by virtual record ids
async def test_bulk_delete_is_scoped_to_its_org(db) -> None:
    result = await _pipeline(db).bulk_delete_embeddings([VRID], A)
    assert result["virtual_record_ids_deleted"] == 1
    assert _ids(db) == {"b-b1", "b-b2"}


async def test_bulk_delete_takes_the_bound_org_when_none_is_given(db) -> None:
    with _as(B):
        await _pipeline(db).bulk_delete_embeddings([VRID])
    assert _ids(db) == {"a-b1", "a-b2"}


async def test_bulk_delete_without_any_org_deletes_nothing(db) -> None:
    from app.exceptions.indexing_exceptions import EmbeddingDeletionError

    with pytest.raises(EmbeddingDeletionError):
        await _pipeline(db).bulk_delete_embeddings([VRID])
    assert len(_ids(db)) == 4


# a connector purge (and the shared-point strip and scans)
async def test_connector_purge_is_scoped_to_its_org(db) -> None:
    pipeline = _pipeline(db)
    pipeline.collection_registry.resolve_delete_scope = AsyncMock(
        return_value=DeleteScope(action=DeleteAction.FILTERED_DELETE, collection_names=[COLL])
    )
    with _as(A):
        await pipeline.purge_connector(DeleteContext(org_id=A, connector_id="c1"))
    assert _ids(db) == {"b-b1", "b-b2"}


async def test_connector_purge_without_an_org_raises(db) -> None:
    pipeline = _pipeline(db)
    pipeline.collection_registry.resolve_delete_scope = AsyncMock(
        return_value=DeleteScope(action=DeleteAction.FILTERED_DELETE, collection_names=[COLL])
    )
    with pytest.raises(OrgRequired):
        await pipeline.purge_connector(DeleteContext(org_id="", connector_id="c1"))
    assert len(_ids(db)) == 4


async def test_connector_scans_see_only_the_bound_org(db) -> None:
    pipeline = _pipeline(db)
    other = _point(B, "x")
    other.payload["metadata"]["virtualRecordId"] = "vr-b-only"
    await db.upsert_points(COLL, [other])
    with _as(A):
        assert (await pipeline._scan_exclusively_owned_vrids([COLL], "c1")).ids == [VRID]
    with _as(B):
        assert set((await pipeline._scan_exclusively_owned_vrids([COLL], "c1")).ids) == {VRID, "vr-b-only"}
    with pytest.raises(OrgRequired):
        await pipeline._scan_exclusively_owned_vrids([COLL], "c1")


# the membership writes
async def test_membership_batch_write_is_scoped_to_its_org(db) -> None:
    with _as(A):
        await write_membership_batch_locked(db, COLL, [VRID], ["c9"], None, None)
    assert _payload(db, A)["connectorIds"] == ["c9"]
    assert _payload(db, B)["connectorIds"] == ["c1"]


async def test_membership_batch_write_without_an_org_raises(db) -> None:
    with pytest.raises(OrgRequired):
        await write_membership_batch_locked(db, COLL, [VRID], ["c9"], None, None)
    assert _payload(db, A)["connectorIds"] == ["c1"]


async def test_membership_sync_is_scoped_to_its_org(db) -> None:
    with _as(A):
        await sync_vector_membership(db, StaticCollectionLocator([COLL]), _graph(["r1"]), VRID, MagicMock())
    assert _payload(db, A)["connectorIds"] == ["c9"]
    assert _payload(db, B)["connectorIds"] == ["c1"]


async def test_membership_sync_without_an_org_raises(db) -> None:
    with pytest.raises(OrgRequired):
        await sync_vector_membership(db, StaticCollectionLocator([COLL]), _graph(["r1"]), VRID, MagicMock())
    assert _payload(db, A)["connectorIds"] == ["c1"]


async def test_rewrite_or_delete_deletes_only_its_orgs_points(db) -> None:
    with _as(B):
        outcome = await rewrite_or_delete_virtual_record(db, StaticCollectionLocator([COLL]), _graph([]), VRID, MagicMock())
    assert outcome == "deleted"
    assert _ids(db) == {"a-b1", "a-b2"}


async def test_rewrite_or_delete_without_an_org_raises(db) -> None:
    with pytest.raises(OrgRequired):
        await rewrite_or_delete_virtual_record(db, StaticCollectionLocator([COLL]), _graph([]), VRID, MagicMock())
    assert len(_ids(db)) == 4


# a system context is not an org
async def test_a_system_context_cannot_delete(db) -> None:
    with org_context.system("test"), pytest.raises(OrgRequired):
        await _vector_store(db).delete_embeddings(VRID, COLL)
    assert len(_ids(db)) == 4


# the maintenance paths bind the org they act for
async def test_the_orphan_sweep_binds_each_mappings_org_and_skips_one_without() -> None:
    from app.indexing_main import (
        _sweep_orphaned_virtual_record_mappings as sweep_orphaned_virtual_record_mappings,
    )

    graph = AsyncMock()
    graph.get_documents_paginated = AsyncMock(
        return_value=[{"_key": "v-a", "orgId": A}, {"_key": "v-none"}]
    )
    graph.get_virtual_record_ids_in_use = AsyncMock(return_value=set())
    seen: list[tuple[str, str]] = []

    async def _cleanup(vrid: str) -> str:
        seen.append((vrid, org_context.current_org_id()))
        return "deleted"

    pipeline = MagicMock()
    pipeline.rewrite_or_delete_vector_membership = _cleanup
    pipeline.orgs_holding_points = AsyncMock(return_value=set())  # no org holds points of v-none: nothing to clean for it
    await sweep_orphaned_virtual_record_mappings(graph_provider=graph, pipeline=pipeline, logger=MagicMock(), page_size=10)
    assert seen == [("v-a", A)]
    pipeline.orgs_holding_points.assert_awaited_once_with("v-none")  # it was looked up, not skipped by an error


async def test_the_membership_backfill_binds_the_org_of_each_record() -> None:
    from app.modules.indexing.vector_membership_backfill import (
        run_vector_membership_backfill_tick,
    )

    graph = AsyncMock()
    graph.get_app_needing_vector_membership_backfill = AsyncMock(return_value={"_key": "app-1"})
    graph.page_records_for_vector_membership_backfill = AsyncMock(return_value=[
        {"_key": "r1", "virtualRecordId": "v1", "orgId": A},
        {"_key": "r2", "virtualRecordId": "v2", "orgId": None},
    ])
    graph.update_node = AsyncMock()
    seen: list[tuple[str, str]] = []

    async def _sync(vrid: str) -> None:
        seen.append((vrid, org_context.current_org_id()))

    pipeline = MagicMock()
    pipeline.sync_vector_membership = _sync
    pipeline.orgs_holding_points = AsyncMock(return_value=set())  # v2: no org on either side and no points
    lock = AsyncMock()
    lock.try_acquire = AsyncMock(return_value=True)
    await run_vector_membership_backfill_tick(
        logger=MagicMock(), graph_provider=graph, pipeline=pipeline, lock=lock, page_size=10, vrid_pause_ms=0,
    )
    assert seen == [("v1", A)]  # v2 names no org: failed, never synced for "any"
    pipeline.orgs_holding_points.assert_awaited_once_with("v2")


# a VRID shared by two orgs: each org's delete removes its own points when it has no record left
def _shared_graph(owners: dict[str, str]) -> AsyncMock:
    """Records on the VRID, key -> org; the graph answers for all orgs, as it does."""
    graph = AsyncMock()
    graph.get_records_by_virtual_record_id = AsyncMock(side_effect=lambda *a, **k: list(owners))
    graph.get_document = AsyncMock(
        side_effect=lambda key, *a, **k: {"_key": key, "orgId": owners[key], "connectorId": "c9", "recordGroupId": None}
    )
    graph.get_edges_from_node = AsyncMock(return_value=[])
    return graph


async def test_a_shared_vrid_is_deleted_per_org_as_each_orgs_last_record_goes(db) -> None:
    with _as(A):  # A's record is gone, B's remains
        outcome = await rewrite_or_delete_virtual_record(
            db, StaticCollectionLocator([COLL]), _shared_graph({"rb": B}), VRID, MagicMock()
        )
    assert outcome == "deleted"
    assert _ids(db) == {"b-b1", "b-b2"}
    with _as(B):  # now B's goes too
        outcome = await rewrite_or_delete_virtual_record(
            db, StaticCollectionLocator([COLL]), _shared_graph({}), VRID, MagicMock()
        )
    assert outcome == "deleted"
    assert _ids(db) == set()


async def test_a_shared_vrid_keeps_an_orgs_points_while_it_has_a_record(db) -> None:
    with _as(B):
        outcome = await rewrite_or_delete_virtual_record(
            db, StaticCollectionLocator([COLL]), _shared_graph({"ra": A, "rb": B}), VRID, MagicMock()
        )
    assert outcome == "rewritten"
    assert len(_ids(db)) == 4
    assert _payload(db, B)["connectorIds"] == ["c9"]
    assert _payload(db, A)["connectorIds"] == ["c1"]


async def test_bulk_delete_of_a_shared_vrid_removes_only_the_deleting_orgs_points(db) -> None:
    pipeline = _pipeline(db)
    pipeline.graph_provider = _shared_graph({"rb": B})
    await pipeline.bulk_delete_embeddings([VRID], A)
    assert _ids(db) == {"b-b1", "b-b2"}
    pipeline.graph_provider = _shared_graph({})
    await pipeline.bulk_delete_embeddings([VRID], B)
    assert _ids(db) == set()


async def test_a_shared_vrids_mapping_and_stored_content_stay_while_another_org_holds_points(db) -> None:
    """Releasing the mapping on the strength of one org's points alone would strand the other org's points."""
    pipeline = _pipeline(db)
    del pipeline._forget_virtual_record_mappings  # the real one
    pipeline.stored_content = MagicMock()
    pipeline.stored_content.release_virtual_records = AsyncMock(return_value=[])
    assert await pipeline._forget_virtual_record_mappings([VRID], org_id=A) == []
    pipeline.stored_content.release_virtual_records.assert_not_awaited()
    pipeline.graph_provider = _shared_graph({"rb": B})
    await pipeline.bulk_delete_embeddings([VRID], A)  # A's points go; B's stay
    assert await pipeline._forget_virtual_record_mappings([VRID], org_id=A) == []
    pipeline.stored_content.release_virtual_records.assert_not_awaited()
    pipeline.graph_provider = _shared_graph({})
    await pipeline.bulk_delete_embeddings([VRID], B)  # the last org's delete releases it
    pipeline.stored_content.release_virtual_records.assert_awaited_once()


async def test_bulk_delete_with_an_org_other_than_the_bound_one_raises(db) -> None:
    from app.exceptions.indexing_exceptions import EmbeddingDeletionError

    with _as(A), pytest.raises(EmbeddingDeletionError, match="differs from the bound one"):
        await _pipeline(db).bulk_delete_embeddings([VRID], B)
    assert len(_ids(db)) == 4


# the orphan sweep finds the owners of a row that names none from the points themselves
def _sweep_graph(rows: list[dict]) -> AsyncMock:
    graph = AsyncMock()
    graph.get_documents_paginated = AsyncMock(side_effect=lambda *a, **k: list(rows))
    graph.get_virtual_record_ids_in_use = AsyncMock(return_value=set())
    return graph


async def test_the_orphan_sweep_cleans_a_row_with_no_org_org_by_org_then_drops_the_row(db) -> None:
    from app.indexing_main import _sweep_orphaned_virtual_record_mappings as sweep

    pipeline = _pipeline(db)
    swept = await sweep(graph_provider=_sweep_graph([{"_key": VRID}]), pipeline=pipeline, logger=MagicMock(), page_size=10)

    assert _ids(db) == set()                       # A's and B's points both went, each under its own org
    assert pipeline._forget_virtual_record_mappings.await_count == 1  # and the row only after the last org's
    assert swept == 1


async def test_the_orphan_sweep_drops_a_row_with_no_org_and_no_points(db) -> None:
    from app.indexing_main import _sweep_orphaned_virtual_record_mappings as sweep

    pipeline = _pipeline(db)
    await sweep(graph_provider=_sweep_graph([{"_key": "vr-nothing-left"}]), pipeline=pipeline, logger=MagicMock(), page_size=10)

    pipeline._forget_virtual_record_mappings.assert_awaited_once_with(["vr-nothing-left"])
    assert len(_ids(db)) == 4


# a legacy shared VRID: the one mapping row outlives the first org's cleanup
async def test_the_mapping_row_is_kept_while_another_org_still_has_points(db) -> None:
    released: list[list[str]] = []

    async def release(ids: list[str]) -> None:
        released.append(ids)

    for _ in range(2):  # a second lap changes nothing and stays quiet
        with _as(A):
            outcome = await rewrite_or_delete_virtual_record(
                db, StaticCollectionLocator([COLL]), _shared_graph({}), VRID, MagicMock(), release_mapping=release,
            )
    assert outcome == "deleted"
    assert _ids(db) == {"b-b1", "b-b2"}
    assert released == []
    with _as(B):
        await rewrite_or_delete_virtual_record(
            db, StaticCollectionLocator([COLL]), _shared_graph({}), VRID, MagicMock(), release_mapping=release,
        )
    assert released == [[VRID]]



# an event that carries its org only explicitly (no envelope) still rewrites the membership of a VRID the org keeps
async def test_bulk_delete_rewrites_membership_for_an_explicit_org_with_none_bound(db) -> None:
    pipeline = _pipeline(db)
    pipeline.graph_provider = _shared_graph({"ra": A})
    result = await pipeline.bulk_delete_embeddings([VRID], A)
    assert result["virtual_record_ids_rewritten"] == 1
    assert _payload(db, A)["connectorIds"] == ["c9"]
    assert _payload(db, B)["connectorIds"] == ["c1"]


# the vector store deletes for the org its caller holds, not for whichever one is bound
async def test_vector_store_deletes_take_the_explicit_org_and_refuse_a_different_bound_one(db) -> None:
    from app.services.vector_db.errors import OrgMismatch

    store = _vector_store(db)
    await store.delete_embeddings(VRID, COLL, org_id=A)           # none bound: the explicit org is used
    assert _ids(db) == {"b-b1", "b-b2"}
    with _as(A), pytest.raises(OrgMismatch):
        await store.delete_blocks_by_ids({"b1"}, VRID, COLL, org_id=B)
    assert _ids(db) == {"b-b1", "b-b2"}


@pytest.mark.parametrize("payload", [{"metadata": {"orgId": B}}, {"metadata.orgId": B}, {"metadata_orgId": B}])
async def test_a_payload_cannot_move_points_to_another_org(db, payload) -> None:
    from app.services.vector_db.errors import OrgMismatch

    flt = await db.filter_collection(must={"orgId": A, "virtualRecordId": VRID})
    with pytest.raises(OrgMismatch):
        await db.set_payload(COLL, payload, flt)
    with pytest.raises(OrgMismatch):
        await db.update_payload_by_ids(COLL, ["a-b1"], payload, org_id=A)
    with pytest.raises(OrgMismatch):
        await db.overwrite_payload(COLL, payload, flt)
    assert _payload(db, A)["metadata"]["orgId"] == A


# the org-less invalidation of a scope stamp reaches the points of that record, org by org, and nothing else
async def test_marking_a_record_stale_with_no_org_bound_invalidates_every_owners_stamp_and_only_this_records(db, monkeypatch) -> None:
    from app.services.permissions.mode import ENV_SCOPE_FIELDS
    from app.services.permissions.scope_fields import ScopeFields
    from app.services.vector_db.membership import mark_scope_stale

    monkeypatch.setenv(ENV_SCOPE_FIELDS, "1")
    other = _point(A, "o1")
    other.id, other.payload["metadata"]["virtualRecordId"] = "a-other", "vr-other"
    await db.upsert_points(COLL, [other])
    trusted = ScopeFields.container().to_payload()
    for point in db.collections[COLL].values():
        point.payload.update(trusted)
        assert ScopeFields.from_payload(point.payload).trusted

    await mark_scope_stale(db, StaticCollectionLocator([COLL]), VRID, MagicMock())          # no org bound

    for pid in ("a-b1", "a-b2", "b-b1", "b-b2"):
        assert not ScopeFields.from_payload(db.collections[COLL][pid].payload).trusted, pid
    assert ScopeFields.from_payload(db.collections[COLL]["a-other"].payload).trusted
