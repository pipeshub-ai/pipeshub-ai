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
        {"id": "c2", "name": "Drive", "status": "SYNCING"},  # never finished a sync
        {"id": "c3", "name": "Quiet", "status": "IDLE"},
    ]
    assert access_status.notices(apps) == [
        {"code": "permissions_updating", "connectorId": "c1", "connectorName": "Jira"},
        {"code": "connector_syncing", "connectorId": "c2", "connectorName": "Drive", "firstSync": True},
    ]


def test_a_routine_resync_of_an_established_connector_says_nothing_unless_the_search_found_nothing() -> None:
    established = {"id": "c1", "name": "Jira", "status": "SYNCING", "lastSyncFinishedAtTimestamp": 1700000000000}
    assert access_status.notices([established]) == []
    # a resync that found nothing is not "nothing to search yet": no firstSync, so the client does not say so
    assert access_status.notices([established], empty=True) == [{"code": "connector_syncing", "connectorId": "c1", "connectorName": "Jira"}]
    first = {"id": "c2", "status": "FULL_SYNCING"}
    assert access_status.notices([first]) == [{"code": "connector_syncing", "connectorId": "c2", "firstSync": True}]
    idle = {"id": "c3", "status": "IDLE"}
    assert access_status.notices([idle], empty=True) == []


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
    first = next(iter([e async for e in _run(n)]))
    second = next(iter([e async for e in _run([])]))
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
    read = AsyncMock(return_value=[{"id": "c1", "name": "Jira", "status": "SYNCING"}])  # never finished a sync
    with patch.object(service, "_resolve_search_scope", AsyncMock(return_value=(containers, {}, {"_key": "k"}))), \
            patch.object(service, "_permission_filter", AsyncMock(return_value=None)), \
            patch.object(access_status, "read_apps", read), patch.object(mod, "access_status", access_status):
        result = await service.search_with_filters(queries=["q"], user_id="u1", org_id="o1")
    assert read.await_args.args[1] == ["c1"]
    assert result["status"] == Status.ACCESSIBLE_RECORDS_NOT_FOUND.value
    assert result["accessNotices"] == [{"code": "connector_syncing", "connectorId": "c1", "connectorName": "Jira", "firstSync": True}]


async def test_a_search_with_no_notices_has_no_such_field(service) -> None:
    containers = MagicMock(is_empty=False, app_ids=frozenset({"c1"}))
    with patch.object(service, "_resolve_search_scope", AsyncMock(return_value=(containers, {}, {"_key": "k"}))), \
            patch.object(service, "_permission_filter", AsyncMock(return_value=None)), \
            patch.object(access_status, "read_apps", AsyncMock(return_value=[{"id": "c1", "status": "IDLE"}])):
        result = await service.search_with_filters(queries=["q"], user_id="u1", org_id="o1")
    assert "accessNotices" not in result


async def test_an_empty_search_names_a_syncing_connector_that_a_non_empty_one_would_not(service) -> None:
    containers = MagicMock(is_empty=False, app_ids=frozenset({"c1"}))
    established = {"id": "c1", "name": "Jira", "status": "SYNCING", "lastSyncFinishedAtTimestamp": 1700000000000}
    with patch.object(service, "_resolve_search_scope", AsyncMock(return_value=(containers, {}, {"_key": "k"}))), \
            patch.object(service, "_permission_filter", AsyncMock(return_value=None)), \
            patch.object(access_status, "read_apps", AsyncMock(return_value=[established])):
        result = await service.search_with_filters(queries=["q"], user_id="u1", org_id="o1")
    assert result["accessNotices"] == [{"code": "connector_syncing", "connectorId": "c1", "connectorName": "Jira"}]


async def test_a_demo_app_the_person_switched_off_is_not_read(service) -> None:
    containers = MagicMock(is_empty=False, app_ids=frozenset({"c1", "demo"}))
    read = AsyncMock(return_value=[])
    with patch.object(service, "_resolve_search_scope", AsyncMock(return_value=(containers, {}, {"_key": "k"}))), \
            patch.object(service, "_permission_filter", AsyncMock(return_value=None)), \
            patch.object(service, "_excluded_demo_apps", AsyncMock(return_value=frozenset({"demo"}))), \
            patch.object(access_status, "read_apps", read):
        await service.search_with_filters(queries=["q"], user_id="u1", org_id="o1")
    assert read.await_args.args[1] == ["c1"]


@degraded_notice.scoped
async def _two_searches(service: RetrievalService, read: AsyncMock):  # noqa: ANN202
    yield [await service._notice_apps(frozenset({"c1", "c2"}), "u1", "o1") for _ in range(3)]


async def test_a_run_reads_each_connector_once_however_often_it_searches(service) -> None:
    read = AsyncMock(return_value=[{"id": "c1", "status": "SYNCING"}])
    with patch.object(service, "_excluded_demo_apps", AsyncMock(return_value=frozenset())), patch.object(access_status, "read_apps", read):
        (apps,) = [a async for a in _two_searches(service, read)]
    assert read.await_count == 1 and read.await_args.args[1] == ["c1", "c2"]
    assert all(a == [{"id": "c1", "status": "SYNCING"}] for a in apps)  # c2 was not returned and is not asked about again


async def test_outside_a_tracked_run_nothing_is_remembered(service) -> None:
    read = AsyncMock(return_value=[])
    with patch.object(service, "_excluded_demo_apps", AsyncMock(return_value=frozenset())), patch.object(access_status, "read_apps", read):
        await service._notice_apps(frozenset({"c1"}), "u1", "o1")
        await service._notice_apps(frozenset({"c1"}), "u1", "o1")
    assert read.await_count == 2


@degraded_notice.scoped
async def _unavailable(service: RetrievalService):  # noqa: ANN202
    service._create_empty_response("x", Status.PERMISSION_CHECK_UNAVAILABLE)
    yield degraded_notice.flag({"answer": "x"})


async def test_a_check_that_could_not_be_made_reaches_the_final_chat_answer_too(service) -> None:
    (final,) = [e async for e in _unavailable(service)]
    assert final["accessNotices"] == [{"code": "check_unavailable"}]


async def test_a_successful_search_carries_the_notices_of_the_connectors_it_reached(service, mock_graph_provider, mock_vector_db_service) -> None:
    from app.services.graph_db.interface.graph_db_provider import AccessCheck

    record = {"_key": "rec1", "virtualRecordId": "vr1", "origin": "google_drive", "recordName": "Runbook",
              "webUrl": "https://example.com/doc", "mimeType": "application/pdf", "connectorName": "gdrive"}
    hit = {"content": "rollback steps", "score": 0.9, "metadata": {"virtualRecordId": "vr1", "orgId": "o1", "recordId": "rec1", "point_id": "p1"}}
    mock_graph_provider.get_user_by_user_id.return_value = {"_key": "user-key", "email": "t@example.com"}
    mock_graph_provider.check_access = AsyncMock(return_value=AccessCheck(node_ids=frozenset({"rec1"}), records_by_vrid={"vr1": "rec1"}))
    mock_graph_provider.get_records_by_record_ids.return_value = [dict(record)]
    containers = MagicMock(is_empty=False, app_ids=frozenset({"c1"}), scope_connector_ids=None)
    updating = {"id": "c1", "name": "Jira", PENDING_KEY: PENDING, "status": "IDLE"}
    with patch.object(service, "_resolve_search_scope", AsyncMock(return_value=(containers, {}, {"_key": "user-key"}))), \
            patch.object(service, "_permission_filter", AsyncMock(return_value=({}, set()))), \
            patch.object(service, "_search_and_adjudicate", AsyncMock(return_value=([hit], {"vr1": "rec1"}, False))), \
            patch.object(service, "_notice_apps", AsyncMock(return_value=[updating])):
        result = await service.search_with_filters(queries=["q"], user_id="u1", org_id="o1")
    assert result["status"] == Status.SUCCESS.value, result.get("message")
    assert result["accessNotices"] == [{"code": "permissions_updating", "connectorId": "c1", "connectorName": "Jira"}]


# The record-id path: no containers, so the connectors come from the person's own gate.
async def _record_id_search(service, mock_graph_provider, gated, apps, *, filters=None, time_range=None, excluded=frozenset()) -> tuple[dict, AsyncMock]:
    mock_graph_provider.get_knowledge_hub_access_v3 = AsyncMock(return_value={"gated_app_ids": list(gated)})
    mock_graph_provider.get_user_by_user_id.return_value = {"_key": "user-key", "email": "t@example.com"}
    read = AsyncMock(return_value=apps)
    with patch.object(service, "_excluded_demo_apps", AsyncMock(return_value=excluded)), \
            patch.object(service, "_get_accessible_virtual_ids_task", AsyncMock(return_value={})), \
            patch.object(access_status, "read_apps", read):
        result = await service.search_with_filters(queries=["q"], user_id="u1", org_id="o1", filter_groups=filters, time_range=time_range)
    return result, read


SYNCING = {"id": "c1", "name": "Jira", "status": "FULL_SYNCING"}


async def test_the_record_id_path_reports_notices_for_the_connectors_the_person_is_gated_into(service, mock_graph_provider) -> None:
    # a switched-off demo app keeps the record-id path; a date range does too
    result, read = await _record_id_search(service, mock_graph_provider, {"c1", "demo", "c9"}, [SYNCING], excluded=frozenset({"demo"}),
                                           time_range={"start": 1, "end": 2})
    assert read.await_args.args[1] == ["c1", "c9"]  # the demo app is dropped; both are in the person's gate
    assert result["status"] == Status.ACCESSIBLE_RECORDS_NOT_FOUND.value
    assert result["accessNotices"] == [{"code": "connector_syncing", "connectorId": "c1", "connectorName": "Jira", "firstSync": True}]


async def test_a_connector_outside_the_persons_gate_is_never_read_on_the_record_id_path(service, mock_graph_provider) -> None:
    _, read = await _record_id_search(service, mock_graph_provider, set(), [], time_range={"start": 1, "end": 2})
    read.assert_not_awaited()


async def test_a_scoped_record_id_search_reads_only_the_scoped_connectors_within_the_gate(service, mock_graph_provider) -> None:
    _, read = await _record_id_search(service, mock_graph_provider, {"c1", "c2"}, [], filters={"apps": ["c2", "c3"]}, time_range={"start": 1, "end": 2})
    assert read.await_args.args[1] == ["c2"]


async def test_an_unreadable_gate_gives_no_notices_and_does_not_fail_the_search(service, mock_graph_provider) -> None:
    mock_graph_provider.get_knowledge_hub_access_v3 = AsyncMock(side_effect=ConnectionError("down"))
    mock_graph_provider.get_user_by_user_id.return_value = {"_key": "user-key", "email": "t@example.com"}
    with patch.object(service, "_excluded_demo_apps", AsyncMock(return_value=frozenset())), \
            patch.object(service, "_get_accessible_virtual_ids_task", AsyncMock(return_value={})):
        result = await service.search_with_filters(queries=["q"], user_id="u1", org_id="o1", time_range={"start": 1, "end": 2})
    assert result["status"] == Status.ACCESSIBLE_RECORDS_NOT_FOUND.value
    assert "accessNotices" not in result


async def test_a_strict_scope_with_nothing_selected_reaches_no_connector_and_says_nothing_about_any(service, mock_graph_provider) -> None:
    # a project chat with nothing selected searches no records, so no connector may be reported as syncing
    result, read = await _record_id_search(service, mock_graph_provider, {"c1"}, [SYNCING], filters={"strictScope": True, "apps": []},
                                           time_range={"start": 1, "end": 2})
    mock_graph_provider.get_knowledge_hub_access_v3.assert_not_awaited()
    read.assert_not_awaited()
    assert "accessNotices" not in result


async def test_a_strict_scope_that_names_a_connector_still_reports_it(service, mock_graph_provider) -> None:
    result, read = await _record_id_search(service, mock_graph_provider, {"c1", "c2"}, [SYNCING], filters={"strictScope": True, "apps": ["c1"]},
                                           time_range={"start": 1, "end": 2})
    assert read.await_args.args[1] == ["c1"]
    assert result["accessNotices"][0]["connectorId"] == "c1"


async def test_an_entity_filter_that_matches_nothing_still_carries_the_notices_with_its_own_message_and_status(service) -> None:
    containers = MagicMock(is_empty=False, app_ids=frozenset({"c1"}))
    with patch.object(service, "_resolve_search_scope", AsyncMock(return_value=(containers, {}, {"_key": "k"}))), \
            patch.object(service, "_entity_filter_virtual_ids", AsyncMock(return_value=([], None))), \
            patch.object(service, "_notice_apps", AsyncMock(return_value=[SYNCING])):
        result = await service.search_with_filters(
            queries=["q"], user_id="u1", org_id="o1", filter_groups={"entityFilters": {"x": 1}}, virtual_record_ids_from_tool=["v1"])
    assert result["status"] == Status.EMPTY_RESPONSE.value
    assert result["message"] == mod.ENTITY_FILTER_NO_MATCH_MESSAGE
    assert result["accessNotices"] == [{"code": "connector_syncing", "connectorId": "c1", "connectorName": "Jira", "firstSync": True}]
