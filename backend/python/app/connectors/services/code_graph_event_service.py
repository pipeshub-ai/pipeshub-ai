from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.config.constants.arangodb import EventTypes
from app.connectors.core.sync.task_manager import (
    SyncTaskManager,
    code_edge_build_task_manager,
)

if TYPE_CHECKING:
    from logging import Logger

    from app.modules.code_graph.edge_build_runner import CodeEdgeBuildRunner


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

    async def process_event(
        self,
        event_type: str,
        payload: dict[str, Any],
    ) -> bool:
        if event_type != EventTypes.BUILD_CODE_EDGES.value:
            self._logger.error("Unknown code graph event type: %s", event_type)
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

        org_id, connector_id, record_group_id = (
            str(scope[0]),
            str(scope[1]),
            str(scope[2]),
        )
        lease = await self._runner.acquire(
            org_id=org_id,
            record_group_id=record_group_id,
        )
        if lease is None:
            self._logger.info(
                "Code edge build busy for org=%s record_group=%s; retrying event",
                org_id,
                record_group_id,
            )
            return False

        try:
            task = await self._task_manager.start_if_idle(
                f"{org_id}:{record_group_id}",
                self._runner.run(
                    org_id=org_id,
                    connector_id=connector_id,
                    record_group_id=record_group_id,
                    lease=lease,
                ),
            )
        except Exception:
            await self._runner.release(lease)
            raise

        if task is None:
            await self._runner.release(lease)
            return False
        return True
