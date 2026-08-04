"""Tests for ``app.modules.agents.record_escalation.coverage``."""
from __future__ import annotations

from app.modules.agents.record_escalation.coverage import (
    _count_blocks,
    _find_record,
    _is_countable_index,
    _is_fragment,
    analyze_coverage,
)


def _record(record_id: str, blocks: list | None = None) -> dict:
    d: dict = {"id": record_id}
    if blocks is not None:
        d["block_containers"] = {"blocks": blocks}
    return d


def _entry(vrid: str, block_index: int | None = None) -> dict:
    d: dict = {"virtual_record_id": vrid}
    if block_index is not None:
        d["block_index"] = block_index
    return d


class TestIsFragment:
    def test_fragment_block(self) -> None:
        assert _is_fragment({"parent_block_index": 0}) is True

    def test_non_fragment_block(self) -> None:
        assert _is_fragment({"type": "TEXT"}) is False

    def test_parent_block_index_none(self) -> None:
        assert _is_fragment({"parent_block_index": None}) is False

    def test_empty_block(self) -> None:
        assert _is_fragment({}) is False


class TestIsCountableIndex:
    def test_index_out_of_range_counts(self) -> None:
        record = _record("r1", blocks=[{"type": "TEXT"}])
        assert _is_countable_index(record, 99) is True

    def test_negative_index_counts(self) -> None:
        record = _record("r1", blocks=[{"type": "TEXT"}])
        assert _is_countable_index(record, -1) is True

    def test_normal_block_is_countable(self) -> None:
        record = _record("r1", blocks=[{"type": "TEXT"}, {"type": "TABLE"}])
        assert _is_countable_index(record, 0) is True
        assert _is_countable_index(record, 1) is True

    def test_fragment_block_is_not_countable(self) -> None:
        record = _record("r1", blocks=[{"parent_block_index": 0}])
        assert _is_countable_index(record, 0) is False

    def test_non_dict_block_counts(self) -> None:
        record = _record("r1", blocks=["not-a-dict"])
        assert _is_countable_index(record, 0) is True

    def test_no_block_containers(self) -> None:
        record = {"id": "r1"}
        assert _is_countable_index(record, 0) is True

    def test_empty_blocks_list(self) -> None:
        record = _record("r1", blocks=[])
        assert _is_countable_index(record, 0) is True


class TestCountBlocks:
    def test_no_block_containers(self) -> None:
        assert _count_blocks({"id": "r1"}) == 0

    def test_empty_blocks(self) -> None:
        assert _count_blocks(_record("r1", blocks=[])) == 0

    def test_normal_blocks(self) -> None:
        assert _count_blocks(_record("r1", blocks=[
            {"type": "TEXT"},
            {"type": "TABLE"},
            {"type": "IMAGE"},
        ])) == 3

    def test_fragments_excluded(self) -> None:
        assert _count_blocks(_record("r1", blocks=[
            {"type": "TEXT"},
            {"parent_block_index": 0},
            {"type": "TABLE"},
        ])) == 2

    def test_non_dict_blocks_excluded(self) -> None:
        assert _count_blocks(_record("r1", blocks=[
            {"type": "TEXT"},
            "not-a-dict",
            42,
        ])) == 1


class TestFindRecord:
    def test_found(self) -> None:
        vmap = {"vr1": {"id": "r1", "name": "A"}, "vr2": {"id": "r2", "name": "B"}}
        assert _find_record("r2", vmap) == {"id": "r2", "name": "B"}

    def test_not_found(self) -> None:
        assert _find_record("missing", {"vr1": {"id": "r1"}}) is None

    def test_none_values_skipped(self) -> None:
        assert _find_record("r1", {"vr1": None, "vr2": {"id": "r1"}}) == {"id": "r1"}

    def test_empty_map(self) -> None:
        assert _find_record("r1", {}) is None


class TestAnalyzeCoverage:
    def test_empty_inputs(self) -> None:
        assert analyze_coverage([], {}) == {}

    def test_entry_without_vrid_skipped(self) -> None:
        assert analyze_coverage([{"block_index": 0}], {"vr1": _record("r1", [{"type": "TEXT"}])}) == {}

    def test_entry_with_unmatched_vrid_skipped(self) -> None:
        assert analyze_coverage(
            [_entry("vr_missing", block_index=0)],
            {"vr1": _record("r1", [{"type": "TEXT"}])},
        ) == {}

    def test_entry_with_no_record_id_skipped(self) -> None:
        assert analyze_coverage(
            [_entry("vr1", block_index=0)],
            {"vr1": {"name": "no-id"}},
        ) == {}

    def test_summary_hit_no_block_index(self) -> None:
        result = analyze_coverage(
            [_entry("vr1")],
            {"vr1": _record("r1", [{"type": "TEXT"}, {"type": "TABLE"}])},
        )
        assert result == {"r1": (0, 2)}

    def test_single_block_hit(self) -> None:
        result = analyze_coverage(
            [_entry("vr1", block_index=0)],
            {"vr1": _record("r1", [{"type": "TEXT"}, {"type": "TABLE"}, {"type": "IMAGE"}])},
        )
        assert result == {"r1": (1, 3)}

    def test_multiple_distinct_blocks(self) -> None:
        result = analyze_coverage(
            [_entry("vr1", block_index=0), _entry("vr1", block_index=2)],
            {"vr1": _record("r1", [{"type": "TEXT"}, {"type": "TABLE"}, {"type": "IMAGE"}])},
        )
        assert result == {"r1": (2, 3)}

    def test_duplicate_block_index_deduped(self) -> None:
        result = analyze_coverage(
            [_entry("vr1", block_index=0), _entry("vr1", block_index=0)],
            {"vr1": _record("r1", [{"type": "TEXT"}, {"type": "TABLE"}])},
        )
        assert result == {"r1": (1, 2)}

    def test_fragment_block_not_counted(self) -> None:
        result = analyze_coverage(
            [_entry("vr1"), _entry("vr1", block_index=1)],
            {"vr1": _record("r1", [{"type": "TEXT"}, {"parent_block_index": 0}])},
        )
        assert result == {"r1": (0, 1)}

    def test_out_of_range_block_index_still_counted(self) -> None:
        result = analyze_coverage(
            [_entry("vr1", block_index=99)],
            {"vr1": _record("r1", [{"type": "TEXT"}])},
        )
        assert result == {"r1": (1, 1)}

    def test_non_int_block_index_ignored(self) -> None:
        result = analyze_coverage(
            [_entry("vr1"), {"virtual_record_id": "vr1", "block_index": "not-an-int"}],
            {"vr1": _record("r1", [{"type": "TEXT"}])},
        )
        assert result == {"r1": (0, 1)}

    def test_multiple_records(self) -> None:
        result = analyze_coverage(
            [_entry("vr1", block_index=0), _entry("vr2", block_index=1)],
            {
                "vr1": _record("r1", [{"type": "TEXT"}, {"type": "TABLE"}]),
                "vr2": _record("r2", [{"type": "A"}, {"type": "B"}, {"type": "C"}]),
            },
        )
        assert result["r1"] == (1, 2)
        assert result["r2"] == (1, 3)

    def test_record_with_no_blocks(self) -> None:
        result = analyze_coverage(
            [_entry("vr1", block_index=5)],
            {"vr1": _record("r1", blocks=[])},
        )
        assert result == {"r1": (1, 0)}
