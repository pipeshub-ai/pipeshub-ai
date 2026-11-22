"""A failed read of what a user may access is logged once, where the search handles it, without the graph error's text
or the user's graph key (docs/logging.md, "Search access reads").

The graph error's text carries a host, a query, the graph key and record content (tests/support/graph_read_errors.py).
The caller's user id is the bound context's, so each message is the event name. Every case runs with an error no
client stamped (an outage, WARNING) and with one the graph client stamped as our query bug (ERROR, text-free stack).
"""

from __future__ import annotations

import logging
import random
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import app.modules.retrieval.retrieval_service as mod
from app.core import org_context
from app.core.org_context import OrgContext
from app.exceptions.fastapi_responses import Status
from app.modules.demo_data import access as demo_access
from app.modules.retrieval.entity_filters import (
    TOO_BROAD_MESSAGE,
    EntityFilterUnavailableError,
)
from tests.support.graph_read_errors import CASES, GRAPH_KEY, SENTINELS, wrapped
from tests.support.log_capture import assert_not_logged, assert_text_free, failure_line
from tests.unit.modules.retrieval.test_narrow_access_fallback import (
    ORG,
    _Access,
    _corpus,
    _FakeStore,
    _graph,
    _half_readable,
    _search,
)
from tests.unit.modules.retrieval.test_narrow_access_fallback import (
    _service as _narrow_service,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Iterator

LOGGER = "test.retrieval_access_reads"
USER_ID = "u-5d1e"


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.delenv("SEARCH_NARROW_ACCESS_FALLBACK", raising=False)
    monkeypatch.delenv("FUSION_RECORD_CUT", raising=False)
    monkeypatch.delenv("HIGH_ACCURACY_MODE", raising=False)
    mod._user_cache.clear()
    mod.narrow_access_memo.clear()
    demo_access._demo_ids_cache.clear()
    with patch(
        "app.agents.actions.knowledge_graph.location.resolve_ancestor_locations",
        AsyncMock(return_value={}),
    ):
        yield
    mod._user_cache.clear()
    mod.narrow_access_memo.clear()
    demo_access._demo_ids_cache.clear()


@pytest.fixture
def log(caplog: pytest.LogCaptureFixture) -> pytest.LogCaptureFixture:
    caplog.set_level(logging.DEBUG, logger=LOGGER)
    caplog.set_level(logging.DEBUG, logger=demo_access.__name__)
    return caplog


@pytest.fixture
def service(mock_config_service, mock_vector_db_service, mock_graph_provider, mock_blob_store) -> mod.RetrievalService:
    with patch("app.modules.retrieval.retrieval_service.SparseEmbedder"):
        registry = MagicMock()
        registry.strategy_name = "single"
        registry.resolve_for_query = AsyncMock(return_value=["records"])
        return mod.RetrievalService(logger=logging.getLogger(LOGGER), config_service=mock_config_service,
                                    collection_registry=registry, vector_db_service=mock_vector_db_service,
                                    graph_provider=mock_graph_provider, blob_store=mock_blob_store)


def _narrow(store: _FakeStore, graph: AsyncMock, *, containers: bool = True) -> mod.RetrievalService:
    graph.get_knowledge_hub_access_v3 = AsyncMock(return_value={"gated_app_ids": []})
    service = _narrow_service(store, graph, containers=containers)
    service.logger = logging.getLogger(LOGGER)
    return service


def _failing(cause: Callable[[], Exception]) -> AsyncMock:
    """A read that fails each time it is called, with a new exception each time, as a real outage does."""
    async def read(*_: object, **__: object) -> dict[str, str]:
        raise wrapped(cause())

    return AsyncMock(side_effect=read)


def _warned(records: list[logging.LogRecord]) -> list[str]:
    return [r.getMessage() for r in records if r.levelno >= logging.WARNING]


def _no_user_in_warnings(log: pytest.LogCaptureFixture) -> None:
    """No WARNING-or-above line names the user (DEBUG cache lines on main do, adjacent to this change)."""
    assert_not_logged([r for r in log.records if r.levelno >= logging.WARNING], USER_ID)


def _checked(log: pytest.LogCaptureFixture, event: str, func: str, error_class: str, level: int) -> dict[str, object]:
    assert_text_free(log.records, *SENTINELS)
    return failure_line(log.records, event, error_class, level, *SENTINELS, handler=func)


# --- the connector gate and the demo setting: the access notices -------------------------------------------------


@pytest.mark.parametrize(("error", "error_class", "level"), CASES)
async def test_an_unreadable_connector_gate_logs_one_text_free_line_and_the_search_goes_on(
    service, mock_graph_provider, log, error, error_class, level,
) -> None:
    mock_graph_provider.get_knowledge_hub_access_v3 = AsyncMock(side_effect=error())
    mock_graph_provider.get_user_by_user_id.return_value = {"_key": GRAPH_KEY}
    with patch.object(service, "_excluded_demo_apps", AsyncMock(return_value=frozenset())), \
            patch.object(service, "_get_accessible_virtual_ids_task", AsyncMock(return_value={})):
        result = await service.search_with_filters(queries=["q"], user_id=USER_ID, org_id="o1",
                                                   time_range={"start": 1, "end": 2})

    assert result["status"] == Status.ACCESSIBLE_RECORDS_NOT_FOUND.value
    assert "accessNotices" not in result
    _checked(log, "search.notice_gate_failed", "_notice_app_ids", error_class, level)
    # The record-id path's own line (search_with_filters, adjacent) still names the user; nothing else does.
    assert _warned(log.records) == ["search.notice_gate_failed", f"No accessible documents found for user {USER_ID} and org o1"]


@pytest.mark.parametrize(("error", "error_class", "level"), CASES)
async def test_an_unreadable_demo_setting_logs_one_text_free_line_and_excludes_nothing(
    service, log, error, error_class, level,
) -> None:
    with patch.object(mod, "excluded_demo_connector_ids", AsyncMock(side_effect=error())):
        assert await service._excluded_demo_apps(USER_ID, "o1") == frozenset()

    _checked(log, "search.demo_setting_failed", "_excluded_demo_apps", error_class, level)
    _no_user_in_warnings(log)


async def test_the_user_is_the_bound_one_on_the_line_not_in_the_message(service, log) -> None:
    with org_context.bind(OrgContext("o1", USER_ID, "user", "jwt", "-")), \
            patch.object(mod, "excluded_demo_connector_ids", AsyncMock(side_effect=CASES[0][0]())):
        await service._excluded_demo_apps(USER_ID, "o1")

    fields = failure_line(log.records, "search.demo_setting_failed", "unavailable", logging.WARNING, *SENTINELS,
                          handler="_excluded_demo_apps")  # CASES[0], an outage; the message is the event, not the user
    assert fields["user_id"] == USER_ID
    assert fields["org_id"] == "o1"


@pytest.mark.parametrize(("error", "error_class", "level"), CASES)
async def test_an_unlisted_demo_connector_set_logs_one_text_free_line_and_is_read_again(log, error, error_class, level) -> None:
    graph = AsyncMock()
    graph.get_org_apps = AsyncMock(side_effect=[error(), error()])  # two reads, two failures

    assert await demo_access.demo_connector_ids(graph, "o1") == ()
    assert await demo_access.demo_connector_ids(graph, "o1") == ()

    assert graph.get_org_apps.await_count == 2  # a failure is not cached
    assert_text_free(log.records, *SENTINELS)
    lines = [r for r in log.records if r.getMessage() == "demo_data.list_failed"]
    assert [(r.error_class, r.levelno) for r in lines] == [(error_class, level)] * 2


# --- the search's scope: answered as "could not check" ------------------------------------------------------------


@pytest.mark.parametrize(("cause", "error_class", "level"), CASES)
async def test_an_unreadable_access_scope_answers_unavailable_with_one_text_free_line(
    service, log, cause, error_class, level,
) -> None:
    with patch.object(service, "_resolve_search_scope", AsyncMock(side_effect=wrapped(cause()))):
        result = await service.search_with_filters(queries=["q"], user_id=USER_ID, org_id="o1")

    assert result["status"] == Status.PERMISSION_CHECK_UNAVAILABLE.value
    _checked(log, "search.access_unreadable", "search_with_filters", error_class, level)
    assert _warned(log.records) == ["search.access_unreadable"]
    _no_user_in_warnings(log)


async def test_two_failed_reads_of_a_remembered_scope_are_two_lines_at_their_own_level(service, log) -> None:
    first, second = wrapped(CASES[0][0]()), wrapped(CASES[1][0]())
    mod.narrow_access_memo.mark(mod.narrow_access_memo.key("o1", USER_ID, {}))
    with patch.object(service, "_resolve_search_scope", AsyncMock(side_effect=[first, second])):
        result = await service.search_with_filters(queries=["q"], user_id=USER_ID, org_id="o1")

    assert result["status"] == Status.PERMISSION_CHECK_UNAVAILABLE.value
    assert [(r.getMessage(), r.levelno) for r in log.records if r.levelno >= logging.WARNING] == [
        ("search.remembered_scope_unreadable", logging.WARNING), ("search.access_unreadable", logging.ERROR),
    ]
    assert not mod.narrow_access_memo.is_narrow(mod.narrow_access_memo.key("o1", USER_ID, {}))
    _no_user_in_warnings(log)


@pytest.mark.parametrize(("cause", "error_class", "level"), CASES)
async def test_an_unreadable_record_list_on_a_remembered_scope_searches_the_containers_with_one_line(
    log, cause, error_class, level,
) -> None:
    corpus, readable = _half_readable(16)
    graph = _graph(_Access(readable, set(readable)))
    service = _narrow(_FakeStore(list(corpus.points)), graph)
    await _search(service, 10)
    key = mod.narrow_access_memo.key(ORG, "alice", {})
    assert mod.narrow_access_memo.is_narrow(key)
    graph.get_accessible_virtual_record_ids = _failing(cause)
    log.clear()

    response = await _search(service, 10)

    assert response["status"] == Status.SUCCESS.value
    assert response["searchResults"]
    assert not mod.narrow_access_memo.is_narrow(key)
    # The container search then falls short and retries by record ids: a second read, a second failure.
    assert _warned(log.records) == ["search.remembered_scope_unreadable", "search.record_id_retry_failed"]
    assert_text_free(log.records, *SENTINELS)
    assert {(r.error_class, r.levelno) for r in log.records if r.levelno >= logging.WARNING} == {(error_class, level)}


@pytest.mark.parametrize(("cause", "error_class", "level"), CASES)
async def test_a_failed_record_list_with_nothing_admitted_answers_unavailable_with_one_line(
    log, cause, error_class, level,
) -> None:
    corpus = _corpus(8, records=1000)
    readable = set(random.Random(8).sample(corpus.vrids, 10))
    graph = _graph(_Access(readable, set(readable)))
    graph.get_accessible_virtual_record_ids = _failing(cause)

    response = await _search(_narrow(_FakeStore(list(corpus.points)), graph), 10)

    assert response["status"] == Status.PERMISSION_CHECK_UNAVAILABLE.value
    _checked(log, "search.access_unreadable", "search_with_filters", error_class, level)
    assert _warned(log.records) == ["search.access_unreadable"]


@pytest.mark.parametrize(("cause", "error_class", "level"), CASES)
async def test_a_failed_record_id_retry_keeps_the_admitted_page_with_one_line(log, cause, error_class, level) -> None:
    corpus, readable = _half_readable(9)
    store = _FakeStore(list(corpus.points))
    graph = _graph(_Access(readable, set(readable)))
    graph.get_accessible_virtual_record_ids = _failing(cause)

    response = await _search(_narrow(store, graph), 10)

    assert response["status"] == Status.SUCCESS.value
    assert response["searchResults"]
    assert store.used_record_ids() == [False]
    _checked(log, "search.record_id_retry_failed", "search_with_filters", error_class, level)
    assert _warned(log.records) == ["search.record_id_retry_failed"]


@pytest.mark.parametrize(("error", "error_class", "level"), CASES)
async def test_a_failed_check_of_the_hits_answers_unavailable_with_one_line(log, error, error_class, level) -> None:
    corpus = _corpus(4, records=40)
    graph = _graph(_Access(set(corpus.vrids), set(corpus.vrids)))
    graph.check_access = AsyncMock(side_effect=error())

    response = await _search(_narrow(_FakeStore(list(corpus.points)), graph, containers=False), 10)

    assert response["status"] == Status.PERMISSION_CHECK_UNAVAILABLE.value
    _checked(log, "search.hit_check_failed", "search_with_filters", error_class, level)
    assert _warned(log.records) == ["search.hit_check_failed"]


@pytest.mark.parametrize(("cause", "error_class", "level"), CASES)
async def test_an_unverifiable_container_page_is_degraded_with_one_line(log, cause, error_class, level) -> None:
    corpus = _corpus(5, records=40)
    graph = _graph(_Access(set(corpus.vrids), set(corpus.vrids)))
    graph.check_access = AsyncMock(side_effect=wrapped(cause()))

    response = await _search(_narrow(_FakeStore(list(corpus.points)), graph), 10)

    assert response["status"] == Status.PERMISSION_CHECK_UNAVAILABLE.value
    fields = _checked(log, "container_search.verification_unavailable", "_search_and_adjudicate", error_class, level)
    assert fields["vrid_count"] == len(graph.check_access.await_args.kwargs["virtual_record_ids"])
    assert _warned(log.records) == ["container_search.verification_unavailable"]


# --- reads that only make the answer poorer -----------------------------------------------------------------------


@pytest.mark.parametrize(("error", "error_class", "level"), CASES)
async def test_a_failed_batch_fetch_falls_back_per_id_with_one_line(log, error, error_class, level) -> None:
    corpus = _corpus(6, records=20)
    graph = _graph(_Access(set(corpus.vrids), set(corpus.vrids)))
    bare = graph.get_records_by_record_ids.side_effect

    async def files_without_mime(record_ids: list[str], org_id: str) -> list[dict]:
        return [{**r, "recordType": "FILE", "mimeType": None} for r in await bare(record_ids, org_id)]

    graph.get_records_by_record_ids = AsyncMock(side_effect=files_without_mime)
    graph.get_nodes_by_field_in = AsyncMock(side_effect=error())
    graph.get_document = AsyncMock(side_effect=lambda rid, _c: {"id": rid, "mimeType": "text/plain",
                                                               "webUrl": f"https://example.test/{rid}"})

    response = await _search(_narrow(_FakeStore(list(corpus.points)), graph, containers=False), 5)

    assert response["status"] == Status.SUCCESS.value
    assert response["searchResults"]
    assert graph.get_document.await_count
    fields = _checked(log, "search.batch_fetch_failed", "_fetch_by_ids", error_class, level)
    assert fields["collection"] == "files"


@pytest.mark.parametrize(("error", "error_class", "level"), CASES)
async def test_a_failed_deeper_read_keeps_the_plain_result_with_one_line(service, log, error, error_class, level) -> None:
    with patch.object(service, "_execute_parallel_searches", AsyncMock(return_value=[])), \
            patch.object(service, "_keep_permitted_hits", AsyncMock(side_effect=error())):
        found = await service._diversity_candidates(
            ["q"], {}, 40, "o1", USER_ID, mod._QueryPlan(), containers=None, enumerated={},
            by_record_ids=AsyncMock(), record_ids_first=False, record_cut=None, allow_requery=False,
            user_key=GRAPH_KEY, scope_connector_ids=None, scopes=[],
        )

    assert found == ([], {})
    _checked(log, "search.diversity_read_failed", "_diversity_candidates", error_class, level)


@pytest.mark.parametrize(("error", "error_class", "level"), CASES)
async def test_an_unavailable_entity_filter_answers_an_error_with_one_text_free_line(
    service, log, error, error_class, level,
) -> None:
    resolver = MagicMock()
    try:
        raise EntityFilterUnavailableError("named-entity lookup failed") from error()
    except EntityFilterUnavailableError as exc:
        resolver.match = AsyncMock(side_effect=exc)
    with patch.object(mod, "EntityFilterResolver", return_value=resolver):
        vids, response = await service._entity_filter_virtual_ids("o1", USER_ID, {"x": 1}, {}, None, {})

    assert vids is None
    assert response["status"] == Status.ERROR.value
    _checked(log, "search.entity_filter_unavailable", "_entity_filter_virtual_ids", error_class, level)
    _no_user_in_warnings(log)


@pytest.mark.parametrize(("cause", "error_class", "level"), CASES)
async def test_an_unreadable_scope_for_a_broad_entity_filter_answers_unavailable_with_one_line(
    service, log, cause, error_class, level,
) -> None:
    resolver = MagicMock()
    resolver.match = AsyncMock(return_value=MagicMock(error=TOO_BROAD_MESSAGE))
    with patch.object(mod, "EntityFilterResolver", return_value=resolver), \
            patch.object(service, "_excluded_demo_apps", AsyncMock(return_value=frozenset())), \
            patch.object(service, "_get_accessible_virtual_ids_task", AsyncMock(side_effect=wrapped(cause()))):
        vids, response = await service._entity_filter_virtual_ids("o1", USER_ID, {"x": 1}, {}, None, None)

    assert vids is None
    assert response["status"] == Status.PERMISSION_CHECK_UNAVAILABLE.value
    _checked(log, "search.access_unreadable", "_entity_filter_virtual_ids", error_class, level)
    _no_user_in_warnings(log)


@pytest.mark.parametrize(("cause", "error_class", "level"), CASES)
async def test_an_unreadable_selection_answers_unavailable_with_one_line(
    service, mock_graph_provider, log, cause, error_class, level,
) -> None:
    mock_graph_provider.get_user_by_user_id.return_value = {"_key": GRAPH_KEY}
    with patch.object(mod, "resolve_request_scopes", AsyncMock(side_effect=wrapped(cause()))):
        response = await service._resolve_selection(USER_ID, "o1", {}, {"kb": ["kb-1"]})

    assert response["status"] == Status.PERMISSION_CHECK_UNAVAILABLE.value
    _checked(log, "search.selection_unreadable", "_resolve_selection", error_class, level)
    _no_user_in_warnings(log)

