"""The pgvector provider's lifecycle, catalog and collection methods (points are covered with the changes that add them)."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core import org_context
from app.core.db import CapabilityError, shared
from app.services.vector_db.collection_registry import _is_collection_missing
from app.services.vector_db.models import FilterMode, HealthStatus, VectorPoint
from app.services.vector_db.pgvector.catalog import CollectionRow, OrgSpace
from app.services.vector_db.pgvector.config import PgVectorConfig
from app.services.vector_db.pgvector.errors import (
    CollectionAlreadyExistsError,
    CollectionNotFoundError,
    OrgRequired,
    PgVectorError,
    PgVectorUnsupportedError,
)
from app.services.vector_db.pgvector.pgvector import UNKNOWN_MODEL, PgVectorService

if TYPE_CHECKING:
    from collections.abc import Iterator
    from contextlib import AbstractContextManager

ORG = "a" * 24
ROW = CollectionRow(7, "records", True, True, "v", "ready")


class FakeDB:
    """Stands in for KernelDB: counts starts and closes, reports the capabilities of a chosen server, answers ``run``."""

    instances: list[FakeDB] = []
    vector_version: str | None = "0.8.4"
    start_error: Exception | None = None

    def __init__(self, cfg) -> None:
        self.cfg = cfg
        self.started = self.closed = 0
        self.count = 0
        self.held = False
        self.capabilities = SimpleNamespace(pg_major=17, versions={"vector": self.vector_version} if self.vector_version else {})
        FakeDB.instances.append(self)

    async def start(self) -> None:
        await asyncio.sleep(0)                  # a real start yields: two connects at once interleave here
        if self.start_error:
            raise self.start_error
        self.started += 1

    async def close(self) -> None:
        self.closed += 1

    async def run(self, fn, **opts) -> object:
        outer = self

        class Tx:
            async def fetchval(self, sql, *args) -> object:
                return outer.held if "org_holds_points" in sql else outer.count

        return await fn(Tx())


@pytest.fixture(autouse=True)
def _fake_runtime(monkeypatch) -> Iterator[None]:
    shared._entries.clear()
    FakeDB.instances = []
    FakeDB.vector_version, FakeDB.start_error = "0.8.4", None
    monkeypatch.setattr(shared, "KernelDB", FakeDB)
    yield
    shared._entries.clear()


@pytest.fixture
def config_service() -> MagicMock:
    cs = MagicMock()
    cs.get_config = AsyncMock(return_value={"dsn": "postgresql://pipeshub_indexing_login@localhost:1/db"})
    return cs


@pytest.fixture
async def svc(config_service) -> PgVectorService:
    s = await PgVectorService.create(config_service)
    s._catalog = MagicMock()
    return s


def in_org(org: str = ORG) -> AbstractContextManager[object]:
    return org_context.bind(org_context.OrgContext(org, None, "service", "test", "-"))


# ---- lifecycle ------------------------------------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_one_database_runtime_for_100_providers_on_one_config(config_service) -> None:
    """chat_helpers builds a provider per call: they share one runtime."""
    providers = await asyncio.gather(*[PgVectorService.create(config_service) for _ in range(100)])
    assert len(FakeDB.instances) == 1 and FakeDB.instances[0].started == 1
    assert FakeDB.instances[0].cfg.statement_cache_size == 0          # PgBouncer transaction mode
    assert FakeDB.instances[0].cfg.role == "pipeshub_indexing"        # the role of the login in the DSN
    assert len({id(p.get_service_client()) for p in providers}) == 1


@pytest.mark.asyncio
async def test_two_connects_at_once_acquire_the_runtime_once(config_service) -> None:
    s = PgVectorService(PgVectorConfig(dsn="postgresql://pipeshub_indexing_login@h/db"))
    await asyncio.gather(*[s.connect() for _ in range(10)])
    assert len(FakeDB.instances) == 1 and FakeDB.instances[0].started == 1
    await s.disconnect()
    assert FakeDB.instances[0].closed == 1                                       # released exactly what was acquired


@pytest.mark.asyncio
async def test_a_reconnect_gets_a_placement_policy_on_the_new_runtime(config_service) -> None:
    s = PgVectorService(PgVectorConfig(dsn="postgresql://pipeshub_indexing_login@h/db"))
    await s.connect()
    first = s.placement
    assert first is not None
    await s.disconnect()
    assert s.placement is None                                                   # the policy held the released runtime
    await s.connect()
    assert s.placement is not None and s.placement is not first and s.placement._db is s._db
    own = PgVectorService(PgVectorConfig(dsn="postgresql://pipeshub_indexing_login@h/db"))
    mine = own.placement = SimpleNamespace(before_first_write=AsyncMock())       # a policy the caller supplied is kept
    await own.connect()
    await own.disconnect()
    assert own.placement is mine


@pytest.mark.asyncio
async def test_runtime_closes_with_last_disconnect(config_service) -> None:
    a = await PgVectorService.create(config_service)
    b = await PgVectorService.create(config_service)
    await a.disconnect()
    assert FakeDB.instances[0].closed == 0
    await b.disconnect()
    assert FakeDB.instances[0].closed == 1
    await b.disconnect()  # idempotent
    assert FakeDB.instances[0].closed == 1


@pytest.mark.asyncio
async def test_missing_extension_releases_the_runtime(config_service) -> None:
    FakeDB.vector_version = None
    with pytest.raises(PgVectorUnsupportedError, match="not installed"):
        await PgVectorService.create(config_service)
    assert FakeDB.instances[0].closed == 1


@pytest.mark.asyncio
async def test_an_old_pgvector_is_refused_and_releases_the_runtime(config_service) -> None:
    FakeDB.vector_version = "0.6.2"
    with pytest.raises(PgVectorUnsupportedError, match="0.7.0"):
        await PgVectorService.create(config_service)
    assert FakeDB.instances[0].closed == 1


@pytest.mark.asyncio
async def test_a_failed_capability_check_is_an_unsupported_error(config_service) -> None:
    FakeDB.start_error = CapabilityError("required extension vector is absent")
    with pytest.raises(PgVectorUnsupportedError, match="vector is absent"):
        await PgVectorService.create(config_service)


@pytest.mark.asyncio
async def test_a_missing_configuration_is_an_error_not_a_default(monkeypatch) -> None:
    monkeypatch.delenv("PGVECTOR_DSN", raising=False)
    cs = MagicMock()
    cs.get_config = AsyncMock(return_value=None)
    with pytest.raises(ValueError, match="dsn"):
        await PgVectorService.create(cs)
    assert FakeDB.instances == []


@pytest.mark.asyncio
async def test_a_provider_that_is_not_connected_says_so() -> None:
    s = PgVectorService(PgVectorConfig(dsn="postgresql://h/db"))
    with pytest.raises(PgVectorError, match="not connected"):
        await s.get_collections()
    health = await s.health_check()
    assert health.status == HealthStatus.UNHEALTHY and health.message == "Not connected"


@pytest.mark.asyncio
async def test_health_reports_the_version_and_an_unreachable_catalog(svc) -> None:
    svc._catalog.collections = AsyncMock(return_value=[ROW])
    ok = await svc.health_check()
    assert ok.status == HealthStatus.HEALTHY and ok.server_version == "pgvector 0.8.4" and ok.latency_ms is not None
    svc._catalog.collections = AsyncMock(side_effect=ConnectionResetError("gone"))
    bad = await svc.health_check()
    assert bad.status == HealthStatus.UNHEALTHY and "gone" not in bad.message and "ConnectionResetError" in bad.message   # no driver text, host or port


def test_identity_and_capabilities() -> None:
    s = PgVectorService(PgVectorConfig(dsn="postgresql://h/db"))
    caps = s.get_capabilities()
    assert s.get_service_name() == "pgvector" and s.get_service() is s
    assert caps.supports_sparse_vectors and caps.supports_multi_collection and caps.stamps_in_catalog and caps.spaces_per_org
    assert not caps.supports_server_side_text_search


@pytest.mark.asyncio
async def test_reading_and_searching_are_not_implemented_yet(svc) -> None:
    flt = await svc.filter_collection(must={"orgId": ORG})
    for call in (svc.scroll("records", flt, 1), svc.retrieve_points("records", ["x"]), svc.query_nearest_points("records", [])):
        with pytest.raises(NotImplementedError, match="pgvector"):
            await call


# ---- collections: the same answers as the other providers ------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_creating_an_existing_collection_fails_with_the_registry_token(svc) -> None:
    svc._catalog.collection = AsyncMock(return_value=ROW)
    svc._catalog.create_collection = AsyncMock()
    with pytest.raises(CollectionAlreadyExistsError, match="already exists"):
        await svc.create_collection("records")
    svc._catalog.create_collection.assert_not_awaited()
    svc._catalog.collection = AsyncMock(return_value=None)
    await svc.create_collection("records")
    svc._catalog.create_collection.assert_awaited_once_with("records", sparse_enabled=True, sparse_idf=True)


@pytest.mark.asyncio
async def test_a_missing_collection(svc) -> None:
    svc._catalog.collection = AsyncMock(return_value=None)
    svc._catalog.require_collection = AsyncMock(side_effect=CollectionNotFoundError("nope"))
    assert await svc.collection_exists("nope") is False
    info = await svc.get_collection_info("nope")
    assert info.exists is False and info.name == "nope"
    with pytest.raises(CollectionNotFoundError) as e:
        await svc.get_collection("nope")
    assert _is_collection_missing(e.value)


@pytest.mark.asyncio
async def test_deleting_a_missing_collection_is_not_an_error_and_a_present_one_is_marked(svc) -> None:
    svc._catalog.mark_deleting = AsyncMock(return_value=False)
    await svc.delete_collection("nope")
    svc._catalog.mark_deleting = AsyncMock(return_value=True)
    await svc.delete_collection("records")
    svc._catalog.mark_deleting.assert_awaited_with("records")


@pytest.mark.asyncio
async def test_collection_info_is_the_calling_orgs(svc) -> None:
    svc._catalog.collection = AsyncMock(return_value=ROW)
    svc._catalog.active_space = AsyncMock(return_value=OrgSpace(3, "active", "bge", 1024, "cosine"))
    FakeDB.instances[0].count = 42
    no_org = await svc.get_collection_info("records")                             # no org: existence only, nothing counted
    assert (no_org.exists, no_org.dense_dimension, no_org.points_count) == (True, None, 0)
    with in_org():
        info = await svc.get_collection_info("records")
    assert (info.exists, info.dense_dimension, info.points_count) == (True, 1024, 42)
    svc._catalog.active_space = AsyncMock(return_value=None)
    with in_org():
        info = await svc.get_collection_info("records")
    assert info.exists and info.dense_dimension is None and info.points_count == 0


@pytest.mark.asyncio
async def test_create_index_is_accepted_and_ignored(svc) -> None:
    assert await svc.create_index("records", "metadata.orgId", {"type": "keyword"}) is None


@pytest.mark.asyncio
async def test_filter_collection_builds_a_provider_neutral_expression(svc) -> None:
    flt = await svc.filter_collection(must={"orgId": ORG, "virtualRecordId": ["v1", "v2"]}, should={"connectorIds": ["c"]}, origin="CONNECTOR")
    keys = {c.key: (c.value, c.values) for c in flt.must}
    assert keys["metadata.orgId"][0] == ORG and keys["metadata.virtualRecordId"][1] == ["v1", "v2"]
    assert any(c.key == "metadata.origin" for c in flt.must) or any(c.key == "metadata.origin" for c in flt.should)
    blank = await svc.filter_collection(FilterMode.MUST, must={"x": "", "y": None})
    assert blank.must == []
    # A blank org would be dropped and widen the filter to every org: it raises instead.
    with pytest.raises(OrgRequired):
        await svc.filter_collection(FilterMode.MUST, must={"orgId": None, "x": ""})


# ---- stamps, orgs of records, the spaces of an org ------------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_stamps_and_vrid_lookup_go_to_the_catalog(svc) -> None:
    svc._catalog.stamp = AsyncMock(return_value="s1")
    svc._catalog.set_stamp = AsyncMock(return_value="s0")
    svc._catalog.orgs_of_vrids = AsyncMock(return_value={"v": {ORG}})
    assert await svc.get_collection_stamp("records") == "s1"
    assert await svc.set_collection_stamp("records", "s2") == "s0"          # the one in force
    assert await svc.orgs_of_virtual_records(["v"]) == {"v": {ORG}}


@pytest.mark.asyncio
async def test_space_acceptance(svc) -> None:
    def space(model: str, dim: int = 4, named_by: str | None = None) -> AsyncMock:
        return AsyncMock(return_value=OrgSpace(1, "active", model, dim, "cosine", named_by))

    with pytest.raises(OrgRequired):                                       # no org: refused before any lookup
        await svc.org_space_accepts("p:m:4", 4)
    with in_org():
        svc._catalog.active_space = AsyncMock(return_value=None)
        assert await svc.org_space_accepts("p:m:4", 4) is False
        svc._catalog.active_space = space("p:m:4")
        assert await svc.org_space_accepts("p:m:4", 4) is True
        assert await svc.org_space_accepts("p:other:4", 4) is False        # a named space takes its own model only
        assert await svc.org_space_accepts("p:m:4", 8) is False            # another dimension
        svc._catalog.active_space = space(UNKNOWN_MODEL)
        FakeDB.instances[0].held = False
        assert await svc.org_space_accepts("anything:4", 4) is True        # nameless and empty: takes any model
        FakeDB.instances[0].held = True
        assert await svc.org_space_accepts("p:m:4", 4, current_identity="p:m:4") is True
        assert await svc.org_space_accepts("p:m:4", 4, current_identity="p:x:4") is False
        assert await svc.org_space_accepts(None, 4, current_identity=None) is False
        svc._catalog.active_space = space(UNKNOWN_MODEL, named_by="p:m:4")
        assert await svc.org_space_accepts("p:m:4", 4) is True             # named later by a stamped write


@pytest.mark.asyncio
async def test_holding_points_needs_an_org(svc) -> None:
    with pytest.raises(OrgRequired):
        await svc.org_holds_points()
    FakeDB.instances[0].held = True
    with in_org():
        assert await svc.org_holds_points() is True


def test_transient_errors_are_classified() -> None:
    import asyncpg

    from app.services.vector_db.models import SearchFailureKind

    s = PgVectorService(PgVectorConfig(dsn="postgresql://h/db"))
    assert s.transient_search_error(asyncpg.QueryCanceledError()) is SearchFailureKind.TIMEOUT
    assert s.transient_search_error(ValueError("x")) is None


# ---- writes -----------------------------------------------------------------------------------------------------------------------
def point(i: int, org: str = ORG, dim: int = 4, model: str | None = "p:m:4") -> VectorPoint:
    import uuid

    meta = {"orgId": org, "virtualRecordId": "r1", **({"embeddingModel": model} if model else {})}
    return VectorPoint(id=str(uuid.UUID(int=i)), dense_vector=[0.5] * dim, payload={"page_content": "t", "metadata": meta})


@pytest.fixture
def writer(svc, monkeypatch) -> tuple[PgVectorService, SimpleNamespace]:
    """A connected provider whose catalog answers and whose statements are recorded instead of run."""
    from app.services.vector_db.pgvector import writes

    calls = SimpleNamespace(upsert=[], delete=[], placed=[], payload=[])
    space = OrgSpace(5, "active", "p:m:4", 4, "cosine")
    svc._catalog.require_collection = AsyncMock(return_value=ROW)
    svc._catalog.org_spaces = AsyncMock(return_value=[space])
    svc._catalog.forget = MagicMock()

    async def upsert(tx, coll, space_id, batch, computed_at, *, safe_mode) -> None:
        calls.upsert.append((coll, space_id, len(batch.point_ids), batch.org))

    async def delete_batch(tx, org, coll, compiled, *, safe_mode) -> int:
        calls.delete.append(org)
        return writes.DELETE_BATCH if len(calls.delete) < 3 else 7

    monkeypatch.setattr(writes, "upsert", upsert)
    monkeypatch.setattr(writes, "delete_batch", delete_batch)
    svc.placement = SimpleNamespace(before_first_write=AsyncMock(side_effect=lambda org: calls.placed.append(org)))
    return svc, calls


@pytest.mark.asyncio
async def test_a_batch_is_written_in_pieces_after_the_org_is_placed_and_into_the_orgs_space(writer) -> None:
    svc, calls = writer
    await svc.upsert_points("records", [point(i) for i in range(1, 1202)])
    assert calls.upsert == [(7, 5, 500, ORG), (7, 5, 500, ORG), (7, 5, 201, ORG)] and calls.placed == [ORG] * 3
    await svc.upsert_points("records", [])                                   # nothing to write is not an error
    assert len(calls.upsert) == 3


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [None, "", "  ", "not-an-org", "A" * 24])
async def test_a_write_without_a_valid_org_never_reaches_the_database(writer, bad) -> None:
    svc, calls = writer
    flt = await svc.filter_collection(must={"virtualRecordId": "r1"})
    meta_bad = point(1)
    meta_bad.payload["metadata"]["orgId"] = bad
    with pytest.raises(OrgRequired):
        await svc.upsert_points("records", [meta_bad])
    with pytest.raises(OrgRequired):
        await svc.update_payload_by_ids("records", ["x"], {"k": 1}, org_id=bad if bad is not None else "")
    for call in (svc.delete_points("records", flt), svc.set_payload("records", {"k": 1}, flt), svc.overwrite_payload("records", {"k": 1}, flt)):
        with pytest.raises(OrgRequired):
            await call
    assert not calls.upsert and not calls.delete and not calls.placed


@pytest.mark.asyncio
async def test_a_batch_of_two_orgs_or_two_models_or_two_dimensions_is_refused(writer) -> None:
    from app.services.vector_db.pgvector.errors import OrgMismatch, SpaceMismatch

    svc, calls = writer
    with pytest.raises(OrgMismatch):
        await svc.upsert_points("records", [point(1), point(2, org="b" * 24)])
    with pytest.raises(SpaceMismatch, match="one embedding model"):
        await svc.upsert_points("records", [point(1), point(2, model="p:other:4")])
    with pytest.raises(SpaceMismatch, match="one dimension"):
        await svc.upsert_points("records", [point(1), point(2, dim=8)])
    assert not calls.upsert


@pytest.mark.asyncio
async def test_a_destructive_call_with_a_filter_that_matches_everything_is_refused(writer) -> None:
    svc, calls = writer
    empty = await svc.filter_collection()
    only_count = await svc.filter_collection(max_values={"connectorIds": 1})
    for call in (svc.delete_points("records", empty), svc.set_payload("records", {"k": 1}, empty), svc.overwrite_payload("records", {"k": 1}, empty)):
        with pytest.raises(ValueError, match="empty filter"):
            await call
    with pytest.raises(ValueError, match="array-length"):
        await svc.delete_points("records", only_count)
    assert not calls.delete


@pytest.mark.asyncio
async def test_deletes_run_batch_by_batch_until_one_is_short(writer) -> None:
    svc, calls = writer
    await svc.delete_points("records", await svc.filter_collection(must={"orgId": ORG, "virtualRecordId": "r1"}))
    assert calls.delete == [ORG] * 3


@pytest.mark.asyncio
async def test_a_foreign_key_failure_is_a_space_mismatch_and_forgets_the_cached_spaces(writer, monkeypatch) -> None:
    import asyncpg

    from app.services.vector_db.pgvector import writes
    from app.services.vector_db.pgvector.errors import SpaceMismatch

    svc, _ = writer

    async def stale(*a, **k) -> None:
        raise asyncpg.ForeignKeyViolationError("fk")

    monkeypatch.setattr(writes, "upsert", stale)
    with pytest.raises(SpaceMismatch):
        await svc.upsert_points("records", [point(1)])
    svc._catalog.forget.assert_called_with(org=ORG)


@pytest.mark.asyncio
async def test_ids_that_are_not_uuids_name_no_point_and_the_request_org_is_the_default(writer, monkeypatch) -> None:
    from app.services.vector_db.pgvector import writes

    svc, _ = writer
    seen = []

    async def merge(tx, org, coll, payload, c, ids, *, replace=False) -> int:
        seen.append((org, ids))
        return 1

    monkeypatch.setattr(writes, "merge_payload", merge)
    await svc.update_payload_by_ids("records", ["not-a-uuid"], {"k": 1}, org_id=ORG)
    assert seen == []                                                          # nothing named a point: no statement
    with pytest.raises(OrgRequired):
        await svc.update_payload_by_ids("records", ["00000000-0000-0000-0000-000000000001"], {"k": 1})
    with in_org():
        await svc.update_payload_by_ids("records", ["00000000-0000-0000-0000-000000000001"], {"k": 1})
    assert [o for o, _ in seen] == [ORG]


@pytest.mark.asyncio
async def test_the_orgs_space_for_a_write(writer) -> None:
    from app.services.vector_db.pgvector.errors import SpaceMismatch

    svc, _ = writer
    cat = svc._catalog
    cat.activate_space = AsyncMock(return_value=OrgSpace(9, "active", "p:new:8", 8, "cosine"))
    cat.replace_empty_space = AsyncMock(return_value=False)
    cat.name_space = AsyncMock(return_value="p:m:4")
    assert (await svc._space_for_write(ORG, 4, {"p:m:4"})).space_id == 5       # the org's own space
    cat.org_spaces = AsyncMock(return_value=[])
    assert (await svc._space_for_write(ORG, 8, {"p:new:8"})).space_id == 9     # no space yet: the vectors define it
    cat.activate_space.assert_awaited_with(ORG, "p:new:8", 8)
    cat.org_spaces = AsyncMock(return_value=[OrgSpace(5, "active", "p:m:4", 4, "cosine")])
    with pytest.raises(SpaceMismatch):                                         # another dimension while the org holds points
        await svc._space_for_write(ORG, 8, {"p:new:8"})
    cat.replace_empty_space.assert_awaited_once()
    nameless = OrgSpace(5, "active", UNKNOWN_MODEL, 4, "cosine")
    cat.org_spaces = AsyncMock(return_value=[nameless])
    assert (await svc._space_for_write(ORG, 4, {"p:m:4"})).effective_model == "p:m:4"      # the first stamped write names it
    cat.name_space = AsyncMock(return_value="p:someone-else:4")
    with pytest.raises(SpaceMismatch):
        await svc._space_for_write(ORG, 4, {"p:m:4"})
