"""The trash purge's scheduling, leadership, budgets and bookkeeping.

The graph here is an in-memory store that answers as both real stores do: the
keyset walk by (deletedAtTimestamp, key), a deleting connector's rows moving the
cursor without being returned, and the due check made again at delete time.
``tests/integration/test_trash_purge_e2e.py`` runs the same purge on a real
Neo4j and ArangoDB.
"""

from __future__ import annotations

import asyncio
import copy
import json
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import EventTypes
from app.connectors.services import trash_purge as purge_module
from app.connectors.services.trash_purge import (
    OUTBOX_DIRECTORY,
    STATE_KEY,
    Outcome,
    PurgeSettings,
    TrashPurgeError,
    TrashPurger,
    stored_document_ids,
)
from app.services.featureflag.platform_settings import PLATFORM_SETTINGS_KEY
from app.services.graph_db.common.utils import trash_purge_row

DAY_MS = 24 * 60 * 60 * 1000
ORG = "org-1"
DOC_A = "65f1c0ffee0123456789abcd"
DOC_B = "65f1c0ffee0123456789abce"


def _ms(year: int, month: int, day: int, hour: int) -> int:
    return int(datetime(year, month, day, hour, 30, tzinfo=timezone.utc).timestamp() * 1000)


NOW = _ms(2026, 10, 4, 3)


class _Graph:
    def __init__(self) -> None:
        self.records: dict[str, dict] = {}
        self.app_status: dict[str, str] = {}
        self.refuse: set[str] = set()
        self.refuse_counting = False
        self.groups_removed: list[str] = []
        self.calls: list[str] = []

    def trash(self, key: str, *, days_ago: float, connector: str = "kb-1", **extra: object) -> None:
        self.records[key] = {
            "_key": key, "orgId": ORG, "connectorId": connector, "connectorName": "KB", "origin": "UPLOAD",
            "externalRecordId": DOC_A, "isDeleted": True, "deletedAtTimestamp": NOW - int(days_ago * DAY_MS),
            "virtualRecordId": f"vr-{key}", "version": 1, **extra,
        }

    def _due(self, rec: dict, cutoff: int, max_attempts: int) -> bool:
        return (
            rec.get("isDeleted") is True
            and rec.get("deletedAtTimestamp") is not None
            and rec["deletedAtTimestamp"] <= cutoff
            and (rec.get("purgeAttempts") or 0) < max_attempts
        )

    def _row(self, rec: dict) -> dict:
        payload = {"orgId": rec["orgId"], "recordId": rec["_key"], "virtualRecordId": rec.get("virtualRecordId"),
                   "connectorId": rec["connectorId"], "version": rec.get("version", 1)}
        return trash_purge_row(rec["_key"], rec, {"isFile": True}, payload)

    async def get_purgeable_trashed_records(
        self, org_id, deleted_before, *, after=None, limit=500, max_attempts=5, transaction=None,
    ) -> dict:
        self.calls.append("list")
        due = sorted(
            (r for r in self.records.values() if r["orgId"] == org_id and self._due(r, deleted_before, max_attempts)),
            key=lambda r: (r["deletedAtTimestamp"], r["_key"]),
        )
        if after:
            due = [r for r in due if (r["deletedAtTimestamp"], r["_key"]) > tuple(after)]
        page = due[:limit]
        rows = [self._row(r) for r in page if self.app_status.get(r["connectorId"]) != "DELETING"]
        last = page[-1] if len(page) >= limit else None
        return {"records": rows, "next": (last["deletedAtTimestamp"], last["_key"]) if last else None}

    async def purge_trashed_records(
        self, record_ids, org_id, deleted_before, *, max_attempts=5, transaction=None,
    ) -> dict:
        self.calls.append("purge")
        if self.refuse & set(record_ids):
            raise RuntimeError("graph refused the write")
        purged, kept = [], []
        for key in dict.fromkeys(record_ids):
            rec = self.records.get(key)
            if (
                rec is not None and rec["orgId"] == org_id and self._due(rec, deleted_before, max_attempts)
                and self.app_status.get(rec["connectorId"]) != "DELETING"
            ):
                purged.append(self._row(self.records.pop(key)))
            else:
                kept.append(key)
        return {"purged": purged, "kept": kept}

    async def record_purge_failure(self, record_ids, org_id, error, transaction=None) -> int:
        if self.refuse_counting:
            raise RuntimeError("graph down")
        counted = 0
        for key in record_ids:
            rec = self.records.get(key)
            if rec and rec["orgId"] == org_id and rec.get("isDeleted") is True:
                rec["purgeAttempts"] = (rec.get("purgeAttempts") or 0) + 1
                rec["purgeLastError"] = error
                counted += 1
        return counted

    async def get_trash_purge_stats(self, org_id, max_attempts=5, transaction=None) -> dict:
        trashed = [r for r in self.records.values()
                   if r["orgId"] == org_id and r.get("isDeleted") is True and r.get("deletedAtTimestamp") is not None]
        stuck = [r for r in trashed if (r.get("purgeAttempts") or 0) >= max_attempts]
        pending = [r["deletedAtTimestamp"] for r in trashed if r not in stuck]
        return {"trashed": len(trashed), "stuck": len(stuck), "oldestDeletedAt": min(pending) if pending else None}

    async def purge_trash_kept_record_groups(self, org_id, *, limit=100, transaction=None) -> list[str]:
        return []

    async def get_document(self, key, collection, transaction=None, raise_on_error=False) -> dict | None:
        return copy.deepcopy(self.records.get(key))

    async def get_all_orgs(self, *, active=True, is_external=False, transaction=None) -> list[dict]:
        return [] if is_external else [{"_key": ORG}]


class _KV:
    def __init__(self, purge_settings: dict | None = None) -> None:
        self.values: dict[str, Any] = {PLATFORM_SETTINGS_KEY: {"softDeletePurge": purge_settings or {}}}

    async def get_config(self, key, default=None, use_cache=False, *, raise_on_error=False) -> object:
        return copy.deepcopy(self.values.get(key, default))

    async def set_config(self, key, value) -> bool:
        self.values[key] = json.loads(json.dumps(value))
        return True

    async def delete_config(self, key) -> bool:
        return self.values.pop(key, None) is not None

    async def list_keys_in_directory(self, directory) -> list[str]:
        return sorted(k for k in self.values if k.startswith(directory))

    def outbox(self) -> dict:
        return {k: v for k, v in self.values.items() if k.startswith(OUTBOX_DIRECTORY)}


class _SharedLease:
    """Redis SET NX semantics over one shared key, as VectorMembershipBackfillLeaderLock uses."""

    def __init__(self, owner: str, store: dict[str, str]) -> None:
        self.owner = owner
        self.store = store

    async def try_acquire(self) -> bool:
        holder = self.store.setdefault("leader", self.owner)
        return holder == self.owner

    async def refresh(self) -> bool:
        return self.store.get("leader") == self.owner

    async def release(self) -> None:
        if self.store.get("leader") == self.owner:
            del self.store["leader"]

    async def close(self) -> None:
        return None


class _Broker:
    def __init__(self) -> None:
        self.events: list[dict] = []
        self.accept = True

    async def send_messages(self, topic, messages) -> list[bool]:
        if self.accept:
            self.events.extend(m for _k, m in messages)
        return [self.accept] * len(messages)

    def deleted(self) -> list[str]:
        return [e["payload"]["recordId"] for e in self.events if e["eventType"] == EventTypes.DELETE_RECORD.value]


@pytest.fixture
def flag(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    mock = AsyncMock(return_value=True)
    monkeypatch.setattr(purge_module, "is_soft_delete_enabled", mock)
    monkeypatch.delenv("SOFT_DELETE_PURGE_INTERVAL_SECONDS", raising=False)
    monkeypatch.delenv("SOFT_DELETE_PURGE_MIN_AGE_SECONDS", raising=False)
    return mock


def _purger(
    graph: _Graph, kv: _KV, broker: _Broker | None = None, *, lease=None, now: int = NOW, monotonic=None,
) -> TrashPurger:
    return TrashPurger(
        MagicMock(), graph, kv, broker or _Broker(), lease or _SharedLease("me", {}),
        clock=lambda: now, sleep=AsyncMock(), monotonic=monotonic or (lambda: 0.0),
    )


class TestSettings:
    def test_defaults_are_the_designs(self, flag) -> None:
        s = PurgeSettings.from_config(None)
        assert (s.enabled, s.interval_days, s.min_age_days, s.run_hour_utc, s.page_size, s.max_attempts) == (
            True, 14, 14, 3, 500, 5
        )
        assert (s.max_records_per_run, s.max_run_minutes, s.page_pause_ms) == (500_000, 120, 200)
        assert s.min_age_ms == 14 * DAY_MS

    def test_values_are_bounded_and_bad_ones_fall_back(self, flag) -> None:
        s = PurgeSettings.from_config({
            "enabled": "no", "intervalDays": 0, "minAgeDays": 0, "pageSize": 100_000,
            "runHourUtc": "x", "maxAttempts": True,
        })
        assert s.enabled is True, "only a real boolean switches it off"
        assert s.interval_days == 1
        assert s.min_age_days == 1, "a mistyped 0 must not empty the trash"
        assert s.page_size == 1000
        assert s.run_hour_utc == 3 and s.max_attempts == 5

    def test_environment_overrides_in_seconds(self, flag, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SOFT_DELETE_PURGE_INTERVAL_SECONDS", "300")
        monkeypatch.setenv("SOFT_DELETE_PURGE_MIN_AGE_SECONDS", "0")
        s = PurgeSettings.from_config({"minAgeDays": 30})
        assert s.min_age_ms == 0
        assert s.is_due(None, _ms(2026, 10, 4, 15)), "no run hour with the interval overridden"
        assert not s.is_due(NOW, NOW + 299_000)
        assert s.is_due(NOW, NOW + 300_000)


class TestIsDue:
    def test_only_at_the_run_hour(self, flag) -> None:
        s = PurgeSettings()
        assert s.is_due(None, _ms(2026, 10, 4, 3))
        assert not s.is_due(None, _ms(2026, 10, 4, 4))

    def test_counts_whole_days_since_the_last_start(self, flag) -> None:
        s = PurgeSettings()
        last = _ms(2026, 10, 4, 3) + 20 * 60 * 1000
        assert not s.is_due(last, _ms(2026, 10, 17, 3))
        # A tick earlier in the hour than last time still counts on the 14th day.
        assert s.is_due(last, _ms(2026, 10, 18, 3) - 20 * 60 * 1000)


class TestTick:
    async def test_of_two_replicas_only_the_leader_purges(self, flag) -> None:
        graph, kv, shared = _Graph(), _KV(), {}
        graph.trash("a", days_ago=20)
        shared["leader"] = "other"

        assert await _purger(graph, kv, lease=_SharedLease("me", shared)).tick() == Outcome.NOT_LEADER
        assert graph.calls == [] and STATE_KEY not in kv.values

        del shared["leader"]
        assert await _purger(graph, kv, lease=_SharedLease("me", shared)).tick() == Outcome.FINISHED
        assert "a" not in graph.records
        assert shared == {}, "the lease is let go after the tick"

    async def test_the_kill_switch_stops_it(self, flag) -> None:
        graph, kv = _Graph(), _KV({"enabled": False})
        graph.trash("a", days_ago=20)
        assert await _purger(graph, kv).tick() == Outcome.DISABLED
        assert "a" in graph.records and graph.calls == []

    async def test_not_due_outside_the_run_hour(self, flag) -> None:
        graph, kv = _Graph(), _KV()
        graph.trash("a", days_ago=20)
        assert await _purger(graph, kv, now=_ms(2026, 10, 4, 9)).tick() == Outcome.NOT_DUE
        assert graph.calls == []

    async def test_a_flag_turned_off_mid_run_stops_at_the_page_and_resumes_later(self, flag) -> None:
        graph, kv = _Graph(), _KV({"pageSize": 1})
        graph.trash("a", days_ago=20)
        graph.trash("b", days_ago=19)
        # On at the start and for the first page, off before the second.
        flag.side_effect = [True, True, False]

        assert await _purger(graph, kv).tick() == Outcome.STOPPED
        assert set(graph.records) == {"b"}
        assert kv.values[STATE_KEY]["status"] == "running"

        flag.side_effect = None
        assert await _purger(graph, kv, now=_ms(2026, 10, 5, 11)).tick() == Outcome.FINISHED
        assert graph.records == {}, "resumed outside the run hour: it is the same run"

    async def test_the_record_budget_pauses_the_run_until_the_next_tick(self, flag) -> None:
        graph, kv = _Graph(), _KV({"pageSize": 1, "maxRecordsPerRun": 1})
        graph.trash("a", days_ago=20)
        graph.trash("b", days_ago=19)

        assert await _purger(graph, kv).tick() == Outcome.PAUSED
        assert set(graph.records) == {"b"}
        assert await _purger(graph, kv).tick() == Outcome.PAUSED
        assert graph.records == {}
        assert await _purger(graph, kv).tick() == Outcome.FINISHED

    async def test_the_time_budget_pauses_the_run(self, flag) -> None:
        graph, kv = _Graph(), _KV({"maxRunMinutes": 1})
        graph.trash("a", days_ago=20)
        clock = iter([0.0, 61.0])
        assert await _purger(graph, kv, monotonic=lambda: next(clock)).tick() == Outcome.PAUSED
        assert "a" in graph.records

    async def test_a_deleting_connectors_trash_moves_the_cursor_and_stays(self, flag) -> None:
        graph, kv = _Graph(), _KV({"pageSize": 1})
        graph.trash("a", days_ago=20, connector="going")
        graph.trash("b", days_ago=19)
        graph.app_status["going"] = "DELETING"

        assert await _purger(graph, kv).tick() == Outcome.FINISHED
        assert set(graph.records) == {"a"}

    async def test_with_the_trash_off_nothing_is_read_or_written(self, flag) -> None:
        graph, kv = _Graph(), _KV()
        graph.trash("a", days_ago=20)
        flag.return_value = False
        assert await _purger(graph, kv).tick() == Outcome.DISABLED
        assert graph.calls == [] and STATE_KEY not in kv.values and kv.outbox() == {}


class TestPageFailures:
    async def test_events_the_broker_refuses_stay_owed_for_the_records_that_went(self, flag) -> None:
        graph, kv, broker = _Graph(), _KV(), _Broker()
        graph.trash("a", days_ago=20)
        graph.trash("young", days_ago=1)
        broker.accept = False

        assert await _purger(graph, kv, broker).tick() == Outcome.PAUSED
        [entry] = kv.outbox().values()
        assert list(entry["items"]) == ["a"]
        assert entry["items"]["a"]["documentIds"] == [DOC_A]

        broker.accept = True
        assert await _purger(graph, kv, broker).tick() == Outcome.FINISHED
        assert broker.deleted() == ["a"]
        assert kv.outbox() == {}

    async def test_a_record_the_graph_refuses_is_counted_and_the_rest_go(self, flag) -> None:
        graph, kv = _Graph(), _KV()
        graph.trash("a", days_ago=20)
        graph.trash("bad", days_ago=19)
        graph.refuse = {"bad"}

        assert await _purger(graph, kv).tick() == Outcome.FINISHED

        assert set(graph.records) == {"bad"}
        assert graph.records["bad"]["purgeAttempts"] == 1
        assert graph.records["bad"]["purgeLastError"] == "RuntimeError: graph refused the write"
        assert kv.values[STATE_KEY]["lastCounts"] == {"purged": 1, "kept": 0, "failed": 1, "groups": 0}

    async def test_a_failure_that_cannot_be_counted_stops_the_tick(self, flag) -> None:
        graph, kv = _Graph(), _KV()
        graph.trash("bad", days_ago=20)
        graph.refuse = {"bad"}
        graph.refuse_counting = True

        with pytest.raises(TrashPurgeError):
            await _purger(graph, kv).tick()
        assert "bad" in graph.records
        assert kv.values[STATE_KEY]["status"] == "running"

        # The record is still there, so the saved page owes nothing.
        graph.refuse, graph.refuse_counting = set(), False
        broker = _Broker()
        assert await _purger(graph, kv, broker).tick() == Outcome.FINISHED
        assert broker.deleted() == ["bad"]
        assert kv.outbox() == {}

    async def test_a_malformed_outbox_entry_is_dropped(self, flag) -> None:
        graph, kv = _Graph(), _KV()
        kv.values[f"{OUTBOX_DIRECTORY}junk"] = "not a page"
        assert await _purger(graph, kv).tick() == Outcome.FINISHED
        assert kv.outbox() == {}


class TestBookkeeping:
    async def test_the_backlog_gauges_count_pending_and_stuck(self, flag) -> None:
        graph, kv = _Graph(), _KV()
        graph.trash("young", days_ago=3)
        graph.trash("stuck", days_ago=40, purgeAttempts=5)
        with patch.object(purge_module, "set_trash_backlog") as gauges:
            assert await _purger(graph, kv).tick() == Outcome.FINISHED
        gauges.assert_called_once_with(1, 1, pytest.approx(3 * 24 * 3600, rel=1e-6))

    async def test_a_finished_run_records_when_it_started(self, flag) -> None:
        graph, kv = _Graph(), _KV()
        assert await _purger(graph, kv).tick() == Outcome.FINISHED
        state = kv.values[STATE_KEY]
        assert state["status"] == "idle" and state["run"] is None
        assert state["lastStartedAt"] == NOW
        assert await _purger(graph, kv, now=NOW + DAY_MS).tick() == Outcome.NOT_DUE


class TestStoredDocuments:
    def test_an_upload_a_local_fs_copy_and_a_web_copy(self) -> None:
        assert stored_document_ids({"uploadDocumentId": DOC_A}) == [DOC_A]
        assert stored_document_ids({"filePath": f"storage://{DOC_B}"}) == [DOC_B]
        assert stored_document_ids({"storageDocumentId": DOC_B}) == [DOC_B]

    def test_only_storage_document_ids_once_each(self) -> None:
        row = {"uploadDocumentId": DOC_A, "filePath": "/docs/a.pdf", "storageDocumentId": DOC_A}
        assert stored_document_ids(row) == [DOC_A]
        assert stored_document_ids({"filePath": "storage://not-an-id", "storageDocumentId": ""}) == []


class TestLoop:
    async def test_a_failed_tick_is_counted_and_retried_later(self, monkeypatch: pytest.MonkeyPatch) -> None:
        container = MagicMock()
        container.messaging_producer = _Broker()
        sleeps: list[float] = []

        async def sleep(seconds: float) -> None:
            sleeps.append(seconds)
            if len(sleeps) > 2:
                raise asyncio.CancelledError

        tick = AsyncMock(side_effect=[RuntimeError("graph down"), Outcome.FINISHED])
        monkeypatch.setattr(purge_module.MessagingUtils, "_get_redis_config", AsyncMock(return_value=MagicMock()))
        monkeypatch.setattr(purge_module.TrashPurger, "tick", tick)
        monkeypatch.setenv("SOFT_DELETE_PURGE_TICK_SECONDS", "60")
        monkeypatch.setenv("SOFT_DELETE_PURGE_STARTUP_GRACE_SECONDS", "0")
        runs = MagicMock()
        monkeypatch.setattr(purge_module, "record_purge_run", runs)

        with pytest.raises(asyncio.CancelledError):
            await purge_module.run_trash_purge_loop(container, _Graph(), sleep=sleep)

        assert tick.await_count == 2
        assert sleeps == [0.0, 120.0, 60.0], "backs off after a failure, then back to the hour"
        runs.assert_called_once_with("failed")
