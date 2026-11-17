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


def test_every_delete_of_points_sits_in_a_fenced_function() -> None:
    sites = set(_calls_by_function("delete_points"))
    assert sites == {"delete_blocks_by_ids", "delete_embeddings", "_fence_and_verify"}, f"a new delete site: {sorted(sites)}"
    fenced = set(_calls_by_function("_fenced"))
    assert sites <= fenced, f"unfenced delete sites: {sorted(sites - fenced)}"


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
