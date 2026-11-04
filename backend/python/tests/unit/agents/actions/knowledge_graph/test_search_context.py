"""What ``execute_search`` shows the model, and what it records as shown.

These run the real context pipeline and renderer; only retrieval, blob
loading and graph enrichment are stood in for.
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agent_loop_lib.core.messages import ImagePart, TextPart
from app.agents.actions.knowledge_graph.ops.search import execute_search
from app.agents.agent_loop.retrieval_ledger import RetrievalContextLedger
from app.agents.chat_modes.prefetch import prefetch_retrieval
from app.models.blocks import BlockType
from app.modules.retrieval.context.ordering import RELEVANCE_ORDERED_CONTEXT_ENV_VAR
from app.utils.chat_helpers import CitationRefMapper, ImageBudget
from app.utils.image_admission import ImageAdmission, admission_from_state
from app.utils.image_policy import permissive_policy

_SEARCH = "app.agents.actions.knowledge_graph.ops.search"
_BUILDER = "app.modules.retrieval.context.builder"
_PNG = (
    "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
    "+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


@pytest.fixture(autouse=True)
def _targeted_retrieval_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """These pin the fit to the shared tool-result cap, which TARGETED_RETRIEVAL
    replaces with its own; that path is covered in test_targeted_retrieval.py."""
    monkeypatch.setenv("TARGETED_RETRIEVAL", "false")


def _record(vrid: str, blocks: int = 4) -> dict[str, Any]:
    return {
        "id": f"rec-{vrid}",
        "_id": f"records/rec-{vrid}",
        "virtual_record_id": vrid,
        "record_name": f"Doc {vrid}",
        "context_metadata": f"Record ID: rec-{vrid}\nName: Doc {vrid}",
        "frontend_url": "https://app.example",
        "block_containers": {
            "blocks": [{"type": BlockType.TEXT.value, "data": f"{vrid} block {i}"} for i in range(blocks)],
            "block_groups": [],
        },
    }


def _unit(vrid: str, index: int, text: str, score: float) -> dict[str, Any]:
    return {
        "virtual_record_id": vrid,
        "block_index": index,
        "block_type": BlockType.TEXT.value,
        "content": text,
        "metadata": {},
        "score": score,
    }


def _state(**extra: Any) -> dict[str, Any]:  # noqa: ANN401
    retrieval = AsyncMock()
    retrieval.search_with_filters.return_value = {
        "status_code": 200,
        "searchResults": [{"virtual_record_id": vrid} for vrid in ("v0", "v1", "v2", "v3", "v4", "a", "b")],
        "virtual_to_record_map": {},
    }
    return {
        "logger": MagicMock(),
        "retrieval_service": retrieval,
        "graph_provider": AsyncMock(),
        "config_service": None,
        "org_id": "o1",
        "user_id": "u1",
        "filters": {"apps": ["app-1"], "kb": []},
        **extra,
    }


async def _search(
    state: dict[str, Any], records: dict[str, Any], units: list[dict[str, Any]], **patches: Any,  # noqa: ANN401
) -> str | list[Any]:
    async def flatten(_results, _blob, _org, _mm, vr_map, *_a, **_kw) -> list[dict[str, Any]]:
        vr_map.update(records)
        return [dict(unit) for unit in units]

    with patch(f"{_BUILDER}.get_flattened_results", side_effect=flatten), \
            patch(f"{_BUILDER}.enrich_records_with_graph_context", new_callable=AsyncMock), \
            patch(f"{_SEARCH}.BlobStorage"):
        if "cap" in patches:
            with patch(f"{_SEARCH}.DEFAULT_MAX_RESULT_CHARS", patches["cap"]):
                return await execute_search(state, "annual fee")
        return await execute_search(state, "annual fee")


def _five_records() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    records = {f"v{i}": _record(f"v{i}") for i in range(5)}
    units = [_unit(f"v{i}", 1, f"v{i} " + "x" * 900, 0.9 - i / 10) for i in range(5)]
    return records, units


class TestFitsTheToolResultCap:
    @pytest.mark.asyncio
    async def test_lowest_ranked_records_go_whole_and_the_result_says_so(self) -> None:
        records, units = _five_records()
        state = _state()

        result = await _search(state, records, units, cap=3_000)

        assert isinstance(result, str)
        assert len(result) <= 3_000
        assert "lower-ranked records left out to fit the result size" in result
        assert "v0 xxx" in result
        assert "v4 xxx" not in result

    @pytest.mark.asyncio
    async def test_state_and_trace_hold_exactly_what_was_shown(self) -> None:
        records, units = _five_records()
        state = _state(include_retrieval_context=True)

        result = await _search(state, records, units, cap=3_000)

        shown = {vrid for vrid in records if f"{vrid} xxx" in result}
        assert shown and shown != set(records)
        assert {u["virtual_record_id"] for u in state["final_results"]} == shown
        assert set(state["virtual_record_id_to_result"]) == shown
        assert {r["virtual_record_id"] for r in state["tool_records"]} == shown
        assert set(state["retrieved_by"]) == shown
        assert state["known_record_ids"] == {f"rec-{vrid}" for vrid in shown}
        delta = RetrievalContextLedger().take_delta(state)
        assert {r.virtualRecordId for r in delta.records} == shown
        assert sorted(delta.known_record_ids) == sorted(f"rec-{vrid}" for vrid in shown)
        # One citation ref per block shown; none for the records left out.
        assert len(state["citation_ref_mapper"].ref_to_url) == len(state["final_results"])

    @pytest.mark.asyncio
    async def test_a_long_keyword_hint_is_sized_in_before_the_records(self) -> None:
        records, units = _five_records()
        state = _state(config_service=MagicMock())
        hint = "\n\nAdditional records found via pattern matching:\n" + "h" * 1_200

        async def merge(**kwargs: Any) -> list[dict[str, Any]]:  # noqa: ANN401
            kwargs["virtual_record_id_to_result"]["g1"] = {"_key": "rec-g1", "recordName": "Grep doc"}
            return [{"virtual_record_id": "g1", "source": "pattern_match"}]

        with patch(f"{_SEARCH}.run_pattern_match_with_llm_grep", AsyncMock(return_value=[{"virtual_record_id": "g1"}])), \
                patch(f"{_SEARCH}.merge_pattern_match_results", side_effect=merge), \
                patch(f"{_SEARCH}.render_pattern_match_hint", return_value=hint):
            result = await _search(state, records, units, cap=3_000)

        assert len(result) <= 3_000
        assert result.endswith(hint)
        assert "g1" in state["virtual_record_id_to_result"]

    @pytest.mark.asyncio
    async def test_no_record_is_shown_by_its_neighbours_alone(self) -> None:
        records = {f"v{i}": _record(f"v{i}", blocks=6) for i in range(10)}
        units = [_unit(f"v{i}", 1, f"v{i} " + "x" * 1_200, 0.9 - i / 100) for i in range(10)]
        state = _state()

        result = await _search(state, records, units, cap=6_000)

        shown = {u["virtual_record_id"] for u in state["final_results"]}
        with_hit = {u["virtual_record_id"] for u in state["final_results"] if u.get("score") is not None}
        assert shown == with_hit
        assert all(f"{vrid} xxx" in result for vrid in shown)
        assert set(state["virtual_record_id_to_result"]) == shown

    @pytest.mark.asyncio
    async def test_everything_that_fits_is_shown_unchanged(self) -> None:
        records, units = _five_records()
        state = _state()

        result = await _search(state, records, units)

        # Five hits, and the text either side of each in the slots they left.
        assert result.startswith("Top 15 blocks from 5 records (ranked sample — other records may match).")
        assert "left out" not in result


class TestVectorAndGrepHits:
    @pytest.mark.asyncio
    async def test_both_are_shown_and_both_are_recorded_as_shown(self) -> None:
        records = {"v0": _record("v0")}
        state = _state(config_service=MagicMock(), include_retrieval_context=True)
        graph_record = {"_key": "rec-g1", "virtualRecordId": "g1", "recordName": "Grep doc"}

        async def merge(**kwargs: Any) -> list[dict[str, Any]]:  # noqa: ANN401
            kwargs["virtual_record_id_to_result"]["g1"] = graph_record
            return [{"virtual_record_id": "g1", "source": "pattern_match"}]

        grep_hits = [{"virtual_record_id": "v0"}, {"virtual_record_id": "g1"}]
        with patch(f"{_SEARCH}.run_pattern_match_with_llm_grep", AsyncMock(return_value=grep_hits)), \
                patch(f"{_SEARCH}.merge_pattern_match_results", side_effect=merge):
            result = await _search(state, records, [_unit("v0", 1, "vector hit", 0.8)])

        assert "vector hit" in result
        assert "Grep doc" in result
        assert set(state["virtual_record_id_to_result"]) == {"v0", "g1"}
        assert state["retrieved_by"]["v0"].layers == ["vector", "pattern_match"]
        assert state["retrieved_by"]["g1"].layers == ["pattern_match"]
        assert (state["retrieved_by"]["g1"].record_id, state["retrieved_by"]["g1"].record_name) == (
            "rec-g1", "Grep doc",
        )
        delta = RetrievalContextLedger().take_delta(state)
        assert {r.virtualRecordId for r in delta.records} == {"v0", "g1"}


class TestGrepOnly:
    @pytest.mark.asyncio
    async def test_a_keyword_hint_over_the_cap_still_answers(self) -> None:
        """No semantic units to cut: the hint goes out and the generic cap trims it."""
        state = _state(config_service=MagicMock())

        async def merge(**kwargs: Any) -> list[dict[str, Any]]:  # noqa: ANN401
            kwargs["virtual_record_id_to_result"]["g1"] = {"_key": "rec-g1", "recordName": "Grep doc"}
            return [{"virtual_record_id": "g1", "source": "pattern_match"}]

        with patch(f"{_SEARCH}.run_pattern_match_with_llm_grep", AsyncMock(return_value=[{"virtual_record_id": "g1"}])), \
                patch(f"{_SEARCH}.merge_pattern_match_results", side_effect=merge), \
                patch(f"{_SEARCH}.render_pattern_match_hint", return_value="h" * 5_000):
            result = await _search(state, {}, [], cap=1_000)

        assert result.startswith("No content blocks from semantic search, but 1 record")
        assert result.endswith("h" * 5_000)

    @pytest.mark.asyncio
    async def test_a_record_cut_by_rank_and_listed_by_grep_is_traced_to_grep_only(self) -> None:
        records = {"v0": _record("v0"), "v1": _record("v1")}
        units = [_unit("v0", 1, "kept hit", 0.9), _unit("v1", 1, "cut hit", 0.1)]
        state = _state(
            config_service=MagicMock(), include_retrieval_context=True,
            filters={"apps": ["a1", "a2", "a3", "a4", "a5"], "kb": []},
        )

        async def merge(**kwargs: Any) -> list[dict[str, Any]]:  # noqa: ANN401
            assert "v1" not in kwargs["virtual_record_id_to_result"]
            kwargs["virtual_record_id_to_result"]["v1"] = {"_key": "rec-v1", "recordName": "Doc v1"}
            return [{"virtual_record_id": "v1", "source": "pattern_match"}]

        with patch(f"{_SEARCH}.run_pattern_match_with_llm_grep", AsyncMock(return_value=[{"virtual_record_id": "v1"}])), \
                patch(f"{_SEARCH}.merge_pattern_match_results", side_effect=merge), \
                patch(f"{_BUILDER}.rank_units", side_effect=lambda units, limit=None: units[:1]):
            result = await _search(state, records, units)

        assert "cut hit" not in result
        assert state["retrieved_by"]["v0"].layers == ["vector"]
        assert state["retrieved_by"]["v1"].layers == ["pattern_match"]


class TestGrepHitOnARecordTheFitDrops:
    """Grep hits on records semantic search already loaded are not merged as
    keyword records, since their blocks are about to be shown. When the fit
    then leaves such a record out, the hint must name it, or nothing in the
    result points at the record that matched the query's exact terms."""

    @staticmethod
    async def _search_with_grep(
        state: dict[str, Any], grep_hits: list[dict[str, Any]], records: dict[str, Any] | None = None,
        units: list[dict[str, Any]] | None = None, merged: list[dict[str, Any]] | None = None, cap: int = 3_000,
    ) -> str | list[Any]:
        if records is None:
            records, units = _five_records()

        async def merge(**kwargs: Any) -> list[dict[str, Any]]:  # noqa: ANN401
            for entry in merged or []:
                kwargs["virtual_record_id_to_result"][entry["virtual_record_id"]] = entry["graph_record"]
            return [{"virtual_record_id": e["virtual_record_id"], "source": "pattern_match"} for e in merged or []]

        with patch(f"{_SEARCH}.run_pattern_match_with_llm_grep", AsyncMock(return_value=grep_hits)), \
                patch(f"{_SEARCH}.merge_pattern_match_results", side_effect=merge):
            return await _search(state, records, units, cap=cap)

    @pytest.mark.asyncio
    async def test_the_dropped_record_is_named_and_can_be_fetched(self) -> None:
        state = _state(config_service=MagicMock(), include_retrieval_context=True)
        grep = [{"virtual_record_id": "v4", "match_count": 3, "match_preview": "INV-4242 total"}]

        result = await self._search_with_grep(state, grep)

        assert isinstance(result, str)
        assert len(result) <= 3_000
        assert "v4 xxx" not in result
        hint = result[result.index("Additional records found via pattern matching"):]
        assert "Record ID: rec-v4\nName: Doc v4\nKeyword Matches: 3\nMatched Text: INV-4242 total" in hint
        assert "v4" in state["virtual_record_id_to_result"]
        assert "rec-v4" in state["known_record_ids"]
        assert "v4" not in {u["virtual_record_id"] for u in state["final_results"]}
        assert state["retrieved_by"]["v4"].layers == ["pattern_match"]
        delta = RetrievalContextLedger().take_delta(state)
        dropped = next(r for r in delta.records if r.virtualRecordId == "v4")
        assert dropped.blockIndices == []

    @pytest.mark.asyncio
    async def test_naming_many_dropped_records_still_fits_the_result(self) -> None:
        """Every record the fit drops could add a hint entry; unbounded, the
        hint alone outgrew the result and the generic cap cut it."""
        records = {f"v{i:02d}": _record(f"v{i:02d}") for i in range(40)}
        for record in records.values():
            record["context_metadata"] += "\nSummary: " + "s" * 1_500
        units = [_unit(vrid, 1, f"{vrid} " + "x" * 900, 0.9 - i / 100) for i, vrid in enumerate(records)]
        state = _state(config_service=MagicMock())
        grep = [{"virtual_record_id": vrid, "match_count": 2} for vrid in reversed(list(records))]

        result = await self._search_with_grep(state, grep, records, units, cap=20_000)

        assert len(result) <= 20_000
        hint = result[result.index("Additional records found via pattern matching"):]
        dropped = [vrid for vrid in reversed(list(records)) if f"{vrid} xxx" not in result]
        named = [vrid for vrid in dropped if f"Name: Doc {vrid}" in hint]
        assert named == dropped[: len(named)]
        assert 0 < len(named) < len(dropped)
        assert {f"rec-{vrid}" for vrid in named} <= state["known_record_ids"]
        assert all(f"rec-{vrid}" not in state["known_record_ids"] for vrid in records
                   if vrid not in named and f"{vrid} xxx" not in result)

    @pytest.mark.asyncio
    async def test_a_shown_record_is_not_named_again(self) -> None:
        state = _state(config_service=MagicMock())

        result = await self._search_with_grep(state, [{"virtual_record_id": "v0"}])

        assert "v0 xxx" in result
        assert "pattern matching" not in result

    @pytest.mark.asyncio
    async def test_nothing_dropped_means_no_hint(self) -> None:
        state = _state(config_service=MagicMock())
        records, units = _five_records()

        result = await self._search_with_grep(state, [{"virtual_record_id": "v4"}], records, units, cap=100_000)

        assert "v4 xxx" in result
        assert "pattern matching" not in result

    @pytest.mark.asyncio
    async def test_named_after_the_grep_only_records(self) -> None:
        state = _state(config_service=MagicMock())
        grep_only = {"virtual_record_id": "g1", "graph_record": {"_key": "rec-g1", "recordName": "Grep doc"}}

        result = await self._search_with_grep(
            state, [{"virtual_record_id": "v4"}, {"virtual_record_id": "g1"}], merged=[grep_only],
        )

        assert "Grep doc" in result
        assert result.index("Grep doc") < result.index("rec-v4")
        assert {"g1", "v4"} <= set(state["virtual_record_id_to_result"])

    @pytest.mark.asyncio
    async def test_best_grep_rank_first(self) -> None:
        state = _state(config_service=MagicMock())
        grep = [
            {"virtual_record_id": "v3", "matched_terms": 1, "match_count": 1},
            {"virtual_record_id": "v4", "matched_terms": 2, "match_count": 1},
        ]

        result = await self._search_with_grep(state, grep, cap=2_500)

        assert "v3 xxx" not in result and "v4 xxx" not in result
        assert result.index("rec-v4") < result.index("rec-v3")

    @pytest.mark.timeout(180)
    def test_identical_under_any_hash_seed(self) -> None:
        from tests.support.hash_seeds import outputs_across_hash_seeds

        outputs = outputs_across_hash_seeds(
            "import asyncio, os\n"
            "from unittest.mock import MagicMock\n"
            "os.environ['TARGETED_RETRIEVAL'] = 'false'\n"
            "from tests.unit.agents.actions.knowledge_graph.test_search_context import "
            "TestGrepHitOnARecordTheFitDrops as T, _state\n"
            "grep = [{'virtual_record_id': v, 'match_count': 2} for v in ('v4', 'v3', 'v2')]\n"
            "print(repr(asyncio.run(T._search_with_grep(_state(config_service=MagicMock()), grep, cap=2_500))))\n"
        )

        assert len(outputs) == 1
        assert "pattern matching" in outputs.pop()

    @pytest.mark.asyncio
    async def test_with_several_sources(self) -> None:
        state = _state(config_service=MagicMock(), filters={"apps": ["a1", "a2", "a3"], "kb": []})
        records, units = _five_records()

        async def flatten(_results, _blob, _org, _mm, vr_map, *_a, **_kw) -> list[dict[str, Any]]:
            vr_map.update(records)
            return [dict(unit) for unit in units]

        grep = AsyncMock(return_value=[{"virtual_record_id": "v4"}])
        with patch(f"{_BUILDER}.get_flattened_results", side_effect=flatten), \
                patch(f"{_BUILDER}.enrich_records_with_graph_context", new_callable=AsyncMock), \
                patch(f"{_SEARCH}.BlobStorage"), patch(f"{_SEARCH}.DEFAULT_MAX_RESULT_CHARS", 3_000), \
                patch(f"{_SEARCH}.run_pattern_match_with_llm_grep", grep):
            result = await execute_search(state, "annual fee", source_ids=["a1", "a2", "a3"])

        assert state["retrieval_service"].search_with_filters.await_count == 3
        assert "v4 xxx" not in result
        assert "Name: Doc v4" in result
        assert "v4" in state["virtual_record_id_to_result"]


def _grep_only_record(i: int) -> dict[str, Any]:
    return {
        "_key": f"rec-g{i:02d}",
        "recordName": f"Grep doc {i:02d}",
        "recordType": "FILE",
        "webUrl": "https://drive.example/file/" + "u" * 400,
    }


class TestManyGrepOnlyRecords:
    """A large store's keyword index returns up to 50 records per command. As
    full metadata blocks, sized before the ranked records, they displaced the
    passages the search exists to show."""

    @staticmethod
    async def _search_with_grep_only(state: dict[str, Any], count: int) -> str | list[Any]:
        records = {f"v{i:02d}": _record(f"v{i:02d}") for i in range(16)}
        units = [_unit(vrid, 1, f"{vrid} " + "x" * 2_000, 0.9 - i / 100) for i, vrid in enumerate(records)]

        async def merge(**kwargs: Any) -> list[dict[str, Any]]:  # noqa: ANN401
            for i in range(count):
                kwargs["virtual_record_id_to_result"][f"g{i:02d}"] = _grep_only_record(i)
            return [{"virtual_record_id": f"g{i:02d}", "source": "pattern_match"} for i in range(count)]

        grep = [{"virtual_record_id": f"g{i:02d}"} for i in range(count)]
        with patch(f"{_SEARCH}.run_pattern_match_with_llm_grep", AsyncMock(return_value=grep)), \
                patch(f"{_SEARCH}.merge_pattern_match_results", side_effect=merge):
            return await _search(state, records, units, cap=32_000)

    @staticmethod
    def _shown(result: str) -> int:
        return sum(f"v{i:02d} xxx" in result for i in range(16))

    @pytest.mark.asyncio
    async def test_the_best_few_get_full_metadata_and_the_rest_one_line_each(self) -> None:
        from app.agents.actions.knowledge_graph.ops.search import (
            _GREP_ONLY_HINT_SHARE,
            _MAX_FULL_GREP_ONLY_HINTS,
        )

        baseline = self._shown(await self._search_with_grep_only(_state(config_service=MagicMock()), 0))
        state = _state(config_service=MagicMock())

        result = await self._search_with_grep_only(state, 45)

        assert isinstance(result, str)
        assert len(result) <= 32_000
        hint = result[result.index("Additional records found via pattern matching"):]
        assert len(hint) <= 32_000 // _GREP_ONLY_HINT_SHARE + 600
        full = [i for i in range(45) if f"Name: Grep doc {i:02d}" in hint]
        assert full == list(range(len(full)))
        assert 0 < len(full) <= _MAX_FULL_GREP_ONLY_HINTS
        brief = [i for i in range(45) if f"- Grep doc {i:02d} (Record ID: rec-g{i:02d})" in hint]
        assert brief == list(range(len(full), len(full) + len(brief)))
        assert len(brief) >= 20
        assert hint.count("<record>") == len(full)
        per_record = 2_000
        assert self._shown(result) >= baseline - (32_000 // _GREP_ONLY_HINT_SHARE) // per_record - 1

    @pytest.mark.asyncio
    async def test_one_line_records_are_registered_as_shown(self) -> None:
        state = _state(config_service=MagicMock(), include_retrieval_context=True)

        result = await self._search_with_grep_only(state, 45)

        brief = [i for i in range(45) if f"- Grep doc {i:02d} (Record ID: rec-g{i:02d})" in result]
        unlisted = [i for i in range(45) if f"Grep doc {i:02d}" not in result]
        assert brief
        for i in brief:
            assert f"rec-g{i:02d}" in state["known_record_ids"]
            assert f"g{i:02d}" in state["virtual_record_id_to_result"]
            assert state["retrieved_by"][f"g{i:02d}"].layers == ["pattern_match"]
        for i in unlisted:
            assert f"rec-g{i:02d}" not in state["known_record_ids"]
        if unlisted:
            assert f"{len(unlisted)} more keyword-matched record" in result

    @pytest.mark.asyncio
    async def test_a_few_grep_only_records_render_as_before(self) -> None:
        from app.utils.pattern_match import render_pattern_match_hint

        state = _state(config_service=MagicMock())

        result = await self._search_with_grep_only(state, 3)

        entries = [{"virtual_record_id": f"g{i:02d}", "source": "pattern_match"} for i in range(3)]
        expected = render_pattern_match_hint(entries, {f"g{i:02d}": _grep_only_record(i) for i in range(3)})
        assert expected.count("<record>") == 3
        assert result.endswith(expected)
        assert "Record ID only" not in result


class TestReadingOrder:
    @pytest.mark.asyncio
    async def test_by_default_records_keep_their_id_order(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(RELEVANCE_ORDERED_CONTEXT_ENV_VAR, raising=False)
        monkeypatch.delenv("HIGH_ACCURACY_MODE", raising=False)
        records = {"a": _record("a"), "b": _record("b")}
        units = [_unit("b", 0, "beta hit", 0.9), _unit("a", 0, "alpha hit", 0.2)]

        result = await _search(_state(), records, units)

        assert result.index("alpha hit") < result.index("beta hit")
        assert "most relevant record first" not in result

    @pytest.mark.asyncio
    async def test_the_switch_puts_the_most_relevant_record_first(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(RELEVANCE_ORDERED_CONTEXT_ENV_VAR, "true")
        records = {"a": _record("a"), "b": _record("b")}
        units = [_unit("b", 0, "beta hit", 0.9), _unit("a", 0, "alpha hit", 0.2)]

        result = await _search(_state(), records, units)

        assert result.index("beta hit") < result.index("alpha hit")
        assert "most relevant record first" in result

    @pytest.mark.asyncio
    async def test_identical_searches_render_byte_identical_results(self) -> None:
        records, units = _five_records()
        first = await _search(_state(), records, units, cap=3_000)
        second = await _search(_state(), records, units, cap=3_000)
        assert first == second


class TestWronglyTypedState:
    @pytest.mark.asyncio
    async def test_accumulators_of_the_wrong_type_are_started_over(self) -> None:
        records = {"a": _record("a")}
        state = _state(
            final_results=[None, "oops"],
            virtual_record_id_to_result=["oops"],
            tool_records={"oops": 1},
            image_budget="oops",
            citation_ref_mapper={"oops": 1},
        )

        result = await _search(state, records, [_unit("a", 0, "alpha hit", 0.5)])

        assert "alpha hit" in result
        assert {u["virtual_record_id"] for u in state["final_results"]} == {"a"}
        assert set(state["virtual_record_id_to_result"]) == {"a"}
        assert isinstance(state["citation_ref_mapper"], CitationRefMapper)
        assert isinstance(state["image_budget"], ImageBudget)


def _image_units() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    records = {"v1": _record("v1")}
    image = {
        "virtual_record_id": "v1",
        "block_index": 0,
        "block_type": BlockType.IMAGE.value,
        "content": _PNG,
        "metadata": {},
        "score": 0.9,
    }
    return records, [image]


class TestImages:
    @pytest.mark.asyncio
    async def test_downscales_are_decided_off_the_event_loop_first(self) -> None:
        records, units = _image_units()
        state = _state(is_multimodal_llm=True)
        with patch("app.utils.image_admission.ImageAdmission.warm", new_callable=AsyncMock) as warm:
            await _search(state, records, units)
        warm.assert_awaited_once_with([_PNG])

    @pytest.mark.asyncio
    async def test_a_multimodal_model_gets_the_image_with_the_text(self) -> None:
        records, units = _image_units()
        state = _state(is_multimodal_llm=True)

        result = await _search(state, records, units)

        assert isinstance(result, list)
        assert isinstance(result[0], TextPart)
        assert [type(part) for part in result[1:]] == [ImagePart]
        assert "pending_tool_images" not in state
        assert state["image_budget"].used == 1

    @pytest.mark.asyncio
    async def test_an_image_prefetch_attached_is_not_attached_again_by_a_search(self) -> None:
        records, units = _image_units()
        state = _state(is_multimodal_llm=True)
        state["image_admission"] = ImageAdmission(permissive_policy(1), budget=state.setdefault("image_budget", ImageBudget()))
        retrieval = AsyncMock()
        retrieval.search_with_filters.return_value = {
            "status_code": 200, "searchResults": [{"virtual_record_id": "v1"}], "virtual_to_record_map": {},
        }

        async def flatten(_results, _blob, _org, _mm, vr_map, *_a, **_kw) -> list[dict[str, Any]]:
            vr_map.update(records)
            return [dict(unit) for unit in units]

        with patch(f"{_BUILDER}.get_flattened_results", side_effect=flatten), \
                patch(f"{_BUILDER}.enrich_virtual_record_id_to_result_with_fk_children", new_callable=AsyncMock), \
                patch(f"{_BUILDER}.enrich_records_with_graph_context", new_callable=AsyncMock):
            prefetched = await prefetch_retrieval(
                query="q", org_id="o1", user_id="u1", retrieval_service=retrieval,
                graph_provider=object(), blob_store=object(), filters={}, limit=10,
                is_multimodal_llm=True, previous_conversations=None, logger=MagicMock(),
                image_budget=state["image_budget"], image_admission=admission_from_state(state),
            )
        assert len(prefetched.collected_images) == 1

        result = await _search(state, records, units)

        assert isinstance(result, str)
        assert "shown above" in result
        assert state["image_budget"].used == 1

    @pytest.mark.asyncio
    async def test_a_text_model_gets_text_only(self) -> None:
        records, units = _image_units()
        state = _state(is_multimodal_llm=False)

        result = await _search(state, records, units)

        assert isinstance(result, str)
        assert "pending_tool_images" not in state

    @pytest.mark.asyncio
    async def test_an_exhausted_image_budget_falls_back_to_text(self) -> None:
        records, units = _image_units()
        budget = ImageBudget(max_images=1)
        budget.try_consume(1)
        state = _state(is_multimodal_llm=True, image_budget=budget)

        result = await _search(state, records, units)

        assert isinstance(result, str)
        assert "not shown" in result


def _png(color: tuple[int, int, int]) -> str:
    import base64
    import io

    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (640, 480), color).save(buffer, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()


def _figure_record(vrid: str, uris: list[str]) -> dict[str, Any]:
    record = _record(vrid, blocks=1)
    record["block_containers"]["blocks"] += [
        {"index": i + 1, "type": BlockType.IMAGE.value, "data": {"uri": uri, "description": f"figure {i}"}}
        for i, uri in enumerate(uris)
    ]
    for i, block in enumerate(record["block_containers"]["blocks"]):
        block["index"] = i
    return record


class TestSearchLeavesImageRoomForAFetch:
    """A search runs before the model can ask for anything. Given the whole
    per-request image cap it spent all of it, and the record the model then
    fetched for its figures came back with none."""

    @staticmethod
    def _multimodal_state(provider: str = "openai") -> dict[str, Any]:
        from app.utils.image_policy import resolve_image_policy

        budget = ImageBudget()
        return _state(
            is_multimodal_llm=True,
            image_budget=budget,
            image_admission=ImageAdmission(resolve_image_policy(provider=provider, is_multimodal=True), budget=budget),
        )

    @staticmethod
    async def _fetch(state: dict[str, Any], record: dict[str, Any]) -> list[Any]:
        from types import SimpleNamespace

        from app.agents.actions.knowledge_graph.ops.fetch import execute_fetch_record
        from app.services.graph_db.interface.graph_db_provider import AccessCheck

        live = state.setdefault("virtual_record_id_to_result", {})
        live[record["virtual_record_id"]] = record
        # The fetch checks the record is readable and inside the turn's apps: this
        # user may read every record asked for, and each is in the picked app.
        graph = AsyncMock()
        graph.get_user_by_user_id = AsyncMock(return_value={"_key": "u1", "id": "u1"})
        graph.check_access = AsyncMock(
            side_effect=lambda *_args, node_ids=(), **_kwargs: AccessCheck(
                node_ids=frozenset(node_ids), node_ids_in_scope=frozenset(node_ids),
            ),
        )
        context = SimpleNamespace(
            model_name="gpt-4", org_id="o1", user_id="u1", graph_provider=graph,
            full_records_fetched=set(), tool_state=state, is_multimodal_llm=True,
            context_length=128_000, query="", retrieval_service=None,
            needs_whole_document=False, include_retrieval_context=False,
        )
        output, _ = await execute_fetch_record(
            context=context, virtual_records=live, citation_ref_mapper=state.get("citation_ref_mapper"),
            record_ids=[record["id"]], reason="read it",
        )
        return output.data if isinstance(output.data, list) else [output.data]

    @staticmethod
    def _images(parts: list[Any]) -> int:
        return sum(isinstance(part, ImagePart) or getattr(part, "type", None) == "image" for part in parts)

    @staticmethod
    def _text(parts: list[Any]) -> str:
        return "".join(getattr(part, "text", "") or (part if isinstance(part, str) else "") for part in parts)

    @pytest.mark.asyncio
    async def test_eight_image_hits_leave_half_the_cap_for_a_fetched_record(self) -> None:
        uris = [_png((i * 30, 80, 160)) for i in range(8)]
        records = {f"i{i}": _figure_record(f"i{i}", [uris[i]]) for i in range(8)}
        units = [
            {"virtual_record_id": f"i{i}", "block_index": 1, "block_type": BlockType.IMAGE.value,
             "content": uris[i], "metadata": {}, "score": 0.9 - i / 100}
            for i in range(8)
        ]
        state = self._multimodal_state()

        searched = await _search(state, records, units)
        fetched = await self._fetch(state, _figure_record("f1", [_png((10, 200 + i * 10, 40)) for i in range(3)]))

        assert self._images(searched) == 4
        assert self._images(fetched) == 3
        assert "not shown" not in self._text(fetched)

    @pytest.mark.asyncio
    async def test_prefetch_takes_only_its_share(self) -> None:
        uris = [_png((i * 30, 10, 10)) for i in range(8)]
        records = {f"i{i}": _figure_record(f"i{i}", [uris[i]]) for i in range(8)}
        units = [
            {"virtual_record_id": f"i{i}", "block_index": 1, "block_type": BlockType.IMAGE.value,
             "content": uris[i], "metadata": {}, "score": 0.9 - i / 100}
            for i in range(8)
        ]
        state = self._multimodal_state()
        retrieval = AsyncMock()
        retrieval.search_with_filters.return_value = {
            "status_code": 200, "searchResults": [{"virtual_record_id": v} for v in records],
            "virtual_to_record_map": {},
        }

        async def flatten(_results, _blob, _org, _mm, vr_map, *_a, **_kw) -> list[dict[str, Any]]:
            vr_map.update(records)
            return [dict(unit) for unit in units]

        with patch(f"{_BUILDER}.get_flattened_results", side_effect=flatten), \
                patch(f"{_BUILDER}.enrich_virtual_record_id_to_result_with_fk_children", new_callable=AsyncMock), \
                patch(f"{_BUILDER}.enrich_records_with_graph_context", new_callable=AsyncMock):
            prefetched = await prefetch_retrieval(
                query="q", org_id="o1", user_id="u1", retrieval_service=retrieval,
                graph_provider=object(), blob_store=object(), filters={}, limit=10,
                is_multimodal_llm=True, previous_conversations=None, logger=MagicMock(),
                image_budget=state["image_budget"], image_admission=state["image_admission"],
            )
        fetched = await self._fetch(state, _figure_record("f1", [_png((10, 200 + i * 10, 40)) for i in range(3)]))

        assert len(prefetched.collected_images) == 4
        assert self._images(fetched) == 3

    @pytest.mark.asyncio
    async def test_a_one_image_model_gives_its_slot_to_the_fetch(self) -> None:
        uri = _png((90, 90, 90))
        state = self._multimodal_state("ollama")
        unit = {"virtual_record_id": "i0", "block_index": 1, "block_type": BlockType.IMAGE.value,
                "content": uri, "metadata": {}, "score": 0.9}

        searched = await _search(state, {"i0": _figure_record("i0", [uri])}, [unit])
        fetched = await self._fetch(state, _figure_record("f1", [_png((1, 2, 3))]))

        assert self._images(searched if isinstance(searched, list) else [searched]) == 0
        assert self._images(fetched) == 1

    @pytest.mark.asyncio
    async def test_a_fetch_points_at_the_copy_search_attached(self) -> None:
        uri = _png((200, 30, 30))
        record = _figure_record("p1", [uri])
        unit = {"virtual_record_id": "p1", "block_index": 1, "block_type": BlockType.IMAGE.value,
                "content": uri, "metadata": {}, "score": 0.9}
        state = self._multimodal_state()

        searched = await _search(state, {"p1": record}, [unit])
        fetched = await self._fetch(state, record)

        assert self._images(searched) == 1
        assert self._images(fetched) == 0
        assert "shown above" in self._text(fetched)


class TestEmpty:
    @pytest.mark.asyncio
    async def test_hits_whose_records_did_not_load_are_an_empty_search(self) -> None:
        result = await _search(_state(), {"v1": None}, [_unit("v1", 0, "lost", 0.9)])

        assert json.loads(result)["result_count"] == 0


class TestAConnectedAppsLimit:
    """The search tool hands the turn's connection limit to the graph context."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(("filters", "sources"), [
        ({"apps": ["app-1"], "kb": []}, None),
        ({"apps": ["app-1"], "kb": [], "records": ["f"], "connectionApps": [], "connectionRecordGroups": [],
             "connectionRecords": ["f"]}, {"apps": [], "recordGroups": [], "records": ["f"]}),
    ])
    async def test_the_turns_limit_reaches_the_graph_context(self, filters, sources) -> None:
        records, units = _five_records()
        graph_context = AsyncMock()

        async def flatten(_results, _blob, _org, _mm, vr_map, *_a, **_kw) -> list[dict[str, Any]]:
            vr_map.update(records)
            return [dict(unit) for unit in units]

        with patch(f"{_BUILDER}.get_flattened_results", side_effect=flatten), \
                patch(f"{_BUILDER}.enrich_records_with_graph_context", graph_context), \
                patch(f"{_SEARCH}.BlobStorage"):
            await execute_search(_state(filters=filters), "annual fee")

        graph_context.assert_awaited()
        assert graph_context.await_args.kwargs["connection_sources"] == sources


class TestAGrepOnlyHitsConnectorName:
    """A record found only by grep is cited from its graph document, which
    search never named; the citation showed no source name."""

    @staticmethod
    async def _cite(filters: dict[str, Any]) -> dict[str, Any]:
        from app.utils.citations import _append_record_page_citation

        state = _state(config_service=MagicMock(), filters=filters)
        state["graph_provider"].get_document = AsyncMock(return_value={"_key": "app-1", "name": "Board finance"})
        records, units = _five_records()
        hit = {**_grep_only_record(0), "connectorId": "app-1", "virtualRecordId": "g00"}

        async def merge(**kwargs: Any) -> list[dict[str, Any]]:  # noqa: ANN401
            kwargs["virtual_record_id_to_result"]["g00"] = hit
            return [{"virtual_record_id": "g00", "source": "pattern_match"}]

        with patch(f"{_SEARCH}.run_pattern_match_with_llm_grep", AsyncMock(return_value=[{"virtual_record_id": "g00"}])), \
                patch(f"{_SEARCH}.merge_pattern_match_results", side_effect=merge):
            await _search(state, records, units)
        citations: list[dict[str, Any]] = []
        _append_record_page_citation(hit, "https://app.example/record/rec-g00", citations, {}, 1)
        return citations[0]["metadata"]

    @pytest.mark.asyncio
    @pytest.mark.parametrize("filters", [
        {"apps": ["app-1"], "kb": []},
        {"apps": ["app-1"], "kb": [], "connectionApps": ["app-1"], "connectionRecordGroups": [],
            "connectionRecords": []},
    ])
    async def test_is_named_without_a_limit_or_under_its_whole_app(self, filters) -> None:
        assert (await self._cite(filters))["connectorInstanceName"] == "Board finance"

    @pytest.mark.asyncio
    async def test_is_not_named_under_a_narrower_limit(self) -> None:
        meta = await self._cite({
            "apps": ["app-1"], "kb": [], "records": ["rec-g00"], "connectionApps": [],
            "connectionRecordGroups": [], "connectionRecords": ["rec-g00"],
        })

        assert meta["connectorInstanceName"] == ""
