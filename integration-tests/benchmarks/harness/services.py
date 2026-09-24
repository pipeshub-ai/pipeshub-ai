"""Dependency container and per-run context.

Services are created lazily (a `score` or `report` run never opens a PipesHub
session) and can be injected, which is how tests run the whole pipeline
against fakes.
"""

from __future__ import annotations

import hashlib
import inspect
import logging
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from typing import Any

from benchmarks.harness.config import CorpusConfig, ModelSelector, RunConfig
from benchmarks.harness.corpus.view import CorpusView
from benchmarks.harness.credentials import Credentials
from benchmarks.harness.datasets import DatasetPlugin
from benchmarks.harness.errors import ConfigError, CostLimitError, IngestError
from benchmarks.harness.guard import ToolCallGuard
from benchmarks.harness.llm.cache import CachedLLMClient, LLMCache
from benchmarks.harness.llm.client import LiteLLMClient, LLMClient, ResolvedModel
from benchmarks.harness.llm.registry import ModelResolver
from benchmarks.harness.models import IngestManifest, Question
from benchmarks.harness.paths import cache_dir as cache_dir_for
from benchmarks.harness.report.summary import RunSummary
from benchmarks.harness.store import RunStore
from benchmarks.harness.systems import ADAPTER_REGISTRY, AdapterSpec
from benchmarks.harness.systems.base import PreparedCorpus, SystemAdapter
from benchmarks.harness.systems.pipeshub.kb_api import RecordStatus
from benchmarks.harness.systems.pipeshub.records import resolve_virtual_record_ids
from benchmarks.harness.systems.pipeshub.seed import verify_indexing_embedding
from benchmarks.harness.systems.pipeshub.session import UserSession
from benchmarks.harness.systems.rag.answerer import RecordRef
from benchmarks.harness.systems.rag.rerank import CrossEncoderReranker
from benchmarks.harness.systems.rag.retrieval import ChunkIndex, QueryEncoder
from benchmarks.harness.systems.rag.standard_index import StandardChunkIndex, StandardIndexIngestor


logger = logging.getLogger(__name__)

class CostTracker:
    def __init__(self, limit_usd: float | None) -> None:
        self._limit = limit_usd
        self._spent = 0.0
        self._lock = threading.Lock()

    @property
    def spent(self) -> float:
        return self._spent

    def add(self, cost_usd: float | None) -> None:
        if not cost_usd:
            return
        with self._lock:
            self._spent += cost_usd
            if self._limit is not None and self._spent > self._limit:
                raise CostLimitError(f"spent ${self._spent:.2f}, over the ${self._limit:.2f} limit")


class Services:
    def __init__(
        self,
        config: RunConfig,
        credentials: Credentials,
        *,
        cache_dir: Path | None = None,
        llm: LLMClient | None = None,
        session: UserSession | None = None,
        resolver: ModelResolver | None = None,
        dataset_path: Path | None = None,
        split_path: Path | None = None,
        expected_question_count: int | None = None,
        article_source_factory: Callable[[str], object] | None = None,
        adapter_registry: Mapping[str, AdapterSpec] | None = None,
    ) -> None:
        self.config = config
        self.credentials = credentials
        # Dataset-scoped so two benchmarks never share a corpus cache or an
        # LLM cache; `config.dataset.name` names the subdirectory.
        self.cache_dir = cache_dir if cache_dir is not None else cache_dir_for(config.dataset.name)
        # `None` means the dataset plugin's own split; a test overrides it.
        # `None` means the dataset plugin's own split; a test overrides it.
        self.split_path_override = split_path
        for name, value in {"llm": llm, "session": session, "resolver": resolver}.items():
            if value is not None:
                self.__dict__[name] = value  # pre-seed the cached_property
        self._dataset_path = dataset_path
        self._expected_question_count = expected_question_count
        # `None` means the dataset supplies its own; a test injects a fake.
        self._article_source_factory = article_source_factory
        self.adapter_registry = adapter_registry if adapter_registry is not None else ADAPTER_REGISTRY
        self._models: dict[ModelSelector, ResolvedModel] = {}
        # Reentrant: `rag_index` holds it and calls `virtual_record_ids`,
        # which takes it again via `kb_records`.
        self._rag_lock = threading.RLock()
        self._rag_indexes: dict[str, tuple[ChunkIndex, dict[str, RecordRef]]] = {}
        self._rerankers: dict[str, CrossEncoderReranker] = {}
        self._kb_records: dict[str, list[RecordStatus]] = {}

    @cached_property
    def session(self) -> UserSession:
        email, password = self.credentials.require_user()
        return UserSession(self.config.pipeshub.base_url, email=email, password=password)

    @cached_property
    def resolver(self) -> ModelResolver:
        from ai_models_setup import list_configured_llm_models

        return ModelResolver(lambda: list_configured_llm_models(self.session))

    @cached_property
    def llm(self) -> LLMClient:
        return CachedLLMClient(LiteLLMClient(self.credentials), LLMCache(self.cache_dir / "llm_cache.sqlite"))

    @cached_property
    def guard(self) -> ToolCallGuard:
        g = self.config.guard
        return ToolCallGuard(g.allowed_tool_patterns, g.denied_tool_patterns, strict=g.strict)

    @cached_property
    def cost(self) -> CostTracker:
        return CostTracker(self.config.limits.max_cost_usd)

    @cached_property
    def _reads_pipeshub(self) -> bool:
        """Whether any system in the run talks to PipesHub. Without one, the
        run must not need a PipesHub instance up just to name its models."""
        return any(
            s.kind == "pipeshub"
            or (s.kind in ("naive_rag", "advanced_rag") and str(s.options.get("index", "pipeshub")) == "pipeshub")
            for s in self.config.systems
        )

    @staticmethod
    def _direct(selector: ModelSelector) -> ResolvedModel:
        return ResolvedModel(
            model_key="", provider=selector.call_provider or str(selector.provider), model_name=selector.model,
            is_reasoning=selector.is_reasoning, reasoning_effort=selector.reasoning_effort,
            deployment=selector.deployment,
        )

    def model(self, selector: ModelSelector) -> ResolvedModel:
        if selector not in self._models:
            if not self._reads_pipeshub and selector.provider:
                self._models[selector] = self._direct(selector)
            else:
                self._models[selector] = self.resolver.resolve(selector)
        return self._models[selector]

    def judge_model(self, selector: ModelSelector) -> ResolvedModel:
        """Judges need not be registered in PipesHub (no keys stored there):
        an unregistered judge with a provider is called directly."""
        if selector not in self._models:
            if not self._reads_pipeshub and selector.provider:
                self._models[selector] = self._direct(selector)
                return self._models[selector]
            found = self.resolver.find(selector)
            if found is None and selector.provider:
                found = self._direct(selector)
            self._models[selector] = found or self.resolver.resolve(selector)
        return self._models[selector]

    @cached_property
    def embedding_model(self) -> ResolvedModel:
        selector = self.config.embedding
        if selector is None:
            raise ConfigError("RAG systems need `embedding:` — the model PipesHub indexed with")
        verify_indexing_embedding(self.session, selector)
        return ResolvedModel(
            model_key="", provider=selector.provider, model_name=selector.model, deployment=selector.deployment,
        )

    @cached_property
    def qdrant(self) -> Any:  # noqa: ANN401
        from qdrant_client import QdrantClient

        api_key = self.credentials.env.get("QDRANT_API_KEY") or None
        # `check_version` only exists on newer clients; the server/client minor
        # mismatch warning is harmless here.
        kwargs = {"url": self.config.qdrant.url, "api_key": api_key, "timeout": 60, "prefer_grpc": False}
        if "check_version" in inspect.signature(QdrantClient.__init__).parameters:
            kwargs["check_version"] = False
        return QdrantClient(**kwargs)

    def kb_records(self, kb_id: str) -> list[RecordStatus]:
        """The KB's records, listed once per run (paging 12k records is slow)."""
        with self._rag_lock:
            if kb_id not in self._kb_records:
                from benchmarks.harness.systems.pipeshub.kb_api import KnowledgeBaseApi
                from benchmarks.harness.systems.pipeshub.session import ConnectorApi

                api = KnowledgeBaseApi(self.session, ConnectorApi(self.session, self.config.pipeshub.connector_url))
                self._kb_records[kb_id] = api.list_records(kb_id)
            return self._kb_records[kb_id]

    def virtual_record_ids(self, ingest: IngestManifest) -> dict[str, str]:
        """`virtualRecordId -> recordId`, from the listing where the backend
        provides it."""
        listed = {r.record_id: r.virtual_record_id for r in self.kb_records(ingest.kb_id) if r.virtual_record_id}
        return resolve_virtual_record_ids(self.session, ingest, self.cache_dir, listed)

    def verify_vectors(self, collection: str, key: str, expected: dict[str, str], gold_refs: set[str]) -> None:
        """Every article PipesHub (or the standard build) calls indexed must
        have points in Qdrant. `expected` maps the payload value at `key` to
        the article URL."""
        hits = self.qdrant.facet(collection, key=key, limit=len(expected) + 1000, exact=True).hits
        present = {str(h.value) for h in hits if h.count > 0}
        missing = {url for value, url in expected.items() if value not in present}
        missing_gold = missing & gold_refs
        allowed = (1 - self.config.pipeshub.min_indexed_ratio) * len(expected)
        logger.info("%s: %d/%d articles have vectors", collection, len(expected) - len(missing), len(expected))
        if missing_gold or len(missing) > allowed:
            raise IngestError(
                f"{collection}: {len(missing)} articles have no vectors ({len(missing_gold)} gold), "
                f"e.g. {sorted(missing_gold or missing)[:5]}",
            )

    def rag_index(self, ingest: IngestManifest, corpus: CorpusView) -> tuple[ChunkIndex, dict[str, RecordRef]]:
        """One index per KB, shared by every RAG system in the run."""
        with self._rag_lock:
            if ingest.kb_id not in self._rag_indexes:
                vrids = self.virtual_record_ids(ingest)
                by_record = {r.record_id: r for r in ingest.records}
                records = {}
                for vrid, record_id in vrids.items():
                    record = by_record.get(record_id)
                    if record is None:
                        continue
                    document = corpus.document(record.canonical_url)
                    title = document.title if document is not None else record.record_name
                    records[vrid] = RecordRef(record_id, record.canonical_url, title)
                index = ChunkIndex(self.qdrant, self.config.qdrant.collection, self.query_encoder, list(records))
                self._rag_indexes[ingest.kb_id] = (index, records)
            return self._rag_indexes[ingest.kb_id]

    @cached_property
    def query_encoder(self) -> QueryEncoder:
        return QueryEncoder(self.llm, self.embedding_model)

    def standard_ingestor(self, system_id: str, corpus: CorpusView) -> StandardIndexIngestor:
        return StandardIndexIngestor(
            self.qdrant, self.llm, self.embedding_model, corpus, self.config.standard_index,
            system_id=system_id, cache_dir=self.cache_dir,
        )

    def standard_index(self, ingest: IngestManifest, corpus: CorpusView) -> tuple[StandardChunkIndex, dict[str, RecordRef]]:
        records = {}
        for r in ingest.records:
            document = corpus.document(r.canonical_url)
            records[r.canonical_url] = RecordRef(r.canonical_url, r.canonical_url, document.title if document else r.record_name)
        return StandardChunkIndex(self.qdrant, ingest.kb_id, self.query_encoder), records

    def reranker(self, model_name: str) -> CrossEncoderReranker:
        with self._rag_lock:
            if model_name not in self._rerankers:
                self._rerankers[model_name] = CrossEncoderReranker(model_name)
            return self._rerankers[model_name]

    # Test overrides a dataset plugin reads instead of reaching for the real
    # source. Loading questions and fetching documents are the dataset's job;
    # the container only supplies what any dataset would need to do them.
    @property
    def dataset_path_override(self) -> Path | None:
        return self._dataset_path

    @property
    def expected_question_count(self) -> int | None:
        return self._expected_question_count

    @property
    def article_source_override(self) -> Callable[[str], object] | None:
        return self._article_source_factory

    def corpus_dir(self, corpus: CorpusConfig, question_ids: Sequence[int]) -> Path:
        name = f"{corpus.tier}-{corpus.snapshot:%Y%m%d}"
        if corpus.tier == "GD":
            name += f"-d{corpus.distractor_count}"
        if corpus.scope == "selected_questions":
            ids = ",".join(str(q) for q in sorted(question_ids))
            name += f"-q{hashlib.sha256(ids.encode()).hexdigest()[:8]}"
        return self.cache_dir / "corpus" / name


@dataclass
class RunContext:
    config: RunConfig
    store: RunStore
    services: Services
    # Resolved once from `config.dataset.name`; the stages reach every
    # dataset-specific decision through it and know nothing about FRAMES.
    dataset: DatasetPlugin
    retry_errors: bool = False
    all_questions: list[Question] = field(default_factory=list)
    questions: list[Question] = field(default_factory=list)
    corpus: CorpusView | None = None
    adapters: dict[str, SystemAdapter] = field(default_factory=dict)
    prepared: dict[str, PreparedCorpus] = field(default_factory=dict)
    summary: RunSummary | None = None

    def require_corpus(self) -> CorpusView:
        if self.corpus is None:
            raise ConfigError("corpus stage has not run")
        return self.corpus
