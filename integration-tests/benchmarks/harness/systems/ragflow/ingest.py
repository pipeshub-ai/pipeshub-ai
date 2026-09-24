"""Load the corpus into one RAGFlow dataset and parse it.

Keyed by corpus version, with a local checkpoint of uploaded documents, so a
rerun or a resumed run uploads and parses only what is missing.
"""

from __future__ import annotations

import json
import logging
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, Any

from benchmarks.harness.corpus.manifest import read_article_html
from benchmarks.harness.errors import IngestError
from benchmarks.harness.models import CorpusDocument, CorpusManifest, IndexReport, IngestedRecord, IngestManifest
from benchmarks.harness.store import atomic_write_text
from benchmarks.harness.systems.base import PreparedCorpus
from benchmarks.harness.systems.ragflow.client import RUN_DONE, RUN_FAILED

if TYPE_CHECKING:
    from pathlib import Path

    from benchmarks.harness.systems.ragflow.client import RagflowClient

logger = logging.getLogger(__name__)

_HTML_MIME = "text/html"
# The largest page RAGFlow's document listing accepts.
_PAGE_SIZE = 100


def dataset_name(corpus_version: str) -> str:
    return f"frames-{corpus_version[:12]}"


class RagflowIngestor:
    def __init__(
        self,
        client: RagflowClient,
        *,
        system_id: str,
        base_url: str,
        corpus_dir: Path,
        cache_dir: Path,
        dataset_config: dict[str, Any],
        batch_size: int = 50,
        upload_workers: int = 4,
        poll_interval_s: float = 60.0,
        timeout_s: float = 259_200.0,
    ) -> None:
        self._client = client
        self._system_id = system_id
        self._base_url = base_url
        self._corpus_dir = corpus_dir
        self._cache_dir = cache_dir / "ragflow"
        self._dataset_config = dataset_config
        self._batch_size = batch_size
        self._upload_workers = upload_workers
        self._poll_interval_s = poll_interval_s
        self._timeout_s = timeout_s

    def _checkpoint(self, dataset_id: str) -> Path:
        return self._cache_dir / f"{dataset_id}.documents.json"

    def _load(self, dataset_id: str) -> dict[str, str]:
        path = self._checkpoint(dataset_id)
        return json.loads(path.read_text()) if path.exists() else {}

    def _save(self, dataset_id: str, uploaded: dict[str, str]) -> None:
        path = self._checkpoint(dataset_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(path, json.dumps(uploaded, indent=0, sort_keys=True))

    def prepare(self, manifest: CorpusManifest) -> PreparedCorpus:
        name = dataset_name(manifest.corpus_version)
        dataset_id = self._client.dataset_id(name) or self._client.create_dataset({**self._dataset_config, "name": name})
        uploaded = self._load(dataset_id)  # canonical_url -> document id
        todo = [d for d in manifest.documents if d.canonical_url not in uploaded]
        logger.info("%s: %d documents uploaded, %d to go", name, len(uploaded), len(todo))

        def upload(batch: list[CorpusDocument]) -> dict[str, str]:
            files = [(d.filename, read_article_html(self._corpus_dir, d.filename), _HTML_MIME) for d in batch]
            ids_by_name = self._client.upload(dataset_id, files)
            missing = [d.filename for d in batch if d.filename not in ids_by_name]
            if missing:
                raise IngestError(f"{self._system_id}: RAGFlow did not accept {missing[:5]}")
            self._client.parse(dataset_id, list(ids_by_name.values()))
            return {d.canonical_url: ids_by_name[d.filename] for d in batch}

        batches = [todo[i:i + self._batch_size] for i in range(0, len(todo), self._batch_size)]
        with ThreadPoolExecutor(self._upload_workers) as pool:
            for n, landed in enumerate(pool.map(upload, batches), start=1):
                uploaded.update(landed)
                self._save(dataset_id, uploaded)
                if n % 10 == 0:
                    logger.info("%s: uploaded %d/%d", name, len(uploaded), len(manifest.documents))

        by_url = {d.canonical_url: d for d in manifest.documents}
        records = [
            IngestedRecord(record_id=doc_id, record_name=by_url[url].filename, canonical_url=url)
            for url, doc_id in uploaded.items() if url in by_url
        ]
        ingest = IngestManifest(
            system=self._system_id, kb_id=dataset_id, corpus_version=manifest.corpus_version,
            base_url=self._base_url, records=records,
        )
        return PreparedCorpus(system=self._system_id, corpus_version=manifest.corpus_version, ingest=ingest)

    def _statuses(self, dataset_id: str) -> dict[str, str]:
        statuses: dict[str, str] = {}
        page = 1
        while True:
            docs, total = self._client.documents(dataset_id, page, _PAGE_SIZE)
            statuses.update({str(d["id"]): str(d.get("run", "")) for d in docs})
            if not docs or len(statuses) >= total:
                return statuses
            page += 1

    def wait_ready(self, prepared: PreparedCorpus, manifest: CorpusManifest) -> IndexReport:
        if prepared.ingest is None:
            raise IngestError(f"{self._system_id}: prepare produced no ingest manifest")
        started = time.monotonic()
        wanted = {r.record_id for r in prepared.ingest.records}
        while True:
            statuses = {doc_id: run for doc_id, run in self._statuses(prepared.ingest.kb_id).items() if doc_id in wanted}
            pending = [d for d in wanted if statuses.get(d) not in ({RUN_DONE} | RUN_FAILED)]
            if not pending:
                break
            if time.monotonic() - started > self._timeout_s:
                raise IngestError(f"{self._system_id}: {len(pending)} documents still parsing after {self._timeout_s}s")
            logger.info("%s: %d/%d documents still parsing", self._system_id, len(pending), len(wanted))
            time.sleep(self._poll_interval_s)

        url_of = {r.record_id: r.canonical_url for r in prepared.ingest.records}
        done = {url_of[d] for d, run in statuses.items() if run == RUN_DONE}
        gold = {d.canonical_url for d in manifest.documents if d.tier == "gold"}
        return IndexReport(
            total=len(statuses),
            status_counts=dict(Counter(statuses.values())),
            gold_total=len(gold),
            gold_indexed=len(gold & done),
            unindexed_urls=sorted(url_of[d] for d, run in statuses.items() if run != RUN_DONE),
            elapsed_s=time.monotonic() - started,
        )
