"""The order the model reads the chosen units in."""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.utils.chat_helpers import flattened_result_sort_key
from app.utils.env_utils import accuracy_feature_enabled

if TYPE_CHECKING:
    from app.modules.retrieval.context.units import Unit

# Follows HIGH_ACCURACY_MODE when unset. Showing the most relevant record first
# changes what the model reads first, and is not yet measured, so by default
# records keep the order they always had.
RELEVANCE_ORDERED_CONTEXT_ENV_VAR = "RELEVANCE_ORDERED_CONTEXT"


def relevance_ordered_context() -> bool:
    return accuracy_feature_enabled(RELEVANCE_ORDERED_CONTEXT_ENV_VAR)


def order_for_reading(ranked: list[Unit], *, by_relevance: bool) -> list[Unit]:
    """``ranked`` (most relevant first) in the order the model reads it.

    Each record's blocks are in document order, a record summary first. With
    ``by_relevance`` a record comes where its best unit ranked; without it,
    records are ordered by virtual record id, as they always were.
    """
    if not by_relevance:
        return sorted(ranked, key=flattened_result_sort_key)
    record_rank: dict[str, int] = {}
    for unit in ranked:
        record_rank.setdefault(unit.get("virtual_record_id") or "", len(record_rank))
    return sorted(
        ranked,
        key=lambda unit: (
            record_rank[unit.get("virtual_record_id") or ""],
            flattened_result_sort_key(unit)[1],
        ),
    )
