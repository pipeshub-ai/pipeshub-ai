"""Every delete of vector points reachable from indexing confirms the virtual record's lease first.

Two kinds of test, so a missed site fails one of them:
- a static check: every ``delete_points`` call in the vector store sits in a function that is fenced (``_fenced``), and every release of
  an orphaned virtual record in one that confirms the lease (``_require_lease``);
- a runtime check per site: with the lease taken away, the site raises ``LeaseLost`` and no delete reaches the store.

The table (call site | fenced how | test) is in the pull request description.
"""
from __future__ import annotations

import ast
import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio

from app.modules.pipeline import leases as leases_mod
from app.modules.pipeline.leases import LeaseLost, RecordLeases
from app.modules.transformers import vectorstore as vs_mod
from app.modules.transformers.vectorstore import VectorStore, VirtualRecordBusy
from app.services.vector_db.models import CollectionConfig
from tests.support.in_memory_vector_db import InMemoryVectorDBService
from tests.support.index_world import COLL, ORG, VRID, make_store
from tests.support.lease_fakes import FakeLeases

SOURCE = Path(vs_mod.__file__).read_text(encoding="utf-8")


def _calls_by_function(attribute: str) -> dict[str, list[ast.Call]]:
    out: dict[str, list[ast.Call]] = {}
    for function in ast.walk(ast.parse(SOURCE)):
        if isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for node in ast.walk(function):
                if isinstance(node, ast.Call):
                    name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", "")
                    if name == attribute:
                        out.setdefault(function.name, []).append(node)
    return out


def _function_calls(function_name: str, called: str) -> bool:
    return any(function_name in _calls_by_function(called) for _ in [0])


APP = Path(vs_mod.__file__).resolve().parents[2]
VECTOR_PROVIDERS = ("services/vector_db/qdrant", "services/vector_db/opensearch", "services/vector_db/redis", "services/vector_db/pgvector",
                    "services/vector_db/interface")

# Every ``delete_points`` call in the application, by (file, function), and what makes it safe. A new call site fails the test until it
# is listed here with its reason (and, for a write of a record's points, until it is fenced).
FENCED = {  # the points of a record being indexed: under the virtual record's lease, confirmed before the call
    ("modules/transformers/vectorstore.py", "delete_blocks_by_ids"),
    ("modules/transformers/vectorstore.py", "delete_embeddings"),
    ("modules/transformers/vectorstore.py", "_drop_points_of_other_runs"),
    ("modules/indexing/run.py", "delete_points_for_virtual_record"),
}
SWEEP = {("modules/indexing/stray_sweep.py", "sweep_stray_points")}   # runs under apply's lease, with a fence around each call
GRAPH_CHECKED = {   # the last record of a virtual record, or a connector, went away: the graph is re-read before deleting
    ("modules/indexing/run.py", "purge_connector"),
    ("modules/indexing/run.py", "bulk_delete_embeddings"),
    ("services/vector_db/membership.py", "_drop_points_where_no_record_remains"),
    ("services/vector_db/membership.py", "_rewrite_or_delete_locked"),
}
# The precision guard's deletes are pinned to points their caller knows are its own or are provably wrong or dead:
#   delete_written: the points of the batch's records (or entities) at the batch's precision and epoch in the writer's org, removed only on a
#                   definite verdict (the pin holds the other precision, or the epoch moved: wrong whoever wrote them), after the pin and the
#                   epoch were read again for that attempt; the lease is not consulted. Reached from ``settle_unconfirmed`` and ``drain_debt``;
#   delete_leaked:  the record's points tagged with a precision other than the pin's (wrong whoever wrote them);
#   sweep_dead:     the points of epochs a reset retired (no live writer can own them).
PRECISION_GUARD = {
    ("services/vector_db/precision_guard.py", "delete_written"),
    ("services/vector_db/precision_guard.py", "delete_leaked"),
    ("services/vector_db/precision_guard.py", "sweep_dead"),
}
ENTITY_FILES = {"modules/transformers/entity_vectorstore.py"}   # the entity index, keyed by entity: not the points of a record


DELETE_NAMES = {"delete_points", "delete_collection"}
# Not the vector store: the graph database has collections of its own.
NOT_VECTOR = ("services/graph_db",)


def _delete_references(tree: ast.AST) -> set[tuple[str, str]]:
    """(function, name) of every reference to a delete of the vector store in *tree*: a called attribute, the attribute taken without
    a call (an alias: ``f = service.delete_points``), and a name given as a string (``getattr(service, "delete_points")``)."""
    found: set[tuple[str, str]] = set()

    def walk(node: ast.AST, function: str) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            function = node.name
        if isinstance(node, ast.Attribute) and node.attr in DELETE_NAMES:
            found.add((function, node.attr))
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value in DELETE_NAMES:
            found.add((function, node.value))
        for child in ast.iter_child_nodes(node):
            walk(child, function)

    walk(tree, "<module>")
    return found


def _all_delete_sites(names: set[str] = DELETE_NAMES) -> set[tuple[str, str]]:
    sites: set[tuple[str, str]] = set()
    for path in APP.rglob("*.py"):
        relative = path.relative_to(APP).as_posix()
        text = path.read_text(encoding="utf-8")
        if relative.startswith((*VECTOR_PROVIDERS, *NOT_VECTOR)) or not any(n in text for n in names):
            continue
        sites.update((relative, function) for function, name in _delete_references(ast.parse(text)) if name in names)
    return sites


# Every reference to ``delete_collection`` (a whole collection, not a record's points) outside the vector providers.
# Each drops a whole collection, never the points of one virtual record: a connector's scope proven exclusively its own, the entity
# index under its own lock, or the registry's drop that both go through. A new one fails the test until it is listed with its reason.
COLLECTION_SITES = {
    ("modules/indexing/run.py", "_drop_scope_collections"),
    ("modules/indexing/run.py", "purge_connector_by_virtual_record_ids"),
    ("modules/transformers/entity_vectorstore.py", "_drop_collection"),
    ("services/vector_db/collection_registry.py", "_drop"),
}


def test_every_delete_of_points_in_the_application_is_listed_and_the_record_writing_ones_are_fenced() -> None:
    sites = _all_delete_sites({"delete_points"})
    unlisted = {s for s in sites if s not in FENCED | SWEEP | GRAPH_CHECKED | PRECISION_GUARD and s[0] not in ENTITY_FILES}
    assert not unlisted, f"a delete of vector points that is not listed (fence it, or list it with its reason): {sorted(unlisted)}"
    assert FENCED <= sites and SWEEP <= sites, "a listed fenced site no longer exists"
    fenced_calls = set(_calls_by_function("_fenced")) | set(_calls_by_function("fenced_delete"))
    for file, function in FENCED:
        text = (APP / file).read_text(encoding="utf-8")
        node = next(n for n in ast.walk(ast.parse(text)) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == function)
        assert "_fenced(" in ast.get_source_segment(text, node) or "fenced_delete(" in ast.get_source_segment(text, node), f"{file}:{function}"
    assert fenced_calls >= {"delete_blocks_by_ids", "delete_embeddings", "_drop_points_of_other_runs"}


def test_every_collection_drop_in_the_application_is_listed() -> None:
    sites = _all_delete_sites({"delete_collection"})
    assert sites == COLLECTION_SITES, f"a drop of a vector collection that is not listed (or a listed one that is gone): {sorted(sites ^ COLLECTION_SITES)}"


@pytest.mark.parametrize("source", [
    "async def f(svc, col, flt):\n    drop = svc.delete_points\n    await drop(col, flt)\n",
    "async def f(svc, col, flt):\n    await getattr(svc, 'delete_points')(col, flt)\n",
    "async def f(svc, col):\n    await svc.delete_collection(col)\n",
    "async def f(svc, col):\n    fn = getattr(svc, 'delete_collection', None)\n    await fn(col)\n",
])
def test_the_scan_sees_a_delete_taken_by_alias_or_by_name(source: str) -> None:
    assert _delete_references(ast.parse(source)), source


def test_every_release_of_an_orphaned_virtual_record_confirms_the_lease() -> None:
    releasing = set(_calls_by_function("rewrite_or_delete_virtual_record"))
    assert releasing == {"_cleanup_orphaned_embeddings_if_needed", "_resync_membership_after_write"}, sorted(releasing)
    for function in releasing:
        source = ast.get_source_segment(SOURCE, next(n for n in ast.walk(ast.parse(SOURCE))
                                                     if isinstance(n, ast.AsyncFunctionDef) and n.name == function))
        assert "_require_lease(" in source, f"{function} releases points without confirming the lease"


@pytest_asyncio.fixture
async def db() -> InMemoryVectorDBService:
    service = InMemoryVectorDBService()
    await service.create_collection(COLL, CollectionConfig(embedding_size=2))
    return service


@pytest.fixture
def manager(monkeypatch: pytest.MonkeyPatch) -> FakeLeases:
    fake = FakeLeases()
    leases = RecordLeases()
    leases.bind(fake)
    monkeypatch.setattr(VectorStore, "vrid_leases", leases)
    monkeypatch.setattr(leases_mod, "_POLL_S", 0.01)
    monkeypatch.setattr(leases_mod, "_MIN_RENEW_S", 0.05)
    monkeypatch.setattr(leases_mod, "messaging_env", SimpleNamespace(concurrency_lease_seconds=0.15, record_lease_wait_seconds=5.0))
    monkeypatch.setattr(vs_mod, "messaging_env", SimpleNamespace(record_lease_wait_seconds=5.0))
    return fake


def _sites(store: VectorStore) -> dict[str, object]:
    attempt = SimpleNamespace(rev="r1", stamp={})
    store.graph_provider.get_document = AsyncMock(return_value=None)       # the record is gone: the orphan release is entered
    store.graph_provider.get_records_by_virtual_record_id = AsyncMock(return_value=[])
    return {
        "delete_blocks_by_ids": lambda: store.delete_blocks_by_ids({"b"}, VRID, COLL, org_id=ORG),
        "delete_embeddings": lambda: store.delete_embeddings(VRID, COLL, org_id=ORG),
        "_fence_and_verify": lambda: store._fence_and_verify(attempt, "key", [], "rec", VRID, COLL, ORG),
        "orphan_release": lambda: VectorStore._cleanup_orphaned_embeddings_if_needed(store, "rec", VRID, ORG, MagicMock(md5_hash=None)),
    }


@pytest.mark.parametrize("site", ["delete_blocks_by_ids", "delete_embeddings", "_fence_and_verify", "orphan_release"])
async def test_a_site_whose_lease_was_taken_away_deletes_nothing(db, manager, site: str) -> None:
    store = make_store(db)
    spied: list[str] = []
    real_delete = db.delete_points

    async def spy(*args: object, **kwargs: object) -> None:
        spied.append("delete")
        await real_delete(*args, **kwargs)

    db.delete_points = spy  # type: ignore[method-assign]
    action = _sites(store)[site]
    async with store._vrid_lease("t", ORG, VRID):
        manager.take_away()
        with pytest.raises(LeaseLost):
            await action()  # type: ignore[operator]
    assert spied == [], "no delete reached the store"


async def test_purge_of_a_record_takes_the_lease_itself(db, manager) -> None:
    """The first reconciliation pass purges a record's vectors before ``apply`` runs: it holds the lease for the purge."""
    store = make_store(db)
    seen: list[dict] = []
    real = store.delete_embeddings

    async def spy(*args: object, **kwargs: object) -> None:
        seen.append(dict(manager.owner))
        await real(*args, **kwargs)

    store.delete_embeddings = spy  # type: ignore[method-assign]
    store.collection_registry.resolve_write_collection = MagicMock(return_value=COLL)
    await store.purge_record_vectors(ORG, VRID, None)
    assert seen and f"record:vrid:{ORG}:{VRID}" in seen[0], "the purge ran under the virtual record's lease"
    assert manager.owner == {}, "and released it"


async def test_a_delete_slower_than_the_lease_cannot_land_after_the_lease_is_lost(db, manager) -> None:
    """A delete call in flight keeps confirming the lease; when it is lost the call is cancelled and the run stops."""
    store = make_store(db)
    landed: list[str] = []

    async def slow_delete(*args: object, **kwargs: object) -> None:
        await asyncio.sleep(1.0)
        landed.append("delete landed")

    db.delete_points = slow_delete  # type: ignore[method-assign]

    async def lose_it_meanwhile() -> None:
        await asyncio.sleep(0.2)
        manager.take_away()

    async def attempt() -> None:
        async with store._vrid_lease("t", ORG, VRID):
            stealer = asyncio.create_task(lose_it_meanwhile())
            try:
                await store.delete_embeddings(VRID, COLL, org_id=ORG)
            finally:
                await stealer

    with pytest.raises(VirtualRecordBusy):  # the in-flight call was cancelled and the lost lease handed the delivery back
        await attempt()
    assert landed == []


async def test_one_failed_renewal_during_a_delete_does_not_drop_the_lease(db, manager) -> None:
    """Only the hold's renewer cancels the run, and only when the lease is really lost: one renewal error while a delete is in flight
    (the lease store briefly unreachable) leaves the lease held and the delete to finish."""
    store = make_store(db)
    landed: list[str] = []

    async def slow_delete(*args: object, **kwargs: object) -> None:
        manager.errors = 1               # one renewal fails while the call is in flight
        await asyncio.sleep(0.3)
        landed.append("delete landed")

    db.delete_points = slow_delete  # type: ignore[method-assign]
    async with store._vrid_lease("t", ORG, VRID):
        await store.delete_embeddings(VRID, COLL, org_id=ORG)
    assert landed == ["delete landed"]


async def test_the_vector_only_reindex_delete_runs_under_the_virtual_records_lease(db, manager) -> None:
    """``IndexingPipeline.delete_points_for_virtual_record`` wipes a whole virtual record: while a sibling holds its lease it waits and
    is handed back, deleting nothing."""
    from app.modules.indexing.run import IndexingPipeline
    from app.services.vector_db.collection_locator import StaticCollectionLocator
    from tests.support.vector_db import make_collection_registry

    monkey = pytest.MonkeyPatch()
    monkey.setattr(vs_mod, "messaging_env", SimpleNamespace(record_lease_wait_seconds=0.1))
    monkey.setattr(leases_mod, "messaging_env", SimpleNamespace(concurrency_lease_seconds=5.0, record_lease_wait_seconds=0.1))
    try:
        pipeline = IndexingPipeline(
            logger=MagicMock(), config_service=AsyncMock(), graph_provider=AsyncMock(),
            collection_registry=make_collection_registry(COLL), vector_db_service=db,
        )
        pipeline.collection_locator = StaticCollectionLocator([COLL])
        spied: list[str] = []
        real_delete = db.delete_points

        async def spy(*args: object, **kwargs: object) -> None:
            spied.append("delete")
            await real_delete(*args, **kwargs)

        db.delete_points = spy  # type: ignore[method-assign]
        manager.owner[f"record:vrid:{ORG}:{VRID}"] = "a sibling that is writing the virtual record"
        ctx = SimpleNamespace(org_id=ORG, connector_id="c1", record_group_id=None, mime_type="text/plain")
        with pytest.raises(VirtualRecordBusy):
            await pipeline.delete_points_for_virtual_record(VRID, ctx)
        assert spied == []
        manager.owner.clear()
        await pipeline.delete_points_for_virtual_record(VRID, ctx)    # free: it deletes, under the lease
        assert spied == ["delete"] and manager.owner == {}
    finally:
        monkey.undo()


async def test_renewals_that_keep_failing_cancel_the_delete_in_flight(db, manager) -> None:
    """A delete slower than the lease term, with the lease store unreachable for the rest of the term: the hold's renewer (it keeps
    renewing while the call is in flight) gives up once the term is nearly out, and the delete does not land."""
    store = make_store(db)
    landed: list[str] = []

    async def slow_delete(*args: object, **kwargs: object) -> None:
        manager.errors = 10_000          # every renewal from now on fails
        await asyncio.sleep(1.0)         # far longer than the 0.15 s term
        landed.append("delete landed")

    db.delete_points = slow_delete  # type: ignore[method-assign]
    with pytest.raises(VirtualRecordBusy):
        async with store._vrid_lease("t", ORG, VRID):
            await store.delete_embeddings(VRID, COLL, org_id=ORG)
    assert landed == []


async def test_a_lease_store_error_at_the_confirm_is_asked_once_more(db, manager, monkeypatch) -> None:
    """QA-S7e-29: one error from the lease store at a delete's confirm does not hand the delivery back; the confirm is asked again."""
    monkeypatch.setattr(leases_mod, "_MIN_RENEW_S", 60.0)  # the renewer stays out of the way: only the confirm asks the store
    monkeypatch.setattr(leases_mod, "messaging_env", SimpleNamespace(concurrency_lease_seconds=180.0, record_lease_wait_seconds=1.0))
    store = make_store(db)
    async with store._vrid_lease("t", ORG, VRID):
        manager.errors = 1       # the next renewal (this confirm) raises, the retry answers
        await store.delete_embeddings(VRID, COLL, org_id=ORG)
    assert manager.errors == 0


async def test_two_lease_store_errors_in_a_row_at_the_confirm_count_as_not_ours(db, manager, monkeypatch) -> None:
    monkeypatch.setattr(leases_mod, "_MIN_RENEW_S", 60.0)
    monkeypatch.setattr(leases_mod, "messaging_env", SimpleNamespace(concurrency_lease_seconds=180.0, record_lease_wait_seconds=1.0))
    store = make_store(db)
    async with store._vrid_lease("t", ORG, VRID):
        handle = vs_mod._HELD_LEASE.get()
        manager.errors = 1
        assert await handle.confirm() is True, "one error is asked again"
        manager.errors = 2
        assert await handle.confirm() is False, "a second error in a row hands the delivery back"
        manager.errors = 0


async def test_a_confirm_answered_not_ours_is_not_retried(db, manager) -> None:
    store = make_store(db)
    manager.renews = False
    with pytest.raises(VirtualRecordBusy):
        async with store._vrid_lease("t", ORG, VRID):
            manager.errors = 0
            await store.delete_embeddings(VRID, COLL, org_id=ORG)
