"""The order the model reads units in."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.modules.retrieval.context.units import Unit


RELEVANCE_RANK_KEY = "relevance_rank"


def order_for_reading(units: list[Unit]) -> list[Unit]:
    """Records most relevant first; each record's blocks in document order.

    ``units`` must already be in relevance order: a record ranks where its
    first unit appears. Every unit is stamped with its record's 1-based rank
    under ``relevance_rank``.
    """
    record_rank: dict[str, int] = {}
    for unit in units:
        vrid = unit.get("virtual_record_id") or ""
        record_rank.setdefault(vrid, len(record_rank) + 1)
    for unit in units:
        unit[RELEVANCE_RANK_KEY] = record_rank[unit.get("virtual_record_id") or ""]
    return sorted(units, key=_reading_key)


def _reading_key(unit: Unit) -> tuple[int, int]:
    block_index = unit.get("block_index")
    # A record summary has no position in the document; it leads its record.
    return unit[RELEVANCE_RANK_KEY], -1 if block_index is None else block_index
