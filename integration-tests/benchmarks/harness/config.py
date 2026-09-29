"""Run configuration: YAML parsed into validated, frozen Pydantic models.

Every tuning knob lives here or as a code constant. The environment supplies
only secrets (`credentials.py`) and the PipesHub endpoints, which are left out
of the config hash so a resumed run may point at a different host.
"""

from __future__ import annotations

import hashlib
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from benchmarks.harness.errors import ConfigError
from benchmarks.harness.guard import (
    DEFAULT_ALLOWED_TOOL_PATTERNS,
    DEFAULT_DENIED_TOOL_PATTERNS,
)

FRAMES_SNAPSHOT = datetime(2024, 10, 15, tzinfo=UTC)
_ENDPOINT_FIELDS = {"base_url", "connector_url"}
_QDRANT_ENDPOINT_FIELDS = {"url"}
# How a model is reached, not which model it is.
_ROUTING_FIELDS = {"provider", "call_provider", "deployment", "pipeshub_provider", "model_key"}
# How hard the run is driven, not what it measures. Raising a budget or a
# worker count mid-run must not make the run unresumable.
_OPERATIONAL_FIELDS = {"concurrency"}
_LIMITS_FIELDS = {"max_cost_usd", "max_error_rate", "min_items_for_breaker"}
_STATS_FIELDS = {"bootstrap_samples"}


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class DatasetConfig(_Frozen):
    # Which benchmark this run measures; resolved against the dataset
    # registry (`datasets.py`). Defaults to FRAMES so existing configs load.
    name: str = "frames"
    split: Literal["dev", "heldout", "all"] = "dev"
    limit: int | None = Field(default=None, ge=1)
    question_ids: tuple[str, ...] | None = None


class CorpusConfig(_Frozen):
    tier: Literal["G", "GD"] = "G"
    snapshot: datetime = FRAMES_SNAPSHOT
    # `selected_questions` builds only the articles the chosen questions need.
    scope: Literal["all", "selected_questions"] = "all"
    distractor_count: int = Field(default=2500, ge=0)
    fetch_workers: int = Field(default=4, ge=1, le=8)
    max_failed_gold_ratio: float = Field(default=0.005, ge=0, le=1)


class ModelSelector(_Frozen):
    """A model as registered in PipesHub (`/ai-models/llm`)."""

    model: str
    provider: str | None = None
    model_key: str | None = None
    # Only used when `seed-models` has to register the model.
    is_reasoning: bool = False
    # Sent to every system that calls this model (PipesHub: `reasoningEffort`).
    reasoning_effort: Literal["low", "medium", "high"] | None = None
    # Azure deployment name when it differs from `model`.
    deployment: str | None = None
    # Provider the harness calls the model through when it differs from
    # PipesHub's registration (same model, e.g. PipesHub on azureOpenAI and the
    # baselines on openAI).
    call_provider: str | None = None


class EmbeddingSelector(_Frozen):
    """The embedding model PipesHub indexed with. Baselines embed queries with
    it directly; `prepare` refuses to run if PipesHub's default differs."""

    model: str
    provider: str
    pipeshub_provider: str | None = None
    deployment: str | None = None


class ModelPrice(_Frozen):
    """USD per million tokens. A request whose input exceeds
    `long_context_threshold` is billed at the multiplied rates in full."""

    input_per_mtok: float = Field(ge=0)
    cached_input_per_mtok: float | None = Field(default=None, ge=0)
    output_per_mtok: float = Field(ge=0)
    long_context_threshold: int | None = Field(default=None, ge=1)
    long_context_input_multiplier: float = Field(default=1.0, ge=1)
    long_context_output_multiplier: float = Field(default=1.0, ge=1)
    source: str = ""


class StandardIndexConfig(_Frozen):
    """The baselines' own collection (RAG option `index: standard`): what a
    developer gets from the usual recipe — 512-token recursive chunks with
    64-token overlap, dense + BM25 (with IDF) — independent of PipesHub's chunker."""

    chunk_tokens: int = Field(default=512, ge=64, le=8192)
    chunk_overlap: int = Field(default=64, ge=0, le=1024)
    embed_batch_size: int = Field(default=128, ge=1, le=2048)
    embed_workers: int = Field(default=4, ge=1, le=16)
    collection_prefix: str = Field(default="frames_std", pattern=r"^[a-z0-9_]{1,40}$")


class QdrantConfig(_Frozen):
    """The vector store PipesHub writes to; baselines read the same points."""

    url: str = Field(default_factory=lambda: os.getenv("PIPESHUB_QDRANT_URL", "http://localhost:6333"))
    collection: str = "records"


class PipesHubConfig(_Frozen):
    base_url: str = Field(
        default_factory=lambda: os.getenv("PIPESHUB_BASE_URL", "http://localhost:3000"),
    )
    connector_url: str = Field(
        default_factory=lambda: os.getenv("PIPESHUB_CONNECTOR_URL", "http://localhost:8088"),
    )
    stream_timeout_s: int = Field(default=900, ge=30)
    index_timeout_s: int = Field(default=7200, ge=60)
    index_poll_interval_s: int = Field(default=30, ge=1)
    min_gold_indexed_ratio: float = Field(default=0.995, ge=0, le=1)
    # Over every corpus article (distractors included), counting ones that never uploaded.
    min_indexed_ratio: float = Field(default=0.99, ge=0, le=1)
    upload_batch_size: int = Field(default=50, ge=1, le=1000)
    search_limit: int = Field(default=100, ge=1, le=100)


class SystemConfig(_Frozen):
    kind: str  # key in `systems.ADAPTER_REGISTRY`
    label: str | None = None
    repeats: int = Field(default=1, ge=1, le=10)
    concurrency: int = Field(default=2, ge=1, le=16)
    options: dict[str, Any] = Field(default_factory=dict)

    @property
    def id(self) -> str:
        return self.label or self.kind


class EvidenceSupportConfig(_Frozen):
    """The judge-time check that correct answers rest on the context their
    system was shown (`grading/evidence_support.py`)."""

    # "primary"/"secondary" name a configured judge; a selector names another.
    judge: Literal["primary", "secondary"] | ModelSelector = "primary"
    # What one check may show the judge; larger evidence is cut to the
    # passages (and their neighbours) that best match the question and answer.
    max_evidence_tokens: int = Field(default=6_000, ge=500, le=200_000)
    # System ids to verify; None = every system except closed book, which is
    # shown nothing and so has nothing to check.
    systems: tuple[str, ...] | None = None


class GradingConfig(_Frozen):
    primary: ModelSelector
    secondary: ModelSelector | None = None
    strict_grader: bool = True
    claim_support: bool = True
    max_claims: int = Field(default=8, ge=1, le=32)
    fuzzy_threshold: int = Field(default=90, ge=50, le=100)
    concurrency: int = Field(default=8, ge=1, le=32)
    evidence_support: EvidenceSupportConfig = EvidenceSupportConfig()

    def evidence_judge(self) -> ModelSelector:
        judge = self.evidence_support.judge
        if judge == "primary":
            return self.primary
        if judge == "secondary":
            if self.secondary is None:
                raise ConfigError("grading.evidence_support.judge is `secondary`, but no secondary judge is configured")
            return self.secondary
        return judge


class GuardConfig(_Frozen):
    allowed_tool_patterns: tuple[str, ...] = DEFAULT_ALLOWED_TOOL_PATTERNS
    denied_tool_patterns: tuple[str, ...] = DEFAULT_DENIED_TOOL_PATTERNS
    strict: bool = True


class LimitsConfig(_Frozen):
    max_error_rate: float = Field(default=0.2, ge=0, le=1)
    min_items_for_breaker: int = Field(default=20, ge=1)
    max_cost_usd: float | None = Field(default=None, gt=0)


class StatsConfig(_Frozen):
    bootstrap_samples: int = Field(default=10_000, ge=100)
    confidence: float = Field(default=0.95, gt=0.5, lt=1)


class RunConfig(_Frozen):
    run_name: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,62}$")
    seed: int = 20241015
    dataset: DatasetConfig = DatasetConfig()
    corpus: CorpusConfig = CorpusConfig()
    pipeshub: PipesHubConfig = Field(default_factory=PipesHubConfig)
    answerer: ModelSelector
    embedding: EmbeddingSelector | None = None
    qdrant: QdrantConfig = Field(default_factory=QdrantConfig)
    standard_index: StandardIndexConfig = StandardIndexConfig()
    pricing: dict[str, ModelPrice] = Field(default_factory=dict)
    systems: tuple[SystemConfig, ...]
    grading: GradingConfig
    guard: GuardConfig = GuardConfig()
    limits: LimitsConfig = LimitsConfig()
    stats: StatsConfig = StatsConfig()

    @model_validator(mode="after")
    def _unique_system_ids(self) -> RunConfig:
        ids = [system.id for system in self.systems]
        if not ids:
            raise ValueError("at least one system is required")
        if len(ids) != len(set(ids)):
            raise ValueError(f"system labels must be unique, got {ids}")
        unknown = set(self.grading.evidence_support.systems or ()) - set(ids)
        if unknown:
            raise ValueError(f"grading.evidence_support.systems names unknown systems {sorted(unknown)}")
        return self

    def evidence_verified_systems(self) -> set[str]:
        chosen = self.grading.evidence_support.systems
        if chosen is not None:
            return set(chosen)
        return {system.id for system in self.systems if system.kind != "closed_book"}

    def config_hash(self) -> str:
        """Identifies what the run measures — not how it is driven.

        Endpoints and provider routing are excluded: the same model served
        from OpenAI or Azure is the same model. So are concurrency, budget
        caps and bootstrap sample count: raising a cost ceiling to let a
        stalled run finish changed nothing about what was measured, and
        refusing to resume it would throw away everything already paid for.
        The evidence-support settings are left out too: they only audit
        answers already given, and runs started before the check existed
        must still resume to be verified."""
        payload = self.model_dump_json(exclude={
            "pipeshub": _ENDPOINT_FIELDS,
            "qdrant": _QDRANT_ENDPOINT_FIELDS,
            "answerer": _ROUTING_FIELDS,
            "embedding": _ROUTING_FIELDS,
            "grading": {"primary": _ROUTING_FIELDS, "secondary": _ROUTING_FIELDS, "evidence_support": True},
            "limits": _LIMITS_FIELDS,
            "stats": _STATS_FIELDS,
            "systems": {"__all__": _OPERATIONAL_FIELDS},
        })
        return hashlib.sha256(payload.encode()).hexdigest()

    def system(self, system_id: str) -> SystemConfig:
        for system in self.systems:
            if system.id == system_id:
                return system
        raise ConfigError(f"unknown system {system_id!r}")


def load_config(path: Path) -> RunConfig:
    try:
        raw = yaml.safe_load(path.read_text())
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(f"cannot read config {path}: {exc}") from exc
    try:
        return RunConfig.model_validate(raw or {})
    except ValidationError as exc:
        raise ConfigError(f"invalid config {path}:\n{exc}") from exc
