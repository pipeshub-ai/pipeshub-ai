"""Ranking units with a reranker model, falling back to retrieval order."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from app.models.blocks import BlockType
from app.modules.retrieval.context.ranking import (
    RERANK_SCORE_KEY,
    RelevanceRanker,
    UnitRanker,
)
from app.modules.retrieval.context.units import unit_children
from app.utils.chat_helpers import is_base64_image

if TYPE_CHECKING:
    from app.modules.reranker.interface import IReranker
    from app.modules.retrieval.context.units import Unit

logger = logging.getLogger(__name__)

# Candidates sent to the reranker: the best by retrieval score. Cross-encoder
# cost is linear in this, and past a few dozen the extra recall is small.
RERANK_MAX_DOCUMENTS = 64
# Units kept after reranking, unless the caller asks for fewer.
RERANK_TOP_N = 15
# One rerank call must not hold up search; past this, retrieval order is used.
RERANK_TIMEOUT_SECONDS = 5.0
# About RERANKER_MAX_INPUT_TOKENS of text; hosted APIs bill for what is sent.
RERANK_MAX_DOCUMENT_CHARS = 4_000


class RerankingRanker(UnitRanker):
    """Reorders the best retrieval candidates by reranker score alone.

    Any failure, including a timeout, returns what ``fallback`` would have,
    so a broken reranker never costs more than the latency budget.
    """

    def __init__(self, reranker: IReranker, fallback: UnitRanker | None = None) -> None:
        self._reranker = reranker
        self._fallback = fallback or RelevanceRanker()

    async def rank(
        self,
        units: list[Unit],
        *,
        query: str,
        records: dict[str, Any],
        limit: int | None = None,
    ) -> list[Unit]:
        candidates = await self._fallback.rank(
            units, query=query, records=records, limit=RERANK_MAX_DOCUMENTS,
        )
        if not candidates or not query.strip():
            return await self._fallback.rank(units, query=query, records=records, limit=limit)

        keep = RERANK_TOP_N if limit is None else min(limit, RERANK_TOP_N)
        documents = [ranking_text(unit, records) for unit in candidates]
        try:
            hits = await asyncio.wait_for(
                self._reranker.rerank(query, documents, top_n=keep),
                timeout=RERANK_TIMEOUT_SECONDS,
            )
        except Exception as exc:  # fail open to retrieval order
            logger.warning(
                "Reranking %d units with %s failed, keeping retrieval order: %r",
                len(documents), self._reranker.model_name, exc,
            )
            return await self._fallback.rank(units, query=query, records=records, limit=limit)

        ranked: list[Unit] = []
        seen: set[int] = set()
        for hit in hits:
            if hit.index in seen or not 0 <= hit.index < len(candidates):
                continue
            seen.add(hit.index)
            unit = candidates[hit.index]
            unit[RERANK_SCORE_KEY] = hit.score
            ranked.append(unit)
        if not ranked:
            logger.warning("%s returned no hits, keeping retrieval order", self._reranker.model_name)
            return await self._fallback.rank(units, query=query, records=records, limit=limit)
        return ranked[:keep]


def ranker_for(reranker: IReranker | None) -> UnitRanker:
    return RerankingRanker(reranker) if reranker is not None else RelevanceRanker()


def ranking_text(unit: Unit, records: dict[str, Any]) -> str:
    """What the reranker reads for a unit: its record's name, then its text.

    The name matters because a passage often never says what it is about.
    Images contribute their description, never their bytes.
    """
    record = records.get(unit.get("virtual_record_id") or "") or {}
    parts = [
        record.get("record_name"),
        unit.get("qualified_name"),
        *_texts(unit),
    ]
    text = "\n".join(str(part).strip() for part in parts if isinstance(part, str) and part.strip())
    return text[:RERANK_MAX_DOCUMENT_CHARS]


def _texts(unit: Unit) -> list[str]:
    content = unit.get("content")
    if isinstance(content, str):
        return [] if _is_image_bytes(unit, content) else [content]
    if isinstance(content, tuple) and content and isinstance(content[0], str):
        texts = [content[0]]
        for child in unit_children(unit):
            texts.extend(_texts(child))
        return texts
    return []


def _is_image_bytes(unit: Unit, content: str) -> bool:
    """An image unit holds either its description or the image itself."""
    return unit.get("block_type") == BlockType.IMAGE.value and (
        content.startswith("data:") or is_base64_image(content)
    )
