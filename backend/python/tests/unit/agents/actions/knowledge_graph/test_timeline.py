"""``knowledgegraph__search_timeline``: periods, scope, what the model is shown.

The search op runs with the real context pipeline and renderer; only
retrieval, blob loading and graph enrichment are stood in for, as in
test_search_context.py.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agent_loop_lib.tools.toolset import ToolsetBuilder
from app.agents.actions.knowledge_graph.knowledge_graph import KnowledgeGraph
from app.agents.actions.knowledge_graph.ops import timeline
from app.agents.actions.knowledge_graph.ops.entity_filters import (
    ENTITY_ID_FILTER_KEY_CACHE_KEY,
    RECORD_SCOPED_ENTITY_CACHE_KEY,
)
from app.agents.actions.knowledge_graph.ops.entity_records import EntitySearchScope
from app.agents.actions.knowledge_graph.ops.timeline import (
    DEFAULT_LOOKBACK_DAYS,
    MAX_PERIODS,
    PERIOD_SEARCH_LIMIT,
    SPAN_SEARCH_LIMIT,
    TIMELINE_SUMMARIES_ENV_VAR,
    TIMELINE_TOOL_NAME,
    execute_timeline_search,
    plan_periods,
    span_of,
    timeline_summaries_enabled,
)
from app.models.blocks import BlockType

_BUILDER = "app.modules.retrieval.context.builder"


def _ms(day: str) -> int:
    return int(datetime.fromisoformat(day).replace(tzinfo=timezone.utc).timestamp() * 1000)


def _end_of(day: str) -> int:
    return _ms(day) + 24 * 60 * 60 * 1000 - 1


def _labels(start: str, end: str) -> list[str]:
    return [p.label for p in plan_periods(_ms(start), _end_of(end))]


class TestPlanPeriods:
    def test_a_span_of_months_is_one_period_per_month(self) -> None:
        assert _labels("2026-03-01", "2026-05-31") == [
            "March 2026 (2026-03-01 to 2026-03-31)",
            "April 2026 (2026-04-01 to 2026-04-30)",
            "May 2026 (2026-05-01 to 2026-05-31)",
        ]

    def test_the_first_and_last_months_are_clipped_to_the_span(self) -> None:
        periods = plan_periods(_ms("2026-03-15"), _end_of("2026-05-10"))
        assert periods[0].start_ms == _ms("2026-03-15")
        assert periods[-1].end_ms == _end_of("2026-05-10")
        assert periods[0].label == "March 2026 (2026-03-15 to 2026-03-31)"

    def test_more_months_than_periods_are_grouped_evenly_and_cover_the_span(self) -> None:
        periods = plan_periods(_ms("2026-01-01"), _end_of("2026-10-17"))
        assert len(periods) == MAX_PERIODS
        assert periods[0].start_ms == _ms("2026-01-01")
        assert periods[-1].end_ms == _end_of("2026-10-17")
        for before, after in zip(periods, periods[1:]):
            assert after.start_ms == before.end_ms + 1

    def test_a_short_span_is_cut_into_weeks_of_whole_days(self) -> None:
        assert _labels("2026-09-20", "2026-09-30") == ["2026-09-20 to 2026-09-25", "2026-09-26 to 2026-09-30"]
        assert _labels("2026-07-20", "2026-08-02") == ["2026-07-20 to 2026-07-26", "2026-07-27 to 2026-08-02"]

    def test_one_day_is_one_period(self) -> None:
        assert _labels("2026-05-05", "2026-05-05") == ["2026-05-05"]

    def test_an_inverted_span_is_read_the_right_way_round(self) -> None:
        assert plan_periods(_end_of("2026-05-31"), _ms("2026-03-01")) == plan_periods(
            _ms("2026-03-01"), _end_of("2026-05-31"),
        )


def test_the_span_is_the_earliest_and_latest_dated_record() -> None:
    records = {
        "a": {"sourceCreatedAtTimestamp": _ms("2026-04-09"), "sourceLastModifiedTimestamp": _ms("2026-06-01")},
        "b": {"sourceCreatedAtTimestamp": _ms("2026-03-03")},
        "c": {"sourceCreatedAtTimestamp": None},
        "d": "not a record",
    }
    assert span_of(records, "created") == (_ms("2026-03-03"), _ms("2026-04-09"))
    assert span_of(records, "modified") == (_ms("2026-06-01"), _ms("2026-06-01"))
    assert span_of({}, "created") is None


class TestOfferedUnlessSwitchedOff:
    def _served(self) -> list[str]:
        instance = KnowledgeGraph(state={})
        declared = ToolsetBuilder(instance, name="knowledgegraph", description="", path_prefix="/tools/knowledgegraph").tools
        return [t.name for t in instance.tools_for_request(list(declared))]

    @pytest.mark.parametrize("value", [None, "", "true", "TRUE", "1", "yes"])
    def test_offered_unless_set_to_false(self, monkeypatch: pytest.MonkeyPatch, value: str | None) -> None:
        # Unset and blank (what Compose passes for an unset variable) are on.
        if value is None:
            monkeypatch.delenv(TIMELINE_SUMMARIES_ENV_VAR, raising=False)
        else:
            monkeypatch.setenv(TIMELINE_SUMMARIES_ENV_VAR, value)
        assert timeline_summaries_enabled() is True
        assert TIMELINE_TOOL_NAME in self._served()

    @pytest.mark.parametrize("value", ["false", "FALSE", "0", "no", "off", " false "])
    def test_withheld_when_set_to_false(self, monkeypatch: pytest.MonkeyPatch, value: str) -> None:
        monkeypatch.setenv(TIMELINE_SUMMARIES_ENV_VAR, value)
        assert timeline_summaries_enabled() is False
        assert TIMELINE_TOOL_NAME not in self._served()

    @pytest.mark.parametrize("mode", ["true", "false"])
    def test_high_accuracy_mode_does_not_change_it(self, monkeypatch: pytest.MonkeyPatch, mode: str) -> None:
        monkeypatch.setenv("HIGH_ACCURACY_MODE", mode)
        monkeypatch.delenv(TIMELINE_SUMMARIES_ENV_VAR, raising=False)
        assert TIMELINE_TOOL_NAME in self._served()
        monkeypatch.setenv(TIMELINE_SUMMARIES_ENV_VAR, "false")
        assert TIMELINE_TOOL_NAME not in self._served()


# ── The op ────────────────────────────────────────────────────────────────


def _record(vrid: str) -> dict[str, Any]:
    return {
        "id": f"rec-{vrid}",
        "_id": f"records/rec-{vrid}",
        "virtual_record_id": vrid,
        "record_name": f"Thread {vrid}",
        "context_metadata": f"Record ID: rec-{vrid}\nName: Thread {vrid}",
        "frontend_url": "https://app.example",
        "block_containers": {
            "blocks": [{"type": BlockType.TEXT.value, "data": f"{vrid} said {'x' * 50}"} for _ in range(2)],
            "block_groups": [],
        },
    }


class _Corpus:
    """Records dated at the source, searched by the date filter the call passes."""

    def __init__(
        self, dated: dict[str, str], *, size: int = 400, failing: set[str] | None = None,
        scores: dict[str, float] | None = None, sizes: dict[str, int] | None = None,
    ) -> None:
        self.dated = {vrid: _ms(day) if day else None for vrid, day in dated.items()}
        self.size = size
        self.failing = failing or set()
        self.scores = scores or {}
        self.sizes = sizes or {}
        self.refuse_groups: set[str] = set()
        self.calls: list[dict[str, Any]] = []

    async def search_with_filters(self, **kwargs: Any) -> dict[str, Any]:  # noqa: ANN401
        self.calls.append(kwargs)
        time_range = kwargs.get("time_range") or {}
        lo = time_range.get("source_created_after_ms", time_range.get("source_updated_after_ms", 0))
        hi = time_range.get("source_created_before_ms", time_range.get("source_updated_before_ms", 2**62))
        vrids = [
            vrid for vrid, ms in self.dated.items()
            # A date filter never matches a record with no source date.
            if (ms is None and not time_range) or (ms is not None and lo <= ms <= hi)
        ]
        if self.refuse_groups & set(kwargs["filter_groups"]):
            vrids = []
        if any(vrid in self.failing for vrid in vrids):
            return {"status_code": 503, "searchResults": [], "message": "vector store unavailable"}
        if not vrids:
            return {"status_code": 404, "searchResults": [], "message": "No accessible documents"}
        vrids = sorted(vrids, key=lambda vrid: -self.scores.get(vrid, 0.5))[:kwargs["limit"]]
        return {
            "status_code": 200,
            "searchResults": [{"virtual_record_id": vrid, "score": self.scores.get(vrid, 0.5)} for vrid in vrids],
            "virtual_to_record_map": {
                vrid: {"sourceCreatedAtTimestamp": self.dated[vrid], "sourceLastModifiedTimestamp": self.dated[vrid]}
                for vrid in vrids
            },
        }

    def units(self, results: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            {
                "virtual_record_id": r["virtual_record_id"],
                "block_index": 0,
                "block_type": BlockType.TEXT.value,
                "content": f"{r['virtual_record_id']} " + "y" * self.sizes.get(r["virtual_record_id"], self.size),
                "metadata": {},
                "score": self.scores.get(r["virtual_record_id"], 0.5),
            }
            for r in results
        ]


def _state(corpus: _Corpus, **extra: Any) -> dict[str, Any]:  # noqa: ANN401
    return {
        "logger": MagicMock(),
        "retrieval_service": corpus,
        "graph_provider": AsyncMock(),
        "config_service": None,
        "org_id": "o1",
        "user_id": "u1",
        "filters": {"apps": ["slack-1"], "kb": []},
        **extra,
    }


async def _run(corpus: _Corpus, state: dict[str, Any] | None = None, **kwargs: Any) -> Any:  # noqa: ANN401
    state = state if state is not None else _state(corpus)

    async def flatten(results, _blob, _org, _mm, vr_map, *_a, **_kw) -> list[dict[str, Any]]:
        vr_map.update({r["virtual_record_id"]: _record(r["virtual_record_id"]) for r in results})
        return corpus.units(results)

    with patch(f"{_BUILDER}.get_flattened_results", side_effect=flatten), \
            patch(f"{_BUILDER}.enrich_records_with_graph_context", new_callable=AsyncMock), \
            patch(f"{_BUILDER}.enrich_records_with_address_links", new_callable=AsyncMock), \
            patch("app.modules.transformers.blob_storage.BlobStorage"):
        return await execute_timeline_search(state, "slack bot problems", **kwargs)


_THREE_MONTHS = {"mar": "2026-03-03", "apr": "2026-04-09", "may": "2026-05-06", "may2": "2026-05-20"}


class TestShowsEveryPeriodInDateOrder:
    @pytest.mark.asyncio
    async def test_one_search_per_period_with_that_period_as_the_date_filter(self) -> None:
        corpus = _Corpus(_THREE_MONTHS)
        await _run(corpus, start="2026-03-01", end="2026-05-31")

        whole, *periods = corpus.calls
        assert whole["time_range"] == {
            "source_created_after_ms": _ms("2026-03-01"), "source_created_before_ms": _end_of("2026-05-31"),
        }
        assert whole["limit"] == SPAN_SEARCH_LIMIT
        assert [c["time_range"] for c in periods] == [
            {"source_created_after_ms": _ms("2026-03-01"), "source_created_before_ms": _end_of("2026-03-31")},
            {"source_created_after_ms": _ms("2026-04-01"), "source_created_before_ms": _end_of("2026-04-30")},
            {"source_created_after_ms": _ms("2026-05-01"), "source_created_before_ms": _end_of("2026-05-31")},
        ]
        assert all(c["limit"] == PERIOD_SEARCH_LIMIT and c["queries"] == ["slack bot problems"] for c in periods)
        assert all(c["filter_groups"]["apps"] == ["slack-1"] for c in corpus.calls)

    @pytest.mark.asyncio
    async def test_periods_and_records_read_oldest_first(self) -> None:
        corpus = _Corpus({**_THREE_MONTHS, "may0": "2026-05-02"})
        text = await _run(corpus, start="2026-03-01", end="2026-05-31")

        assert isinstance(text, str)
        order = [text.index(marker) for marker in (
            "### March 2026", "mar yyy", "### April 2026", "apr yyy", "### May 2026", "may0 yyy", "may yyy", "may2 yyy",
        )]
        assert order == sorted(order)
        assert text.startswith('Timeline for "slack bot problems": 3 periods from 2026-03-01 to 2026-05-31')

    @pytest.mark.asyncio
    async def test_a_period_with_nothing_says_so(self) -> None:
        corpus = _Corpus({"mar": "2026-03-03", "may": "2026-05-06"})
        text = await _run(corpus, start="2026-03-01", end="2026-05-31")

        april = text.split("### April 2026", 1)[1].split("###", 1)[0]
        assert "No matching records." in april

    @pytest.mark.asyncio
    async def test_a_failed_period_is_not_reported_as_empty(self) -> None:
        corpus = _Corpus(_THREE_MONTHS, failing={"apr"})
        text = await _run(corpus, start="2026-03-01", end="2026-05-31")

        april = text.split("### April 2026", 1)[1].split("###", 1)[0]
        assert "This period's own search failed. Search it again" in april
        assert "mar yyy" in text and "may yyy" in text

    @pytest.mark.asyncio
    async def test_every_period_keeps_a_share_when_one_period_is_crowded(self) -> None:
        dated = {f"mar{i}": f"2026-03-{i + 1:02d}" for i in range(9)}
        dated["may"] = "2026-05-06"
        corpus = _Corpus(dated, size=3_000)
        with patch.object(timeline, "TIMELINE_RESULT_CHARS", 12_000):
            text = await _run(corpus, start="2026-03-01", end="2026-05-31")

        assert len(text) <= 12_000 + 200
        assert "may yyy" in text
        assert sum(f"mar{i} yyy" in text for i in range(9)) < 9


class TestTheWholeSpanSearch:
    @pytest.mark.asyncio
    async def test_a_match_its_period_search_ranked_too_low_still_shows_under_its_period(self) -> None:
        dated = {f"mar{i:02d}": "2026-03-10" for i in range(PERIOD_SEARCH_LIMIT + 2)}
        dated["apr"] = "2026-04-09"
        scores = {vrid: 0.9 - i / 100 for i, vrid in enumerate(sorted(dated))}
        corpus = _Corpus(dated, scores=scores, size=40)
        text = await _run(corpus, start="2026-03-01", end="2026-04-30")

        march_search = corpus.calls[1]
        assert march_search["limit"] == PERIOD_SEARCH_LIMIT
        last = f"mar{PERIOD_SEARCH_LIMIT + 1:02d}"
        march = text.split("### March 2026", 1)[1].split("###", 1)[0]
        assert f"{last} yyy" in march

    @pytest.mark.asyncio
    async def test_a_failed_whole_span_search_leaves_the_periods(self) -> None:
        corpus = _Corpus(_THREE_MONTHS)
        real = corpus.search_with_filters

        async def flaky(**kwargs: Any) -> dict[str, Any]:  # noqa: ANN401
            if kwargs["limit"] == SPAN_SEARCH_LIMIT:
                raise TimeoutError("vector store timed out")
            return await real(**kwargs)

        corpus.search_with_filters = flaky  # type: ignore[method-assign]
        text = await _run(corpus, start="2026-03-01", end="2026-05-31")
        assert all(f"{vrid} yyy" in text for vrid in _THREE_MONTHS)


def test_the_densest_period_gets_the_most_and_every_period_some() -> None:
    assert timeline._shares(900, [None, 1, 8]) == [0, 150 + 600 * 1 // 9, 150 + 600 * 8 // 9]
    assert timeline._shares(900, [0, 0]) == [450, 450]
    assert timeline._shares(900, [None, None]) == [0, 0]


@pytest.mark.asyncio
async def test_one_overlong_match_is_named_not_allowed_to_crowd_out_later_periods() -> None:
    corpus = _Corpus({"big": "2026-03-02", "mar": "2026-03-20", "may": "2026-05-06"},
                     scores={"big": 0.99}, sizes={"big": 20_000}, size=40)
    state = _state(corpus)
    with patch.object(timeline, "TIMELINE_RESULT_CHARS", 9_000):
        text = await _run(corpus, state, start="2026-03-01", end="2026-05-31")

    assert "big yyy" not in text
    assert "too long to show here" in text and "Thread big (Record ID: rec-big)" in text
    assert "mar yyy" in text and "may yyy" in text
    assert len(text) <= 9_000
    assert "rec-big" in state["known_record_ids"]


_TODAY = _ms("2026-06-30")


class TestUndatedAndFailedWithoutDates:
    @pytest.mark.asyncio
    async def test_a_failed_whole_span_search_still_runs_the_periods(self) -> None:
        corpus = _Corpus(_THREE_MONTHS)
        real = corpus.search_with_filters

        async def flaky(**kwargs: Any) -> dict[str, Any]:  # noqa: ANN401
            if kwargs["limit"] == SPAN_SEARCH_LIMIT:
                raise TimeoutError("vector store timed out")
            return await real(**kwargs)

        corpus.search_with_filters = flaky  # type: ignore[method-assign]
        with patch.object(timeline, "_now_ms", return_value=_TODAY):
            text = await _run(corpus)

        assert len(corpus.calls) == MAX_PERIODS
        assert all(f"{vrid} yyy" in text for vrid in _THREE_MONTHS)

    @pytest.mark.asyncio
    async def test_matches_that_are_all_undated_are_shown_not_reported_missing(self) -> None:
        corpus = _Corpus({"a": None, "b": None})
        with patch.object(timeline, "_now_ms", return_value=_TODAY):
            text = await _run(corpus)

        assert isinstance(text, str)
        undated = text.split("### No source date", 1)[1]
        assert "a yyy" in undated and "b yyy" in undated

    @pytest.mark.asyncio
    async def test_an_empty_timeline_says_to_search_without_dates(self) -> None:
        corpus = _Corpus({})
        result = json.loads(await _run(corpus, start="2026-03-01", end="2026-05-31"))

        assert "search with knowledgegraph__search and no date filters" in result["message"]


class TestTheSpan:
    @pytest.mark.asyncio
    async def test_no_dates_covers_the_year_before_today_and_says_so(self) -> None:
        corpus = _Corpus(_THREE_MONTHS)
        with patch.object(timeline, "_now_ms", return_value=_TODAY):
            text = await _run(corpus)

        whole, *periods = corpus.calls
        assert whole["time_range"] is None and whole["limit"] == SPAN_SEARCH_LIMIT
        assert periods[0]["time_range"]["source_created_after_ms"] == _TODAY - DEFAULT_LOOKBACK_DAYS * 86_400_000
        assert periods[-1]["time_range"]["source_created_before_ms"] == _TODAY
        assert len(periods) == MAX_PERIODS
        assert "No start was given, so this covers 2025-06-30 on" in text

    @pytest.mark.asyncio
    async def test_the_best_matches_bunched_in_one_month_do_not_narrow_the_span(self) -> None:
        dated: dict[str, str | None] = {f"may{i:02d}": f"2026-05-{i % 28 + 1:02d}" for i in range(SPAN_SEARCH_LIMIT)}
        dated.update({"mar-a": "2026-03-04", "mar-b": "2026-03-18", "nodate": None})
        scores = dict.fromkeys(dated, 0.9) | {"mar-a": 0.3, "mar-b": 0.3, "nodate": 0.95}
        corpus = _Corpus(dated, scores=scores, size=40)
        with patch.object(timeline, "_now_ms", return_value=_TODAY):
            text = await _run(corpus)

        whole, *periods = corpus.calls
        assert any(
            c["time_range"]["source_created_after_ms"] <= _ms("2026-03-04") <= c["time_range"]["source_created_before_ms"]
            for c in periods
        )
        top = await corpus.search_with_filters(**whole)
        assert not {"mar-a", "mar-b"} & {h["virtual_record_id"] for h in top["searchResults"]}
        assert "mar-a yyy" in text and "mar-b yyy" in text
        undated = text.split("### No source date", 1)[1]
        assert "nodate yyy" in undated
        assert "Matches whose record has no source date come last." in text

    @pytest.mark.asyncio
    async def test_start_alone_runs_to_today(self) -> None:
        corpus = _Corpus(_THREE_MONTHS)
        with patch.object(timeline, "_now_ms", return_value=_TODAY):
            await _run(corpus, start="2026-05-01")

        assert corpus.calls[0]["time_range"] == {
            "source_created_after_ms": _ms("2026-05-01"), "source_created_before_ms": _TODAY,
        }
        assert corpus.calls[1]["time_range"]["source_created_after_ms"] == _ms("2026-05-01")
        assert corpus.calls[-1]["time_range"]["source_created_before_ms"] == _TODAY

    @pytest.mark.asyncio
    async def test_end_alone_covers_the_year_before_it(self) -> None:
        corpus = _Corpus(_THREE_MONTHS)
        with patch.object(timeline, "_now_ms", return_value=_TODAY):
            await _run(corpus, end="2026-04-30")

        assert corpus.calls[0]["time_range"] == {"source_created_before_ms": _end_of("2026-04-30")}
        start = _end_of("2026-04-30") - DEFAULT_LOOKBACK_DAYS * 86_400_000
        assert corpus.calls[1]["time_range"]["source_created_after_ms"] == start
        assert corpus.calls[-1]["time_range"]["source_created_before_ms"] == _end_of("2026-04-30")

    @pytest.mark.asyncio
    async def test_a_match_older_than_a_year_extends_the_span_back(self) -> None:
        corpus = _Corpus({**_THREE_MONTHS, "old": "2024-11-05"})
        with patch.object(timeline, "_now_ms", return_value=_TODAY):
            text = await _run(corpus)

        assert corpus.calls[1]["time_range"]["source_created_after_ms"] == _ms("2024-11-05")
        assert "old yyy" in text

    @pytest.mark.asyncio
    async def test_modified_places_records_by_their_last_update(self) -> None:
        corpus = _Corpus(_THREE_MONTHS)
        await _run(corpus, start="2026-03-01", end="2026-05-31", date_field="modified")

        assert set(corpus.calls[0]["time_range"]) == {"source_updated_after_ms", "source_updated_before_ms"}


class TestRefusals:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(("kwargs", "message"), [
        ({"start": "2026-05-31", "end": "2026-03-01"}, "start must be on or before end"),
        ({"start": "March"}, "start: 'March' is not a valid ISO 8601 date"),
        ({"date_field": "indexed"}, "date_field must be 'created' or 'modified'"),
    ])
    async def test_bad_arguments_name_the_tool_s_own_parameters(self, kwargs: dict[str, str], message: str) -> None:
        corpus = _Corpus(_THREE_MONTHS)
        result = json.loads(await _run(corpus, **kwargs))

        assert result["status"] == "error"
        assert result["message"].startswith(message)
        assert corpus.calls == []

    @pytest.mark.asyncio
    async def test_nothing_anywhere_is_an_empty_result(self) -> None:
        corpus = _Corpus({})
        result = json.loads(await _run(corpus, start="2026-03-01", end="2026-05-31"))

        assert result["status"] == "success" and result["result_count"] == 0

    @pytest.mark.asyncio
    async def test_entity_ids_never_seen_this_turn_search_nothing(self) -> None:
        corpus = _Corpus(_THREE_MONTHS)
        result = json.loads(await _run(corpus, start="2026-03-01", end="2026-05-31", entity_ids=["made-up"]))

        assert result["status"] == "error" and "made-up" in result["message"]
        assert corpus.calls == []


class TestWhatTheModelCanCite:
    @pytest.mark.asyncio
    async def test_state_holds_exactly_what_was_shown(self) -> None:
        corpus = _Corpus(_THREE_MONTHS)
        state = _state(corpus, include_retrieval_context=True, final_results=[{"virtual_record_id": "old", "block_index": 0}])
        text = await _run(corpus, state, start="2026-03-01", end="2026-05-31")

        shown = {vrid for vrid in _THREE_MONTHS if f"{vrid} yyy" in text}
        assert shown == set(_THREE_MONTHS)
        assert {u["virtual_record_id"] for u in state["final_results"]} == shown | {"old"}
        assert set(state["virtual_record_id_to_result"]) == shown
        assert {r["virtual_record_id"] for r in state["tool_records"]} == shown
        assert set(state["retrieved_by"]) == shown
        assert state["known_record_ids"] == {f"rec-{vrid}" for vrid in shown}
        # One citation ref per block shown, and none for the earlier search's block.
        assert len(state["citation_ref_mapper"].ref_to_url) == len(state["final_results"]) - 1

    @pytest.mark.asyncio
    async def test_the_activity_line_counts_periods_and_names_records(self) -> None:
        corpus = _Corpus(_THREE_MONTHS)
        text = await _run(corpus, start="2026-03-01", end="2026-05-31")

        summary = timeline.timeline_result_summary({}, MagicMock(content=text, is_error=False))
        assert summary.startswith("Searched 3 periods, found 4 records")
        assert "Thread apr" in summary


class TestEntityFilters:
    @pytest.mark.asyncio
    async def test_an_entity_filter_that_matches_nothing_is_dropped_once_and_said(self) -> None:
        corpus = _Corpus(_THREE_MONTHS)
        corpus.refuse_groups = {"departments"}
        state = _state(corpus, **{ENTITY_ID_FILTER_KEY_CACHE_KEY: {"dept-1": ("departments", "Engineering")}})
        text = await _run(corpus, state, start="2026-03-01", end="2026-05-31", entity_ids=["dept-1"])

        with_filter = [c for c in corpus.calls if "departments" in c["filter_groups"]]
        without = [c for c in corpus.calls if "departments" not in c["filter_groups"]]
        assert len(with_filter) == len(without) == 4
        assert "these results are NOT limited to it" in text
        assert "apr yyy" in text

    @pytest.mark.asyncio
    async def test_a_project_scoped_chat_with_an_entity_filter_that_matches_nothing_retries(self) -> None:
        corpus = _Corpus(_THREE_MONTHS)
        corpus.refuse_groups = {"departments"}
        state = _state(
            corpus,
            filters={"apps": ["slack-1"], "kb": [], "strictScope": True},
            **{ENTITY_ID_FILTER_KEY_CACHE_KEY: {"dept-1": ("departments", "Engineering")}},
        )
        text = await _run(corpus, state, start="2026-03-01", end="2026-05-31", entity_ids=["dept-1"])

        assert isinstance(text, str) and "apr yyy" in text
        assert "these results are NOT limited to it" in text
        assert all(c["filter_groups"]["strictScope"] is True for c in corpus.calls)
        assert any("departments" not in c["filter_groups"] for c in corpus.calls)

    @pytest.mark.asyncio
    async def test_a_project_scoped_chat_with_a_matching_entity_filter_keeps_both(self) -> None:
        corpus = _Corpus(_THREE_MONTHS)
        state = _state(
            corpus,
            filters={"apps": ["slack-1"], "kb": [], "strictScope": True},
            **{ENTITY_ID_FILTER_KEY_CACHE_KEY: {"dept-1": ("departments", "Engineering")}},
        )
        text = await _run(corpus, state, start="2026-03-01", end="2026-05-31", entity_ids=["dept-1"])

        assert "apr yyy" in text.split("### April 2026", 1)[1]
        assert all(c["filter_groups"]["strictScope"] is True for c in corpus.calls)
        assert all(c["filter_groups"]["departments"] == ["Engineering"] for c in corpus.calls)
        assert "NOT limited" not in text

    @pytest.mark.asyncio
    async def test_a_matching_entity_filter_is_kept(self) -> None:
        corpus = _Corpus(_THREE_MONTHS)
        state = _state(corpus, **{ENTITY_ID_FILTER_KEY_CACHE_KEY: {"dept-1": ("departments", "Engineering")}})
        text = await _run(corpus, state, start="2026-03-01", end="2026-05-31", entity_ids=["dept-1"])

        assert all(c["filter_groups"]["departments"] == ["Engineering"] for c in corpus.calls)
        assert "NOT limited" not in text

    @pytest.mark.asyncio
    async def test_an_empty_record_group_stays_empty(self) -> None:
        corpus = _Corpus(_THREE_MONTHS)
        state = _state(corpus, **{RECORD_SCOPED_ENTITY_CACHE_KEY: {"grp-1": "record_group"}})
        empty = EntitySearchScope(virtual_ids=[], truncated=False)
        with patch.object(timeline, "resolve_entity_virtual_ids", AsyncMock(return_value=empty)):
            result = json.loads(await _run(corpus, state, start="2026-03-01", end="2026-05-31", entity_ids=["grp-1"]))

        assert result["result_count"] == 0 and corpus.calls == []


@pytest.mark.asyncio
async def test_status_lines_are_inside_the_size_and_the_footer_survives() -> None:
    corpus = _Corpus({"mar": "2026-01-03", "oct": "2026-06-20"}, size=3_000, sizes={"mar": 3_000, "oct": 3_000})
    with patch.object(timeline, "TIMELINE_RESULT_CHARS", 7_000), \
            patch.object(timeline, "_now_ms", return_value=_TODAY):
        text = await _run(corpus, start="2026-01-01")

    assert len(text) <= 7_000
    assert text.count("No matching records.") == 4
    assert text.rstrip().endswith("created_after/created_before.")


@pytest.mark.asyncio
async def test_a_period_searched_over_part_of_the_index_is_not_called_empty() -> None:
    notice = "1 of 2 parts of the search index could not be searched, so this search may have missed documents."
    corpus = _Corpus({"mar": "2026-03-03", "may": "2026-05-06"})
    real = corpus.search_with_filters

    async def partial_april(**kwargs: Any) -> dict[str, Any]:  # noqa: ANN401
        time_range = kwargs.get("time_range") or {}
        if time_range.get("source_created_after_ms") == _ms("2026-04-01"):
            corpus.calls.append(kwargs)
            return {"status_code": 200, "searchResults": [], "searchIncomplete": notice}
        return await real(**kwargs)

    corpus.search_with_filters = partial_april  # type: ignore[method-assign]
    text = await _run(corpus, start="2026-03-01", end="2026-05-31")

    april = text.split("### April 2026", 1)[1].split("###", 1)[0]
    assert "No matching records." not in april
    assert notice in april and "Search it again" in april


# ── knowledgegraph__search over a wide span ───────────────────────────────


def _range(after: str, before: str | None = None, field: str = "created") -> dict[str, int]:
    keys = timeline._DATE_FIELDS[field][2:4]
    bounds = {keys[0]: _ms(after)}
    if before is not None:
        bounds[keys[1]] = _end_of(before)
    return bounds


class TestAutoTimelineField:
    @pytest.mark.parametrize(("after", "before", "field"), [
        # 31 days (all of March) stays ranked; one day more is a timeline.
        ("2026-03-01", "2026-03-31", None),
        ("2026-03-01", "2026-04-01", "created"),
        ("2026-02-01", "2026-02-28", None),
        ("2026-01-01", "2026-06-30", "created"),
    ])
    def test_more_than_a_months_whole_days_is_a_timeline(self, after: str, before: str, field: str | None) -> None:
        assert timeline.AUTO_TIMELINE_MIN_DAYS == 31
        assert timeline.auto_timeline_field(_range(after, before)) == field

    def test_the_modified_date_counts_too(self) -> None:
        assert timeline.auto_timeline_field(_range("2026-03-01", "2026-05-31", "modified")) == "modified"

    def test_a_start_alone_runs_to_today_and_a_future_end_is_today(self) -> None:
        with patch.object(timeline, "_now_ms", return_value=_TODAY):
            assert timeline.auto_timeline_field(_range("2026-06-01")) is None
            assert timeline.auto_timeline_field(_range("2026-05-01")) == "created"
            assert timeline.auto_timeline_field(_range("2026-06-01", "2026-12-31")) is None

    def test_an_end_alone_or_no_range_stays_ranked(self) -> None:
        assert timeline.auto_timeline_field({"source_created_before_ms": _ms("2026-05-31")}) is None
        assert timeline.auto_timeline_field(None) is None
        assert timeline.auto_timeline_field({}) is None

    def test_switched_off_it_stays_ranked(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(TIMELINE_SUMMARIES_ENV_VAR, "false")
        assert timeline.auto_timeline_field(_range("2026-01-01", "2026-06-30")) is None


async def _run_search(corpus: _Corpus, state: dict[str, Any] | None = None, **kwargs: Any) -> Any:  # noqa: ANN401
    from app.agents.actions.knowledge_graph.ops import search as search_op

    ranked = AsyncMock(return_value="ranked")

    async def flatten(results, _blob, _org, _mm, vr_map, *_a, **_kw) -> list[dict[str, Any]]:
        vr_map.update({r["virtual_record_id"]: _record(r["virtual_record_id"]) for r in results})
        return corpus.units(results)

    with patch.object(search_op, "_execute_search", ranked), \
            patch(f"{_BUILDER}.get_flattened_results", side_effect=flatten), \
            patch(f"{_BUILDER}.enrich_records_with_graph_context", new_callable=AsyncMock), \
            patch(f"{_BUILDER}.enrich_records_with_address_links", new_callable=AsyncMock), \
            patch("app.modules.transformers.blob_storage.BlobStorage"):
        return await search_op.execute_search(state or _state(corpus), "slack bot problems", **kwargs), ranked


class TestTheSearchToolOverAWideSpan:
    @pytest.mark.asyncio
    async def test_a_quarter_is_searched_period_by_period_and_says_why(self) -> None:
        corpus = _Corpus(_THREE_MONTHS)

        text, ranked = await _run_search(corpus, created_after="2026-03-01", created_before="2026-05-31")

        ranked.assert_not_awaited()
        assert text.startswith('Timeline for "slack bot problems": 3 periods from 2026-03-01 to 2026-05-31')
        assert "Note: This date range covers more than 31 days, so it was searched period by period." in text
        assert "keep the dates to 31 days or less" in text
        assert len(corpus.calls) == 4

    @pytest.mark.asyncio
    async def test_modified_dates_place_records_by_their_last_update(self) -> None:
        corpus = _Corpus(_THREE_MONTHS)

        await _run_search(corpus, modified_after="2026-03-01", modified_before="2026-05-31")

        assert set(corpus.calls[0]["time_range"]) == {"source_updated_after_ms", "source_updated_before_ms"}

    @pytest.mark.asyncio
    @pytest.mark.parametrize("kwargs", [
        {"created_after": "2026-03-01", "created_before": "2026-03-31"},
        {"created_before": "2026-05-31"},
        {},
        # The timeline bounds one date only, so bounds on both stay ranked.
        {"created_after": "2026-03-01", "created_before": "2026-05-31", "modified_after": "2026-04-01"},
        {"created_after": "March"},
    ])
    async def test_otherwise_it_is_the_ranked_search(self, kwargs: dict[str, str]) -> None:
        corpus = _Corpus(_THREE_MONTHS)

        result, ranked = await _run_search(corpus, **kwargs)

        assert result == "ranked"
        ranked.assert_awaited_once()
        assert corpus.calls == []

    @pytest.mark.asyncio
    async def test_a_search_inside_a_resolved_scope_stays_ranked(self) -> None:
        corpus = _Corpus(_THREE_MONTHS)

        result, _ = await _run_search(
            corpus, created_after="2026-03-01", created_before="2026-05-31", scope_virtual_record_ids=["mar"],
        )

        assert result == "ranked"

    @pytest.mark.asyncio
    async def test_switched_off_it_stays_ranked(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(TIMELINE_SUMMARIES_ENV_VAR, "false")
        corpus = _Corpus(_THREE_MONTHS)

        result, _ = await _run_search(corpus, created_after="2026-03-01", created_before="2026-05-31")

        assert result == "ranked"

    def test_its_activity_line_reads_as_a_timeline(self) -> None:
        instance = KnowledgeGraph(state={})
        declared = ToolsetBuilder(instance, name="knowledgegraph", description="", path_prefix="/tools/knowledgegraph").tools
        tool = next(t for t in declared if t.name == "knowledgegraph__search")
        text = 'Timeline for "q": 2 periods from 2026-03-01 to 2026-04-30, oldest first.\nName: Thread a\n'

        summary = tool.summarize_result({"query": "q"}, MagicMock(content=text, is_error=False))

        assert summary.startswith("Searched 2 periods")


class TestEverySearchKeepsTheTurnsScope:
    """A selection below app level and an agent's or project's allow-list
    reach every search of a timeline, as they reach knowledgegraph__search."""

    _SCOPES = [
        pytest.param(
            {"apps": [], "kb": ["kb-1"], "recordGroups": ["proj-a"], "allowedRecordGroups": ["proj-a"],
             "strictScope": True},
            {"kb": ["kb-1"], "recordGroups": ["proj-a"], "allowedRecordGroups": ["proj-a"], "strictScope": True},
            id="record-group",
        ),
        pytest.param(
            {"apps": ["whole-app", "beta"], "kb": [], "records": ["beta-root"], "allowedRecords": ["beta-root"]},
            {"apps": ["whole-app", "beta"], "records": ["beta-root"], "allowedRecords": ["beta-root"]},
            id="records",
        ),
        pytest.param(
            {"apps": [], "kb": [], "records": ["folder-1"], "allowedRecordGroups": ["proj-a"], "strictScope": True},
            {"records": ["folder-1"], "allowedRecordGroups": ["proj-a"], "strictScope": True},
            id="folder",
        ),
    ]

    @staticmethod
    def _assert_kept(corpus: _Corpus, kept: dict[str, Any]) -> None:
        assert len(corpus.calls) == 4
        for call in corpus.calls:
            for key, value in kept.items():
                assert call["filter_groups"][key] == value, key

    @pytest.mark.asyncio
    @pytest.mark.parametrize(("filters", "kept"), _SCOPES)
    async def test_an_automatic_timeline(self, filters: dict[str, Any], kept: dict[str, Any]) -> None:
        corpus = _Corpus(_THREE_MONTHS)

        await _run_search(
            corpus, _state(corpus, filters=filters), created_after="2026-03-01", created_before="2026-05-31",
        )

        self._assert_kept(corpus, kept)

    @pytest.mark.asyncio
    @pytest.mark.parametrize(("filters", "kept"), _SCOPES)
    async def test_the_timeline_tool(self, filters: dict[str, Any], kept: dict[str, Any]) -> None:
        corpus = _Corpus(_THREE_MONTHS)

        await _run(corpus, _state(corpus, filters=filters), start="2026-03-01", end="2026-05-31")

        self._assert_kept(corpus, kept)

    @pytest.mark.asyncio
    async def test_naming_a_source_does_not_drop_a_selection(self) -> None:
        corpus = _Corpus(_THREE_MONTHS)
        filters = {"apps": ["whole-app", "beta"], "kb": [], "records": ["beta-root"], "allowedRecords": ["beta-root"]}

        await _run(corpus, _state(corpus, filters=filters), start="2026-03-01", end="2026-05-31", source_ids=["beta"])

        for call in corpus.calls:
            assert call["filter_groups"]["records"] == ["beta-root"]
            assert call["filter_groups"]["allowedRecords"] == ["beta-root"]

    @pytest.mark.asyncio
    async def test_a_placeholder_agent_keeps_its_own_sources_only_without_a_selection(self) -> None:
        corpus = _Corpus(_THREE_MONTHS)
        bare = _state(corpus, is_placeholder_agent=True, apps=["drive-1"], kb=["kb-9"],
                      filters={"apps": ["synthetic"], "kb": []})
        await _run(corpus, bare, start="2026-03-01", end="2026-05-31")
        assert {(tuple(c["filter_groups"]["apps"]), tuple(c["filter_groups"]["kb"])) for c in corpus.calls} == {
            (("drive-1",), ("kb-9",)),
        }

        corpus.calls.clear()
        selected = _state(corpus, is_placeholder_agent=True, apps=["drive-1"], kb=["kb-9"],
                          filters={"apps": ["drive-1"], "kb": [], "recordGroups": ["folder-x"]})
        await _run(corpus, selected, start="2026-03-01", end="2026-05-31")
        for call in corpus.calls:
            assert call["filter_groups"]["apps"] == ["drive-1"]
            assert call["filter_groups"]["kb"] == []
            assert call["filter_groups"]["recordGroups"] == ["folder-x"]
