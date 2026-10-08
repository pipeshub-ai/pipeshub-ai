"""Real-model comparison of ordinary delegation vs delegate handoff.

Answers: with `PIPESHUB_DELEGATE_HANDOFF` on, does the root agent skip its
second model pass without hurting the answer, and how often does the model
choose `final=true` when it should (and shouldn't)?

Each run goes through what `stream_bridge.py` runs for the default assistant
in `react` mode: `PipesHubAgentFactory.create`, `Agent.stream()`,
`TerminalAnswerStreamer` and `AnswerFinalizer`, with the same transports
production registers. Only the context is minimal (no graph, knowledge or
connectors). The arms differ only in the flag:

    delegate  PIPESHUB_DELEGATE_HANDOFF=false  (today's behaviour)
    handoff   PIPESHUB_DELEGATE_HANDOFF=true

Model calls, tokens and cost are counted where the factory's transports are
registered, so the root agent, its delegates and the intent call are all
included. Time-to-first-answer is read off the `answer_chunk` frames a
browser would receive.

Usage (real model; credentials from the environment or `--env-file`):

    python -m app.agents.agent_loop.evals.handoff_comparison \\
        --provider anthropic --model claude-sonnet-4-6 --runs 3 --out report.json
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import math
import os
import random
import re
import statistics
import sys
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from typing import TYPE_CHECKING, Any, Literal
from unittest.mock import patch

from pydantic import BaseModel, Field, PrivateAttr

from app.agent_loop_lib.modules.providers.budget.pricing import (
    MODEL_PRICING,
    ModelPricing,
    get_pricing,
)
from app.agent_loop_lib.transport.base import LLMTransport
from app.agent_loop_lib.transport.registry import TransportRegistry
from app.agents.agent_loop import factory as factory_module
from app.agents.agent_loop.answer_streamer import TerminalAnswerStreamer
from app.agents.agent_loop.context import AgentContext
from app.agents.agent_loop.evals.handoff_queries import (
    HANDOFF_QUERIES,
    HandoffQuery,
    select_queries,
)
from app.agents.agent_loop.factory import PipesHubAgentFactory
from app.agents.agent_loop.hooks.citations import CitationCollector
from app.agents.agent_loop.respond import AnswerFinalizer
from app.services.artifact_registry.models import ArtifactMetadata, ArtifactVisibility

if TYPE_CHECKING:
    from app.agent_loop_lib.core.responses import TokenUsage
    from app.models.entities import ArtifactType

logger = logging.getLogger(__name__)

Arm = Literal["delegate", "handoff"]
ARMS: tuple[Arm, ...] = ("delegate", "handoff")
HANDOFF_ENV = "PIPESHUB_DELEGATE_HANDOFF"


# --------------------------------------------------------------------------
# Models
# --------------------------------------------------------------------------


class CallRecord(BaseModel):
    kind: Literal["complete", "structured", "stream"]
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    duration_s: float = 0.0
    failed: bool = False

    def add_usage(self, usage: "TokenUsage | None") -> None:
        if usage is None:
            return
        self.input_tokens = usage.input_tokens
        self.output_tokens = usage.output_tokens
        self.cache_read_tokens = usage.cache_read_tokens
        self.cache_write_tokens = usage.cache_write_tokens


class AnswerTiming(BaseModel):
    first_text_s: float | None = None
    first_answer_s: float | None = None
    last_answer_s: float | None = None


class RunRecord(BaseModel):
    query_id: str
    category: str
    arm: Arm
    rep: int
    expect_final: bool | None
    wall_s: float = 0.0
    setup_s: float = 0.0
    first_text_s: float | None = None
    first_answer_s: float | None = None
    last_answer_s: float | None = None
    model_calls: int = 0
    root_model_calls: int = 0
    calls_by_kind: dict[str, int] = Field(default_factory=dict)
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost_usd: float = 0.0
    final_requested: bool = False
    final_requested_delegates: list[str] = Field(default_factory=list)
    answered_by: str | None = None
    fallbacks: int = 0
    answer: str = ""
    artifacts: list[str] = Field(default_factory=list)
    raw_citation_markers: int = 0
    unresolved_citation_markers: int = 0
    normalized_citations: int = 0
    clarification_asked: bool = False
    errors: list[str] = Field(default_factory=list)


class JudgeVerdict(BaseModel):
    preference: Literal["A", "B", "tie"]
    reason: str
    completeness_a: int = Field(ge=1, le=5)
    completeness_b: int = Field(ge=1, le=5)


class JudgedPair(BaseModel):
    query_id: str
    category: str
    rep: int
    a_is: Arm
    winner: Arm | Literal["tie"]
    reason: str
    completeness: dict[str, int]


class MetricStats(BaseModel):
    n: int
    median: float
    p90: float


class ArmSummary(BaseModel):
    runs: int
    failed_runs: int
    latency_s: MetricStats | None
    first_answer_s: MetricStats | None
    last_answer_s: MetricStats | None
    cost_usd: MetricStats | None
    model_calls: MetricStats | None
    final_requested_rate: float
    answered_directly_rate: float
    fallbacks: int


class FinalChoice(BaseModel):
    query_id: str
    expect_final: bool | None
    runs: int
    final_requested_rate: float
    answered_directly_rate: float
    fallbacks: int
    matches_expectation: float | None


class JudgeSummary(BaseModel):
    pairs: int
    handoff_wins: int
    ties: int
    delegate_wins: int
    mean_completeness: dict[str, float]


class Report(BaseModel):
    provider: str
    model: str
    judge_model: str | None
    runs_per_query: int
    pricing_known: bool
    records: list[RunRecord]
    by_arm: dict[str, ArmSummary]
    by_category: dict[str, dict[str, ArmSummary]]
    final_choice: list[FinalChoice]
    final_agreement: float | None
    judged: list[JudgedPair]
    judge: JudgeSummary | None
    judge_by_category: dict[str, JudgeSummary]
    judge_cost_usd: float


class JudgeRequest(BaseModel):
    prompt: str
    answer_a: str
    answer_b: str
    artifacts_a: list[str]
    artifacts_b: list[str]


JudgeFn = Callable[[JudgeRequest], Awaitable[JudgeVerdict]]
TransportOverride = Callable[[HandoffQuery, Arm], LLMTransport | None]


# --------------------------------------------------------------------------
# Counting at the transport layer
# --------------------------------------------------------------------------


class CallLedger:
    def __init__(self) -> None:
        self.calls: list[CallRecord] = []

    @contextlib.contextmanager
    def track(self, kind: Literal["complete", "structured", "stream"]) -> Iterator[CallRecord]:
        record = CallRecord(kind=kind)
        self.calls.append(record)
        started = time.perf_counter()
        try:
            yield record
        except BaseException:
            record.failed = True
            raise
        finally:
            record.duration_s = time.perf_counter() - started


class MeteredTransport(LLMTransport):
    """Counts every call the wrapped transport serves, whichever agent made it."""

    def __init__(self, inner: LLMTransport, ledger: CallLedger) -> None:
        super().__init__()
        self._inner = inner
        self._ledger = ledger

    @property
    def provider(self) -> str:
        return self._inner.provider

    @property
    def model_name(self) -> str:
        return self._inner.model_name

    def __getattr__(self, name: str) -> Any:  # noqa: ANN401
        if name == "_inner":
            raise AttributeError(name)
        return getattr(self._inner, name)

    async def complete(self, messages: list, **kwargs: Any) -> Any:  # noqa: ANN401
        with self._ledger.track("complete") as record:
            response = await self._inner.complete(messages, **kwargs)
            record.model = response.model
            record.add_usage(response.usage)
            return response

    async def complete_structured(self, messages: list, output_schema: dict, **kwargs: Any) -> Any:  # noqa: ANN401
        with self._ledger.track("structured") as record:
            response = await self._inner.complete_structured(messages, output_schema, **kwargs)
            record.model = response.model
            record.add_usage(response.usage)
            return response

    async def stream(self, messages: list, **kwargs: Any) -> AsyncIterator[Any]:  # noqa: ANN401
        from app.agent_loop_lib.core.streaming import StreamCompleteEvent

        with self._ledger.track("stream") as record:
            async for event in self._inner.stream(messages, **kwargs):
                if isinstance(event, StreamCompleteEvent):
                    record.model = event.response.model
                    record.add_usage(event.response.usage)
                yield event


class MeteredRegistry(TransportRegistry):
    """Stands in for the registry `PipesHubAgentFactory.create` builds, so
    every transport it registers (including the one skills and the intent
    call resolve) is metered. `override` replaces the real transports."""

    def __init__(self, ledger: CallLedger, override: LLMTransport | None = None) -> None:
        super().__init__()
        self._ledger = ledger
        self._override = override

    def register(self, provider: str, factory: Callable[[], LLMTransport]) -> None:
        super().register(
            provider, lambda: MeteredTransport(self._override or factory(), self._ledger),
        )


# --------------------------------------------------------------------------
# What a client would see
# --------------------------------------------------------------------------


class RecordingSink:
    """`EventSink` that timestamps the frames a client receives."""

    def __init__(self) -> None:
        self._t0 = time.perf_counter()
        self._chunks: list[tuple[float, str]] = []
        self._frozen = False
        self.artifact_names: list[str] = []

    async def write(self, event: dict[str, Any]) -> bool:
        data = event.get("data") or {}
        if event.get("event") == "answer_chunk" and not self._frozen:
            self._chunks.append((time.perf_counter() - self._t0, data.get("accumulated", "")))
        elif event.get("event") == "artifact":
            self.artifact_names.append(data.get("fileName", ""))
        return True

    async def flush(self) -> None:
        return None

    def freeze(self) -> None:
        """The finalizer re-sends the finished answer; that is not a token."""
        self._frozen = True

    def timing(self) -> AnswerTiming:
        """An empty `accumulated` clears the screen (a turn that went on to call
        a tool), so the answer starts at the first text after the last clear."""
        timing = AnswerTiming()
        segment_start: float | None = None
        for at, shown in self._chunks:
            if not shown:
                segment_start = None
                continue
            if timing.first_text_s is None:
                timing.first_text_s = at
            if segment_start is None:
                segment_start = at
            timing.last_answer_s = at
        timing.first_answer_s = segment_start
        if segment_start is None:
            timing.last_answer_s = None
        return timing


class _InMemoryArtifactRegistry:
    """Just enough of `ArtifactRegistryService` for files to be delivered
    without a graph or blob store."""

    def __init__(self) -> None:
        self.items: dict[str, ArtifactMetadata] = {}

    def _store(self, name: str, artifact_type: ArtifactType, mime_type: str, content: bytes, **fields: Any) -> ArtifactMetadata:  # noqa: ANN401
        existing = self.items.get(name)
        metadata = ArtifactMetadata(
            artifact_id=existing.artifact_id if existing else uuid.uuid4().hex,
            org_id=fields["org_id"], conversation_id=fields.get("conversation_id"),
            name=name, logical_name=name, artifact_type=artifact_type, mime_type=mime_type,
            version=existing.version + 1 if existing else 1, size_bytes=len(content),
            source_tool=fields.get("source_tool"),
            is_temporary=fields.get("is_temporary", False), visibility=ArtifactVisibility.VISIBLE,
        )
        self.items[name] = metadata
        return metadata

    async def register_output(self, *, actor: Any, name: str, artifact_type: ArtifactType, mime_type: str, content: bytes, conversation_id: str, source_tool: str | None = None, **_: Any) -> tuple[ArtifactMetadata, None]:  # noqa: ANN401
        return self._store(
            name, artifact_type, mime_type, content,
            org_id=actor.org_id, conversation_id=conversation_id, source_tool=source_tool,
        ), None

    async def register(self, *, actor: Any, name: str, artifact_type: ArtifactType, mime_type: str, content: bytes, conversation_id: str, source_tool: str | None = None, is_temporary: bool = False, **_: Any) -> ArtifactMetadata:  # noqa: ANN401
        return self._store(
            name, artifact_type, mime_type, content, org_id=actor.org_id,
            conversation_id=conversation_id, source_tool=source_tool, is_temporary=is_temporary,
        )

    async def record_derivation(self, **_: Any) -> None:  # noqa: ANN401
        return None

    async def get_download_url(self, *, actor: Any, artifact_id: str) -> str:  # noqa: ANN401
        return f"memory://{artifact_id}"


class _EvalContext(AgentContext):
    _artifacts: _InMemoryArtifactRegistry = PrivateAttr(default_factory=_InMemoryArtifactRegistry)

    @property
    def artifact_registry(self) -> Any:  # noqa: ANN401
        return self._artifacts


class _DefaultsConfigService:
    """Every platform flag and setting reads as its default."""

    async def get_config(self, key: str, default: Any = None, **_: Any) -> Any:  # noqa: ANN401
        return default


# --------------------------------------------------------------------------
# One run
# --------------------------------------------------------------------------

_RAW_MARKER = re.compile(r"\[[^\]]+\]\(ref[^)\s]*\)")
_NUMBERED_MARKER = re.compile(r"\[\d+\]")


@contextlib.contextmanager
def delegate_handoff_flag(arm: Arm) -> Iterator[None]:
    """Sets the env var `factory._delegate_handoff_enabled` reads, then checks
    the helper agrees so a changed source of truth fails loudly."""
    previous = os.environ.get(HANDOFF_ENV)
    os.environ[HANDOFF_ENV] = "true" if arm == "handoff" else "false"
    try:
        if factory_module._delegate_handoff_enabled() != (arm == "handoff"):
            raise RuntimeError(f"{HANDOFF_ENV} no longer controls factory._delegate_handoff_enabled()")
        yield
    finally:
        if previous is None:
            os.environ.pop(HANDOFF_ENV, None)
        else:
            os.environ[HANDOFF_ENV] = previous


def _build_context(query: HandoffQuery, llm: Any, sink: RecordingSink, provider_id: str, model_name: str) -> _EvalContext:  # noqa: ANN401
    context = _EvalContext(
        org_id="handoff-eval-org", user_id="handoff-eval-user", user_email="eval@example.com",
        user_info={"userId": "handoff-eval-user", "orgId": "handoff-eval-org"},
        org_info={"name": "HandoffEval"},
        logger=logging.getLogger("handoff_comparison.agent"),
        llm=llm, config_service=_DefaultsConfigService(),
        invocation="assistant", protocol="legacy", event_sink=sink,
        conversation_id=f"handoff-eval-{uuid.uuid4().hex[:8]}",
        llm_provider=provider_id, model_name=model_name, query=query.prompt,
        web_search_config={"provider": "duckduckgo", "configuration": {}},
    )
    context.tool_state["web_search_config"] = context.web_search_config
    context.tool_state["event_sink"] = sink
    context.tool_state["sse_protocol"] = "legacy"
    return context


def _requested_final(turns: list[Any]) -> list[str]:
    return [
        call.name for turn in turns for call in turn.tool_calls
        if call.arguments.get("final") in (True, "true")
    ]


def _fill_from_ledger(record: RunRecord, ledger: CallLedger, model_name: str, pricing: ModelPricing | None) -> None:
    record.model_calls = len(ledger.calls)
    for call in ledger.calls:
        record.calls_by_kind[call.kind] = record.calls_by_kind.get(call.kind, 0) + 1
        record.input_tokens += call.input_tokens
        record.output_tokens += call.output_tokens
        record.cache_read_tokens += call.cache_read_tokens
        record.cache_write_tokens += call.cache_write_tokens
        price = pricing or get_pricing(call.model or model_name)
        record.cost_usd += price.cost_usd(
            call.input_tokens, call.output_tokens, call.cache_read_tokens, call.cache_write_tokens,
        )


async def _drive(
    query: HandoffQuery, context: _EvalContext, llm: Any, model_name: str, sink: RecordingSink, record: RunRecord, started: float,  # noqa: ANN401
) -> None:
    agent, _runtime, goal, clarifying = await PipesHubAgentFactory().create(
        context, llm, "react", query=query.prompt, model_name=model_name,
    )
    record.setup_s = time.perf_counter() - started
    if clarifying:
        record.clarification_asked = True
        return

    collector = CitationCollector(context)
    streamer = TerminalAnswerStreamer(context, collector, sink)
    async for event in agent.stream(goal):
        await streamer.on_event(event)
    sink.freeze()
    result = agent.last_stream_result

    completion = await AnswerFinalizer(context, collector).run(
        agent_success=result.success, agent_error=result.error, agent_output=result.output,
        event_sink=sink, streamed_answer=streamer.streamed_answer,
        reasoning_turns=streamer.reasoning_turns, agent_needs_input=result.needs_input,
        answered_via=result.answered_by,
    )

    record.answer = str(completion.get("answer") or "")
    record.answered_by = result.answered_by
    record.root_model_calls = result.usage.requests
    requested = _requested_final(result.turns)
    record.final_requested = bool(requested)
    record.final_requested_delegates = requested
    record.fallbacks = max(len(requested) - (1 if result.answered_by else 0), 0)
    if not result.success:
        record.errors.append(result.error or "agent run failed")
    raw_output = result.output if isinstance(result.output, str) else ""
    record.raw_citation_markers = len(_RAW_MARKER.findall(raw_output))
    record.unresolved_citation_markers = len(_RAW_MARKER.findall(record.answer))
    record.normalized_citations = len(_NUMBERED_MARKER.findall(record.answer))
    record.artifacts = sorted(
        {a.get("name", "") for a in context.artifacts_registered_this_run} | set(sink.artifact_names) - {""},
    )


async def run_once(
    query: HandoffQuery, arm: Arm, rep: int, *, llm: Any, provider_id: str, model_name: str,  # noqa: ANN401
    pricing: ModelPricing | None = None, timeout_s: float = 300.0, transport: LLMTransport | None = None,
) -> RunRecord:
    record = RunRecord(query_id=query.id, category=query.category, arm=arm, rep=rep, expect_final=query.expect_final)
    ledger = CallLedger()
    sink = RecordingSink()
    context = _build_context(query, llm, sink, provider_id, model_name)
    started = time.perf_counter()
    try:
        with delegate_handoff_flag(arm), patch.object(
            factory_module, "TransportRegistry", lambda: MeteredRegistry(ledger, transport),
        ):
            await asyncio.wait_for(
                _drive(query, context, llm, model_name, sink, record, started), timeout_s,
            )
    except Exception as exc:
        record.errors.append(f"{type(exc).__name__}: {exc}")
    finally:
        record.wall_s = time.perf_counter() - started
        timing = sink.timing()
        record.first_text_s, record.first_answer_s, record.last_answer_s = (
            timing.first_text_s, timing.first_answer_s, timing.last_answer_s,
        )
        _fill_from_ledger(record, ledger, model_name, pricing)
        if context.sandbox_manager is not None:
            with contextlib.suppress(Exception):
                await context.sandbox_manager.destroy_all()
    return record


# --------------------------------------------------------------------------
# Judge
# --------------------------------------------------------------------------

_JUDGE_SYSTEM = """\
You compare two answers to the same user request. You do not know how either was produced.
Judge only what the user receives: correctness, completeness against the request, directness, \
and honesty about anything that failed or could not be verified. Files listed as attached \
were delivered to the user; do not penalise an answer for not pasting their contents. \
Penalise broken citation markers, leaked internal paths, and answers written as a report \
to someone else instead of to the user. Ignore length and style unless they affect those.
Reply with one JSON object and nothing else:
{"preference": "A" | "B" | "tie", "reason": "<one or two sentences>", \
"completeness_a": <1-5>, "completeness_b": <1-5>}"""


_ARTIFACT_MARKER = re.compile(r"\n*::artifact\[[^\]]*\]\([^)]*\)\{[^}]*\}")


def _judge_user_message(request: JudgeRequest) -> str:
    def block(label: str, answer: str, files: list[str]) -> str:
        attached = ", ".join(files) if files else "none"
        body = _ARTIFACT_MARKER.sub("", answer).strip()
        return f"## Answer {label}\nFiles attached: {attached}\n\n{body or '(empty)'}"

    return (
        f"## User request\n{request.prompt}\n\n"
        f"{block('A', request.answer_a, request.artifacts_a)}\n\n"
        f"{block('B', request.answer_b, request.artifacts_b)}"
    )


def _parse_verdict(text: str) -> JudgeVerdict:
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise ValueError(f"no JSON object in judge reply: {text[:200]!r}")
    return JudgeVerdict.model_validate(json.loads(text[start:end + 1]))


def _message_text(content: Any) -> str:  # noqa: ANN401
    if isinstance(content, str):
        return content
    return "".join(
        part.get("text", "") if isinstance(part, dict) else str(part) for part in content
    )


class LLMJudge:
    """Pairwise judge on a LangChain chat model; keeps its own token counts so
    judging cost is reported apart from the arms."""

    def __init__(self, llm: Any, model_name: str) -> None:  # noqa: ANN401
        self._llm = llm
        self.model_name = model_name
        self.input_tokens = 0
        self.output_tokens = 0

    async def __call__(self, request: JudgeRequest) -> JudgeVerdict:
        from langchain_core.messages import HumanMessage, SystemMessage

        messages = [SystemMessage(content=_JUDGE_SYSTEM), HumanMessage(content=_judge_user_message(request))]
        last_error: Exception | None = None
        for _ in range(2):
            reply = await self._llm.ainvoke(messages)
            usage = getattr(reply, "usage_metadata", None) or {}
            self.input_tokens += usage.get("input_tokens", 0)
            self.output_tokens += usage.get("output_tokens", 0)
            try:
                return _parse_verdict(_message_text(reply.content))
            except (ValueError, json.JSONDecodeError) as exc:
                last_error = exc
        raise ValueError(f"judge reply unparseable: {last_error}")

    def cost_usd(self) -> float:
        return get_pricing(self.model_name).cost_usd(self.input_tokens, self.output_tokens)


async def judge_pairs(
    records: list[RunRecord], judge: JudgeFn, queries: tuple[HandoffQuery, ...], rng: random.Random,
) -> list[JudgedPair]:
    prompts = {q.id: q.prompt for q in queries}
    grouped: dict[tuple[str, int], dict[str, RunRecord]] = {}
    for record in records:
        grouped.setdefault((record.query_id, record.rep), {})[record.arm] = record

    judged: list[JudgedPair] = []
    for (query_id, rep), arms in sorted(grouped.items()):
        delegate, handoff = arms.get("delegate"), arms.get("handoff")
        if delegate is None or handoff is None or not (delegate.answer and handoff.answer):
            continue
        first, second = (delegate, handoff) if rng.random() < 0.5 else (handoff, delegate)
        try:
            verdict = await judge(JudgeRequest(
                prompt=prompts[query_id], answer_a=first.answer, answer_b=second.answer,
                artifacts_a=first.artifacts, artifacts_b=second.artifacts,
            ))
        except Exception as exc:
            logger.warning("judge failed for %s rep %d: %s", query_id, rep, exc)
            continue
        winner: Arm | Literal["tie"] = (
            "tie" if verdict.preference == "tie" else (first.arm if verdict.preference == "A" else second.arm)
        )
        judged.append(JudgedPair(
            query_id=query_id, category=delegate.category, rep=rep, a_is=first.arm, winner=winner,
            reason=verdict.reason,
            completeness={first.arm: verdict.completeness_a, second.arm: verdict.completeness_b},
        ))
    return judged


# --------------------------------------------------------------------------
# Aggregation
# --------------------------------------------------------------------------


def _stats(values: list[float]) -> MetricStats | None:
    if not values:
        return None
    ordered = sorted(values)
    rank = max(math.ceil(0.9 * len(ordered)) - 1, 0)
    return MetricStats(n=len(ordered), median=statistics.median(ordered), p90=ordered[rank])


def summarize_arm(records: list[RunRecord]) -> ArmSummary:
    ok = [r for r in records if not r.errors]

    def of(attr: str) -> MetricStats | None:
        return _stats([v for r in ok if (v := getattr(r, attr)) is not None])

    count = len(records) or 1
    return ArmSummary(
        runs=len(records), failed_runs=len(records) - len(ok),
        latency_s=of("wall_s"), first_answer_s=of("first_answer_s"), last_answer_s=of("last_answer_s"),
        cost_usd=of("cost_usd"), model_calls=of("model_calls"),
        final_requested_rate=sum(r.final_requested for r in records) / count,
        answered_directly_rate=sum(r.answered_by is not None for r in records) / count,
        fallbacks=sum(r.fallbacks for r in records),
    )


def summarize_final_choice(records: list[RunRecord]) -> tuple[list[FinalChoice], float | None]:
    handoff = [r for r in records if r.arm == "handoff"]
    rows: list[FinalChoice] = []
    for query_id in dict.fromkeys(r.query_id for r in handoff):
        runs = [r for r in handoff if r.query_id == query_id]
        expect = runs[0].expect_final
        requested = sum(r.final_requested for r in runs) / len(runs)
        rows.append(FinalChoice(
            query_id=query_id, expect_final=expect, runs=len(runs), final_requested_rate=requested,
            answered_directly_rate=sum(r.answered_by is not None for r in runs) / len(runs),
            fallbacks=sum(r.fallbacks for r in runs),
            matches_expectation=None if expect is None else sum(r.final_requested == expect for r in runs) / len(runs),
        ))
    scored = [r for r in handoff if r.expect_final is not None]
    agreement = sum(r.final_requested == r.expect_final for r in scored) / len(scored) if scored else None
    return rows, agreement


def summarize_judge(pairs: list[JudgedPair]) -> JudgeSummary | None:
    if not pairs:
        return None
    return JudgeSummary(
        pairs=len(pairs),
        handoff_wins=sum(p.winner == "handoff" for p in pairs),
        ties=sum(p.winner == "tie" for p in pairs),
        delegate_wins=sum(p.winner == "delegate" for p in pairs),
        mean_completeness={
            arm: round(statistics.mean(p.completeness[arm] for p in pairs), 2) for arm in ARMS
        },
    )


def build_report(
    records: list[RunRecord], judged: list[JudgedPair], *, provider: str, model: str,
    judge_model: str | None, runs: int, pricing_known: bool, judge_cost_usd: float,
) -> Report:
    categories = list(dict.fromkeys(r.category for r in records))
    final_choice, agreement = summarize_final_choice(records)
    return Report(
        provider=provider, model=model, judge_model=judge_model, runs_per_query=runs,
        pricing_known=pricing_known, records=records,
        by_arm={arm: summarize_arm([r for r in records if r.arm == arm]) for arm in ARMS},
        by_category={
            cat: {arm: summarize_arm([r for r in records if r.arm == arm and r.category == cat]) for arm in ARMS}
            for cat in categories
        },
        final_choice=final_choice, final_agreement=agreement, judged=judged,
        judge=summarize_judge(judged),
        judge_by_category={
            cat: summary for cat in categories
            if (summary := summarize_judge([p for p in judged if p.category == cat])) is not None
        },
        judge_cost_usd=judge_cost_usd,
    )


# --------------------------------------------------------------------------
# Running the whole comparison
# --------------------------------------------------------------------------


async def run_comparison(
    queries: tuple[HandoffQuery, ...], *, llm: Any, provider_id: str, model_name: str, runs: int = 3,  # noqa: ANN401
    judge: JudgeFn | None = None, judge_model: str | None = None, judge_cost: Callable[[], float] = lambda: 0.0,
    pricing: ModelPricing | None = None, timeout_s: float = 300.0, seed: int = 0,
    transport_for: TransportOverride | None = None, progress: Callable[[str], None] = lambda _: None,
) -> Report:
    """Arms alternate which goes first per (query, rep), so a slow minute at
    the provider does not land on one arm."""
    records: list[RunRecord] = []
    for rep in range(runs):
        for index, query in enumerate(queries):
            order = ARMS if (rep + index) % 2 == 0 else ARMS[::-1]
            for arm in order:
                record = await run_once(
                    query, arm, rep, llm=llm, provider_id=provider_id, model_name=model_name,
                    pricing=pricing, timeout_s=timeout_s,
                    transport=transport_for(query, arm) if transport_for else None,
                )
                records.append(record)
                progress(
                    f"[rep {rep + 1}/{runs}] {query.id:<24} {arm:<8} {record.wall_s:6.1f}s "
                    f"calls={record.model_calls} final={record.final_requested} "
                    f"{'ERR ' + record.errors[0][:80] if record.errors else ''}",
                )
    judged = await judge_pairs(records, judge, queries, random.Random(seed)) if judge else []
    return build_report(
        records, judged, provider=provider_id, model=model_name, judge_model=judge_model, runs=runs,
        pricing_known=pricing is not None or model_name in MODEL_PRICING, judge_cost_usd=judge_cost(),
    )


# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------


def _cell(stats: MetricStats | None, fmt: str = "{:.1f}") -> str:
    return "-" if stats is None else f"{fmt.format(stats.median)} / {fmt.format(stats.p90)}"


def render_markdown(report: Report) -> str:
    lines = [
        f"# Delegate handoff comparison: {report.provider} / {report.model}",
        f"{report.runs_per_query} run(s) per query and arm. Cells are median / p90."
        + ("" if report.pricing_known else " Cost uses default pricing: model not in the pricing table."),
        "",
        "| scope | arm | runs (failed) | latency s | first answer s | last answer s | cost $ | model calls | final req | answered directly | fallbacks |",
        "|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    scopes = [("all", report.by_arm), *report.by_category.items()]
    for name, arms in scopes:
        for arm in ARMS:
            s = arms[arm]
            lines.append(
                f"| {name} | {arm} | {s.runs} ({s.failed_runs}) | {_cell(s.latency_s)} | {_cell(s.first_answer_s)} "
                f"| {_cell(s.last_answer_s)} | {_cell(s.cost_usd, '{:.4f}')} | {_cell(s.model_calls, '{:.0f}')} "
                f"| {s.final_requested_rate:.0%} | {s.answered_directly_rate:.0%} | {s.fallbacks} |",
            )
    lines += ["", "## Does the model choose final=true when it should?", "",
              "| query | expected | final requested | answered directly | fallbacks | matches expectation |",
              "|---|---|---|---|---|---|"]
    for row in report.final_choice:
        expected = "either" if row.expect_final is None else str(row.expect_final).lower()
        match = "-" if row.matches_expectation is None else f"{row.matches_expectation:.0%}"
        lines.append(
            f"| {row.query_id} | {expected} | {row.final_requested_rate:.0%} "
            f"| {row.answered_directly_rate:.0%} | {row.fallbacks} | {match} |",
        )
    if report.final_agreement is not None:
        lines.append(f"\nOverall agreement with expectation: {report.final_agreement:.0%}")
    if report.judge is not None:
        lines += ["", f"## Quality (pairwise, blind, judge {report.judge_model})", "",
                  "| scope | pairs | handoff wins | ties | delegate wins | completeness handoff | completeness delegate |",
                  "|---|---|---|---|---|---|---|"]
        for name, j in [("all", report.judge), *report.judge_by_category.items()]:
            lines.append(
                f"| {name} | {j.pairs} | {j.handoff_wins} | {j.ties} | {j.delegate_wins} "
                f"| {j.mean_completeness['handoff']} | {j.mean_completeness['delegate']} |",
            )
        lines.append(f"\nJudge cost: ${report.judge_cost_usd:.4f}")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Credentials, environment, CLI
# --------------------------------------------------------------------------


class ProviderSpec(BaseModel):
    provider_id: str
    required_env: tuple[str, ...]
    key_env: str
    optional_config_env: dict[str, str] = Field(default_factory=dict)


PROVIDERS: dict[str, ProviderSpec] = {
    "anthropic": ProviderSpec(provider_id="anthropic", required_env=("ANTHROPIC_API_KEY",), key_env="ANTHROPIC_API_KEY"),
    "openai": ProviderSpec(
        provider_id="openAI", required_env=("OPENAI_API_KEY",), key_env="OPENAI_API_KEY",
        optional_config_env={"OPENAI_ORG_ID": "organizationId"},
    ),
}


class CredentialsError(RuntimeError):
    pass


def load_env_file(path: str) -> None:
    """`KEY=VALUE` lines into `os.environ`; variables already set win."""
    with open(path, encoding="utf-8") as handle:
        for raw in handle:
            line = raw.strip().removeprefix("export ").strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def require_credentials(provider: str) -> ProviderSpec:
    spec = PROVIDERS[provider]
    missing = [name for name in spec.required_env if not os.environ.get(name)]
    if missing:
        raise CredentialsError(
            f"Missing environment variable(s) for provider '{provider}': {', '.join(missing)}. "
            "Export them or pass --env-file with KEY=VALUE lines.",
        )
    return spec


def build_llm(spec: ProviderSpec, model: str) -> Any:  # noqa: ANN401
    from app.utils.aimodels import get_generator_model

    configuration: dict[str, Any] = {"apiKey": os.environ[spec.key_env], "model": model}
    for env_name, field in spec.optional_config_env.items():
        if os.environ.get(env_name):
            configuration[field] = os.environ[env_name]
    return get_generator_model(
        spec.provider_id, {"isDefault": True, "configuration": configuration}, model_name=model,
    )


def configure_environment() -> None:
    """What the harness assumes of this host: no knowledge/skills (they need a
    graph), local code execution unless the operator chose another backend."""
    os.environ.setdefault("PIPESHUB_ENABLE_SKILLS", "false")
    os.environ.setdefault("SANDBOX_MODE", "local")
    if os.environ["SANDBOX_MODE"].strip().lower() == "local":
        os.environ.setdefault("SANDBOX_ALLOW_LOCAL", "true")


def sandbox_problem() -> str | None:
    from app.agent_loop_lib.sandbox.coding.settings import (
        SandboxUnavailableError,
        resolve_sandbox_mode,
    )

    try:
        resolve_sandbox_mode()
    except SandboxUnavailableError as exc:
        return str(exc)
    return None


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m app.agents.agent_loop.evals.handoff_comparison",
        description="Compare ordinary delegation with delegate handoff (PIPESHUB_DELEGATE_HANDOFF) on a real model.",
    )
    parser.add_argument("--provider", required=True, choices=sorted(PROVIDERS))
    parser.add_argument("--model", required=True)
    parser.add_argument("--judge-model", help="defaults to --model")
    parser.add_argument("--runs", type=int, default=3, help="repetitions per query and arm (default 3)")
    parser.add_argument("--queries", help=f"comma-separated ids (default all): {', '.join(q.id for q in HANDOFF_QUERIES)}")
    parser.add_argument("--out", help="write the full JSON report here")
    parser.add_argument("--env-file", help="KEY=VALUE file with the provider credentials")
    parser.add_argument("--no-judge", action="store_true", help="skip the pairwise quality judge")
    parser.add_argument("--timeout", type=float, default=300.0, help="seconds per run (default 300)")
    parser.add_argument("--seed", type=int, default=0, help="seeds the judge's A/B order")
    parser.add_argument("--price-in", type=float, help="USD per 1M input tokens, overrides the pricing table")
    parser.add_argument("--price-out", type=float, help="USD per 1M output tokens, overrides the pricing table")
    return parser.parse_args(argv)


async def _amain(args: argparse.Namespace) -> int:
    try:
        if args.env_file:
            load_env_file(args.env_file)
        spec = require_credentials(args.provider)
        queries = select_queries([q.strip() for q in args.queries.split(",") if q.strip()] if args.queries else None)
    except (CredentialsError, ValueError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)  # noqa: T201
        return 2

    configure_environment()
    problem = sandbox_problem() if any(q.needs_code for q in queries) else None
    if problem:
        no_code = ",".join(q.id for q in HANDOFF_QUERIES if not q.needs_code)
        print(f"error: code execution is unavailable here: {problem}\nRun without code: --queries {no_code}", file=sys.stderr)  # noqa: T201
        return 2
    if (args.price_in is None) != (args.price_out is None):
        print("error: pass --price-in and --price-out together", file=sys.stderr)  # noqa: T201
        return 2

    pricing = None
    if args.price_in is not None:
        pricing = ModelPricing(input_price_per_mtok=args.price_in, output_price_per_mtok=args.price_out)
    judge_model = args.judge_model or args.model
    judge = None if args.no_judge else LLMJudge(build_llm(spec, judge_model), judge_model)

    def progress(message: str) -> None:
        print(message, file=sys.stderr, flush=True)  # noqa: T201

    report = await run_comparison(
        queries, llm=build_llm(spec, args.model), provider_id=args.provider, model_name=args.model,
        runs=args.runs, judge=judge, judge_model=None if judge is None else judge_model,
        judge_cost=judge.cost_usd if judge else lambda: 0.0, pricing=pricing,
        timeout_s=args.timeout, seed=args.seed, progress=progress,
    )
    print(render_markdown(report))  # noqa: T201
    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            handle.write(report.model_dump_json(indent=2))
    return 0


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(_amain(_parse_args(argv)))


if __name__ == "__main__":
    sys.exit(main())
