"""System-under-test interfaces.

Split by capability (interface segregation) so each system implements only
what it supports. A competitor adapter next round adds one package under
`systems/` and one `ADAPTER_REGISTRY` entry — no stage changes.
"""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, ConfigDict

from benchmarks.harness.models import AskItem, CorpusManifest, IndexReport, IngestManifest, Prediction, RankedList


class AdapterCapabilities(BaseModel):
    model_config = ConfigDict(frozen=True)

    ingests_corpus: bool = False
    retrieval_trace: bool = False
    citations: bool = False
    ranked_search: bool = False
    # Receives the question's gold refs. True ONLY for an upper-bound
    # system that is *defined* as having perfect retrieval; any other
    # adapter declaring it is reading the answer key.
    needs_gold_refs: bool = False
    # Which corpus index this system reads, for the diagnostics'
    # same-index controls. A RAG adapter overrides it per config.
    reads_index: str = "none"


class PreparedCorpus(BaseModel):
    model_config = ConfigDict(frozen=True)

    system: str
    corpus_version: str
    ingest: IngestManifest | None = None


class CorpusIngestor(Protocol):
    def prepare(self, manifest: CorpusManifest) -> PreparedCorpus: ...

    def wait_ready(self, prepared: PreparedCorpus, manifest: CorpusManifest) -> IndexReport: ...


class RankedRetriever(Protocol):
    def ranked_search(self, item: AskItem, prepared: PreparedCorpus, k: int) -> RankedList: ...


class SystemAdapter(Protocol):
    system_id: str
    capabilities: AdapterCapabilities

    def answer(self, item: AskItem, prepared: PreparedCorpus, repeat: int) -> Prediction: ...

    def ingestor(self) -> CorpusIngestor | None: ...

    def retriever(self) -> RankedRetriever | None: ...
