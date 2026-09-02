"""Trigger cross-file code edge resolution when a repository finishes indexing."""

from __future__ import annotations

import asyncio
import os
from typing import TYPE_CHECKING

from app.modules.code_graph.connectors import (
    _NORMALIZED_CODE_TYPES,
    _normalized,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from logging import Logger

_DEFAULT_QUIET_PERIOD_SECONDS = 30.0


def quiet_period_seconds() -> float:
    raw = os.getenv("PIPESHUB_CODE_EDGE_BUILD_QUIET_SECONDS", "").strip()
    if raw:
        try:
            value = float(raw)
        except ValueError:
            pass
        else:
            if value > 0:
                return value
    return _DEFAULT_QUIET_PERIOD_SECONDS


class EdgeBuildScheduler:
    """Run one edge build per record group, once its records stop arriving.

    Answering "is this the last file?" needs a drain query on every completed
    record, and every record that finishes after the group drains asks again —
    so a repo's tail produces a burst of redundant triggers. Waiting for the
    records to go *quiet* needs neither: each completion just pushes the
    deadline out, and the group's last one lets it expire. The build re-checks
    that the group really has drained, so firing early during a lull is
    harmless — it returns, and the next completion schedules another.
    """

    def __init__(
        self,
        build: Callable[..., Awaitable[None]],
        logger: Logger,
        quiet_period: float | None = None,
    ) -> None:
        self._build = build
        self._logger = logger
        self._quiet_period = quiet_period if quiet_period is not None else quiet_period_seconds()
        self._deadlines: dict[tuple[str, str], float] = {}
        self._waiters: dict[tuple[str, str], asyncio.Task[None]] = {}
        # Serialises builds for one group: a record completing mid-build starts
        # a new waiter, which must queue behind the build already running rather
        # than race it and be dropped by the cross-process lock.
        self._locks: dict[tuple[str, str], asyncio.Lock] = {}

    def schedule(self, record: dict[str, object]) -> None:
        """Note that a code record finished; build once its group goes quiet."""
        if _normalized(record.get("connectorName")) not in _NORMALIZED_CODE_TYPES:
            return

        org_id = record.get("orgId")
        connector_id = record.get("connectorId")
        record_group_id = record.get("recordGroupId")
        if not all(
            isinstance(value, str) and value
            for value in (org_id, connector_id, record_group_id)
        ):
            self._logger.warning(
                "Skipping automatic code edge build; record %s lacks scope identifiers",
                record.get("_key") or record.get("id"),
            )
            return

        key = (str(org_id), str(record_group_id))
        loop = asyncio.get_running_loop()
        self._deadlines[key] = loop.time() + self._quiet_period
        if key not in self._waiters:
            self._waiters[key] = loop.create_task(
                self._build_when_quiet(key, str(connector_id))
            )

    async def _build_when_quiet(
        self, key: tuple[str, str], connector_id: str
    ) -> None:
        org_id, record_group_id = key
        try:
            loop = asyncio.get_running_loop()
            while True:
                remaining = self._deadlines.get(key, loop.time()) - loop.time()
                if remaining <= 0:
                    break
                await asyncio.sleep(remaining)

            # Deregistered before building, so a record finishing during the
            # build schedules the next one instead of being swallowed.
            self._waiters.pop(key, None)
            self._deadlines.pop(key, None)

            lock = self._locks.setdefault(key, asyncio.Lock())
            async with lock:
                await self._build(
                    org_id=org_id,
                    connector_id=connector_id,
                    record_group_id=record_group_id,
                )
        except asyncio.CancelledError:
            self._logger.info(
                "Code edge build for org=%s record_group=%s cancelled; the sync "
                "point is unchanged, so the next completed record redoes it",
                org_id,
                record_group_id,
            )
            raise
        except Exception:
            # Never propagate: this runs detached from the record whose
            # completion scheduled it, and that record indexed fine.
            self._logger.exception(
                "Automatic code edge build failed for org=%s record_group=%s",
                org_id,
                record_group_id,
            )
        finally:
            self._waiters.pop(key, None)
            self._deadlines.pop(key, None)
            if not self._locks.get(key, asyncio.Lock()).locked():
                self._locks.pop(key, None)

    async def drain(self) -> None:
        """Await in-flight waiters; for shutdown and for tests."""
        for task in list(self._waiters.values()):
            task.cancel()
        await asyncio.gather(*self._waiters.values(), return_exceptions=True)
