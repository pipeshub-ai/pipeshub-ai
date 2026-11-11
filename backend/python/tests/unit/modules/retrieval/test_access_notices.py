"""A search says, as codes, that a source it reached is syncing or updating permissions, and only for sources the person is gated into."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import app.modules.retrieval.retrieval_service as mod
from app.exceptions.fastapi_responses import Status
from app.modules.retrieval import degraded_notice
from app.modules.retrieval.retrieval_service import RetrievalService
from app.services.permissions import access_status
from app.services.permissions.structure_generation import PENDING_KEY

pytestmark = pytest.mark.asyncio

PENDING = json.dumps({"root-1": 5})


def test_a_connector_with_pending_roots_or_syncing_gets_a_code_with_its_own_name_only() -> None:
    apps = [
        {"id": "c1", "name": "Jira", PENDING_KEY: PENDING, "status": "IDLE", "orgId": "o", "secret": "x"},
        {"id": "c2", "name": "Drive", "status": "SYNCING"},
        {"id": "c3", "name": "Quiet", "status": "IDLE"},
    ]
    assert access_status.notices(apps) == [
        {"code": "permissions_updating", "connectorId": "c1", "connectorName": "Jira"},
        {"code": "connector_syncing", "connectorId": "c2", "connectorName": "Drive"},
    ]


def test_an_unreadable_list_of_roots_reads_as_updating_never_as_nothing() -> None:
    assert access_status.notices([{"id": "c1", PENDING_KEY: "not json"}]) == [{"code": "permissions_updating", "connectorId": "c1"}]


def test_no_apps_no_notices() -> None:
    assert access_status.notices([]) == []


async def test_a_failed_read_of_the_apps_gives_no_notices_and_does_not_fail() -> None:
    graph = MagicMock()
    graph.get_nodes_by_field_in = AsyncMock(side_effect=ConnectionError("down"))
    assert await access_status.read_apps(graph, ["c1"]) == []
    graph.get_nodes_by_field_in = AsyncMock(return_value=[{"id": "c1"}])
    assert await access_status.read_apps(graph, ["c1", "c1"]) == [{"id": "c1"}]
    assert graph.get_nodes_by_field_in.await_args.args[2] == ["c1"]
    assert await access_status.read_apps(graph, []) == []


@degraded_notice.scoped
async def _run(notices: list[dict[str, str]]):  # noqa: ANN202
    degraded_notice.note_access(notices)
    degraded_notice.note_access(notices)  # a second search of the run adds nothing twice
    yield degraded_notice.flag({"answer": "x"})


async def test_the_final_answer_carries_the_notices_once_and_the_next_run_starts_clean() -> None:
    n = [{"code": "connector_syncing", "connectorId": "c1"}]
    first = [e async for e in _run(n)][0]
    second = [e async for e in _run([])][0]
    assert first["accessNotices"] == n
    assert "accessNotices" not in second
    assert degraded_notice.access_notices() == []


def test_a_check_that_could_not_be_made_says_so_without_naming_anything() -> None:
    svc = RetrievalService.__new__(RetrievalService)
    r = svc._create_empty_response("x", Status.PERMISSION_CHECK_UNAVAILABLE)
    assert r["accessNotices"] == [{"code": "check_unavailable"}]
    assert "accessNotices" not in svc._create_empty_response("x", Status.ACCESSIBLE_RECORDS_NOT_FOUND)


@pytest.fixture
def service(logger, mock_config_service, mock_vector_db_service, mock_graph_provider, mock_blob_store) -> RetrievalService:
    with patch("app.modules.retrieval.retrieval_service.SparseEmbedder"):
        registry = MagicMock()
        registry.strategy_name = "single"
        registry.resolve_for_query = AsyncMock(return_value=["records"])
        return RetrievalService(logger=logger, config_service=mock_config_service, collection_registry=registry,
                                vector_db_service=mock_vector_db_service, graph_provider=mock_graph_provider, blob_store=mock_blob_store)


async def test_a_search_reads_only_the_connectors_the_person_is_gated_into_and_reports_their_notices(service) -> None:
    containers = MagicMock(is_empty=False, app_ids=frozenset({"c1"}))
    read = AsyncMock(return_value=[{"id": "c1", "name": "Jira", "status": "SYNCING"}])
    with patch.object(service, "_resolve_search_scope", AsyncMock(return_value=(containers, {}, {"_key": "k"}))), \
            patch.object(service, "_permission_filter", AsyncMock(return_value=None)), \
            patch.object(access_status, "read_apps", read), patch.object(mod, "access_status", access_status):
        result = await service.search_with_filters(queries=["q"], user_id="u1", org_id="o1")
    assert read.await_args.args[1] == frozenset({"c1"})
    assert result["status"] == Status.ACCESSIBLE_RECORDS_NOT_FOUND.value
    assert result["accessNotices"] == [{"code": "connector_syncing", "connectorId": "c1", "connectorName": "Jira"}]


async def test_a_search_with_no_notices_has_no_such_field(service) -> None:
    containers = MagicMock(is_empty=False, app_ids=frozenset({"c1"}))
    with patch.object(service, "_resolve_search_scope", AsyncMock(return_value=(containers, {}, {"_key": "k"}))), \
            patch.object(service, "_permission_filter", AsyncMock(return_value=None)), \
            patch.object(access_status, "read_apps", AsyncMock(return_value=[{"id": "c1", "status": "IDLE"}])):
        result = await service.search_with_filters(queries=["q"], user_id="u1", org_id="o1")
    assert "accessNotices" not in result
