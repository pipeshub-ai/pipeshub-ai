from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Any

from app.config.constants.arangodb import CollectionNames
from app.modules.code_graph import edge_build_trigger
from app.modules.code_graph.edge_builder import build_code_graph_edges

if TYPE_CHECKING:
    from logging import Logger

    from redis.asyncio import Redis

    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

BuildLease = tuple[str, str]


class CodeEdgeBuildRunner:
    def __init__(
        self,
        graph_provider: IGraphDBProvider,
        redis: Redis,
        log: Logger,
    ) -> None:
        self._graph_provider = graph_provider
        self._redis = redis
        self._log = log

    async def acquire(
        self,
        *,
        org_id: str,
        record_group_id: str,
    ) -> BuildLease | None:
        return await edge_build_trigger.acquire_build_lock(
            self._redis,
            org_id,
            record_group_id,
        )

    async def release(self, lease: BuildLease) -> None:
        await edge_build_trigger.release_build_lock(
            self._redis,
            lease[0],
            lease[1],
        )

    async def run(
        self,
        *,
        org_id: str,
        connector_id: str,
        record_group_id: str,
        lease: BuildLease | None = None,
    ) -> None:
        owned_lease = lease or await self.acquire(
            org_id=org_id,
            record_group_id=record_group_id,
        )
        if owned_lease is None:
            raise RuntimeError(
                f"Code edge build is already running for {org_id}/{record_group_id}"
            )

        renewal = asyncio.create_task(
            edge_build_trigger.renew_build_lock_until_cancelled(
                self._redis,
                owned_lease[0],
                owned_lease[1],
                self._log,
            )
        )
        try:
            if await edge_build_trigger.group_has_unfinished_records(
                self._graph_provider,
                org_id,
                record_group_id,
            ):
                return

            last_build = (
                await edge_build_trigger.read_build_state(
                    self._graph_provider,
                    org_id,
                    record_group_id,
                )
            ).last_build

            # Read before the watermark query: a record updated in between is
            # invisible now and would otherwise also fall below the next run.
            started_at_ms = int(time.time() * 1000)

            touched_record_ids = None
            if last_build is not None:
                touched_record_ids = await edge_build_trigger.records_updated_since(
                    self._graph_provider,
                    org_id,
                    record_group_id,
                    last_build,
                )
                if not touched_record_ids:
                    await self._settle(
                        org_id=org_id,
                        connector_id=connector_id,
                        record_group_id=record_group_id,
                        started_at_ms=started_at_ms,
                    )
                    return

            self._log.info(
                "Automatic code edge build starting for org=%s record_group=%s "
                "mode=%s touched_records=%s",
                org_id,
                record_group_id,
                "incremental" if touched_record_ids is not None else "full",
                (
                    len(touched_record_ids)
                    if touched_record_ids is not None
                    else "all"
                ),
            )
            result = await build_code_graph_edges(
                graph_provider=self._graph_provider,
                org_id=org_id,
                record_group_id=record_group_id,
                touched_record_ids=touched_record_ids,
                log=self._log,
            )
            await self._settle(
                org_id=org_id,
                connector_id=connector_id,
                record_group_id=record_group_id,
                started_at_ms=started_at_ms,
                last_build_at=started_at_ms,
            )
            self._log.info(
                "Automatic code edge build complete: %s",
                result.as_log_fields(),
            )
        except Exception:
            self._log.exception(
                "Automatic code edge build failed for org=%s record_group=%s",
                org_id,
                record_group_id,
            )
            raise
        finally:
            renewal.cancel()
            await asyncio.gather(renewal, return_exceptions=True)
            try:
                await self.release(owned_lease)
            except Exception:
                self._log.exception(
                    "Failed to release code edge build lock for org=%s "
                    "record_group=%s",
                    org_id,
                    record_group_id,
                )

    async def _settle(
        self,
        *,
        org_id: str,
        connector_id: str,
        record_group_id: str,
        started_at_ms: int,
        last_build_at: int | None = None,
    ) -> None:
        state = await edge_build_trigger.read_build_state(
            self._graph_provider,
            org_id,
            record_group_id,
        )
        sync_point_data: dict[str, Any] = {
            "orgId": org_id,
            "connectorId": connector_id,
            "syncDataPointType": "codeEdgeBuild",
            "edgeBuildPending": edge_build_trigger.still_owed(
                state,
                started_at_ms,
            ),
        }
        if last_build_at is not None:
            sync_point_data["lastEdgeBuildAt"] = last_build_at
        await self._graph_provider.upsert_sync_point(
            sync_point_key=edge_build_trigger.sync_point_key_for(record_group_id),
            sync_point_data=sync_point_data,
            collection=CollectionNames.SYNC_POINTS.value,
        )
