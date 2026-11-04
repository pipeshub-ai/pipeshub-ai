from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from app.config.constants.arangodb import EventTypes
from app.connectors.core.sync.task_manager import (
    SyncTaskManager,
    code_edge_build_task_manager,
)
from app.modules.code_graph import enabled as code_graph_switch
from app.telemetry.modules import code_graph_metrics

# Chained runs in a row before what is still owed is left to the reconciler: a pending
# flag that never clears (a stale duplicate sync point did, B3) must not rebuild forever.
_MAX_CHAINED_RUNS = 5

if TYPE_CHECKING:
    from logging import Logger

    from app.modules.code_graph.edge_build_runner import BuildLease, CodeEdgeBuildRunner


class CodeGraphEventService:
    def __init__(
        self,
        logger: Logger,
        runner: CodeEdgeBuildRunner,
        task_manager: SyncTaskManager = code_edge_build_task_manager,
    ) -> None:
        self._logger = logger
        self._runner = runner
        self._task_manager = task_manager
        # Strong references: the loop keeps only weak ones to tasks it runs.
        self._follow_ups: set[asyncio.Task] = set()
        self._chained: dict[str, int] = {}

    async def process_event(
        self,
        event_type: str,
        payload: dict[str, Any],
    ) -> bool:
        if event_type != EventTypes.BUILD_CODE_EDGES.value:
            self._logger.error("Unknown code graph event type: %s", event_type)
            return True
        if not code_graph_switch.CODE_GRAPH_ENABLED:
            self._logger.debug("CODE_GRAPH_ENABLED is false; dropping buildCodeEdges")
            return True

        scope = (
            payload.get("orgId"),
            payload.get("connectorId"),
            payload.get("recordGroupId"),
        )
        if not all(isinstance(value, str) and value for value in scope):
            self._logger.error(
                "buildCodeEdges message is missing scope identifiers: %s",
                sorted(payload.keys()),
            )
            return True

        await self.start_build(str(scope[0]), str(scope[1]), str(scope[2]))
        # Acked whether or not a build started: the request is marked pending on
        # the sync point before publish, and a busy repo's running build serves
        # it before releasing its lease (or the reconciler does). Redelivery only
        # blocked the partition behind the busy repo (SC-7).
        return True

    async def start_build(self, org_id: str, connector_id: str, record_group_id: str) -> bool:
        """Start a background build unless one holds the repo; True when started."""
        lease = await self._runner.acquire(
            org_id=org_id,
            record_group_id=record_group_id,
        )
        if lease is None:
            code_graph_metrics.record_busy_request()
            self._logger.info(
                "Code edge build busy for org=%s record_group=%s; the running build serves it",
                org_id,
                record_group_id,
            )
            return False

        try:
            task = await self._task_manager.start_if_idle(
                f"{org_id}:{record_group_id}",
                self._run_then_follow_up(org_id, connector_id, record_group_id, lease),
            )
        except Exception:
            await self._runner.release(lease)
            raise

        if task is None:
            # The previous build is still winding down.
            await self._runner.release(lease)
            return False
        return True

    async def _run_then_follow_up(self, org_id: str, connector_id: str, record_group_id: str,
                                  lease: BuildLease) -> None:
        """A run that ends at its follow-up cap with a request still owed starts the next run
        as soon as this one's task is gone; it used to wait for the reconciler (up to 10 min, B3)."""
        still_owed = await self._runner.run(org_id=org_id, connector_id=connector_id,
                                            record_group_id=record_group_id, lease=lease)
        repo = f"{org_id}:{record_group_id}"
        if not still_owed:
            self._chained.pop(repo, None)
            return
        chained = self._chained.get(repo, 0) + 1
        if chained > _MAX_CHAINED_RUNS:
            self._chained.pop(repo, None)
            self._logger.warning(
                "Code edge build for %s still owed after %d chained runs; leaving it to the reconciler",
                repo, _MAX_CHAINED_RUNS,
            )
            return
        self._chained[repo] = chained
        this_task = asyncio.current_task()

        async def follow_up() -> None:
            if this_task is not None:
                await asyncio.wait({this_task})
            await self.start_build(org_id, connector_id, record_group_id)

        self._follow_ups.add(task := asyncio.get_running_loop().create_task(follow_up()))
        task.add_done_callback(self._follow_ups.discard)

