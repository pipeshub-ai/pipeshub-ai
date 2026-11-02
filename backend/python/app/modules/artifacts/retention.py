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

Both deletes run while the sweep holds the exclusivity lease (``record:<id>``)
of every record holding the virtual record, the lease a re-index holds from
its ``contentRev`` write to the pointer flip. The graph re-checks cannot see a
write that commits while they run (a record moving back onto an old revision:
its ``contentRev`` and the stage states or the artifact document it reuses are
different documents), so without the lease a delete could land right after
that move. A record whose lease is held elsewhere leaves its virtual record for
the next pass.

It runs whether or not artifact writes are on: sets written while they were on
still have to be retired after they are turned off, and stage states exist
either way. A storage service without the artifact routes stops the artifact
step only. Deletes are idempotent, so a pass cut short is simply resumed.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from dataclasses import dataclass, fields
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from app.config.constants.arangodb import CollectionNames
from app.modules.artifacts.store import ARTIFACT_ROUTES, ArtifactRoutesUnavailable
from app.services.graph_db.common.record_visibility import RecordVisibility
from app.services.messaging.config import messaging_env
from app.telemetry.modules.record_storage_metrics import record_retention
from app.utils.time_conversion import get_epoch_timestamp_in_ms

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable
    from logging import Logger

    from app.modules.artifacts.store import NodeArtifactStore
    from app.services.distributed.interface import IDistributedLeaseManager
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

DEFAULT_GRACE_MS = 24 * 3600 * 1000
# Once per process: the recovery loop builds a new sweep every pass, so a per-sweep latch would log every minute.
_unfenced_warned = False


def reset_unfenced_warning() -> None:
    global _unfenced_warned  # noqa: PLW0603 - a process-wide latch by design
    _unfenced_warned = False

DEFAULT_PAGE_SIZE = 200
# Virtual records whose artifact sets are listed and deleted at once.
RETENTION_CONCURRENCY = 8
# One listing attempt, no retries: a vrid that cannot be listed now is simply
# reached again on a later pass, and the shared session's 120 s read bound with
# three attempts would let one hung storage call hold a page for minutes.
RETENTION_LIST_TIMEOUT_SECONDS = 10.0
# One delete attempt, well under the record lease term (120 s) the sweep holds
# while it runs. Storage goes on with a delete this gives up on; its stamp check
# (``ifUnchangedSince``) is what keeps that safe once the lease has lapsed.
RETENTION_DELETE_TIMEOUT_SECONDS = 30.0
# Least a delete is given when the tick's deadline is close, so one hung delete
# cannot hold a tick much past its budget.
RETENTION_DELETE_MIN_TIMEOUT_SECONDS = 5.0
# Virtual records failing in a row that end the page: storage (or the graph) is
# down, and every further vrid would only wait out its own timeout.
RETENTION_MAX_CONSECUTIVE_FAILURES = 3


@dataclass
class RetentionReport:
    scanned: int = 0
    revisions_deleted: int = 0
    revisions_kept: int = 0
    skipped_trashed: int = 0
    # Virtual records left for the next pass because a holder's lease was held.
    skipped_busy: int = 0
    stage_states_deleted: int = 0
    failed: int = 0
    # The recovery lease was lost part-way; the page is to be read again.
    interrupted: bool = False
    # The page stopped early on its deadline or on failures in a row; the walk
    # goes on from the key the page returned.
    cut_short: bool = False

    def add(self, other: RetentionReport) -> None:
        for f in fields(self):
            if f.type == "bool":
                setattr(self, f.name, getattr(self, f.name) or getattr(other, f.name))
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


@dataclass
class _HolderLeases:
    """The record leases one virtual record's retirement holds; ``ids`` is None without leases."""

    ids: set[str] | None
    manager: IDistributedLeaseManager | None
    owner: str
    term_s: float

    def covers(self, holders: list[dict[str, Any]]) -> bool:
        """False when a record joined the virtual record after the leases were taken."""
        return self.ids is None or {str(h["id"]) for h in holders if h.get("id")} <= self.ids

    async def renew(self) -> bool:
        if self.manager is None or not self.ids:
            return True
        leases = [(f"record:{record_id}", self.owner) for record_id in self.ids]
        renewed = await self.manager.renew_many(leases, self.term_s)
        return all(renewed.get(lease, False) for lease in leases)


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
        leases: IDistributedLeaseManager | None = None,
        list_timeout_seconds: float = RETENTION_LIST_TIMEOUT_SECONDS,
        delete_timeout_seconds: float = RETENTION_DELETE_TIMEOUT_SECONDS,
        max_consecutive_failures: int = RETENTION_MAX_CONSECUTIVE_FAILURES,
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
        # None without distributed concurrency: no re-index holds a lease to wait for either.
        self.leases = leases
        self.list_timeout_seconds = list_timeout_seconds
        self.delete_timeout_seconds = delete_timeout_seconds
        self.max_consecutive_failures = max(1, max_consecutive_failures)
        self._now_ms = now_ms

    async def run_page(
        self,
        after_key: str | None,
        *,
        still_leader: Callable[[], bool] | None = None,
        deadline: float | None = None,
    ) -> tuple[str | None, RetentionReport]:
        """One page of virtual records after ``after_key``.

        Returns the key to continue from (None once the walk reached the end) and
        what the page did. When ``still_leader`` turns False the page stops and
        hands back ``after_key``, so the next pass reads the same page again.
        Past ``deadline`` (``time.monotonic()``), or after failures in a row, no
        further virtual record is started and the page hands back the key of the
        last one before the first it did not try (``cut_short``). Raises when
        the page or its trash check cannot be read; a virtual record that fails
        on its own is logged and counted.
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

        unheld = await self._unheld_stage_revisions([v for v in vrids if v not in trashed], cutoff_ms, report)
        gate = asyncio.Semaphore(self.concurrency)
        # Per pointer: whether it was tried. A failure counts (the next walk
        # retries it), even one of the run that ended the page: resuming at it
        # could hold the walk on a few vrids that keep failing.
        finished = [not p.get("virtualRecordId") or p["virtualRecordId"] in trashed for p in pointers]
        failing_run: list[int] = []
        tried = 0

        def stop_starting() -> bool:
            # The deadline never stops the first vrid of a page: when the page's own
            # graph reads used up the budget, the walk would otherwise re-read the
            # same page every tick and never move.
            if len(failing_run) >= self.max_consecutive_failures or (
                deadline is not None and tried > 0 and time.monotonic() >= deadline
            ):
                report.cut_short = True
            return report.cut_short

        async def one(index: int, pointer: dict[str, Any]) -> None:
            nonlocal tried
            vrid = str(pointer["virtualRecordId"])
            async with gate:
                if not leader() or stop_starting():
                    return
                tried += 1
                try:
                    ok = await self._retire_virtual_record(
                        pointer, unheld.get(vrid, []), cutoff_ms, report, leader, deadline
                    )
                except Exception as exc:
                    ok = False
                    report.failed += 1
                    self.logger.warning(
                        "Revision retention: could not retire old revisions of %s; next pass retries: %s: %s",
                        vrid, type(exc).__name__, exc,
                    )
                if ok:
                    failing_run.clear()
                else:
                    failing_run.append(index)
                finished[index] = True

        await asyncio.gather(*(one(i, p) for i, p in enumerate(pointers) if not finished[i]))
        if report.interrupted:
            return after_key, report
        if report.cut_short:
            done = next((i for i, f in enumerate(finished) if not f), len(pointers))
            if done < len(pointers):
                resume = next(
                    (str(p["virtualRecordId"]) for p in reversed(pointers[:done]) if p.get("virtualRecordId")),
                    after_key,
                )
                return resume, report
            report.cut_short = False
        next_key = str(pointers[-1]["virtualRecordId"]) if len(pointers) >= self.page_size else None
        return next_key, report

    async def _unheld_stage_revisions(
        self, vrids: list[str], cutoff_ms: int, report: RetentionReport
    ) -> dict[str, list[str]]:
        """The revisions, by virtual record, whose stage states look retirable; read for the whole page."""
        if not vrids:
            return {}
        try:
            pairs = await self.graph_provider.stage_states_unheld_revisions(vrids, updated_before_ms=cutoff_ms)
        except Exception as exc:
            report.failed += 1
            self.logger.warning(
                "Revision retention: could not read the stage states of %d virtual record(s); "
                "next pass retries: %s: %s", len(vrids), type(exc).__name__, exc,
            )
            return {}
        by_vrid: dict[str, list[str]] = {}
        for vrid, rev in pairs:
            by_vrid.setdefault(vrid, []).append(rev)
        return by_vrid

    async def _deletable_revisions(
        self, pointer: dict[str, Any], cutoff_ms: int, report: RetentionReport
    ) -> tuple[list[dict[str, Any]], bool]:
        """Artifact sets past the grace that are neither the current nor the previous revision.

        The flag is False when the listing failed (storage or network), which feeds
        the page's failures-in-a-row stop.
        """
        vrid = str(pointer["virtualRecordId"])
        if not pointer.get("currentRev") or not pointer.get("orgId") or not ARTIFACT_ROUTES.available:
            return [], True
        try:
            revisions = await self.store.list_revisions(
                pointer["orgId"], vrid, timeout_seconds=self.list_timeout_seconds
            )
        except ArtifactRoutesUnavailable as exc:
            # Process-wide, as for writes: the storage service has no artifact routes
            # until the upgrade that brings them, which restarts this service.
            if ARTIFACT_ROUTES.mark_unavailable(exc.status):
                self.logger.warning(
                    "Storage has no artifact routes (HTTP %d); old revisions are not retired "
                    "until this service restarts", exc.status,
                )
            return [], True
        except Exception as exc:
            # The stage states of this virtual record are retired all the same.
            report.failed += 1
            self.logger.warning(
                "Revision retention: could not list the revisions of %s; next pass retries: %s: %s",
                vrid, type(exc).__name__, exc,
            )
            return [], False
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
        return candidates, True

    async def _retire_virtual_record(
        self,
        pointer: dict[str, Any],
        unheld_revs: list[str],
        cutoff_ms: int,
        report: RetentionReport,
        leader: Callable[[], bool],
        deadline: float | None = None,
    ) -> bool:
        """Retire one virtual record; False when its artifact listing failed."""
        vrid = str(pointer["virtualRecordId"])
        candidates, listed = await self._deletable_revisions(pointer, cutoff_ms, report)
        if not candidates and not unheld_revs:
            return listed
        org_id = pointer.get("orgId")
        if not org_id:
            # The holders are read per organization; without one nothing is known to be unheld.
            return listed

        def keep_all() -> None:
            report.revisions_kept += len(candidates)
            record_retention("revision", "kept", len(candidates))

        def busy() -> None:
            keep_all()
            report.skipped_busy += 1
            record_retention("virtual_record", "skipped_busy")

        async with self._holder_leases(vrid, str(org_id)) as lease:
            if lease is None:
                busy()
                return listed
            # Read after the leases are held, the holders last: a record that moved onto
            # an old revision, or into the trash, before then is seen here.
            mapping = (
                await self.graph_provider.get_document(vrid, CollectionNames.VIRTUAL_RECORD_TO_DOC_ID_MAPPING.value)
                if candidates else None
            )
            holders = await self._holders(vrid, str(org_id))
            if not lease.covers(holders):
                busy()
                return listed
            if any(h.get("isDeleted") for h in holders):
                keep_all()
                return listed
            if unheld_revs and leader():
                # The statement checks the rule again against the graph as it is now.
                deleted = await self.graph_provider.stage_states_delete_unheld_revisions(
                    [(vrid, rev) for rev in unheld_revs], updated_before_ms=cutoff_ms
                )
                report.stage_states_deleted += deleted
                record_retention("stage_state", "deleted", deleted)
            if candidates:
                await self._retire_revisions(
                    str(org_id), candidates, mapping, holders, lease, report, leader, deadline
                )
            return listed

    async def _holders(self, vrid: str, org_id: str) -> list[dict[str, Any]]:
        return (await self.graph_provider.get_virtual_record_holders([vrid], org_id)).get(vrid, [])

    @contextlib.asynccontextmanager
    async def _holder_leases(self, vrid: str, org_id: str) -> AsyncIterator[_HolderLeases | None]:
        """The record leases of the virtual record's holders, or None when one is held elsewhere
        (a re-index or a stage writing that record now)."""
        leases = self.leases
        if leases is None:
            yield _HolderLeases(None, None, "", 0.0)
            return
        term = messaging_env.concurrency_lease_seconds
        owner = f"revision-retention:{uuid4().hex[:8]}"
        held = _HolderLeases(set(), leases, owner, term)
        try:
            for holder in await self._holders(vrid, org_id):
                record_id = str(holder.get("id") or "")
                if not record_id or record_id in held.ids:
                    continue
                if not await leases.try_acquire(f"record:{record_id}", owner, 1, term):
                    yield None
                    return
                held.ids.add(record_id)
            yield held
        finally:
            for record_id in held.ids:
                with contextlib.suppress(Exception):
                    await leases.release(f"record:{record_id}", owner)

    def _delete_timeout(self, deadline: float | None) -> float:
        if deadline is None:
            return self.delete_timeout_seconds
        remaining = deadline - time.monotonic()
        return min(self.delete_timeout_seconds, max(RETENTION_DELETE_MIN_TIMEOUT_SECONDS, remaining))

    async def _retire_revisions(
        self,
        org_id: str,
        candidates: list[dict[str, Any]],
        mapping: dict[str, Any] | None,
        holders: list[dict[str, Any]],
        lease: _HolderLeases,
        report: RetentionReport,
        leader: Callable[[], bool],
        deadline: float | None = None,
    ) -> None:
        def keep(count: int = 1) -> None:
            if count <= 0:
                return
            report.revisions_kept += count
            record_retention("revision", "kept", count)

        if mapping is None or not mapping.get("currentRev"):
            keep(len(candidates))
            return
        # A record whose source went back to earlier bytes is on an old rev, and its
        # writer re-uses that revision's document.
        held = {mapping.get("currentRev"), mapping.get("previousRev")} | _held_revs(holders)
        for index, revision in enumerate(candidates):
            if revision.get("rev") in held:
                keep()
                continue
            if revision.get("deleteFence") is not True:
                # A storage service from before the delete fence ignores the stamp and
                # would delete unfenced (Node must be upgraded before this service).
                global _unfenced_warned  # noqa: PLW0603
                if not _unfenced_warned:
                    _unfenced_warned = True
                    self.logger.warning(
                        "Storage does not report the artifact delete fence; old revisions are "
                        "kept until the storage service is upgraded"
                    )
                keep()
                record_retention("revision", "unfenced")
                continue
            # Past the tick's deadline the rest is left for the next walk; a delete can
            # be slow (a whole tree) and the leases must outlast it, so they are renewed
            # right before it.
            if not leader() or (deadline is not None and time.monotonic() >= deadline) or not await lease.renew():
                keep(len(candidates) - index)
                return
            try:
                # One bounded attempt carrying the stamp the revision was listed with:
                # storage refuses the delete if the revision was written again since, so
                # a delete that outlives this lease cannot remove what a re-index filed.
                purged = await self.store.delete_revision(
                    org_id,
                    str(revision["documentId"]),
                    if_unchanged_since=_stored_at_ms(revision),
                    timeout_seconds=self._delete_timeout(deadline),
                )
            except Exception:
                record_retention("revision", "failed")
                raise
            if purged is None:
                keep()
                record_retention("revision", "changed")
                continue
            if purged < 1:
                # Something else removed it since it was listed: nothing was deleted here.
                keep()
                record_retention("revision", "gone")
                continue
            report.revisions_deleted += 1
            record_retention("revision", "deleted")
            if not await lease.renew():
                # The delete took longer than the lease term: stop; storage's stamp check
                # protected that delete, and the next walk reads the holders again.
                self.logger.warning(
                    "Revision retention: the record leases lapsed during a delete; "
                    "the rest of the virtual record is left for the next pass"
                )
                keep(len(candidates) - index - 1)
                return
