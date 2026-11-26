"""SK-1..SK-4, SK-6, SK-7: objects sealed with the partition's data key (D33)."""

from __future__ import annotations

import logging

import pytest

from harness_core.testing.ids import SeededIds
from harness_core.workspace import (
    ObjectScope,
    ObjectUnreadable,
    WorkspaceKeyDestroyed,
)
from harness_core.workspace.memory import InMemoryBinaryObjectStore, InMemoryKeyProvider
from harness_core.workspace.sealing import SealedObjectStore

SESSION_1 = ObjectScope("org-1", "ws:session-1")
SESSION_2 = ObjectScope("org-1", "ws:session-2")
PLAINTEXT = b"quarterly numbers: 42, 17, 99 -- confidential"


def _stores() -> tuple[SealedObjectStore, InMemoryBinaryObjectStore, InMemoryKeyProvider]:
    inner = InMemoryBinaryObjectStore()
    keys = InMemoryKeyProvider(ids=SeededIds())
    return SealedObjectStore(inner, keys), inner, keys


async def test_sk1_stored_bytes_are_ciphertext() -> None:
    store, inner, _ = _stores()
    ref = await store.put(SESSION_1, PLAINTEXT)
    [stored] = inner.stored_bytes()
    assert PLAINTEXT not in stored and b"confidential" not in stored
    assert ref.size_bytes == len(PLAINTEXT)
    assert ref.content_hash.startswith("hmac-sha256:")
    assert await store.get(SESSION_1, ref) == PLAINTEXT


async def test_sk2_dedupe_is_per_partition() -> None:
    store, inner, _ = _stores()
    first = await store.put(SESSION_1, PLAINTEXT)
    second = await store.put(SESSION_1, PLAINTEXT)
    assert first == second
    assert inner.object_count == 1
    elsewhere = await store.put(SESSION_2, PLAINTEXT)
    assert elsewhere.content_hash != first.content_hash
    assert elsewhere.object_id != first.object_id
    assert inner.object_count == 2


async def test_sk3_a_ref_does_not_open_in_another_partition(caplog: pytest.LogCaptureFixture) -> None:
    store, _, _ = _stores()
    ref = await store.put(SESSION_1, PLAINTEXT)
    await store.put(SESSION_2, b"warm the other key")
    with caplog.at_level(logging.WARNING), pytest.raises(ObjectUnreadable):
        await store.get(SESSION_2, ref)
    assert "workspace.object_unreadable" in caplog.text and "reason=other_key" in caplog.text
    assert "confidential" not in caplog.text


async def test_sk4_destroying_the_key_shreds_every_object_of_the_partition() -> None:
    store, inner, keys = _stores()
    refs = [await store.put(SESSION_1, f"file {i}".encode()) for i in range(5)]
    survivor = await store.put(SESSION_2, PLAINTEXT)
    await keys.destroy("org-1", "ws:session-1")
    for ref in refs:
        with pytest.raises(WorkspaceKeyDestroyed):
            await store.get(SESSION_1, ref)
    with pytest.raises(WorkspaceKeyDestroyed):
        await store.put(SESSION_1, b"new writes are refused too")
    assert inner.object_count == 6
    assert await store.get(SESSION_2, survivor) == PLAINTEXT


async def test_sk4_a_key_reissued_elsewhere_cannot_open_shredded_bytes() -> None:
    store, inner, keys = _stores()
    ref = await store.put(SESSION_1, PLAINTEXT)
    await keys.destroy("org-1", "ws:session-1")
    # Even an attacker-supplied fresh provider for the same scope only gets a new key.
    with pytest.raises(ObjectUnreadable):
        await SealedObjectStore(inner, InMemoryKeyProvider()).get(SESSION_1, ref)


async def test_sk6_key_material_is_not_in_repr() -> None:
    _, _, keys = _stores()
    key = await keys.data_key("org-1", "ws:session-1")
    assert key.material.hex() not in repr(key)
    assert repr(key.material) not in repr(key)


async def test_sk7_tampered_bytes_fail_authentication() -> None:
    store, inner, _ = _stores()
    ref = await store.put(SESSION_1, PLAINTEXT)
    inner.tamper()
    with pytest.raises(ObjectUnreadable):
        await store.get(SESSION_1, ref)


async def test_sk7_a_forged_content_hash_fails_authentication() -> None:
    store, _, _ = _stores()
    ref = await store.put(SESSION_1, PLAINTEXT)
    other = await store.put(SESSION_1, b"other")
    forged = type(ref)(ref.object_id, other.content_hash, ref.size_bytes)
    with pytest.raises(ObjectUnreadable):
        await store.get(SESSION_1, forged)


async def test_an_unpartitioned_scope_is_refused() -> None:
    store, _, _ = _stores()
    with pytest.raises(ValueError, match="partitioned"):
        await store.put(ObjectScope("org-1"), PLAINTEXT)
