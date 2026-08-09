"""Run history has to survive Redis. These pin the two halves of that: a
terminal run reaches the archive before anything expires it, and a run that
has aged out of Redis is still readable.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

import pytest
from fakeredis import aioredis as fake_aioredis

from app.services.tasks.adapters.archiving_run_store import ArchivingRunStore
from app.services.tasks.adapters.redis import keys as k
from app.services.tasks.adapters.redis.run_store import RedisRunStore
from app.services.tasks.domain.models import (
    Page,
    RunStatus,
    TaskRun,
    compute_idempotency_key,
)
from app.services.tasks.interface.run_archive import IRunArchive

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


class _InMemoryArchive(IRunArchive):
    def __init__(self, *, broken: bool = False) -> None:
        self.runs: dict[str, TaskRun] = {}
        self.archive_calls = 0
        self._broken = broken

    async def archive(self, run: TaskRun) -> None:
        self.archive_calls += 1
        if self._broken:
            raise ConnectionError("graph unreachable")
        self.runs[run.run_id] = run

    async def get(self, run_id: str) -> TaskRun | None:
        return self.runs.get(run_id)

    async def list_for_task(
        self, task_id: str, *, limit: int = 50, offset: int = 0,
    ) -> Page[TaskRun]:
        matching = sorted(
            (run for run in self.runs.values() if run.task_id == task_id),
            key=lambda run: run.created_at,
            reverse=True,
        )
        return Page(
            items=matching[offset:offset + limit],
            total=len(matching),
            limit=limit,
            offset=offset,
        )


def _run(*, status: RunStatus = RunStatus.PENDING, minutes_ago: int = 0, task_id: str = "task-1") -> TaskRun:
    created = (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)).isoformat()
    return TaskRun(
        task_id=task_id,
        org_id="org-1",
        idempotency_key=compute_idempotency_key(task_id, created),
        scheduled_for=created,
        created_at=created,
        status=status,
    )


@pytest.fixture
async def redis_client() -> AsyncIterator[fake_aioredis.FakeRedis]:
    client = fake_aioredis.FakeRedis(decode_responses=True)
    yield client
    await client.aclose()


@pytest.fixture
async def store_and_archive(
    redis_client: fake_aioredis.FakeRedis,
) -> AsyncIterator[tuple[ArchivingRunStore, _InMemoryArchive]]:
    archive = _InMemoryArchive()
    yield ArchivingRunStore(RedisRunStore(redis_client), archive), archive


class TestArchivingOnTerminalStates:
    @pytest.mark.parametrize("status", [
        RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.DLQ, RunStatus.CANCELLED,
    ])
    async def test_a_terminal_update_is_archived(self, store_and_archive, status) -> None:
        store, archive = store_and_archive
        run = await store.create_if_absent(_run())

        await store.update(run.model_copy(update={"status": status}))

        assert archive.runs[run.run_id].status == status

    async def test_a_non_terminal_update_is_not_archived(self, store_and_archive) -> None:
        store, archive = store_and_archive
        run = await store.create_if_absent(_run())

        await store.update(run.model_copy(update={"status": RunStatus.RUNNING}))

        assert archive.runs == {}

    async def test_an_abandoned_run_is_archived_by_the_reaper(
        self, store_and_archive,
    ) -> None:
        """ABANDONED is reached by `reap_abandoned`, not `update`, so it is
        the one terminal state an update-only hook would miss."""
        store, archive = store_and_archive
        run = await store.create_if_absent(_run())
        await store.claim_for_execution(run.run_id, owner="worker-1", lease_seconds=0.001)

        reaped = await store.reap_abandoned(now=datetime.now(timezone.utc) + timedelta(hours=1))

        assert [r.run_id for r in reaped] == [run.run_id]
        assert archive.runs[run.run_id].status == RunStatus.ABANDONED


class TestRedisCleanup:
    async def test_a_terminal_run_gets_a_ttl(self, redis_client) -> None:
        store = ArchivingRunStore(RedisRunStore(redis_client), _InMemoryArchive())
        run = await store.create_if_absent(_run())
        assert await redis_client.ttl(k.run_hash_key(run.run_id)) == -1

        await store.update(run.model_copy(update={"status": RunStatus.SUCCEEDED}))

        assert await redis_client.ttl(k.run_hash_key(run.run_id)) > 0

    async def test_the_idempotency_key_expires_with_its_run(self, redis_client) -> None:
        """It exists to collapse duplicate dispatches of one fire; keeping it
        forever leaks one key per run."""
        store = ArchivingRunStore(RedisRunStore(redis_client), _InMemoryArchive())
        run = await store.create_if_absent(_run())

        await store.update(run.model_copy(update={"status": RunStatus.SUCCEEDED}))

        assert await redis_client.ttl(k.run_idempotency_key(run.idempotency_key)) > 0

    async def test_the_per_task_index_is_trimmed(self, redis_client) -> None:
        inner = RedisRunStore(redis_client, max_runs_per_task=3)
        store = ArchivingRunStore(inner, _InMemoryArchive())
        for i in range(6):
            run = await store.create_if_absent(_run(minutes_ago=10 - i))
            await store.update(run.model_copy(update={"status": RunStatus.SUCCEEDED}))

        assert await redis_client.zcard(k.runs_by_task_zset_key("task-1")) == 3

    async def test_a_failed_archive_leaves_the_run_in_redis(self, redis_client) -> None:
        """Expiring a run we could not archive would lose it outright."""
        store = ArchivingRunStore(RedisRunStore(redis_client), _InMemoryArchive(broken=True))
        run = await store.create_if_absent(_run())

        await store.update(run.model_copy(update={"status": RunStatus.SUCCEEDED}))

        assert await redis_client.ttl(k.run_hash_key(run.run_id)) == -1
        assert await store.get(run.run_id) is not None


class TestReadFallback:
    async def test_a_run_evicted_from_redis_is_still_readable(
        self, redis_client, store_and_archive,
    ) -> None:
        store, _archive = store_and_archive
        run = await store.create_if_absent(_run())
        await store.update(run.model_copy(update={"status": RunStatus.SUCCEEDED}))
        await redis_client.delete(k.run_hash_key(run.run_id))

        found = await store.get(run.run_id)

        assert found is not None
        assert found.status == RunStatus.SUCCEEDED

    async def test_an_unknown_run_is_still_none(self, store_and_archive) -> None:
        store, _archive = store_and_archive
        assert await store.get("no-such-run") is None

    async def test_history_survives_the_redis_index_being_trimmed(
        self, redis_client,
    ) -> None:
        inner = RedisRunStore(redis_client, max_runs_per_task=2)
        archive = _InMemoryArchive()
        store = ArchivingRunStore(inner, archive)
        for i in range(5):
            run = await store.create_if_absent(_run(minutes_ago=10 - i))
            await store.update(run.model_copy(update={"status": RunStatus.SUCCEEDED}))

        page = await store.list_for_task("task-1", limit=10)

        assert page.total == 5
        assert len(page.items) == 5

    async def test_an_in_flight_run_appears_before_it_is_archived(
        self, store_and_archive,
    ) -> None:
        store, _archive = store_and_archive
        done = await store.create_if_absent(_run(minutes_ago=5))
        await store.update(done.model_copy(update={"status": RunStatus.SUCCEEDED}))
        live = await store.create_if_absent(_run(minutes_ago=0))

        page = await store.list_for_task("task-1", limit=10)

        assert [item.run_id for item in page.items] == [live.run_id, done.run_id]
        assert page.total == 2

    async def test_a_run_is_not_listed_twice_while_it_is_in_both(
        self, store_and_archive,
    ) -> None:
        store, _archive = store_and_archive
        run = await store.create_if_absent(_run())
        await store.update(run.model_copy(update={"status": RunStatus.SUCCEEDED}))

        page = await store.list_for_task("task-1", limit=10)

        assert [item.run_id for item in page.items] == [run.run_id]
        assert page.total == 1

    async def test_other_tasks_runs_are_not_returned(self, store_and_archive) -> None:
        store, _archive = store_and_archive
        mine = await store.create_if_absent(_run(task_id="task-1"))
        await store.update(mine.model_copy(update={"status": RunStatus.SUCCEEDED}))
        theirs = await store.create_if_absent(_run(task_id="task-2"))
        await store.update(theirs.model_copy(update={"status": RunStatus.SUCCEEDED}))

        page = await store.list_for_task("task-1", limit=10)

        assert [item.run_id for item in page.items] == [mine.run_id]


class TestLiveOperationsAreUntouched:
    async def test_claim_resume_and_heartbeat_still_work(self, store_and_archive) -> None:
        """The decorator must not get between the executor and the Lua
        scripts that give claims their atomicity."""
        store, _archive = store_and_archive
        run = await store.create_if_absent(_run())

        claimed = await store.claim_for_execution(run.run_id, owner="w1", lease_seconds=60)
        assert claimed is not None
        assert claimed.status == RunStatus.RUNNING
        assert await store.heartbeat(run.run_id, "w1", 60) is True
        assert await store.claim_for_execution(run.run_id, owner="w2", lease_seconds=60) is None

    async def test_duplicate_creates_are_still_refused(self, store_and_archive) -> None:
        store, _archive = store_and_archive
        run = _run()
        assert await store.create_if_absent(run) is not None
        assert await store.create_if_absent(run) is None
