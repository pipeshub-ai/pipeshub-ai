from __future__ import annotations

import asyncio
import os
import time
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

from app.config.constants.arangodb import CollectionNames
from app.modules.code_graph import edge_build_trigger
from app.modules.code_graph.edge_builder import build_code_graph_edges

if TYPE_CHECKING:
    from logging import Logger

    from redis.asyncio import Redis

    from app.modules.code_graph.facts_source import CodeFactsSource
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

BuildLease = tuple[str, str]

# How many builds this process runs at once, and per org. A build is CPU-bound
# for seconds to minutes; nothing bounded them, so an onboarding burst ran one
# per repo in parallel on the connectors service.
_MAX_CONCURRENT_BUILDS = max(1, int(os.getenv("CODE_EDGE_BUILD_MAX_CONCURRENT", "2")))
_MAX_BUILDS_PER_ORG = max(1, int(os.getenv("CODE_EDGE_BUILD_MAX_PER_ORG", "1")))
_process_slots: dict[str, asyncio.Semaphore] = {}
_org_slots: dict[str, asyncio.Semaphore] = {}


@asynccontextmanager
async def _build_slot(org_id: str):  # noqa: ANN202 -- an async context manager
    """Wait for this org's turn, then for a process-wide one.

    Per org first, so one org's queue of repos never holds a global slot while
    it waits on itself.
    """
    process = _process_slots.setdefault("all", asyncio.Semaphore(_MAX_CONCURRENT_BUILDS))
    org = _org_slots.setdefault(org_id, asyncio.Semaphore(_MAX_BUILDS_PER_ORG))
    async with org, process:
        yield


class CodeEdgeBuildRunner:
    def __init__(
        self,
        graph_provider: IGraphDBProvider,
        redis: Redis,
        log: Logger,
        facts_source: CodeFactsSource | None = None,
    ) -> None:
        self._graph_provider = graph_provider
        self._redis = redis
        self._log = log
        self._facts_source = facts_source

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
            async with _build_slot(org_id):
                await self._build(org_id=org_id, connector_id=connector_id,
                                  record_group_id=record_group_id)
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

    async def _build(self, *, org_id: str, connector_id: str, record_group_id: str) -> None:
        if await edge_build_trigger.group_has_unfinished_records(
            self._graph_provider,
            org_id,
            record_group_id,
        ):
            return

        start_state = await edge_build_trigger.read_build_state(
            self._graph_provider,
            org_id,
            record_group_id,
        )
        last_build = start_state.last_build

        # Read before the watermark query: a record updated in between is
        # invisible now and would otherwise also fall below the next run.
        started_at_ms = int(time.time() * 1000)

        touched_record_ids = None
        full_requested = start_state.full_request_id is not None
        if last_build is not None and not full_requested:
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
                    started_request_id=start_state.request_id,
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
            facts_source=self._facts_source,
            log=self._log,
        )
        if result.facts_missing:
            # Those files kept their old edges; holding the watermark back
            # keeps them in the next build instead of skipping them for good.
            self._log.warning(
                "Code edge build for org=%s record_group=%s skipped %d file(s) "
                "without readable facts; the watermark stays where it was",
                org_id, record_group_id, result.facts_missing,
            )
        await self._settle(
            org_id=org_id,
            connector_id=connector_id,
            record_group_id=record_group_id,
            started_at_ms=started_at_ms,
            started_request_id=start_state.request_id,
            last_build_at=None if result.facts_missing else started_at_ms,
            served_full_request_id=(
                start_state.full_request_id
                if touched_record_ids is None and not result.facts_missing else None
            ),
        )
        self._log.info(
            "Automatic code edge build complete: %s",
            result.as_log_fields(),
        )

    async def _settle(
        self,
        *,
        org_id: str,
        connector_id: str,
        record_group_id: str,
        started_at_ms: int,
        started_request_id: str | None = None,
        last_build_at: int | None = None,
        served_full_request_id: str | None = None,
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
                started_request_id,
            ),
        }
        if last_build_at is not None:
            sync_point_data["lastEdgeBuildAt"] = last_build_at
        if served_full_request_id is not None and not edge_build_trigger.full_build_still_owed(
            state, served_full_request_id,
        ):
            sync_point_data["edgeBuildFullRequestId"] = None
        await self._graph_provider.upsert_sync_point(
            sync_point_key=edge_build_trigger.sync_point_key_for(record_group_id),
            sync_point_data=sync_point_data,
            collection=CollectionNames.SYNC_POINTS.value,
        )
