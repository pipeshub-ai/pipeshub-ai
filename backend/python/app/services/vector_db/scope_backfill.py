"""`ScopeBackfill`: write the scope fields of the points that already exist, one connector instance at a time.

Run by an operator (`python -m app.scripts.scope_projection`), never by itself, and only while the scope fields are on
(`scope_fields_enabled`). It computes nothing of its own: the fields of a point are `scope_fields_for_state`, the function
the live writers use, so a back-filled point carries what a newly indexed one would.

- **Resumable.** The records of the connector are read in key order, a page at a time. After a page is written its last key is
  saved on the app document (`scopeBackfillAfter`); a run that dies anywhere starts again at the last saved key and writes the
  page it was on a second time (the fields are a function of the graph, so that changes nothing). A connector whose saved
  version is not the current `SCOPE_FIELDS_VERSION` starts over.
- **Bounded.** One run handles at most `max_pages` pages and returns `running` when there are more; the operator runs it again.
- **Org-scoped.** Every graph read names the organisation, and every write only matches points of that organisation.
- **Fail closed.** A point whose fields could not be computed or written gets the stale marker (legacy, version 0), which keeps
  it on the legacy filter, and is counted in `scopeBackfillStale`. The back-fill never leaves a guess on a point. If even the
  marker cannot be written the point keeps what it had, which a live writer put there from the graph.
- **Safe beside live indexing.** The back-fill writes a point's scope keys only, never its membership arrays, under the same
  per-virtual-record lock the live writers take (so in this process the two do not interleave). Across processes there is no lock,
  so every write is followed by a second read of the graph: if the fields differ from what was written, the graph changed
  between the first read and the write, and the point is written again from the new graph (up to `MAX_ROUNDS`, then it is marked
  stale). A live write that lands after ours has read the graph after the change and is right; one that landed before ours is
  corrected by the second read. A live writer that read the graph long before it wrote (a held-up process) is not stopped here:
  that needs a fence, which is a separate change.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.config.constants.arangodb import CollectionNames
from app.core.org_context import for_org
from app.services.permissions.metrics import SCOPE_STAMP_FAILED
from app.services.permissions.mode import scope_fields_enabled
from app.services.permissions.scope_fields import SCOPE_FIELDS_VERSION, ScopeFields
from app.services.vector_db.membership import (
    STALE_SCOPE,
    resolve_virtual_record_state,
    scope_fields_for_state,
    vrid_write_locks,
)

if TYPE_CHECKING:
    import logging

    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
    from app.services.vector_db.interface.vector_db import IVectorDBService
    from app.services.vector_db.membership import CollectionLocator

PAGE_SIZE = 100
MAX_PAGES = 20  # records per run: 2,000 at the default page size
MAX_ROUNDS = 3  # write + read-back rounds for one virtual record before it is marked stale

RUNNING, DONE, FAILED = "running", "done", "failed"


class ScopeFieldsOff(RuntimeError):
    """The back-fill was asked for while the scope fields are not written: nothing else would keep them current."""


class UnknownConnector(ValueError):
    """The connector instance is not an app of this organisation."""


@dataclass
class Progress:
    state: str = RUNNING
    after: str | None = None
    version: int = SCOPE_FIELDS_VERSION
    records: int = 0
    stale: int = 0
    error: str | None = None

    def line(self) -> str:
        return f"{self.state}  version={self.version} records={self.records} stale={self.stale} after={self.after or '-'}" + (
            f" error={self.error}" if self.error else "")


_KEYS = {"state": "scopeBackfillState", "after": "scopeBackfillAfter", "version": "scopeBackfillVersion",
         "records": "scopeBackfillRecords", "stale": "scopeBackfillStale", "error": "scopeBackfillError"}


class AppDocCheckpoints:
    """The checkpoint lives on the connector's app document, so it survives the process and needs no other store."""

    def __init__(self, graph_provider: IGraphDBProvider) -> None:
        self._graph = graph_provider

    async def load(self, app_id: str) -> Progress | None:
        doc = await self._graph.get_document(app_id, CollectionNames.APPS.value)
        if not doc or doc.get(_KEYS["state"]) is None:
            return None
        return Progress(str(doc[_KEYS["state"]]), doc.get(_KEYS["after"]), int(doc.get(_KEYS["version"]) or 0),
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
        if not org_id:
            raise UnknownConnector("an organisation is required")
        with for_org(org_id, job="scope-backfill"):
            stamps = self._graph.permission_stamp_graph()
            if not (await stamps.app(org_id, app_id)).connector_type:
                raise UnknownConnector(f"{app_id} is not a connector of this organisation")
            p = await self._checkpoints.load(app_id)
            if p is None or restart or p.version != SCOPE_FIELDS_VERSION:
                p = Progress()
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
                    for vrid in sorted({n.vrid for n in nodes.values() if n.vrid and not n.deleted}):
                        p.stale += await self._stamp(org_id, vrid) == "stale"
                    p.after, p.records = ids[-1], p.records + len(ids)
                    await self._checkpoints.save(app_id, p)  # only after the page is written
            except Exception as exc:
                p.state, p.error = FAILED, type(exc).__name__
                SCOPE_STAMP_FAILED.inc("backfill")
                self._log.warning("scope back-fill of %s stopped at %s: %s", app_id, p.after, p.error)
            await self._checkpoints.save(app_id, p)
            return p

    async def _stamp(self, org_id: str, vrid: str) -> str:
        """`done`, `skipped` (no point to write) or `stale` (the marker was written, or could not be)."""
        try:
            async with vrid_write_locks([vrid]):
                for _ in range(MAX_ROUNDS):
                    state = await resolve_virtual_record_state(self._graph, vrid)
                    collections = self._locator.collections_for_records(state.records)
                    if not collections:
                        return "skipped"
                    fields = await scope_fields_for_state(self._graph, state, org_id, self._log)
                    await self._write(collections, org_id, vrid, fields)
                    if fields == STALE_SCOPE:
                        return "stale"
                    # the graph may have changed between the read and the write: write again if it says something else now
                    again = await scope_fields_for_state(self._graph, await resolve_virtual_record_state(self._graph, vrid), org_id, self._log)
                    if again == fields:
                        return "done"
                await self._write(collections, org_id, vrid, STALE_SCOPE)
                return "stale"
        except Exception as exc:
            SCOPE_STAMP_FAILED.inc("backfill")
            self._log.warning("scope fields of %s not written: %s", vrid, type(exc).__name__)
            try:
                await self._write(await self._locator.all_collections(fresh=True), org_id, vrid, STALE_SCOPE)
            except Exception as inner:
                self._log.error("could not mark %s stale: %s", vrid, type(inner).__name__)
            return "stale"

    async def _write(self, collections: list[str] | tuple[str, ...], org_id: str, vrid: str, fields: ScopeFields) -> None:
        flt = await self._vdb.filter_collection(must={"orgId": org_id, "virtualRecordId": vrid})
        for name in collections:
            await self._vdb.set_payload(name, fields.to_payload(), flt)
