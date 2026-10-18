"""TARGETED_RETRIEVAL's search side: the switch, its knobs, and which passages
of each record a search keeps."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.agent_loop_lib.hooks.middleware.builtin.budget_reduction import (
    DEFAULT_MAX_RESULT_CHARS,
)
from app.models.blocks import BlockType, GroupType
from app.modules.retrieval.context.targeted import (
    DEFAULT_FOCUSED_READ_CHARS,
    DEFAULT_SEARCH_MAX_CHARS,
    cap_passages_per_record,
    focused_read_chars,
    more_passages_list,
    more_passages_note,
    passages_per_record,
    search_max_chars,
    shown_block_keys,
    targeted_retrieval_enabled,
)
from app.modules.retrieval.context.units import NEIGHBOUR_OF_KEY

if TYPE_CHECKING:
    import pytest


def _leaf(vrid: str, index: int, score: float) -> dict[str, Any]:
    return {
        "virtual_record_id": vrid, "block_index": index, "block_type": BlockType.TEXT.value,
        "content": f"{vrid} passage {index}", "score": score,
    }


def _neighbour(vrid: str, index: int, anchor: int) -> dict[str, Any]:
    unit = _leaf(vrid, index, 0.0)
    del unit["score"]
    unit[NEIGHBOUR_OF_KEY] = [anchor]
    return unit


def _thread(vrid: str, replies: int, matched: dict[int, float], score: float = 0.9) -> dict[str, Any]:
    children = [
        {"virtual_record_id": vrid, "block_index": i, "block_type": BlockType.TEXT.value,
         "content": f"{vrid} reply {i}", "score": score}
        for i in range(replies)
    ]
    return {
        "virtual_record_id": vrid, "block_index": 0, "block_group_index": 0,
        "block_type": GroupType.CONVERSATION.value, "content": ("", children),
        "hit_block_scores": dict(matched), "score": score,
    }


def _children(unit: dict[str, Any]) -> list[int]:
    return [child["block_index"] for child in unit["content"][1]]


class TestSwitchAndKnobs:
    def test_off_unless_set_true_and_not_part_of_high_accuracy_mode(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("TARGETED_RETRIEVAL", raising=False)
        monkeypatch.setenv("HIGH_ACCURACY_MODE", "true")
        assert targeted_retrieval_enabled() is False
        monkeypatch.setenv("TARGETED_RETRIEVAL", "true")
        assert targeted_retrieval_enabled() is True
        monkeypatch.setenv("TARGETED_RETRIEVAL", "yes")
        assert targeted_retrieval_enabled() is False

    def test_defaults(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for name in ("TARGETED_SEARCH_MAX_CHARS", "TARGETED_SEARCH_PASSAGES_PER_RECORD", "FOCUSED_READ_MAX_CHARS"):
            monkeypatch.delenv(name, raising=False)
        assert search_max_chars() == DEFAULT_SEARCH_MAX_CHARS == 32_000
        # The search API's own per-record count: 8 at the assistant's limit of 50.
        assert passages_per_record(50) == 8
        assert passages_per_record(None) == 8
        assert passages_per_record(10) == 4
        assert focused_read_chars() == DEFAULT_FOCUSED_READ_CHARS == 16_000

    def test_knobs_are_clamped_and_a_malformed_value_keeps_the_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TARGETED_SEARCH_MAX_CHARS", "999999")
        monkeypatch.setenv("TARGETED_SEARCH_PASSAGES_PER_RECORD", "0")
        monkeypatch.setenv("FOCUSED_READ_MAX_CHARS", "ten")
        assert search_max_chars() == DEFAULT_MAX_RESULT_CHARS
        assert passages_per_record(50) == 1
        assert focused_read_chars() == DEFAULT_FOCUSED_READ_CHARS


class TestCapPassagesPerRecord:
    def test_a_record_keeps_its_best_passages_and_the_rest_are_counted(self) -> None:
        ranked = [_leaf("a", i, 0.9 - i / 100) for i in range(6)] + [_leaf("b", 0, 0.5)]

        capped = cap_passages_per_record(ranked, 2)

        assert [(u["virtual_record_id"], u["block_index"]) for u in capped.units] == [("a", 0), ("a", 1), ("b", 0)]
        assert capped.omitted == {"a": 4}
        assert capped.repeats_dropped == 0

    def test_neighbours_stay_only_beside_a_passage_that_stayed(self) -> None:
        ranked = [_leaf("a", 10, 0.9), _leaf("a", 20, 0.8), _neighbour("a", 11, 10), _neighbour("a", 21, 20)]

        capped = cap_passages_per_record(ranked, 1)

        assert [u["block_index"] for u in capped.units] == [10, 11]

    def test_a_long_section_keeps_its_matched_passages_then_the_nearest(self) -> None:
        thread = _thread("t", 40, {30: 0.7, 5: 0.9})
        original_children = _children(thread)

        capped = cap_passages_per_record([thread], 4)

        (unit,) = capped.units
        # Both matches, then the two passages nearest a match (ties go to the earlier),
        # in document order.
        assert _children(unit) == [4, 5, 6, 30]
        assert capped.omitted == {"t": 36}
        assert _children(thread) == original_children  # the input is not changed

    def test_the_best_match_wins_when_only_one_passage_fits(self) -> None:
        capped = cap_passages_per_record([_thread("t", 10, {2: 0.4, 8: 0.95})], 1)
        assert _children(capped.units[0]) == [8]

    def test_a_section_that_fits_is_passed_through_unchanged(self) -> None:
        thread = _thread("t", 3, {1: 0.9})
        capped = cap_passages_per_record([thread], 4)
        assert capped.units == [thread]
        assert capped.omitted == {}

    def test_every_record_keeps_at_least_one_passage(self) -> None:
        ranked = [_thread("t", 5, {0: 0.9}), _thread("t2", 5, {4: 0.8})]
        capped = cap_passages_per_record(ranked, 1)
        assert [u["virtual_record_id"] for u in capped.units] == ["t", "t2"]

    def test_passages_already_shown_give_way_to_new_ones(self) -> None:
        ranked = [_leaf("a", 0, 0.9), _leaf("a", 1, 0.8), _leaf("a", 2, 0.7)]

        capped = cap_passages_per_record(ranked, 2, already_shown={("a", 0)})

        assert [u["block_index"] for u in capped.units] == [1, 2]
        assert capped.repeats_dropped == 1
        assert capped.omitted == {}

    def test_a_record_with_nothing_new_moves_behind_the_others(self) -> None:
        ranked = [_leaf("old", 0, 0.95), _leaf("new", 3, 0.5)]

        capped = cap_passages_per_record(ranked, 2, already_shown={("old", 0)})

        assert [u["virtual_record_id"] for u in capped.units] == ["new", "old"]

    def test_inside_a_section_shown_replies_go_last(self) -> None:
        thread = _thread("t", 6, {2: 0.9})
        capped = cap_passages_per_record([thread], 2, already_shown={("t", 2), ("t", 1)})
        # The match and reply 1 were shown already, so the new replies nearest
        # the match (3, then 0 before 4 on a tie) take the room.
        assert _children(capped.units[0]) == [0, 3]
        assert capped.repeats_dropped == 2

    def test_a_trimmed_section_counts_only_the_replies_it_still_holds(self) -> None:
        capped = cap_passages_per_record([_thread("t", 40, {20: 0.9})], 2)
        # The section's own block_index (0, its first reply) was cut and is not shown.
        assert shown_block_keys(capped.units) == {("t", 19), ("t", 20)}

    def test_shown_block_keys_reads_every_block_a_unit_renders(self) -> None:
        keys = shown_block_keys([_leaf("a", 3, 0.9), _thread("t", 2, {}), "junk", {"block_index": 1}])
        assert keys == {("a", 3), ("t", 0), ("t", 1)}


class TestNotes:
    def test_the_top_note_names_no_record_and_says_what_to_do(self) -> None:
        note = more_passages_note(4, 2, 3)
        assert "at most 4 matching passages" in note
        assert "2 records had more" in note
        assert "knowledgegraph__fetch_record" in note and "`query`" in note
        assert "3 passages you already received" in note
        assert more_passages_note(4, 0, 0) == ""

    def test_the_list_names_only_shown_records_by_record_id(self) -> None:
        records = {"va": {"id": "rec-a", "record_name": "PR 12"}}
        text = more_passages_list({"va": 5, "vb": 2}, records)
        assert "- Record ID: rec-a | PR 12 | 5 more matching passages not shown" in text
        assert "vb" not in text
        assert more_passages_list({"vb": 2}, records) == ""
