"""Unit tests for CollectionManifestStore.

The manifest's consumers are the embedding-model-change guard and the rebuild
flow — the two most destructive paths in the system — so the properties under
test here are the ones whose absence loses data: a concurrent writer's entry
must not be silently dropped, and a stale in-process copy must not be served
forever to a caller about to drop collections.
"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.vector_db.collection_manifest import (
    MANIFEST_CONFIG_KEY,
    CollectionManifestStore,
    ManagedCollection,
    ManifestConflictError,
)


class FakeKV:
    """A shared config store, so two manifest stores can race over one key."""

    def __init__(self) -> None:
        self.data: dict = {}
        self.writes = 0
        self.reads = 0

    def as_config_service(self):
        async def get_config(key, default=None):
            self.reads += 1
            return self.data.get(key, default)

        async def set_config(key, value):
            self.writes += 1
            # Deep-copy through the boundary the way a real KV round trip would,
            # so a store cannot accidentally share a mutable dict with another.
            self.data[key] = {k: dict(v) for k, v in value.items()}

        svc = MagicMock()
        svc.get_config = AsyncMock(side_effect=get_config)
        svc.set_config = AsyncMock(side_effect=set_config)
        return svc


def _entry(name: str, dimension: int = 1024, ctype: str = "records") -> ManagedCollection:
    return ManagedCollection(
        name=name,
        collection_type=ctype,
        embedding_dimension=dimension,
        strategy_name="single",
    )


def _store(kv: FakeKV) -> CollectionManifestStore:
    return CollectionManifestStore(kv.as_config_service(), MagicMock())


class TestRoundTrip:
    @pytest.mark.asyncio
    async def test_record_then_list(self):
        store = _store(FakeKV())
        await store.record(_entry("records"))
        assert [e.name for e in await store.list()] == ["records"]

    @pytest.mark.asyncio
    async def test_empty_manifest_lists_nothing(self):
        assert await _store(FakeKV()).list() == []

    @pytest.mark.asyncio
    async def test_forget_removes_only_the_named_entry(self):
        store = _store(FakeKV())
        await store.record(_entry("records"))
        await store.record(_entry("entities", ctype="entities"))

        await store.forget("records")

        assert [e.name for e in await store.list()] == ["entities"]

    @pytest.mark.asyncio
    async def test_forget_unknown_name_is_a_noop(self):
        kv = FakeKV()
        store = _store(kv)
        await store.record(_entry("records"))
        writes_before = kv.writes

        await store.forget("never_existed")

        assert kv.writes == writes_before

    @pytest.mark.asyncio
    async def test_recording_an_identical_entry_does_not_write(self):
        """ensure_collection re-records on every existence-cache miss; that
        must not turn into a KV write per record."""
        kv = FakeKV()
        store = _store(kv)
        await store.record(_entry("records"))
        writes_before = kv.writes

        await store.record(_entry("records"))

        assert kv.writes == writes_before

    @pytest.mark.asyncio
    async def test_updating_a_changed_entry_does_write(self):
        kv = FakeKV()
        store = _store(kv)
        await store.record(_entry("records", dimension=768))
        writes_before = kv.writes

        await store.record(_entry("records", dimension=1024))

        assert kv.writes > writes_before
        assert (await store.get("records")).embedding_dimension == 1024


class TestConcurrentWriters:
    @pytest.mark.asyncio
    async def test_two_stores_over_one_kv_both_survive(self):
        """The lost-update case: each process holds its own view and writes the
        whole map. Without a merge, whichever wrote last erases the other's
        collection from the manifest — and the model-change guard then waves
        through a change while that collection still holds vectors."""
        kv = FakeKV()
        indexing = _store(kv)
        query = _store(kv)

        await indexing.record(_entry("drive_records"))
        await query.record(_entry("slack_records"))

        names = {e.name for e in await _store(kv).list()}
        assert names == {"drive_records", "slack_records"}

    @pytest.mark.asyncio
    async def test_interleaved_records_all_survive(self):
        kv = FakeKV()
        stores = [_store(kv) for _ in range(4)]

        await asyncio.gather(
            *(s.record(_entry(f"c{i}_records")) for i, s in enumerate(stores))
        )

        names = {e.name for e in await _store(kv).list()}
        assert names == {"c0_records", "c1_records", "c2_records", "c3_records"}

    @pytest.mark.asyncio
    async def test_forget_preserves_another_writers_entry(self):
        kv = FakeKV()
        a, b = _store(kv), _store(kv)
        await a.record(_entry("a_records"))
        await b.record(_entry("b_records"))

        await a.forget("a_records")

        assert [e.name for e in await _store(kv).list()] == ["b_records"]


class TestFreshness:
    @pytest.mark.asyncio
    async def test_cached_read_does_not_hit_the_store_again(self):
        kv = FakeKV()
        store = _store(kv)
        await store.list()
        reads_after_first = kv.reads

        await store.list()

        assert kv.reads == reads_after_first

    @pytest.mark.asyncio
    async def test_fresh_read_bypasses_the_cache(self):
        """The rebuild and model-change paths pass fresh=True; acting on a
        stale view there drops the wrong collections."""
        kv = FakeKV()
        observer = _store(kv)
        await observer.list()

        await _store(kv).record(_entry("late_records"))

        assert [e.name for e in await observer.list(fresh=True)] == ["late_records"]

    @pytest.mark.asyncio
    async def test_mutation_reflects_immediately_in_the_same_store(self):
        store = _store(FakeKV())
        await store.record(_entry("records"))
        assert [e.name for e in await store.list()] == ["records"]

    @pytest.mark.asyncio
    async def test_listing_returns_a_copy(self):
        """A caller mutating the returned list must not corrupt the cache."""
        store = _store(FakeKV())
        await store.record(_entry("records"))

        (await store.list()).clear()

        assert [e.name for e in await store.list()] == ["records"]


class TestMalformedAndConflicting:
    @pytest.mark.asyncio
    async def test_malformed_entry_is_dropped_not_raised(self):
        kv = FakeKV()
        kv.data[MANIFEST_CONFIG_KEY] = {
            "good": {
                "name": "good",
                "collection_type": "records",
                "embedding_dimension": 1024,
                "strategy_name": "single",
            },
            "bad": {"unexpected_field": True},
        }

        assert [e.name for e in await _store(kv).list()] == ["good"]

    @pytest.mark.asyncio
    async def test_second_collection_type_claiming_one_name_is_rejected(self):
        """Two datasets in one physical collection would make the rebuild flow
        recreate it at the wrong dimension for one of them."""
        store = _store(FakeKV())
        await store.record(_entry("shared", ctype="records"))

        with pytest.raises(ManifestConflictError):
            await store.record(_entry("shared", ctype="entities"))

    @pytest.mark.asyncio
    async def test_missing_key_reads_as_empty(self):
        kv = FakeKV()
        kv.data[MANIFEST_CONFIG_KEY] = None
        assert await _store(kv).list() == []
