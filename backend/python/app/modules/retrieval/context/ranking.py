"""Relevance ranking of renderable units."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from app.modules.retrieval.context.units import Unit, unit_score

# Set on a unit a reranker scored; telemetry reports it.
RERANK_SCORE_KEY = "rerank_score"


class UnitRanker(ABC):
    """Orders units most relevant to ``query`` first and keeps the best ``limit``."""

    @abstractmethod
    async def rank(
        self,
        units: list[Unit],
        *,
        query: str,
        records: dict[str, Any],
        limit: int | None = None,
    ) -> list[Unit]: ...


class RelevanceRanker(UnitRanker):
    """Ranks by retrieval score.

    Ties, and units without a score, keep the order retrieval returned them
    in, so the ranking is deterministic.
    """

    async def rank(
        self,
        units: list[Unit],
        *,
        query: str = "",
        records: dict[str, Any] | None = None,
        limit: int | None = None,
    ) -> list[Unit]:
        ranked = [
            unit
            for _, unit in sorted(
                enumerate(units),
                key=lambda item: (_sort_score(item[1]), item[0]),
            )
        ]
        return ranked if limit is None else ranked[: max(limit, 0)]


def _sort_score(unit: Unit) -> float:
    score = unit_score(unit)
    return float("inf") if score is None else -score
