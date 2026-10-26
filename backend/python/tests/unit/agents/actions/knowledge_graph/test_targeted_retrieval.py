"""TARGETED_RETRIEVAL through the real search, prefetch, fetch and prompt code.

Search finds with less (a smaller result, a few passages per record, nothing
shown twice), fetch reads the part a `query` names, and the prompt says when
to do that. With the switch off, every one of them is what it was.
Only retrieval, blob loading and graph enrichment are stood in for.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.actions.knowledge_graph.ops.fetch import execute_fetch_record
from app.agents.actions.knowledge_graph.ops.search import execute_search
from app.agents.agent_loop.hooks.citations import (
    CitationCollector,
    _FetchFullRecordTool,
)
from app.agents.chat_modes.prefetch import prefetch_retrieval
from app.models.blocks import BlockType, GroupType
from app.modules.agents.record_escalation.policy import policy_text

_SEARCH = "app.agents.actions.knowledge_graph.ops.search"
_BUILDER = "app.modules.retrieval.context.builder"


@pytest.fixture
def targeted(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TARGETED_RETRIEVAL", "true")
    for name in ("TARGETED_SEARCH_MAX_CHARS", "TARGETED_SEARCH_PASSAGES_PER_RECORD", "FOCUSED_READ_MAX_CHARS"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TARGETED_RETRIEVAL", "false")


@pytest.fixture
def unset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TARGETED_RETRIEVAL", raising=False)
    for name in ("TARGETED_SEARCH_MAX_CHARS", "TARGETED_SEARCH_PASSAGES_PER_RECORD", "FOCUSED_READ_MAX_CHARS"):
        monkeypatch.delenv(name, raising=False)


def _record(vrid: str, blocks: int = 60, chars: int = 900) -> dict[str, Any]:
    return {
        "id": f"rec-{vrid}",
        "_id": f"records/rec-{vrid}",
        "virtual_record_id": vrid,
        "record_name": f"Doc {vrid}",
        "context_metadata": f"Record ID: rec-{vrid}\nName: Doc {vrid}",
        "frontend_url": "https://app.example",
        "block_containers": {
            "blocks": [
                {"index": i, "type": BlockType.TEXT.value, "data": f"{vrid} para {i} " + "w" * chars}
                for i in range(blocks)
            ],
            "block_groups": [],
        },
    }


def _unit(vrid: str, index: int, score: float, chars: int = 900) -> dict[str, Any]:
    return {
        "virtual_record_id": vrid, "block_index": index, "block_type": BlockType.TEXT.value,
        "content": f"{vrid} para {index} " + "w" * chars, "metadata": {}, "score": score,
    }


def _thread_unit(vrid: str, replies: int, matched: int, chars: int = 600) -> dict[str, Any]:
    children = [
        {"virtual_record_id": vrid, "block_index": i, "block_type": BlockType.TEXT.value,
         "content": f"{vrid} reply {i} " + "r" * chars, "metadata": {}, "score": 0.95}
        for i in range(replies)
    ]
    return {
        "virtual_record_id": vrid, "block_index": 0, "block_group_index": 0,
        "block_type": GroupType.CONVERSATION.value, "content": ("", children),
        "hit_block_scores": {matched: 0.95}, "metadata": {}, "score": 0.95,
    }


def _state(**extra: Any) -> dict[str, Any]:  # noqa: ANN401
    retrieval = AsyncMock()
    retrieval.search_with_filters.return_value = {
        "status_code": 200, "searchResults": [{"virtual_record_id": "x"}], "virtual_to_record_map": {},
    }
    return {
        "logger": MagicMock(), "retrieval_service": retrieval, "graph_provider": AsyncMock(),
        "config_service": None, "org_id": "o1", "user_id": "u1",
        "filters": {"apps": ["app-1"], "kb": []}, **extra,
    }


async def _search(state: dict[str, Any], records: dict[str, Any], units: list[dict[str, Any]], query: str = "q") -> str:
    async def flatten(_results, _blob, _org, _mm, vr_map, *_a, **_kw) -> list[dict[str, Any]]:
        vr_map.update(records)
        return [dict(unit) for unit in units]

    with patch(f"{_BUILDER}.get_flattened_results", side_effect=flatten), \
            patch(f"{_BUILDER}.enrich_records_with_graph_context", new_callable=AsyncMock), \
            patch(f"{_SEARCH}.BlobStorage"):
        result = await execute_search(state, query)
    assert isinstance(result, str)
    return result


def _corpus(records: int = 8, hits_per_record: int = 6) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    recs = {f"v{r}": _record(f"v{r}", chars=1_500) for r in range(records)}
    units = [
        _unit(f"v{r}", 2 + 3 * h, 0.9 - r / 50 - h / 500, chars=1_500)
        for r in range(records) for h in range(hits_per_record)
    ]
    return recs, units


class TestOnByDefault:
    """A deployment that never set TARGETED_RETRIEVAL gets the feature."""

    @pytest.mark.asyncio
    async def test_unset_a_search_holds_half(self, unset: None) -> None:
        records, units = _corpus(hits_per_record=12)
        result = await _search(_state(), records, units)
        assert 28_000 < len(result) <= 32_000

    def test_unset_the_tool_takes_a_query(self, unset: None) -> None:
        context = SimpleNamespace(tool_state={})
        tool = _FetchFullRecordTool(CitationCollector(context), context)  # type: ignore[arg-type]
        assert [p.name for p in tool.parameters][-1] == "query"


class TestSearchFindsWithLess:
    @pytest.mark.asyncio
    async def test_off_a_search_still_fills_the_shared_cap(self, off: None) -> None:
        records, units = _corpus()
        result = await _search(_state(), records, units)
        assert 56_000 < len(result) <= 64_000
        assert "matching passages" not in result

    @pytest.mark.asyncio
    async def test_on_a_search_holds_half_and_no_record_takes_more_than_its_share(self, targeted: None) -> None:
        records, units = _corpus(hits_per_record=12)
        on_state = _state()
        after = await _search(on_state, records, units)

        assert 28_000 < len(after) <= 32_000
        per_record: dict[str, int] = {}
        for unit in on_state["final_results"]:
            if unit.get("score") is not None:
                per_record[unit["virtual_record_id"]] = per_record.get(unit["virtual_record_id"], 0) + 1
        assert max(per_record.values()) <= 8

    @pytest.mark.asyncio
    async def test_a_trimmed_record_is_named_with_how_to_read_the_rest(self, targeted: None) -> None:
        records = {"v0": _record("v0")}
        units = [_unit("v0", i, 0.9 - i / 100, chars=100) for i in range(12)]

        result = await _search(_state(), records, units)

        assert "Note: each record shows at most 8 matching passages. 1 record had more" in result
        assert "- Record ID: rec-v0 | Doc v0 | 4 more matching passages not shown" in result
        assert "v0 para 7 " in result and "v0 para 9 " not in result

    @pytest.mark.asyncio
    async def test_under_the_full_read_policy_a_trimmed_result_asks_for_no_query(self, targeted: None) -> None:
        records = {"v0": _record("v0")}
        units = [_unit("v0", i, 0.9 - i / 100, chars=100) for i in range(12)]

        result = await _search(_state(read_records_in_full=True), records, units)

        assert "4 more matching passages not shown" in result
        assert "with that record's ID to read it before answering" in result
        assert "`query`" not in result

    @pytest.mark.asyncio
    async def test_a_long_thread_keeps_the_reply_that_matched_and_its_neighbours(self, targeted: None) -> None:
        records = {"t0": _record("t0", blocks=80)}
        result = await _search(_state(), records, [_thread_unit("t0", 80, matched=57)])

        # The match, then the nearest replies either side (a tie goes to the earlier).
        for reply in range(53, 61):
            assert f"t0 reply {reply} " in result
        assert "t0 reply 10 " not in result
        assert "72 more matching passages not shown" in result

    @pytest.mark.asyncio
    async def test_a_second_search_does_not_resend_what_the_first_showed(self, targeted: None) -> None:
        records = {"v0": _record("v0"), "v1": _record("v1")}
        first_units = [_unit("v0", i, 0.9 - i / 100, chars=100) for i in range(3)]
        state = _state()
        first = await _search(state, records, first_units, query="first")
        assert "v0 para 0 " in first

        second_units = [*first_units, _unit("v0", 9, 0.5, chars=100), _unit("v1", 4, 0.4, chars=100)]
        second = await _search(state, records, second_units, query="second")

        assert "v0 para 9 " in second and "v1 para 4 " in second
        assert "v0 para 0 " in second  # room for the repeats here, so they stay ...
        state_full = _state()
        state_full["final_results"] = state["final_results"]
        with patch.dict("os.environ", {"TARGETED_SEARCH_PASSAGES_PER_RECORD": "1"}):
            third = await _search(
                state_full, records, [*second_units, _unit("v0", 20, 0.3, chars=100)], query="third",
            )
        # ... and with no room, new passages win and the result says what was skipped.
        assert "v0 para 20 " in third
        assert "v0 para 0 " not in third
        assert "you already received from an earlier search in this request" in third


    @pytest.mark.asyncio
    async def test_every_slice_of_a_thread_counts_as_shown_not_just_the_first(self, targeted: None) -> None:
        records = {"t0": _record("t0", blocks=40)}
        state = _state()
        with patch.dict("os.environ", {"TARGETED_SEARCH_PASSAGES_PER_RECORD": "2"}):
            first = await _search(state, records, [_thread_unit("t0", 40, matched=5)])
            second = await _search(state, records, [_thread_unit("t0", 40, matched=30)])
            third = await _search(state, records, [{
                **_thread_unit("t0", 40, matched=30), "hit_block_scores": {30: 0.95, 20: 0.9},
            }])
        assert "t0 reply 5 " in first
        assert "t0 reply 30 " in second
        # The second search's slice is known as shown, though the thread's
        # first slice already stood for it in final_results.
        assert "t0 reply 20 " in third and "t0 reply 30 " not in third
        assert "you already received from an earlier search in this request" in third


class TestPrefetch:
    @staticmethod
    async def _prefetch(records: dict[str, Any], units: list[dict[str, Any]], **kwargs: Any) -> Any:  # noqa: ANN401
        retrieval = AsyncMock()
        retrieval.search_with_filters.return_value = {
            "status_code": 200, "searchResults": [{"virtual_record_id": "x"}], "virtual_to_record_map": {},
        }

        async def flatten(_results, _blob, _org, _mm, vr_map, *_a, **_kw) -> list[dict[str, Any]]:
            vr_map.update(records)
            return [dict(unit) for unit in units]

        with patch(f"{_BUILDER}.get_flattened_results", side_effect=flatten), \
                patch(f"{_BUILDER}.enrich_virtual_record_id_to_result_with_fk_children", new_callable=AsyncMock), \
                patch(f"{_BUILDER}.enrich_records_with_graph_context", new_callable=AsyncMock), \
                patch(f"{_BUILDER}.enrich_records_with_address_links", new_callable=AsyncMock):
            return await prefetch_retrieval(
                query="q", org_id="o1", user_id="u1", retrieval_service=retrieval,
                graph_provider=object(), blob_store=object(), filters={}, limit=50,
                is_multimodal_llm=False, previous_conversations=None, logger=MagicMock(), **kwargs,
            )

    @pytest.mark.asyncio
    async def test_off_the_first_turn_context_is_unbounded(self, off: None) -> None:
        records, units = _corpus()
        prefetched = await self._prefetch(records, units)
        assert len(prefetched.formatted_context) > 64_000

    @pytest.mark.asyncio
    async def test_on_it_is_held_to_one_search_result(self, targeted: None) -> None:
        records, units = _corpus(hits_per_record=12)
        prefetched = await self._prefetch(records, units)
        assert len(prefetched.formatted_context) <= 32_000
        assert "matching passages not shown" in prefetched.formatted_context
        assert "`query`" in prefetched.formatted_context

    @pytest.mark.asyncio
    async def test_under_the_full_read_policy_it_asks_for_no_query(self, targeted: None) -> None:
        records, units = _corpus(hits_per_record=12)
        prefetched = await self._prefetch(records, units, read_in_full=True)
        assert "matching passages not shown" in prefetched.formatted_context
        assert "`query`" not in prefetched.formatted_context
        assert len(prefetched.formatted_context) <= 32_000

    @pytest.mark.asyncio
    async def test_a_turn_with_no_tools_is_not_asked_to_read_a_record(self, targeted: None) -> None:
        records, units = _corpus(hits_per_record=12)
        prefetched = await self._prefetch(records, units, can_fetch=False)
        assert "knowledgegraph__fetch_record" not in prefetched.formatted_context
        assert len(prefetched.formatted_context) <= 32_000


def _long_record(name: str, blocks: int = 200, chars: int = 600) -> dict[str, Any]:
    return {
        "id": f"rec-{name}",
        "virtual_record_id": f"vr-{name}",
        "record_name": f"Doc {name}",
        "frontend_url": "https://app.test",
        "context_metadata": f"Record ID: rec-{name}\nName: Doc {name}",
        "block_containers": {
            "blocks": [
                {"index": i, "type": BlockType.TEXT.value, "data": f"{name} para {i} " + "z" * chars}
                for i in range(blocks)
            ],
            "block_groups": [],
        },
    }


def _fetch_context(retrieval: Any = None, **overrides: Any) -> SimpleNamespace:  # noqa: ANN401
    graph = AsyncMock()
    graph.config_service = MagicMock()
    values: dict[str, Any] = {
        "model_name": "claude", "org_id": "org-1", "user_id": "user-1", "graph_provider": graph,
        "full_records_fetched": set(), "tool_state": {}, "is_multimodal_llm": False,
        "context_length": 200_000, "query": "what image tag did the release use",
        "retrieval_service": retrieval, "needs_whole_document": False, "include_retrieval_context": True,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _ranking(name: str, indices: list[int]) -> MagicMock:
    service = MagicMock()
    service.search_with_filters = AsyncMock(return_value={"searchResults": [
        {"metadata": {"virtualRecordId": f"vr-{name}", "blockIndex": i}, "score": 1.0 - n / 100}
        for n, i in enumerate(indices)
    ]})
    return service


async def _fetch(context: SimpleNamespace, records: list[dict[str, Any]], **kwargs: Any) -> str:  # noqa: ANN401
    structured = MagicMock()
    structured.coroutine = AsyncMock(return_value={"ok": True, "records": records, "not_available_ids": []})
    with patch("app.utils.fetch_full_record.create_fetch_full_record_tool", return_value=structured):
        output, _ = await execute_fetch_record(
            context=context, virtual_records={}, citation_ref_mapper=None,
            record_ids=[r["id"] for r in records], reason="need the tag", **kwargs,
        )
    assert output.success, output.error
    return str(output.data)


class TestFocusedRead:
    @pytest.mark.asyncio
    async def test_without_a_query_a_record_is_read_as_before(self, targeted: None) -> None:
        text = await _fetch(_fetch_context(), [_long_record("pr")])
        assert len(text) > 100_000
        assert "Focused read" not in text

    @pytest.mark.asyncio
    async def test_a_query_reads_the_part_it_names_within_the_focused_size(self, targeted: None) -> None:
        retrieval = _ranking("pr", [151])
        text = await _fetch(_fetch_context(retrieval), [_long_record("pr")], query="docker image tag")

        assert len(text) <= 16_000 + 1_500
        assert "pr para 151 " in text and "pr para 150 " in text
        assert "pr para 90 " not in text
        assert 'Focused read for "docker image tag"' in text
        # The ranking was asked the model's query, not the question.
        assert retrieval.search_with_filters.call_args.kwargs["queries"] == ["docker image tag"]

    @pytest.mark.asyncio
    async def test_a_record_that_fits_is_read_whole_with_a_query(self, targeted: None) -> None:
        text = await _fetch(_fetch_context(_ranking("s", [3])), [_long_record("s", blocks=8)], query="tag")
        assert all(f"s para {i} " in text for i in range(8))
        assert "Focused read" not in text

    @pytest.mark.asyncio
    async def test_no_record_of_a_focused_read_takes_more_than_its_size(self, targeted: None) -> None:
        records = [_long_record("big"), _long_record("small", blocks=3)]
        retrieval = MagicMock()
        retrieval.search_with_filters = AsyncMock(side_effect=lambda **kw: {"searchResults": [
            {"metadata": {"virtualRecordId": kw["virtual_record_ids_from_tool"][0], "blockIndex": 100}, "score": 1.0},
        ]})
        text = await _fetch(_fetch_context(retrieval), records, query="tag")

        big = text[: text.index("Record ID: rec-small")]  # records are read in the order named
        # The small record's unused room does not go to the big one.
        assert len(big) <= 16_000 + 600
        assert all(f"small para {i} " in text for i in range(3))

    @pytest.mark.asyncio
    async def test_a_query_is_ignored_while_the_switch_is_off(self, off: None) -> None:
        retrieval = _ranking("pr", [151])
        text = await _fetch(_fetch_context(retrieval), [_long_record("pr")], query="docker image tag")
        assert len(text) > 100_000
        retrieval.search_with_filters.assert_not_called()


    @pytest.mark.asyncio
    async def test_a_query_is_ignored_under_the_full_read_policy(self, targeted: None) -> None:
        retrieval = _ranking("pr", [151])
        context = _fetch_context(retrieval, tool_state={"read_records_in_full": True})
        text = await _fetch(context, [_long_record("pr")], query="docker image tag")
        assert len(text) > 100_000
        assert "Focused read" not in text


class TestToolAndPrompt:
    @staticmethod
    def _tool() -> _FetchFullRecordTool:
        context = SimpleNamespace(tool_state={})
        return _FetchFullRecordTool(CitationCollector(context), context)  # type: ignore[arg-type]

    def test_off_the_tool_reads_as_before(self, off: None) -> None:
        tool = self._tool()
        assert [p.name for p in tool.parameters] == ["record_ids", "reason", "start_block", "max_blocks"]
        assert "`query`" not in tool.description
        assert "Reading the right part" not in policy_text("knowledgegraph__fetch_record")

    def test_on_the_tool_takes_a_query_and_says_when_to_use_it(self, targeted: None) -> None:
        tool = self._tool()
        assert [p.name for p in tool.parameters][-1] == "query"
        assert "pass `query` naming it" in tool.description
        assert "16,000 characters per record" in tool.description

    def test_under_the_full_read_policy_the_tool_offers_no_query(self, targeted: None) -> None:
        context = SimpleNamespace(tool_state={"read_records_in_full": True})
        tool = _FetchFullRecordTool(CitationCollector(context), context)  # type: ignore[arg-type]
        assert "query" not in [p.name for p in tool.parameters]
        assert "`query`" not in tool.description

    @pytest.mark.asyncio
    async def test_off_a_query_argument_is_refused_as_before(self, off: None) -> None:
        output = await self._tool().execute(record_ids=["r1"], query="tag")
        assert not output.success
        assert "Unexpected argument(s): query" in (output.error or "")

    @pytest.mark.asyncio
    async def test_on_a_query_that_is_not_text_is_a_correctable_error(self, targeted: None) -> None:
        output = await self._tool().execute(record_ids=["r1"], query=["tag"])
        assert not output.success
        assert "query must be a short text" in (output.error or "")

    def test_the_policy_adds_focused_reads_only_without_the_full_read_rule(self) -> None:
        text = policy_text("knowledgegraph__fetch_record", focused_reads=True)
        assert "Reading the right part" in text
        assert "a `query` naming the detail" in text
        assert "Reading the right part" not in policy_text(
            "knowledgegraph__fetch_record", focused_reads=True, read_in_full=True,
        )
