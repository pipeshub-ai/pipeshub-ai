"""Retire artifact-set revisions and pipeline stage states nothing will read again.

Every content revision of a virtual record leaves an artifact set
(``record-revisions/{vrid}/{rev}/``, one storage document) and stage states keyed
``{vrid}:{rev}:{stage}``. Both are removed with the virtual record when it is
released, but a record that changes keeps adding revisions while it lives. This
sweep walks ``virtualRecordToDocIdMapping`` by key and, for each virtual record:

* keeps every revision while any record holding it is in the trash, since a
  restore must find its content where it left it;
* deletes an artifact set whose rev is neither ``currentRev`` nor
  ``previousRev`` (the previous revision is what the next index of the record
  inherits image descriptions from) nor the ``contentRev`` of a record holding
  the virtual record, and that is older than the grace; with no ``currentRev``
  it deletes none;
* deletes the stage states of a revision no record holding the virtual record
  is on (trashed records count), once its newest state is older than the grace.

It runs whether or not artifact writes are on: sets written while they were on
still have to be retired after they are turned off, and stage states exist
either way. A storage service without the artifact routes stops the artifact
step only. Deletes are idempotent, so a pass cut short is simply resumed.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, fields
from typing import TYPE_CHECKING, Any

from app.config.constants.arangodb import CollectionNames
from app.modules.artifacts.store import ARTIFACT_ROUTES, ArtifactRoutesUnavailable
from app.services.graph_db.common.record_visibility import RecordVisibility
from app.telemetry.modules.record_storage_metrics import record_retention
from app.utils.time_conversion import get_epoch_timestamp_in_ms

if TYPE_CHECKING:
    from collections.abc import Callable
    from logging import Logger

    from app.modules.artifacts.store import NodeArtifactStore
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

DEFAULT_GRACE_MS = 24 * 3600 * 1000
DEFAULT_PAGE_SIZE = 200
# Virtual records whose artifact sets are listed and deleted at once.
RETENTION_CONCURRENCY = 8


@dataclass
class RetentionReport:
    scanned: int = 0
    revisions_deleted: int = 0
    revisions_kept: int = 0
    skipped_trashed: int = 0
    stage_states_deleted: int = 0
    failed: int = 0
    # The recovery lease was lost part-way; the page is to be read again.
    interrupted: bool = False

    def add(self, other: RetentionReport) -> None:
        for f in fields(self):
            if f.name == "interrupted":
                self.interrupted = self.interrupted or other.interrupted
            else:
                setattr(self, f.name, getattr(self, f.name) + getattr(other, f.name))

    @property
    def removed(self) -> int:
        return self.revisions_deleted + self.stage_states_deleted


def _stored_at_ms(revision: dict[str, Any]) -> int | None:
    """The revision document's last write, or None when it carries no usable time."""
    stamps = [v for v in (revision.get("createdAt"), revision.get("updatedAt")) if isinstance(v, (int, float))]
    return int(max(stamps)) if stamps else None


def _held_revs(holders: list[dict[str, Any]]) -> set[str]:
    return {str(h["contentRev"]) for h in holders if h.get("contentRev")}


class RevisionRetentionSweep:
    def __init__(
        self,
        graph_provider: IGraphDBProvider,
        store: NodeArtifactStore,
        logger: Logger,
        *,
        grace_ms: int = DEFAULT_GRACE_MS,
        page_size: int = DEFAULT_PAGE_SIZE,
        concurrency: int = RETENTION_CONCURRENCY,
        now_ms: Callable[[], int] = get_epoch_timestamp_in_ms,
    ) -> None:
        if grace_ms <= 0:
            raise ValueError("retention needs a positive grace")
        self.graph_provider = graph_provider
        self.store = store
        self.logger = logger
        self.grace_ms = grace_ms
        self.page_size = max(1, page_size)
        self.concurrency = max(1, concurrency)
        self._now_ms = now_ms

    async def run_page(
        self, after_key: str | None, *, still_leader: Callable[[], bool] | None = None
    ) -> tuple[str | None, RetentionReport]:
        """One page of virtual records after ``after_key``.

        Returns the key to continue from (None once the walk reached the end) and
        what the page did. When ``still_leader`` turns False the page stops and
        hands back ``after_key``, so the next pass reads the same page again.
        Raises when the page or its trash check cannot be read; a virtual record
        that fails on its own is logged and counted.
        """
        report = RetentionReport()
        pointers = await self.graph_provider.get_revision_pointers(after_key, self.page_size)
        if not pointers:
            return None, report
        report.scanned = len(pointers)
        cutoff_ms = self._now_ms() - self.grace_ms
        vrids = [str(p["virtualRecordId"]) for p in pointers if p.get("virtualRecordId")]
        trashed = await self.graph_provider.get_virtual_record_ids_in_use(
            vrids, visibility=RecordVisibility.DELETED
        )
        report.skipped_trashed = len(trashed)
        record_retention("virtual_record", "skipped_trashed", len(trashed))

        def leader() -> bool:
            if still_leader is None or still_leader():
                return True
            report.interrupted = True
            return False

        await self._retire_stage_states([v for v in vrids if v not in trashed], cutoff_ms, report, leader)

        gate = asyncio.Semaphore(self.concurrency)

        async def one(pointer: dict[str, Any]) -> None:
            async with gate:
                if not leader():
                    return
                try:
                    await self._retire_revisions(pointer, cutoff_ms, report, leader)
                except Exception as exc:
                    report.failed += 1
                    self.logger.warning(
                        "Revision retention: could not retire old revisions of %s; next pass retries: %s: %s",
                        pointer.get("virtualRecordId"), type(exc).__name__, exc,
                    )

        await asyncio.gather(
            *(one(p) for p in pointers if p.get("virtualRecordId") and p["virtualRecordId"] not in trashed)
        )
        if report.interrupted:
            return after_key, report
        next_key = str(pointers[-1]["virtualRecordId"]) if len(pointers) >= self.page_size else None
        return next_key, report

    async def _retire_stage_states(
        self, vrids: list[str], cutoff_ms: int, report: RetentionReport, leader: Callable[[], bool]
    ) -> None:
        if not vrids:
            return
        try:
            unheld = await self.graph_provider.stage_states_unheld_revisions(vrids, updated_before_ms=cutoff_ms)
            if not unheld or not leader():
                return
            deleted = await self.graph_provider.stage_states_delete_unheld_revisions(
                unheld, updated_before_ms=cutoff_ms
            )
        except Exception as exc:
            report.failed += 1
            self.logger.warning(
                "Revision retention: could not retire stage states of %d virtual record(s); "
                "next pass retries: %s: %s", len(vrids), type(exc).__name__, exc,
            )
            return
        report.stage_states_deleted += deleted
        record_retention("stage_state", "deleted", deleted)

    async def _retire_revisions(
        self, pointer: dict[str, Any], cutoff_ms: int, report: RetentionReport, leader: Callable[[], bool]
    ) -> None:
        vrid = str(pointer["virtualRecordId"])
        org_id = pointer.get("orgId")
        if not pointer.get("currentRev") or not org_id or not ARTIFACT_ROUTES.available:
            return
        try:
            revisions = await self.store.list_revisions(org_id, vrid)
        except ArtifactRoutesUnavailable as exc:
            # Process-wide, as for writes: the storage service has no artifact routes
            # until the upgrade that brings them, which restarts this service.
            if ARTIFACT_ROUTES.mark_unavailable(exc.status):
                self.logger.warning(
                    "Storage has no artifact routes (HTTP %d); old revisions are not retired "
                    "until this service restarts", exc.status,
                )
            return
        keep = {pointer.get("currentRev"), pointer.get("previousRev")}
        candidates = []
        for revision in revisions:
            stored_at = _stored_at_ms(revision)
            if (
                revision.get("rev") in keep
                or not revision.get("documentId")
                or stored_at is None
                or stored_at >= cutoff_ms
            ):
                report.revisions_kept += 1
                record_retention("revision", "kept")
            else:
                candidates.append(revision)
        if not candidates:
            return

        # Read the pointer and the holders again right before deleting: a record
        # can have moved onto an old revision (its source went back to earlier
        # bytes) since the page was read, or into the trash.
        fresh = await self.graph_provider.get_document(
            vrid, CollectionNames.VIRTUAL_RECORD_TO_DOC_ID_MAPPING.value
        )
        holders = (await self.graph_provider.get_virtual_record_holders([vrid], org_id)).get(vrid, [])
        if fresh is None or not fresh.get("currentRev") or any(h.get("isDeleted") for h in holders):
            report.revisions_kept += len(candidates)
            record_retention("revision", "kept", len(candidates))
            return
        keep = {fresh.get("currentRev"), fresh.get("previousRev")} | _held_revs(holders)
        for revision in candidates:
            if revision.get("rev") in keep or not leader():
                report.revisions_kept += 1
                record_retention("revision", "kept")
                continue
            try:
                await self.store.delete_revision(org_id, str(revision["documentId"]))
            except Exception:
                record_retention("revision", "failed")
                raise
            report.revisions_deleted += 1
            record_retention("revision", "deleted")
