"""Pre-retrieval query transforms, each one LLM call on the answering model
(its tokens count toward the system's cost like any other call)."""

from __future__ import annotations

import re
from datetime import datetime

from benchmarks.harness.llm.client import ChatMessage, LLMClient, LLMRequest, LLMResponse, ResolvedModel
from benchmarks.harness.systems.baselines.answering import dated_system_prompt

EXPANSION_PROMPT_VERSION = "rag-expand-v1"
DECOMPOSITION_PROMPT_VERSION = "rag-decompose-v1"
_MAX_TOKENS = 1024
_EFFORT = "low"
_TIMEOUT_S = 120.0
_LIST_PREFIX = re.compile(r"^\s*(?:[-*•]|\d+[.)])\s*")

_EXPANSION_SYSTEM = (
    "You write search queries for a search engine over Wikipedia articles. Given a question, "
    "write {n} different search queries that together would retrieve the passages needed to "
    "answer it: vary wording, name the specific entities, and cover different aspects. "
    "Output one query per line and nothing else."
)
_DECOMPOSITION_SYSTEM = (
    "You break multi-hop questions into the simple factual sub-questions that must be answered "
    "to answer the original, in the order they need answering (at most {n}). Each sub-question "
    "must be self-contained and searchable on its own; when a later sub-question depends on an "
    "earlier answer, describe that dependency in words. Output one sub-question per line and "
    "nothing else."
)


def _lines(text: str, limit: int) -> list[str]:
    queries = [_LIST_PREFIX.sub("", line).strip() for line in text.splitlines()]
    return list(dict.fromkeys(q for q in queries if q))[:limit]


def _ask(
    llm: LLMClient, model: ResolvedModel, system: str, question: str, version: str, current_time: datetime,
) -> LLMResponse:
    return llm.complete(LLMRequest(
        model=model, max_tokens=_MAX_TOKENS, prompt_version=version,
        # A rewrite is not a reasoning task; at the answerer's effort the model
        # can spend its whole budget thinking about three search queries.
        effort=_EFFORT, reserve_reasoning_tokens=False, timeout_s=_TIMEOUT_S,
        messages=(
            ChatMessage(role="system", content=dated_system_prompt(system, current_time)),
            ChatMessage(role="user", content=f"Question: {question}"),
        ),
    ))


def expand(
    llm: LLMClient, model: ResolvedModel, question: str, n: int, current_time: datetime,
) -> tuple[list[str], LLMResponse]:
    response = _ask(llm, model, _EXPANSION_SYSTEM.format(n=n), question, EXPANSION_PROMPT_VERSION, current_time)
    return _lines(response.text, n), response


def decompose(
    llm: LLMClient, model: ResolvedModel, question: str, max_n: int, current_time: datetime,
) -> tuple[list[str], LLMResponse]:
    response = _ask(llm, model, _DECOMPOSITION_SYSTEM.format(n=max_n), question, DECOMPOSITION_PROMPT_VERSION, current_time)
    return _lines(response.text, max_n), response
