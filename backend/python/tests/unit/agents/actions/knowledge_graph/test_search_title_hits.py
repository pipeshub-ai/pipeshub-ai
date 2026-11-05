"""Main search also finds records by name when their content is not indexed yet.

The content search is unchanged. A second lookup over the record titles (same
access context, scope and exclusions as the entity tools) adds the records
whose text search cannot read yet in a block of their own after the content,
each labelled "content not indexed yet". The lookup never fails the answer.
"""
from __future__ import annotations

import json
from contextlib import ExitStack
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.actions.knowledge_graph.ops import search as search_mod
from app.agents.actions.knowledge_graph.ops.search import (
    TITLE_LOOKUP_GAP_NOTE,
    execute_search,
)
from app.modules.retrieval.entity_permissions import (
    EntityAccessError,
    EntitySearchTimedOut,
)
from app.modules.retrieval.title_hits import UnindexedMatch

CONTENT_TEXT = "Enterprise pricing strategy 2026"
STORE = object()


def _match(record_id: str = "r2", name: str = "Quarterly planning", **extra: Any) -> UnindexedMatch:  # noqa: ANN401
    return UnindexedMatch(
        record_id=record_id, name=name, score=0.9, indexing_status="QUEUED", connector="Confluence", **extra,
    )


def _found() -> dict[str, Any]:
    return {
        "status_code": 200,
        "searchResults": [{"virtual_record_id": "vr1", "block_index": 0}],
        "virtual_to_record_map": {"vr1": {"id": "r1"}},
    }


def _empty() -> dict[str, Any]:
    return {"status_code": 200, "searchResults": [], "virtual_to_record_map": {}}


def _state(retrieval: AsyncMock, **extra: Any) -> dict[str, Any]:  # noqa: ANN401
    return {
        "logger": MagicMock(),
        "retrieval_service": retrieval,
        "graph_provider": AsyncMock(),
        "config_service": MagicMock(),
        "entity_vector_store": STORE,
        "org_id": "o1",
        "user_id": "u1",
        "filters": {"apps": ["wiki", "drive"], "kb": []},
        "final_results": [],
        **extra,
    }


def _flatten(_results: Any, _blob: Any, _org: Any, _mm: Any, vr_map: dict, *_a: Any, **_k: Any) -> list[dict]:  # noqa: ANN401
    vr_map.update({"vr1": {"id": "r1", "record_name": "Pricing"}})
    return [{"virtual_record_id": "vr1", "block_index": 0}]


class _Rendering:
    """The content pipeline stubbed to one block of text, as the other search tests do."""

    def __init__(self, titles: list[UnindexedMatch] | Exception | None, *, max_chars: int | None = None) -> None:
        self.titles = titles
        self.max_chars = max_chars
        self.stack = ExitStack()
        self.lookup = AsyncMock()
        self.build_candidates = MagicMock(return_value=SimpleNamespace(has_candidates=False))

    def __enter__(self) -> _Rendering:
        enter = self.stack.enter_context
        mod = "app.agents.actions.knowledge_graph.ops.search"
        enter(patch("app.agents.actions.knowledge_graph.ops.time_range.parse_time_range", return_value=({}, None)))
        enter(patch(f"{mod}.compose_result_tail", return_value="\n---\n"))
        enter(patch(f"{mod}.dedupe_append_final_results", side_effect=lambda old, new: old + new))
        enter(patch("app.modules.agents.record_escalation.render_coverage_note", return_value=""))
        enter(patch("app.modules.agents.record_escalation.render_candidate_table", return_value=""))
        enter(patch("app.modules.agents.record_escalation.build_candidates", self.build_candidates))
        enter(patch("app.modules.agents.record_escalation.analyze_coverage", return_value={}))
        enter(patch(
            "app.modules.retrieval.context.renderer.build_message_content_array",
            return_value=([[{"type": "text", "text": CONTENT_TEXT}]], MagicMock()),
        ))
        enter(patch("app.modules.retrieval.context.builder.enrich_records_with_graph_context", new_callable=AsyncMock))
        enter(patch("app.modules.retrieval.context.builder.get_flattened_results", side_effect=_flatten))
        enter(patch(f"{mod}.BlobStorage"))
        enter(patch(f"{mod}.get_record_id_shortener_if_enabled", return_value=None))
        enter(patch(f"{mod}.load_entity_access_context", AsyncMock(return_value=MagicMock())))
        if isinstance(self.titles, Exception):
            self.lookup.side_effect = self.titles
        else:
            self.lookup.return_value = self.titles or []
        enter(patch(f"{mod}.find_unindexed_matches", self.lookup))
        if self.max_chars is not None:
            enter(patch(f"{mod}.search_max_chars", return_value=self.max_chars))
        return self

    def __exit__(self, *exc: object) -> None:
        self.stack.close()


async def _search(retrieval_result: dict, titles: Any = None, *, state_extra: dict | None = None,  # noqa: ANN401
                  max_chars: int | None = None, **kwargs: Any) -> tuple[Any, dict, _Rendering]:  # noqa: ANN401
    retrieval = AsyncMock()
    retrieval.search_with_filters.return_value = retrieval_result
    state = _state(retrieval, **(state_extra or {}))
    with _Rendering(titles, max_chars=max_chars) as rendering:
        result = await execute_search(state, "quarterly planning", **kwargs)
    return result, state, rendering


class TestContentAndTitleHits:
    @pytest.mark.asyncio
    async def test_a_title_hit_for_a_record_the_content_shows_is_one_entry(self) -> None:
        result, _, _ = await _search(_found(), [_match("r1", "Pricing"), _match("r2")])
        assert CONTENT_TEXT in result
        assert "Record ID: r2" in result
        assert "Record ID: r1" not in result

    @pytest.mark.asyncio
    async def test_content_comes_first_and_is_unchanged(self) -> None:
        plain, _, _ = await _search(_found(), None)
        with_titles, _, _ = await _search(_found(), [_match()])
        assert with_titles.startswith(plain)
        block = with_titles[len(plain):]
        assert block.startswith("Records whose names match, but whose content is not indexed yet")
        assert "Quarterly planning | Confluence | status: QUEUED | Record ID: r2" in block
        assert "content is not indexed yet (status: QUEUED)" in block

    @pytest.mark.asyncio
    async def test_the_title_block_is_cut_before_the_content(self) -> None:
        plain, _, _ = await _search(_found(), None)
        matches = [_match(f"t{i}", f"Plan {i}") for i in range(5)]
        full, _, _ = await _search(_found(), matches)
        room = len(full) - len(plain) - 120

        cut, _, _ = await _search(_found(), matches, max_chars=len(plain) + room)
        none, _, _ = await _search(_found(), matches, max_chars=len(plain))

        assert cut.startswith(plain) and len(cut) <= len(plain) + room
        assert 0 < cut.count("Record ID: t") < 5
        assert none == plain

    @pytest.mark.asyncio
    async def test_the_shown_records_are_remembered_but_not_fed_to_the_fetch_plan(self) -> None:
        result, state, rendering = await _search(
            _found(), [_match("r2", virtual_record_id="vr-title")], state_extra={"include_retrieval_context": True},
        )
        assert "r2" in state["known_record_ids"]
        assert state["known_record_names"]["r2"] == "Quarterly planning"
        assert state["retrieved_by"]["vr-title"].layers == ["entity"]
        assert all(unit.get("virtual_record_id") == "vr1" for unit in state["final_results"])
        assert [r["id"] for r in rendering.build_candidates.call_args.kwargs["records_in_relevance_order"]] == ["r1"]
        assert "Record ID: r2" in result

    @pytest.mark.asyncio
    async def test_a_record_the_budget_left_out_is_not_remembered(self) -> None:
        plain, _, _ = await _search(_found(), None)
        _, state, _ = await _search(_found(), [_match()], max_chars=len(plain))
        assert "r2" not in (state.get("known_record_ids") or set())


class TestOnlyUnindexedRecordsMatch:
    @pytest.mark.asyncio
    async def test_it_is_not_an_empty_result(self) -> None:
        result, state, _ = await _search(_empty(), [_match()])
        assert not result.startswith("{")
        assert "No results found" not in result
        assert "1 record matches by name" in result
        assert "Quarterly planning | Confluence | status: QUEUED | Record ID: r2" in result
        assert "content is not indexed yet" in result
        assert "r2" in state["known_record_ids"]

    @pytest.mark.asyncio
    async def test_nothing_by_name_is_still_an_empty_result(self) -> None:
        result, _, _ = await _search(_empty(), [])
        assert json.loads(result)["message"] == "No results found"

    @pytest.mark.asyncio
    async def test_a_source_that_could_not_be_searched_is_still_said(self) -> None:
        retrieval = AsyncMock()
        retrieval.search_with_filters.return_value = {**_empty(), "searchIncomplete": "1 of 3 parts failed."}
        state = _state(retrieval)
        with _Rendering([_match()]):
            result = await execute_search(state, "quarterly planning")
        assert "1 of 3 parts failed." in result
        assert "Record ID: r2" in result

    @pytest.mark.asyncio
    async def test_the_header_fits_the_result_budget(self) -> None:
        result, _, _ = await _search(_empty(), [_match(f"t{i}") for i in range(10)], max_chars=600)
        assert len(result) <= 600


class TestTheLookupNeverFailsTheAnswer:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("error", [EntitySearchTimedOut("slow"), EntityAccessError("db"), TimeoutError(), RuntimeError("x")])
    async def test_content_is_returned_with_a_gap_note(self, error: Exception) -> None:
        result, _, _ = await _search(_found(), error)
        assert CONTENT_TEXT in result
        assert TITLE_LOOKUP_GAP_NOTE in result

    @pytest.mark.asyncio
    async def test_with_no_content_the_empty_answer_carries_the_note(self) -> None:
        result, _, _ = await _search(_empty(), EntityAccessError("db"))
        assert TITLE_LOOKUP_GAP_NOTE in json.loads(result)["message"]

    @pytest.mark.asyncio
    async def test_a_hung_lookup_is_stopped(self) -> None:
        async def hang(*_a: object, **_k: object) -> None:
            import asyncio

            await asyncio.sleep(60)

        retrieval = AsyncMock()
        retrieval.search_with_filters.return_value = _found()
        state = _state(retrieval)
        with _Rendering(None) as rendering, patch.object(search_mod, "_TITLE_LOOKUP_TIMEOUT_SECONDS", 0.05):
            rendering.lookup.side_effect = hang
            result = await execute_search(state, "quarterly planning")
        assert CONTENT_TEXT in result and TITLE_LOOKUP_GAP_NOTE in result


class TestWhatTheLookupIsGiven:
    @pytest.mark.asyncio
    async def test_it_uses_the_stores_graph_query_and_the_turns_access_context(self) -> None:
        _, state, rendering = await _search(_found(), [])
        args = rendering.lookup.await_args.args
        assert args[0] is STORE and args[1] is state["graph_provider"]
        assert args[3] == "quarterly planning"

    @pytest.mark.asyncio
    async def test_a_search_narrowed_to_sources_narrows_the_lookup_too(self) -> None:
        retrieval = AsyncMock()
        retrieval.search_with_filters.return_value = _found()
        state = _state(retrieval)
        with _Rendering([]) as rendering:
            load = search_mod.load_entity_access_context
            await execute_search(state, "quarterly planning", source_ids=["wiki"])
        load.assert_awaited_once_with(state, ["wiki"])
        assert rendering.lookup.await_count == 1

    @pytest.mark.asyncio
    async def test_an_unnarrowed_search_takes_the_turns_scope(self) -> None:
        retrieval = AsyncMock()
        retrieval.search_with_filters.return_value = _found()
        state = _state(retrieval)
        with _Rendering([]):
            load = search_mod.load_entity_access_context
            await execute_search(state, "quarterly planning")
        load.assert_awaited_once_with(state, None)

    @pytest.mark.asyncio
    async def test_no_lookup_without_an_entity_store(self) -> None:
        _, _, rendering = await _search(_found(), [_match()], state_extra={"entity_vector_store": None})
        rendering.lookup.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_lookup_for_a_search_scoped_to_entities(self) -> None:
        """Titles know no entity scope, so they would list records outside it."""
        result, _, rendering = await _search(_found(), [_match()], scope_virtual_record_ids=["vr1"])
        rendering.lookup.assert_not_called()
        assert "Record ID: r2" not in result
