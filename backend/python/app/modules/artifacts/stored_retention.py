"""Retire the documents stored writes replace, and collect the ones nothing names.

Under the stored-write protocol a copy-on-write switch never deletes the document it replaces: it
appends ``{doc, rev, sidecar, at}`` to the mapping's ``retired`` list and marks the document retired
on the storage service (``retiredAt``, the server's clock). This sweep, run by the cluster-wide
recovery leader, finishes the job:

* **Retirement sweep.** For each mapping with a non-empty ``retired`` list it re-reads the mapping and
  purges an entry only when all of these hold: the document is not the mapped one; the document
  carries ``retiredAt`` and ``now - retiredAt >= GR`` (so a late in-place write is fenced for at
  least GR before the purge); and ``now - at >= GR``. A document without a mark is marked now and
  not purged on the same visit. While the retire route is unavailable the entry is held
  (``held_no_fence``). The purged entries leave the list by a compare-and-set on ``switchSeq`` that
  does not bump it, so a sweep never makes a writer retry. A mapping that is itself a fingerprint of
  an older writer (``documentId != switchDoc`` or ``updatedAt != switchUpdatedAt``) is skipped.
* **Stray pass.** At most ``STRAYS_PER_TICK`` mappings per tick, walked by a cursor of its own so every
  mapping is reached in turn and none is listed twice in one cycle: ``record_<vrid>`` documents older than
  G by the database's clock (the storage service filters on its numeric ``createdAt``) that are
  neither the mapped document nor in ``retired`` are purged, after re-reading the mapping. Mappings
  switched within GR are left alone (the quiet period), and a mapping with no document is left to
  the vrid purge. A writer that has been running longer than ``WRITER_DEADLINE_SECONDS`` does not
  switch, so a document the pass may purge is never switched afterwards.

Both pause while the indexing members are not all known to follow the protocol (an untagged member
is live, or the membership result is stale): the pause protects data, nothing needs re-arming.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from app.config.constants.arangodb import CollectionNames
from app.modules.transformers.stored_write import retired_list
from app.telemetry.modules.record_storage_metrics import record_retention
from app.utils.time_conversion import get_epoch_timestamp_in_ms

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from logging import Logger

    from app.modules.transformers.blob_storage import BlobStorage
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
    from app.services.messaging.membership import MembershipVerdict

#: GR: how long a retired document is fenced, and how old a retired entry must be, before its purge.
RETIRE_GRACE_MS = 10 * 60 * 1000
#: G: how old an unnamed ``record_<vrid>`` document must be before the stray pass purges it.
STRAY_GRACE_SECONDS = 60 * 60
STRAYS_PER_TICK = 20
DEFAULT_PAGE_SIZE = 200
MAPPING = CollectionNames.VIRTUAL_RECORD_TO_DOC_ID_MAPPING.value


@dataclass
class StoredRetentionReport:
    scanned: int = 0
    purged: int = 0
    marked: int = 0
    held_no_fence: int = 0
    fingerprints: int = 0
    strays_purged: int = 0
    paused: bool = False
    failed: int = 0
    interrupted: bool = False

    def add(self, other: StoredRetentionReport) -> None:
        for name in ("scanned", "purged", "marked", "held_no_fence", "fingerprints", "strays_purged", "failed"):
            setattr(self, name, getattr(self, name) + getattr(other, name))
        self.paused = self.paused or other.paused
        self.interrupted = self.interrupted or other.interrupted


class StoredRecordRetention:
    def __init__(
        self,
        graph_provider: IGraphDBProvider,
        blob: BlobStorage,
        logger: Logger,
        *,
        membership: Callable[[], Awaitable[MembershipVerdict]],
        grace_ms: int = RETIRE_GRACE_MS,
        stray_grace_seconds: int = STRAY_GRACE_SECONDS,
        page_size: int = DEFAULT_PAGE_SIZE,
        strays_per_tick: int = STRAYS_PER_TICK,
        now_ms: Callable[[], int] = get_epoch_timestamp_in_ms,
    ) -> None:
        self.graph = graph_provider
        self.blob = blob
        self.logger = logger
        self._membership = membership
        self.grace_ms = grace_ms
        self.stray_grace_seconds = stray_grace_seconds
        self.page_size = max(1, page_size)
        self.strays_per_tick = max(0, strays_per_tick)
        self._now_ms = now_ms

    async def run_page(
        self, after_key: str | None, *, still_leader: Callable[[], bool] | None = None,
    ) -> tuple[str | None, StoredRetentionReport]:
        """One page of mappings after ``after_key``; returns the key to continue from (None at the end)."""
        report = StoredRetentionReport()
        pointers = await self.graph.get_switch_pointers(after_key, self.page_size)
        if not pointers:
            return None, report
        report.scanned = len(pointers)
        for pointer in pointers:
            if await self._halted(report, still_leader):
                return after_key, report
            target = self._target(pointer)
            if target is None:
                continue
            vrid, org_id = target
            try:
                if retired_list(pointer):
                    await self._retire_entries(vrid, org_id, report)
            except Exception as exc:
                report.failed += 1
                record_retention("stored_record", "failed")
                self.logger.warning(
                    "Stored-record retention: %s not finished; next pass retries: %s: %s",
                    vrid, type(exc).__name__, exc,
                )
        next_key = str(pointers[-1]["virtualRecordId"]) if len(pointers) >= self.page_size else None
        return next_key, report

    async def run_strays(
        self, after_key: str | None, *, still_leader: Callable[[], bool] | None = None,
    ) -> tuple[str | None, StoredRetentionReport]:
        """The stray pass over up to ``strays_per_tick`` mappings after ``after_key``; returns the key to
        continue from (None at the end, so the next call starts over)."""
        report = StoredRetentionReport()
        pointers = await self.graph.get_switch_pointers(after_key, self.strays_per_tick)
        if not pointers:
            return None, report
        for pointer in pointers:
            if await self._halted(report, still_leader):
                return after_key, report
            target = self._target(pointer)
            if target is None:
                continue
            vrid, org_id = target
            after_key = vrid
            if not pointer.get("documentId"):
                continue
            try:
                await self._collect_strays(vrid, org_id, report)
            except Exception as exc:
                report.failed += 1
                record_retention("stored_record", "failed")
                self.logger.warning(
                    "Stored-record stray pass: %s not finished; next cycle retries: %s: %s",
                    vrid, type(exc).__name__, exc,
                )
        next_key = str(pointers[-1]["virtualRecordId"]) if len(pointers) >= self.strays_per_tick else None
        return next_key, report

    async def _halted(self, report: StoredRetentionReport, still_leader: Callable[[], bool] | None) -> bool:
        if still_leader is not None and not still_leader():
            report.interrupted = True
            return True
        # Before every batch: a member that joined meanwhile pauses the rest.
        verdict = await self._membership()
        if not verdict.ok:
            report.paused = True
            record_retention("stored_record", "paused_membership")
            return True
        return False

    @staticmethod
    def _target(pointer: dict[str, Any]) -> tuple[str, str] | None:
        vrid = str(pointer.get("virtualRecordId") or "")
        org_id = pointer.get("orgId")
        return (vrid, str(org_id)) if vrid and org_id else None

    @staticmethod
    def _is_fingerprint(mapping: dict[str, Any]) -> bool:
        """The mapping was last written by a writer that does not follow the protocol (or a reconciliation
        write that fell back to the plain upsert): its documentId is not the document of its last switch."""
        document = mapping.get("documentId")
        return bool(document) and (
            mapping.get("switchDoc") != document or mapping.get("updatedAt") != mapping.get("switchUpdatedAt")
        )

    async def _retire_entries(self, vrid: str, org_id: str, report: StoredRetentionReport) -> None:
        mapping = await self.graph.get_document(vrid, MAPPING)
        if not mapping or not retired_list(mapping):
            return
        if self._is_fingerprint(mapping):
            report.fingerprints += 1
            record_retention("stored_record", "skipped_fingerprint")
            return
        now = self._now_ms()
        mapped = mapping.get("documentId")
        keep: list[dict[str, Any]] = []
        changed = False
        for entry in retired_list(mapping):
            document = str(entry.get("doc") or "")
            if not document:
                changed = True
                continue
            if document == mapped:
                # Never purge the mapped document; the entry is stale.
                changed = True
                continue
            row = await self.blob._get_document_row(org_id, document)
            if row is None:
                changed = True
                continue
            retired_at = row.get("retiredAt")
            if not isinstance(retired_at, (int, float)) or not retired_at:
                outcome = await self.blob._retire_document(org_id, document)
                if outcome == "unavailable":
                    report.held_no_fence += 1
                    record_retention("stored_record", "held_no_fence")
                elif outcome == "gone":
                    changed = True
                    continue
                elif outcome == "marked":
                    report.marked += 1
                keep.append(entry)
                continue
            at = entry.get("at")
            if now - retired_at < self.grace_ms or not isinstance(at, (int, float)) or now - at < self.grace_ms:
                keep.append(entry)
                continue
            # The mapping may have been switched back to it meanwhile.
            fresh = await self.graph.get_document(vrid, MAPPING)
            if not fresh or fresh.get("documentId") == document:
                keep.append(entry)
                continue
            await self.blob.purge_document(org_id, document)
            report.purged += 1
            record_retention("stored_record", "purged")
            changed = True
        if changed:
            # No bump: a sweep never makes a writer retry. A refusal means a switch landed; the next pass redoes it.
            await self.graph.update_node_fields_if_match(
                vrid, MAPPING, {"retired": keep}, {"switchSeq": mapping.get("switchSeq")},
            )

    async def _collect_strays(self, vrid: str, org_id: str, report: StoredRetentionReport) -> None:
        mapping = await self.graph.get_document(vrid, MAPPING)
        if not mapping or not mapping.get("documentId"):
            return
        last = mapping.get("lastSwitchAt")
        if isinstance(last, (int, float)) and self._now_ms() - last < self.grace_ms:
            return
        candidates = await self.blob.list_record_documents(
            org_id, vrid, older_than_seconds=self.stray_grace_seconds,
        )
        # A storage service that applies olderThanSeconds reports retiredAt (null when live) on every
        # entry. One that does not (an older release, mid-rollout) would list a copy a writer has just
        # made and not yet switched to: nothing is purged from its answer.
        if any("retiredAt" not in d for d in candidates):
            record_retention("stored_record", "held_no_age_filter")
            return
        for document in candidates:
            if document.get("documentName") != f"record_{vrid}":
                continue
            document_id = str(document["_id"])
            fresh = await self.graph.get_document(vrid, MAPPING)
            if not fresh or not fresh.get("documentId"):
                return
            named = {fresh.get("documentId")} | {e.get("doc") for e in retired_list(fresh)}
            if document_id in named:
                continue
            await self.blob.purge_document(org_id, document_id)
            report.strays_purged += 1
            record_retention("stored_record", "stray_purged")
            await asyncio.sleep(0)
