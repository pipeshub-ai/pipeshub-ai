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
from app.modules.retrieval import degraded_notice
from app.modules.retrieval.retrieval_service import (
    SEARCH_INDEX_TIMEOUT_MESSAGE,
    SEARCH_INDEX_UNREACHABLE_MESSAGE,
    RetrievalService,
)
from app.services.graph_db.interface.graph_db_provider import (
    AccessCheck,
    AccessibleContainers,
)
from app.services.vector_db.models import VectorDBCapabilities
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


async def test_a_degraded_answer_from_the_store_is_flagged_in_the_response(service, mock_vector_db_service) -> None:
    point = _point()
    point.degraded = "admission"
    mock_vector_db_service.query_nearest_points = AsyncMock(return_value=[[point]])

    result = await _search(service)

    assert result["status"] == Status.SUCCESS.value
    assert result["searchDegraded"] is True


async def test_a_complete_answer_carries_no_degraded_flag(service, mock_vector_db_service) -> None:
    point = _point()
    point.degraded = None
    mock_vector_db_service.query_nearest_points = AsyncMock(return_value=[[point]])

    assert "searchDegraded" not in await _search(service)


async def test_a_degraded_collection_whose_hits_fall_below_the_cut_still_flags_the_answer(service, mock_vector_db_service) -> None:
    strong, weak = _point("strong"), _point("weak")
    strong.score, strong.degraded = 0.9, None
    weak.score, weak.degraded = 0.1, "timeout"

    async def by_collection(collection_name: str, requests: list) -> list[list]:
        return [[strong]] if collection_name == "full" else [[weak]]

    mock_vector_db_service.query_nearest_points = AsyncMock(side_effect=by_collection)
    plan = mod._QueryPlan(collections=["full", "partial"])

    hits = await service._run_searches(["q"], None, 1, "o1", "u1", [[0.1]], [None], plan=plan)

    assert len(hits) == 1  # the weak hit was trimmed ...
    assert plan.degraded is True  # ... and the flag was kept


async def test_a_deeper_read_for_source_diversity_passes_its_degraded_flag_on(service) -> None:
    plan = mod._QueryPlan(dense=[[0.1]], sparse=[None], collections=["records"])

    async def deeper_read(*_args: object, plan: mod._QueryPlan, **_kwargs: object) -> list:
        plan.degraded = True
        return []

    service._execute_parallel_searches = AsyncMock(side_effect=deeper_read)
    service._keep_permitted_hits = AsyncMock(return_value={})

    await service._diversity_candidates(
        ["q"], None, 30, "o1", "u1", plan, containers=None, enumerated={"vr1": "rec1"}, by_record_ids=AsyncMock(),
        record_ids_first=False, record_cut=None, allow_requery=True, user_key="user-key",
        scope_connector_ids=None, scopes=(),
    )

    assert plan.degraded is True


def _partial_and_empty(reason: str = "timeout") -> AsyncMock:
    """A provider whose partial search found nothing, and says so without any hit to mark."""
    async def query(collection_name: str, requests: list) -> list[list]:
        degraded_notice.note(reason)
        return [[] for _ in requests]

    return AsyncMock(side_effect=query)


@pytest.fixture
def tracked_request() -> Iterator[None]:
    with degraded_notice.tracking():      # every list of the module, reset after: a sync fixture's context is the worker's own
        yield


async def test_an_empty_partial_answer_flags_the_dense_search(service, mock_vector_db_service, tracked_request) -> None:
    mock_vector_db_service.query_nearest_points = _partial_and_empty()

    result = await _search(service)

    assert result["searchDegraded"] is True
    assert degraded_notice.was_degraded() is True


async def test_an_empty_partial_answer_says_so_in_words(service, mock_vector_db_service, tracked_request) -> None:
    """An empty answer the store gave up on reads as such to every reader (agents read the message and ``searchIncomplete``, not the flag)."""
    mock_vector_db_service.query_nearest_points = _partial_and_empty("overload")

    result = await _search(service)

    assert result["status"] == Status.EMPTY_RESPONSE.value
    assert result["searchIncomplete"] == mod.SEARCH_DEGRADED_MESSAGE
    assert result["message"].startswith(mod.SEARCH_DEGRADED_MESSAGE)


async def test_a_degraded_answer_with_hits_says_so_in_words(service, mock_vector_db_service) -> None:
    point = _point()
    point.degraded = "timeout"
    mock_vector_db_service.query_nearest_points = AsyncMock(return_value=[[point]])

    assert (await _search(service))["searchIncomplete"] == mod.SEARCH_DEGRADED_MESSAGE


async def test_an_empty_partial_answer_flags_the_dense_search_outside_a_tracked_request(service, mock_vector_db_service) -> None:
    mock_vector_db_service.query_nearest_points = _partial_and_empty()

    assert (await _search(service))["searchDegraded"] is True


async def test_a_complete_empty_answer_is_not_flagged(service, mock_vector_db_service, tracked_request) -> None:
    mock_vector_db_service.query_nearest_points = AsyncMock(return_value=[[]])

    assert "searchDegraded" not in await _search(service)


async def test_an_empty_partial_answer_flags_the_lexical_search(service, mock_vector_db_service, tracked_request) -> None:
    mock_vector_db_service.get_capabilities = MagicMock(return_value=VectorDBCapabilities(supports_server_side_text_search=True))
    mock_vector_db_service.query_nearest_points = _partial_and_empty()
    service._capabilities = mock_vector_db_service.get_capabilities()
    service._container_filter_enabled = AsyncMock(return_value=False)

    result = await service.search_lexical(["rollback"], user_id="u1", org_id="o1")

    assert result.degraded is True


async def test_a_complete_lexical_search_is_not_flagged(service, mock_vector_db_service, tracked_request) -> None:
    mock_vector_db_service.query_nearest_points = AsyncMock(return_value=[[]])
    service._capabilities = VectorDBCapabilities(supports_server_side_text_search=True)
    service._container_filter_enabled = AsyncMock(return_value=False)

    assert (await service.search_lexical(["rollback"], user_id="u1", org_id="o1")).degraded is False


async def test_an_exact_answer_of_a_space_without_its_bit_index_says_nothing_was_missed(service, mock_vector_db_service) -> None:
    """``quantization`` flags an answer read exactly (or from the index with a deeper rerank): nothing a full search finds is missing."""
    point = _point()
    point.degraded = "quantization"
    mock_vector_db_service.query_nearest_points = AsyncMock(return_value=[[point]])

    result = await _search(service)

    assert result["searchDegraded"] is True
    assert "searchIncomplete" not in result


async def test_an_empty_answer_whose_word_search_hit_its_cap_says_part_of_it_did_not_run(service, mock_vector_db_service, tracked_request) -> None:
    mock_vector_db_service.query_nearest_points = _partial_and_empty("partial")

    result = await _search(service)

    assert result["searchIncomplete"] == mod.SEARCH_PARTIAL_MESSAGE
    assert result["message"].startswith(mod.SEARCH_PARTIAL_MESSAGE)


@pytest.mark.parametrize(("reasons", "said"), [
    (set(), None), ({"quantization"}, None), ({"timeout"}, "time"), ({"admission"}, "time"), ({"overload"}, "time"),
    ({"partial"}, "part"), ({"error"}, "part"), ({"sizing"}, "part"), ({"a-reason-of-another-store"}, "part"), ({"quantization", "timeout"}, "time"),
])
def test_the_notice_is_true_for_the_reasons_the_stores_gave(reasons: set[str], said: str | None) -> None:
    plan = mod._QueryPlan(degraded=bool(reasons), degraded_reasons=set(reasons))
    assert plan.incomplete_notice() == {None: None, "time": mod.SEARCH_DEGRADED_MESSAGE, "part": mod.SEARCH_PARTIAL_MESSAGE}[said]


async def test_a_requery_that_stopped_short_does_not_mark_the_attempt_that_was_kept(service, mock_graph_provider, monkeypatch: pytest.MonkeyPatch) -> None:
    """The partial search, like the failed collections, is the kept attempt's: a wider re-query that came back worse and stopped short is dropped with it."""
    hits = [{"score": 0.9 - i / 10, "content": "c", "metadata": {"virtualRecordId": f"v{i}", "recordId": f"r{i}"}} for i in range(3)]
    calls = []

    async def search(*_a: object, plan: mod._QueryPlan, **_k: object) -> list[dict]:
        calls.append(1)
        if len(calls) == 1:
            return hits
        plan.degraded = True
        plan.degraded_reasons.add("timeout")
        return hits[:1]
    service._execute_parallel_searches = search
    monkeypatch.setattr(service, "_should_requery", lambda **_k: len(calls) == 1)
    monkeypatch.setattr(mod, "narrow_access_fallback_enabled", lambda: False)
    mock_graph_provider.check_access = AsyncMock(side_effect=lambda *a, virtual_record_ids, **k: AccessCheck(records_by_vrid={v: v for v in virtual_record_ids}))
    plan = mod._QueryPlan()

    kept, _, _ = await service._search_and_adjudicate(["q"], None, 3, "o1", "u1", AccessibleContainers(), allow_requery=True, scope_connector_ids=None, plan=plan,
                                                     user_key="k", compare=False)

    assert len(calls) == 2 and len(kept) == 3                         # the re-query ran and the first attempt was kept
    assert plan.degraded is False and plan.degraded_reasons == set()
    assert plan.incomplete_notice() is None


async def test_two_searches_at_once_each_report_only_their_own_partial_search(service, mock_vector_db_service, tracked_request) -> None:
    """Searches of one request run side by side (a turn's parallel tools, a timeline's periods): the one that timed out gets the sentence, the one that
    finished in full does not, though it finished after the other noted its timeout."""
    import asyncio

    noted = asyncio.Event()

    async def query(collection_name: str, requests: list) -> list[list]:
        if collection_name == "late":
            degraded_notice.note("timeout")
            noted.set()
            return [[] for _ in requests]
        await noted.wait()              # finishes after the other search noted its timeout
        return [[] for _ in requests]

    mock_vector_db_service.query_nearest_points = AsyncMock(side_effect=query)
    timed_out: list[str] = []
    complete: list[str] = []
    await asyncio.gather(service._fan_out_searches(["full"], [MagicMock()], 10, degraded=complete),
                         service._fan_out_searches(["late"], [MagicMock()], 10, degraded=timed_out))

    assert timed_out == ["timeout"] and complete == []
    assert mod._QueryPlan(degraded=True, degraded_reasons=set(timed_out)).incomplete_notice() == mod.SEARCH_DEGRADED_MESSAGE
    assert degraded_notice.was_degraded()                  # the request still knows a search of it was partial


async def test_the_shadow_comparisons_partial_search_is_its_own(service, mock_vector_db_service, tracked_request) -> None:
    """The shadow comparison repeats a search in a task of its own: a timeout of it reaches neither the request's answer nor a search beside it."""
    import asyncio

    noted = asyncio.Event()

    async def query(collection_name: str, requests: list) -> list[list]:
        if mod._SHADOW_SCOPE_FILTER.get() is not None:              # the shadow's search
            degraded_notice.note("timeout")
            noted.set()
        else:
            await noted.wait()
        return [[] for _ in requests]

    mock_vector_db_service.query_nearest_points = AsyncMock(side_effect=query)
    started: list[asyncio.Task] = []

    class Shadow:
        def submit(self, org_id, user_key, returned, accessible, run_scoped, check, served) -> None:
            started.append(asyncio.get_running_loop().create_task(run_scoped(object())))

    service._shadow = Shadow()
    plan = mod._QueryPlan(dense=[[0.1]], sparse=[None], collections=["records"])
    service._submit_shadow(["q"], None, 10, "o1", "u1", "user-key", None, (), plan, {"vr1"}, {"vr1": "r1"})
    own: list[str] = []
    await service._fan_out_searches(["records"], [MagicMock()], 10, degraded=own)
    await started[0]

    assert own == []
    assert not degraded_notice.was_degraded()


async def test_a_diversity_read_that_moved_to_the_record_ids_keeps_only_their_partial_search(service) -> None:
    """The deeper container search stopped short and was dropped for the record-id search (narrow shortfall), which ran in full: its hits, and its
    complete search, are what joins the response."""
    from types import SimpleNamespace

    plan = mod._QueryPlan(dense=[[0.1]], sparse=[None], collections=["records"])

    async def container(*_a: object, plan: mod._QueryPlan, **_k: object) -> tuple:
        plan.degraded = True
        plan.degraded_reasons.add("timeout")
        plan.narrow_shortfall = True
        return [], {}, False
    service._search_and_adjudicate = container
    hit = {"score": 0.9, "content": "c", "metadata": {"virtualRecordId": "v1", "recordId": "r1"}}
    by_record_ids = AsyncMock(return_value=SimpleNamespace(results=[hit], accessible={"v1": "r1"}))

    await service._diversity_candidates(
        ["q"], None, 30, "o1", "u1", plan, containers=AccessibleContainers(), enumerated=None, by_record_ids=by_record_ids,
        record_ids_first=False, record_cut=None, allow_requery=True, user_key="user-key", scope_connector_ids=None, scopes=(),
    )

    assert by_record_ids.await_count == 1
    assert plan.degraded is False and plan.incomplete_notice() is None
