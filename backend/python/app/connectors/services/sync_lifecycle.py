"""Shared run wrapper for connector syncs.

Both entry points that execute ``connector.run_sync()`` — the Kafka-driven
path (``EventService``) and the startup-resume path (``ConnectorFactory``) —
must apply the same lifecycle: establish the run-scoped ContextVar, heartbeat
the run while discovery is quiet, close discovery only on success, mark the
run failed on error, respect superseding runs, and finally reset the app
status. Keeping it in one place stops the two paths from drifting.
"""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Optional, Protocol

from app.connectors.services.sync_run_context import (
    reset_sync_run_id,
    set_sync_run_id,
)

if TYPE_CHECKING:
    from app.connectors.services.sync_progress_store import ConnectorSyncProgressStore

HEARTBEAT_INTERVAL_SECONDS = 60

StoreGetter = Callable[[], Awaitable[Optional["ConnectorSyncProgressStore"]]]
# async () -> None; resets apps.status once the run settles
IdleStatusSetter = Callable[[], Awaitable[None]]


class SyncRunnable(Protocol):
    async def run_sync(self) -> None: ...


async def _heartbeat_sync_run(
    get_store: StoreGetter, org_id: str, connector_id: str, run_id: str
) -> None:
    """Keep a long discovery alive even when it has not emitted a record yet."""
    while True:
        await asyncio.sleep(HEARTBEAT_INTERVAL_SECONDS)
        store = await get_store()
        if store:
            await store.touch_heartbeat(org_id, connector_id, run_id=run_id)


async def run_sync_with_lifecycle(
    *,
    connector: SyncRunnable,
    connector_id: str,
    org_id: Optional[str],
    run_id: Optional[str],
    logger: logging.Logger,
    get_store: StoreGetter,
    set_idle_status: IdleStatusSetter,
) -> None:
    """Run ``connector.run_sync()`` with the full progress lifecycle applied."""
    start = time.monotonic()
    failed = False
    heartbeat_task: Optional[asyncio.Task] = None
    try:
        token = set_sync_run_id(run_id)
        try:
            if org_id and run_id:
                heartbeat_task = asyncio.create_task(
                    _heartbeat_sync_run(get_store, org_id, connector_id, run_id)
                )
            await connector.run_sync()
        finally:
            reset_sync_run_id(token)
    except BaseException:
        failed = True
        raise
    finally:
        if heartbeat_task:
            heartbeat_task.cancel()
            await asyncio.gather(heartbeat_task, return_exceptions=True)
        elapsed = time.monotonic() - start
        mins, secs = divmod(elapsed, 60)
        elapsed_str = f"{int(mins)}m {secs:.1f}s" if mins else f"{secs:.1f}s"
        logger.info(
            f"✅ Sync finished for connector {connector_id} — total time: {elapsed_str}"
        )
        # A concurrent re-trigger cancels this task and starts a fresh run
        # (start_run writes a new runId, then start_sync cancels us). Our
        # finally then runs: if we blindly closed discovery + set IDLE we
        # would clobber the newer run's progress/status. Only touch state we
        # still own. (No early return here — a bare `return` in a finally
        # block would swallow the CancelledError that cancellation raises.)
        store = await get_store() if org_id else None
        superseded = store is not None and not await store.is_current_run(
            org_id, connector_id, run_id
        )
        if superseded:
            logger.info(
                f"Sync task for connector {connector_id} was superseded by a newer run; "
                "leaving status and progress to the newer run"
            )
        else:
            # Discovery is complete once run_sync() returns; freeze the run total so
            # the indexing phase can drive "Indexing X of Y" while apps.status is IDLE.
            if org_id and store and failed:
                await store.mark_failed(org_id, connector_id, run_id=run_id)
            elif org_id and store:
                await store.close_discovery(org_id, connector_id, expected_run_id=run_id)
            try:
                await set_idle_status()
                logger.info(f"✅ Cleared status for connector {connector_id} after sync")
            except Exception as clear_err:
                logger.error(
                    f"❌ Failed to clear status for connector {connector_id}: {clear_err}"
                )
            if failed:
                logger.error(
                    "Connector sync %s failed before discovery completed; progress may be partial",
                    connector_id,
                )
