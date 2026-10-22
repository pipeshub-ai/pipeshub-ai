"""Vector store faults injected under the real search path.

Everything from ``search_with_filters`` down to the fan-out runs for real; only
the provider's ``query_nearest_points`` (and the embedding model) is faked, so
these show what a caller sees when the store times out under load.
"""

from collections.abc import Iterator
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import grpc
import pytest

import app.modules.retrieval.retrieval_service as mod
from app.exceptions.fastapi_responses import Status
from app.modules.retrieval.retrieval_service import (
    SEARCH_INDEX_TIMEOUT_MESSAGE,
    SEARCH_INDEX_UNREACHABLE_MESSAGE,
    RetrievalService,
)
from app.services.graph_db.interface.graph_db_provider import AccessCheck
from app.services.vector_db.qdrant.errors import classify_qdrant_search_error

pytestmark = pytest.mark.asyncio

_RECORD = {
    "_key": "rec1",
    "virtualRecordId": "vr1",
    "origin": "google_drive",
    "recordName": "Runbook",
    "webUrl": "https://example.com/doc",
    "mimeType": "application/pdf",
    "connectorName": "gdrive",
}


def _rpc_error(code: grpc.StatusCode) -> grpc.aio.AioRpcError:
    return grpc.aio.AioRpcError(code, grpc.aio.Metadata(), grpc.aio.Metadata(), details="timed out")


def _point(pid: str = "p1", vrid: str = "vr1") -> MagicMock:
    point = MagicMock()
    point.id = pid
    point.score = 0.9
    point.payload = {"page_content": "rollback steps", "metadata": {"virtualRecordId": vrid, "orgId": "o1"}}
    return point


@pytest.fixture(autouse=True)
def _clear_user_cache() -> Iterator[None]:
    mod._user_cache.clear()
    yield
    mod._user_cache.clear()


@pytest.fixture(autouse=True)
def _no_backoff() -> Iterator[None]:
    with patch("app.utils.retry.asyncio.sleep", new_callable=AsyncMock):
        yield


@pytest.fixture
def service(logger, mock_config_service, mock_vector_db_service, mock_graph_provider, mock_blob_store) -> RetrievalService:
    with patch("app.modules.retrieval.retrieval_service.SparseEmbedder"):
        registry = MagicMock()
        registry.strategy_name = "single"
        registry.resolve_for_query = AsyncMock(return_value=["records"])
        svc = RetrievalService(
            logger=logger,
            config_service=mock_config_service,
            collection_registry=registry,
            vector_db_service=mock_vector_db_service,
            graph_provider=mock_graph_provider,
            blob_store=mock_blob_store,
        )
    dense = AsyncMock()
    dense.aembed_query = AsyncMock(return_value=[0.1])
    svc.get_embedding_model_instance = AsyncMock(return_value=dense)
    # The Qdrant provider's own classifier, so gRPC codes are judged as in production.
    mock_vector_db_service.transient_search_error = MagicMock(side_effect=classify_qdrant_search_error)
    mock_graph_provider.get_accessible_virtual_record_ids.return_value = {"vr1": "rec1"}
    # Every hit is checked by virtual record id before it is served.
    mock_graph_provider.get_user_by_user_id.return_value = {"_key": "user-key", "email": "test@example.com"}
    mock_graph_provider.check_access = AsyncMock(
        side_effect=lambda _user_key, _org_id, *, node_ids=(), virtual_record_ids=(), **_kwargs: AccessCheck(
            node_ids=frozenset(node_ids),
            records_by_vrid={v: "rec1" for v in virtual_record_ids if v == "vr1"},
        )
    )
    mock_graph_provider.get_records_by_record_ids.return_value = [dict(_RECORD)]
    return svc


async def _search(svc: RetrievalService) -> dict[str, Any]:
    return await svc.search_with_filters(queries=["how do I roll back"], user_id="u1", org_id="o1")


async def test_a_timeout_then_success_returns_the_results(service, mock_vector_db_service) -> None:
    mock_vector_db_service.query_nearest_points = AsyncMock(
        side_effect=[_rpc_error(grpc.StatusCode.DEADLINE_EXCEEDED), [[_point()]]]
    )

    result = await _search(service)

    assert result["status"] == Status.SUCCESS.value
    assert [r["metadata"]["recordId"] for r in result["searchResults"]] == ["rec1"]
    assert "searchIncomplete" not in result
    assert mock_vector_db_service.query_nearest_points.await_count == 2


async def test_a_bad_request_is_not_retried_and_stays_a_server_error(service, mock_vector_db_service) -> None:
    mock_vector_db_service.query_nearest_points = AsyncMock(
        side_effect=_rpc_error(grpc.StatusCode.INVALID_ARGUMENT)
    )

    result = await _search(service)

    assert result["status"] == Status.ERROR.value
    assert result["status_code"] == 500
    assert mock_vector_db_service.query_nearest_points.await_count == 1


@pytest.mark.parametrize(("code", "message"), [
    (grpc.StatusCode.DEADLINE_EXCEEDED, SEARCH_INDEX_TIMEOUT_MESSAGE),
    (grpc.StatusCode.UNAVAILABLE, SEARCH_INDEX_UNREACHABLE_MESSAGE),
])
async def test_failing_twice_says_why_and_is_not_an_empty_result(
    service, mock_vector_db_service, code, message,
) -> None:
    mock_vector_db_service.query_nearest_points = AsyncMock(side_effect=_rpc_error(code))

    result = await _search(service)

    assert result["status"] == Status.SEARCH_INDEX_UNAVAILABLE.value
    assert result["status"] != Status.EMPTY_RESPONSE.value
    assert result["status_code"] == 503
    assert result["message"] == message
    assert result["searchResults"] == []
    assert mock_vector_db_service.query_nearest_points.await_count == 2


async def test_one_collection_failing_keeps_the_others_results_and_says_so(
    service, mock_vector_db_service,
) -> None:
    service.collection_registry.resolve_for_query = AsyncMock(return_value=["records", "slack_records"])

    async def _query(collection_name: str, requests: list) -> list[list]:
        if collection_name == "slack_records":
            raise _rpc_error(grpc.StatusCode.DEADLINE_EXCEEDED)
        return [[_point()]]

    mock_vector_db_service.query_nearest_points = AsyncMock(side_effect=_query)

    result = await _search(service)

    assert result["status"] == Status.SUCCESS.value
    assert [r["metadata"]["recordId"] for r in result["searchResults"]] == ["rec1"]
    assert result["searchIncomplete"] == (
        "1 of 2 parts of the search index could not be searched, so this search may have missed documents."
    )


async def test_a_collection_with_a_non_transient_error_is_not_retried_but_still_noted(
    service, mock_vector_db_service,
) -> None:
    """Retrying a bad request repeats it; dropping it silently would hide that
    part of the index was never searched."""
    service.collection_registry.resolve_for_query = AsyncMock(return_value=["records", "slack_records"])
    calls: list[str] = []

    async def _query(collection_name: str, requests: list) -> list[list]:
        calls.append(collection_name)
        if collection_name == "slack_records":
            raise _rpc_error(grpc.StatusCode.INVALID_ARGUMENT)
        return [[_point()]]

    mock_vector_db_service.query_nearest_points = AsyncMock(side_effect=_query)

    result = await _search(service)

    assert result["status"] == Status.SUCCESS.value
    assert [r["metadata"]["recordId"] for r in result["searchResults"]] == ["rec1"]
    assert calls.count("slack_records") == 1
    assert result["searchIncomplete"].startswith("1 of 2 parts of the search index could not be searched")


async def test_nothing_found_where_the_search_ran_is_not_reported_as_nothing_found(
    service, mock_vector_db_service,
) -> None:
    service.collection_registry.resolve_for_query = AsyncMock(return_value=["records", "slack_records"])

    async def _query(collection_name: str, requests: list) -> list[list]:
        if collection_name == "slack_records":
            raise _rpc_error(grpc.StatusCode.UNAVAILABLE)
        return [[]]

    mock_vector_db_service.query_nearest_points = AsyncMock(side_effect=_query)

    result = await _search(service)

    assert result["status"] == Status.EMPTY_RESPONSE.value
    assert result["searchIncomplete"].startswith("1 of 2 parts of the search index could not be searched")
    assert result["message"].startswith(result["searchIncomplete"])


async def test_an_ordinary_empty_search_carries_no_failure(service, mock_vector_db_service) -> None:
    mock_vector_db_service.query_nearest_points = AsyncMock(return_value=[[]])

    result = await _search(service)

    assert result["status"] == Status.EMPTY_RESPONSE.value
    assert "searchIncomplete" not in result
    assert mock_vector_db_service.query_nearest_points.await_count == 1
