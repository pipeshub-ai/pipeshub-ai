"""Domain models shared across stages.

Everything a stage persists or hands to another stage is one of these, so no
raw dicts cross a boundary. Wire payloads from PipesHub (`RetrievalEvent`) use
camelCase aliases and ignore unknown keys for forward compatibility.
"""

from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, model_validator
from pydantic.alias_generators import to_camel

Split = Literal["dev", "heldout"]
FailureKind = Literal["run_error", "timeout", "protocol", "transport", "http", "llm"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _renamed(values: Any, mapping: dict[str, str]) -> Any:
    """Read run artefacts written before a field was renamed.

    Run directories and corpus caches are the evidence behind published
    numbers, so a rename must not make them unreadable — re-fetching a pinned
    corpus would change `corpus_version` and invalidate every run built on it.
    Write paths only ever emit the current names.
    """
    if not isinstance(values, dict):
        return values
    for old, new in mapping.items():
        if old in values and new not in values:
            values[new] = values.pop(old)
    # Question ids were ints before they were strings.
    for key in ("question_id", "id"):
        if isinstance(values.get(key), int):
            values[key] = str(values[key])
    return values


class _Wire(BaseModel):
    model_config = ConfigDict(extra="ignore", alias_generator=to_camel, populate_by_name=True)


class Question(_Model):
    """One benchmark question. `id` is a string because dataset ids are not
    numbers outside FRAMES (HotpotQA hashes, MuSiQue `2hop__…`); `labels` are
    free-form tags a dataset stratifies and reports by; `gold_refs` are opaque
    to the harness — only the dataset plugin knows whether they are URLs,
    paragraph indices or internal document ids."""

    id: str
    prompt: str
    answer: str
    labels: tuple[str, ...] = ()
    gold_refs: tuple[str, ...] = ()
    split: Split | None = None

    @model_validator(mode="before")
    @classmethod
    def _legacy(cls, values: Any) -> Any:
        return _renamed(values, {"reasoning_types": "labels", "gold_urls": "gold_refs"})


class AskItem(_Model):
    """What a system under test receives. Deliberately has no answer field.

    `gold_refs` is the evaluation ground truth and is populated ONLY for
    adapters that declare `needs_gold_refs` (the oracle upper bound). Every
    other system gets an empty tuple, so it cannot read the key even by
    accident.
    """

    question_id: str
    prompt: str
    gold_refs: tuple[str, ...] = ()

    @classmethod
    def from_question(cls, question: Question, *, with_gold: bool = False) -> AskItem:
        return cls(
            question_id=question.id,
            prompt=question.prompt,
            gold_refs=question.gold_refs if with_gold else (),
        )


class CorpusDocument(_Model):
    """A pinned corpus article. The identity/versioning fields a *specific*
    source needs (MediaWiki revids, a filesystem mtime, an S3 etag) live in
    `source`, so a corpus that is not a wiki does not carry wiki columns."""

    canonical_url: str
    title: str
    tier: Literal["gold", "distractor"]
    filename: str
    text_filename: str
    sha256: str
    # Contributes to `corpus_version`: two corpora with the same text but
    # different source revisions are not the same corpus.
    revision: str = ""
    source: dict[str, Any] = Field(default_factory=dict)
    # Refs this document points at, in the dataset's own ref vocabulary. Used
    # for distractor mining and for the "answer was one link away" diagnosis;
    # empty for a corpus with no link graph.
    outlinks: tuple[str, ...] = ()

    @model_validator(mode="before")
    @classmethod
    def _legacy(cls, values: Any) -> Any:
        if not isinstance(values, dict) or "revid" not in values:
            return values
        moved = {k: values.pop(k) for k in ("host", "page_id", "rev_timestamp", "post_snapshot_revision")
                 if k in values}
        # `revid` stringified keeps `corpus_version` byte-identical to the
        # value the cached corpus was built under.
        values["revision"] = str(values.pop("revid"))
        values.setdefault("source", {}).update(moved)
        return values


class CorpusManifest(_Model):
    snapshot: datetime
    tier: Literal["G", "GD"]
    harness_version: str
    documents: list[CorpusDocument]
    # A dataset's own ref (normalised) -> canonical URL of the pinned document.
    # Datasets whose refs already are corpus keys leave this empty.
    gold_aliases: dict[str, str] = Field(default_factory=dict)
    unresolved: list[str] = Field(default_factory=list)

    _index: dict[str, CorpusDocument] | None = PrivateAttr(default=None)

    @property
    def corpus_version(self) -> str:
        rows = sorted(f"{d.canonical_url}\t{d.revision}\t{d.sha256}" for d in self.documents)
        return hashlib.sha256("\n".join(rows).encode()).hexdigest()

    def document(self, canonical_url: str) -> CorpusDocument | None:
        if self._index is None:
            self._index = {d.canonical_url: d for d in self.documents}
        return self._index.get(canonical_url)


class IngestedRecord(_Model):
    record_id: str
    record_name: str
    canonical_url: str


class IngestManifest(_Model):
    system: str
    kb_id: str
    corpus_version: str
    base_url: str
    records: list[IngestedRecord]


class IndexReport(_Model):
    total: int
    status_counts: dict[str, int]
    gold_total: int
    gold_indexed: int
    reindexed: list[str] = Field(default_factory=list)
    unindexed_urls: list[str] = Field(default_factory=list)
    elapsed_s: float = 0.0

    @property
    def gold_ratio(self) -> float:
        return self.gold_indexed / self.gold_total if self.gold_total else 1.0

    def indexed_ratio(self, corpus_size: int) -> float:
        return self.status_counts.get("COMPLETED", 0) / corpus_size if corpus_size else 1.0


class FetchedRange(_Wire):
    start_block: int
    blocks_rendered: int
    complete: bool


class RetrievedRecordRef(_Wire):
    virtual_record_id: str
    record_id: str | None = None
    record_name: str | None = None
    block_indices: list[int] = Field(default_factory=list)
    summary_hit: bool = False
    fetched: list[FetchedRange] = Field(default_factory=list)

    @property
    def reached_model(self) -> bool:
        return bool(
            self.block_indices
            or self.summary_hit
            or any(f.blocks_rendered > 0 for f in self.fetched)
        )


class RetrievalEvent(_Wire):
    seq: int
    source: str
    status: str
    status_reason: str | None = None
    tool_name: str | None = None
    tool_call_id: str | None = None
    records: list[RetrievedRecordRef] = Field(default_factory=list)
    known_record_ids: list[str] = Field(default_factory=list)
    error_message: str | None = None


class ToolCallTrace(_Model):
    tool_call_id: str
    name: str
    args_json: str = ""
    status: str | None = None
    result_summary: str | None = None
    parent_run_id: str | None = None
    # Client-side ms since the stream opened (start frame, result frame).
    started_ms: int | None = None
    ended_ms: int | None = None
    # TOOL_CALL_RESULT `content`: a short preview, or why the call was blocked.
    result_preview: str | None = None


class Citation(_Model):
    display_index: int | None = None
    record_id: str | None = None
    virtual_record_id: str | None = None
    record_name: str | None = None
    content: str = ""
    block_nums: list[int] = Field(default_factory=list)
    web_url: str | None = None


class SystemFailure(_Model):
    kind: FailureKind
    code: str | None = None
    message: str


class RunStats(_Model):
    """Loop-outcome signals from the `run_usage` frame."""

    turns: int | None = None
    max_turns: int | None = None
    completion_gate_nudges: int = 0
    auxiliary_llm_calls: int = 0
    agent_error: str | None = None

    @property
    def hit_turn_cap(self) -> bool:
        return self.turns is not None and self.max_turns is not None and self.turns >= self.max_turns


class StreamTrace(_Model):
    conversation_id: str | None = None
    tool_calls: list[ToolCallTrace] = Field(default_factory=list)
    retrieval_events: list[RetrievalEvent] = Field(default_factory=list)
    # Consecutive batches of root tool calls — the stream has no turn counter.
    tool_waves: int = 0
    frame_counts: dict[str, int] = Field(default_factory=dict)
    finished: bool = False
    confidence: str | None = None
    run_stats: RunStats | None = None
    # PipesHub's persisted transcript (`parts`): every model turn, reasoning,
    # and tool call in order — what the agent actually did.
    transcript: list[dict[str, Any]] = Field(default_factory=list)

    @property
    def root_tool_calls(self) -> list[ToolCallTrace]:
        return [call for call in self.tool_calls if call.parent_run_id is None]


class CallUsage(_Model):
    """One LLM request. `input_tokens` includes `cached_tokens`; `output_tokens`
    includes hidden reasoning."""

    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    purpose: str = "answer"


class RetrievedChunk(_Model):
    """One chunk a RAG baseline put in context, in context order."""

    url: str
    virtual_record_id: str
    block_index: int | None = None
    score: float = 0.0


class Prediction(_Model):
    system: str
    question_id: str
    repeat: int
    answer: str = ""
    citations: list[Citation] = Field(default_factory=list)
    trace: StreamTrace | None = None
    # Baselines: canonical URLs of the articles placed in the prompt.
    context_urls: list[str] = Field(default_factory=list)
    context_truncated: bool = False
    latency_ms: int = 0
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    cost_usd: float | None = None
    llm_calls: list[CallUsage] = Field(default_factory=list)
    # Wall clock when the system was asked (lines up backend logs).
    started_at: datetime | None = None
    retrieved: list[RetrievedChunk] = Field(default_factory=list)
    # Queries a pipeline searched with (expansion / decomposition output).
    queries: list[str] = Field(default_factory=list)
    error: SystemFailure | None = None
    policy_violations: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _legacy_id(cls, values: Any) -> Any:
        return _renamed(values, {})

    @property
    def key(self) -> tuple[str, str, int]:
        return (self.system, self.question_id, self.repeat)


def answer_fingerprint(answer: str) -> str:
    """Ties a judgment to the exact answer it graded, so a retried question
    (new answer, same key) is graded again instead of reusing a stale verdict."""
    return hashlib.sha256(answer.encode()).hexdigest()[:16]


class Judgment(_Model):
    system: str
    question_id: str
    repeat: int
    answer_sha: str
    # Which rubric produced this verdict; the set of rubrics is a registry,
    # not a closed literal, so a dataset can bring its own (EM, F1, ...).
    rubric: str
    role: Literal["primary", "secondary"]
    judge_model: str
    prompt_version: str
    # TRUE/FALSE (frames), CORRECT/INCORRECT/NOT_ATTEMPTED (strict), UNPARSEABLE.
    label: str
    parse_ok: bool
    reasked: bool = False
    raw: str = ""
    cache_hit: bool = False
    # Grading is the most call-heavy stage; without this the run budget
    # cannot see it. None on judgments written before it was recorded.
    cost_usd: float | None = None

    @model_validator(mode="before")
    @classmethod
    def _legacy(cls, values: Any) -> Any:
        return _renamed(values, {"kind": "rubric"})

    @property
    def key(self) -> tuple[str, str, int, str, str, str]:
        return (self.system, self.question_id, self.repeat, self.answer_sha, self.rubric, self.role)

    @property
    def correct(self) -> bool:
        return self.label in {"TRUE", "CORRECT"}


class ClaimSupport(_Model):
    system: str
    question_id: str
    repeat: int
    answer_sha: str
    claim_index: int
    claim: str
    cited_display_indices: list[int]
    support: float  # LongCite scale: 1 full, 0.5 partial, 0 none
    necessary: list[bool] = Field(default_factory=list)
    judge_model: str
    # Claim support fans out 2 x len(evidence) extra calls per claim, so it
    # is the least bounded thing the run pays for. None on older records.
    cost_usd: float | None = None

    @model_validator(mode="before")
    @classmethod
    def _legacy_id(cls, values: Any) -> Any:
        return _renamed(values, {})


class RankedList(_Model):
    system: str
    question_id: str
    ranked_refs: list[str]

    @model_validator(mode="before")
    @classmethod
    def _legacy(cls, values: Any) -> Any:
        return _renamed(values, {"ranked_urls": "ranked_refs"})


class QuestionScore(_Model):
    system: str
    question_id: str
    repeat: int
    split: Split | None
    labels: tuple[str, ...] = ()
    gold_count: int
    # Primary/secondary verdicts of whichever rubric the run graded with.
    correct: bool | None = None
    secondary_correct: bool | None = None
    strict_label: str | None = None
    context_recall: float | None = None
    all_gold_in_context: bool | None = None
    surfaced_recall: float | None = None
    n_tool_calls: int = 0
    n_searches: int = 0
    n_fetches: int = 0
    tool_waves: int = 0
    citation_integrity: bool | None = None
    cited_precision: float | None = None
    cited_recall: float | None = None
    all_hops_cited: bool | None = None
    alce_recall: float | None = None
    alce_precision: float | None = None
    grounded: bool | None = None
    failure: str | None = None
    missing_gold: list[str] = Field(default_factory=list)
    latency_ms: int = 0
    cost_usd: float | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cached_tokens: int | None = None
    llm_calls: int | None = None
    error_kind: str | None = None
    policy_violation: bool = False

    @model_validator(mode="before")
    @classmethod
    def _legacy(cls, values: Any) -> Any:
        return _renamed(values, {
            "frames_correct": "correct",
            "frames_secondary_correct": "secondary_correct",
            "reasoning_types": "labels",
        })


class RunMeta(_Model):
    run_id: str
    run_name: str
    created_at: datetime
    config_hash: str
    harness_version: str
    git_sha: str | None = None
    dataset_revision: str
    dataset_sha256: str
    split_sha256: str
    corpus_version: str | None = None
    snapshot: datetime
    models: dict[str, str] = Field(default_factory=dict)
    backend: dict[str, str] = Field(default_factory=dict)
    # version -> sha256 of every prompt the run ANSWERED with. The grader
    # prompts were already pinned; without these a published board could not
    # be traced to the prompts that produced it.
    answer_prompts: dict[str, str] = Field(default_factory=dict)
