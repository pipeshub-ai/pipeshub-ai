"""POST /search lists records found by name whose content is not indexed yet.

The field is additive: ``searchResults`` and ``records`` are what the content
search returns, as before. ``unindexedMatches`` holds records that match the
query by name and have no indexed text, each with its status and a note. A
search that found no indexed content at all (404) answers 200 when there are
such records. ``/retrieve`` is unchanged.
"""
from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# The route module only loads cleanly once app.edition_config is imported first.
import app.edition_config  # noqa: F401
from app.agents.actions.knowledge_graph.ops.outside_dates import (
    DATE_FILTER_SAFETY_NET_ENV_VAR,
)
from app.api.routes import search as search_route
from app.api.routes.search import RetrieveQuery, SearchQuery, retrieve, search
from app.modules.retrieval.title_hits import UnindexedMatch
from app.services.graph_db.interface.graph_db_provider import STRICT_SCOPE_FILTER_KEY
from app.utils.not_indexed import not_indexed_note

ROUTE = "app.api.routes.search"


@pytest.fixture(autouse=True)
def _one_search_per_request(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(DATE_FILTER_SAFETY_NET_ENV_VAR, "false")


def _match(record_id: str = "r2", name: str = "Quarterly planning") -> UnindexedMatch:
    return UnindexedMatch(
        record_id=record_id, name=name, score=0.9, indexing_status="QUEUED",
        connector="Confluence", connector_id="conf-1", record_type="FILE",
    )


def _content() -> dict[str, Any]:
    return {
        "searchResults": [{
            "content": "a passage", "score": 0.8, "virtual_record_id": "vr1", "block_index": 0,
            "metadata": {"recordId": "r1", "virtualRecordId": "vr1"},
        }],
        "records": [{"_key": "r1", "recordName": "Pricing"}],
        "status": "success",
        "status_code": 200,
        "message": "ok",
        "virtual_to_record_map": {"vr1": {"_key": "r1"}},
    }


def _nothing_indexed() -> dict[str, Any]:
    return {
        "searchResults": [], "records": [], "status": "accessible_records_not_found",
        "status_code": 404, "message": "No documents are available for you to search yet.",
    }


def _empty_200() -> dict[str, Any]:
    return {
        "searchResults": [], "records": [], "status": "empty_response",
        "status_code": 200, "message": "No relevant documents found for your search query.",
    }


def _request(user: dict | None = None) -> MagicMock:
    request = MagicMock()
    request.state.user = user or {"userId": "user1", "orgId": "org1"}
    request.app.container.logger.return_value = MagicMock()
    return request


async def _search(
    content: dict[str, Any], by_name: list[UnindexedMatch] | None, **body: Any,  # noqa: ANN401
) -> tuple[int, dict[str, Any], AsyncMock]:
    retrieval = MagicMock()
    retrieval.search_with_filters = AsyncMock(return_value=dict(content))
    lookup = AsyncMock(return_value=by_name)
    with patch(f"{ROUTE}._unindexed_matches", lookup):
        response = await search(
            request=_request(), body=SearchQuery(query="quarterly planning", rewriteQuery=False, **body),
            retrieval_service=retrieval, graph_provider=MagicMock(),
        )
    return response.status_code, json.loads(response.body), lookup


class TestTheAdditiveField:
    @pytest.mark.asyncio
    async def test_matches_are_added_and_the_content_fields_are_unchanged(self) -> None:
        status, plain, _ = await _search(_content(), None)
        status_with, body, _ = await _search(_content(), [_match()])

        assert status == status_with == 200
        assert body.pop("unindexedMatches") == [{
            "recordId": "r2", "recordName": "Quarterly planning", "connector": "Confluence",
            "connectorId": "conf-1", "recordType": "FILE", "indexingStatus": "QUEUED",
            "note": not_indexed_note("QUEUED"),
        }]
        assert body == plain
        assert "unindexedMatches" not in plain

    @pytest.mark.asyncio
    async def test_a_record_the_passages_already_cite_is_not_listed_again(self) -> None:
        _, body, _ = await _search(_content(), [_match("r1", "Pricing"), _match("r2")])
        assert [m["recordId"] for m in body["unindexedMatches"]] == ["r2"]

    @pytest.mark.asyncio
    async def test_nothing_to_add_leaves_the_response_as_it_was(self) -> None:
        _, plain, _ = await _search(_content(), None)
        _, body, _ = await _search(_content(), [])
        assert body == plain

    @pytest.mark.asyncio
    async def test_a_failed_lookup_leaves_the_response_as_it_was(self) -> None:
        _, plain, _ = await _search(_content(), None)
        status, body, lookup = await _search(_content(), None)
        lookup.assert_awaited_once()
        assert status == 200 and body == plain


class TestNothingIndexedMatched:
    @pytest.mark.asyncio
    async def test_matches_by_name_turn_the_404_into_an_answer(self) -> None:
        status, body, _ = await _search(_nothing_indexed(), [_match()])
        assert status == 200
        assert body["status"] == "success" and body["status_code"] == 200
        assert body["searchResults"] == [] and body["records"] == []
        assert "unindexedMatches" in body["message"]
        assert [m["recordId"] for m in body["unindexedMatches"]] == ["r2"]

    @pytest.mark.asyncio
    async def test_a_name_only_match_after_an_empty_200_is_a_success(self) -> None:
        status, body, _ = await _search(_empty_200(), [_match()])
        assert status == 200 and body["status"] == "success"
        assert body["message"].startswith("No indexed content matched; 1 record(s) matched by name only")
        assert [m["recordId"] for m in body["unindexedMatches"]] == ["r2"]

    @pytest.mark.asyncio
    async def test_an_empty_200_without_matches_stays_empty(self) -> None:
        _, body, _ = await _search(_empty_200(), [])
        assert body["status"] == "empty_response" and "unindexedMatches" not in body

    @pytest.mark.asyncio
    async def test_without_matches_the_404_stays(self) -> None:
        status, body, _ = await _search(_nothing_indexed(), [])
        assert status == 404
        assert "unindexedMatches" not in body

    @pytest.mark.asyncio
    async def test_a_failed_lookup_keeps_the_404(self) -> None:
        status, body, _ = await _search(_nothing_indexed(), None)
        assert status == 404 and "unindexedMatches" not in body

    @pytest.mark.asyncio
    async def test_an_error_is_never_turned_into_an_answer(self) -> None:
        failed = {"searchResults": [], "records": [], "status": "search_index_unavailable",
                  "status_code": 503, "message": "down"}
        status, body, _ = await _search(failed, [_match()])
        assert status == 503 and "unindexedMatches" not in body


class TestWhenTheLookupRuns:
    @pytest.mark.asyncio
    async def test_it_gets_the_query_and_the_filters_the_search_ran_with(self) -> None:
        _, _, lookup = await _search(_content(), [], filters={"apps": ["wiki"]})
        args = lookup.await_args.args
        assert args[2] == {"apps": ["wiki"]} and args[3] == "quarterly planning"

    @pytest.mark.asyncio
    @pytest.mark.parametrize("body", [
        {"createdAfter": "2026-01-01"},
        {"modifiedBefore": "2026-12-01"},
        {"timeline": "created", "createdAfter": "2026-01-01"},
        {"filters": {"entityFilters": {"people": ["p1"]}}},
        {"filters": {"topics": ["pricing"]}},
    ])
    async def test_no_lookup_when_dates_or_entities_narrow_the_search(self, body: dict) -> None:
        """Names are not dated or tagged, so a lookup would list records outside the search."""
        _, response, lookup = await _search(_content(), [_match()], **body)
        lookup.assert_not_awaited()
        assert "unindexedMatches" not in response

    @pytest.mark.asyncio
    async def test_retrieve_never_looks_up_names(self) -> None:
        retrieval = MagicMock()
        retrieval.search_with_filters = AsyncMock(return_value=_content())
        lookup = AsyncMock(return_value=[_match()])
        with patch(f"{ROUTE}._unindexed_matches", lookup), patch(f"{ROUTE}.setup_query_transformation"):
            response = await retrieve(
                request=_request(), body=RetrieveQuery(query="quarterly planning"),
                retrieval_service=retrieval, graph_provider=MagicMock(),
            )
        lookup.assert_not_awaited()
        assert "unindexedMatches" not in json.loads(response.body)


class TestTheLookupItself:
    """``_unindexed_matches`` resolves the caller's access like the entity tools do."""

    @staticmethod
    def _patches(store: Any = "store", matches: Any = None) -> tuple[dict[str, Any], MagicMock]:  # noqa: ANN401
        context = MagicMock()
        mocks = {
            "load_entity_vector_store": AsyncMock(return_value=store),
            "get_entity_access_context": AsyncMock(return_value=context),
            "demo_exclusions_for_run": AsyncMock(return_value=frozenset({"demo-1"})),
            "find_unindexed_matches": AsyncMock(return_value=[_match()] if matches is None else matches),
        }
        return mocks, context

    async def _run(self, filter_groups: dict, mocks: dict[str, Any]) -> Any:  # noqa: ANN401
        request = _request({"userId": "user1", "orgId": "org1"})
        with patch.multiple(ROUTE, **mocks):
            return await search_route._unindexed_matches(
                request, MagicMock(name="graph"), filter_groups, "quarterly planning", MagicMock(),
            )

    @pytest.mark.asyncio
    async def test_it_uses_the_callers_sources_limits_and_demo_exclusions(self) -> None:
        mocks, context = self._patches()
        filter_groups = {"apps": ["wiki"], "kb": ["kb-1"], STRICT_SCOPE_FILTER_KEY: True, "connectionApps": ["wiki"]}

        found = await self._run(filter_groups, mocks)

        assert [m.record_id for m in found] == ["r2"]
        kwargs = mocks["get_entity_access_context"].await_args.kwargs
        assert mocks["get_entity_access_context"].await_args.args[0] is None
        assert (kwargs["org_id"], kwargs["user_id"]) == ("org1", "user1")
        assert kwargs["source_ids"] == ["wiki", "kb-1"]
        assert kwargs["strict"] is True
        assert kwargs["exclude_app_ids"] == frozenset({"demo-1"})
        assert kwargs["filters"] is filter_groups
        assert mocks["find_unindexed_matches"].await_args.args[2] is context

    @pytest.mark.asyncio
    async def test_without_an_entity_store_there_is_nothing_to_add(self) -> None:
        mocks, _ = self._patches(store=None)
        assert await self._run({}, mocks) == []
        mocks["get_entity_access_context"].assert_not_awaited()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("failing", ["get_entity_access_context", "find_unindexed_matches", "demo_exclusions_for_run"])
    async def test_a_failure_is_none_and_never_raised(self, failing: str) -> None:
        mocks, _ = self._patches()
        mocks[failing].side_effect = RuntimeError("down")
        assert await self._run({}, mocks) is None
