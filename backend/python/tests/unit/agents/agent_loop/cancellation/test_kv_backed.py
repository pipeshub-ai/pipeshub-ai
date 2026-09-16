"""`KVBackedRunCancellationRegistry` (`app/agents/agent_loop/cancellation/
kv_backed.py`) — cross-process cancellation fan-out over a `KeyValueStore`,
for a multi-worker/multi-replica query service where a cancel POST can land
on a process that doesn't own the run. Uses a real in-memory fake store
(no Redis/etcd) and a shrunk poll interval so the watcher tests run fast."""

from __future__ import annotations

import asyncio

import pytest

from app.agent_loop_lib.core.context import CancellationToken
from app.agents.agent_loop.cancellation import kv_backed as kv_backed_module
from app.agents.agent_loop.cancellation.kv_backed import KVBackedRunCancellationRegistry
from app.agents.agent_loop.cancellation.registry import RunOwner


def _owner(
    user_id: str = "user-1", org_id: str = "org-1", conversation_id: str | None = "conv-1"
) -> RunOwner:
    return RunOwner(user_id=user_id, org_id=org_id, conversation_id=conversation_id)


class _FakeKVStore:
    """Minimal in-memory stand-in for `KeyValueStore[str]` — only the three
    methods `KVBackedRunCancellationRegistry` actually calls."""

    def __init__(self) -> None:
        self.data: dict[str, str] = {}

    async def create_key(
        self, key: str, value: str, overwrite: bool = True, ttl: int | None = None,
    ) -> bool:
        if not overwrite and key in self.data:
            return False
        self.data[key] = value
        return True

    async def get_key(self, key: str) -> str | None:
        return self.data.get(key)

    async def delete_key(self, key: str) -> bool:
        return self.data.pop(key, None) is not None


@pytest.fixture(autouse=True)
def _fast_poll(monkeypatch: pytest.MonkeyPatch) -> None:
    """The real 1s poll interval would make the watcher tests below slow
    (or flaky under load); every test in this module gets a fast one."""
    monkeypatch.setattr(kv_backed_module, "_POLL_INTERVAL_SECONDS", 0.01)


class TestLocalHit:
    """Same worker owns the run — no KV round trip, same outcomes as
    `InProcessRunCancellationRegistry` alone."""

    async def test_is_active_reflects_the_local_entry(self) -> None:
        registry = KVBackedRunCancellationRegistry(_FakeKVStore())
        assert await registry.is_active("run-1") is False
        await registry.register("run-1", CancellationToken(), _owner())
        assert await registry.is_active("run-1") is True
        await registry.unregister("run-1")

    async def test_matching_owner_cancels_locally_without_touching_the_store(self) -> None:
        store = _FakeKVStore()
        registry = KVBackedRunCancellationRegistry(store)
        token = CancellationToken()
        await registry.register("run-1", token, _owner())

        outcome = await registry.cancel("run-1", _owner())

        assert outcome == "cancelled"
        assert token.is_cancelled is True
        assert store.data == {}
        await registry.unregister("run-1")

    async def test_mismatched_owner_is_forbidden_locally_without_touching_the_store(self) -> None:
        store = _FakeKVStore()
        registry = KVBackedRunCancellationRegistry(store)
        token = CancellationToken()
        await registry.register("run-1", token, _owner(user_id="alice"))

        outcome = await registry.cancel("run-1", _owner(user_id="mallory"))

        assert outcome == "forbidden"
        assert token.is_cancelled is False
        assert store.data == {}
        await registry.unregister("run-1")

    async def test_mismatched_conversation_id_is_forbidden_locally_for_the_same_user_and_org(
        self,
    ) -> None:
        """Same same-user, cross-conversation guard as InProcess, exercised
        through the KV-backed wrapper's local-hit path."""
        store = _FakeKVStore()
        registry = KVBackedRunCancellationRegistry(store)
        token = CancellationToken()
        await registry.register("run-1", token, _owner(conversation_id="conv-B"))

        outcome = await registry.cancel("run-1", _owner(conversation_id="conv-A"))

        assert outcome == "forbidden"
        assert token.is_cancelled is False
        assert store.data == {}
        await registry.unregister("run-1")


class TestCrossProcessCancel:
    """A cancel POST lands on a worker that doesn't own the run — it must
    publish to the store, and only the OWNING worker's watcher, polling
    the same store, actually flips the token."""

    async def test_owning_workers_watcher_picks_up_a_cross_process_cancel(self) -> None:
        store = _FakeKVStore()
        owner_registry = KVBackedRunCancellationRegistry(store)
        other_registry = KVBackedRunCancellationRegistry(store)
        token = CancellationToken()
        await owner_registry.register("run-1", token, _owner())
        try:
            outcome = await other_registry.cancel("run-1", _owner())
            # "Delivered", not "confirmed" yet — the watcher hasn't polled.
            assert outcome == "cancelled"
            assert token.is_cancelled is False

            await asyncio.wait_for(token.wait(), timeout=2.0)
            assert token.is_cancelled is True
        finally:
            await owner_registry.unregister("run-1")

    async def test_watcher_rejects_a_mismatched_requester_then_accepts_the_real_one(self) -> None:
        store = _FakeKVStore()
        owner_registry = KVBackedRunCancellationRegistry(store)
        other_registry = KVBackedRunCancellationRegistry(store)
        token = CancellationToken()
        await owner_registry.register("run-1", token, _owner())
        try:
            wrong_outcome = await other_registry.cancel("run-1", _owner(user_id="mallory"))
            assert wrong_outcome == "cancelled"  # delivered — watcher decides

            # Give the watcher a few poll ticks to see (and reject) it.
            await asyncio.sleep(0.05)
            assert token.is_cancelled is False

            real_outcome = await other_registry.cancel("run-1", _owner())
            assert real_outcome == "cancelled"

            await asyncio.wait_for(token.wait(), timeout=2.0)
            assert token.is_cancelled is True
        finally:
            await owner_registry.unregister("run-1")

    async def test_watcher_rejects_a_cross_process_conversation_id_mismatch(self) -> None:
        """`conversation_id` must survive the `model_dump_json()`/
        `model_validate_json()` round trip through the KV store and still
        be enforced by the owning worker's watcher, not just the local
        (same-process) `cancel()` path covered by TestLocalHit above."""
        store = _FakeKVStore()
        owner_registry = KVBackedRunCancellationRegistry(store)
        other_registry = KVBackedRunCancellationRegistry(store)
        token = CancellationToken()
        await owner_registry.register("run-1", token, _owner(conversation_id="conv-B"))
        try:
            outcome = await other_registry.cancel("run-1", _owner(conversation_id="conv-A"))
            assert outcome == "cancelled"  # delivered — watcher decides

            await asyncio.sleep(0.05)
            assert token.is_cancelled is False
        finally:
            await owner_registry.unregister("run-1")

    async def test_store_publish_failure_on_a_cross_process_miss_returns_not_found(self) -> None:
        store = _FakeKVStore()

        async def _boom(*_args: object, **_kwargs: object) -> bool:
            raise RuntimeError("redis unreachable")

        store.create_key = _boom  # type: ignore[method-assign]
        registry = KVBackedRunCancellationRegistry(store)

        outcome = await registry.cancel("no-such-run", _owner())

        assert outcome == "not_found"

    async def test_cancel_for_a_run_id_nobody_owns_anywhere_is_delivered_but_inert(self) -> None:
        """No local entry on this worker, and no owning worker ever polls
        the key — it just sits until TTL expiry. Not distinguishable from
        the "delivered, watcher hasn't polled yet" case from the caller's
        point of view, which is the documented behavior."""
        store = _FakeKVStore()
        registry = KVBackedRunCancellationRegistry(store)

        outcome = await registry.cancel("orphan-run", _owner())

        assert outcome == "cancelled"
        assert store.data[kv_backed_module._key("orphan-run")]


class TestUnregister:
    async def test_stops_the_watcher_task_and_deletes_the_kv_key(self) -> None:
        store = _FakeKVStore()
        registry = KVBackedRunCancellationRegistry(store)
        token = CancellationToken()
        await registry.register("run-1", token, _owner())
        watcher = registry._watchers["run-1"]
        # Simulate a leaked key (e.g. a stray publish) that unregister must
        # still clean up.
        await store.create_key(kv_backed_module._key("run-1"), "stale", overwrite=True)

        await registry.unregister("run-1")

        assert watcher.done()
        assert "run-1" not in registry._watchers
        assert await store.get_key(kv_backed_module._key("run-1")) is None

    async def test_a_cancel_published_after_unregister_finds_no_active_watcher(self) -> None:
        store = _FakeKVStore()
        owner_registry = KVBackedRunCancellationRegistry(store)
        other_registry = KVBackedRunCancellationRegistry(store)
        token = CancellationToken()
        await owner_registry.register("run-1", token, _owner())
        await owner_registry.unregister("run-1")

        outcome = await other_registry.cancel("run-1", _owner())

        # Published (there's no way for `other_registry` to know the run
        # already ended), but nothing is left watching for it, and the run
        # itself already completed normally — inert, not a bug.
        assert outcome == "cancelled"
        await asyncio.sleep(0.05)
        assert token.is_cancelled is False
