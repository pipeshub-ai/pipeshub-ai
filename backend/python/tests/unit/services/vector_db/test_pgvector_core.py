"""The pgvector provider's building blocks: configuration, the capability rules, the org scope and the catalog calls."""

from __future__ import annotations

from typing import Never

import asyncpg
import pytest

from app.core import org_context
from app.services.vector_db.collection_registry import _is_collection_missing
from app.services.vector_db.models import SearchFailureKind
from app.services.vector_db.pgvector.catalog import Catalog
from app.services.vector_db.pgvector.config import PgVectorConfig
from app.services.vector_db.pgvector.errors import (
    CollectionAlreadyExistsError,
    CollectionNotFoundError,
    OrgMismatch,
    OrgRequired,
    PgVectorError,
    PgVectorUnsupportedError,
    VectorOrgError,
    classify_pg_search_error,
)
from app.services.vector_db.pgvector.probe import evaluate
from app.services.vector_db.pgvector.scope import (
    ambient_or_required,
    catalog_scope,
    org_scope,
    require_org,
)

ORG = "a" * 24


# ---- configuration ----------------------------------------------------------------------------------------------------
def test_config_requires_dsn(monkeypatch) -> None:
    monkeypatch.delenv("PGVECTOR_DSN", raising=False)
    with pytest.raises(ValueError, match="dsn"):
        PgVectorConfig.from_dict({})
    with pytest.raises(ValueError, match="dsn"):
        PgVectorConfig.from_dict({"dsn": None})
    monkeypatch.setenv("PGVECTOR_DSN", "postgresql://x")
    assert PgVectorConfig.from_dict({}).to_db_config().statement_cache_size == 0


@pytest.mark.parametrize(("dsn", "service"), [
    ("postgresql://pipeshub_indexing_login:pw@h:5432/db", "indexing"),
    ("postgres://pipeshub_connectors_login@h/db", "connectors"),
    ("postgresql://postgres@h/db", "query"),
])
def test_the_service_role_follows_the_login(dsn, service) -> None:
    assert PgVectorConfig.from_dict({"dsn": dsn}).to_db_config().role == f"pipeshub_{service}"
    assert PgVectorConfig.from_dict({"dsn": dsn, "service": "node"}).to_db_config().role == "pipeshub_node"


def test_a_stored_null_is_the_default_and_a_stored_false_is_false(monkeypatch) -> None:
    for name in ("PGVECTOR_STRICT", "PGVECTOR_FENCE", "PGVECTOR_PLACEMENT", "PGVECTOR_POOLER", "PGVECTOR_SERVICE"):
        monkeypatch.delenv(name, raising=False)
    nulls = dict.fromkeys(("service", "pool_max", "strict", "pooler", "fence", "placement", "placement_wait", "catalog_ttl", "search"))
    cfg = PgVectorConfig.from_dict({"dsn": "postgresql://pipeshub_query_login@h/db", **nulls})
    assert (cfg.fence, cfg.strict, cfg.placement, cfg.pool_max, cfg.pooler) == (True, False, "own_leaf", 10, "pgbouncer_txn")
    assert (cfg.placement_wait, cfg.catalog_ttl, cfg.service) == (30.0, 5.0, "query")
    off = PgVectorConfig.from_dict({"dsn": "postgresql://h/db", "fence": False, "strict": True, "placement": "none"})
    assert (off.fence, off.strict, off.placement) == (False, True, "none")


def test_an_unknown_placement_policy_is_refused() -> None:
    with pytest.raises(ValueError, match="placement"):
        PgVectorConfig.from_dict({"dsn": "postgresql://h/db", "placement": "owm_leaf"})


def test_a_pool_size_below_one_is_one() -> None:
    assert PgVectorConfig.from_dict({"dsn": "postgresql://h/db", "pool_max": 0}).pool_max == 1


def test_a_process_login_in_the_environment_beats_a_dsn_in_the_config_store(monkeypatch, caplog) -> None:
    monkeypatch.setenv("PGVECTOR_DSN", "postgresql://pipeshub_query_login:a@h/db")
    with caplog.at_level("WARNING"):
        cfg = PgVectorConfig.from_dict({"dsn": "postgresql://pipeshub_indexing_login:b@h/db"})
    assert cfg.dsn.startswith("postgresql://pipeshub_query_login") and cfg.service == "query"
    assert "ignoring 'dsn'" in caplog.text and "pipeshub_indexing_login" not in caplog.text      # the log line names no credential


def test_the_jobs_login_comes_from_the_process_environment_only(monkeypatch, caplog) -> None:
    monkeypatch.setenv("PGVECTOR_DSN", "postgresql://pipeshub_query_login:a@h/db")
    store = {"jobs_dsn": "postgresql://pipeshub_jobs:from-store@h/db"}
    monkeypatch.delenv("PGVECTOR_JOBS_DSN", raising=False)
    with caplog.at_level("WARNING"):
        assert PgVectorConfig.from_dict(store).jobs_dsn == ""                      # a process without it never picks one up from the store
    assert "ignoring 'jobs_dsn'" in caplog.text and "from-store" not in caplog.text and "pipeshub_jobs" not in caplog.text
    monkeypatch.setenv("PGVECTOR_JOBS_DSN", "postgresql://pipeshub_jobs:from-env@h/db")
    assert PgVectorConfig.from_dict(store).jobs_dsn.endswith("from-env@h/db")
    assert PgVectorConfig.from_dict({}).jobs_dsn.endswith("from-env@h/db")


# ---- errors ------------------------------------------------------------------------------------------------------------
def test_registry_error_strings() -> None:
    assert "already exists" in str(CollectionAlreadyExistsError("c")).lower()
    assert _is_collection_missing(CollectionNotFoundError("c"))
    assert issubclass(OrgRequired, VectorOrgError) and issubclass(OrgMismatch, VectorOrgError)


def test_transient_classification() -> None:
    assert classify_pg_search_error(asyncpg.QueryCanceledError()) is SearchFailureKind.TIMEOUT
    assert classify_pg_search_error(ConnectionResetError()) is SearchFailureKind.UNAVAILABLE
    assert classify_pg_search_error(asyncpg.UndefinedTableError()) is None


# ---- capability rules ----------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("pg", [17, 18])
@pytest.mark.parametrize(
    ("ext", "iterative", "parallel", "safe", "bit18"),
    [
        ("0.7.4", False, False, True, False),
        ("0.8.0", True, False, True, False),
        ("0.8.2", True, True, True, False),
        ("0.8.3", True, True, True, True),      # the boundary: bit HNSW is back on PostgreSQL 18, the safe mode still holds
        ("0.8.4", True, True, False, True),
    ],
)
def test_probe_flags(pg, ext, iterative, parallel, safe, bit18) -> None:
    caps = evaluate(pg, ext)
    assert (caps.iterative_scan, caps.parallel_hnsw_build, caps.hnsw_safe_mode) == (iterative, parallel, safe)
    assert caps.bit_hnsw is (True if pg == 17 else bit18)
    assert caps.halfvec


@pytest.mark.parametrize(
    ("ext", "refused"),
    [("0.6.2", True), ("0.7.4", True), ("0.8.0", True), ("0.8.1", True), ("0.8.2", False), ("0.8.4", False)],
)
def test_strict_refusals(ext, refused) -> None:
    if refused:
        with pytest.raises(PgVectorUnsupportedError):
            evaluate(17, ext, strict=True)
    else:
        evaluate(17, ext, strict=True)


@pytest.mark.parametrize("ext", ["0.6.2", "", "garbage"])
def test_below_floor_or_unreadable_version_is_refused_even_when_not_strict(ext) -> None:
    with pytest.raises(PgVectorUnsupportedError, match="0.7.0"):
        evaluate(17, ext)


# ---- the org of a call -----------------------------------------------------------------------------------------------------
@pytest.mark.parametrize("bad", [None, "", "   ", "A" * 24, "a" * 23, "a' OR 1=1 --", 5])
def test_an_org_that_is_not_an_org_id_is_refused(bad) -> None:
    with pytest.raises(OrgRequired):
        require_org(bad)
    with pytest.raises(OrgRequired), org_scope(bad):
        pass


def test_the_explicit_org_is_bound_when_none_is_ambient_and_must_match_when_one_is() -> None:
    with org_scope(ORG) as org:
        assert org == ORG and ambient_or_required() == ORG
    with pytest.raises(OrgRequired):
        ambient_or_required()
    with org_context.bind(org_context.OrgContext(ORG, None, "service", "t", "-")):
        with org_scope(ORG):
            pass
        with pytest.raises(OrgMismatch), org_scope("b" * 24):
            pass


def test_the_catalog_scope_binds_the_placeholder_org_only_when_none_is_ambient() -> None:
    with catalog_scope():
        assert org_context.current().org_id == org_context.SYSTEM_ORG_ID
    with org_scope(ORG), catalog_scope():
        assert org_context.current().org_id == ORG


# ---- the catalog: every statement bound, scoped, and its errors kept ------------------------------------------------------
class FakeDB:
    """KernelDB.run stand-in: records (sql, args, opts, org) per statement and answers from a script keyed by function name."""

    def __init__(self, answers: dict[str, object]) -> None:
        self.answers, self.calls = answers, []

    async def run(self, fn, **opts) -> object:
        outer = self

        class Tx:
            async def _go(_self, kind, sql, *args) -> object:
                outer.calls.append((sql, args, opts, org_context.current_or_system().org_id))
                key = next(k for k in outer.answers if k in sql)
                ans = outer.answers[key]
                if isinstance(ans, BaseException):
                    raise ans
                return ans.pop(0) if isinstance(ans, list) and kind != "fetch" else ans

            async def fetch(self, sql, *a) -> object:
                return await self._go("fetch", sql, *a)

            async def fetchrow(self, sql, *a) -> object:
                return await self._go("row", sql, *a)

            async def fetchval(self, sql, *a) -> object:
                return await self._go("val", sql, *a)

        return await fn(Tx())


def rec(**kw: object) -> dict[str, object]:
    return kw


@pytest.mark.asyncio
async def test_a_collection_lookup_is_bound_scoped_to_the_catalog_and_cached() -> None:
    row = rec(collection_id=1, sparse_enabled=True, sparse_idf=True, lexical_version="v", status="ready")
    db = FakeDB({"collection_get": row})
    cat = Catalog(db, ttl=60)
    evil = "x'); DROP TABLE retrieval.points; --"
    assert await cat.collection(evil) is not None and await cat.collection(evil) is not None
    assert len(db.calls) == 1                                              # the second answer came from the cache
    sql, args, opts, org = db.calls[0]
    assert args == (evil,) and evil not in sql and "$1" in sql and opts["custom_plan"] is True
    assert org == org_context.SYSTEM_ORG_ID
    with pytest.raises(CollectionNotFoundError):
        await Catalog(FakeDB({"collection_get": None}), ttl=0).require_collection("nope")


@pytest.mark.asyncio
async def test_listing_collections_reads_every_page() -> None:
    page = [rec(collection_id=i, name=f"c{i}", sparse_enabled=True, sparse_idf=True, lexical_version="v", status="ready") for i in range(1, 1001)]
    last = [rec(collection_id=1001, name="c1001", sparse_enabled=True, sparse_idf=True, lexical_version="v", status="ready")]
    pages = iter([page, last])
    seen = []

    class DB:
        async def run(self, fn, **opts) -> object:
            class Tx:
                async def fetch(_s, sql, after) -> object:
                    seen.append(after)
                    return next(pages)
            return await fn(Tx())

    db = DB()
    names = [c.name for c in await Catalog(db, ttl=0).collections()]
    assert len(names) == 1001 and names[-1] == "c1001"
    assert seen == [0, 1000]


@pytest.mark.asyncio
async def test_org_spaces_run_for_the_named_org_only_and_a_bad_org_never_reaches_the_database() -> None:
    db = FakeDB({"org_space_list": [], "org_space_named_by": []})
    cat = Catalog(db, ttl=60)
    assert await cat.org_spaces(ORG) == []
    assert {c[3] for c in db.calls} == {ORG}
    with pytest.raises(OrgRequired):
        await cat.org_spaces("not-an-org")
    with pytest.raises(OrgRequired):
        await cat.activate_space("", "m", 4)
    assert len(db.calls) == 2


@pytest.mark.asyncio
async def test_adopting_a_space_does_not_swallow_a_unique_violation() -> None:
    cat = Catalog(FakeDB({"org_space_adopt": asyncpg.UniqueViolationError("dup")}), ttl=0)
    with pytest.raises(asyncpg.UniqueViolationError):
        await cat.activate_space(ORG, "m", 4)


@pytest.mark.asyncio
async def test_a_job_without_the_jobs_credential_names_the_command_and_a_duplicate_is_a_typed_error() -> None:
    from app.core.db import DbConfigError

    cat = Catalog(FakeDB({"collection_create": DbConfigError("no jobs dsn")}), ttl=0)
    with pytest.raises(PgVectorError, match="PGVECTOR_JOBS_DSN"):
        await cat.create_collection("c", sparse_enabled=True, sparse_idf=True)
    cat = Catalog(FakeDB({"collection_create": asyncpg.UniqueViolationError("dup")}), ttl=0)
    with pytest.raises(CollectionAlreadyExistsError):
        await cat.create_collection("c", sparse_enabled=True, sparse_idf=True)


@pytest.mark.asyncio
async def test_vrid_lookup_splits_a_refused_batch_and_gives_up_on_one_vrid() -> None:
    class Limit(FakeDB):
        async def run(self, fn, **opts) -> object:
            class Tx:
                async def fetch(_s, sql, vrids) -> object:
                    self.calls.append(len(vrids))
                    if len(vrids) > 1:
                        raise asyncpg.ProgramLimitExceededError("too many")
                    return [{"vrid": vrids[0], "org_id": ORG}]
            return await fn(Tx())

    db = Limit({})
    found = await Catalog(db, ttl=0).orgs_of_vrids([f"v{i}" for i in range(5)])
    assert set(found) == {f"v{i}" for i in range(5)} and all(v == {ORG} for v in found.values())
    assert await Catalog(Limit({}), ttl=0).orgs_of_vrids([]) == {}

    class Always(FakeDB):
        async def run(self, fn, **opts) -> Never:
            raise asyncpg.ProgramLimitExceededError("too many")
    with pytest.raises(asyncpg.ProgramLimitExceededError):
        await Catalog(Always({}), ttl=0).orgs_of_vrids(["only"])
