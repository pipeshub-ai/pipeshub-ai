"""POST /api/v1/search with ``timeline``: the timeline tool's period-by-period
search, through the plain search's own retrieval call."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

import app.edition_config  # noqa: F401
from app.agents.actions.knowledge_graph.ops.timeline import (
    PERIOD_SEARCH_LIMIT,
    SPAN_SEARCH_LIMIT,
    TIMELINE_SUMMARIES_ENV_VAR,
)
from app.api.routes.search import SearchQuery, search

_DAY_MS = 24 * 60 * 60 * 1000


def _ms(day: str) -> int:
    return int(datetime.fromisoformat(day).replace(tzinfo=timezone.utc).timestamp() * 1000)


class _Index:
    """Records dated at their source, searched the way the retrieval service
    is called by the route: by ``time_range``, best score first, up to
    ``limit``, in the search API's response shape."""

    def __init__(self, records: dict[str, tuple[str, float]]) -> None:
        # vrid -> (created day, score); modified is a day after created.
        self.records = {vrid: (_ms(day), score) for vrid, (day, score) in records.items()}
        self.calls: list[dict[str, Any]] = []
        self.raises = False

    async def search_with_filters(self, **kwargs: Any) -> dict[str, Any]:  # noqa: ANN401
        self.calls.append(kwargs)
        if self.raises:
            raise RuntimeError("vector store down")
        time_range = kwargs.get("time_range") or {}

        def inside(created: int) -> bool:
            modified = created + _DAY_MS
            return (
                time_range.get("source_created_after_ms", 0) <= created
                <= time_range.get("source_created_before_ms", 2**62)
                and time_range.get("source_updated_after_ms", 0) <= modified
                <= time_range.get("source_updated_before_ms", 2**62)
            )

        found = sorted(
            (vrid for vrid, (created, _) in self.records.items() if inside(created)),
            key=lambda vrid: -self.records[vrid][1],
        )[: kwargs["limit"]]
        if not found:
            return {"searchResults": [], "records": [], "status": "accessible_records_not_found",
                    "status_code": 404, "message": "No accessible documents"}
        docs = {
            vrid: {
                "_key": f"rec-{vrid}",
                "recordName": f"Doc {vrid}",
                "sourceCreatedAtTimestamp": self.records[vrid][0],
                "sourceLastModifiedTimestamp": self.records[vrid][0] + _DAY_MS,
            }
            for vrid in found
        }
        return {
            "searchResults": [
                {
                    "content": f"passage of {vrid}",
                    "score": self.records[vrid][1],
                    "virtual_record_id": vrid,
                    "block_index": 0,
                    "metadata": {"recordId": f"rec-{vrid}", "virtualRecordId": vrid},
                }
                for vrid in found
            ],
            "records": list(docs.values()),
            "status": "success",
            "status_code": 200,
            "message": "Query processed successfully. Relevant records retrieved.",
            "virtual_to_record_map": docs,
            "omittedPassages": {f"rec-{found[0]}": 2},
        }


def _request() -> MagicMock:
    request = MagicMock()
    request.state.user = {"userId": "user1", "orgId": "org1"}
    request.app.container.logger.return_value = MagicMock()
    return request


async def _search(body: SearchQuery, index: _Index) -> tuple[int, dict[str, Any]]:
    retrieval = MagicMock()
    retrieval.search_with_filters = index.search_with_filters
    response = await search(
        request=_request(), body=body, retrieval_service=retrieval, graph_provider=MagicMock(),
    )
    return response.status_code, json.loads(response.body)


# A quarter where the best matches bunch in March: a plain search of three
# returns only March, so April and May are lost.
_QUARTER = {
    "m1": ("2025-03-03", 0.95), "m2": ("2025-03-10", 0.94), "m3": ("2025-03-20", 0.93),
    "a1": ("2025-04-15", 0.40),
    "y1": ("2025-05-28", 0.30), "y2": ("2025-05-02", 0.35),
}


def _quarter(**fields: Any) -> SearchQuery:  # noqa: ANN401
    return SearchQuery(
        query="rollout", rewriteQuery=False, timeline="created",
        createdAfter="2025-03-01", createdBefore="2025-05-31", **fields,
    )


@pytest.fixture(autouse=True)
def _switch_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(TIMELINE_SUMMARIES_ENV_VAR, raising=False)


class TestTheField:
    def test_left_out_by_default(self) -> None:
        assert SearchQuery(query="q").timeline is None

    @pytest.mark.parametrize("value", ["created", "modified"])
    def test_takes_the_date_to_place_records_by(self, value: str) -> None:
        assert SearchQuery(query="q", timeline=value).timeline == value

    @pytest.mark.parametrize("value", ["true", "updated", ""])
    def test_refuses_anything_else(self, value: str) -> None:
        with pytest.raises(ValidationError):
            SearchQuery(query="q", timeline=value)


class TestLeftOut:
    @pytest.mark.asyncio
    async def test_a_plain_search_runs_once_and_says_nothing_of_a_timeline(self) -> None:
        index = _Index(_QUARTER)

        status, body = await _search(
            SearchQuery(query="rollout", rewriteQuery=False, limit=3,
                        createdAfter="2025-03-01", createdBefore="2025-05-31"),
            index,
        )

        assert status == 200
        assert len(index.calls) == 1
        assert "timeline" not in body
        assert [h["virtual_record_id"] for h in body["searchResults"]] == ["m1", "m2", "m3"]

    @pytest.mark.asyncio
    async def test_switched_off_the_field_is_ignored(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(TIMELINE_SUMMARIES_ENV_VAR, "false")
        index = _Index(_QUARTER)

        status, body = await _search(_quarter(limit=3), index)

        assert status == 200
        assert len(index.calls) == 1
        assert "timeline" not in body


class TestTimeline:
    @pytest.mark.asyncio
    async def test_every_month_of_the_span_gets_its_own_search_and_label(self) -> None:
        index = _Index(_QUARTER)

        status, body = await _search(_quarter(limit=3), index)

        assert status == 200
        periods = body["timeline"]["periods"]
        assert [p["label"] for p in periods] == [
            "March 2025 (2025-03-01 to 2025-03-31)",
            "April 2025 (2025-04-01 to 2025-04-30)",
            "May 2025 (2025-05-01 to 2025-05-31)",
        ]
        assert [(p["start"], p["end"]) for p in periods] == [
            ("2025-03-01", "2025-03-31"), ("2025-04-01", "2025-04-30"), ("2025-05-01", "2025-05-31"),
        ]
        assert [p["records"] for p in periods] == [3, 1, 2]
        assert body["timeline"]["dateField"] == "created"
        assert (body["timeline"]["start"], body["timeline"]["end"]) == ("2025-03-01", "2025-05-31")
        assert body["timeline"]["notes"] == []

    @pytest.mark.asyncio
    async def test_matches_come_in_date_order_each_labelled_with_its_period(self) -> None:
        index = _Index(_QUARTER)

        _, body = await _search(_quarter(limit=3), index)

        hits = body["searchResults"]
        assert [(h["virtual_record_id"], h["timelinePeriod"]) for h in hits] == [
            ("m1", 0), ("m2", 0), ("m3", 0), ("a1", 1), ("y2", 2), ("y1", 2),
        ]
        keys = [r["_key"] for r in body["records"]]
        assert sorted(keys) == sorted(f"rec-{v}" for v in _QUARTER)
        assert set(body["virtual_to_record_map"]) == set(_QUARTER)
        assert body["omittedPassages"]

    @pytest.mark.asyncio
    async def test_every_search_is_the_plain_searchs_call_with_its_permissions_and_filters(self) -> None:
        index = _Index(_QUARTER)
        filters = {"apps": ["slack-1"], "kb": []}

        await _search(_quarter(limit=3, filters=filters), index)

        # One search of the whole span, then one per month.
        assert len(index.calls) == 4
        for call in index.calls:
            assert call["user_id"] == "user1"
            assert call["org_id"] == "org1"
            assert call["filter_groups"] == filters
            assert call["knowledge_search"] is True
            assert call["queries"] == ["rollout"]
        ranges = sorted(
            (c["time_range"]["source_created_after_ms"], c["time_range"]["source_created_before_ms"])
            for c in index.calls
        )
        assert (_ms("2025-03-01"), _ms("2025-06-01") - 1) in ranges
        assert (_ms("2025-04-01"), _ms("2025-05-01") - 1) in ranges

    @pytest.mark.asyncio
    async def test_limit_is_the_matches_per_period(self) -> None:
        index = _Index(_QUARTER)

        await _search(_quarter(limit=2), index)
        limits = sorted(c["limit"] for c in index.calls)
        assert limits == [2, 2, 2, SPAN_SEARCH_LIMIT]

        index.calls.clear()
        await _search(_quarter(), index)
        limits = sorted(c["limit"] for c in index.calls)
        assert limits == [PERIOD_SEARCH_LIMIT] * 3 + [SPAN_SEARCH_LIMIT]

    @pytest.mark.asyncio
    async def test_bounds_on_the_other_date_stay_on_every_search(self) -> None:
        index = _Index(_QUARTER)

        _, body = await _search(_quarter(limit=3, modifiedAfter="2025-04-01"), index)

        for call in index.calls:
            assert call["time_range"]["source_updated_after_ms"] == _ms("2025-04-01")
        assert {h["virtual_record_id"] for h in body["searchResults"]} == {"a1", "y1", "y2"}
        assert body["timeline"]["periods"][0]["records"] == 0

    @pytest.mark.asyncio
    async def test_modified_places_records_by_their_last_change(self) -> None:
        index = _Index(_QUARTER)

        _, body = await _search(
            SearchQuery(query="rollout", rewriteQuery=False, timeline="modified",
                        modifiedAfter="2025-03-01", modifiedBefore="2025-05-31"),
            index,
        )

        assert body["timeline"]["dateField"] == "modified"
        for call in index.calls:
            assert "source_updated_after_ms" in call["time_range"]
            assert "source_created_after_ms" not in call["time_range"]

    @pytest.mark.asyncio
    async def test_without_a_start_it_reaches_back_a_year_and_says_so(self) -> None:
        index = _Index(_QUARTER)

        status, body = await _search(
            SearchQuery(query="rollout", rewriteQuery=False, timeline="created", createdBefore="2025-05-31"),
            index,
        )

        assert status == 200
        assert body["timeline"]["start"] == "2024-05-31"
        assert len(body["timeline"]["notes"]) == 1
        assert "No start date was given" in body["timeline"]["notes"][0]
        assert {h["virtual_record_id"] for h in body["searchResults"]} == set(_QUARTER)

    @pytest.mark.asyncio
    async def test_nothing_anywhere_answers_as_the_plain_search_would(self) -> None:
        index = _Index({})

        status, body = await _search(_quarter(), index)

        assert status == 404
        assert body["searchResults"] == []
        assert "timeline" not in body

    @pytest.mark.asyncio
    async def test_a_quiet_period_is_listed_with_no_records(self) -> None:
        index = _Index({"m1": ("2025-03-03", 0.9), "y1": ("2025-05-28", 0.3)})

        status, body = await _search(_quarter(), index)

        assert status == 200
        assert [p["records"] for p in body["timeline"]["periods"]] == [1, 0, 1]

    @pytest.mark.asyncio
    async def test_a_start_in_the_future_is_a_400_before_any_search(self) -> None:
        index = _Index(_QUARTER)

        with pytest.raises(HTTPException) as exc_info:
            await _search(
                SearchQuery(query="q", rewriteQuery=False, timeline="created", createdAfter="2999-01-01"),
                index,
            )

        assert exc_info.value.status_code == 400
        assert "createdAfter" in exc_info.value.detail
        assert index.calls == []

    @pytest.mark.asyncio
    async def test_every_search_failing_is_a_500(self) -> None:
        index = _Index(_QUARTER)
        index.raises = True

        with pytest.raises(HTTPException) as exc_info:
            await _search(_quarter(), index)

        assert exc_info.value.status_code == 500


def _status(code: int, message: str = "") -> dict[str, Any]:
    return {"searchResults": [], "records": [], "status": "x", "status_code": code, "message": message}


class _Scripted:
    """Answers the whole-span search (the one asking for ``SPAN_SEARCH_LIMIT``)
    and the period searches separately, the whole span after a delay so the
    periods finish first."""

    def __init__(self, whole: dict[str, Any] | Exception, period: Any) -> None:  # noqa: ANN401
        self.whole = whole
        self.period = period

    async def search_with_filters(self, **kwargs: Any) -> dict[str, Any]:  # noqa: ANN401
        if kwargs["limit"] == SPAN_SEARCH_LIMIT:
            await asyncio.sleep(0.05)
            if isinstance(self.whole, Exception):
                raise self.whole
            return dict(self.whole)
        answer = self.period(kwargs["time_range"]) if callable(self.period) else self.period
        return dict(answer)


async def _scripted(index: _Scripted, body: SearchQuery | None = None) -> tuple[int, dict[str, Any]]:
    return await _search(body or _quarter(limit=3), index)  # type: ignore[arg-type]


_CAVEAT = "Some periods could not be searched in full, so finding nothing does not show the information is missing."


class TestWhenNothingIsFound:
    @pytest.mark.asyncio
    async def test_the_whole_span_searchs_failure_wins_over_a_quicker_empty_month(self) -> None:
        status, body = await _scripted(
            _Scripted(whole=_status(503, "vector store unavailable"), period=_status(404, "No accessible documents")),
        )

        assert status == 503
        assert body["message"] == "vector store unavailable"

    @pytest.mark.asyncio
    async def test_a_refused_filter_is_passed_on_as_the_plain_search_would(self) -> None:
        status, body = await _scripted(
            _Scripted(whole=_status(422, "unknown connector"), period=_status(404)),
        )

        assert status == 422
        assert body["message"] == "unknown connector"

    @pytest.mark.asyncio
    async def test_when_the_whole_span_search_raised_a_periods_failure_is_passed_on(self) -> None:
        status, _ = await _scripted(
            _Scripted(whole=RuntimeError("down"), period=_status(503, "vector store unavailable")),
        )

        assert status == 503

    @pytest.mark.asyncio
    async def test_when_the_whole_span_search_raised_and_months_were_empty_it_is_a_500(self) -> None:
        with pytest.raises(HTTPException) as exc_info:
            await _scripted(_Scripted(whole=RuntimeError("down"), period=_status(404)))

        assert exc_info.value.status_code == 500

    @pytest.mark.asyncio
    async def test_a_period_that_could_not_be_searched_keeps_empty_from_meaning_missing(self) -> None:
        april = _ms("2025-04-01")

        def period(time_range: dict[str, int]) -> dict[str, Any]:
            if time_range["source_created_after_ms"] == april:
                return _status(503, "vector store unavailable")
            return _status(200, "No results")

        status, body = await _scripted(_Scripted(whole=_status(200, "No results"), period=period))

        assert status == 200
        assert [p.get("searchFailed", False) for p in body["timeline"]["periods"]] == [False, True, False]
        caveat = "Some periods could not be searched in full, so finding nothing does not show the information is missing."
        assert caveat in body["timeline"]["notes"]
        assert body["message"] == f"No results found in any period. {caveat}"
        assert "knowledgegraph__search" not in json.dumps(body)

    @pytest.mark.asyncio
    async def test_empty_months_that_were_all_searched_carry_no_caveat(self) -> None:
        status, body = await _scripted(_Scripted(whole=_status(200, "No results"), period=_status(200, "No results")))

        assert status == 200
        assert body["timeline"]["notes"] == []
        assert body["message"] == "No results found in any period."


class TestAFailureIsNeverReportedAsNoRecords:
    """One search answers 404 and another fails, nothing is found: the
    failure is what the caller gets, whichever search it came from."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("code", [422, 500, 503])
    async def test_a_months_failure_wins_over_the_whole_spans_404(self, code: int) -> None:
        april = _ms("2025-04-01")

        def period(time_range: dict[str, int]) -> dict[str, Any]:
            return _status(code, "failed") if time_range["source_created_after_ms"] == april else _status(404)

        status, body = await _scripted(_Scripted(whole=_status(404, "No accessible documents"), period=period))

        assert status == code
        assert body["message"] == "failed"

    @pytest.mark.asyncio
    async def test_with_no_failure_the_whole_spans_own_404_is_passed_on(self) -> None:
        status, body = await _scripted(_Scripted(whole=_status(404, "whole span"), period=_status(404, "a month")))

        assert status == 404
        assert body["message"] == "whole span"

    @pytest.mark.asyncio
    async def test_a_raised_whole_span_search_beside_an_empty_and_a_404_month_is_not_a_negative(self) -> None:
        april = _ms("2025-04-01")

        def period(time_range: dict[str, int]) -> dict[str, Any]:
            return _status(404) if time_range["source_created_after_ms"] == april else _status(200, "No results")

        status, body = await _scripted(_Scripted(whole=RuntimeError("down"), period=period))

        assert status == 200
        assert _CAVEAT in body["timeline"]["notes"]


class TestTheWholeSpanSearchCountsForTheCaveat:
    """With no start, only the whole-span search covers undated records and
    those older than the year a timeline reaches back."""

    _NO_START = SearchQuery(
        query="rollout", rewriteQuery=False, timeline="created", createdBefore="2025-05-31", limit=3,
    )

    @pytest.mark.asyncio
    @pytest.mark.parametrize("whole", [
        _status(503, "vector store unavailable"),
        {**_status(200, "No results"), "searchIncomplete": "Only part of the index was searched."},
    ])
    async def test_a_failed_or_partial_whole_span_search_keeps_empty_periods_from_meaning_missing(
        self, whole: dict[str, Any],
    ) -> None:
        status, body = await _scripted(
            _Scripted(whole=whole, period=_status(200, "No results")), self._NO_START,
        )

        assert status == 200
        assert _CAVEAT in body["timeline"]["notes"]
        assert body["message"] == f"No results found in any period. {_CAVEAT}"

    @pytest.mark.asyncio
    async def test_a_whole_span_search_that_ran_in_full_adds_no_caveat(self) -> None:
        status, body = await _scripted(
            _Scripted(whole=_status(200, "No results"), period=_status(200, "No results")), self._NO_START,
        )

        assert status == 200
        assert _CAVEAT not in body["timeline"]["notes"]
