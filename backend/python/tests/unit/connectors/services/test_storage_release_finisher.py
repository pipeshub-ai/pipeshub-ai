"""The storage-release finisher completes a knowledge-base delete that is stuck DELETING."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.connectors.services.storage_release import (
    GRACE_MS,
    StorageReleaseReconciler,
    list_pending_storage_releases,
    record_pending_storage_release,
    retry_delay_ms,
)
from app.core.org_context import ambient_org, current_org_id

NOW = 10 * 24 * 60 * 60 * 1000
ORG, KB = "org1", "kb1"


class Config:
    def __init__(self) -> None:
        self.kv: dict[str, object] = {}

    async def get_config(self, key, default=None, use_cache=True, **_kw) -> object:
        return self.kv.get(key, default)

    async def set_config(self, key, value, **_kw) -> bool:
        self.kv[key] = value
        return True

    async def delete_config(self, key) -> bool:
        return self.kv.pop(key, None) is not None

    async def list_keys_in_directory(self, directory) -> list[str]:
        return [k for k in self.kv if k.startswith(directory)]


class Graph:
    def __init__(self, app: dict | None) -> None:
        self.app = app

    async def get_document(self, key, collection, **_kw) -> object:
        return self.app


async def reconciler(app: dict | None, *, age_ms: int, finish=None, lease=None, heartbeat=0.01, attempts=0, leader=True) -> tuple[StorageReleaseReconciler, Config, SimpleNamespace]:
    config = Config()
    await record_pending_storage_release(config, org_id=ORG, connector_id=KB, now_ms=NOW - age_ms)
    if attempts:
        key = f"/services/storageRelease/pending/{KB}"
        config.kv[key] = {**config.kv[key], "attempts": attempts}
    lock = SimpleNamespace(try_acquire=AsyncMock(return_value=leader), refresh=AsyncMock(return_value=True))
    return StorageReleaseReconciler(
        logger=MagicMock(), graph_provider=Graph(app), config_service=config, lock=lock, now_ms=lambda: NOW,
        finish_kb_delete=finish, lease_held=lease, heartbeat_seconds=heartbeat,
    ), config, lock


DELETING_KB = {"_key": KB, "type": "KB", "status": "DELETING"}


@pytest.mark.asyncio
async def test_a_stuck_kb_is_finished_after_the_grace_period() -> None:
    finish = AsyncMock(return_value={"success": True})
    r, config, _ = await reconciler(DELETING_KB, age_ms=GRACE_MS + 1, finish=finish)

    assert await r.tick() == "deleted"

    finish.assert_awaited_once_with(ORG, KB)
    # The storage release is still owed: the next pass finds the KB gone and runs it.
    assert [i["connectorId"] for i in await list_pending_storage_releases(config)] == [KB]


@pytest.mark.asyncio
async def test_with_the_finisher_off_a_stuck_kb_stays_stuck() -> None:
    """Negative control: no finisher wired, as before this change."""
    r, config, _ = await reconciler(DELETING_KB, age_ms=GRACE_MS + 1, finish=None)

    assert await r.tick() == "idle"


@pytest.mark.asyncio
async def test_within_the_grace_period_the_owner_is_left_to_finish() -> None:
    finish = AsyncMock(return_value={"success": True})
    r, _, _ = await reconciler(DELETING_KB, age_ms=GRACE_MS - 60_000, finish=finish)

    assert await r.tick() == "idle"
    finish.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_held_lease_is_a_hint_to_wait() -> None:
    finish = AsyncMock(return_value={"success": True})
    r, _, _ = await reconciler(DELETING_KB, age_ms=GRACE_MS + 1, finish=finish, lease=AsyncMock(return_value=True))

    assert await r.tick() == "idle"
    finish.assert_not_awaited()


@pytest.mark.asyncio
async def test_the_finish_runs_bound_to_the_intents_org(monkeypatch) -> None:
    """The loop runs with no ambient org; the org-checked graph calls of the delete fail closed without one."""
    monkeypatch.setenv("ORG_CONTEXT_MODE", "enforce")
    seen: list[str | None] = []

    async def finish(org_id: str, kb_id: str) -> dict:
        seen.append(ambient_org(org_id, site="delete_connector_instance"))
        seen.append(current_org_id())
        return {"success": True}

    r, _, _ = await reconciler(DELETING_KB, age_ms=GRACE_MS + 1, finish=finish)

    assert await r.tick() == "deleted"
    assert seen == [ORG, ORG]


@pytest.mark.asyncio
async def test_a_failed_finish_is_rescheduled_with_backoff() -> None:
    finish = AsyncMock(return_value={"success": False, "code": 500})
    r, config, _ = await reconciler(DELETING_KB, age_ms=GRACE_MS + 1, finish=finish)

    assert await r.tick() == "retrying"

    (intent,) = await list_pending_storage_releases(config)
    assert intent["attempts"] == 1 and intent["nextAttemptAt"] == NOW + retry_delay_ms(0)
    assert await r.tick() == "idle"  # not due yet


@pytest.mark.asyncio
async def test_a_finish_that_raises_is_a_failed_attempt_not_a_crash() -> None:
    finish = AsyncMock(side_effect=RuntimeError("boom"))
    r, config, _ = await reconciler(DELETING_KB, age_ms=GRACE_MS + 1, finish=finish)

    assert await r.tick() == "retrying"
    assert (await list_pending_storage_releases(config))[0]["attempts"] == 1


@pytest.mark.asyncio
async def test_only_the_leader_finishes() -> None:
    finish = AsyncMock(return_value={"success": True})
    r, _, _ = await reconciler(DELETING_KB, age_ms=GRACE_MS + 1, finish=finish, leader=False)

    assert await r.tick() == "not_leader"
    finish.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_connector_being_deleted_is_not_a_kb_and_is_left_to_its_own_delete() -> None:
    finish = AsyncMock(return_value={"success": True})
    r, _, _ = await reconciler({"_key": KB, "type": "JIRA", "status": "DELETING"}, age_ms=GRACE_MS + 1, finish=finish)

    assert await r.tick() == "idle"
    finish.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_live_kb_is_never_finished() -> None:
    finish = AsyncMock(return_value={"success": True})
    r, _, _ = await reconciler({"_key": KB, "type": "KB"}, age_ms=GRACE_MS + 1, finish=finish)

    assert await r.tick() == "idle"
    finish.assert_not_awaited()


@pytest.mark.asyncio
async def test_the_leader_lock_is_renewed_while_the_delete_runs() -> None:
    """R6-6: a long delete must not let a second replica start the same one."""
    async def slow(org_id: str, kb_id: str) -> dict:
        await asyncio.sleep(0.12)
        return {"success": True}

    r, _, lock = await reconciler(DELETING_KB, age_ms=GRACE_MS + 1, finish=slow, heartbeat=0.02)

    assert await r.tick() == "deleted"
    assert lock.refresh.await_count >= 3


@pytest.mark.asyncio
async def test_the_heartbeat_stops_with_the_delete() -> None:
    finish = AsyncMock(return_value={"success": True})
    r, _, lock = await reconciler(DELETING_KB, age_ms=GRACE_MS + 1, finish=finish, heartbeat=0.01)

    await r.tick()
    seen = lock.refresh.await_count
    await asyncio.sleep(0.05)
    assert lock.refresh.await_count == seen


@pytest.mark.asyncio
async def test_a_retry_after_a_failed_attempt_is_due_without_the_grace_wait() -> None:
    finish = AsyncMock(return_value={"success": True})
    r, _, _ = await reconciler(DELETING_KB, age_ms=60_000, finish=finish, attempts=1)

    assert await r.tick() == "deleted"
