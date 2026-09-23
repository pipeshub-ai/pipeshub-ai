"""Fitting rendered units to a size budget by relevance, block by block.

Dropping whole records lets one long record crowd out another record's best
block, and stopping at the first record that does not fit drops every
smaller record after it. Here each hit, with the neighbours rendered around
it, is kept or dropped on its own, least relevant first.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.modules.retrieval.context.neighbours import NEIGHBOUR_KEY
from app.modules.retrieval.context.ranking import UNIT_RANK_KEY

if TYPE_CHECKING:
    from app.modules.retrieval.context.manifest import ContentManifest
    from app.modules.retrieval.context.units import Unit

# What joins one rendered record to the next.
_RECORD_SEPARATOR_CHARS = 1


@dataclass(frozen=True)
class BudgetFit:
    units: list[Unit]
    """The units kept, in the order they were given."""
    omitted_hits: int
    """Hits left out, not counting the neighbours that went with them."""


def fit_to_budget(units: list[Unit], manifest: ContentManifest, max_chars: int) -> BudgetFit:
    """Keep the most relevant hits whose rendered text fits in ``max_chars``.

    ``manifest`` is from rendering exactly ``units``; it gives what each unit
    and each record's framing cost. A hit and its neighbours form a bundle,
    taken in relevance order (``UNIT_RANK_KEY``, else list position). A
    bundle that does not fit is tried as the hit alone, then skipped, and the
    walk goes on so smaller bundles further down can still fit. The most
    relevant bundle is always kept.
    """
    unit_cost = {segment.unit_position: segment.end - segment.start for segment in manifest.segments}
    framing_cost = _framing_costs(units, manifest, unit_cost)

    bundles: dict[int, list[int]] = defaultdict(list)
    for position, unit in enumerate(units):
        bundles[_rank(unit, position)].append(position)

    kept: set[int] = set()
    open_records: set[str] = set()
    used = 0
    omitted = 0
    for rank in sorted(bundles):
        members = bundles[rank]
        hits = [p for p in members if not units[p].get(NEIGHBOUR_KEY)] or members[:1]
        for candidate in (members, hits):
            cost = _cost(candidate, units, unit_cost, framing_cost, open_records)
            if not kept or used + cost <= max_chars:
                kept.update(candidate)
                open_records.update(_vrid(units[p]) for p in candidate)
                used += cost
                break
        else:
            omitted += len(hits)
    return BudgetFit(
        units=[unit for position, unit in enumerate(units) if position in kept],
        omitted_hits=omitted,
    )


def _rank(unit: Unit, position: int) -> int:
    rank = unit.get(UNIT_RANK_KEY)
    return rank if isinstance(rank, int) and not isinstance(rank, bool) else position


def _vrid(unit: Unit) -> str:
    return str(unit.get("virtual_record_id") or "")


def _framing_costs(
    units: list[Unit], manifest: ContentManifest, unit_cost: dict[int, int],
) -> dict[str, int]:
    """Per record: its header, closing tag and separator, beyond its units."""
    units_in_record: dict[str, int] = defaultdict(int)
    for position, cost in unit_cost.items():
        units_in_record[_vrid(units[position])] += cost
    return {
        record.virtual_record_id: (
            record.end - record.start - units_in_record[record.virtual_record_id]
            + _RECORD_SEPARATOR_CHARS
        )
        for record in manifest.records
    }


def _cost(
    positions: list[int],
    units: list[Unit],
    unit_cost: dict[int, int],
    framing_cost: dict[str, int],
    open_records: set[str],
) -> int:
    new_records = {_vrid(units[p]) for p in positions} - open_records
    return (
        sum(unit_cost.get(p, 0) for p in positions)
        + sum(framing_cost.get(vrid, 0) for vrid in new_records)
    )
