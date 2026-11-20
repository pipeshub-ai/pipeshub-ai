"""Reference model for PR 6107: the half-moved-record family, against a stateful reference model.

Own world (records + points per (vrid, collection) + stored blobs), own generator, own seeds. The real
``EventProcessor.on_event`` and the real ``_orchestrate_via_services`` run; only the stores are modelled.

Oracle (the rule in page_limit.py's docstring): an over-limit PDF whose record has a searchable index and whose content
is not proven changed keeps that index. After the event(s) settle, the record must be SEARCHABLE: flagged searchable AND
a member of points that exist under the vrid it names, in its own collection.

``run_matrix(seed)`` yields ``(scenario key, verdict)`` for every cell of crash point x retry x keep-old-copy x sharing x labels x
flag; a verdict other than ``kept`` is a record the page limit lost, left unsearchable, or took the index of.
"""
# ruff: noqa: ANN201, ANN202
from __future__ import annotations

import asyncio
import hashlib
import itertools
import random
from contextlib import ExitStack
from unittest.mock import AsyncMock, MagicMock, patch

from app.events import events as ev
from app.modules.parsers.pdf.page_count import Unavailable
from app.services.graph_db.common.record_visibility import (
    PENDING_VIRTUAL_RECORD_ID,
    is_searchable_record,
)
from tests.unit.events.test_events import _drain, _make_event_processor

BUF = b"%PDF-1.4 advisor probe bytes"
MD5 = hashlib.md5(BUF).hexdigest()
V1 = "v-orig"
RUNNING = ("IN_PROGRESS", "QUEUED")


class Crash(Exception):
    pass


class Model:
    """Graph records, vector points with membership per (vrid, collection), stored content per vrid."""

    def __init__(self, me: dict, twins: list[dict]) -> None:
        self.records = {"rec-1": {"_key": "rec-1", "recordType": "FILE", "orgId": "org-1", **me}}
        for i, t in enumerate(twins):
            self.records[f"twin-{i}"] = {"_key": f"twin-{i}", "recordType": "FILE", "orgId": "org-1", **t}
        self.points: dict[tuple[str, str], set[str]] = {}
        for key, doc in self.records.items():
            self.points.setdefault((doc["virtualRecordId"], doc["coll"]), set()).add(key)
        self.blobs = {V1}
        self.log: list[str] = []

    # --- graph ---
    def holders(self, vrid: str) -> list[str]:
        return [
            k for k, d in self.records.items()
            if d.get("virtualRecordId") == vrid
            or (d.get(PENDING_VIRTUAL_RECORD_ID) == vrid and d.get("indexingStatus") in RUNNING)
        ]

    async def get_document(self, key, collection, **_kw):
        doc = self.records.get(key)
        return dict(doc) if doc is not None else None

    def _merge(self, key, fields):
        rec = self.records[key]
        for name, value in fields.items():
            if value is None:
                rec.pop(name, None)
            else:
                rec[name] = value

    async def update_node(self, key, collection, fields, *_a, **_kw):
        self._merge(key, fields)
        return True

    async def write_attempt_start(self, key, fields, *_a, **_kw):
        # record_diagnostics attempt-start rule: a new revision drops the count of the old bytes (D-75)
        if "contentRev" in fields and fields["contentRev"] != self.records[key].get("contentRev"):
            self.records[key].pop("pageCount", None)
        self._merge(key, fields)
        return True

    async def update_node_fields_if_match(self, key, collection, updates, expected, *_a, unset=(), **_kw):
        rec = self.records[key]
        if any(rec.get(n) != v for n, v in expected.items()):
            return False
        self._merge(key, updates)
        for n in unset:
            rec.pop(n, None)
        return True

    async def get_records_by_virtual_record_id(self, vrid, *_a, **_kw):
        return [k for k, d in self.records.items() if d.get("virtualRecordId") == vrid]

    # --- vector membership: membership.py:1057-1098 (rewrite, or delete when no holder) and :920 (drop a collection no holder is in) ---
    async def rewrite_or_delete(self, vrid):
        self.log.append(f"rewrite({vrid})")
        holders = self.holders(vrid)
        if not holders:
            for k in [k for k in self.points if k[0] == vrid]:
                del self.points[k]
            self.blobs.discard(vrid)
            return "deleted"
        for k in [k for k in self.points if k[0] == vrid]:
            here = {h for h in holders if self.records[h]["coll"] == k[1]}
            if here:
                self.points[k] = here
            else:
                del self.points[k]
        return "rewritten"

    def cleanup_storage(self, org_id, vrid):
        self.log.append(f"cleanup({vrid})")
        if not self.holders(vrid):
            self.blobs.discard(vrid)
        return MagicMock()

    async def sync(self, vrid):
        """membership.sync_vector_membership: the arrays of the points that exist follow the holders; it never deletes."""
        self.log.append(f"sync({vrid})")
        holders = self.holders(vrid)
        for k in [k for k in self.points if k[0] == vrid]:
            here = {h for h in holders if self.records[h]["coll"] == k[1]}
            if here:
                self.points[k] = here

    # --- oracle ---
    def searchable(self, key="rec-1") -> bool:
        rec = self.records[key]
        return is_searchable_record(rec) and key in self.points.get((rec.get("virtualRecordId"), rec["coll"]), set())


def run_attempt(model: Model, *, payload_vrid, event_type, early, parser_pages, crash, koc, label, refused: list, recon_meta: bool = True):
    """One delivery through the real on_event. ``crash`` names the step that raises."""
    ep, _logger, processor, gp = _make_event_processor()
    for name in ("get_document", "update_node", "update_node_fields_if_match", "get_records_by_virtual_record_id", "write_attempt_start"):
        setattr(gp, name, getattr(model, name))

    async def rewrite(vrid):
        me = model.records["rec-1"]
        if crash == "keep_rewrite" and vrid == V1 and me.get("virtualRecordId") == V1:
            raise Crash("vector store down during the keep's membership rewrite")
        return await model.rewrite_or_delete(vrid)

    processor.indexing_pipeline.rewrite_or_delete_vector_membership = AsyncMock(side_effect=rewrite)

    async def sync(vrid):
        me = model.records["rec-1"]
        if crash == "keep_rewrite" and vrid == V1 and me.get("virtualRecordId") == V1:
            raise Crash("vector store down during the keep's membership sync")
        return await model.sync(vrid)

    processor.indexing_pipeline.sync_vector_membership = AsyncMock(side_effect=sync)

    async def holding(vrid):
        return {"org-1"} if any(k[0] == vrid for k in model.points) else set()

    processor.indexing_pipeline.orgs_holding_points = AsyncMock(side_effect=holding)

    async def mark(record_id, status, reason=None, extra_fields=None):
        model._merge(record_id, {"indexingStatus": status.value, "reason": reason, **(extra_fields or {})})
        refused.append(reason)

    processor._mark_record = AsyncMock(side_effect=mark)

    async def unindex(record_doc, org_id, *, processor, logger, fallback_vrid=None):
        vrid = record_doc.get("virtualRecordId") or fallback_vrid
        model._merge("rec-1", {"virtualRecordId": None})
        if vrid:
            await model.rewrite_or_delete(vrid)

    async def parse(**_kw):
        if crash == "parse":
            raise Crash("parser died after the run prepared the record")
        return MagicMock(block_container=MagicMock(blocks=[MagicMock()], block_groups=[]), metadata={"page_count": parser_pages}, provider_used=None)

    def spawn(org_id, vrid):
        if crash == "before_strip":
            raise Crash("process died between the id write and the old id's rewrite")
        return model.cleanup_storage(org_id, vrid)

    ep.parsing_client = MagicMock(circuit_open=False)
    ep.parsing_client.parse = AsyncMock(side_effect=parse)
    ep.extraction_client = MagicMock()
    ep.sink_orchestrator = MagicMock()
    ep.sink_orchestrator.blob_storage.get_reconciliation_metadata = AsyncMock(return_value={"m": 1} if recon_meta else None)

    async def stored(org_id, vrid):
        return vrid in model.blobs

    async def stop_after_commit(*_a, **_kw):
        raise Crash("stop: the file went on to be indexed (not modelled further)")

    with ExitStack() as stack:
        stack.enter_context(patch("app.modules.indexing.unindex.unindex_record", new=AsyncMock(side_effect=unindex)))
        stack.enter_context(patch.object(ep, "_check_duplicate_by_md5", new_callable=AsyncMock, return_value=ev.DedupDecision()))
        stack.enter_context(patch("app.events.events.may_resume", return_value=False))
        stack.enter_context(patch("app.events.events.notify_record_indexed", new_callable=AsyncMock))
        stack.enter_context(patch("app.modules.parsers.pdf.page_count.count_pdf_pages", new_callable=AsyncMock, return_value=early))
        stack.enter_context(patch.object(ep, "_use_service_pipeline", return_value=True))
        stack.enter_context(patch.object(ep, "_spawn_abandoned_vrid_cleanup", side_effect=spawn))
        stack.enter_context(patch.object(ep, "_vrid_has_stored_content", side_effect=stored))
        stack.enter_context(patch.object(ep, "_persist_parsing_completed", side_effect=stop_after_commit))
        stack.enter_context(patch("app.events.events.keep_old_copy.swap_allowed", new=AsyncMock(return_value=koc)))
        stack.enter_context(patch("app.events.events.start_run", return_value=MagicMock()))
        stack.enter_context(patch("app.modules.transformers.block_container_validator.BlockContainerValidator", MagicMock()))
        stack.enter_context(patch("app.events.events.content_revision", return_value="rev-of-these-bytes"))
        payload = {
            "recordId": "rec-1", "orgId": "org-1", "virtualRecordId": payload_vrid, "version": 1, "connectorName": "",
            "recordName": "report.pdf", "buffer": BUF, "deliveryId": "d-1",
        }
        event = {"payload": payload, "eventType": event_type}
        if label == "pdf":
            payload.update(extension="pdf", mimeType="application/pdf")
        else:
            # Labels the dispatch reads as a PDF (file_types.legacy_route) but the raw membership test at events.py:2520-2524 does not
            # count as a reconciliation type: an upper-case extension with a generic mime.
            payload.update(extension="PDF", mimeType="application/octet-stream")
        try:
            asyncio.run(_drain(ep.on_event(event)))
            return "ok"
        except Crash as c:
            return f"crash:{c}"


def scenario(rng: random.Random, share, label, crash, retry_msg, retry_count, koc, failure_write):
    status = rng.choice(["COMPLETED", "QUEUED"])
    flagged = rng.choice([True, True, False]) or status != "COMPLETED"
    me = {"indexingStatus": status, "virtualRecordId": V1, "contentRev": "rev-indexed", "md5Checksum": MD5, "coll": "c-a"}
    if flagged:
        me["hasIndexedCopy"] = True
    if rng.random() < 0.5:
        me["pageCount"] = rng.choice([501, 700, 4000])  # an over-limit copy indexed earlier; absent = before counts were kept
    twins = []
    if share != "unshared":
        twins = [
            {"indexingStatus": "COMPLETED", "hasIndexedCopy": True, "virtualRecordId": V1, "md5Checksum": MD5,
             "coll": "c-a" if share == "same-collection" else "c-b"}
            for _ in range(rng.choice([1, 2]))
        ]
    model = Model(me, twins)
    assert model.searchable()
    refused: list = []
    trace = []
    recon = rng.choice([True, False])
    first = run_attempt(model, payload_vrid=V1, event_type="updateRecord", early=Unavailable(), parser_pages=rng.choice([501, 600, 9000]),
                        crash=crash, koc=koc, label=label, refused=refused, recon_meta=recon)
    trace.append(("attempt1", first, dict(model.records["rec-1"])))
    if crash is not None:
        if first.startswith("crash") and failure_write and not refused:
            model._merge("rec-1", {"indexingStatus": "FAILED", "reason": "attempt failed"})
        if retry_msg == "same-message":
            vrid, etype = V1, "updateRecord"  # the broker redelivers the same payload
        else:
            # indexing_main.py:487-520: the stranded-record recovery rebuilds the payload from the record as it is now
            vrid, etype = model.records["rec-1"].get("virtualRecordId"), "reindexRecord"
        early = 600 if retry_count == "known" else Unavailable()
        second = run_attempt(model, payload_vrid=vrid, event_type=etype, early=early, parser_pages=600, crash=None, koc=koc,
                             label=label, refused=refused, recon_meta=recon)
        trace.append(("attempt2", second, dict(model.records["rec-1"])))
    rec = model.records["rec-1"]
    if refused:
        verdict = "DROPPED(refused)"
    elif not model.searchable():
        verdict = "PHANTOM(kept, not searchable)" if is_searchable_record(rec) else "LOST(not searchable)"
    elif rec.get("virtualRecordId") != V1:
        verdict = "MOVED"
    else:
        verdict = "kept"
    extra = "+pending-left" if rec.get(PENDING_VIRTUAL_RECORD_ID) else ""
    return verdict + extra, trace, model




GRID = list(itertools.product(
    ["unshared", "same-collection", "other-collection"],
    ["pdf", "upper-ext"],
    [None, "before_strip", "parse", "keep_rewrite"],
    ["same-message", "recovery-message"],
    ["known", "unavailable"],
    [False, True],
))


def run_matrix(seed: int):
    """Every cell of the matrix once: yields ``(key, verdict, trace, model)``."""
    rng = random.Random(seed)
    for share, label, crash, retry_msg, retry_count, koc in GRID:
        if crash is None and (retry_msg, retry_count) != ("same-message", "known"):
            continue  # no retry: one row is enough
        for failure_write in ([False] if crash is None else [False, True]):
            verdict, trace, model = scenario(rng, share, label, crash, retry_msg, retry_count, koc, failure_write)
            key = (share, label, crash or "-", retry_msg if crash else "-", retry_count if crash else "-",
                   "koc-on" if koc else "koc-off", "FAILED-written" if failure_write else "no-status-write")
            yield key, verdict, trace, model


def within_the_limit(share: str, label: str, koc: bool):
    """A file within the limit: the isolation still happens, after the late check. Returns ``(record, model)``."""
    me = {"indexingStatus": "COMPLETED", "hasIndexedCopy": True, "virtualRecordId": V1, "contentRev": "rev-indexed",
          "md5Checksum": MD5, "coll": "c-a"}
    twins = [] if share == "unshared" else [{"indexingStatus": "COMPLETED", "hasIndexedCopy": True, "virtualRecordId": V1,
                                              "md5Checksum": MD5, "coll": "c-a" if share == "same-collection" else "c-b"}]
    model = Model(me, twins)
    run_attempt(model, payload_vrid=V1, event_type="updateRecord", early=Unavailable(), parser_pages=300, crash=None, koc=koc,
                label=label, refused=[])
    return model.records["rec-1"], model
