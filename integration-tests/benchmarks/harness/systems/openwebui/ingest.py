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
from benchmarks.harness.models import CorpusManifest, IndexReport, IngestedRecord, IngestManifest
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
        poll_interval_s: float = 30.0,
        timeout_s: float = 172_800.0,
    ) -> None:
        self._client = client
        self._system_id = system_id
        self._base_url = base_url
        self._corpus_dir = corpus_dir
        self._cache_dir = cache_dir / "openwebui"
        self._upload_workers = upload_workers
        self._poll_interval_s = poll_interval_s
        self._timeout_s = timeout_s

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
        uploaded = self._load(knowledge_id)  # canonical_url -> file_id
        todo = [d for d in manifest.documents if d.canonical_url not in uploaded]
        logger.info("%s: %d files uploaded, %d to go", name, len(uploaded), len(todo))

        def upload(document: object) -> tuple[str, str]:
            content = read_article_html(self._corpus_dir, document.filename)  # type: ignore[attr-defined]
            file_id = self._client.upload_file(document.filename, content, _HTML_MIME, knowledge_id)  # type: ignore[attr-defined]
            return document.canonical_url, file_id  # type: ignore[attr-defined]

        with ThreadPoolExecutor(self._upload_workers) as pool:
            for n, (url, file_id) in enumerate(pool.map(upload, todo), start=1):
                uploaded[url] = file_id
                if n % 200 == 0:
                    self._save(knowledge_id, uploaded)
                    logger.info("%s: uploaded %d/%d", name, n, len(todo))
        self._save(knowledge_id, uploaded)

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

    def wait_ready(self, prepared: PreparedCorpus, manifest: CorpusManifest) -> IndexReport:
        if prepared.ingest is None:
            raise IngestError(f"{self._system_id}: prepare produced no ingest manifest")
        started = time.monotonic()
        pending = {r.record_id: r.canonical_url for r in prepared.ingest.records}
        status: dict[str, str] = {}
        while True:
            for file_id in list(pending):
                state = self._client.file_status(file_id)
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
            status_counts=dict(Counter(status.values())),
            gold_total=len(gold),
            gold_indexed=len(gold & done),
            unindexed_urls=sorted(url_of[f] for f, s in status.items() if s != FILE_DONE),
            elapsed_s=time.monotonic() - started,
        )
