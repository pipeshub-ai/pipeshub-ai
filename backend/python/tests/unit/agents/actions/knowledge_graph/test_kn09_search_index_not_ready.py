"""KN-09g..k (C3.24, WP-100): a search over a record the person picked that is still being indexed says so.

Search finds only indexed content, so a search scoped to a record that is queued or being indexed comes back empty,
and "No results found" reads as "the record holds nothing". When the search names its records (a selection of one
record, or of a file, ``recordsExact`` and ``records``) and finds nothing, each such record the person may read and
that is still indexing is named with the plain line fetch, lookup and navigate already give (KN-09a..f). ADR-OWD-02
stays: a record the person cannot read, or one that does not exist, adds nothing, so the answer is the same as for
a search over nothing.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.actions.knowledge_graph.ops.search import execute_search
from app.config.constants.arangodb import ProgressStatus
from app.utils.not_indexed import INDEX_NOT_READY
from tests.support.bound_org import bind_test_org
from tests.support.record_access import InMemoryRecordAccess

if TYPE_CHECKING:
    from collections.abc import Iterator

PENDING_ID = "6c1d3a52-2f0e-4c8e-9d7a-3b5f0e1a2c44"
DONE_ID = "7d2e4b63-3f1f-4d9f-8e8b-4c6f1f2b3d55"
MISSING_ID = "0a0a0a0a-0000-4000-8000-000000000000"
NAME = "Q3 sales review.pdf"
LINE = f"{NAME} is still being indexed, so its contents cannot be searched yet; try again in a few minutes."
NO_RESULTS = "No results found"


@pytest.fixture(autouse=True)
def _bound_org() -> Iterator[str]:
    """The search tool runs inside the run or request that bound its org (the state names the same one)."""
    yield from bind_test_org("o1")


def _documents(status: str) -> dict[str, dict[str, Any]]:
    return {
        PENDING_ID: {"_key": PENDING_ID, "recordName": NAME, "connectorId": "drive-1", "indexingStatus": status},
        DONE_ID: {
            "_key": DONE_ID, "recordName": "Q2 review.pdf", "connectorId": "drive-1",
            "indexingStatus": ProgressStatus.COMPLETED.value,
        },
    }


def _graph(status: str, *, readable: tuple[str, ...] = (PENDING_ID, DONE_ID)) -> MagicMock:
    graph = MagicMock()
    graph.config_service = None
    records = InMemoryRecordAccess().user("u1", "o1")
    for record_id in readable:
        records = records.readable("u1", "o1", record_id)
    records.serve(graph)
    graph.access = records
    documents = _documents(status)

    async def document(document_key: str, collection: str) -> dict[str, Any] | None:
        return documents.get(document_key)

    graph.get_document = AsyncMock(side_effect=document)
    return graph


def _state(graph: MagicMock, filters: dict[str, Any]) -> dict[str, Any]:
    retrieval = AsyncMock()
    retrieval.search_with_filters.return_value = {"status_code": 200, "searchResults": [], "virtual_to_record_map": {}}
    return {
        "logger": MagicMock(),
        "retrieval_service": retrieval,
        "graph_provider": graph,
        "config_service": None,
        "org_id": "o1",
        "user_id": "u1",
        "filters": {"apps": ["drive-1"], "kb": [], **filters},
        "final_results": [],
    }


async def _search(graph: MagicMock, filters: dict[str, Any]) -> dict[str, Any]:
    with patch("app.agents.actions.knowledge_graph.ops.time_range.parse_time_range", return_value=({}, None)):
        return json.loads(await execute_search(_state(graph, filters), "pricing"))


@pytest.mark.asyncio
@pytest.mark.parametrize("key", ["recordsExact", "records"])
@pytest.mark.parametrize(
    "status", [ProgressStatus.NOT_STARTED.value, ProgressStatus.QUEUED.value, ProgressStatus.IN_PROGRESS.value]
)
async def test_kn_09g_a_search_over_a_readable_record_still_indexing_answers_index_not_ready(key: str, status: str) -> None:
    body = await _search(_graph(status), {key: [PENDING_ID]})

    assert body["status"] == INDEX_NOT_READY
    assert body["records"] == [{"recordId": PENDING_ID, "name": NAME, "indexingStatus": status}]
    assert body["message"].startswith(LINE)
    assert body["results"] == [] and body["result_count"] == 0


@pytest.mark.asyncio
async def test_kn_09h_an_unreadable_record_still_indexing_searches_exactly_like_one_that_does_not_exist() -> None:
    """ADR-OWD-02: no access reads as not found; nothing about the record, its status least of all, is said."""
    hidden = await _search(_graph(ProgressStatus.IN_PROGRESS.value, readable=()), {"recordsExact": [PENDING_ID]})
    missing = await _search(_graph(ProgressStatus.IN_PROGRESS.value), {"recordsExact": [MISSING_ID]})

    assert hidden == missing
    assert hidden["status"] == "success" and hidden["message"] == NO_RESULTS
    assert NAME not in json.dumps(hidden) and "indexed" not in json.dumps(hidden)


@pytest.mark.asyncio
async def test_kn_09i_beside_an_indexed_record_the_pending_one_is_named_and_the_search_stays_a_success() -> None:
    body = await _search(_graph(ProgressStatus.QUEUED.value), {"recordsExact": [PENDING_ID, DONE_ID, MISSING_ID]})

    assert body["status"] == "success"
    assert body["message"].startswith(NO_RESULTS)
    assert LINE in body["message"]
    assert "Q2 review.pdf" not in body["message"] and MISSING_ID not in body["message"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status", [ProgressStatus.AUTO_INDEX_OFF.value, ProgressStatus.FAILED.value, ProgressStatus.EMPTY.value]
)
async def test_kn_09j_a_record_that_waiting_will_not_index_keeps_the_plain_empty_answer(status: str) -> None:
    """Only a status that clears by itself says "try again in a few minutes" (as fetch, KN-09c)."""
    body = await _search(_graph(status), {"recordsExact": [PENDING_ID]})

    assert body["status"] == "success" and body["message"] == NO_RESULTS


@pytest.mark.asyncio
async def test_kn_09k_a_search_that_names_no_record_reads_nothing_more() -> None:
    graph = _graph(ProgressStatus.QUEUED.value)

    body = await _search(graph, {})

    assert body["message"] == NO_RESULTS
    graph.get_document.assert_not_awaited()
    assert graph.access.asked_about == []


_BUILDER = "app.modules.retrieval.context.builder"
_SEARCH = "app.agents.actions.knowledge_graph.ops.search"


def _shown_record(record_id: str, name: str) -> dict[str, Any]:
    return {
        "id": record_id,
        "_id": f"records/{record_id}",
        "virtual_record_id": "v-done",
        "record_name": name,
        "context_metadata": f"Record ID: {record_id}\nName: {name}",
        "frontend_url": "https://app.example",
        "block_containers": {"blocks": [{"index": 0, "type": "text", "data": "Q2 pricing went up 4%."}], "block_groups": []},
    }


async def _search_with_results(graph: MagicMock, filters: dict[str, Any], shown_id: str = DONE_ID) -> str:
    """A search whose retrieval found the indexed record's passage, rendered by the real search code."""
    state = _state(graph, filters)
    state["retrieval_service"].search_with_filters.return_value = {
        "status_code": 200, "searchResults": [{"virtual_record_id": "v-done"}], "virtual_to_record_map": {},
    }
    unit = {
        "virtual_record_id": "v-done", "block_index": 0, "block_type": "text",
        "content": "Q2 pricing went up 4%.", "metadata": {}, "score": 0.9,
    }

    async def flatten(_results, _blob, _org, _mm, vr_map, *_a, **_kw) -> list[dict[str, Any]]:
        vr_map.update({"v-done": _shown_record(shown_id, "Q2 review.pdf")})
        return [dict(unit)]

    with patch("app.agents.actions.knowledge_graph.ops.time_range.parse_time_range", return_value=({}, None)), \
            patch(f"{_BUILDER}.get_flattened_results", side_effect=flatten), \
            patch(f"{_BUILDER}.enrich_records_with_graph_context", new_callable=AsyncMock), \
            patch(f"{_SEARCH}.BlobStorage"):
        result = await execute_search(state, "pricing")
    assert isinstance(result, str)
    return result


@pytest.mark.asyncio
@pytest.mark.parametrize("key", ["recordsExact", "records"])
async def test_kn_09l_a_search_with_results_names_the_picked_record_still_indexing(key: str) -> None:
    """WP100-FU-3: results from one picked record do not hide that another picked one is still being indexed."""
    text = await _search_with_results(_graph(ProgressStatus.QUEUED.value), {key: [PENDING_ID, DONE_ID, MISSING_ID]})

    assert "Q2 pricing went up 4%." in text
    assert LINE in text
    assert text.count(LINE) == 1
    assert MISSING_ID not in text


@pytest.mark.asyncio
async def test_kn_09m_with_results_an_unreadable_or_settled_record_adds_nothing() -> None:
    """ADR-OWD-02 holds with results too: no line for a record the person cannot read or one indexing will not fix."""
    hidden = await _search_with_results(
        _graph(ProgressStatus.IN_PROGRESS.value, readable=(DONE_ID,)), {"recordsExact": [PENDING_ID, DONE_ID]},
    )
    failed = await _search_with_results(_graph(ProgressStatus.FAILED.value), {"recordsExact": [PENDING_ID, DONE_ID]})

    for text in (hidden, failed):
        assert "Q2 pricing went up 4%." in text
        assert NAME not in text and "still being indexed" not in text


@pytest.mark.asyncio
async def test_kn_09n_a_free_text_search_with_results_reads_no_indexing_status() -> None:
    graph = _graph(ProgressStatus.QUEUED.value)

    text = await _search_with_results(graph, {})

    assert "still being indexed" not in text
    graph.get_document.assert_not_awaited()


@pytest.mark.asyncio
async def test_kn_09o_a_record_whose_content_the_results_show_is_not_called_indexing() -> None:
    """A record being indexed again still has its earlier content searchable: what is shown is not "still indexing"."""
    text = await _search_with_results(
        _graph(ProgressStatus.IN_PROGRESS.value), {"recordsExact": [PENDING_ID]}, shown_id=PENDING_ID,
    )

    assert "Q2 pricing went up 4%." in text
    assert "still being indexed" not in text
