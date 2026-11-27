"""A periodic count of the records an install holds, by connector type and indexing status.

Announced as the ``records.snapshot.taken`` platform event, one per org. The
count is the one ``GET /api/v1/stats`` serves (``get_connector_stats``), which
is per connector instance, so its cost is bounded here:

* it runs only while something listens for the event;
* it runs in its own task, one count at a time, never on a request;
* a tick covers at most ``max_orgs`` orgs, and the next tick continues where
  this one stopped, so every org is counted in turn;
* an org with more than ``max_connectors`` instances is counted over the first
  ones only, and the event says the count is not complete.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

from app import platform_events
from app.core.org_context import for_org, system
from app.utils import error_class

EVENT = "records.snapshot.taken"

DEFAULT_INTERVAL_SECONDS = 15 * 60
MIN_INTERVAL_SECONDS = 60
DEFAULT_MAX_ORGS = 20
DEFAULT_MAX_CONNECTORS = 50


def _env_int(name: str, default: int, *, minimum: int) -> int:
    raw = os.environ.get(name, "").strip()
    try:
        value = int(raw) if raw else default
    except ValueError:
        return default
    return value if value >= minimum else default


def interval_seconds() -> int:
    """``RECORDS_SNAPSHOT_INTERVAL_SECONDS``: 0 turns the count off; under a minute is refused (the default applies)."""
    if os.environ.get("RECORDS_SNAPSHOT_INTERVAL_SECONDS", "").strip() == "0":
        return 0
    return _env_int("RECORDS_SNAPSHOT_INTERVAL_SECONDS", DEFAULT_INTERVAL_SECONDS, minimum=MIN_INTERVAL_SECONDS)


def _id(doc: dict) -> str | None:
    value = doc.get("id") or doc.get("_key")
    return str(value) if value else None


class RecordsSnapshot:
    """Counts a bounded number of orgs per ``tick`` and announces each org's counts."""

    def __init__(
        self,
        graph_provider: Any,  # noqa: ANN401 - IGraphDBProvider; only three of its reads are used
        logger: logging.Logger,
        *,
        max_orgs: int | None = None,
        max_connectors: int | None = None,
    ) -> None:
        self._graph = graph_provider
        self._logger = logger
        self._max_orgs = max_orgs or _env_int("RECORDS_SNAPSHOT_MAX_ORGS_PER_TICK", DEFAULT_MAX_ORGS, minimum=1)
        self._max_connectors = max_connectors or _env_int(
            "RECORDS_SNAPSHOT_MAX_CONNECTORS_PER_ORG", DEFAULT_MAX_CONNECTORS, minimum=1
        )
        # the org id the last tick ended on: the next one starts after it
        self._after: str | None = None

    async def tick(self) -> int:
        """Count the next orgs in turn; returns how many were announced. Does nothing with no listener."""
        if not platform_events.has_listeners(EVENT):
            return 0
        with system("records_snapshot"):
            orgs = await self._graph.get_all_orgs()
        by_id = {org_id: org for org in orgs or [] if (org_id := _id(org))}
        ids = sorted(by_id)
        later = [org_id for org_id in ids if self._after is None or org_id > self._after]
        chosen = (later + [org_id for org_id in ids if org_id not in later])[: self._max_orgs]
        announced = 0
        for org_id in chosen:
            self._after = org_id
            try:
                with for_org(org_id, job="records_snapshot"):
                    counts, complete = await self._count(org_id)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                # one org's failure must not cost the others their count
                error_class.log_failure(
                    self._logger, "records_snapshot.org_failed", e, level=logging.WARNING, include_error_text=False
                )
                continue
            platform_events.emit(
                "records.snapshot.taken",
                org_id=org_id,
                email_domain=str(by_id[org_id].get("domain") or "unknown"),
                records_by_connector_status=counts,
                complete=complete,
            )
            announced += 1
        if chosen and chosen[-1] == ids[-1]:
            self._after = None
        return announced

    async def _count(self, org_id: str) -> tuple[dict[tuple[str, str], int], bool]:
        apps = await self._graph.get_org_apps(org_id, raise_on_error=True)
        counts: dict[tuple[str, str], int] = {}
        complete = len(apps) <= self._max_connectors
        for app in apps[: self._max_connectors]:
            app_id = _id(app)
            if app_id is None:
                continue
            result = await self._graph.get_connector_stats(org_id, app_id)
            if not result.get("success"):
                complete = False
                continue
            connector = str(app.get("type") or "unknown")
            by_status = ((result.get("data") or {}).get("stats") or {}).get("indexingStatus") or {}
            for status, count in by_status.items():
                if count:
                    counts[(connector, str(status))] = counts.get((connector, str(status)), 0) + int(count)
        return counts, complete


async def run_records_snapshot(snapshot: RecordsSnapshot, logger: logging.Logger, interval_s: int) -> None:
    """Tick forever; best-effort, never fatal."""
    while True:
        try:
            await snapshot.tick()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            error_class.log_failure(logger, "records_snapshot.tick_failed", e, level=logging.WARNING, include_error_text=False)
        await asyncio.sleep(interval_s)
