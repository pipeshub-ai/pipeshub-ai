"""Contract suites every ``ContentStorePort`` and ``CheckpointPort`` adapter must pass.

Subclass :class:`ContentStoreContract` with a ``store`` fixture, or
:class:`CheckpointContract` with a ``checkpoints`` fixture. The in-memory
adapters run them in ``tests/unit/harness_core/journal/test_memory_content_contract.py``.
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

import pytest

from harness_core.journal.types import Checkpoint, ContentMissing, ContentRef

if TYPE_CHECKING:
    from harness_core.journal.ports import CheckpointPort, ContentStorePort


class ContentStoreContract:
    async def test_put_then_get_returns_the_bytes(self, store: ContentStorePort) -> None:
        data = "payload é中\U0001f600".encode()
        ref = await store.put(data)
        assert await store.get(ref) == data

    async def test_the_ref_names_the_content_hash_and_size(self, store: ContentStorePort) -> None:
        data = b"x" * 1000
        ref = await store.put(data)
        assert (ref.hash, ref.size) == (f"sha256:{hashlib.sha256(data).hexdigest()}", 1000)

    async def test_the_same_content_twice_has_the_same_hash(self, store: ContentStorePort) -> None:
        first, second = await store.put(b"same"), await store.put(b"same")
        assert (first.hash, first.size) == (second.hash, second.size)
        assert await store.get(second) == b"same"

    async def test_a_large_payload_round_trips(self, store: ContentStorePort) -> None:
        # Past any inline threshold an adapter keeps (PipesHub: 16 KiB inline, then sealed by Node).
        data = bytes(range(256)) * 256
        assert await store.get(await store.put(data)) == data

    async def test_empty_content_round_trips(self, store: ContentStorePort) -> None:
        assert await store.get(await store.put(b"")) == b""

    async def test_an_unknown_ref_is_missing(self, store: ContentStorePort) -> None:
        stranger = ContentRef(ref="cas:" + "0" * 64, hash="sha256:" + "0" * 64, size=1)
        with pytest.raises(ContentMissing):
            _ = await store.get(stranger)

    async def test_a_ref_whose_hash_or_size_does_not_match_is_missing(self, store: ContentStorePort) -> None:
        ref = await store.put(b"original")
        for forged in (
            ref.model_copy(update={"size": ref.size + 1}),
            ref.model_copy(update={"hash": "sha256:" + "f" * 64}),
        ):
            with pytest.raises(ContentMissing):
                _ = await store.get(forged)


def checkpoint(run_id: str = "run-1", seq: int = 7, **state: object) -> Checkpoint:
    return Checkpoint(run_id=run_id, stream_id="stream-1", fold_version=1, seq=seq, state=dict(state))  # pyright: ignore[reportArgumentType]


class CheckpointContract:
    async def test_save_then_load_returns_the_checkpoint(self, checkpoints: CheckpointPort) -> None:
        saved = checkpoint(cursor="turn-3", nested={"a": [1, 2]})
        assert await checkpoints.load(await checkpoints.save(saved)) == saved

    async def test_an_unknown_ref_loads_nothing(self, checkpoints: CheckpointPort) -> None:
        assert await checkpoints.load("ckpt:never-saved") is None

    async def test_checkpoints_of_different_points_have_different_refs(self, checkpoints: CheckpointPort) -> None:
        a = await checkpoints.save(checkpoint(seq=1))
        b = await checkpoints.save(checkpoint(seq=2))
        c = await checkpoints.save(checkpoint(run_id="run-2", seq=1))
        assert len({a, b, c}) == 3
        assert (await checkpoints.load(a), await checkpoints.load(c)) == (checkpoint(seq=1), checkpoint(run_id="run-2", seq=1))


__all__ = ["CheckpointContract", "ContentStoreContract", "checkpoint"]
