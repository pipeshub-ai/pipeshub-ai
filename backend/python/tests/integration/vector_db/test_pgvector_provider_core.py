"""The pgvector provider's collections, stamps and per-org questions against a migrated database, through PgBouncer.

Run: KERNEL_TEST_PG_BINS=<pg bin> KERNEL_TEST_PGBOUNCER=<pgbouncer> pytest tests/integration/vector_db/test_pgvector_provider_core.py -m integration
"""
from __future__ import annotations

import logging
import uuid
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core import org_context
from app.modules.indexing.run import IndexingPipeline
from app.services.vector_db import membership
from app.services.vector_db.models import CollectionConfig
from app.services.vector_db.pgvector.config import PgVectorConfig
from app.services.vector_db.pgvector.errors import (
    CollectionAlreadyExistsError,
    CollectionNotFoundError,
    OrgRequired,
    PgVectorError,
)
from app.services.vector_db.pgvector.pgvector import PgVectorService
from tests.integration.db.conftest import cluster, env, make  # noqa: F401  (fixtures)
from tests.support.vector_db import make_collection_registry

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]
if TYPE_CHECKING:
    from contextlib import AbstractContextManager

ORG_A, ORG_B = "a1" * 12, "b2" * 12


def in_org(org: str) -> AbstractContextManager[object]:
    return org_context.bind(org_context.OrgContext(org, None, "service", "test", "-"))


@pytest.fixture
async def svc(env):  # noqa: ANN201, F811
    port = env.start_bouncer()
    s = await PgVectorService.create(PgVectorConfig(dsn=env.dsn("indexing", port), jobs_dsn=env.dsn("jobs", port), placement="none"))
    yield s
    await s.disconnect()


async def test_the_default_collections_exist_and_a_new_one_is_created_once(svc) -> None:
    names = {c.name for c in await svc.get_collections()}
    assert {"records", "entities"} <= names
    assert await svc.collection_exists("records") is True and await svc.collection_exists("nope") is False
    name = f"c_{uuid.uuid4().hex[:8]}"
    await svc.create_collection(name, CollectionConfig(enable_sparse=False, sparse_idf=False))
    assert await svc.collection_exists(name)
    row = await svc.get_collection(name)
    assert (row.sparse_enabled, row.sparse_idf, row.status) == (False, False, "ready")
    with pytest.raises(CollectionAlreadyExistsError, match="already exists"):
        await svc.create_collection(name)


async def test_a_collection_that_is_not_there(svc) -> None:
    info = await svc.get_collection_info("nope")
    assert info.exists is False and info.points_count == 0
    with pytest.raises(CollectionNotFoundError, match="does not exist"):
        await svc.get_collection("nope")
    await svc.delete_collection("nope")                          # not an error, as on the other providers


async def test_a_deleted_collection_is_gone_at_once_and_its_name_can_be_used_again(svc) -> None:
    name = f"c_{uuid.uuid4().hex[:8]}"
    await svc.create_collection(name)
    await svc.delete_collection(name)
    assert await svc.collection_exists(name) is False
    await svc.create_collection(name)                            # the old rows drain in the background, the name is free
    assert await svc.collection_exists(name)


async def test_creating_a_collection_without_the_jobs_credential_names_the_setting(env) -> None:  # noqa: F811
    port = env.start_bouncer()
    s = await PgVectorService.create(PgVectorConfig(dsn=env.dsn("indexing", port), placement="none"))
    try:
        with pytest.raises(PgVectorError, match="PGVECTOR_JOBS_DSN"):
            await s.create_collection("c_nojobs")
    finally:
        await s.disconnect()


async def test_the_stamp_is_set_once(svc) -> None:
    assert await svc.get_collection_stamp("records") is None
    assert await svc.set_collection_stamp("records", "first") == "first"
    assert await svc.set_collection_stamp("records", "second") == "first"      # the one in force
    assert await svc.get_collection_stamp("records") == "first"
    with pytest.raises(CollectionNotFoundError):
        await svc.set_collection_stamp("nope", "x")


async def _put(env, org: str, point: str, space: int = 1, coll: int = 1, tombstone: bool = False) -> None:  # noqa: F811
    c = await env.admin()            # the administrator bypasses row-level security: the test seeds both orgs' rows
    try:
        sk = await c.fetchval(
            "INSERT INTO retrieval.points (org_id, collection_id, space_id, point_id, group_key, embedding, embedding_dim) "
            "VALUES ($1, $2, $3, $4, $5, '[0.5,0.25,-0.125,1]'::ext.halfvec, 4) RETURNING point_sk", org, coll, space, uuid.UUID(point), point)
        if tombstone:
            await c.execute("INSERT INTO retrieval.point_tombstones (org_id, point_sk) VALUES ($1, $2)", org, sk)
    finally:
        await c.close()


async def test_the_info_and_questions_about_spaces_are_each_orgs_own(svc, env) -> None:  # noqa: F811
    assert (await svc.get_collection_info("records")).dense_dimension is None
    for org in (ORG_A, ORG_B):
        await svc._catalog.activate_space(org, "p:m:4", 4)
    await _put(env, ORG_A, "00000000-0000-0000-0000-00000000000a")
    await _put(env, ORG_A, "00000000-0000-0000-0000-0000000000a2", tombstone=True)         # kept in safe mode, not live
    with in_org(ORG_A):
        info = await svc.get_collection_info("records")
        assert (info.exists, info.dense_dimension, info.points_count) == (True, 4, 1)
        assert await svc.org_holds_points() is True
        assert await svc.org_space_accepts("p:m:4", 4) is True and await svc.org_space_accepts("p:m:4", 8) is False
    with in_org(ORG_B):
        info = await svc.get_collection_info("records")
        assert (info.dense_dimension, info.points_count) == (4, 0)                          # another org's points are not counted
        assert await svc.org_holds_points() is False
    with pytest.raises(OrgRequired):
        await svc.org_holds_points()


async def test_orgs_of_virtual_records_names_only_orgs_that_hold_them(svc, env) -> None:  # noqa: F811
    for org in (ORG_A, ORG_B):
        await svc._catalog.activate_space(org, "p:m:4", 4)
    c = await env.admin()
    try:
        for org, vrid in ((ORG_A, "vr-1"), (ORG_B, "vr-1"), (ORG_A, "vr-2")):
            await c.execute(
                "INSERT INTO retrieval.points (org_id, collection_id, space_id, point_id, group_key, embedding, embedding_dim) "
                "VALUES ($1, 1, 1, $2, $3, '[1,0,0,0]'::ext.halfvec, 4)", org, uuid.uuid4(), vrid)
    finally:
        await c.close()
    found = await svc.orgs_of_virtual_records(["vr-1", "vr-2", "vr-none"])
    assert found == {"vr-1": {ORG_A, ORG_B}, "vr-2": {ORG_A}}
    assert await svc.orgs_of_virtual_records([]) == {}


async def test_a_provider_works_again_after_a_disconnect_and_a_connect(env) -> None:  # noqa: F811
    port = env.start_bouncer()
    s = await PgVectorService.create(PgVectorConfig(dsn=env.dsn("indexing", port), jobs_dsn=env.dsn("jobs", port), placement="own_leaf"))
    try:
        await s.disconnect()
        with pytest.raises(PgVectorError, match="not connected"):
            await s.get_collections()
        await s.connect()
        assert s.placement is not None and s.placement._db is s._db               # the new runtime, not the released one
        assert {c.name for c in await s.get_collections()} >= {"records", "entities"}
        assert (await s.health_check()).status.value == "healthy"
    finally:
        await s.disconnect()


# ---- the delete of a virtual record: release its mapping and stored content unless another org still holds it ----------------------------
async def _put_vrid(env, org: str, vrid: str) -> None:  # noqa: F811
    c = await env.admin()
    try:
        await c.execute(
            "INSERT INTO retrieval.points (org_id, collection_id, space_id, point_id, group_key, vrid, embedding, embedding_dim) "
            "VALUES ($1, 1, 1, $2, $3, $3, '[1,0,0,0]'::ext.halfvec, 4)", org, uuid.uuid4(), vrid)
    finally:
        await c.close()


async def _vrids_of(env, org: str) -> set[str]:  # noqa: F811
    c = await env.admin()
    try:
        return {r[0] for r in await c.fetch("SELECT vrid FROM retrieval.points WHERE org_id = $1", org)}
    finally:
        await c.close()


@pytest.fixture
def pipeline(svc, monkeypatch):  # noqa: ANN201
    """The indexing pipeline over the real store; the graph knows no record of any vrid, and storage records what it releases."""
    monkeypatch.setattr(membership, "EMPTY_CONFIRM_DELAY_SECONDS", 0)
    graph = MagicMock()
    graph.get_records_by_virtual_record_id = AsyncMock(return_value=[])
    graph.delete_nodes = AsyncMock()
    stored = MagicMock()
    stored.release_virtual_records = AsyncMock(return_value=[])
    p = IndexingPipeline(logging.getLogger("test"), MagicMock(), graph, make_collection_registry("records"), svc, stored_content=stored)
    p.collection_locator.all_collections = AsyncMock(return_value=["records"])
    return p


async def _seed(svc, env, holders: dict[str, list[str]]) -> None:  # noqa: F811
    for org in (ORG_A, ORG_B):
        await svc._catalog.activate_space(org, "p:m:4", 4)
    for org, vrids in holders.items():
        for vrid in vrids:
            await _put_vrid(env, org, vrid)


async def test_deleting_the_last_copy_releases_the_mapping_and_the_stored_content(svc, env, pipeline) -> None:  # noqa: F811
    await _seed(svc, env, {ORG_A: ["vr-last"]})
    with in_org(ORG_A):
        await svc.delete_points("records", await svc.filter_collection(must={"orgId": ORG_A, "virtualRecordId": "vr-last"}))
        assert await pipeline._forget_virtual_record_mappings(["vr-last"], org_id=ORG_A) == []
    pipeline.stored_content.release_virtual_records.assert_awaited_once_with(["vr-last"], org_id=ORG_A)


async def test_a_vrid_another_org_still_holds_keeps_its_mapping_and_stored_content(svc, env, pipeline) -> None:  # noqa: F811
    await _seed(svc, env, {ORG_A: ["vr-shared"], ORG_B: ["vr-shared"]})
    with in_org(ORG_A):
        assert await pipeline.rewrite_or_delete_vector_membership("vr-shared") == "kept"
        assert await pipeline._forget_virtual_record_mappings(["vr-shared"], org_id=ORG_A) == []
    assert await _vrids_of(env, ORG_A) == set() and await _vrids_of(env, ORG_B) == {"vr-shared"}     # only the caller's points went
    pipeline.stored_content.release_virtual_records.assert_not_awaited()
    pipeline.graph_provider.delete_nodes.assert_not_awaited()


async def test_the_orphan_retry_deletes_the_points_and_releases_the_mapping(svc, env, pipeline) -> None:  # noqa: F811
    await _seed(svc, env, {ORG_A: ["vr-orphan"], ORG_B: ["vr-other"]})
    with in_org(ORG_A):                                     # what the orphan sweep runs for a mapping row no record references
        assert await pipeline.rewrite_or_delete_vector_membership("vr-orphan") == "deleted"
    assert await _vrids_of(env, ORG_A) == set() and await _vrids_of(env, ORG_B) == {"vr-other"}
    pipeline.stored_content.release_virtual_records.assert_awaited_once()
    assert pipeline.stored_content.release_virtual_records.await_args.args == (["vr-orphan"],)


# ---- the orphan sweep on PostgreSQL: each org has its own mapping row for a shared vrid ---------------------------------------------------
async def test_the_orphan_sweep_keeps_and_releases_each_orgs_mapping_row(svc, env, make, monkeypatch) -> None:  # noqa: F811
    """Two orgs each have their own mapping row for one vrid and no record left on it; a third org still holds points. The real
    PostgreSQL graph and the real store, both as the indexing role, the store answering the hold check with ``vrid_held_elsewhere``.
    Each org's points go on the first tick and both rows stay; a backed-off row is not swept again; once the third org's points are
    gone, each row is released after its own back-off, and one org's recheck does not release the other's."""
    import app.indexing_main as m
    from app.config.constants.arangodb import CollectionNames
    from app.services.graph_db.postgres.provider import PostgresGraphProvider

    monkeypatch.setattr(membership, "EMPTY_CONFIRM_DELAY_SECONDS", 0)
    monkeypatch.setattr(membership, "_shared_row_kept", set())
    monkeypatch.setattr(m, "_orphan_sweep_cursor", {})
    monkeypatch.setattr(m, "_orphan_kept_until", {})
    holder, vrid, mapping = "c3" * 12, "vr-swept", CollectionNames.VIRTUAL_RECORD_TO_DOC_ID_MAPPING.value
    graph = PostgresGraphProvider(logging.getLogger("test"), await make("indexing", env.start_bouncer()))
    pipeline = IndexingPipeline(logging.getLogger("test"), MagicMock(), graph, make_collection_registry("records"), svc)
    pipeline.collection_locator.all_collections = AsyncMock(return_value=["records"])
    for org in (ORG_A, ORG_B, holder):
        await svc._catalog.activate_space(org, "p:m:4", 4)
        await _put_vrid(env, org, vrid)
    for org in (ORG_A, ORG_B):
        with in_org(org):
            assert await graph.create_node_if_absent(vrid, mapping, {"id": vrid, "orgId": org, "virtualRecordId": vrid})

    async def sweep(org: str) -> int:
        with in_org(org):
            return await m._sweep_orphaned_virtual_record_mappings(
                graph_provider=graph, pipeline=pipeline, logger=logging.getLogger("test"), page_size=100, org_id=org)

    async def row(org: str) -> dict | None:
        with in_org(org):
            return await graph.get_document(vrid, mapping)

    assert [await sweep(ORG_A), await sweep(ORG_B)] == [0, 0]
    assert await _vrids_of(env, ORG_A) == set() and await _vrids_of(env, ORG_B) == set() and await _vrids_of(env, holder) == {vrid}
    assert await row(ORG_A) is not None and await row(ORG_B) is not None
    assert set(m._orphan_kept_until) == {(ORG_A, vrid), (ORG_B, vrid)}
    assert [await sweep(ORG_A), await sweep(ORG_B)] == [0, 0]               # backed off: not swept again

    with in_org(holder):
        await svc.delete_points("records", await svc.filter_collection(must={"orgId": holder, "virtualRecordId": vrid}))
    m._orphan_kept_until[(ORG_A, vrid)] = 0.0                                 # ORG_A's back-off ran out, ORG_B's did not
    assert await sweep(ORG_A) == 1 and await row(ORG_A) is None
    assert await sweep(ORG_B) == 0 and await row(ORG_B) is not None
    m._orphan_kept_until[(ORG_B, vrid)] = 0.0
    assert await sweep(ORG_B) == 1 and await row(ORG_B) is None
    assert m._orphan_kept_until == {}
