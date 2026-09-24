"""Load the corpus into one Open WebUI knowledge base.

Keyed by corpus version, with a local checkpoint of uploaded files, so a
rerun or a resumed run reuses what is already there and uploads only what is
missing.
"""

from __future__ import annotations

import json
import logging
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING

from benchmarks.harness.corpus.manifest import read_article_html
from benchmarks.harness.errors import IngestError
from benchmarks.harness.models import INDEXED, CorpusDocument, CorpusManifest, IndexReport, IngestedRecord, IngestManifest
from benchmarks.harness.store import atomic_write_text
from benchmarks.harness.systems.base import PreparedCorpus
from benchmarks.harness.systems.openwebui.client import FILE_DONE, FILE_FAILED

if TYPE_CHECKING:
    from pathlib import Path

    from benchmarks.harness.systems.openwebui.client import OpenWebUIClient

logger = logging.getLogger(__name__)

_HTML_MIME = "text/html"


def knowledge_name(corpus_version: str) -> str:
    return f"frames-{corpus_version[:12]}"


class OpenWebUIIngestor:
    def __init__(
        self,
        client: OpenWebUIClient,
        *,
        system_id: str,
        base_url: str,
        corpus_dir: Path,
        cache_dir: Path,
        upload_workers: int = 8,
        window: int = 64,
        poll_interval_s: float = 30.0,
        timeout_s: float = 172_800.0,
        stale_after_s: float = 1_800.0,
        upload_rounds: int = 3,
    ) -> None:
        self._client = client
        self._system_id = system_id
        self._base_url = base_url
        self._corpus_dir = corpus_dir
        self._cache_dir = cache_dir / "openwebui"
        self._upload_workers = upload_workers
        self._window = window
        self._poll_interval_s = poll_interval_s
        self._timeout_s = timeout_s
        self._stale_after_s = stale_after_s
        self._upload_rounds = upload_rounds

    def _reconcile(self, knowledge_id: str, manifest: CorpusManifest) -> dict[str, str]:
        """What the knowledge base already holds, by file name, merged over
        the local checkpoint. An upload that landed after the last checkpoint
        was written is found here instead of being uploaded twice."""
        uploaded = self._load(knowledge_id)
        by_filename = {d.filename: d.canonical_url for d in manifest.documents}
        linked: set[str] = set()
        for filename, file_ids in self._client.knowledge_files(knowledge_id).items():
            url = by_filename.get(filename)
            if url is None:
                continue
            if len(file_ids) > 1:
                logger.warning("%s is in the knowledge base %d times", filename, len(file_ids))
            if uploaded.get(url) not in file_ids:
                uploaded[url] = file_ids[0]
            linked.update(file_ids)
        # A file that failed processing, or whose processing died with a
        # server restart, was never linked; forget it so it is uploaded again
        # rather than reported unindexed for good.
        lost = [url for url, file_id in uploaded.items() if file_id not in linked and self._is_lost(file_id)]
        for url in lost:
            del uploaded[url]
        if lost:
            logger.info("%d files failed or stalled processing and will be uploaded again", len(lost))
        return uploaded

    def _is_lost(self, file_id: str) -> bool:
        status, updated_at = self._client.file_state(file_id)
        if status == FILE_FAILED:
            return True
        return status != FILE_DONE and time.time() - updated_at > self._stale_after_s

    def _await_processed(self, file_ids: list[str]) -> None:
        pending = set(file_ids)
        started = time.monotonic()
        # A file still pending past the stale limit is left to the next
        # round's reconcile rather than waited on.
        while pending and time.monotonic() - started < self._stale_after_s:
            pending = {f for f in pending if self._client.file_status(f) not in (FILE_DONE, FILE_FAILED)}
            if pending:
                time.sleep(min(self._poll_interval_s, 5.0))

    def _checkpoint(self, knowledge_id: str) -> Path:
        return self._cache_dir / f"{knowledge_id}.files.json"

    def _load(self, knowledge_id: str) -> dict[str, str]:
        path = self._checkpoint(knowledge_id)
        return json.loads(path.read_text()) if path.exists() else {}

    def _save(self, knowledge_id: str, uploaded: dict[str, str]) -> None:
        path = self._checkpoint(knowledge_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(path, json.dumps(uploaded, indent=0, sort_keys=True))

    def prepare(self, manifest: CorpusManifest) -> PreparedCorpus:
        name = knowledge_name(manifest.corpus_version)
        knowledge_id = self._client.knowledge_id(name) or self._client.create_knowledge(
            name, f"FRAMES corpus {manifest.corpus_version}",
        )
        # Processing fails transiently (database pool, embedding endpoint);
        # each round re-uploads what the previous one lost.
        for _round in range(self._upload_rounds):
            uploaded = self._reconcile(knowledge_id, manifest)  # canonical_url -> file_id
            todo = [d for d in manifest.documents if d.canonical_url not in uploaded]
            logger.info("%s: %d files in the knowledge base, %d to go", name, len(uploaded), len(todo))
            if not todo:
                break
            self._upload(knowledge_id, name, todo, uploaded, len(manifest.documents))

        by_url = {d.canonical_url: d for d in manifest.documents}
        records = [
            IngestedRecord(record_id=file_id, record_name=by_url[url].filename, canonical_url=url)
            for url, file_id in uploaded.items() if url in by_url
        ]
        ingest = IngestManifest(
            system=self._system_id, kb_id=knowledge_id, corpus_version=manifest.corpus_version,
            base_url=self._base_url, records=records,
        )
        return PreparedCorpus(system=self._system_id, corpus_version=manifest.corpus_version, ingest=ingest)

    def _upload(
        self, knowledge_id: str, name: str, todo: list[CorpusDocument], uploaded: dict[str, str], total: int,
    ) -> None:
        def upload(document: CorpusDocument) -> tuple[str, str]:
            content = read_article_html(self._corpus_dir, document.filename)
            return document.canonical_url, self._client.upload_file(document.filename, content, _HTML_MIME, knowledge_id)

        # Upload a window, then wait for it to be processed before the next:
        # Open WebUI processes files in the background, and uploads that run
        # ahead of it pile up until its database pool starves and requests fail.
        with ThreadPoolExecutor(self._upload_workers) as pool:
            for start in range(0, len(todo), self._window):
                landed = dict(pool.map(upload, todo[start:start + self._window]))
                uploaded.update(landed)
                self._save(knowledge_id, uploaded)
                self._await_processed(list(landed.values()))
                logger.info("%s: uploaded %d/%d", name, len(uploaded), total)

    def wait_ready(self, prepared: PreparedCorpus, manifest: CorpusManifest) -> IndexReport:
        if prepared.ingest is None:
            raise IngestError(f"{self._system_id}: prepare produced no ingest manifest")
        started = time.monotonic()
        pending = {r.record_id: r.canonical_url for r in prepared.ingest.records}
        status: dict[str, str] = {}
        while True:
            # Open WebUI links a file only after its chunks are stored, but a
            # status write can still fail afterwards; linked means searchable.
            linked = {f for ids in self._client.knowledge_files(prepared.ingest.kb_id).values() for f in ids}
            for file_id in list(pending):
                state = FILE_DONE if file_id in linked else self._client.file_status(file_id)
                if state in (FILE_DONE, FILE_FAILED):
                    status[file_id] = state
                    pending.pop(file_id)
            if not pending:
                break
            if time.monotonic() - started > self._timeout_s:
                raise IngestError(f"{self._system_id}: {len(pending)} files still processing after {self._timeout_s}s")
            logger.info("%s: %d files still processing", self._system_id, len(pending))
            time.sleep(self._poll_interval_s)

        url_of = {r.record_id: r.canonical_url for r in prepared.ingest.records}
        done = {url_of[f] for f, s in status.items() if s == FILE_DONE}
        gold = {d.canonical_url for d in manifest.documents if d.tier == "gold"}
        return IndexReport(
            total=len(status),
            status_counts=dict(Counter(INDEXED if s == FILE_DONE else s for s in status.values())),
            gold_total=len(gold),
            gold_indexed=len(gold & done),
            unindexed_urls=sorted(url_of[f] for f, s in status.items() if s != FILE_DONE),
            elapsed_s=time.monotonic() - started,
        )
