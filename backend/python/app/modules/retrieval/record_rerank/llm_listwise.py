"""Listwise LLM reranking: one call sees every candidate and names the best.

The candidates go in the fused order as ``[i] title :: text`` lines; the
reply is the candidate numbers, best first. Unknown, repeated and missing
numbers are dropped, and a reply with none is an error, which keeps the
fused order.

The model is one of the organisation's configured LLMs:
``RECORD_RERANK_MODEL`` names one by its model name or key; unset, or naming
none that is configured, the default chat model is used. It is built at low
reasoning effort and cached per org for five minutes.
"""

from __future__ import annotations

import json
import logging
import os
import re
from typing import TYPE_CHECKING, override

from cachetools import TTLCache
from langchain_core.messages import HumanMessage, SystemMessage

from app.modules.retrieval.record_rerank.base import (
    RecordReranker,
    RecordRerankError,
    RerankerDeps,
    RerankRequest,
)
from app.modules.retrieval.record_rerank.factory import register_record_reranker
from app.utils.aimodels import coerce_message_content_to_text
from app.utils.llm import get_llm, get_llm_by_name

if TYPE_CHECKING:
    from langchain_core.language_models.chat_models import BaseChatModel

    from app.config.configuration_service import ConfigurationService

RECORD_RERANK_MODEL_ENV_VAR = "RECORD_RERANK_MODEL"
# Ranking needs little deliberation, and the call sits on the search's path.
_REASONING_EFFORT = "low"
_LLM_TTL_S = 300.0

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You rank search results for an enterprise knowledge base. Given a user question and numbered candidate "
    "documents (title and a snippet), select the {top} documents most likely to contain the information needed "
    "to answer the question, most relevant first. Prefer the document the question describes (its type, project, "
    "meeting, version, or scope) over documents on a related topic. Return JSON only: "
    '{{"ranking": [{top} candidate numbers]}}.'
)

_DIGITS = re.compile(r"\d+")


def candidate_lines(request: RerankRequest) -> str:
    lines = []
    for number, candidate in enumerate(request.candidates, start=1):
        title = candidate.title or "(untitled)"
        lines.append(f"[{number}] {title} :: {candidate.text}" if candidate.text else f"[{number}] {title}")
    return "\n".join(lines)


def user_prompt(request: RerankRequest) -> str:
    return f"Question: {request.question}\n\nCandidates:\n{candidate_lines(request)}"


def _ranking_text(reply: str) -> str:
    """The part of ``reply`` that holds the ranked numbers: the ``ranking``
    value of a JSON object (fenced or not), else the whole reply."""
    start, end = reply.find("{"), reply.rfind("}")
    if 0 <= start < end:
        try:
            parsed = json.loads(reply[start:end + 1])
        except ValueError:
            return reply
        if isinstance(parsed, dict):
            return json.dumps(parsed.get("ranking"))
    return reply


def parse_ranking(reply: str, count: int, top: int) -> list[int]:
    """Zero-based candidate positions, best first: numbers outside 1..count
    are dropped, repeats keep their first place, at most ``top``."""
    numbers = (int(digits) for digits in _DIGITS.findall(_ranking_text(reply)))
    positions = dict.fromkeys(n - 1 for n in numbers if 1 <= n <= count)
    return list(positions)[:top]


async def rerank_llm(config_service: ConfigurationService, model: str) -> BaseChatModel:
    """The configured LLM named ``model``, else the default chat model."""
    if model:
        named = await get_llm_by_name(config_service, model, reasoning_effort=_REASONING_EFFORT)
        if named is not None:
            return named[0]
        logger.warning("record_rerank: no configured LLM is named %r; using the default chat model", model)
    llm, _ = await get_llm(config_service, reasoning_effort=_REASONING_EFFORT)
    return llm


@register_record_reranker("llm_listwise")
class LlmListwiseReranker(RecordReranker):
    def __init__(self, deps: RerankerDeps) -> None:
        self._config_service = deps.config_service
        self._llms: TTLCache[tuple[str, str], BaseChatModel] = TTLCache(maxsize=256, ttl=_LLM_TTL_S)

    async def _llm(self, org_id: str) -> BaseChatModel:
        model = (os.getenv(RECORD_RERANK_MODEL_ENV_VAR) or "").strip()
        llm = self._llms.get((org_id, model))
        if llm is None:
            if self._config_service is None:
                raise RecordRerankError("no configuration service to build the reranking LLM")
            llm = self._llms[(org_id, model)] = await rerank_llm(self._config_service, model)
        return llm

    @override
    async def rank(self, request: RerankRequest) -> list[str]:
        if not request.candidates:
            return []
        llm = await self._llm(request.org_id)
        response = await llm.ainvoke([
            SystemMessage(content=SYSTEM_PROMPT.format(top=request.top)),
            HumanMessage(content=user_prompt(request)),
        ])
        reply = coerce_message_content_to_text(getattr(response, "content", ""))
        positions = parse_ranking(reply, len(request.candidates), request.top)
        if not positions:
            raise RecordRerankError(f"no candidate numbers in the reply ({len(reply)} chars)")
        return [request.candidates[position].record_id for position in positions]
