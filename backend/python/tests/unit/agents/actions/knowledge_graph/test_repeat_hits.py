"""A record that keeps coming up in searches without being read gets a
pointer to fetch_record, at the block where its hits cluster."""

from __future__ import annotations

import random
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.actions.knowledge_graph.ops.fetch import FETCH_RECORD_GRANTED_KEY
from app.agents.actions.knowledge_graph.ops.repeat_hits import (
    _CLUSTER_SPAN_BLOCKS,
    READ_RECORD_IDS_KEY,
    REPEAT_HIT_SEARCHES,
    cluster_start,
    mark_read,
    observe_search,
    repeat_hit_note,
)
from app.agents.actions.knowledge_graph.ops.search import execute_search
from app.modules.retrieval.context.builder import KnowledgeContext
from app.modules.retrieval.context.neighbours import NEIGHBOUR_KEY
from app.modules.retrieval.context.renderer import RenderedKnowledge
from tests.unit.agents.actions.knowledge_graph.test_fetch import (
    _fetch_records,
    _text_record,
)

_SEARCH = "app.agents.actions.knowledge_graph.ops.search"
_RECORDS = {"vr1": {"id": "r1"}, "vr2": {"id": "r2"}}


def _hit(vrid: str, index: int, **extra: object) -> dict:
    return {"virtual_record_id": vrid, "block_index": index, **extra}


class TestClusterStart:
    def test_matches_a_brute_force_window_search(self) -> None:
        rng = random.Random(7)
        for _ in range(500):
            blocks = set(rng.sample(range(300), rng.randint(1, 25)))
            ordered = sorted(blocks)
            counts = [
                (sum(1 for b in ordered if first <= b < first + _CLUSTER_SPAN_BLOCKS), -first)
                for first in ordered
            ]
            best_count, neg_first = max(counts)
            assert cluster_start(blocks) == -neg_first, (sorted(blocks), best_count)

    def test_no_blocks_starts_at_zero(self) -> None:
        assert cluster_start([]) == 0


class TestNote:
    def _state(self, *, granted: bool = True) -> dict:
        return {FETCH_RECORD_GRANTED_KEY: True} if granted else {}

    def _search(self, state: dict, units: list[dict]) -> str:
        return repeat_hit_note(state, observe_search(state, units, _RECORDS))

    def test_appears_on_the_third_search_that_surfaces_an_unread_record(self) -> None:
        state = self._state()
        notes = [self._search(state, [_hit("vr1", 40 + i), _hit("vr1", 90)]) for i in range(REPEAT_HIT_SEARCHES)]

        assert notes[:-1] == ["", ""]
        assert "record r1 has come up in 3 searches" in notes[-1]
        assert 'knowledgegraph__fetch_record with record_ids=["r1"] and start_block=40' in notes[-1]

    def test_neighbours_do_not_count_as_hits(self) -> None:
        state = self._state()
        for _ in range(REPEAT_HIT_SEARCHES):
            note = self._search(state, [_hit("vr1", 3, **{NEIGHBOUR_KEY: True}), _hit("vr2", 1)])

        assert "r1" not in note
        assert "record r2" in note

    def test_a_record_already_read_is_not_named(self) -> None:
        state = self._state()
        mark_read(state, ["r1"])
        for _ in range(REPEAT_HIT_SEARCHES):
            note = self._search(state, [_hit("vr1", 5)])

        assert note == ""

    def test_the_tool_is_not_named_when_it_was_never_granted(self) -> None:
        state = self._state(granted=False)
        for _ in range(REPEAT_HIT_SEARCHES + 2):
            note = self._search(state, [_hit("vr1", 5)])

        assert note == ""

    def test_only_records_in_this_search_are_named(self) -> None:
        state = self._state()
        for _ in range(REPEAT_HIT_SEARCHES):
            self._search(state, [_hit("vr1", 5)])

        assert self._search(state, [_hit("vr2", 5)]) == ""


def _search_patches(units: list[dict]):
    builder = MagicMock()
    builder.return_value.build = AsyncMock(return_value=KnowledgeContext(
        units=units, virtual_record_id_to_result={"vr1": {"id": "r1"}},
    ))
    rendered = RenderedKnowledge(records=["Block content"], units=units, images=[], omitted_hits=0)
    return (
        patch(f"{_SEARCH}.KnowledgeContextBuilder", builder),
        patch(f"{_SEARCH}.render_knowledge", return_value=rendered),
        patch(f"{_SEARCH}.BlobStorage"),
        patch("app.agents.actions.knowledge_graph.ops.time_range.parse_time_range", return_value=({}, None)),
    )


def _state() -> dict:
    retrieval = AsyncMock()
    retrieval.search_with_filters.return_value = {
        "status_code": 200,
        "searchResults": [{"virtual_record_id": "vr1", "block_index": 0}],
        "virtual_to_record_map": {"vr1": {"id": "r1"}},
    }
    return {
        "logger": MagicMock(), "retrieval_service": retrieval, "graph_provider": AsyncMock(),
        "config_service": None, "org_id": "o1", "user_id": "u1",
        "filters": {"apps": ["app-1"], "kb": []}, "final_results": [],
        FETCH_RECORD_GRANTED_KEY: True,
    }


class TestInTheSearchResult:
    @pytest.mark.asyncio
    async def test_the_third_search_result_carries_the_note_near_the_top(self) -> None:
        state = _state()
        results = []
        for i in range(REPEAT_HIT_SEARCHES):
            builder, render, blob, time_range = _search_patches([_hit("vr1", 12 + i)])
            with builder, render, blob, time_range:
                results.append(await execute_search(state, f"rephrased query {i}"))

        assert "has come up in" not in results[0] + results[1]
        head = results[-1].split("Block content", 1)[0]
        assert "record r1 has come up in 3 searches" in head
        assert "start_block=12" in head

    @pytest.mark.asyncio
    async def test_a_fetch_that_reads_the_record_silences_it(self) -> None:
        state = _state()
        _text, _context = await _fetch_records([_text_record("r1", blocks=3)], tool_state=state)
        assert "r1" in state[READ_RECORD_IDS_KEY]

        for i in range(REPEAT_HIT_SEARCHES):
            builder, render, blob, time_range = _search_patches([_hit("vr1", i)])
            with builder, render, blob, time_range:
                result = await execute_search(state, f"query {i}")

        assert "has come up in" not in result
