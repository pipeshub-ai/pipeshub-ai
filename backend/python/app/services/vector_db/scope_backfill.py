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
    GENERATION_KEY,
    UNSAFE_KEY,
    structure_generation_of,
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


class UnreadableStructureGeneration(RuntimeError):
    """The connector's stored structure generation is not a whole number: a pass could not say what it stamped from."""


class CheckpointRefused(RuntimeError):
    """The first checkpoint of a run could not be written (typically an app schema without the new key): the run stopped before it wrote anything."""


class UnknownConnector(ValueError):
    """The connector instance is not an app of this organisation."""


@dataclass
class Progress:
    state: str = RUNNING
    after: str | None = None
    version: int = SCOPE_FIELDS_VERSION
    generation: int = 0
    #: The connector's structure generation (`structure_generation.GENERATION_KEY`) as the pass read it BEFORE it read any record: the
    #: stamps it writes are current only while the connector still holds this generation (`permissions.currency`). -1: not recorded.
    structure_generation: int = -1
    records: int = 0
    stale: int = 0
    error: str | None = None

    def line(self) -> str:
        return f"{self.state}  version={self.version} generation={self.generation} structure={self.structure_generation} records={self.records} stale={self.stale} after={self.after or '-'}" + (
            f" error={self.error}" if self.error else "")


_KEYS = {"state": "scopeBackfillState", "after": "scopeBackfillAfter", "version": "scopeBackfillVersion",
         "generation": "scopeBackfillGeneration", "structure_generation": "scopeBackfillStructureGeneration", "records": "scopeBackfillRecords", "stale": "scopeBackfillStale", "error": "scopeBackfillError"}
BACKFILL_KEYS = _KEYS      # the names the checkpoint is kept under on the app document (read by `permissions.currency`)


class AppDocCheckpoints:
    """The checkpoint lives on the connector's app document, so it survives the process and needs no other store."""

    def __init__(self, graph_provider: IGraphDBProvider) -> None:
        self._graph = graph_provider

    async def load(self, app_id: str) -> Progress | None:
        doc = await self._graph.get_document(app_id, CollectionNames.APPS.value, raise_on_error=True)  # a failed read is not "no checkpoint"
        if not doc or doc.get(_KEYS["state"]) is None:
            return None
        return Progress(str(doc[_KEYS["state"]]), doc.get(_KEYS["after"]), int(doc.get(_KEYS["version"]) or 0),
                        int(doc.get(_KEYS["generation"]) if doc.get(_KEYS["generation"]) is not None else -1),
                        int(doc[_KEYS["structure_generation"]]) if doc.get(_KEYS["structure_generation"]) is not None else -1,
                        int(doc.get(_KEYS["records"]) or 0), int(doc.get(_KEYS["stale"]) or 0), doc.get(_KEYS["error"]))

    async def save(self, app_id: str, p: Progress) -> None:
        updates = {_KEYS[k]: getattr(p, k) for k in _KEYS}
        if not await self._graph.update_node(app_id, CollectionNames.APPS.value, updates):
            raise RuntimeError("the scope back-fill checkpoint was not saved")


class ScopeBackfill:
    def __init__(self, graph_provider: IGraphDBProvider, vector_db: IVectorDBService, locator: CollectionLocator,
                 logger: logging.Logger, *, page_size: int = PAGE_SIZE) -> None:
        self._graph, self._vdb, self._locator, self._log = graph_provider, vector_db, locator, logger
        self._page_size = page_size
        self._checkpoints = AppDocCheckpoints(graph_provider)

    async def run_connector(self, org_id: str, app_id: str, *, restart: bool = False, max_pages: int = MAX_PAGES) -> Progress:
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
            structure, unsafe = structure_generation_of(app), bool(app.get(UNSAFE_KEY))
            if structure is None:  # a stored generation that is not a number is not read as "never changed": nothing is written
                raise UnreadableStructureGeneration(f"the structure generation of {app_id} is not a number: {app.get(GENERATION_KEY)!r}")
            # a checkpoint of another field version, scope generation or structure generation says nothing about the points now (a connector
            # marked unsafe too: the mark is cleared only by a pass that starts after it): start over
            generation = current_scope_generation()
            if p is None or restart or p.version != SCOPE_FIELDS_VERSION or p.generation != generation or p.structure_generation != structure or unsafe:
                p = Progress(generation=generation, structure_generation=structure)
                # The unfinished checkpoint is saved BEFORE the unsafe mark is cleared: a crash in between leaves the mark, never a finished
                # pass from an older generation with no mark.
                try:
                    await self._checkpoints.save(app_id, p)
                except Exception as exc:  # the first write of a run: nothing else has been written
                    raise CheckpointRefused(
                        f"the back-fill checkpoint of {app_id} could not be written ({type(exc).__name__}). If the graph store's app schema is older "
                        "than this release (it must list scopeBackfillStructureGeneration), restart a connector or indexing service, which applies "
                        "the schema at start, and run again. Nothing was written."
                    ) from exc
                if unsafe and not await self._graph.update_node(app_id, CollectionNames.APPS.value, {UNSAFE_KEY: False}):
                    raise RuntimeError("the unsafe mark of the connector was not cleared")
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
