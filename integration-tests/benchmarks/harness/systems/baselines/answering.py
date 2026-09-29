"""Shared answering flow for the baselines (template method).

The FRAMES paper publishes no answering prompts, so one frozen, versioned
prompt is used for every baseline; only the documents differ.
"""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from benchmarks.harness.config import FRAMES_SNAPSHOT, ModelPrice
from benchmarks.harness.llm.client import ChatMessage, LLMClient, LLMRequest, LLMResponse, ResolvedModel
from benchmarks.harness.evidence import captured
from benchmarks.harness.models import AskItem, CallUsage, EvidencePassage, Prediction, SystemFailure, render_passages
from benchmarks.harness.pricing import calls_cost
from benchmarks.harness.systems.base import AdapterCapabilities, CorpusIngestor, PreparedCorpus, RankedRetriever

logger = logging.getLogger(__name__)

ANSWER_PROMPT_VERSION = "frames-answer-v3"
#: Separate version so the pin registry covers the grounded text too, and so
#: a cached closed-book answer can never be served to a document-bearing run.
GROUNDED_ANSWER_PROMPT_VERSION = "frames-answer-grounded-v1"
ANSWER_MAX_TOKENS = 2048
EVIDENCE_SOURCE = "baseline_prompt_articles"
_DEFAULT_CONTEXT_TOKENS = 128_000
_RESERVED_TOKENS = 8_000
_CHARS_PER_TOKEN = 4
_SYSTEM_PROMPT = (
    "You answer factual questions. Reason carefully, then state the final answer "
    "explicitly and concisely on the last line."
)
#: Used only when articles are actually supplied. `closed_book` deliberately keeps
#: the prompt above: it is the memory-only floor, and telling it to answer solely
#: from documents it was never given would turn it into a refusal machine and
#: destroy the very baseline the board needs.
_GROUNDED_SYSTEM_PROMPT = (
    "You answer factual questions from the Wikipedia articles provided.\n\n"
    "Answer ONLY from those articles. You may know an answer from your own training "
    "data — do not use it. If the articles do not contain what is needed, say exactly "
    "what is missing instead of filling the gap from memory: an unsupported answer is "
    "worse than an incomplete one. Combining facts that are each stated in the articles "
    "is expected; supplying a fact that is in none of them is not.\n\n"
    "Reason carefully, then state the final answer explicitly and concisely on the last line."
)


def dated_system_prompt(prompt: str, current_time: datetime) -> str:
    """Every system is told the snapshot date, as PipesHub is via `currentTime`."""
    return f"{prompt}\n\nToday's date is {current_time:%Y-%m-%d}."


def call_usage(response: LLMResponse, purpose: str) -> CallUsage:
    return CallUsage(
        input_tokens=response.prompt_tokens or 0, output_tokens=response.completion_tokens or 0,
        cached_tokens=response.cached_tokens or 0, purpose=purpose,
    )


def usage_fields(calls: Sequence[CallUsage], price: ModelPrice | None, fallback_cost: float | None = None) -> dict:
    return {
        "llm_calls": list(calls),
        "prompt_tokens": sum(c.input_tokens for c in calls),
        "completion_tokens": sum(c.output_tokens for c in calls),
        "cost_usd": calls_cost(price, calls) if price is not None else fallback_cost,
    }


@dataclass(frozen=True)
class ContextDocument:
    url: str
    title: str
    text: str


def fit_documents(docs: Sequence[ContextDocument], max_tokens: int) -> tuple[list[ContextDocument], bool]:
    """Keep documents in order until the budget runs out; the last one that
    does not fit is cut. Returns the kept documents and whether anything was lost."""
    budget = max_tokens * _CHARS_PER_TOKEN
    kept: list[ContextDocument] = []
    for doc in docs:
        if budget <= 0:
            return kept, True
        if len(doc.text) > budget:
            kept.append(ContextDocument(doc.url, doc.title, doc.text[:budget]))
            return kept, True
        kept.append(doc)
        budget -= len(doc.text)
    return kept, False


def document_passages(docs: Sequence[ContextDocument]) -> list[EvidencePassage]:
    return [EvidencePassage(header=f"### {d.title}\nURL: {d.url}\n\n", text=d.text) for d in docs]


def build_messages(
    prompt: str, docs: Sequence[ContextDocument], current_time: datetime = FRAMES_SNAPSHOT,
) -> tuple[ChatMessage, ...]:
    if docs:
        articles = render_passages(document_passages(docs))
        user = f"Wikipedia articles:\n\n{articles}\n\nQuestion: {prompt}"
    else:
        user = f"Question: {prompt}"
    system = _GROUNDED_SYSTEM_PROMPT if docs else _SYSTEM_PROMPT
    return (
        ChatMessage(role="system", content=dated_system_prompt(system, current_time)),
        ChatMessage(role="user", content=user),
    )


class BaselineAnswerer(ABC):
    capabilities = AdapterCapabilities()

    def __init__(
        self, system_id: str, llm: LLMClient, model: ResolvedModel,
        *, price: ModelPrice | None = None, current_time: datetime = FRAMES_SNAPSHOT,
    ) -> None:
        self.system_id = system_id
        self._llm = llm
        self._model = model
        self._price = price
        self._current_time = current_time

    @abstractmethod
    def documents_for(self, item: AskItem) -> list[ContextDocument]: ...

    def _context_budget(self) -> int:
        return (self._model.context_length or _DEFAULT_CONTEXT_TOKENS) - _RESERVED_TOKENS

    def answer(self, item: AskItem, prepared: PreparedCorpus, repeat: int) -> Prediction:
        started = time.monotonic()
        docs, truncated = fit_documents(self.documents_for(item), self._context_budget())
        request = LLMRequest(
            model=self._model, messages=build_messages(item.prompt, docs, self._current_time),
            max_tokens=ANSWER_MAX_TOKENS,
            prompt_version=GROUNDED_ANSWER_PROMPT_VERSION if docs else ANSWER_PROMPT_VERSION,
        )
        base = Prediction(
            system=self.system_id, question_id=item.question_id, repeat=repeat,
            context_urls=[d.url for d in docs], context_truncated=truncated,
        )
        try:
            response = self._llm.complete(request)
        except Exception as exc:  # noqa: BLE001 — recorded on the prediction and scored FALSE
            logger.warning("%s q%s failed: %s", self.system_id, item.question_id, exc)
            return base.model_copy(update={
                "latency_ms": int((time.monotonic() - started) * 1000),
                "error": SystemFailure(kind="llm", message=str(exc)[:1000]),
            })
        return base.model_copy(update={
            "answer": response.text, "latency_ms": int((time.monotonic() - started) * 1000),
            # Closed book yields `empty`: its answers can only come from memory.
            "evidence": captured(document_passages(docs), EVIDENCE_SOURCE),
            **usage_fields([call_usage(response, "answer")], self._price, response.cost_usd),
        })

    def ingestor(self) -> CorpusIngestor | None:
        return None

    def retriever(self) -> RankedRetriever | None:
        return None
