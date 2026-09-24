"""Relevance ranking of renderable units."""

from __future__ import annotations

from app.modules.retrieval.context.units import Unit, unit_score


class RelevanceRanker:
    """Orders units most relevant first and keeps the best ``limit``.

    Ranks by retrieval score; ties, and units without a score, keep the order
    retrieval returned them in, so the ranking is deterministic.
    """

    def rank(self, units: list[Unit], limit: int | None = None) -> list[Unit]:
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
