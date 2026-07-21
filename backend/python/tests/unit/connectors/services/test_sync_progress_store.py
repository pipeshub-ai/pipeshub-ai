"""Unit tests for the run-scoped connector sync progress store.

Covers:
- ConnectorSyncProgressStore counter lifecycle against an in-memory fake Redis
  (start_run resets, add_discovered/record_result gated on key existence,
  close_discovery freezes the total).
- summarize_run phase/percent/isActive derivation, including the stalled-run
  and settled cases.
"""

import logging
import time

from app.connectors.services.sync_progress_store import (
    STALE_THRESHOLD_MS,
    ConnectorSyncProgressStore,
    SyncPhase,
    summarize_run,
)


class FakeRedis:
    """Minimal async Redis hash fake (decode_responses=True semantics)."""

    def __init__(self) -> None:
        self.store: dict[str, dict[str, str]] = {}
        self.expiries: dict[str, int] = {}
        self.eval_calls = 0

    async def delete(self, *keys: str) -> None:
        for key in keys:
            self.store.pop(key, None)
            self.expiries.pop(key, None)

    async def hset(self, key, field=None, value=None, mapping=None) -> int:
        h = self.store.setdefault(key, {})
        if mapping:
            for k, v in mapping.items():
                h[k] = str(v)
        if field is not None:
            h[field] = str(value)
        return 1

    async def expire(self, key, ttl) -> bool:
        self.expiries[key] = ttl
        return True

    async def exists(self, key) -> int:
        return 1 if key in self.store else 0

    async def hget(self, key, field):
        return self.store.get(key, {}).get(field)

    async def hincrby(self, key, field, amount=1) -> int:
        h = self.store.setdefault(key, {})
        cur = int(h.get(field, 0) or 0) + amount
        h[field] = str(cur)
        return cur

    async def hgetall(self, key):
        return dict(self.store.get(key, {}))

    async def eval(self, script, numkeys, *keys_and_args):
        self.eval_calls += 1
        keys = keys_and_args[:numkeys]
        args = keys_and_args[numkeys:]
        key = keys[0]
        if "HINCRBY" in script:
            expected_run_id, field, count, heartbeat, outcome_id, ttl = args
            if key not in self.store:
                return 0
            if expected_run_id and await self.hget(key, "runId") != expected_run_id:
                return 0
            if outcome_id:
                outcome_key = keys[1]
                seen = self.store.setdefault(outcome_key, {})
                if outcome_id in seen:
                    return 0
                seen[outcome_id] = "1"
                await self.expire(outcome_key, int(ttl))
            await self.hincrby(key, field, int(count))
            await self.hset(key, "heartbeatAt", heartbeat)
            await self.expire(key, int(ttl))
            return 1

        expected_run_id, phase, heartbeat, ttl = args
        if key not in self.store:
            return 0
        if expected_run_id and await self.hget(key, "runId") != expected_run_id:
            return 0
        discovered = await self.hget(key, "discovered") or "0"
        await self.hset(
            key,
            mapping={
                "phase": phase,
                "total": discovered,
                "heartbeatAt": heartbeat,
            },
        )
        await self.expire(key, int(ttl))
        return 1


def make_store() -> tuple[ConnectorSyncProgressStore, FakeRedis]:
    redis = FakeRedis()
    store = ConnectorSyncProgressStore(logging.getLogger("test"), redis)
    return store, redis


ORG = "org1"
CONN = "conn1"


class TestStoreLifecycle:
    async def test_start_run_initializes_counters(self) -> None:
        store, _ = make_store()
        run_id = await store.start_run(ORG, CONN, full_sync=True, run_id="r1")
        assert run_id == "r1"
        data = await store.get(ORG, CONN)
        assert data is not None
        assert data["phase"] == SyncPhase.DISCOVERING
        assert data["discovered"] == 0
        assert data["indexed"] == 0
        assert data["total"] == 0
        assert data["fullSync"] is True

    async def test_add_discovered_only_when_run_exists(self) -> None:
        store, redis = make_store()
        # No run yet -> no-op (must not create the key).
        await store.add_discovered(ORG, CONN, 5)
        assert await store.get(ORG, CONN) is None

        await store.start_run(ORG, CONN, run_id="r1")
        await store.add_discovered(ORG, CONN, 5)
        await store.add_discovered(ORG, CONN, 3)
        data = await store.get(ORG, CONN)
        assert data["discovered"] == 8
        assert redis.eval_calls == 3

    async def test_close_discovery_freezes_total(self) -> None:
        store, _ = make_store()
        await store.start_run(ORG, CONN, run_id="r1")
        await store.add_discovered(ORG, CONN, 42)
        await store.close_discovery(ORG, CONN)
        data = await store.get(ORG, CONN)
        assert data["phase"] == SyncPhase.INDEXING
        assert data["total"] == 42

    async def test_record_result_buckets_and_gating(self) -> None:
        store, _ = make_store()
        # No run -> ignored.
        await store.record_result(ORG, CONN, outcome="indexed")
        assert await store.get(ORG, CONN) is None

        await store.start_run(ORG, CONN, run_id="r1")
        await store.record_result(ORG, CONN, outcome="indexed")
        await store.record_result(ORG, CONN, outcome="indexed")
        await store.record_result(ORG, CONN, outcome="failed")
        await store.record_result(ORG, CONN, outcome="skipped")
        await store.record_result(ORG, CONN, outcome="bogus")
        data = await store.get(ORG, CONN)
        assert data["indexed"] == 2
        assert data["failed"] == 1
        assert data["skipped"] == 1

    async def test_record_result_is_run_scoped_and_idempotent(self) -> None:
        store, _ = make_store()
        await store.start_run(ORG, CONN, run_id="r1")
        await store.record_result(
            ORG, CONN, outcome="indexed", run_id="r1", record_id="record-1"
        )
        await store.record_result(
            ORG, CONN, outcome="indexed", run_id="r1", record_id="record-1"
        )
        await store.record_result(
            ORG, CONN, outcome="indexed", run_id="obsolete", record_id="record-2"
        )
        data = await store.get(ORG, CONN)
        assert data["indexed"] == 1

    async def test_clear_removes_run(self) -> None:
        store, _ = make_store()
        await store.start_run(ORG, CONN, run_id="r1")
        await store.clear(ORG, CONN)
        assert await store.get(ORG, CONN) is None

    async def test_close_discovery_matching_run_id_closes(self) -> None:
        store, _ = make_store()
        await store.start_run(ORG, CONN, run_id="r1")
        await store.add_discovered(ORG, CONN, 5)
        await store.close_discovery(ORG, CONN, expected_run_id="r1")
        data = await store.get(ORG, CONN)
        assert data["phase"] == SyncPhase.INDEXING
        assert data["total"] == 5

    async def test_close_discovery_stale_run_id_is_noop(self) -> None:
        # A cancelled task carrying an old run_id must not close the newer run
        # that replaced it (start_run already wrote a fresh runId).
        store, _ = make_store()
        await store.start_run(ORG, CONN, run_id="r2")
        await store.add_discovered(ORG, CONN, 7)
        await store.close_discovery(ORG, CONN, expected_run_id="r1")
        data = await store.get(ORG, CONN)
        assert data["phase"] == SyncPhase.DISCOVERING
        assert data["total"] == 0

    async def test_is_current_run(self) -> None:
        store, _ = make_store()
        # No run stored yet -> treat as current so callers fall back.
        assert await store.is_current_run(ORG, CONN, "r1") is True
        await store.start_run(ORG, CONN, run_id="r2")
        assert await store.is_current_run(ORG, CONN, "r2") is True
        assert await store.is_current_run(ORG, CONN, "r1") is False
        # No run_id to compare -> cannot tell, treat as current.
        assert await store.is_current_run(ORG, CONN, None) is True


class TestSummarizeRun:
    def test_none_run_is_idle_inactive(self) -> None:
        view = summarize_run(None)
        assert view["phase"] == SyncPhase.IDLE
        assert view["isActive"] is False
        assert view["percent"] is None

    def test_discovering_is_active_and_indeterminate(self) -> None:
        now = int(time.time() * 1000)
        view = summarize_run(
            {"phase": SyncPhase.DISCOVERING, "discovered": 10, "heartbeatAt": now}
        )
        assert view["isActive"] is True
        assert view["percent"] is None

    def test_indexing_partial_is_active_with_percent(self) -> None:
        now = int(time.time() * 1000)
        view = summarize_run(
            {
                "phase": SyncPhase.INDEXING,
                "total": 42,
                "indexed": 18,
                "failed": 0,
                "skipped": 0,
                "heartbeatAt": now,
            }
        )
        assert view["isActive"] is True
        assert view["processed"] == 18
        assert view["percent"] == 43

    def test_indexing_complete_is_settled(self) -> None:
        now = int(time.time() * 1000)
        view = summarize_run(
            {
                "phase": SyncPhase.INDEXING,
                "total": 10,
                "indexed": 8,
                "failed": 1,
                "skipped": 1,
                "heartbeatAt": now,
            }
        )
        assert view["isActive"] is False
        assert view["percent"] == 100

    def test_indexing_with_zero_total_is_settled(self) -> None:
        now = int(time.time() * 1000)
        view = summarize_run(
            {"phase": SyncPhase.INDEXING, "total": 0, "heartbeatAt": now}
        )
        assert view["isActive"] is False
        assert view["percent"] == 100

    def test_stale_run_is_inactive(self) -> None:
        stale_heartbeat = int(time.time() * 1000) - STALE_THRESHOLD_MS - 1000
        view = summarize_run(
            {
                "phase": SyncPhase.INDEXING,
                "total": 42,
                "indexed": 1,
                "heartbeatAt": stale_heartbeat,
            }
        )
        assert view["isStale"] is True
        assert view["isActive"] is False
