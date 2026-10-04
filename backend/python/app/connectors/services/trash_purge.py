"""Remove records that have been in the trash longer than the retention period.

Runs in-process in the connectors service, which already owns graph deletes and
the message producer. One replica leads at a time, through the same Redis
SET NX lease as the vector membership backfill. The loop wakes every hour and
starts a run when the interval has passed since the last run started, at the
configured hour (UTC).

A run walks each org's trash oldest first, in pages, by
(``deletedAtTimestamp``, key). For each page:

1. The cleanup the page will owe is saved to an outbox in the KV store.
2. The graph delete removes each record's edges, type doc and vertex in one
   transaction, checking again inside it that the record is still in the trash
   and due, so a record restored meanwhile stays.
3. Each removed record gets the hard delete's own events: ``deleteRecord``
   (vectors, stored content of its virtual record, entity point) and
   ``deleteStoredDocuments`` for its uploaded file or stored copy. Then the
   outbox entry goes.

A crash or a broker outage after step 2 leaves the outbox entry; the next tick
publishes the events of every record in it that is gone from the graph and
drops the rest. A record that still has a child, trashed or not, waits: the
org is walked again while a walk both removed something and passed over such
a record, so a trashed tree goes leaves first, one level per walk, in one run. A page the graph refuses is retried record by record, and a
record that fails is counted (``purgeAttempts``) and left out after
``maxAttempts``. Then the record groups kept only for the trash
(``isDeletedAtSource``) go once nothing belongs to them.

Nothing happens while ``ENABLE_SOFT_DELETE`` is off or ``softDeletePurge.enabled``
is false; an unfinished run resumes from its cursor when both are on again.
"""

from __future__ import annotations

import asyncio
import os
import time
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from app.config.constants.arangodb import CollectionNames, EventTypes
from app.connectors.services.vector_cleanup_events import (
    build_stored_document_cleanup_events,
)
from app.connectors.sources.local_fs.connector import LOCAL_FS_STORAGE_PATH_PREFIX
from app.exceptions.graph_db_exceptions import GraphLockUnavailableError
from app.modules.indexing.vector_membership_backfill import (
    VectorMembershipBackfillLeaderLock,
)
from app.services.featureflag.platform_settings import (
    PLATFORM_SETTINGS_KEY,
    is_soft_delete_enabled,
)
from app.services.graph_db.common.utils import is_storage_document_id
from app.services.messaging.utils import MessagingUtils
from app.telemetry.modules.soft_delete_metrics import (
    record_purge_run,
    record_purged,
    set_trash_backlog,
)
from app.utils.time_conversion import get_epoch_timestamp_in_ms

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from logging import Logger

    from app.config.configuration_service import ConfigurationService
    from app.containers.connector import ConnectorAppContainer
    from app.modules.indexing.vector_membership_backfill import LeaderLock
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
    from app.services.messaging.interface.producer import IMessagingProducer

LEADER_KEY = "soft_delete_purge:leader"
STATE_KEY = "/services/softDeletePurge/state"
OUTBOX_DIRECTORY = "/services/softDeletePurge/outbox/"
SETTINGS_FIELD = "softDeletePurge"
RECORD_EVENTS_TOPIC = "record-events"

DAY_MS = 24 * 60 * 60 * 1000
# Above a slow page's graph work, below the gap a dead leader leaves.
LEASE_TTL_SECONDS = 600
GROUP_PAGE_SIZE = 100
MAX_ERROR_LENGTH = 500


class TrashPurgeError(Exception):
    """A step the run cannot go past; the next tick tries again from the saved cursor."""


class Outcome:
    NOT_LEADER = "not_leader"
    DISABLED = "disabled"
    NOT_DUE = "not_due"
    FINISHED = "finished"
    PAUSED = "paused"
    STOPPED = "stopped"
    LOST_LEASE = "lost_lease"


def _env_seconds(name: str) -> float | None:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return None
    try:
        return max(0.0, float(raw))
    except ValueError:
        return None


def _bounded_int(raw: object, default: int, low: int, high: int) -> int:
    if isinstance(raw, bool):
        return default
    try:
        return min(high, max(low, int(raw)))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class PurgeSettings:
    """``softDeletePurge`` in the platform settings, with defaults from the design.

    ``SOFT_DELETE_PURGE_INTERVAL_SECONDS`` and ``SOFT_DELETE_PURGE_MIN_AGE_SECONDS``
    override the day values for development and tests; with the interval
    overridden the run hour no longer applies.
    """

    enabled: bool = True
    interval_days: int = 14
    min_age_days: int = 14
    run_hour_utc: int = 3
    page_size: int = 500
    max_records_per_run: int = 500_000
    max_run_minutes: int = 120
    page_pause_ms: int = 200
    max_attempts: int = 5
    interval_seconds: float | None = None
    min_age_seconds: float | None = None

    @classmethod
    def from_config(cls, raw: object) -> PurgeSettings:
        raw = raw if isinstance(raw, dict) else {}
        defaults = cls()
        enabled = raw.get("enabled", defaults.enabled)
        return cls(
            enabled=enabled if isinstance(enabled, bool) else defaults.enabled,
            interval_days=_bounded_int(raw.get("intervalDays"), defaults.interval_days, 1, 365),
            # At least a day from the settings: a mistyped 0 must not empty the trash on the next run.
            min_age_days=_bounded_int(raw.get("minAgeDays"), defaults.min_age_days, 1, 3650),
            run_hour_utc=_bounded_int(raw.get("runHourUtc"), defaults.run_hour_utc, 0, 23),
            page_size=_bounded_int(raw.get("pageSize"), defaults.page_size, 1, 1000),
            max_records_per_run=_bounded_int(
                raw.get("maxRecordsPerRun"), defaults.max_records_per_run, 1, 10_000_000
            ),
            max_run_minutes=_bounded_int(raw.get("maxRunMinutes"), defaults.max_run_minutes, 1, 24 * 60),
            page_pause_ms=_bounded_int(raw.get("pagePauseMs"), defaults.page_pause_ms, 0, 60_000),
            max_attempts=_bounded_int(raw.get("maxAttempts"), defaults.max_attempts, 1, 100),
            interval_seconds=_env_seconds("SOFT_DELETE_PURGE_INTERVAL_SECONDS"),
            min_age_seconds=_env_seconds("SOFT_DELETE_PURGE_MIN_AGE_SECONDS"),
        )

    @property
    def min_age_ms(self) -> int:
        if self.min_age_seconds is not None:
            return int(self.min_age_seconds * 1000)
        return self.min_age_days * DAY_MS

    def is_due(self, last_started_at: int | None, now_ms: int) -> bool:
        """Whether a new run starts now, given when the last finished run started."""
        if self.interval_seconds is not None:
            return last_started_at is None or now_ms - last_started_at >= self.interval_seconds * 1000
        if datetime.fromtimestamp(now_ms / 1000, tz=timezone.utc).hour != self.run_hour_utc:
            return False
        # Whole days, so a tick a few seconds earlier in the hour than last time still counts.
        return last_started_at is None or now_ms // DAY_MS - last_started_at // DAY_MS >= self.interval_days


async def load_purge_settings(config_service: ConfigurationService) -> PurgeSettings:
    settings = await config_service.get_config(PLATFORM_SETTINGS_KEY, default={}, use_cache=False)
    return PurgeSettings.from_config(settings.get(SETTINGS_FIELD) if isinstance(settings, dict) else None)


@dataclass
class PurgeRun:
    run_id: str
    started_at: int
    cutoff: int
    org_ids: list[str]
    org_index: int = 0
    after: list | None = None
    # This walk of the current org: removed, and passed over for having children.
    pass_purged: int = 0
    pass_held: int = 0
    purged: int = 0
    kept: int = 0
    failed: int = 0
    groups: int = 0

    @classmethod
    def from_dict(cls, raw: dict) -> PurgeRun:
        return cls(
            run_id=str(raw["run_id"]),
            started_at=int(raw["started_at"]),
            cutoff=int(raw["cutoff"]),
            org_ids=[str(o) for o in raw.get("org_ids") or []],
            org_index=int(raw.get("org_index") or 0),
            after=list(raw["after"]) if raw.get("after") else None,
            pass_purged=int(raw.get("pass_purged") or 0),
            pass_held=int(raw.get("pass_held") or 0),
            purged=int(raw.get("purged") or 0),
            kept=int(raw.get("kept") or 0),
            failed=int(raw.get("failed") or 0),
            groups=int(raw.get("groups") or 0),
        )


@dataclass
class PurgeState:
    """Saved after every page, so a crash or a lost lease resumes where it stopped."""

    run: PurgeRun | None = None
    last_started_at: int | None = None
    last_finished_at: int | None = None
    last_counts: dict = field(default_factory=dict)

    @classmethod
    def from_dict(cls, raw: object) -> PurgeState:
        if not isinstance(raw, dict):
            return cls()
        run = raw.get("run")
        return cls(
            run=PurgeRun.from_dict(run) if isinstance(run, dict) else None,
            last_started_at=raw.get("lastStartedAt"),
            last_finished_at=raw.get("lastFinishedAt"),
            last_counts=raw.get("lastCounts") or {},
        )

    def to_dict(self) -> dict:
        return {
            "status": "running" if self.run else "idle",
            "run": asdict(self.run) if self.run else None,
            "lastStartedAt": self.last_started_at,
            "lastFinishedAt": self.last_finished_at,
            "lastCounts": self.last_counts,
        }


def stored_document_ids(row: dict) -> list[str]:
    """The storage documents a purged record owned: its upload, or a connector's stored copy.

    Local FS push-flow records point at theirs with a ``storage://`` path and Web
    pages name theirs in ``storageDocumentId``; each belongs to that one record.
    """
    ids = [row.get("uploadDocumentId")]
    path = row.get("filePath")
    if isinstance(path, str) and path.startswith(LOCAL_FS_STORAGE_PATH_PREFIX):
        ids.append(path[len(LOCAL_FS_STORAGE_PATH_PREFIX):].strip())
    ids.append(row.get("storageDocumentId"))
    return list(dict.fromkeys(i for i in ids if is_storage_document_id(i)))


def cleanup_owed(row: dict) -> dict:
    return {
        "connectorId": row.get("connectorId"),
        "deleteRecord": row["deleteRecordPayload"],
        "documentIds": stored_document_ids(row),
    }


def _short(error: BaseException) -> str:
    return f"{type(error).__name__}: {error}"[:MAX_ERROR_LENGTH]


class TrashPurger:
    def __init__(
        self,
        logger: Logger,
        graph_provider: IGraphDBProvider,
        config_service: ConfigurationService,
        producer: IMessagingProducer,
        lock: LeaderLock,
        *,
        clock: Callable[[], int] = get_epoch_timestamp_in_ms,
        sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.logger = logger
        self.graph = graph_provider
        self.config_service = config_service
        self.producer = producer
        self.lock = lock
        self.clock = clock
        self.sleep = sleep
        self.monotonic = monotonic
        # Failed in this tick: a later walk of the same org skips them, so one
        # run spends one attempt each.
        self._failed: set[str] = set()

    async def tick(self) -> str:
        """Start, resume or skip a run; return what happened."""
        if not await self.lock.try_acquire():
            return Outcome.NOT_LEADER
        try:
            if not await is_soft_delete_enabled(self.config_service):
                return Outcome.DISABLED
            settings = await load_purge_settings(self.config_service)
            if not settings.enabled:
                return Outcome.DISABLED
            if not await self._drain_outbox():
                record_purge_run(Outcome.PAUSED)
                return Outcome.PAUSED
            state = await self._read_state()
            if state.run is None:
                now = self.clock()
                if not settings.is_due(state.last_started_at, now):
                    return Outcome.NOT_DUE
                state.run = PurgeRun(
                    run_id=uuid4().hex,
                    started_at=now,
                    cutoff=now - settings.min_age_ms,
                    org_ids=await self._org_ids(),
                )
                state.last_started_at = now
                await self._save_state(state)
                self.logger.info(
                    "Trash purge %s started: %d org(s), records in the trash since %d or earlier",
                    state.run.run_id, len(state.run.org_ids), state.run.cutoff,
                )
            else:
                self.logger.info("Trash purge %s resumed", state.run.run_id)
            outcome = await self._work(state, settings)
            if outcome != Outcome.FINISHED:
                record_purge_run(outcome)
            return outcome
        finally:
            await self.lock.release()

    async def _work(self, state: PurgeState, settings: PurgeSettings) -> str:
        run = state.run
        assert run is not None
        deadline = self.monotonic() + settings.max_run_minutes * 60
        processed = 0
        while run.org_index < len(run.org_ids):
            org_id = run.org_ids[run.org_index]
            while True:
                stop = await self._stop_reason(deadline, processed, settings)
                if stop:
                    self.logger.info(
                        "Trash purge %s %s after %d record(s) this pass; it resumes from its cursor",
                        run.run_id, stop, processed,
                    )
                    return stop
                page = await self.graph.get_purgeable_trashed_records(
                    org_id,
                    run.cutoff,
                    after=tuple(run.after) if run.after else None,
                    limit=settings.page_size,
                    max_attempts=settings.max_attempts,
                )
                rows = [row for row in page.get("records") or [] if row["id"] not in self._failed]
                run.pass_held += int(page.get("held") or 0)
                if rows:
                    try:
                        published = await self._purge_page(org_id, rows, run, settings)
                    except GraphLockUnavailableError as exc:
                        self.logger.warning("Trash purge %s paused: %s", run.run_id, exc)
                        await self._save_state(state)
                        return Outcome.PAUSED
                    processed += len(rows)
                    if not published:
                        await self._save_state(state)
                        return Outcome.PAUSED
                nxt = page.get("next")
                run.after = list(nxt) if nxt else None
                if run.after is None and run.pass_purged and run.pass_held:
                    # Their children went in this walk, so some of them may go in the next.
                    self.logger.debug(
                        "Trash purge %s: walking org %s again for %d record(s) whose children were in the trash",
                        run.run_id, org_id, run.pass_held,
                    )
                    run.pass_purged = run.pass_held = 0
                    await self._save_state(state)
                    continue
                await self._save_state(state)
                if run.after is None:
                    break
                if settings.page_pause_ms:
                    await self.sleep(settings.page_pause_ms / 1000)
            run.groups += await self._purge_kept_groups(org_id)
            run.org_index += 1
            run.after = None
            run.pass_purged = run.pass_held = 0
            await self._save_state(state)
        await self._finish(state, settings)
        return Outcome.FINISHED

    async def _stop_reason(self, deadline: float, processed: int, settings: PurgeSettings) -> str | None:
        if not await self.lock.refresh():
            return Outcome.LOST_LEASE
        if not await is_soft_delete_enabled(self.config_service):
            return Outcome.STOPPED
        if processed >= settings.max_records_per_run or self.monotonic() >= deadline:
            return Outcome.PAUSED
        return None

    async def _purge_page(self, org_id: str, rows: list[dict], run: PurgeRun, settings: PurgeSettings) -> bool:
        """Purge one page; False when its events could not be published and stay owed."""
        ids = [row["id"] for row in rows]
        outbox_key = f"{OUTBOX_DIRECTORY}{run.run_id}-{uuid4().hex[:12]}"
        await self._save_outbox(outbox_key, org_id, {row["id"]: cleanup_owed(row) for row in rows})
        try:
            result = await self.graph.purge_trashed_records(
                ids, org_id, run.cutoff, max_attempts=settings.max_attempts
            )
            purged, kept = list(result.get("purged") or []), list(result.get("kept") or [])
        except GraphLockUnavailableError:
            # Nothing was attempted; the saved page owes nothing until it runs.
            raise
        except Exception as exc:
            self.logger.warning(
                "Trash purge: the graph refused a page of %d record(s) in org %s; retrying one by one: %s",
                len(ids), org_id, exc,
            )
            purged, kept = await self._purge_one_by_one(org_id, ids, run, settings)
        run.purged += len(purged)
        run.pass_purged += len(purged)
        run.kept += len(kept)
        record_purged("purged", len(purged))
        record_purged("kept", len(kept))
        owed = {row["id"]: cleanup_owed(row) for row in purged}
        if owed and not await self._publish(org_id, owed):
            # Only what is gone from the graph stays owed; the next tick publishes it.
            await self._save_outbox(outbox_key, org_id, owed)
            return False
        await self._forget_outbox(outbox_key)
        self.logger.debug(
            "Trash purge %s: org %s page of %d, %d purged, %d kept",
            run.run_id, org_id, len(ids), len(purged), len(kept),
        )
        return True

    async def _purge_one_by_one(
        self, org_id: str, ids: list[str], run: PurgeRun, settings: PurgeSettings
    ) -> tuple[list[dict], list[str]]:
        purged: list[dict] = []
        kept: list[str] = []
        for record_id in ids:
            try:
                result = await self.graph.purge_trashed_records(
                    [record_id], org_id, run.cutoff, max_attempts=settings.max_attempts
                )
            except GraphLockUnavailableError:
                raise
            except Exception as exc:
                self._failed.add(record_id)
                run.failed += 1
                record_purged("failed", 1)
                self.logger.warning(
                    "Trash purge: could not remove record %s (org %s) from the graph: %s", record_id, org_id, exc
                )
                try:
                    await self.graph.record_purge_failure([record_id], org_id, _short(exc))
                except Exception as count_exc:
                    raise TrashPurgeError(
                        f"could not count the failed purge of record {record_id}: {count_exc}"
                    ) from count_exc
                continue
            purged.extend(result.get("purged") or [])
            kept.extend(result.get("kept") or [])
        return purged, kept

    async def _publish(self, org_id: str, owed: dict[str, dict]) -> bool:
        """The hard delete's events for records gone from the graph; False if any was refused."""
        now = self.clock()
        messages: list[tuple[str | None, dict]] = [
            (
                record_id,
                {"eventType": EventTypes.DELETE_RECORD.value, "timestamp": now, "payload": cleanup["deleteRecord"]},
            )
            for record_id, cleanup in owed.items()
        ]
        documents: dict[str, list[str]] = defaultdict(list)
        for cleanup in owed.values():
            if cleanup.get("connectorId"):
                documents[cleanup["connectorId"]].extend(cleanup.get("documentIds") or [])
        for connector_id, document_ids in documents.items():
            messages.extend(
                (connector_id, event)
                for event in build_stored_document_cleanup_events(
                    org_id=org_id, document_ids=document_ids, connector_id=connector_id
                )
            )
        try:
            results = await self.producer.send_messages(RECORD_EVENTS_TOPIC, messages)
        except Exception as exc:
            self.logger.error(
                "Trash purge: could not publish the cleanup of %d purged record(s) in org %s; "
                "it stays owed: %s", len(owed), org_id, exc,
            )
            return False
        refused = sum(1 for ok in results if not ok)
        if refused:
            self.logger.error(
                "Trash purge: the broker refused %d of %d cleanup event(s) for org %s; they stay owed",
                refused, len(messages), org_id,
            )
            return False
        return True

    async def _drain_outbox(self) -> bool:
        """Publish what an interrupted page still owes; False when the broker refuses it."""
        for key in await self.config_service.list_keys_in_directory(OUTBOX_DIRECTORY):
            entry = await self.config_service.get_config(key, use_cache=False, raise_on_error=True)
            items = entry.get("items") if isinstance(entry, dict) else None
            org_id = entry.get("orgId") if isinstance(entry, dict) else None
            if not org_id or not isinstance(items, dict):
                self.logger.error("Trash purge: dropping malformed outbox entry %s", key)
                await self._forget_outbox(key)
                continue
            # Still there means the page never reached the graph delete, or the
            # record was restored; either way it owes nothing.
            gone = {
                record_id: cleanup
                for record_id, cleanup in items.items()
                if await self.graph.get_document(record_id, CollectionNames.RECORDS.value, raise_on_error=True) is None
            }
            if gone and not await self._publish(org_id, gone):
                return False
            self.logger.info(
                "Trash purge: finished an interrupted page in org %s, %d record(s) cleaned up", org_id, len(gone)
            )
            await self._forget_outbox(key)
        return True

    async def _purge_kept_groups(self, org_id: str) -> int:
        removed = 0
        try:
            # Until a pass removes nothing: a kept parent empties only once its kept child has gone.
            while ids := await self.graph.purge_trash_kept_record_groups(org_id, limit=GROUP_PAGE_SIZE):
                removed += len(ids)
        except Exception as exc:
            # The marks stay, so the next run tries these groups again.
            self.logger.warning("Trash purge: could not remove kept record groups in org %s: %s", org_id, exc)
        if removed:
            self.logger.info("Trash purge: removed %d record group(s) kept for the trash in org %s", removed, org_id)
        return removed

    async def _finish(self, state: PurgeState, settings: PurgeSettings) -> None:
        run = state.run
        assert run is not None
        now = self.clock()
        state.run = None
        state.last_finished_at = now
        state.last_counts = {"purged": run.purged, "kept": run.kept, "failed": run.failed, "groups": run.groups}
        await self._save_state(state)
        duration = max(0.0, (now - run.started_at) / 1000)
        record_purge_run(Outcome.FINISHED, duration)
        self.logger.info(
            "Trash purge %s finished in %.0fs: %d purged, %d kept, %d failed, %d record group(s) removed",
            run.run_id, duration, run.purged, run.kept, run.failed, run.groups,
        )
        await self._refresh_backlog(run.org_ids, settings, now)

    async def _refresh_backlog(self, org_ids: list[str], settings: PurgeSettings, now: int) -> None:
        pending = stuck = 0
        oldest: int | None = None
        try:
            for org_id in org_ids:
                stats = await self.graph.get_trash_purge_stats(org_id, settings.max_attempts)
                stuck += stats["stuck"]
                pending += stats["trashed"] - stats["stuck"]
                if stats["oldestDeletedAt"] is not None:
                    oldest = stats["oldestDeletedAt"] if oldest is None else min(oldest, stats["oldestDeletedAt"])
        except Exception as exc:
            self.logger.warning("Trash purge: could not read the trash backlog: %s", exc)
            return
        set_trash_backlog(pending, stuck, max(0.0, (now - oldest) / 1000) if oldest is not None else 0.0)
        if stuck:
            self.logger.error(
                "Trash purge: %d record(s) failed %d purges and are left in the trash; see purgeLastError",
                stuck, settings.max_attempts,
            )

    async def _org_ids(self) -> list[str]:
        orgs = [
            *await self.graph.get_all_orgs(active=False),
            *await self.graph.get_all_orgs(active=False, is_external=True),
        ]
        return sorted({str(o.get("_key") or o.get("id")) for o in orgs if o.get("_key") or o.get("id")})

    async def _read_state(self) -> PurgeState:
        raw = await self.config_service.get_config(STATE_KEY, use_cache=False, raise_on_error=True)
        return PurgeState.from_dict(raw)

    async def _save_state(self, state: PurgeState) -> None:
        if not await self.config_service.set_config(STATE_KEY, state.to_dict()):
            raise TrashPurgeError("could not save the purge's progress")

    async def _save_outbox(self, key: str, org_id: str, items: dict[str, dict]) -> None:
        # Before the graph delete: once a record is gone, this is the only record of what it owed.
        if not await self.config_service.set_config(
            key, {"orgId": org_id, "createdAt": self.clock(), "items": items}
        ):
            raise TrashPurgeError("could not save the cleanup a purge page owes")

    async def _forget_outbox(self, key: str) -> None:
        if not await self.config_service.delete_config(key):
            # Harmless: the next drain finds those records gone and publishes their
            # cleanup again, which is idempotent.
            self.logger.warning("Trash purge: could not delete outbox entry %s", key)


async def run_trash_purge_loop(
    app_container: ConnectorAppContainer,
    graph_provider: IGraphDBProvider,
    *,
    sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep,
) -> None:
    """Tick the purge every hour (``SOFT_DELETE_PURGE_TICK_SECONDS``) until cancelled."""
    logger = app_container.logger()
    tick_seconds = _env_seconds("SOFT_DELETE_PURGE_TICK_SECONDS") or 3600.0
    grace = _env_seconds("SOFT_DELETE_PURGE_STARTUP_GRACE_SECONDS")
    await sleep(120.0 if grace is None else grace)
    owner = f"purge:{uuid4().hex}"
    lock: VectorMembershipBackfillLeaderLock | None = None
    backoff = 1
    try:
        while True:
            try:
                producer = getattr(app_container, "messaging_producer", None)
                if producer is None:
                    logger.warning("Trash purge skipped this tick; the message producer is not ready")
                else:
                    if lock is None:
                        lock = VectorMembershipBackfillLeaderLock(
                            logger,
                            await MessagingUtils._get_redis_config(app_container),
                            owner,
                            ttl_seconds=LEASE_TTL_SECONDS,
                            key=LEADER_KEY,
                        )
                    purger = TrashPurger(logger, graph_provider, app_container.config_service(), producer, lock)
                    await purger.tick()
                backoff = 1
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Trash purge tick failed; it resumes from its cursor")
                record_purge_run("failed")
                if lock is not None:
                    await lock.close()
                    lock = None
                backoff = min(backoff * 2, 4)
            await sleep(tick_seconds * backoff)
    finally:
        if lock is not None:
            await lock.release()
            await lock.close()
