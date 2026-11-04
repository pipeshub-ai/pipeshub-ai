"""The pgvector provider's building blocks: configuration, the capability rules, the org scope and the catalog calls."""

from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace
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


@pytest.fixture(autouse=True)
def _clean_pgvector_env(monkeypatch) -> None:
    """An exported PGVECTOR_* setting must not change what a test reads."""
    for name in [n for n in os.environ if n.startswith("PGVECTOR_")]:
        monkeypatch.delenv(name)


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


@pytest.mark.parametrize("env_name", ["PGVECTOR_FENCE", "PGVECTOR_STRICT"])
@pytest.mark.parametrize("bad", ["flase", "disabled", "ture", "2", "nope"])
def test_a_misspelt_switch_is_a_startup_error_naming_the_variable(monkeypatch, env_name, bad) -> None:
    monkeypatch.setenv(env_name, bad)
    with pytest.raises(ValueError, match=env_name):
        PgVectorConfig.from_dict({"dsn": "postgresql://h/db"})
    monkeypatch.delenv(env_name)
    with pytest.raises(ValueError, match=env_name):                                  # and the same value in the config store
        PgVectorConfig.from_dict({"dsn": "postgresql://h/db", env_name.removeprefix("PGVECTOR_").lower(): bad})


@pytest.mark.parametrize(("raw", "fence"), [("", True), ("  ", True), ("1", True), ("YES", True), (" On ", True), ("0", False), ("False", False),
                                            ("no", False), ("OFF", False), (True, True), (False, False)])
def test_a_switch_accepts_the_usual_spellings_and_empty_means_the_default(monkeypatch, raw, fence) -> None:
    monkeypatch.delenv("PGVECTOR_FENCE", raising=False)
    assert PgVectorConfig.from_dict({"dsn": "postgresql://h/db", "fence": raw}).fence is fence
    monkeypatch.setenv("PGVECTOR_FENCE", str(raw))
    assert PgVectorConfig.from_dict({"dsn": "postgresql://h/db"}).fence is fence


def test_an_empty_environment_value_is_the_default_for_every_setting(monkeypatch) -> None:
    for name in ("PGVECTOR_FENCE", "PGVECTOR_STRICT", "PGVECTOR_PLACEMENT", "PGVECTOR_POOLER", "PGVECTOR_SERVICE"):
        monkeypatch.setenv(name, "")
    cfg = PgVectorConfig.from_dict({"dsn": "postgresql://pipeshub_query_login@h/db"})
    assert (cfg.fence, cfg.strict, cfg.placement, cfg.pooler, cfg.service) == (True, False, "own_leaf", "pgbouncer_txn", "query")


@pytest.mark.parametrize("key", ["placement_wait", "catalog_ttl"])
@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -1, "nan"])
def test_a_wait_that_is_not_a_finite_non_negative_number_is_refused(key, bad) -> None:
    with pytest.raises(ValueError, match=key):
        PgVectorConfig.from_dict({"dsn": "postgresql://h/db", key: bad})


def test_a_placement_wait_of_zero_is_refused_and_a_zero_cache_time_is_allowed() -> None:
    with pytest.raises(ValueError, match="placement_wait"):
        PgVectorConfig.from_dict({"dsn": "postgresql://h/db", "placement_wait": 0})
    assert PgVectorConfig.from_dict({"dsn": "postgresql://h/db", "catalog_ttl": 0}).catalog_ttl == 0


def test_a_misspelt_search_switch_is_refused_and_a_valid_setting_is_read() -> None:
    from app.services.vector_db.pgvector.tuning import Tuning

    with pytest.raises(ValueError, match="PGVECTOR_VALIDATE_QUANTIZATION"):
        Tuning.from_dict({"validate_quantization": "flase"})
    assert Tuning.from_dict({"validate_quantization": "off"}).validate_quantization is False
    assert Tuning.from_dict({"sampler_max_per_s": "12"}).sampler_max_per_s == 12
    assert Tuning.from_dict({"sampler_max_per_s": 12.0}).sampler_max_per_s == 12


@pytest.mark.parametrize("bad", [12.9, "12.9", "twelve", "1e1x", "nan", "inf", [3]])
def test_a_whole_number_setting_that_is_not_one_is_a_startup_error_naming_the_variable(monkeypatch, bad) -> None:
    from app.services.vector_db.pgvector.tuning import Tuning

    with pytest.raises(ValueError, match="PGVECTOR_SAMPLER_MAX_PER_S"):
        Tuning.from_dict({"sampler_max_per_s": bad})
    monkeypatch.setenv("PGVECTOR_SAMPLER_MAX_PER_S", str(bad))
    with pytest.raises(ValueError, match="PGVECTOR_SAMPLER_MAX_PER_S"):
        Tuning.from_dict(None)
    with pytest.raises(ValueError, match="PGVECTOR_SAMPLER_RATE"):                 # a decimal setting must be a number too
        Tuning.from_dict({"sampler_rate": "often"})


def test_an_unknown_placement_policy_is_refused() -> None:
    with pytest.raises(ValueError, match="placement"):
        PgVectorConfig.from_dict({"dsn": "postgresql://h/db", "placement": "owm_leaf"})


@pytest.mark.parametrize("bad", [0, -3, "0", "many", [4], "2.5"])
def test_a_pool_size_that_is_not_a_whole_number_of_at_least_one_is_a_startup_error_naming_it(bad) -> None:
    with pytest.raises(ValueError, match="pool_max"):
        PgVectorConfig.from_dict({"dsn": "postgresql://h/db", "pool_max": bad})
    assert PgVectorConfig.from_dict({"dsn": "postgresql://h/db", "pool_max": "4"}).pool_max == 4
    assert PgVectorConfig.from_dict({"dsn": "postgresql://h/db", "max_size": 7}).pool_max == 7


@pytest.mark.parametrize("key", ["placement_wait", "catalog_ttl"])
@pytest.mark.parametrize("bad", ["", "soon", [3]])
def test_an_empty_or_unreadable_number_of_seconds_is_an_error_naming_the_setting(key, bad) -> None:
    with pytest.raises(ValueError, match=key):
        PgVectorConfig.from_dict({"dsn": "postgresql://h/db", key: bad})


@pytest.mark.parametrize("name", ["sampler_rate", "validate_floor", "h_slots"])
@pytest.mark.parametrize("meant_as_off", [-1, 10**9, "-1"])
def test_a_search_setting_outside_its_range_is_an_error_not_the_default(name, meant_as_off, monkeypatch) -> None:
    from app.services.vector_db.pgvector.tuning import Tuning

    with pytest.raises(ValueError, match=f"PGVECTOR_{name.upper()}"):
        Tuning.from_dict({name: meant_as_off})
    monkeypatch.setenv(f"PGVECTOR_{name.upper()}", str(meant_as_off))
    with pytest.raises(ValueError, match=f"PGVECTOR_{name.upper()}"):
        Tuning.from_dict(None)


def test_zero_is_how_the_sampler_is_switched_off() -> None:
    from app.services.vector_db.pgvector.tuning import Tuning

    assert Tuning.from_dict({"sampler_rate": 0}).sampler_rate == 0


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
@pytest.mark.parametrize("bad", [None, "", "   ", "A" * 24, "a" * 23, "a" * 24 + "\n", "a' OR 1=1 --", 5])
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
async def test_a_cached_answer_for_one_org_is_not_given_to_a_call_running_for_another() -> None:
    db = FakeDB({"org_space_list": [], "org_space_named_by": []})
    cat = Catalog(db, ttl=60)
    with org_context.bind(org_context.OrgContext(ORG, None, "service", "t", "-")):
        await cat.org_spaces(ORG)
        await cat.org_spaces(ORG)                                             # cached
        with pytest.raises(OrgMismatch):
            await cat.org_spaces("b" * 24)
    with org_context.bind(org_context.OrgContext("b" * 24, None, "service", "t", "-")), pytest.raises(OrgMismatch):
        await cat.org_spaces(ORG)                                             # the cache hit does not skip the check
    assert len(db.calls) == 2


@pytest.mark.asyncio
async def test_a_read_that_started_before_a_write_does_not_store_what_it_saw() -> None:
    gate, release = asyncio.Event(), asyncio.Event()
    stale = rec(collection_id=1, sparse_enabled=True, sparse_idf=True, lexical_version="v", status="ready")

    class Slow:
        async def run(self, fn, **opts) -> object:
            class Tx:
                async def fetchrow(_s, sql, name) -> object:
                    gate.set()
                    await release.wait()
                    return stale
            return await fn(Tx())

    cat = Catalog(Slow(), ttl=60)
    task = asyncio.create_task(cat.collection("records"))
    await gate.wait()
    cat.forget("records")                                                     # a write finished while the read was in flight
    release.set()
    await task
    assert "records" not in cat._collections


@pytest.mark.asyncio
async def test_a_configuration_error_other_than_the_missing_jobs_login_is_not_renamed() -> None:
    from app.core.db import DbConfigError

    class DB(FakeDB):
        cfg = SimpleNamespace(jobs_dsn="postgresql://jobs@h/db")

    cat = Catalog(DB({"collection_create": DbConfigError("pooler must be one of")}), ttl=0)
    with pytest.raises(DbConfigError, match="pooler"):
        await cat.create_collection("c", sparse_enabled=True, sparse_idf=True)


@pytest.mark.asyncio
async def test_adopting_a_space_does_not_swallow_a_unique_violation() -> None:
    cat = Catalog(FakeDB({"org_space_adopt": asyncpg.UniqueViolationError("dup")}), ttl=0)
    with pytest.raises(asyncpg.UniqueViolationError):
        await cat.activate_space(ORG, "m", 4)


@pytest.mark.asyncio
async def test_a_job_without_the_jobs_credential_names_the_command_and_a_duplicate_is_a_typed_error() -> None:
    from app.core.db import DbConfigError

    class NoJobs(FakeDB):
        cfg = SimpleNamespace(jobs_dsn="")

    cat = Catalog(NoJobs({"collection_create": DbConfigError("no jobs dsn")}), ttl=0)
    with pytest.raises(PgVectorError, match="PGVECTOR_JOBS_DSN"):
        await cat.create_collection("c", sparse_enabled=True, sparse_idf=True)
    cat = Catalog(NoJobs({"collection_create": asyncpg.UniqueViolationError("dup")}), ttl=0)
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
                    return [(vrids[0], ORG)]
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


def test_the_repr_of_a_config_never_shows_a_password() -> None:
    cfg = PgVectorConfig.from_dict({"dsn": "postgresql://pipeshub_query_login:s3cret-a@h/db", "jobs_dsn": "postgresql://pipeshub_jobs:s3cret-b@h/db"})
    assert "s3cret" not in repr(cfg) and "s3cret" not in str(cfg)
