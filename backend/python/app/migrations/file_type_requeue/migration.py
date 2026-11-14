"""Re-queue the records the indexer refused as "file type not supported" that it can read now.

A record refused at the gate stays FILE_TYPE_NOT_SUPPORTED until its file changes, so a type the
registry learns to read would never reach the files already refused. This walks every connector's
refused records once per registry version and selects the ones the frozen previous gate
(``gate_v1``) refused and today's gate accepts. Nothing else is touched: a type still refused, a
type turned off, a file over the download ceiling, a placeholder, an internal or a trashed record
stays as it is.

What a selected record becomes:
- A connector in manual indexing (or with files not indexed automatically): AUTO_INDEX_OFF with
  no reason, which leaves it for a person to index. No event is sent.
- A network-share connector (``REQUEUE_PARKED``): AUTO_INDEX_OFF with no reason, then the
  connector's ``requeue_records`` takes back only the files that are on disk. A file moved to
  slower storage is never recalled, and if the event is lost the files stay manually indexable
  (the stranded-record sweep does not read reasonless AUTO_INDEX_OFF).
- Any other connector: QUEUED, then the connector's ``requeue_records``.

The status moves first, in one compare-and-set from FILE_TYPE_NOT_SUPPORTED, and only the ids it
won are sent: a record the indexer moved on meanwhile is never taken back.

Every move is recoverable by this migration itself, whatever the record's origin: an outbox entry
naming the page is saved before the move and kept until none of its records waits where the move
left it. A crash, a lost or refused publish, or an event the connector did not act on is sent again
by the next start's drain. The stranded-record sweep is not relied on, since it leaves uploads alone.

One replica runs it, under a leader lease. ``FILE_TYPE_REQUEUE=false`` skips it without writing
the flag. The flag is written only after every connector finished, every event was sent and the
lease was held throughout; otherwise the next start runs it again, which moves nothing twice.
"""
from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from app.config import file_types
from app.config.constants.arangodb import CollectionNames, ProgressStatus, RecordTypes
from app.connectors.core.registry.filters import (
    IndexingFilterKey,
    load_connector_filters,
)
from app.connectors.services.reindex_events import build_reindex_event
from app.core.org_context import for_org, system
from app.migrations.file_type_requeue import gate_v1
from app.modules.indexing.connector_off_events import ConnectorState, connector_state
from app.utils.env_utils import env_bool
from app.utils.time_conversion import get_epoch_timestamp_in_ms

if TYPE_CHECKING:
    from collections.abc import Callable
    from logging import Logger

    from app.config.configuration_service import ConfigurationService
    from app.models.entities import Record
    from app.modules.indexing.vector_membership_backfill import LeaderLock
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

__all__ = [
    "FLAG_KEY",
    "OUTBOX_DIRECTORY",
    "LEASE_KEY",
    "MAX_RESENDS",
    "PAGE_SIZE",
    "RequeueResult",
    "count_still_selected",
    "is_selected",
    "run_file_type_requeue",
]

FLAG_KEY = "/migrations/file_type_requeue"
# One entry per moved page until its records are no longer waiting (see _move_and_send).
OUTBOX_DIRECTORY = "/migrations/file_type_requeue_outbox/"
# Re-sends of a page whose event was sent but whose records still wait, before it is given up.
MAX_RESENDS = 5
_RESEND_WAIT_CAP_MS = 24 * 3600 * 1000
LEASE_KEY = "pipeshub:migrations:file_type_requeue"
LEASE_TTL_SECONDS = 600
# Also the most ids one sync event carries.
PAGE_SIZE = 500
_APPS_PAGE = 200
_PAUSE_SECONDS = 0.5
_JOB = "file_type_requeue"


@dataclass
class RequeueResult:
    ran: bool = False
    complete: bool = False
    requeued: int = 0
    parked: int = 0
    skipped_disabled: set[str] = field(default_factory=set)
    events: int = 0


def _gate_inputs(record: Record, stored_mime: object) -> dict[str, Any]:
    """What the gate reads for this record's reindex event: the event's fields, the stored mime when
    the event has none, and the CODE_FILE extension from the record name, as the handler derives them."""
    payload = record.to_kafka_record()
    mime = payload.get("mimeType", "unknown")
    if mime == "unknown" or not mime:
        mime = stored_mime or "unknown"
    return {
        "extension": payload.get("extension", "unknown"),
        "mime": mime,
        "record_name": payload.get("recordName"),
        "is_code_file": getattr(record.record_type, "value", record.record_type) == RecordTypes.CODE_FILE.value,
    }


def is_selected(record: Record, row: dict[str, Any], disabled: frozenset[str]) -> tuple[bool, str | None]:
    """Whether the record is re-queued, and the type key that held it back when it is turned off."""
    if record.is_internal or record.is_placeholder or record.is_deleted or row.get("isInternal") or row.get("isPlaceholder"):
        return False, None
    inputs = _gate_inputs(record, row.get("mimeType"))
    if gate_v1.supported(**inputs) is not False:
        return False, None
    name = inputs["record_name"]
    code_file_extension = (
        name.rsplit(".", 1)[-1].lower()
        if inputs["is_code_file"] and isinstance(name, str) and "." in name
        else None
    )
    try:
        decision = file_types.gate_decision(
            mime=inputs["mime"],
            extension=inputs["extension"],
            record_name=name if isinstance(name, str) else None,
            is_code_file=inputs["is_code_file"],
            code_file_extension=code_file_extension,
            size_bytes=row.get("sizeInBytes"),
            disabled=disabled,
        )
    except (TypeError, AttributeError):
        return False, None
    if decision.refusal == "disabled" and decision.file_type is not None:
        return False, decision.file_type.key
    return decision.supported, None


def _network_share(connector_type: str) -> bool:
    from app.connectors.core.factory.connector_factory import ConnectorFactory

    connector_class = ConnectorFactory.get_connector_class(connector_type)
    return bool(getattr(connector_class, "REQUEUE_PARKED", False))


async def _disabled_types(config_service: ConfigurationService) -> frozenset[str]:
    """The types turned off, read as the indexer reads them. Raises when the setting cannot be read."""
    config = await config_service.get_config(file_types.FILE_TYPES_CONFIG_KEY, use_cache=False)
    if config is None:
        config = file_types.disabled_from_env(os.environ.get("FILE_TYPES_DISABLED"))
    return file_types.disabled_keys(config)


def _flag_wants_run(flag: object, disabled: frozenset[str]) -> bool:
    if not isinstance(flag, dict):
        return True
    version = flag.get("version")
    if not isinstance(version, int) or version < file_types.REGISTRY_VERSION:
        return True
    held_back = {k for k in flag.get("skipped_disabled") or [] if isinstance(k, str)}
    return bool(held_back - disabled)


async def _list_apps(graph_provider: IGraphDBProvider) -> list[dict[str, Any]]:
    apps: list[dict[str, Any]] = []
    after: str | None = None
    while True:
        with system(_JOB):
            page = await graph_provider.get_documents_paginated(
                CollectionNames.APPS.value, limit=_APPS_PAGE, sort_field="_key", after_key=after, raise_on_error=True,
            )
        apps.extend(page or [])
        if not page or len(page) < _APPS_PAGE:
            return apps
        after = page[-1].get("_key") or page[-1].get("id")
        if not after:
            return apps


async def _parks_everything(config_service: ConfigurationService, connector_type: str, connector_id: str,
                            logger: Logger) -> tuple[bool, bool]:
    """(manual, files off) for the connector. Raises when its settings cannot be read, so an unknown
    setting never reads as "index automatically"."""
    await config_service.get_config(f"/services/connectors/{connector_id}/config")
    _, indexing = await load_connector_filters(config_service, connector_type, connector_id, logger)
    manual = indexing.is_enabled(IndexingFilterKey.ENABLE_MANUAL_SYNC, default=False)
    files_off = not indexing.is_enabled(IndexingFilterKey.FILES, default=True)
    return manual, files_off


async def _walk_connector(
    config_service: ConfigurationService,
    graph_provider: IGraphDBProvider,
    kafka_service: Any,
    lock: LeaderLock,
    logger: Logger,
    *,
    org_id: str,
    connector_id: str,
    connector_type: str,
    manual: bool,
    files_off: bool,
    parked_connector: bool,
    disabled: frozenset[str],
    result: RequeueResult,
    pause_seconds: float,
) -> bool:
    """One pass over the connector's refused records. False when an event was not sent or the lease was lost."""
    sent_all = True
    after: str | None = None
    while True:
        with for_org(org_id, job=_JOB):
            page = await graph_provider.list_unsupported_page(org_id, connector_id, after, PAGE_SIZE)
            if not page:
                return sent_all
            after = page[-1].get("_key")
            typed = await graph_provider.get_typed_records_batch([r["_key"] for r in page], raise_on_error=True)
            picks: list[tuple[Record, dict[str, Any]]] = []
            for row in page:
                record = typed.get(row["_key"])
                if record is None:
                    continue
                chosen, held_back = is_selected(record, row, disabled)
                if held_back:
                    result.skipped_disabled.add(held_back)
                if chosen:
                    picks.append((record, row))
            to_park = [r.id for r, row in picks if manual or (files_off and row.get("recordType") == RecordTypes.FILE.value)]
            parked_now = set(to_park)
            rest = [r.id for r, _ in picks if r.id not in parked_now]
            if to_park:
                result.parked += len(await graph_provider.transition_unsupported(to_park, ProgressStatus.AUTO_INDEX_OFF.value))
            won: list[str] = []
            if rest:
                won, sent = await _move_and_send(
                    config_service, graph_provider, kafka_service, logger, org_id=org_id, connector_id=connector_id,
                    connector_type=connector_type, ids=rest, parked=parked_connector,
                )
                if parked_connector:
                    result.parked += len(won)
                else:
                    result.requeued += len(won)
                if sent:
                    result.events += 1
                elif sent is False:
                    sent_all = False
        if not await lock.refresh():
            raise _LeaseLost
        if won and pause_seconds:
            await asyncio.sleep(pause_seconds)
        if len(page) < PAGE_SIZE or not after:
            return sent_all


def _waiting(doc: dict[str, Any], *, parked: bool) -> bool:
    """Still where the move left it: QUEUED, or parked without a reason, and not trashed."""
    if doc.get("isDeleted") is True:
        return False
    if parked:
        return doc.get("indexingStatus") == ProgressStatus.AUTO_INDEX_OFF.value and not doc.get("reason")
    return doc.get("indexingStatus") == ProgressStatus.QUEUED.value


async def _publish(kafka_service: Any, logger: Logger, *, org_id: str, connector_id: str,
                   connector_type: str, ids: list[str]) -> bool:
    event = build_reindex_event(
        event_type=f"{connector_type}.reindex", org_id=org_id, connector_id=connector_id,
        connector_name=connector_type, record_ids=ids,
    )
    try:
        sent = bool(await kafka_service.publish_event(event["topic"], {
            "eventType": event["eventType"],
            "timestamp": get_epoch_timestamp_in_ms(),
            "payload": event["payload"],
        }))
    except Exception:
        logger.warning("File-type re-queue: the event for %d record(s) of %s raised", len(ids), connector_id, exc_info=True)
        return False
    if not sent:
        logger.warning("File-type re-queue: the event for %d record(s) of %s was not sent", len(ids), connector_id)
    return sent


async def _save_entry(config_service: ConfigurationService, key: str, entry: dict[str, Any]) -> bool:
    try:
        return bool(await config_service.set_config(key, entry))
    except Exception:
        return False


async def _drop_entry(config_service: ConfigurationService, key: str, logger: Logger) -> None:
    try:
        await config_service.delete_config(key)
    except Exception:
        # Harmless: the next drain finds its records moved on and drops it then.
        logger.warning("File-type re-queue: could not delete outbox entry %s", key, exc_info=True)


async def _move_and_send(
    config_service: ConfigurationService,
    graph_provider: IGraphDBProvider,
    kafka_service: Any,
    logger: Logger,
    *,
    org_id: str,
    connector_id: str,
    connector_type: str,
    ids: list[str],
    parked: bool,
) -> tuple[list[str], bool | None]:
    """Move the ids out of FILE_TYPE_NOT_SUPPORTED and send their event, behind an outbox entry.

    The entry is saved before the move, so a crash, a lost or refused publish, or an event the
    connector could not act on leaves a record of what was moved; the next start's drain sends it
    again. Returns the ids moved and whether the event was sent (None when nothing moved).
    """
    key = f"{OUTBOX_DIRECTORY}{uuid4().hex}"
    entry: dict[str, Any] = {
        "orgId": org_id, "connectorId": connector_id, "connectorType": connector_type,
        "parked": parked, "ids": ids, "sent": False,
    }
    if not await _save_entry(config_service, key, entry):
        logger.warning("File-type re-queue: the outbox entry for %d record(s) of %s was not saved; not moved", len(ids), connector_id)
        return [], False
    target = ProgressStatus.AUTO_INDEX_OFF.value if parked else ProgressStatus.QUEUED.value
    won = await graph_provider.transition_unsupported(ids, target)
    if not won:
        await _drop_entry(config_service, key, logger)
        return [], None
    entry["ids"] = won
    await _save_entry(config_service, key, entry)  # a superset left behind only costs the drain a read
    sent = await _publish(kafka_service, logger, org_id=org_id, connector_id=connector_id,
                          connector_type=connector_type, ids=won)
    if sent:
        if parked:
            # A share's connector takes back only files on disk; the rest are left for manual indexing.
            await _drop_entry(config_service, key, logger)
        else:
            entry["sent"] = True
            entry["sentAt"] = get_epoch_timestamp_in_ms()
            await _save_entry(config_service, key, entry)
    return won, sent


def _stranded_after_ms() -> int:
    try:
        return int(float(os.environ.get("STRANDED_RECORD_REPUBLISH_AFTER_SECONDS", "3600")) * 1000)
    except ValueError:
        return 3600 * 1000


def _resend_wait_ms(attempts: int) -> int:
    """How long after its last send a still-waiting record is sent again: the stranded-record age,
    doubled for each send already made, capped at a day."""
    return min(_stranded_after_ms() * (2 ** max(0, attempts)), _RESEND_WAIT_CAP_MS)


async def _drain_outbox(
    config_service: ConfigurationService,
    graph_provider: IGraphDBProvider,
    kafka_service: Any,
    lock: LeaderLock,
    logger: Logger,
    keys: list[str],
) -> bool:
    """Send again what an earlier run moved and that is still waiting. False while anything is left unsent.

    An entry whose event was not sent is sent again for every record still where the move left it.
    One whose event was sent (the broker took it, but the connector may not have acted on it: an
    upload is not one the stranded-record sweep re-sends) is sent again only for records still
    QUEUED whose last send, or last queueing if later, is older than ``_resend_wait_ms(sends)``.
    A re-send does not move ``queuedAtTimestamp``, so the entry keeps its own ``sentAt`` and count.
    After ``MAX_RESENDS`` the entry is dropped and logged: the records stay QUEUED for the
    stranded-record sweep or a reindex from the UI. An entry is dropped once none of its records waits.
    """
    now = get_epoch_timestamp_in_ms()
    drained = True
    for key in keys:
        try:
            entry = await config_service.get_config(key, use_cache=False)
            org_id = entry.get("orgId") if isinstance(entry, dict) else None
            connector_id = entry.get("connectorId") if isinstance(entry, dict) else None
            connector_type = entry.get("connectorType") if isinstance(entry, dict) else None
            ids = [i for i in (entry.get("ids") or []) if isinstance(i, str)] if isinstance(entry, dict) else []
            if not (org_id and connector_id and connector_type and ids):
                logger.error("File-type re-queue: dropping malformed outbox entry %s", key)
                await _drop_entry(config_service, key, logger)
                continue
            parked = entry.get("parked") is True
            with for_org(org_id, job=_JOB):
                docs = await graph_provider.get_records_by_record_ids(ids, org_id)
            waiting = [d for d in docs or [] if _waiting(d, parked=parked)]
            waiting_ids = [str(d.get("_key") or d.get("id")) for d in waiting]
            if not waiting_ids:
                await _drop_entry(config_service, key, logger)
                continue
            resends = entry.get("resends") if isinstance(entry.get("resends"), int) else 0
            if entry.get("sent") is True:
                if parked:
                    await _drop_entry(config_service, key, logger)
                    continue
                if resends >= MAX_RESENDS:
                    logger.warning(
                        "File-type re-queue: %d record(s) of %s still queued after %d re-sends; left to the "
                        "stranded-record sweep or a reindex", len(waiting_ids), connector_id, resends,
                    )
                    await _drop_entry(config_service, key, logger)
                    continue
                last_sent = entry.get("sentAt") if isinstance(entry.get("sentAt"), int) else 0
                wait = _resend_wait_ms(resends)
                resend = [str(d.get("_key") or d.get("id")) for d in waiting
                          if now - max(int(d.get("queuedAtTimestamp") or 0), last_sent) >= wait]
            else:
                resend = waiting_ids
            entry["ids"] = waiting_ids
            if resend:
                with for_org(org_id, job=_JOB):
                    sent = await _publish(kafka_service, logger, org_id=org_id, connector_id=connector_id,
                                          connector_type=connector_type, ids=resend)
                if not sent:
                    drained = False
                elif parked:
                    await _drop_entry(config_service, key, logger)
                    continue
                else:
                    if entry.get("sent") is True:
                        entry["resends"] = resends + 1
                    entry["sent"], entry["sentAt"] = True, now
            await _save_entry(config_service, key, entry)
        except _LeaseLost:
            raise
        except Exception:
            drained = False
            logger.exception("File-type re-queue: outbox entry %s could not be drained; the next start retries it", key)
        if not await lock.refresh():
            raise _LeaseLost
    return drained


class _LeaseLost(Exception):
    pass


async def count_still_selected(
    graph_provider: IGraphDBProvider, disabled: frozenset[str] = frozenset()
) -> int:
    """How many refused records the selection would still take, over every connector (0 after a full run)."""
    count = 0
    for app in await _list_apps(graph_provider):
        org_id, connector_id = app.get("orgId"), app.get("_key") or app.get("id")
        if not org_id or not connector_id:
            continue
        after: str | None = None
        while True:
            with for_org(org_id, job=_JOB):
                page = await graph_provider.list_unsupported_page(org_id, connector_id, after, PAGE_SIZE)
                if not page:
                    break
                typed = await graph_provider.get_typed_records_batch([r["_key"] for r in page], raise_on_error=True)
            count += sum(1 for row in page if (rec := typed.get(row["_key"])) and is_selected(rec, row, disabled)[0])
            after = page[-1].get("_key")
            if len(page) < PAGE_SIZE or not after:
                break
    return count


async def run_file_type_requeue(
    config_service: ConfigurationService,
    graph_provider: IGraphDBProvider,
    kafka_service: Any,
    logger: Logger,
    *,
    lock: LeaderLock,
    is_network_share: Callable[[str], bool] = _network_share,
    pause_seconds: float = _PAUSE_SECONDS,
) -> RequeueResult:
    """Run the re-queue once for this registry version, on the replica that holds the lease."""
    result = RequeueResult()
    if not env_bool("FILE_TYPE_REQUEUE", True):
        logger.info("File-type re-queue skipped: FILE_TYPE_REQUEUE is false")
        return result
    try:
        disabled = await _disabled_types(config_service)
    except Exception:
        logger.warning("File-type re-queue skipped: the disabled file types could not be read", exc_info=True)
        return result
    try:
        flag = await config_service.get_config(FLAG_KEY, use_cache=False)
    except Exception:
        flag = None
    try:
        pending = await config_service.list_keys_in_directory(OUTBOX_DIRECTORY)
    except Exception:
        logger.warning("File-type re-queue: its outbox could not be listed", exc_info=True)
        pending = []
    walk = _flag_wants_run(flag, disabled)
    if not walk and not pending:
        return result
    if not await lock.try_acquire():
        # Another replica runs it, or the lock backend is down: either way this one must not.
        logger.warning("File-type re-queue skipped: the lease is held elsewhere or the lock backend is unavailable")
        return result

    result.ran = True
    complete = True
    try:
        if pending:
            complete = await _drain_outbox(config_service, graph_provider, kafka_service, lock, logger, pending)
        if walk:
            complete = await _walk_all(
                config_service, graph_provider, kafka_service, lock, logger,
                is_network_share=is_network_share, disabled=disabled, result=result, pause_seconds=pause_seconds,
            ) and complete
            result.complete = complete
            if complete:
                # Before the lease goes: a replica that takes it next must find the run done.
                await _save_flag(config_service, result, logger)
                await _log_left(graph_provider, disabled, logger)
        else:
            result.complete = complete
    except _LeaseLost:
        logger.warning("File-type re-queue stopped: the lease was lost; the next start resumes it")
        result.complete = False
    finally:
        await lock.release()

    logger.info(
        "File-type re-queue: %d record(s) queued, %d left for manual indexing, %d event(s), types held back: %s",
        result.requeued, result.parked, result.events, sorted(result.skipped_disabled) or "none",
    )
    return result


async def _walk_all(
    config_service: ConfigurationService,
    graph_provider: IGraphDBProvider,
    kafka_service: Any,
    lock: LeaderLock,
    logger: Logger,
    *,
    is_network_share: Callable[[str], bool],
    disabled: frozenset[str],
    result: RequeueResult,
    pause_seconds: float,
) -> bool:
    complete = True
    for app in await _list_apps(graph_provider):
        org_id = app.get("orgId")
        connector_id = app.get("_key") or app.get("id")
        connector_type = str(app.get("type") or "").replace(" ", "").lower()
        if not org_id or not connector_id or not connector_type:
            logger.warning("File-type re-queue: connector %s has no org or type; skipped", connector_id)
            continue
        if connector_state(app) in (ConnectorState.DELETING, ConnectorState.REMOVED):
            continue
        try:
            with for_org(org_id, job=_JOB):
                manual, files_off = await _parks_everything(config_service, connector_type, connector_id, logger)
            sent_all = await _walk_connector(
                config_service, graph_provider, kafka_service, lock, logger,
                org_id=org_id, connector_id=connector_id, connector_type=connector_type,
                manual=manual, files_off=files_off, parked_connector=is_network_share(connector_type),
                disabled=disabled, result=result, pause_seconds=pause_seconds,
            )
            complete = complete and sent_all
        except _LeaseLost:
            raise
        except Exception:
            complete = False
            logger.exception("File-type re-queue: connector %s failed; the next start resumes it", connector_id)
    return complete


async def _save_flag(config_service: ConfigurationService, result: RequeueResult, logger: Logger) -> None:
    saved = await _save_entry(config_service, FLAG_KEY, {
        "version": file_types.REGISTRY_VERSION,
        "skipped_disabled": sorted(result.skipped_disabled),
        "requeued": result.requeued,
        "parked": result.parked,
        "timestamp": get_epoch_timestamp_in_ms(),
    })
    if not saved:
        logger.warning("File-type re-queue finished but its flag was not saved; the next start looks again")


async def _log_left(graph_provider: IGraphDBProvider, disabled: frozenset[str], logger: Logger) -> None:
    try:
        left = await count_still_selected(graph_provider, disabled)
        (logger.warning if left else logger.info)("File-type re-queue check: %d selectable record(s) left", left)
    except Exception:
        logger.warning("File-type re-queue check could not count what is left", exc_info=True)
