"""The PostgreSQL graph provider (``DATA_STORE=postgres``): it is opt-in, it comes from the factory over the shared database runtime, and it keeps up with the graph interface."""
from __future__ import annotations

import inspect
import logging
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.graph_db.graph_db_provider_factory import GraphDBProviderFactory
from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
from app.services.graph_db.postgres import core
from app.services.graph_db.postgres.base import NotImplementedInPostgres, PgBase
from app.services.graph_db.postgres.codegraph import CodeGraphMixin
from app.services.graph_db.postgres.delete import DeleteMixin
from app.services.graph_db.postgres.hierarchy import HierarchyMixin, HubMixin
from app.services.graph_db.postgres.identity import IdentityMixin
from app.services.graph_db.postgres.kb import KbMixin
from app.services.graph_db.postgres.kb_listings import KbListingsMixin
from app.services.graph_db.postgres.lookups import LookupsMixin
from app.services.graph_db.postgres.permissions import PermissionsMixin
from app.services.graph_db.postgres.provider import PostgresGraphProvider, unimplemented
from app.services.graph_db.postgres.records import RecordsMixin
from app.services.graph_db.postgres.trash import TrashMixin

FACTORY = "app.services.graph_db.graph_db_provider_factory.GraphDBProviderFactory"


# ---- the factory: off unless asked for --------------------------------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("env", [{}, {"DATA_STORE": "arangodb"}, {"DATA_STORE": "neo4j"}])
async def test_without_data_store_postgres_nothing_postgres_is_built(env: dict) -> None:
    """The default (and the other stores) never reach the PostgreSQL branch: no pool is acquired and no provider is made."""
    with patch.dict(os.environ, env, clear=False), patch("app.core.db.shared.acquire_db", new_callable=AsyncMock) as acquire, \
            patch(f"{FACTORY}._create_arango_http_provider", new_callable=AsyncMock), patch(f"{FACTORY}._create_neo4j_provider", new_callable=AsyncMock), \
            patch("app.services.graph_db.postgres.provider.PostgresGraphProvider") as pg:
        if not env:
            os.environ.pop("DATA_STORE", None)
        await GraphDBProviderFactory.create_provider(MagicMock(), MagicMock())
    acquire.assert_not_awaited()
    pg.assert_not_called()


@pytest.mark.asyncio
async def test_data_store_postgres_builds_the_provider_over_the_shared_runtime() -> None:
    """The provider gets its pool from ``acquire_db`` (one per process and loop), with the config of the process's own service role, never a client of its own."""
    db, config = MagicMock(), MagicMock()
    with patch.dict(os.environ, {"DATA_STORE": "Postgres", "PG_RUNTIME_SERVICE": "indexing"}), \
            patch("app.core.db.shared.acquire_db", new_callable=AsyncMock, return_value=db) as acquire, \
            patch("app.core.db.DbConfig.from_env", return_value="cfg") as from_env:
        provider = await GraphDBProviderFactory.create_provider(logging.getLogger("t"), config)
    from_env.assert_called_once_with("indexing")
    acquire.assert_awaited_once_with("cfg")
    assert isinstance(provider, PostgresGraphProvider)
    assert provider._db is db and provider.config_service is config


@pytest.mark.asyncio
async def test_an_unknown_store_still_names_postgres_among_the_choices() -> None:
    with patch.dict(os.environ, {"DATA_STORE": "cassandra"}), pytest.raises(ValueError, match="'postgres'"):
        await GraphDBProviderFactory.create_provider(MagicMock(), MagicMock())


# ---- parity with the interface ----------------------------------------------------------------------------------------------------------

def test_every_interface_method_is_implemented_or_stubbed_and_a_stub_says_so() -> None:
    abstract = set(IGraphDBProvider.__abstractmethods__)
    assert not PostgresGraphProvider.__abstractmethods__          # it can be built
    assert set(unimplemented()) <= abstract
    assert {"get_document", "batch_upsert_nodes", "create_node_if_absent", "get_existing_keys", "batch_create_edges"}.isdisjoint(unimplemented())
    assert "execute_query" in unimplemented()                      # a raw query has no meaning on this store


@pytest.mark.asyncio
async def test_a_stubbed_method_raises_not_implemented_instead_of_returning_nothing() -> None:
    p = PostgresGraphProvider(logging.getLogger("t"), MagicMock())
    with pytest.raises(NotImplementedInPostgres, match="execute_query"):
        await p.execute_query("RETURN 1")


@pytest.mark.parametrize("name", sorted(unimplemented()))
def test_a_stub_keeps_the_signature_of_the_interface_method(name: str) -> None:
    """Callers and the cross-provider tests read the parameters (``raise_on_error``, ``transaction``) off the class."""
    stub = getattr(PostgresGraphProvider, name)
    assert inspect.signature(stub) == inspect.signature(getattr(IGraphDBProvider, name))
    assert not getattr(stub, "__isabstractmethod__", False)


def _mismatches(name: str) -> list[str]:
    want = inspect.signature(inspect.unwrap(getattr(IGraphDBProvider, name))).parameters
    have = inspect.signature(inspect.unwrap(getattr(PostgresGraphProvider, name))).parameters
    out = []
    for pname, p in want.items():
        q = have.get(pname)
        if q is None:
            out.append(f"{pname}: missing")
        elif q.kind is not p.kind:
            out.append(f"{pname}: {q.kind.name}, the interface says {p.kind.name}")
        elif q.default is inspect.Parameter.empty and p.default is not inspect.Parameter.empty:
            out.append(f"{pname}: required here, optional in the interface")
    out += [f"{pname}: required here, optional in the interface" for pname, q in have.items()
            if pname not in want and q.default is inspect.Parameter.empty and q.kind not in (q.VAR_POSITIONAL, q.VAR_KEYWORD) and pname != "self"]
    shared = [n for n in want if n in have and want[n].kind in (want[n].POSITIONAL_ONLY, want[n].POSITIONAL_OR_KEYWORD)]
    if [n for n in have if n in shared] != shared:
        out.append("positional order differs")
    return out


IMPLEMENTED = sorted(n for n, _ in inspect.getmembers(IGraphDBProvider, inspect.isfunction)
                     if not n.startswith("_") and n not in unimplemented() and n in {*vars(core.CoreMixin), *vars(PermissionsMixin), *vars(IdentityMixin), *vars(CodeGraphMixin), *vars(RecordsMixin), *vars(HierarchyMixin), *vars(HubMixin), *vars(TrashMixin), *vars(LookupsMixin), *vars(DeleteMixin), *vars(KbMixin), *vars(KbListingsMixin)})


@pytest.mark.parametrize("name", IMPLEMENTED)
def test_an_implemented_method_accepts_what_the_interface_declares(name: str) -> None:
    assert inspect.iscoroutinefunction(getattr(PostgresGraphProvider, name))
    assert not _mismatches(name)


def test_the_foundation_implements_the_generic_documents_and_edges() -> None:
    assert len(IMPLEMENTED) >= 20


# ---- structure --------------------------------------------------------------------------------------------------------------------------

def test_no_name_is_defined_by_a_mixin_and_the_base_class() -> None:
    """A name defined twice is silently decided by the MRO."""
    names = {m: {n for n in vars(m) if not (n.startswith("__") and n.endswith("__"))} for m in (core.CoreMixin, PermissionsMixin, IdentityMixin, CodeGraphMixin, RecordsMixin, HierarchyMixin, HubMixin, TrashMixin, LookupsMixin, DeleteMixin, KbMixin, KbListingsMixin)}
    mixins = list(names)
    assert not [n for i, a in enumerate(mixins) for b in mixins[i + 1:] for n in names[a] & names[b]], "defined by two mixins"
    assert not [n for ns in names.values() for n in ns if n in vars(PgBase)]


def test_the_provider_keeps_the_config_service_callers_read_from_it() -> None:
    config = MagicMock()
    assert PostgresGraphProvider(logging.getLogger("t"), MagicMock(), config_service=config).config_service is config
    assert PostgresGraphProvider(logging.getLogger("t"), MagicMock()).config_service is None


# ---- the methods the interface gained so that callers need not know the concrete provider ----------------------------------------------------

HELPERS = ["_normalize_name", "_normalized_name_variants_lower", "_generate_upload_message", "_analyze_upload_structure", "_populate_file_destinations"]
CALLER_METHODS = ["_get_kb_context_for_record", "_fetch_existing_file_names_in_parent", "_validate_upload_context", "get_user_app_ids",
                  "get_user_permission_app_ids", "_create_reindex_event_payload", "delete_inherit_permissions_relation_record_group", "get_user_apps",
                  "delete_all_edges_for_node", "get_template", "clone_agent_template"]


def _providers() -> list[type]:
    from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
    from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider
    return [ArangoHTTPProvider, Neo4jProvider, PostgresGraphProvider]


@pytest.mark.parametrize("provider", _providers(), ids=lambda c: c.__name__)
def test_every_provider_has_every_method_callers_use(provider: type) -> None:
    assert not provider.__abstractmethods__
    for name in CALLER_METHODS:
        assert name in IGraphDBProvider.__abstractmethods__
        assert callable(getattr(provider, name))


@pytest.mark.parametrize("name", CALLER_METHODS)
def test_a_caller_method_of_the_postgres_provider_is_implemented_or_stubbed_never_empty(name: str) -> None:
    if name in unimplemented():
        return
    assert inspect.iscoroutinefunction(getattr(PostgresGraphProvider, name))


def test_the_shared_name_and_upload_helpers_give_what_each_store_gave() -> None:
    """The interface now holds the helpers the Arango and Neo4j providers each carried; the answers are the same on all three."""
    files = [{"filePath": "a/b/x.txt"}, {"filePath": "a/y.txt"}, {"filePath": "z.txt"}]
    valid = {"upload_target": "folder", "parent_folder": {"id": "p1"}}
    result = {"total_created": 3, "folders_created": 2, "failed_files": ["f"], "skipped_files": []}
    out = []
    for cls in _providers():
        p = object.__new__(cls)
        out.append((p._normalize_name("  e\u0301cole "), p._normalize_name(None), p._normalized_name_variants_lower(" Ée "),
                    p._generate_upload_message(result, "folder"), p._analyze_upload_structure(files, valid)))
    assert out[0] == out[1] == out[2]
    assert out[2][0] == "\u00e9cole" and out[2][1] is None


@pytest.mark.asyncio
async def test_ping_runs_one_constant_select_through_the_service_role() -> None:
    """The health probe costs one `SELECT 1` and reads no org or graph rows."""
    tx = MagicMock(fetchval=AsyncMock(return_value=1))
    db = MagicMock()
    db.runtime_uow.return_value.__aenter__ = AsyncMock(return_value=tx)
    db.runtime_uow.return_value.__aexit__ = AsyncMock(return_value=False)
    await PgBase(logging.getLogger("t"), db).ping()
    tx.fetchval.assert_awaited_once_with("/* pipeshub:graph.ping */ SELECT 1")


@pytest.mark.asyncio
@pytest.mark.parametrize(("org_id", "found"), [(None, True), ("o1", True), ("o2", False)])
async def test_get_user_by_email_reads_only_the_ambient_org(org_id: str | None, found: bool) -> None:
    """An ``org_id`` is checked against the bound org, never put into the query: a different org reads as no user."""
    from app.core.org_context import for_org
    from app.services.graph_db.postgres import identity

    p = object.__new__(IdentityMixin)
    p.logger = logging.getLogger("t")
    p._one = AsyncMock(return_value={"email": "a@x.io"})
    user = object()
    with for_org("o1", job="t"), patch.object(identity, "_arango", lambda d, _c: d), \
            patch.object(identity.User, "from_arango_user", return_value=user):
        got = await p.get_user_by_email("A@x.io", org_id=org_id)
    assert got is (user if found else None)
    if found:
        assert p._one.await_args.args[1:] == ("o1", "A@x.io")
    else:
        p._one.assert_not_awaited()
