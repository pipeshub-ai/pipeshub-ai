"""Uploading a pinned corpus into one PipesHub knowledge base, idempotently.

A KB is keyed by corpus version and cached per instance, and upload is a
reconciliation: records already present (matched by name) are not uploaded
again, so an interrupted ingest resumes where it stopped.
"""

from __future__ import annotations

import hashlib
import logging
import time
from collections.abc import Callable, Sequence
from pathlib import Path

import requests

from benchmarks.harness.corpus.manifest import read_article_html, record_name
from benchmarks.harness.errors import IngestError
from benchmarks.harness.models import CorpusDocument, CorpusManifest, IndexReport, IngestedRecord, IngestManifest
from benchmarks.harness.store import atomic_write_text
from benchmarks.harness.systems.base import PreparedCorpus
from benchmarks.harness.systems.pipeshub.indexing import IndexWaiter
from benchmarks.harness.systems.pipeshub.kb_api import KnowledgeBaseApi
from benchmarks.harness.systems.pipeshub.session import PIPESHUB_CLIENT_ERRORS

logger = logging.getLogger(__name__)

KB_NAME_PREFIX = "frames"
_TRANSPORT_RETRIES = 5
_TRANSPORT_BACKOFF_S = 30.0
_HTML_MIME = "text/html"


class PipesHubIngestor:
    def __init__(
        self,
        api: KnowledgeBaseApi,
        *,
        system_id: str,
        base_url: str,
        corpus_dir: Path,
        cache_dir: Path,
        batch_size: int,
        waiter: IndexWaiter,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._api = api
        self._system_id = system_id
        self._base_url = base_url
        self._corpus_dir = corpus_dir
        self._cache_dir = cache_dir / "pipeshub" / hashlib.sha256(base_url.encode()).hexdigest()[:12]
        self._batch_size = batch_size
        self._waiter = waiter
        self._sleep = sleep

    def _cache_file(self, corpus_version: str) -> Path:
        return self._cache_dir / f"{corpus_version}.json"

    def _write_cache(self, ingest: IngestManifest) -> None:
        atomic_write_text(self._cache_file(ingest.corpus_version), ingest.model_dump_json(indent=1))

    def _cached_kb_id(self, corpus_version: str) -> str | None:
        path = self._cache_file(corpus_version)
        if not path.exists():
            return None
        kb_id = IngestManifest.model_validate_json(path.read_text()).kb_id
        return kb_id if self._api.kb_exists(kb_id) else None

    def prepare(self, manifest: CorpusManifest) -> PreparedCorpus:
        version = manifest.corpus_version
        kb_id = self._cached_kb_id(version)
        if kb_id is None:
            kb_id = self._api.create_kb(f"{KB_NAME_PREFIX}-{manifest.tier.lower()}-{version[:12]}")
            # Cached before uploading so an interrupted ingest resumes into
            # this KB instead of starting another.
            self._write_cache(IngestManifest(
                system=self._system_id, kb_id=kb_id, corpus_version=version, base_url=self._base_url, records=[],
            ))
        existing = {r.record_name: r.record_id for r in self._api.list_records(kb_id)}
        missing = [d for d in manifest.documents if record_name(d.filename) not in existing]
        logger.info("KB %s: %d records present, uploading %d", kb_id, len(existing), len(missing))
        existing.update(self._upload(kb_id, missing))
        still_missing = [d for d in missing if record_name(d.filename) not in existing]
        if still_missing:
            # A record can land in the KB without its id reaching us — a batch
            # response that omits entries is enough. Re-list before concluding
            # the upload failed: re-uploading what is already there would both
            # risk duplicates and still leave the id out of the manifest, and a
            # record missing from the manifest is invisible to scoring even
            # though it is indexed and searchable.
            landed = {r.record_name: r.record_id for r in self._api.list_records(kb_id)}
            existing.update({
                name: landed[name] for d in still_missing
                if (name := record_name(d.filename)) in landed
            })
            recovered = [d for d in still_missing if record_name(d.filename) in existing]
            still_missing = [d for d in still_missing if record_name(d.filename) not in existing]
            if recovered:
                logger.info(
                    "KB %s: %d upload(s) landed without returning an id; recovered from the listing",
                    kb_id, len(recovered),
                )
        if still_missing:
            logger.warning("KB %s: retrying %d failed uploads one at a time", kb_id, len(still_missing))
            for doc in still_missing:
                existing.update(self._upload_batch(kb_id, [doc]))
        ingest = IngestManifest(
            system=self._system_id, kb_id=kb_id, corpus_version=version, base_url=self._base_url,
            records=[
                IngestedRecord(record_id=existing[name], record_name=name, canonical_url=d.canonical_url)
                for d in manifest.documents if (name := record_name(d.filename)) in existing
            ],
        )
        if len(ingest.records) != len(manifest.documents):
            # Not a warning: the manifest is what maps a record id back to an
            # article, so a short one reads downstream as a retrieval failure
            # rather than an error — a plausible-looking wrong answer.
            raise IngestError(
                f"KB {kb_id}: ingest manifest has {len(ingest.records)} records for "
                f"{len(manifest.documents)} corpus documents; scoring would silently "
                f"treat the missing ones as unretrievable"
            )
        self._write_cache(ingest)
        return PreparedCorpus(system=self._system_id, corpus_version=version, ingest=ingest)

    def _upload(self, kb_id: str, docs: Sequence[CorpusDocument]) -> dict[str, str]:
        uploaded: dict[str, str] = {}
        for start in range(0, len(docs), self._batch_size):
            batch = docs[start:start + self._batch_size]
            uploaded.update(self._upload_batch(kb_id, batch))
            logger.info("uploaded %d/%d", min(start + self._batch_size, len(docs)), len(docs))
        return uploaded

    def _upload_batch(self, kb_id: str, docs: Sequence[CorpusDocument]) -> dict[str, str]:
        """Survives the backend dropping the connection mid-upload (a service
        restart): wait, re-list the KB, and send only what didn't land."""
        already: dict[str, str] = {}
        for attempt in range(_TRANSPORT_RETRIES + 1):
            try:
                return {**already, **self._upload_batch_once(kb_id, docs)}
            except requests.RequestException as exc:
                if attempt == _TRANSPORT_RETRIES:
                    raise
                logger.warning("upload transport error (%s); retrying in %.0fs", exc, _TRANSPORT_BACKOFF_S)
                self._sleep(_TRANSPORT_BACKOFF_S)
                landed = {r.record_name: r.record_id for r in self._api.list_records(kb_id)}
                already |= {name: landed[name] for d in docs if (name := record_name(d.filename)) in landed}
                docs = [d for d in docs if record_name(d.filename) not in landed]
                if not docs:
                    return already
        return already

    def _upload_batch_once(self, kb_id: str, docs: Sequence[CorpusDocument]) -> dict[str, str]:
        files = [(d.filename, read_article_html(self._corpus_dir, d.filename), _HTML_MIME) for d in docs]
        try:
            result = self._api.upload(kb_id, files)
        except PIPESHUB_CLIENT_ERRORS as exc:
            if len(files) == 1:
                logger.warning("upload of %s failed: %s", files[0][0], exc)
                return {}
            # The helper raises when a whole batch fails; isolate the bad file(s).
            logger.warning("batch upload failed (%s); retrying one file at a time", exc)
            return {name: rid for d in docs for name, rid in self._upload_batch(kb_id, [d]).items()}
        for name in result.failed:
            logger.warning("upload rejected %s", name)
        return {record_name(name): rid for name, rid in result.record_ids.items()}

    def wait_ready(self, prepared: PreparedCorpus, manifest: CorpusManifest) -> IndexReport:
        if prepared.ingest is None:
            raise IngestError("wait_ready called before prepare")
        gold = {d.canonical_url for d in manifest.documents if d.tier == "gold"}
        return self._waiter.wait(prepared.ingest, gold)
