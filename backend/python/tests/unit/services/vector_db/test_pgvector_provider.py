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
from app.services.vector_db.models import FilterMode, HealthStatus
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
    assert bad.status == HealthStatus.UNHEALTHY and "gone" in bad.message


def test_identity_and_capabilities() -> None:
    s = PgVectorService(PgVectorConfig(dsn="postgresql://h/db"))
    caps = s.get_capabilities()
    assert s.get_service_name() == "pgvector" and s.get_service() is s
    assert caps.supports_sparse_vectors and caps.supports_multi_collection and caps.stamps_in_catalog and caps.spaces_per_org
    assert not caps.supports_server_side_text_search


@pytest.mark.asyncio
async def test_points_methods_are_not_implemented_yet(svc) -> None:
    flt = await svc.filter_collection(must={"orgId": ORG})
    for call in (svc.scroll("records", flt, 1), svc.retrieve_points("records", ["x"]), svc.upsert_points("records", []),
                 svc.delete_points("records", flt), svc.update_payload_by_ids("records", ["x"], {}),
                 svc.overwrite_payload("records", {}, flt), svc.set_payload("records", {}, flt), svc.query_nearest_points("records", [])):
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
    blank = await svc.filter_collection(FilterMode.MUST, must={"orgId": None, "x": ""})
    assert blank.must == []


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
