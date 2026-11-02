"""The search API's date-filter safety net (``ops/outside_dates``).

A ranked search with date bounds also runs once without them, and the best
matches the dates left out come back apart from the results, in
``outsideDates``, each with its dates. The case it is for: a release page
written in May about the week of 29 December, which an agent's December to
January creation-date filter leaves out.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock

import pytest

import app.edition_config  # noqa: F401
from app.agents.actions.knowledge_graph.ops.outside_dates import (
    DATE_FILTER_SAFETY_NET_ENV_VAR,
    OUTSIDE_DATES_SEARCH_LIMIT,
)
from app.agents.actions.knowledge_graph.ops.timeline import TIMELINE_SUMMARIES_ENV_VAR
from app.api.routes.search import SearchQuery, search
from app.services.graph_db.interface.graph_db_provider import STRICT_SCOPE_FILTER_KEY


def _ms(day: str) -> int:
    return int(datetime.fromisoformat(day).replace(tzinfo=timezone.utc).timestamp() * 1000)


class _Index:
    """Records dated at their source, searched as the route calls the
    retrieval service: by ``time_range``, best score first, up to ``limit``."""

    def __init__(self, records: dict[str, tuple[str, float]]) -> None:
        self.records = {vrid: (_ms(day), score) for vrid, (day, score) in records.items()}
        self.calls: list[dict[str, Any]] = []
        self.fail_without_dates = False

    async def search_with_filters(self, **kwargs: Any) -> dict[str, Any]:  # noqa: ANN401
        self.calls.append(kwargs)
        time_range = kwargs.get("time_range") or {}
        if not time_range and self.fail_without_dates:
            raise RuntimeError("vector store down")

        def inside(created: int) -> bool:
            # Last changed when created.
            return (
                time_range.get("source_created_after_ms", 0) <= created
                <= time_range.get("source_created_before_ms", 2**62)
                and time_range.get("source_updated_after_ms", 0) <= created
                <= time_range.get("source_updated_before_ms", 2**62)
            )

        found = sorted(
            (vrid for vrid, (created, _) in self.records.items() if inside(created)),
            key=lambda vrid: -self.records[vrid][1],
        )[: kwargs["limit"]]
        if not found:
            return {"searchResults": [], "records": [], "status": "accessible_records_not_found",
                    "status_code": 404, "message": "No documents are available for you to search yet."}
        docs = {
            vrid: {
                "_key": f"rec-{vrid}",
                "id": f"rec-{vrid}",
                "recordName": f"Doc {vrid}",
                "sourceCreatedAtTimestamp": self.records[vrid][0],
                "sourceLastModifiedTimestamp": self.records[vrid][0],
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
                    "metadata": {"recordId": f"rec-{vrid}", "virtualRecordId": vrid, "id": f"rec-{vrid}"},
                }
                for vrid in found
            ],
            "records": list(docs.values()),
            "status": "success",
            "status_code": 200,
            "message": "Query processed successfully. Relevant records retrieved.",
            "virtual_to_record_map": docs,
        }


# The week of 29 December, and what was written about it later.
_WEEK = {
    "standup": ("2026-01-02", 0.50),
    "chat": ("2025-12-30", 0.45),
    "release": ("2026-05-21", 0.90),  # the weekly release page, written in May
    "retro": ("2026-02-10", 0.70),
    "summary": ("2026-03-01", 0.60),
    "older": ("2025-06-01", 0.55),
    "weak": ("2026-04-01", 0.10),
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


def _week(**fields: Any) -> SearchQuery:  # noqa: ANN401
    return SearchQuery(**{
        "query": "what shipped that week", "rewriteQuery": False, "limit": 10,
        "createdAfter": "2025-12-28", "createdBefore": "2026-01-12", **fields,
    })


@pytest.fixture(autouse=True)
def _defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(DATE_FILTER_SAFETY_NET_ENV_VAR, raising=False)
    monkeypatch.delenv(TIMELINE_SUMMARIES_ENV_VAR, raising=False)


class TestADatedSearch:
    @pytest.mark.asyncio
    async def test_adds_the_best_matches_written_outside_the_dates(self) -> None:
        index = _Index(_WEEK)

        status, body = await _search(_week(), index)

        assert status == 200
        # The results are what the dates allow, as before.
        assert [hit["virtual_record_id"] for hit in body["searchResults"]] == ["standup", "chat"]
        outside = body["outsideDates"]
        assert "written outside your dates" in outside["note"]
        # The best three from outside, best first; never more.
        assert [r["_key"] for r in outside["records"]] == ["rec-release", "rec-retro", "rec-summary"]
        assert outside["records"][0]["sourceCreatedAtTimestamp"] == _ms("2026-05-21")
        assert [h["metadata"]["recordId"] for h in outside["searchResults"]] == [
            "rec-release", "rec-retro", "rec-summary",
        ]

    @pytest.mark.asyncio
    async def test_the_second_search_differs_only_in_dates_and_limit(self) -> None:
        index = _Index(_WEEK)
        filters = {
            "apps": ["c1"], "kb": ["kb1"], "recordGroups": ["g1"],
            STRICT_SCOPE_FILTER_KEY: True, "entityFilters": {"people": ["p1"]},
        }

        await _search(_week(filters=filters), index)

        assert len(index.calls) == 2
        bounded, unbounded = sorted(index.calls, key=lambda call: call["time_range"] is None)
        assert bounded["time_range"] == {
            "source_created_after_ms": _ms("2025-12-28"),
            "source_created_before_ms": _ms("2026-01-12") + 24 * 60 * 60 * 1000 - 1,
        }
        assert unbounded["time_range"] is None
        assert unbounded["limit"] == OUTSIDE_DATES_SEARCH_LIMIT
        # The same caller, query, sources, selection, allow-list and entity scope.
        same = {k: v for k, v in bounded.items() if k not in ("time_range", "limit")}
        assert {k: v for k, v in unbounded.items() if k not in ("time_range", "limit")} == same
        assert unbounded["filter_groups"] == filters
        assert unbounded["user_id"] == "user1"
        assert unbounded["org_id"] == "org1"

    @pytest.mark.asyncio
    async def test_strips_the_graph_id_as_the_gateway_does_for_the_results(self) -> None:
        status, body = await _search(_week(), _Index(_WEEK))

        assert status == 200
        assert all("id" not in record for record in body["outsideDates"]["records"])
        assert all("id" not in hit["metadata"] for hit in body["outsideDates"]["searchResults"])

    @pytest.mark.asyncio
    async def test_nothing_from_outside_adds_nothing(self) -> None:
        index = _Index({"standup": ("2026-01-02", 0.5), "chat": ("2025-12-30", 0.4)})

        status, body = await _search(_week(), index)

        assert status == 200
        assert "outsideDates" not in body
        assert len(index.calls) == 2

    @pytest.mark.asyncio
    async def test_a_record_inside_the_dates_is_never_listed_as_outside(self) -> None:
        # Ranked below what the bounded search returned (limit 1), it is a
        # matter of ranking, not of dates.
        index = _Index({"standup": ("2026-01-02", 0.5), "chat": ("2025-12-30", 0.4),
                        "release": ("2026-05-21", 0.3)})

        _, body = await _search(_week(limit=1), index)

        assert [hit["virtual_record_id"] for hit in body["searchResults"]] == ["standup"]
        assert [r["_key"] for r in body["outsideDates"]["records"]] == ["rec-release"]

    @pytest.mark.asyncio
    async def test_nothing_inside_the_dates_answers_with_what_is_outside(self) -> None:
        index = _Index({"release": ("2026-05-21", 0.9)})

        status, body = await _search(_week(), index)

        # The bounded search alone answers 404, which a caller reads as an error.
        assert status == 200
        assert body["status_code"] == 200
        assert body["searchResults"] == []
        assert "Nothing in your dates matched" in body["message"]
        assert [r["_key"] for r in body["outsideDates"]["records"]] == ["rec-release"]

    @pytest.mark.asyncio
    async def test_nothing_anywhere_is_still_a_404(self) -> None:
        status, body = await _search(_week(), _Index({}))

        assert status == 404
        assert "outsideDates" not in body

    @pytest.mark.asyncio
    async def test_a_failed_second_search_leaves_the_results_as_they_were(self) -> None:
        index = _Index(_WEEK)
        index.fail_without_dates = True

        status, body = await _search(_week(), index)

        assert status == 200
        assert [hit["virtual_record_id"] for hit in body["searchResults"]] == ["standup", "chat"]
        assert "outsideDates" not in body

    @pytest.mark.asyncio
    async def test_a_modified_date_bound_gets_the_same_net(self) -> None:
        index = _Index(_WEEK)
        # "off": open-ended, the span runs to today and would be a timeline.
        body = SearchQuery(query="q", rewriteQuery=False, modifiedAfter="2026-04-15", timeline="off")

        _, result = await _search(body, index)

        assert [hit["virtual_record_id"] for hit in result["searchResults"]] == ["release"]
        assert [r["_key"] for r in result["outsideDates"]["records"]] == [
            "rec-retro", "rec-summary", "rec-older",
        ]


class TestUnchanged:
    @pytest.mark.asyncio
    async def test_without_dates(self) -> None:
        index = _Index(_WEEK)

        _, body = await _search(SearchQuery(query="q", rewriteQuery=False, limit=3), index)

        assert len(index.calls) == 1
        assert "outsideDates" not in body

    @pytest.mark.asyncio
    async def test_switched_off(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(DATE_FILTER_SAFETY_NET_ENV_VAR, "false")
        index = _Index(_WEEK)

        _, body = await _search(_week(), index)

        assert len(index.calls) == 1
        assert "outsideDates" not in body

    @pytest.mark.asyncio
    async def test_a_timeline_gets_one_plain_search_without_dates(self) -> None:
        # Over a month, so it runs period by period. Beside those searches,
        # one ranked search without dates; never a second timeline.
        index = _Index(_WEEK)
        body = SearchQuery(
            query="q", rewriteQuery=False, createdAfter="2025-12-01", createdBefore="2026-02-28",
        )

        status, result = await _search(body, index)

        assert status == 200
        assert "timeline" in result
        unbounded = [call for call in index.calls if not call["time_range"]]
        assert len(unbounded) == 1
        assert unbounded[0]["limit"] == OUTSIDE_DATES_SEARCH_LIMIT
        assert [r["_key"] for r in result["outsideDates"]["records"]] == [
            "rec-release", "rec-summary", "rec-older",
        ]

    @pytest.mark.asyncio
    async def test_a_wide_span_searched_ranked_gets_the_net(self) -> None:
        index = _Index(_WEEK)
        body = SearchQuery(
            query="q", rewriteQuery=False, timeline="off",
            createdAfter="2025-12-01", createdBefore="2026-02-28",
        )

        _, result = await _search(body, index)

        assert len(index.calls) == 2
        assert [r["_key"] for r in result["outsideDates"]["records"]][:1] == ["rec-release"]
