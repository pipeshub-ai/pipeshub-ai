"""Fitting units to a size budget by relevance, hit by hit."""

from __future__ import annotations

from app.modules.retrieval.context.budget import fit_to_budget
from app.modules.retrieval.context.manifest import (
    ContentManifest,
    ManifestSource,
    RecordSpan,
    Segment,
)
from app.modules.retrieval.context.neighbours import NEIGHBOUR_KEY
from app.modules.retrieval.context.ranking import UNIT_RANK_KEY

# Every record costs this much framing (header, closing tag, separator).
_FRAMING = 10


def _unit(vrid: str, index: int, rank: int, *, neighbour: bool = False) -> dict:
    unit = {"virtual_record_id": vrid, "block_index": index, UNIT_RANK_KEY: rank}
    if neighbour:
        unit[NEIGHBOUR_KEY] = True
    return unit


def _manifest(units: list[dict], costs: list[int]) -> ContentManifest:
    """A manifest as rendering ``units`` in order would produce, with ``costs`` per unit."""
    segments, records, offset = [], [], 0
    current, record_start = None, 0
    for position, (unit, cost) in enumerate(zip(units, costs)):
        if unit["virtual_record_id"] != current:
            if current is not None:
                records.append(RecordSpan(current, f"id-{current}", record_start, offset))
            current, record_start = unit["virtual_record_id"], offset
            offset += _FRAMING - 1  # the separator is counted by the budget
        segments.append(Segment(offset, offset + cost, frozenset(), True, position))
        offset += cost
    records.append(RecordSpan(current, f"id-{current}", record_start, offset))
    return ContentManifest(ManifestSource.SEARCH, tuple(segments), tuple(records))


def _keys(units: list[dict]) -> list[tuple[str, int]]:
    return [(u["virtual_record_id"], u["block_index"]) for u in units]


def test_everything_that_fits_is_kept_unchanged() -> None:
    units = [_unit("a", 0, 0), _unit("a", 1, 1), _unit("b", 0, 2)]

    fit = fit_to_budget(units, _manifest(units, [100, 100, 100]), max_chars=10_000)

    assert fit.units == units
    assert fit.omitted_hits == 0


def test_a_lower_records_best_hit_beats_a_higher_records_weaker_one() -> None:
    """Record A reads first because its best hit ranks first, but its second
    hit ranks below record B's only hit; the record-level cut dropped B."""
    units = [_unit("a", 0, 0), _unit("a", 1, 2), _unit("b", 0, 1)]

    fit = fit_to_budget(units, _manifest(units, [100, 100, 100]), max_chars=2 * (100 + _FRAMING))

    assert _keys(fit.units) == [("a", 0), ("b", 0)]
    assert fit.omitted_hits == 1


def test_a_hit_that_does_not_fit_is_skipped_and_smaller_ones_still_fit() -> None:
    units = [_unit("a", 0, 0), _unit("b", 0, 1), _unit("c", 0, 2)]

    fit = fit_to_budget(units, _manifest(units, [100, 5_000, 100]), max_chars=300)

    assert _keys(fit.units) == [("a", 0), ("c", 0)]
    assert fit.omitted_hits == 1


def test_neighbours_go_with_their_hit() -> None:
    units = [
        _unit("a", 0, 0, neighbour=True), _unit("a", 1, 0), _unit("a", 2, 0, neighbour=True),
        _unit("b", 5, 1, neighbour=True), _unit("b", 6, 1),
    ]

    fit = fit_to_budget(units, _manifest(units, [50, 100, 50, 50, 100]), max_chars=10_000)
    assert len(fit.units) == 5

    tight = fit_to_budget(units, _manifest(units, [50, 100, 50, 50, 5_000]), max_chars=400)
    assert _keys(tight.units) == [("a", 0), ("a", 1), ("a", 2)]
    assert tight.omitted_hits == 1, "the neighbour that went with it is not a hit"


def test_a_hit_is_kept_without_its_neighbours_when_only_it_fits() -> None:
    units = [_unit("a", 0, 0), _unit("b", 4, 1, neighbour=True), _unit("b", 5, 1)]

    fit = fit_to_budget(units, _manifest(units, [100, 1_000, 100]), max_chars=250)

    assert _keys(fit.units) == [("a", 0), ("b", 5)]
    assert fit.omitted_hits == 0


def test_the_most_relevant_hit_is_kept_even_when_it_alone_is_too_big() -> None:
    units = [_unit("a", 0, 0), _unit("b", 0, 1)]

    fit = fit_to_budget(units, _manifest(units, [10_000, 10]), max_chars=100)

    assert _keys(fit.units) == [("a", 0)]


def test_record_framing_is_paid_once_per_record() -> None:
    """Two hits from one record cost one header; a hit from a new record pays its own."""
    units = [_unit("a", 0, 0), _unit("a", 1, 1), _unit("b", 0, 2)]
    budget = 3 * 100 + _FRAMING + _FRAMING - 1

    fit = fit_to_budget(units, _manifest(units, [100, 100, 100]), max_chars=budget)

    assert _keys(fit.units) == [("a", 0), ("a", 1)]


def test_without_ranks_list_position_is_the_rank() -> None:
    units = [{"virtual_record_id": v, "block_index": 0} for v in ("a", "b", "c")]

    fit = fit_to_budget(units, _manifest(units, [100, 100, 100]), max_chars=2 * (100 + _FRAMING))

    assert _keys(fit.units) == [("a", 0), ("b", 0)]
