"""Unit tests for vector-store cleanup and reindex jobs."""

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import EventTypes, ProgressStatus
from app.services.vector_db.rebuild_state import (
    CLEANUP_PHASE_KEY,
    JOB_LOCK_KEY,
    PHASE_DROPPING,
    PHASE_READY,
    RebuildJobLock,
    get_cleanup_phase,
    set_cleanup_phase,
)


class FakeRedis:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.expiry: dict[str, float] = {}

    def _purge_expired(self, key: str) -> None:
        deadline = self.expiry.get(key)
        if deadline is not None and time.monotonic() >= deadline:
            self.store.pop(key, None)
            self.expiry.pop(key, None)

    async def set(self, key, value, nx=False, xx=False, ex=None, get=False):
        self._purge_expired(key)
        previous = self.store.get(key)
        exists = key in self.store
        if nx and exists:
            return previous if get else None
        if xx and not exists:
            return previous if get else None
        self.store[key] = value
        if ex is not None:
            self.expiry[key] = time.monotonic() + int(ex)
        else:
            self.expiry.pop(key, None)
        return previous if get else True

    async def get(self, key):
        self._purge_expired(key)
        return self.store.get(key)

    async def eval(self, script, _numkeys, *args):
        key = args[0]
        token = str(args[1])
        self._purge_expired(key)
        if self.store.get(key) != token:
            return 0
        if "expire" in script:
            self.expiry[key] = time.monotonic() + int(args[2])
            return 1
        del self.store[key]
        self.expiry.pop(key, None)
        return 1

    async def aclose(self):
        return None


@pytest.mark.asyncio
async def test_rebuild_lock_is_single_flight():
    redis = FakeRedis()
    first = RebuildJobLock(redis, token="a")
    second = RebuildJobLock(redis, token="b")
    assert await first.try_acquire() is True
    assert await second.try_acquire() is False
    await first.release()
    assert JOB_LOCK_KEY not in redis.store
    assert await second.try_acquire() is True


@pytest.mark.asyncio
async def test_rebuild_lock_refresh_only_extends_owned_lease():
    redis = FakeRedis()
    owner = RebuildJobLock(redis, token="a", ttl_seconds=30)
    other = RebuildJobLock(redis, token="b", ttl_seconds=30)

    assert await owner.refresh() is False
    assert JOB_LOCK_KEY not in redis.store

    assert await owner.try_acquire() is True
    redis.expiry[JOB_LOCK_KEY] = time.monotonic() + 1
    assert await owner.refresh() is True
    assert redis.store[JOB_LOCK_KEY] == "a"
    assert redis.expiry[JOB_LOCK_KEY] > time.monotonic() + 10

    token_before = redis.store[JOB_LOCK_KEY]
    expiry_before = redis.expiry[JOB_LOCK_KEY]
    assert await other.refresh() is False
    assert redis.store[JOB_LOCK_KEY] == token_before
    assert redis.expiry[JOB_LOCK_KEY] == expiry_before

    redis.expiry[JOB_LOCK_KEY] = time.monotonic() - 1
    assert await owner.refresh() is False
    assert JOB_LOCK_KEY not in redis.store


@pytest.mark.asyncio
async def test_cleanup_phase_roundtrip():
    redis = FakeRedis()
    assert await get_cleanup_phase(redis) is None
    await set_cleanup_phase(redis, PHASE_DROPPING)
    assert await get_cleanup_phase(redis) == PHASE_DROPPING
    assert redis.store[CLEANUP_PHASE_KEY] == PHASE_DROPPING


@pytest.mark.asyncio
async def test_list_rebuild_apps_skips_deleting():
    from app.connectors.services.vector_store_rebuild import list_rebuild_apps

    graph = AsyncMock()
    graph.get_all_orgs = AsyncMock(return_value=[{"_key": "org-1"}])
    graph.get_org_apps = AsyncMock(
        return_value=[
            {"_key": "app-live", "status": "ACTIVE"},
            {"_key": "app-dead", "status": "DELETING"},
            {"id": "app-no-status"},
        ]
    )

    pairs = await list_rebuild_apps(graph)
    assert pairs == [("org-1", "app-live"), ("org-1", "app-no-status")]
    graph.get_all_orgs.assert_awaited_once_with(active=False)
    graph.get_org_apps.assert_awaited_once_with("org-1", active_only=False)


@pytest.mark.asyncio
async def test_cleanup_publishes_delete_event_only():
    from app.connectors.services.vector_store_rebuild import start_vector_store_cleanup

    redis = FakeRedis()
    lock = RebuildJobLock(redis, token="cleanup")
    await lock.try_acquire()

    graph = AsyncMock()
    graph.get_all_orgs = AsyncMock(return_value=[{"_key": "org-1"}])
    graph.get_org_apps = AsyncMock(return_value=[{"_key": "app-1"}])
    graph.reset_indexing_status_for_connector = AsyncMock()
    # Nothing queued: the job re-checks for in-flight indexing before dropping.
    graph.get_records_by_status = AsyncMock(return_value=[])

    kafka = AsyncMock()

    async def _publish(topic, event):
        redis.store[CLEANUP_PHASE_KEY] = PHASE_READY
        return True

    kafka.publish_event = AsyncMock(side_effect=_publish)

    await start_vector_store_cleanup(
        logger=MagicMock(),
        graph_provider=graph,
        kafka_service=kafka,
        lock=lock,
        redis=redis,
        org_id="org-1",
        user_id="user-1",
        apps=[("org-1", "app-1")],
    )

    event = kafka.publish_event.await_args.args[1]
    assert event["eventType"] == EventTypes.DELETE_VECTOR_COLLECTION.value
    graph.reset_indexing_status_for_connector.assert_awaited_once_with(
        "app-1",
        ProgressStatus.NOT_STARTED.value,
        exclude_statuses=[ProgressStatus.IN_PROGRESS.value],
    )
    assert JOB_LOCK_KEY not in redis.store


@pytest.mark.asyncio
async def test_cleanup_timeout_does_not_page_records():
    from app.connectors.services import vector_store_rebuild as rebuild

    redis = FakeRedis()
    lock = RebuildJobLock(redis, token="cleanup")
    await lock.try_acquire()
    graph = AsyncMock()
    graph.get_all_orgs = AsyncMock(return_value=[])
    graph.get_records_by_status = AsyncMock(return_value=[])
    kafka = AsyncMock()
    kafka.publish_event = AsyncMock(return_value=True)

    with patch.object(rebuild, "_delete_wait_seconds", return_value=0):
        with pytest.raises(TimeoutError):
            await rebuild.start_vector_store_cleanup(
                logger=MagicMock(),
                graph_provider=graph,
                kafka_service=kafka,
                lock=lock,
                redis=redis,
                apps=[],
                org_id="org-1",
                user_id="user-1",
            )

    graph.get_records_by_status.assert_not_awaited()


@pytest.mark.asyncio
async def test_reindex_pages_and_sets_vector_db_only():
    from app.connectors.services import vector_store_rebuild as rebuild

    redis = FakeRedis()
    lock = RebuildJobLock(redis, token="reindex")
    await lock.try_acquire()

    rec1 = MagicMock(id="r1", is_placeholder=False)
    rec2 = MagicMock(id="r2", is_placeholder=False)
    graph = AsyncMock()
    graph.get_all_orgs = AsyncMock(return_value=[{"_key": "org-1"}])
    graph.get_org_apps = AsyncMock(return_value=[{"_key": "app-1"}])
    graph.reset_indexing_status_for_connector = AsyncMock()
    graph.get_records_by_status = AsyncMock(side_effect=[[rec1, rec2], []])

    processor = AsyncMock()
    processor.initialize = AsyncMock()
    processor.reindex_existing_records = AsyncMock()

    with (
        patch.object(rebuild, "DataSourceEntitiesProcessor", return_value=processor),
        patch.object(rebuild, "_page_size", return_value=2),
    ):
        await rebuild.start_vector_store_reindex(
            logger=MagicMock(),
            graph_provider=graph,
            data_store_provider=MagicMock(),
            config_service=MagicMock(),
            lock=lock,
            redis=redis,
            apps=[("org-1", "app-1")],
        )

    processor.reindex_existing_records.assert_awaited_once_with(
        [rec1, rec2], vector_db_only=True
    )
    assert JOB_LOCK_KEY not in redis.store


@pytest.mark.asyncio
async def test_deleting_apps_are_never_listed_for_rebuild():
    """DELETING apps are filtered where the list is built.

    Both jobs now act on the list the API gated on, so the skip has to hold in
    list_rebuild_apps — a job handed a DELETING app would happily process it.
    """
    from app.connectors.services import vector_store_rebuild as rebuild

    graph = AsyncMock()
    graph.get_all_orgs = AsyncMock(return_value=[{"_key": "org-1"}])
    graph.get_org_apps = AsyncMock(
        return_value=[
            {"_key": "gone", "status": "DELETING"},
            {"_key": "live", "status": "ACTIVE"},
        ]
    )

    apps = await rebuild.list_rebuild_apps(graph)

    assert apps == [("org-1", "live")]


@pytest.mark.asyncio
async def test_reindex_processes_only_the_apps_it_was_given():
    from app.connectors.services import vector_store_rebuild as rebuild

    redis = FakeRedis()
    lock = RebuildJobLock(redis, token="reindex")
    await lock.try_acquire()
    graph = AsyncMock()
    graph.reset_indexing_status_for_connector = AsyncMock()
    graph.get_records_by_status = AsyncMock(return_value=[])
    processor = AsyncMock()
    processor.initialize = AsyncMock()

    with patch.object(rebuild, "DataSourceEntitiesProcessor", return_value=processor):
        await rebuild.start_vector_store_reindex(
            logger=MagicMock(),
            graph_provider=graph,
            data_store_provider=MagicMock(),
            config_service=MagicMock(),
            lock=lock,
            redis=redis,
            apps=[],
        )

    graph.reset_indexing_status_for_connector.assert_not_awaited()
    graph.get_records_by_status.assert_not_awaited()
    processor.reindex_existing_records.assert_not_awaited()


@pytest.mark.asyncio
async def test_accept_cleanup_requires_admin():
    from fastapi import HTTPException

    from app.connectors.api.router import _accept_vector_store_job

    request = MagicMock()
    request.state.user = {"userId": "u", "orgId": "o", "role": "member"}
    with patch("app.connectors.api.router.is_request_admin", return_value=False):
        with pytest.raises(HTTPException) as exc:
            await _accept_vector_store_job(
                request, operation="cleanup", kafka_service=MagicMock()
            )
    assert exc.value.status_code == 403


def _admin_rebuild_request():
    request = MagicMock()
    request.state.user = {"userId": "u", "orgId": "o", "role": "admin"}
    container = MagicMock()
    container.logger.return_value = MagicMock()
    container.config_service.return_value = MagicMock()
    container.data_store = AsyncMock(return_value=MagicMock())
    request.app.container = container
    request.app.state.graph_provider = MagicMock()
    return request


async def _close_coro_and_accept(coro):
    if hasattr(coro, "close"):
        coro.close()
    return True


@pytest.mark.asyncio
async def test_accept_conflicts_when_lock_held():
    from fastapi import HTTPException

    from app.connectors.api.router import _accept_vector_store_job
    from app.connectors.services.vector_store_rebuild import VectorStoreRebuildBusyError

    with (
        patch("app.connectors.api.router.is_request_admin", return_value=True),
        # These tests cover accept/409 semantics; the in-flight gate has its own.
        patch(
            "app.connectors.api.router.list_rebuild_apps",
            new_callable=AsyncMock,
            return_value=[],
        ),
        patch(
            "app.connectors.api.router.assert_no_indexing_in_flight",
            new_callable=AsyncMock,
        ),
        patch(
            "app.connectors.api.router.acquire_rebuild_lock",
            side_effect=VectorStoreRebuildBusyError("busy"),
        ),
    ):
        with pytest.raises(HTTPException) as exc:
            await _accept_vector_store_job(
                _admin_rebuild_request(),
                operation="cleanup",
                kafka_service=MagicMock(),
            )
    assert exc.value.status_code == 409


@pytest.mark.asyncio
async def test_reindex_conflicts_while_cleanup_dropping():
    from fastapi import HTTPException

    from app.connectors.api.router import _accept_vector_store_job
    from app.services.vector_db.rebuild_state import PHASE_DROPPING

    lock = AsyncMock()
    redis = AsyncMock()
    with (
        patch("app.connectors.api.router.is_request_admin", return_value=True),
        # These tests cover accept/409 semantics; the in-flight gate has its own.
        patch(
            "app.connectors.api.router.list_rebuild_apps",
            new_callable=AsyncMock,
            return_value=[],
        ),
        patch(
            "app.connectors.api.router.assert_no_indexing_in_flight",
            new_callable=AsyncMock,
        ),
        patch(
            "app.connectors.api.router.acquire_rebuild_lock",
            new_callable=AsyncMock,
            return_value=(lock, redis),
        ),
        patch(
            "app.connectors.api.router.get_cleanup_phase",
            new_callable=AsyncMock,
            return_value=PHASE_DROPPING,
        ),
        patch(
            "app.connectors.api.router.release_rebuild_lock",
            new_callable=AsyncMock,
        ) as release,
    ):
        with pytest.raises(HTTPException) as exc:
            await _accept_vector_store_job(
                _admin_rebuild_request(),
                operation="reindex",
                kafka_service=MagicMock(),
            )
    assert exc.value.status_code == 409
    release.assert_awaited_once()


@pytest.mark.asyncio
async def test_accept_cleanup_returns_accepted_payload():
    from app.connectors.api.router import _accept_vector_store_job

    with (
        patch("app.connectors.api.router.is_request_admin", return_value=True),
        # These tests cover accept/409 semantics; the in-flight gate has its own.
        patch(
            "app.connectors.api.router.list_rebuild_apps",
            new_callable=AsyncMock,
            return_value=[],
        ),
        patch(
            "app.connectors.api.router.assert_no_indexing_in_flight",
            new_callable=AsyncMock,
        ),
        patch(
            "app.connectors.api.router.acquire_rebuild_lock",
            new_callable=AsyncMock,
            return_value=(AsyncMock(), AsyncMock()),
        ),
        patch(
            "app.connectors.api.router.schedule_vector_store_job_async",
            new_callable=AsyncMock,
            side_effect=_close_coro_and_accept,
        ),
    ):
        result = await _accept_vector_store_job(
            _admin_rebuild_request(),
            operation="cleanup",
            kafka_service=MagicMock(),
        )
    assert result == {"accepted": True, "operation": "cleanup"}


@pytest.mark.asyncio
async def test_accept_reindex_returns_accepted_payload():
    from app.connectors.api.router import _accept_vector_store_job

    with (
        patch("app.connectors.api.router.is_request_admin", return_value=True),
        # These tests cover accept/409 semantics; the in-flight gate has its own.
        patch(
            "app.connectors.api.router.list_rebuild_apps",
            new_callable=AsyncMock,
            return_value=[],
        ),
        patch(
            "app.connectors.api.router.assert_no_indexing_in_flight",
            new_callable=AsyncMock,
        ),
        patch(
            "app.connectors.api.router.acquire_rebuild_lock",
            new_callable=AsyncMock,
            return_value=(AsyncMock(), AsyncMock()),
        ),
        patch(
            "app.connectors.api.router.get_cleanup_phase",
            new_callable=AsyncMock,
            return_value=PHASE_READY,
        ),
        patch(
            "app.connectors.api.router.schedule_vector_store_job_async",
            new_callable=AsyncMock,
            side_effect=_close_coro_and_accept,
        ),
    ):
        result = await _accept_vector_store_job(
            _admin_rebuild_request(),
            operation="reindex",
            kafka_service=MagicMock(),
        )
    assert result == {"accepted": True, "operation": "reindex"}


@pytest.mark.asyncio
async def test_accept_conflicts_when_in_process_task_running():
    from fastapi import HTTPException

    from app.connectors.api.router import _accept_vector_store_job

    release = AsyncMock()
    with (
        patch("app.connectors.api.router.is_request_admin", return_value=True),
        # These tests cover accept/409 semantics; the in-flight gate has its own.
        patch(
            "app.connectors.api.router.list_rebuild_apps",
            new_callable=AsyncMock,
            return_value=[],
        ),
        patch(
            "app.connectors.api.router.assert_no_indexing_in_flight",
            new_callable=AsyncMock,
        ),
        patch(
            "app.connectors.api.router.acquire_rebuild_lock",
            new_callable=AsyncMock,
            return_value=(AsyncMock(), AsyncMock()),
        ),
        patch(
            "app.connectors.api.router.schedule_vector_store_job_async",
            new_callable=AsyncMock,
            side_effect=lambda coro: coro.close() or False,
        ),
        patch(
            "app.connectors.api.router.release_rebuild_lock",
            new_callable=AsyncMock,
            side_effect=release,
        ),
    ):
        with pytest.raises(HTTPException) as exc:
            await _accept_vector_store_job(
                _admin_rebuild_request(),
                operation="cleanup",
                kafka_service=MagicMock(),
            )
    assert exc.value.status_code == 409
    release.assert_awaited_once()


# ── In-flight indexing gate ──


@pytest.mark.asyncio
async def test_gate_refuses_when_records_are_queued():
    """A rebuild during live indexing corrupts silently.

    Cleanup wipes points a concurrent run just wrote, and that record stays
    COMPLETED because the status reset deliberately skips IN_PROGRESS — so
    nothing ever re-indexes it.
    """
    from app.connectors.services import vector_store_rebuild as rebuild

    graph = AsyncMock()
    graph.get_records_by_status = AsyncMock(return_value=[MagicMock()])

    with patch.object(rebuild.sync_task_manager, "active_keys", return_value=[]):
        with pytest.raises(rebuild.VectorStoreRebuildConflictError, match="queued"):
            await rebuild.assert_no_indexing_in_flight(graph, [("org-1", "app-1")])


@pytest.mark.asyncio
async def test_gate_refuses_while_a_connector_sync_runs():
    from app.connectors.services import vector_store_rebuild as rebuild

    graph = AsyncMock()
    graph.get_records_by_status = AsyncMock(return_value=[])

    with patch.object(
        rebuild.sync_task_manager, "active_keys", return_value=["drive-1"]
    ):
        with pytest.raises(rebuild.VectorStoreRebuildConflictError, match="sync"):
            await rebuild.assert_no_indexing_in_flight(graph, [("org-1", "app-1")])


@pytest.mark.asyncio
async def test_gate_allows_an_idle_deployment():
    from app.connectors.services import vector_store_rebuild as rebuild

    graph = AsyncMock()
    graph.get_records_by_status = AsyncMock(return_value=[])

    with patch.object(rebuild.sync_task_manager, "active_keys", return_value=[]):
        await rebuild.assert_no_indexing_in_flight(graph, [("org-1", "app-1")])

    # Bounded: pages rather than a single probe, because folder rows have to be
    # looked past, but never a full table scan.
    from app.connectors.services import vector_store_rebuild as rebuild

    kwargs = graph.get_records_by_status.await_args.kwargs
    assert kwargs["limit"] == rebuild.BUSY_SCAN_PAGE_SIZE
    assert kwargs["status_filters"] == [
        ProgressStatus.IN_PROGRESS.value,
        ProgressStatus.QUEUED.value,
    ]


@pytest.mark.asyncio
async def test_cleanup_rechecks_before_dropping():
    """The route's gate runs before scheduling; a sync can start in between."""
    from app.connectors.services import vector_store_rebuild as rebuild

    redis = FakeRedis()
    lock = RebuildJobLock(redis, token="cleanup")
    await lock.try_acquire()
    graph = AsyncMock()
    graph.get_records_by_status = AsyncMock(return_value=[MagicMock()])
    kafka = AsyncMock()

    with patch.object(rebuild.sync_task_manager, "active_keys", return_value=[]):
        with pytest.raises(rebuild.VectorStoreRebuildConflictError):
            await rebuild.start_vector_store_cleanup(
                logger=MagicMock(),
                graph_provider=graph,
                kafka_service=kafka,
                lock=lock,
                redis=redis,
                org_id="org-1",
                user_id="user-1",
                apps=[("org-1", "app-1")],
            )

    kafka.publish_event.assert_not_awaited()


# ── Folder records must not block a rebuild ──


def _rec(record_id, mime):
    r = MagicMock()
    r.id = record_id
    r.mime_type = mime
    return r


@pytest.mark.asyncio
async def test_folder_records_do_not_count_as_busy():
    """Folders are graph scaffolding: no event ever follows them, so they sit in
    QUEUED permanently — and they carry no embeddings, so a rebuild cannot harm
    them. Counting them would block the rebuild for ever."""
    from app.config.constants.arangodb import MimeTypes
    from app.connectors.services import vector_store_rebuild as rebuild

    graph = AsyncMock()
    graph.get_records_by_status = AsyncMock(
        return_value=[
            _rec("f1", MimeTypes.FOLDER.value),
            _rec("f2", MimeTypes.GOOGLE_DRIVE_FOLDER.value),
        ]
    )

    busy = await rebuild.find_busy_connectors(graph, [("org-1", "app-1")])

    assert busy == []


@pytest.mark.asyncio
async def test_a_real_queued_record_still_blocks():
    from app.config.constants.arangodb import MimeTypes
    from app.connectors.services import vector_store_rebuild as rebuild

    graph = AsyncMock()
    graph.get_records_by_status = AsyncMock(
        return_value=[
            _rec("f1", MimeTypes.FOLDER.value),
            _rec("r1", "application/pdf"),
        ]
    )

    busy = await rebuild.find_busy_connectors(graph, [("org-1", "app-1")])

    assert busy == ["app-1"]


@pytest.mark.asyncio
async def test_scan_is_bounded_when_every_row_is_a_folder():
    """A full page of folders must not turn the gate into an endless scan."""
    from app.config.constants.arangodb import MimeTypes
    from app.connectors.services import vector_store_rebuild as rebuild

    page = [
        _rec(f"f{i}", MimeTypes.FOLDER.value)
        for i in range(rebuild.BUSY_SCAN_PAGE_SIZE)
    ]
    graph = AsyncMock()
    graph.get_records_by_status = AsyncMock(return_value=page)

    busy = await rebuild.find_busy_connectors(graph, [("org-1", "app-1")])

    assert busy == []
    assert graph.get_records_by_status.await_count == rebuild.BUSY_SCAN_MAX_PAGES


# ── Lock must not survive a pre-schedule failure ──


@pytest.mark.asyncio
async def test_lock_released_when_data_store_resolution_fails():
    """Only a scheduled job releases the lock in its finally.

    Anything that fails before scheduling must release here, or the key stays
    set and every later cleanup/reindex 409s until the TTL expires.
    """
    from app.connectors.api.router import _accept_vector_store_job

    request = _admin_rebuild_request()
    request.app.container.data_store = AsyncMock(
        side_effect=RuntimeError("data store unavailable")
    )
    lock, redis = MagicMock(), AsyncMock()

    with (
        patch("app.connectors.api.router.is_request_admin", return_value=True),
        patch(
            "app.connectors.api.router.get_cleanup_phase",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch(
            "app.connectors.api.router.acquire_rebuild_lock",
            new_callable=AsyncMock,
            return_value=(lock, redis),
        ),
        patch(
            "app.connectors.api.router.list_rebuild_apps",
            new_callable=AsyncMock,
            return_value=[],
        ),
        patch(
            "app.connectors.api.router.assert_no_indexing_in_flight",
            new_callable=AsyncMock,
        ),
        patch(
            "app.connectors.api.router.release_rebuild_lock", new_callable=AsyncMock
        ) as release,
    ):
        with pytest.raises(RuntimeError, match="data store unavailable"):
            await _accept_vector_store_job(
                request, operation="reindex", kafka_service=MagicMock()
            )

    release.assert_awaited_once()


@pytest.mark.asyncio
async def test_lock_released_exactly_once_when_already_running():
    """The 409 path releases itself; the surrounding except must not repeat it."""
    from fastapi import HTTPException

    from app.connectors.api.router import _accept_vector_store_job

    lock, redis = MagicMock(), AsyncMock()

    async def _decline(coro):
        coro.close()
        return False

    with (
        patch("app.connectors.api.router.is_request_admin", return_value=True),
        patch(
            "app.connectors.api.router.acquire_rebuild_lock",
            new_callable=AsyncMock,
            return_value=(lock, redis),
        ),
        patch(
            "app.connectors.api.router.list_rebuild_apps",
            new_callable=AsyncMock,
            return_value=[],
        ),
        patch(
            "app.connectors.api.router.assert_no_indexing_in_flight",
            new_callable=AsyncMock,
        ),
        patch(
            "app.connectors.api.router.schedule_vector_store_job_async",
            side_effect=_decline,
        ),
        patch(
            "app.connectors.api.router.release_rebuild_lock", new_callable=AsyncMock
        ) as release,
    ):
        with pytest.raises(HTTPException) as exc:
            await _accept_vector_store_job(
                _admin_rebuild_request(), operation="cleanup", kafka_service=MagicMock()
            )

    assert exc.value.status_code == 409
    release.assert_awaited_once()


class TestRebuildLockLifecycle:
    """The lease is short so a crashed job frees it fast, which only works if a
    live job keeps renewing and a busy job is turned away cleanly."""

    @pytest.mark.asyncio
    async def test_busy_lock_is_rejected_and_the_connection_closed(self):
        from app.connectors.services import vector_store_rebuild as m

        redis = FakeRedis()
        await redis.set(JOB_LOCK_KEY, "other-replica", nx=True, ex=60)
        redis.aclose = AsyncMock()

        with patch.object(
            m, "redis_from_config_service", AsyncMock(return_value=redis)
        ):
            with pytest.raises(m.VectorStoreRebuildBusyError):
                await m.acquire_rebuild_lock(MagicMock())

        # Leaking the connection on every rejected attempt would exhaust the pool.
        redis.aclose.assert_awaited_once()
        assert redis.store[JOB_LOCK_KEY] == "other-replica"

    @pytest.mark.asyncio
    async def test_acquire_stores_this_job_s_token(self):
        from app.connectors.services import vector_store_rebuild as m

        redis = FakeRedis()
        with patch.object(
            m, "redis_from_config_service", AsyncMock(return_value=redis)
        ):
            lock, returned = await m.acquire_rebuild_lock(MagicMock())

        assert returned is redis
        assert redis.store[JOB_LOCK_KEY] == lock.token

    @pytest.mark.asyncio
    async def test_renewal_stops_once_the_lock_is_lost(self):
        """Another replica owns it now; renewing would fight for a lease we lost."""
        from app.connectors.services import vector_store_rebuild as m

        lock = MagicMock()
        lock.refresh = AsyncMock(return_value=False)
        logger = MagicMock()

        with patch.object(m.asyncio, "sleep", AsyncMock()):
            await m._renew_lock_until_cancelled(lock, logger)

        lock.refresh.assert_awaited_once()
        logger.error.assert_called_once()

    @pytest.mark.asyncio
    async def test_renewal_survives_a_transient_refresh_error(self):
        """A blip must not silently end renewal — the lease would lapse mid-job
        and let a second rebuild start against the same collection."""
        from app.connectors.services import vector_store_rebuild as m

        lock = MagicMock()
        lock.refresh = AsyncMock(side_effect=[RuntimeError("redis blip"), False])
        logger = MagicMock()

        with patch.object(m.asyncio, "sleep", AsyncMock()):
            await m._renew_lock_until_cancelled(lock, logger)

        assert lock.refresh.await_count == 2
        logger.exception.assert_called_once()
