"""Relevance ranking of renderable units."""

from __future__ import annotations

from app.modules.retrieval.context.units import Unit, unit_score


def rank_units(units: list[Unit], limit: int | None = None) -> list[Unit]:
    """``units`` most relevant first, cut to the best ``limit``.

    Ranks by retrieval score. Ties, and units without a score, keep the order
    retrieval returned them in, so equal inputs always rank the same way.
    """
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
