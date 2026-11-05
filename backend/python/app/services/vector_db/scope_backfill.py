"""`ScopeBackfill`: write the scope fields of the points that already exist, one connector instance at a time.

Run by an operator (`python -m app.scripts.scope_projection`), never by itself, and only while the scope fields are on
(`scope_fields_enabled`). It computes nothing of its own: the fields of a point are `scope_fields_for_state`, the function
the live writers use, so a back-filled point carries what a newly indexed one would.

- **Resumable.** The records of the connector are read in key order, a page at a time. After a page is written its last key is
  saved on the app document (`scopeBackfillAfter`); a run that dies anywhere starts again at the last saved key and writes the
  page it was on a second time (the fields are a function of the graph, so that changes nothing). A connector whose saved
  version, or whose scope generation, is not the current one (`ACL_SCOPE_GENERATION`) starts over, a finished one included.
- **Bounded.** One run handles at most `max_pages` pages and returns `running` when there are more; the operator runs it again.
- **Org-scoped.** Every graph read names the organisation, and every write only matches points of that organisation.
- **Fail closed.** A point whose fields could not be computed or written gets the stale marker (legacy, version 0), which keeps
  it on the legacy filter, and is counted in `scopeBackfillStale`. The marker write is tried 4 times with doubling waits; if it still
  fails the virtual record is logged at error and counted (`pipeshub_scope_fields_failed_total{where="marker"}`), and the run reports
  it as stale (exit code 1). Only then can a point keep what it held, which may be exact fields this run wrote before a later step
  failed: run the back-fill again.
- **Safe beside live indexing.** The back-fill writes a point's scope keys only, never its membership arrays, under the same
  per-virtual-record lock the live writers take (so in this process the two do not interleave; the command is another process, so
  only the read-back below covers it). Across processes there is no lock,
  so every write is followed by a second read of the graph: if the fields differ from what was written, the graph changed
  between the first read and the write, and the point is written again from the new graph (up to `MAX_ROUNDS`, then it is marked
  stale). A live write that lands after ours has read the graph after the change and is right; one that landed before ours is
  corrected by the second read. A live writer that read the graph long before it wrote (a held-up process) is not stopped here:
  that needs a fence, which is a separate change.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from app.config.constants.arangodb import CollectionNames
from app.core.org_context import for_org
from app.services.permissions.metrics import SCOPE_STAMP_FAILED
from app.services.permissions.mode import scope_fields_enabled
from app.services.permissions.scope_fields import (
    SCOPE_FIELDS_VERSION,
    ScopeFields,
    current_scope_generation,
)
from app.services.permissions.structure_generation import (
    CLOSED_KEY,
    GENERATION_KEY,
    OPENED_KEY,
    UNSAFE_KEY,
    new_unsafe_mark,
    same_mark,
    structure_generation_of,
    unclosed_writes,
    unsafe_mark,
    whole_number,
)
from app.services.vector_db.membership import (
    STALE_SCOPE,
    resolve_virtual_record_state,
    scope_fields_for_state,
    vrid_write_locks,
)

if TYPE_CHECKING:
    import logging
    from collections.abc import Collection

    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
    from app.services.vector_db.interface.vector_db import IVectorDBService
    from app.services.vector_db.membership import CollectionLocator

PAGE_SIZE = 100
MAX_PAGES = 20  # records per run: 2,000 at the default page size
MAX_ROUNDS = 3  # write + read-back rounds for one virtual record before it is marked stale
MARKER_ATTEMPTS = 4  # tries to write the stale marker of one virtual record
MARKER_DELAY_SECONDS = 0.25  # doubled after each failed try

RUNNING, DONE, FAILED = "running", "done", "failed"


class ScopeFieldsOff(RuntimeError):
    """The back-fill was asked for while the scope fields are not written: nothing else would keep them current."""


class ScopeGenerationUnavailable(RuntimeError):
    """The shared scope generation could not be read: every stamp this process writes is untrusted, so a pass would only record a
    finished back-fill that no reader may rely on."""


class UnreadableStructureState(RuntimeError):
    """The connector's stored structure generation or unsafe mark cannot be read: a pass could not say what it stamped from."""


class CheckpointRefused(RuntimeError):
    """The first checkpoint of a run could not be written (typically an app schema without the new key): the run stopped before it wrote anything."""


class OpenMarkNotClosed(RuntimeError):
    """The connector carries an open unsafe mark and the operator did not say the write behind it is over."""


class UnknownConnector(ValueError):
    """The connector instance is not an app of this organisation."""


@dataclass
class Progress:
    state: str = RUNNING
    after: str | None = None
    version: int = SCOPE_FIELDS_VERSION
    generation: int = 0
    records: int = 0
    stale: int = 0
    error: str | None = None
    #: The connector's structure generation and unsafe mark as the pass read them BEFORE it read any record: its stamps are current only
    #: while the connector still holds both (`permissions.currency`). -1 / None: not recorded.
    structure_generation: int = -1
    unsafe: int | bool | None = None

    def line(self) -> str:
        return f"{self.state}  version={self.version} generation={self.generation} records={self.records} stale={self.stale} structure={self.structure_generation} unsafe={self.unsafe} after={self.after or '-'}" + (
            f" error={self.error}" if self.error else "")


#: The names the checkpoint is kept under on the app document (also read by `permissions.currency`).
BACKFILL_KEYS = {"state": "scopeBackfillState", "after": "scopeBackfillAfter", "version": "scopeBackfillVersion",
         "generation": "scopeBackfillGeneration", "records": "scopeBackfillRecords", "stale": "scopeBackfillStale", "error": "scopeBackfillError",
         "structure_generation": "scopeBackfillStructureGeneration", "unsafe": "scopeBackfillUnsafe"}


def _mark_or_unreadable(value: object) -> int | bool | None:
    try:
        return unsafe_mark(value)
    except ValueError:
        return -1      # equals no mark that was ever read: the pass starts over


class AppDocCheckpoints:
    """The checkpoint lives on the connector's app document, so it survives the process and needs no other store."""

    def __init__(self, graph_provider: IGraphDBProvider) -> None:
        self._graph = graph_provider

    async def load(self, app_id: str) -> Progress | None:
        doc = await self._graph.get_document(app_id, CollectionNames.APPS.value, raise_on_error=True)  # a failed read is not "no checkpoint"
        if not doc or doc.get(BACKFILL_KEYS["state"]) is None:
            return None
        structure = whole_number(doc.get(BACKFILL_KEYS["structure_generation"]))
        return Progress(
            str(doc[BACKFILL_KEYS["state"]]), doc.get(BACKFILL_KEYS["after"]), int(doc.get(BACKFILL_KEYS["version"]) or 0),
            int(doc.get(BACKFILL_KEYS["generation"]) if doc.get(BACKFILL_KEYS["generation"]) is not None else -1),
            int(doc.get(BACKFILL_KEYS["records"]) or 0), int(doc.get(BACKFILL_KEYS["stale"]) or 0), doc.get(BACKFILL_KEYS["error"]),
            structure_generation=-1 if structure is None else structure,        # a value that is not a number starts the pass over
            unsafe=_mark_or_unreadable(doc.get(BACKFILL_KEYS["unsafe"])))

    async def save(self, app_id: str, p: Progress) -> None:
        updates = {BACKFILL_KEYS[k]: getattr(p, k) for k in BACKFILL_KEYS}
        if not await self._graph.update_node(app_id, CollectionNames.APPS.value, updates):
            raise RuntimeError("the scope back-fill checkpoint was not saved")


class ScopeBackfill:
    def __init__(self, graph_provider: IGraphDBProvider, vector_db: IVectorDBService, locator: CollectionLocator,
                 logger: logging.Logger, *, page_size: int = PAGE_SIZE) -> None:
        self._graph, self._vdb, self._locator, self._log = graph_provider, vector_db, locator, logger
        self._page_size = page_size
        self._checkpoints = AppDocCheckpoints(graph_provider)

    async def run_connector(self, org_id: str, app_id: str, *, restart: bool = False, max_pages: int = MAX_PAGES,
                            close_open_mark: bool = False) -> Progress:
        if not scope_fields_enabled():
            raise ScopeFieldsOff("turn the scope fields on (ACL_SCOPE_FIELDS) before back-filling")
        if current_scope_generation() < 0:  # UNAVAILABLE_GENERATION: before any read, write or checkpoint
            raise ScopeGenerationUnavailable("the scope generation could not be read; fix the configuration store and run again")
        if not org_id:
            raise UnknownConnector("an organisation is required")
        with for_org(org_id, job="scope-backfill"):
            stamps = self._graph.permission_stamp_graph()
            if not (await stamps.app(org_id, app_id)).connector_type:
                raise UnknownConnector(f"{app_id} is not a connector of this organisation")
            p = await self._checkpoints.load(app_id)
            # What the connector's structure was BEFORE any record is read: a change that commits after this moves the generation, so the
            # connector is not current for stamps written from a graph that may predate it (see `permissions.currency`).
            app = await self._graph.get_document(app_id, CollectionNames.APPS.value, raise_on_error=True) or {}
            structure = structure_generation_of(app)
            try:
                mark = unsafe_mark(app.get(UNSAFE_KEY))
            except ValueError:
                structure = None
            in_flight = unclosed_writes(app, allow_closed_ahead=close_open_mark)
            if in_flight is None:
                structure = None
            elif in_flight != 0:
                if not close_open_mark:
                    raise OpenMarkNotClosed(
                        f"{app_id} has {in_flight} unclassified write(s) that started and never wrote their closing mark, so a pass cannot tell whether "
                        "they finished. If no write of this organisation's connectors is still running, run again with --close-open-mark.")
                # The operator says the writes are over: close them and write a mark now, which the pass that starts after it covers.
                mark = new_unsafe_mark()
                if not await self._graph.update_node(app_id, CollectionNames.APPS.value, {CLOSED_KEY: app.get(OPENED_KEY) or 0, UNSAFE_KEY: mark}):
                    raise CheckpointRefused(f"the open writes of {app_id} could not be closed")
            if structure is None:  # a stored generation or mark that is not readable is not read as "never changed" or "none": nothing is written
                raise UnreadableStructureState(
                    f"the structure state of {app_id} is not readable: generation {app.get(GENERATION_KEY)!r}, unsafe mark {app.get(UNSAFE_KEY)!r}")
            # A checkpoint of another field version, scope generation, structure generation or unsafe mark says nothing about the points now:
            # start over. Nothing clears the mark: the pass records the value it read, and the connector is current only while it still reads
            # the same (a mark written during the pass is a different value, and nothing the pass does can erase it).
            generation = current_scope_generation()
            if (p is None or restart or p.version != SCOPE_FIELDS_VERSION or p.generation != generation or p.structure_generation != structure
                    or not same_mark(p.unsafe, mark)):
                p = Progress(generation=generation, structure_generation=structure, unsafe=mark)
                try:
                    await self._checkpoints.save(app_id, p)
                except Exception as exc:  # the first write of a run: nothing else has been written
                    raise CheckpointRefused(
                        f"the back-fill checkpoint of {app_id} could not be written ({type(exc).__name__}). If the graph store's app schema is older "
                        f"than this release (it must list {', '.join([*BACKFILL_KEYS.values(), OPENED_KEY, CLOSED_KEY])}), restart a connector or indexing "
                        "service, which applies the schema at start, and run again. Nothing was written."
                    ) from exc
            if p.state == DONE:
                return p
            p.state, p.error = RUNNING, None
            try:
                for _ in range(max_pages):
                    ids = await stamps.record_page(org_id, app_id, p.after, self._page_size)
                    if not ids:
                        p.state = DONE
                        break
                    nodes = await stamps.nodes(org_id, frozenset(ids))
                    page_stale = 0
                    for vrid in sorted({n.vrid for n in nodes.values() if n.vrid and not n.deleted}):
                        page_stale += await self._stamp(org_id, vrid) == "stale"
                    # counted with the page, so a page written again after a failure is not counted twice
                    moved = replace(p, after=ids[-1], records=p.records + len(ids), stale=p.stale + page_stale)
                    await self._checkpoints.save(app_id, moved)  # only after the page is written
                    p = moved
            except Exception as exc:
                p.state, p.error = FAILED, type(exc).__name__
                SCOPE_STAMP_FAILED.inc("backfill")
                self._log.warning("scope back-fill of %s stopped at %s: %s", app_id, p.after, p.error)
            await self._checkpoints.save(app_id, p)
            return p

    async def _stamp(self, org_id: str, vrid: str) -> str:
        """`done`, `skipped` (no point to write) or `stale` (the stale marker was written, or could not be: see `_mark`)."""
        collections: list[str] | tuple[str, ...] = ()
        try:
            async with vrid_write_locks([vrid]):
                for _ in range(MAX_ROUNDS):
                    state = await resolve_virtual_record_state(self._graph, vrid)
                    collections = self._locator.collections_for_records(state.records)
                    if not collections:
                        return "skipped"
                    fields = await scope_fields_for_state(self._graph, state, org_id, self._log)
                    if fields == STALE_SCOPE:
                        await self._mark(collections, org_id, vrid)
                        return "stale"
                    await self._write(collections, org_id, vrid, fields)
                    # the graph may have changed between the read and the write: write again if it says something else now
                    again = await scope_fields_for_state(self._graph, await resolve_virtual_record_state(self._graph, vrid), org_id, self._log)
                    if again == fields:
                        return "done"
                await self._mark(collections, org_id, vrid)
                return "stale"
        except Exception as exc:
            SCOPE_STAMP_FAILED.inc("backfill")
            self._log.warning("scope fields of %s not written: %s", vrid, type(exc).__name__)
            try:
                targets = collections or await self._locator.all_collections(fresh=True)
            except Exception:
                targets = ()
            await self._mark(targets, org_id, vrid)
            return "stale"

    async def _mark(self, collections: Collection[str], org_id: str, vrid: str) -> bool:
        """Write the stale marker, trying again with a growing wait. False (logged at error with the virtual record, counted) when it
        still fails: the point then keeps what it holds, which may be exact fields this run wrote before a later step failed."""
        delay = MARKER_DELAY_SECONDS
        for attempt in range(MARKER_ATTEMPTS):
            try:
                if collections:
                    await self._write(collections, org_id, vrid, STALE_SCOPE)
                    return True
            except Exception as exc:
                last = type(exc).__name__
            else:
                last = "no collection"
            if attempt + 1 < MARKER_ATTEMPTS:
                await asyncio.sleep(delay)
                delay *= 2
        SCOPE_STAMP_FAILED.inc("marker")
        self._log.error("scope fields of virtual record %s may be wrong and could not be marked stale (%s); run the back-fill again", vrid, last)
        return False

    async def _write(self, collections: list[str] | tuple[str, ...], org_id: str, vrid: str, fields: ScopeFields) -> None:
        flt = await self._vdb.filter_collection(must={"orgId": org_id, "virtualRecordId": vrid})
        for name in collections:
            await self._vdb.set_payload(name, fields.to_payload(), flt)


async def status(graph_provider: IGraphDBProvider, org_id: str, app_id: str) -> Progress | None:
    """The saved progress of a connector of this organisation; `UnknownConnector` for any other app."""
    with for_org(org_id, job="scope-backfill"):
        if not (await graph_provider.permission_stamp_graph().app(org_id, app_id)).connector_type:
            raise UnknownConnector(f"{app_id} is not a connector of this organisation")
        return await AppDocCheckpoints(graph_provider).load(app_id)
