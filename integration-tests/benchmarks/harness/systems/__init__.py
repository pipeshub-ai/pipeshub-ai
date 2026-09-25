"""System registry. Adding a competitor = one factory + one entry below.

Capabilities are declared statically so offline stages (score, report) know
what to measure without constructing an adapter or touching the network.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from benchmarks.harness.config import RunConfig, SystemConfig
from benchmarks.harness.corpus.view import CorpusView
from benchmarks.harness.errors import ConfigError
from benchmarks.harness.models import CorpusManifest, IndexReport, IngestManifest
from benchmarks.harness.pricing import price_for
from benchmarks.harness.systems.base import AdapterCapabilities, CorpusIngestor, PreparedCorpus, SystemAdapter
from benchmarks.harness.systems.baselines.bm25 import DEFAULT_N_DOCS, Bm25Answerer
from benchmarks.harness.systems.baselines.closed_book import ClosedBookAnswerer
from benchmarks.harness.systems.baselines.oracle import OracleAnswerer
from benchmarks.harness.systems.openwebui.adapter import OpenWebUIAdapter
from benchmarks.harness.systems.openwebui.client import InstanceSettings, OpenWebUIClient
from benchmarks.harness.systems.openwebui.ingest import OpenWebUIIngestor
from benchmarks.harness.systems.pipeshub.adapter import PipesHubAdapter
from benchmarks.harness.systems.pipeshub.indexing import IndexWaiter
from benchmarks.harness.systems.pipeshub.ingest import PipesHubIngestor
from benchmarks.harness.systems.pipeshub.kb_api import KnowledgeBaseApi
from benchmarks.harness.systems.pipeshub.session import ConnectorApi
from benchmarks.harness.systems.rag.answerer import RagAnswerer, RagOptions
from benchmarks.harness.systems.ragflow.adapter import RagflowAdapter
from benchmarks.harness.systems.ragflow.client import RagflowClient
from benchmarks.harness.systems.ragflow.ingest import RagflowIngestor

if TYPE_CHECKING:
    from benchmarks.harness.services import Services


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
    # Which corpus index this system reads, for the diagnostics'
    # same-index controls. A callable because a RAG system's index is a
    # config option, not a property of the adapter kind. Declared here so
    # adding a system never means editing the reporting stage.
    index_of: Callable[[SystemConfig], str] | None = None

    def reads_index(self, system: SystemConfig) -> str:
        return self.index_of(system) if self.index_of else self.capabilities.reads_index


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


_OPENWEBUI_OPTIONS = frozenset({
    "base_url", "api_key_env", "upload_workers", "request_timeout_s", "query_generation", "rag_template",
})


def _openwebui(system: SystemConfig, deps: AdapterDeps) -> SystemAdapter:
    """Open WebUI's own pipeline. Its retrieval settings live in the
    instance's environment (see the run config's header); `query_generation`
    and `rag_template` are set per run, since they are instance-wide."""
    import os

    unknown = set(system.options) - _OPENWEBUI_OPTIONS
    if unknown:
        raise ConfigError(f"{system.id}: unknown openwebui options {sorted(unknown)}")
    base_url = str(system.options.get("base_url", "http://localhost:8080"))
    key_env = str(system.options.get("api_key_env", "OPENWEBUI_API_KEY"))
    api_key = os.environ.get(key_env)
    if not api_key:
        raise ConfigError(f"{system.id}: set {key_env} to an Open WebUI API key")
    client = OpenWebUIClient(base_url, api_key, timeout_s=float(system.options.get("request_timeout_s", 900)))
    ingestor = OpenWebUIIngestor(
        client, system_id=system.id, base_url=base_url, corpus_dir=deps.corpus.corpus_dir,
        cache_dir=deps.services.cache_dir, upload_workers=int(system.options.get("upload_workers", 8)),
    )
    model = deps.services.model(deps.config.answerer)
    return OpenWebUIAdapter(
        system.id, client, ingestor, model,
        reasoning_effort=model.reasoning_effort, current_time=deps.config.corpus.snapshot,
        price=_answerer_kwargs(deps)["price"],
        settings=InstanceSettings(
            query_generation=bool(system.options.get("query_generation", False)),
            rag_template=str(system.options.get("rag_template") or ""),
        ),
    )


_RAGFLOW_OPTIONS = frozenset({
    "base_url", "api_key_env", "dataset", "chat", "system_prompt", "batch_size", "upload_workers", "request_timeout_s",
})


def _ragflow(system: SystemConfig, deps: AdapterDeps) -> SystemAdapter:
    """RAGFlow's own pipeline. `dataset` settings apply when the corpus
    dataset is first created, `chat` settings when its chat is."""
    import os

    unknown = set(system.options) - _RAGFLOW_OPTIONS
    if unknown:
        raise ConfigError(f"{system.id}: unknown ragflow options {sorted(unknown)}")
    base_url = str(system.options.get("base_url", "http://localhost:9380"))
    key_env = str(system.options.get("api_key_env", "RAGFLOW_API_KEY"))
    api_key = os.environ.get(key_env)
    if not api_key:
        raise ConfigError(f"{system.id}: set {key_env} to a RAGFlow API key")
    dataset, chat = system.options.get("dataset"), system.options.get("chat")
    if not isinstance(dataset, dict) or not isinstance(chat, dict):
        raise ConfigError(f"{system.id}: `dataset` and `chat` settings are required")
    client = RagflowClient(base_url, api_key, timeout_s=float(system.options.get("request_timeout_s", 900)))
    ingestor = RagflowIngestor(
        client, system_id=system.id, base_url=base_url, corpus_dir=deps.corpus.corpus_dir,
        cache_dir=deps.services.cache_dir, dataset_config=dataset,
        batch_size=int(system.options.get("batch_size", 50)),
        upload_workers=int(system.options.get("upload_workers", 4)),
    )
    system_prompt = system.options.get("system_prompt")
    if system_prompt is not None and "{knowledge}" not in str(system_prompt):
        raise ConfigError(f"{system.id}: `system_prompt` must contain {{knowledge}}, where RAGFlow puts the chunks")
    return RagflowAdapter(
        system.id, client, ingestor, chat_config=chat, current_time=deps.config.corpus.snapshot,
        system_prompt=None if system_prompt is None else str(system_prompt),
    )


def _rag_index(system: SystemConfig) -> str:
    return str(system.options.get("index", "pipeshub"))


ADAPTER_REGISTRY: dict[str, AdapterSpec] = {
    "closed_book": AdapterSpec(_closed_book, ClosedBookAnswerer.capabilities),
    "oracle": AdapterSpec(_oracle, OracleAnswerer.capabilities, lambda _s: "oracle"),
    "bm25": AdapterSpec(_bm25, Bm25Answerer.capabilities),
    "pipeshub": AdapterSpec(_pipeshub, PipesHubAdapter.capabilities, lambda _s: "pipeshub"),
    "naive_rag": AdapterSpec(_naive_rag, RagAnswerer.capabilities, _rag_index),
    "advanced_rag": AdapterSpec(_advanced_rag, RagAnswerer.capabilities, _rag_index),
    "openwebui": AdapterSpec(_openwebui, OpenWebUIAdapter.capabilities, lambda _s: "openwebui"),
    "ragflow": AdapterSpec(_ragflow, RagflowAdapter.capabilities, lambda _s: "ragflow"),
}


def adapter_spec(kind: str, registry: Mapping[str, AdapterSpec] | None = None) -> AdapterSpec:
    specs = registry if registry is not None else ADAPTER_REGISTRY
    if kind not in specs:
        raise ConfigError(f"unknown system kind {kind!r}; known: {sorted(specs)}")
    return specs[kind]
