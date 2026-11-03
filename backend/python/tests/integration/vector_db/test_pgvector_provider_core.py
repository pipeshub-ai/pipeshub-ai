"""The pgvector provider's collections, stamps and per-org questions against a migrated database, through PgBouncer.

Run: KERNEL_TEST_PG_BINS=<pg bin> KERNEL_TEST_PGBOUNCER=<pgbouncer> pytest tests/integration/vector_db/test_pgvector_provider_core.py -m integration
"""
from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

import pytest

from app.core import org_context
from app.services.vector_db.models import CollectionConfig
from app.services.vector_db.pgvector.config import PgVectorConfig
from app.services.vector_db.pgvector.errors import (
    CollectionAlreadyExistsError,
    CollectionNotFoundError,
    OrgRequired,
    PgVectorError,
)
from app.services.vector_db.pgvector.pgvector import PgVectorService
from tests.integration.db.conftest import cluster, env  # noqa: F401  (fixtures)

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
