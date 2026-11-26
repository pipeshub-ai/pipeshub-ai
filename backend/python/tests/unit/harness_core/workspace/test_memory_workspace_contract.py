"""The in-memory workspace adapters pass the P3.2 contract suites."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from harness_core.testing.clock import ManualClock
from harness_core.testing.contracts.workspace import (
    BinaryObjectStoreContract,
    KeyProviderContract,
    WorkspaceStoreContract,
    file,
)
from harness_core.testing.ids import SeededIds
from harness_core.workspace import ObjectScope
from harness_core.workspace.memory import (
    InMemoryBinaryObjectStore,
    InMemoryKeyProvider,
    InMemoryWorkspaceStore,
)
from harness_core.workspace.sealing import SealedObjectStore

_START = datetime(2026, 10, 2, tzinfo=UTC)


class _StoreHarness:
    def __init__(self) -> None:
        self.store = InMemoryWorkspaceStore(clock=ManualClock(_START))
        self.scope = ObjectScope("tenant-a")
        self.property_examples = 60

    def other_tenant(self) -> tuple[InMemoryWorkspaceStore, ObjectScope]:
        return self.store, ObjectScope("tenant-b")

    def workspace_id(self, name: str) -> str:
        return name


class TestInMemoryWorkspaceStore(WorkspaceStoreContract):
    @pytest.fixture
    def harness(self) -> _StoreHarness:  # type: ignore[override]
        return _StoreHarness()

    async def test_created_at_comes_from_the_injected_clock(self, harness: _StoreHarness) -> None:
        manifest = await self._seed(harness, "clock", file("a", "a"))
        assert manifest.created_at_ms == int(_START.timestamp() * 1000)


class _ObjectHarness:
    def __init__(self, objects: object, partition: str | None) -> None:
        self.objects = objects
        self.scope = ObjectScope("tenant-a", partition)
        self.other_tenant = ObjectScope("tenant-b", partition)


class TestInMemoryObjects(BinaryObjectStoreContract):
    @pytest.fixture
    def harness(self) -> _ObjectHarness:  # type: ignore[override]
        return _ObjectHarness(InMemoryBinaryObjectStore(), None)


class TestSealedObjects(BinaryObjectStoreContract):
    @pytest.fixture
    def harness(self) -> _ObjectHarness:  # type: ignore[override]
        store = SealedObjectStore(InMemoryBinaryObjectStore(), InMemoryKeyProvider())
        return _ObjectHarness(store, "ws:session-1")


async def test_md5_content_hash_stores_nothing() -> None:
    inner = InMemoryBinaryObjectStore()
    sealed = SealedObjectStore(inner, InMemoryKeyProvider())
    scope = ObjectScope("tenant-a", "ws:session-1")
    hashed = await sealed.content_hash(scope, b"bytes")
    assert hashed.startswith("hmac-sha256:")
    _ = await inner.content_hash(scope, b"bytes")
    assert inner.object_count == 0
    other_session = await sealed.content_hash(ObjectScope("tenant-a", "ws:session-2"), b"bytes")
    assert other_session != hashed


class _KeyHarness:
    def __init__(self) -> None:
        self.keys = InMemoryKeyProvider(ids=SeededIds())
        self.tenant_id = "tenant-a"
        self.key_domain = "ws:session-1"

    def others(self) -> tuple[tuple[InMemoryKeyProvider, str, str], ...]:
        return ((self.keys, "tenant-a", "ws:session-2"), (self.keys, "tenant-b", "ws:session-1"))

    async def shred(self, tenant_id: str, key_domain: str) -> None:
        await self.keys.destroy(tenant_id, key_domain)


class TestInMemoryKeyProvider(KeyProviderContract):
    @pytest.fixture
    def harness(self) -> _KeyHarness:  # type: ignore[override]
        return _KeyHarness()
