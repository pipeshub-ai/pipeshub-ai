"""System registry. Adding a competitor = one factory + one entry below.

Capabilities are declared statically so offline stages (score, report) know
what to measure without constructing an adapter or touching the network.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from benchmarks.frames.config import RunConfig, SystemConfig
from benchmarks.frames.corpus.view import CorpusView
from benchmarks.frames.errors import ConfigError
from benchmarks.frames.models import CorpusManifest, IndexReport, IngestManifest
from benchmarks.frames.pricing import price_for
from benchmarks.frames.systems.base import AdapterCapabilities, CorpusIngestor, PreparedCorpus, SystemAdapter
from benchmarks.frames.systems.baselines.bm25 import DEFAULT_N_DOCS, Bm25Answerer
from benchmarks.frames.systems.baselines.closed_book import ClosedBookAnswerer
from benchmarks.frames.systems.baselines.oracle import OracleAnswerer
from benchmarks.frames.systems.pipeshub.adapter import PipesHubAdapter
from benchmarks.frames.systems.pipeshub.indexing import IndexWaiter
from benchmarks.frames.systems.pipeshub.ingest import PipesHubIngestor
from benchmarks.frames.systems.pipeshub.kb_api import KnowledgeBaseApi
from benchmarks.frames.systems.pipeshub.session import ConnectorApi
from benchmarks.frames.systems.rag.answerer import RagAnswerer, RagOptions

if TYPE_CHECKING:
    from benchmarks.frames.services import Services


@dataclass(frozen=True)
class AdapterDeps:
    config: RunConfig
    services: Services
    corpus: CorpusView


AdapterFactory = Callable[[SystemConfig, AdapterDeps], SystemAdapter]


@dataclass(frozen=True)
class AdapterSpec:
    factory: AdapterFactory
    capabilities: AdapterCapabilities


def _answerer_kwargs(deps: AdapterDeps) -> dict[str, Any]:
    model = deps.services.model(deps.config.answerer)
    return {"price": price_for(deps.config.pricing, model.model_name), "current_time": deps.config.corpus.snapshot}


def _closed_book(system: SystemConfig, deps: AdapterDeps) -> SystemAdapter:
    return ClosedBookAnswerer(
        system.id, deps.services.llm, deps.services.model(deps.config.answerer), **_answerer_kwargs(deps),
    )


def _oracle(system: SystemConfig, deps: AdapterDeps) -> SystemAdapter:
    return OracleAnswerer(
        system.id, deps.services.llm, deps.services.model(deps.config.answerer), deps.corpus, **_answerer_kwargs(deps),
    )


def _bm25(system: SystemConfig, deps: AdapterDeps) -> SystemAdapter:
    n_docs = int(system.options.get("n_docs", DEFAULT_N_DOCS))
    return Bm25Answerer(
        system.id, deps.services.llm, deps.services.model(deps.config.answerer), deps.corpus,
        n_docs=n_docs, **_answerer_kwargs(deps),
    )


def _pipeshub_ingestor(system: SystemConfig, deps: AdapterDeps) -> PipesHubIngestor:
    """Keyed by corpus version per instance, so every system reading PipesHub's
    index shares one KB and only the first `prepare` uploads anything."""
    services, cfg = deps.services, deps.config
    api = KnowledgeBaseApi(services.session, ConnectorApi(services.session, cfg.pipeshub.connector_url))
    waiter = IndexWaiter(
        lambda kb_id: {r.record_id: r.status for r in api.list_records(kb_id)}, api.reindex,
        poll_interval_s=cfg.pipeshub.index_poll_interval_s, timeout_s=cfg.pipeshub.index_timeout_s,
    )
    return PipesHubIngestor(
        api, system_id=system.id, base_url=cfg.pipeshub.base_url, corpus_dir=deps.corpus.corpus_dir,
        cache_dir=services.cache_dir, batch_size=cfg.pipeshub.upload_batch_size, waiter=waiter,
    )


class _VectorCheckedIngestor:
    """After the index reports done, confirm in Qdrant that every indexed
    article really has points — a COMPLETED status alone doesn't prove it."""

    def __init__(self, inner: CorpusIngestor, check: Callable[[IngestManifest, set[str]], None]) -> None:
        self._inner = inner
        self._check = check

    def prepare(self, manifest: CorpusManifest) -> PreparedCorpus:
        return self._inner.prepare(manifest)

    def wait_ready(self, prepared: PreparedCorpus, manifest: CorpusManifest) -> IndexReport:
        report = self._inner.wait_ready(prepared, manifest)
        if prepared.ingest is not None:
            self._check(prepared.ingest, {d.canonical_url for d in manifest.documents if d.tier == "gold"})
        return report


def _pipeshub_vector_check(deps: AdapterDeps) -> Callable[[IngestManifest, set[str]], None]:
    services = deps.services

    def check(ingest: IngestManifest, gold: set[str]) -> None:
        by_record = {r.record_id: r.canonical_url for r in ingest.records}
        vrids = services.virtual_record_ids(ingest)
        expected = {vrid: by_record[rid] for vrid, rid in vrids.items() if rid in by_record}
        services.verify_vectors(deps.config.qdrant.collection, "metadata.virtualRecordId", expected, gold)

    return check


def _standard_vector_check(deps: AdapterDeps) -> Callable[[IngestManifest, set[str]], None]:
    def check(ingest: IngestManifest, gold: set[str]) -> None:
        expected = {r.canonical_url: r.canonical_url for r in ingest.records}
        deps.services.verify_vectors(ingest.kb_id, "url", expected, gold)

    return check


def _pipeshub(system: SystemConfig, deps: AdapterDeps) -> SystemAdapter:
    services, cfg = deps.services, deps.config
    ingestor = _VectorCheckedIngestor(_pipeshub_ingestor(system, deps), _pipeshub_vector_check(deps))
    return PipesHubAdapter(
        system.id, services.session, ingestor, services.model(cfg.answerer), services.guard,
        current_time=cfg.corpus.snapshot, stream_timeout_s=cfg.pipeshub.stream_timeout_s,
        price=_answerer_kwargs(deps)["price"],
    )


def _rag(options: RagOptions, system: SystemConfig, deps: AdapterDeps) -> SystemAdapter:
    services = deps.services
    if options.index == "standard":
        ingestor: CorpusIngestor = _VectorCheckedIngestor(
            services.standard_ingestor(system.id, deps.corpus), _standard_vector_check(deps),
        )
        index_factory = lambda ingest: services.standard_index(ingest, deps.corpus)  # noqa: E731
    else:
        ingestor = _VectorCheckedIngestor(_pipeshub_ingestor(system, deps), _pipeshub_vector_check(deps))
        index_factory = lambda ingest: services.rag_index(ingest, deps.corpus)  # noqa: E731
    return RagAnswerer(
        system.id, services.llm, services.model(deps.config.answerer), options,
        ingestor=ingestor,
        index_factory=index_factory,
        corpus=deps.corpus,
        reranker=services.reranker(options.reranker) if options.rerank else None,
        **_answerer_kwargs(deps),
    )


def _naive_rag(system: SystemConfig, deps: AdapterDeps) -> SystemAdapter:
    """Dense top-k, then answer. Only `top_k` may be tuned."""
    unknown = set(system.options) - {"top_k", "index"}
    if unknown:
        raise ConfigError(f"naive_rag takes only top_k and index; use advanced_rag for {sorted(unknown)}")
    return _rag(RagOptions(**system.options), system, deps)


def _advanced_rag(system: SystemConfig, deps: AdapterDeps) -> SystemAdapter:
    try:
        options = RagOptions(**system.options)
    except ValidationError as exc:
        raise ConfigError(f"{system.id}: invalid advanced_rag options:\n{exc}") from exc
    return _rag(options, system, deps)


ADAPTER_REGISTRY: dict[str, AdapterSpec] = {
    "closed_book": AdapterSpec(_closed_book, ClosedBookAnswerer.capabilities),
    "oracle": AdapterSpec(_oracle, OracleAnswerer.capabilities),
    "bm25": AdapterSpec(_bm25, Bm25Answerer.capabilities),
    "pipeshub": AdapterSpec(_pipeshub, PipesHubAdapter.capabilities),
    "naive_rag": AdapterSpec(_naive_rag, RagAnswerer.capabilities),
    "advanced_rag": AdapterSpec(_advanced_rag, RagAnswerer.capabilities),
}


def adapter_spec(kind: str, registry: Mapping[str, AdapterSpec] | None = None) -> AdapterSpec:
    specs = registry if registry is not None else ADAPTER_REGISTRY
    if kind not in specs:
        raise ConfigError(f"unknown system kind {kind!r}; known: {sorted(specs)}")
    return specs[kind]
