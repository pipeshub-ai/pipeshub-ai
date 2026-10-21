"""The date-filter safety net (``ops/outside_dates``) and knowledgegraph__search.

A search with date bounds also runs once without them, and the best matches
the dates left out are listed after the results, labelled as written outside
the dates, with their dates. The case it is for: a release page written in
May about the week of 29 December, which a December to January
creation-date filter leaves out.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.actions.knowledge_graph.ops.outside_dates import (
    DATE_FILTER_SAFETY_NET_ENV_VAR,
    MAX_OUTSIDE_DATES_RECORDS,
    OUTSIDE_DATES_SEARCH_LIMIT,
    date_safety_net_enabled,
    outside_bounds,
    outside_dates_payload,
    pick_outside_dates,
    record_dates_label,
    render_outside_dates,
    wants_outside_dates,
)
from app.agents.actions.knowledge_graph.ops.search import execute_search
from app.agents.actions.knowledge_graph.ops.timeline import TIMELINE_SUMMARIES_ENV_VAR
from app.services.graph_db.interface.graph_db_provider import STRICT_SCOPE_FILTER_KEY

_DAY_MS = 24 * 60 * 60 * 1000
_SEARCH = "app.agents.actions.knowledge_graph.ops.search"


def _ms(day: str) -> int:
    return int(datetime.fromisoformat(day).replace(tzinfo=timezone.utc).timestamp() * 1000)


def _record(vrid: str, created: str, modified: str | None = None) -> dict[str, Any]:
    return {
        "_key": f"rec-{vrid}",
        "virtualRecordId": vrid,
        "recordName": f"Doc {vrid}",
        "sourceCreatedAtTimestamp": _ms(created),
        "sourceLastModifiedTimestamp": _ms(modified or created),
    }


def _response(*vrids_and_records: tuple[str, dict[str, Any]], status_code: int = 200) -> dict[str, Any]:
    return {
        "status_code": status_code,
        "searchResults": [
            {"virtual_record_id": vrid, "block_index": 0, "content": f"passage of {vrid}"}
            for vrid, _ in vrids_and_records
        ],
        "virtual_to_record_map": dict(vrids_and_records),
    }


_WEEK = {"source_created_after_ms": _ms("2025-12-28"), "source_created_before_ms": _ms("2026-01-12") + _DAY_MS - 1}


@pytest.fixture(autouse=True)
def _defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(DATE_FILTER_SAFETY_NET_ENV_VAR, raising=False)
    monkeypatch.delenv(TIMELINE_SUMMARIES_ENV_VAR, raising=False)


# ---------------------------------------------------------------------------
# The pieces
# ---------------------------------------------------------------------------


class TestTheSwitch:
    def test_on_by_default(self) -> None:
        assert date_safety_net_enabled()

    @pytest.mark.parametrize("value", ["false", "0", "no", "off", " OFF "])
    def test_off(self, monkeypatch: pytest.MonkeyPatch, value: str) -> None:
        monkeypatch.setenv(DATE_FILTER_SAFETY_NET_ENV_VAR, value)
        assert not date_safety_net_enabled()
        assert not wants_outside_dates(_WEEK)

    def test_blank_stays_on(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(DATE_FILTER_SAFETY_NET_ENV_VAR, "")
        assert date_safety_net_enabled()

    def test_only_a_search_with_dates_wants_it(self) -> None:
        assert wants_outside_dates(_WEEK)
        assert wants_outside_dates({"source_updated_after_ms": 1})
        assert not wants_outside_dates(None)
        assert not wants_outside_dates({})


class TestOutsideBounds:
    def test_created_before_the_start_or_after_the_end(self) -> None:
        assert outside_bounds(_record("a", "2025-12-01"), _WEEK)
        assert outside_bounds(_record("a", "2026-05-21"), _WEEK)
        assert not outside_bounds(_record("a", "2026-01-02"), _WEEK)
        assert not outside_bounds(_record("a", "2026-01-12"), _WEEK), "the end day is inclusive"

    def test_modified_bounds_read_the_last_change(self) -> None:
        since = {"source_updated_after_ms": _ms("2026-04-01")}
        assert outside_bounds(_record("a", "2026-01-02", modified="2026-01-03"), since)
        assert not outside_bounds(_record("a", "2026-01-02", modified="2026-04-02"), since)

    def test_no_date_on_a_bounded_field_is_left_out_by_the_filter(self) -> None:
        record = {"_key": "r", "sourceCreatedAtTimestamp": None, "sourceLastModifiedTimestamp": _ms("2026-01-02")}
        assert outside_bounds(record, _WEEK)
        assert not outside_bounds(record, {"source_updated_after_ms": _ms("2026-01-01")})


class TestPickOutsideDates:
    def test_the_best_few_outside_the_dates_in_rank_order(self) -> None:
        response = _response(
            ("release", _record("release", "2026-05-21")),
            ("standup", _record("standup", "2026-01-02")),
            ("retro", _record("retro", "2026-02-10")),
            ("summary", _record("summary", "2026-03-01")),
            ("older", _record("older", "2025-06-01")),
        )

        picked = pick_outside_dates(response, _WEEK)

        assert len(picked) == MAX_OUTSIDE_DATES_RECORDS == 3
        assert [p.record_id for p in picked] == ["rec-release", "rec-retro", "rec-summary"]

    def test_what_the_bounded_search_shows_is_never_repeated(self) -> None:
        response = _response(
            ("release", _record("release", "2026-05-21")),
            ("retro", _record("retro", "2026-02-10")),
        )

        assert [p.record_id for p in pick_outside_dates(response, _WEEK, shown_virtual_ids={"release"})] == [
            "rec-retro",
        ]
        assert [p.record_id for p in pick_outside_dates(response, _WEEK, shown_record_ids={"rec-retro"})] == [
            "rec-release",
        ]

    def test_a_records_passages_stay_together(self) -> None:
        release = _record("release", "2026-05-21")
        response = {
            "status_code": 200,
            "searchResults": [
                {"virtual_record_id": "release", "block_index": 0, "content": "first"},
                {"metadata": {"virtualRecordId": "retro"}, "content": "retro"},
                {"virtual_record_id": "release", "block_index": 3, "content": "second"},
            ],
            "virtual_to_record_map": {"release": release, "retro": _record("retro", "2026-02-10")},
        }

        picked = pick_outside_dates(response, _WEEK)

        assert [hit["content"] for hit in picked[0].hits] == ["first", "second"]
        assert picked[1].record_id == "rec-retro"

    @pytest.mark.parametrize("response", [None, {"status_code": 503, "searchResults": []}, "nonsense"])
    def test_a_failed_search_names_nothing(self, response: Any) -> None:  # noqa: ANN401
        assert pick_outside_dates(response, _WEEK) == []

    def test_a_hit_without_a_record_is_skipped(self) -> None:
        response = {
            "status_code": 200,
            "searchResults": [{"virtual_record_id": "ghost"}, {"virtual_record_id": "retro"}],
            "virtual_to_record_map": {"retro": _record("retro", "2026-02-10")},
        }
        assert [p.record_id for p in pick_outside_dates(response, _WEEK)] == ["rec-retro"]


class TestLabels:
    def test_dates(self) -> None:
        assert record_dates_label(_record("a", "2026-05-21")) == "created 2026-05-21"
        assert record_dates_label(_record("a", "2026-05-21", modified="2026-06-02")) == (
            "created 2026-05-21, last changed 2026-06-02"
        )
        assert record_dates_label({}) == "no source date"

    def test_the_assistants_list(self) -> None:
        picked = pick_outside_dates(_response(("release", _record("release", "2026-05-21"))), _WEEK)

        text = render_outside_dates(picked, "knowledgegraph__fetch_record")

        assert "written outside your dates" in text
        assert "1. Record ID: rec-release | Doc release | created 2026-05-21 (outside your dates)" in text
        assert "   Match: passage of release" in text
        assert "knowledgegraph__fetch_record" in text

    def test_a_long_passage_is_cut_to_one_line(self) -> None:
        response = _response(("release", _record("release", "2026-05-21")))
        response["searchResults"][0]["content"] = "word\n" * 200

        text = render_outside_dates(pick_outside_dates(response, _WEEK), "fetch")

        match = next(line for line in text.splitlines() if line.startswith("   Match: "))
        assert len(match) <= len("   Match: ") + 240
        assert match.endswith("…")

    def test_the_api_payload_drops_the_graph_id(self) -> None:
        record = {**_record("release", "2026-05-21"), "id": "rec-release"}
        response = _response(("release", record))
        response["searchResults"][0]["metadata"] = {"recordId": "rec-release", "id": "rec-release"}
        response["records"] = [{**record, "linkedRecords": []}]

        payload = outside_dates_payload(pick_outside_dates(response, _WEEK), response)

        assert payload["records"] == [{k: v for k, v in record.items() if k != "id"} | {"linkedRecords": []}]
        assert payload["searchResults"][0]["metadata"] == {"recordId": "rec-release"}
        assert "written outside your dates" in payload["note"]


# ---------------------------------------------------------------------------
# knowledgegraph__search
# ---------------------------------------------------------------------------


class _Index:
    """Records dated at their source, searched as the search tool calls the
    retrieval service: by ``time_range``, best score first, up to ``limit``."""

    def __init__(self, records: dict[str, tuple[str, float]]) -> None:
        self.records = {vrid: (created, score) for vrid, (created, score) in records.items()}
        self.calls: list[dict[str, Any]] = []
        self.fail_without_dates = False

    async def search_with_filters(self, **kwargs: Any) -> dict[str, Any]:  # noqa: ANN401
        self.calls.append(kwargs)
        time_range = kwargs.get("time_range") or {}
        if not time_range and self.fail_without_dates:
            raise RuntimeError("vector store down")
        found = sorted(
            (vrid for vrid, (created, _) in self.records.items()
             if not outside_bounds(_record(vrid, created), time_range)),
            key=lambda vrid: -self.records[vrid][1],
        )[: kwargs["limit"]]
        if not found:
            return {"status_code": 404, "searchResults": [], "records": [], "message": "none"}
        return _response(*((vrid, _record(vrid, self.records[vrid][0])) for vrid in found))


async def _flatten(
    search_results: list[dict[str, Any]],
    blob_store: object,
    org_id: str,
    is_multimodal_llm: bool,
    vr_map: dict[str, Any],
    *args: object,
    **kwargs: object,
) -> list[dict[str, Any]]:
    """A ``get_flattened_results`` stand-in: one unit per hit, its record loaded."""
    units: list[dict[str, Any]] = []
    for hit in search_results:
        vrid = hit["virtual_record_id"]
        vr_map[vrid] = {"id": f"rec-{vrid}", "virtual_record_id": vrid, "record_name": f"Doc {vrid}"}
        units.append({"virtual_record_id": vrid, "block_index": 0, "content": hit.get("content")})
    return units


def _state(index: _Index, **filters: Any) -> dict[str, Any]:  # noqa: ANN401
    retrieval = MagicMock()
    retrieval.search_with_filters = index.search_with_filters
    return {
        "logger": MagicMock(),
        "retrieval_service": retrieval,
        "graph_provider": AsyncMock(),
        # No config service: no keyword (grep) search beside the semantic one.
        "config_service": None,
        "org_id": "o1",
        "user_id": "u1",
        "filters": {"apps": ["app-1"], "kb": [], **filters},
        "final_results": [],
    }


async def _run(state: dict[str, Any], **kwargs: Any) -> str:  # noqa: ANN401
    with patch("app.modules.retrieval.context.builder.get_flattened_results", side_effect=_flatten), \
            patch(f"{_SEARCH}.BlobStorage"):
        result = await execute_search(state, "what shipped that week", **kwargs)
    assert isinstance(result, str)
    return result


_THE_WEEK = {
    "standup": ("2026-01-02", 0.50),
    "chat": ("2025-12-30", 0.45),
    "release": ("2026-05-21", 0.90),
    "retro": ("2026-02-10", 0.70),
    "summary": ("2026-03-01", 0.60),
    "older": ("2025-06-01", 0.55),
}
_DATES = {"created_after": "2025-12-28", "created_before": "2026-01-12"}


class TestADatedSearch:
    @pytest.mark.asyncio
    async def test_lists_the_matches_written_outside_the_dates_after_the_results(self) -> None:
        index = _Index(_THE_WEEK)
        state = _state(index)

        result = await _run(state, **_DATES)

        assert "Doc standup" in result and "Doc chat" in result
        tail = result[result.index("Also matching, but written outside your dates"):]
        assert "1. Record ID: rec-release | Doc release | created 2026-05-21 (outside your dates)" in tail
        assert "2. Record ID: rec-retro | Doc retro | created 2026-02-10" in tail
        assert "3. Record ID: rec-summary | Doc summary | created 2026-03-01" in tail
        assert "rec-older" not in result, "three at most"
        # A line at the top points to the list.
        assert result.index("Note: 3 matching records written outside your dates") < result.index("Doc standup")
        # The model may now read them.
        assert {"rec-release", "rec-retro", "rec-summary"} <= state["known_record_ids"]

    @pytest.mark.asyncio
    async def test_the_extra_search_keeps_every_filter_but_the_dates(self) -> None:
        index = _Index(_THE_WEEK)
        state = _state(index, recordGroups=["g1"], **{STRICT_SCOPE_FILTER_KEY: True, "entityFilters": {"x": ["y"]}})

        await _run(state, **_DATES)

        assert len(index.calls) == 2
        bounded, unbounded = sorted(index.calls, key=lambda call: call["time_range"] is None)
        assert bounded["time_range"] == {
            "source_created_after_ms": _ms("2025-12-28"),
            "source_created_before_ms": _ms("2026-01-12") + _DAY_MS - 1,
        }
        assert unbounded["time_range"] is None
        assert unbounded["limit"] == OUTSIDE_DATES_SEARCH_LIMIT
        assert unbounded["filter_groups"] == bounded["filter_groups"]
        assert unbounded["filter_groups"][STRICT_SCOPE_FILTER_KEY] is True
        assert unbounded["filter_groups"]["entityFilters"] == {"x": ["y"]}
        assert unbounded["filter_groups"]["recordGroups"] == ["g1"]
        assert unbounded["virtual_record_ids_from_tool"] == bounded["virtual_record_ids_from_tool"]
        assert (unbounded["user_id"], unbounded["org_id"], unbounded["queries"]) == (
            bounded["user_id"], bounded["org_id"], bounded["queries"],
        )

    @pytest.mark.asyncio
    async def test_an_allow_list_scope_stays_on_the_extra_search(self) -> None:
        index = _Index(_THE_WEEK)

        await execute_search(
            _state(index), "what shipped that week",
            scope_virtual_record_ids=["release", "standup"], **_DATES,
        )

        assert len(index.calls) == 2
        assert all(call["virtual_record_ids_from_tool"] == ["release", "standup"] for call in index.calls)

    @pytest.mark.asyncio
    async def test_each_named_source_is_searched_without_dates_in_its_own_scope(self) -> None:
        index = _Index(_THE_WEEK)
        state = _state(index)
        state["filters"] = {"apps": ["app-1", "app-2"], "kb": []}

        await _run(state, source_ids=["app-1", "app-2"], **_DATES)

        bounded = [call["filter_groups"] for call in index.calls if call["time_range"]]
        unbounded = [call["filter_groups"] for call in index.calls if not call["time_range"]]
        assert len(bounded) == len(unbounded) == 2
        assert sorted(map(str, unbounded)) == sorted(map(str, bounded))

    @pytest.mark.asyncio
    async def test_nothing_in_the_dates_still_names_what_is_outside(self) -> None:
        index = _Index({"release": ("2026-05-21", 0.9)})
        state = _state(index)

        parsed = json.loads(await _run(state, **_DATES))

        assert parsed["result_count"] == 0
        assert parsed["message"].startswith("No results found")
        assert "1. Record ID: rec-release | Doc release | created 2026-05-21" in parsed["message"]
        assert "rec-release" in state["known_record_ids"]

    @pytest.mark.asyncio
    async def test_a_failed_extra_search_leaves_the_result_as_it_was(self) -> None:
        index = _Index(_THE_WEEK)
        index.fail_without_dates = True

        result = await _run(_state(index), **_DATES)

        assert "Doc standup" in result
        assert "outside your dates" not in result

    @pytest.mark.asyncio
    async def test_modified_dates_get_the_same_net(self) -> None:
        index = _Index(_THE_WEEK)

        result = await _run(_state(index), modified_after="2026-01-01", modified_before="2026-01-31")

        assert "Also matching, but written outside your dates" in result
        assert "Record ID: rec-release" in result


class TestUnchanged:
    @pytest.mark.asyncio
    async def test_without_dates(self) -> None:
        index = _Index(_THE_WEEK)

        result = await _run(_state(index))

        assert len(index.calls) == 1
        assert "outside your dates" not in result

    @pytest.mark.asyncio
    async def test_switched_off(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(DATE_FILTER_SAFETY_NET_ENV_VAR, "false")
        index = _Index(_THE_WEEK)

        result = await _run(_state(index), **_DATES)

        assert len(index.calls) == 1
        assert "outside your dates" not in result



class TestATimeline:
    """A span over a month runs period by period. One plain search without
    dates runs beside the period searches, never a second timeline."""

    @pytest.mark.asyncio
    async def test_gets_one_search_without_dates_and_lists_what_the_span_left_out(self) -> None:
        index = _Index(_THE_WEEK)
        state = _state(index)

        result = await _run(state, created_after="2025-12-01", created_before="2026-01-31")

        assert result.startswith("Timeline for")
        unbounded = [call for call in index.calls if not call["time_range"]]
        assert len(unbounded) == 1
        assert unbounded[0]["limit"] == OUTSIDE_DATES_SEARCH_LIMIT
        assert all(call["filter_groups"] == unbounded[0]["filter_groups"] for call in index.calls)
        assert "Note: 3 matching records written outside your dates" in result
        tail = result[result.index("Also matching, but written outside your dates"):]
        assert [line.split(" | ")[0] for line in tail.splitlines() if "Record ID" in line] == [
            "1. Record ID: rec-release", "2. Record ID: rec-retro", "3. Record ID: rec-summary",
        ]
        assert {"rec-release", "rec-retro", "rec-summary"} <= state["known_record_ids"]

    @pytest.mark.asyncio
    async def test_switched_off_it_is_the_timeline_alone(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(DATE_FILTER_SAFETY_NET_ENV_VAR, "false")
        index = _Index(_THE_WEEK)

        result = await _run(_state(index), created_after="2025-12-01", created_before="2026-01-31")

        assert result.startswith("Timeline for")
        assert all(call["time_range"] for call in index.calls)
        assert "outside your dates" not in result
