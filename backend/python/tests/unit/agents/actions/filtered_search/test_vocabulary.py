"""Unit tests for `FilterVocabularyService`: stub record-group tolerance,
roles-not-tracked signaling, people search/caching, and per-connector
caching behavior."""

from unittest.mock import AsyncMock, MagicMock

from app.agents.actions.filtered_search.vocabulary import FilterVocabularyService
from app.models.entities import RecordGroupType


def _service() -> tuple[FilterVocabularyService, MagicMock]:
    graph = MagicMock()
    return FilterVocabularyService(graph), graph


class TestRecordGroups:
    async def test_normal_group_uses_short_name_as_key(self) -> None:
        service, graph = _service()
        graph.list_record_groups = AsyncMock(return_value=[
            {"groupName": "Core Platform", "shortName": "CORE", "externalGroupId": "10001", "groupType": "PROJECT"},
        ])
        entries = await service.record_groups("org1", "conn1")
        assert len(entries) == 1
        assert entries[0].key == "CORE"
        assert entries[0].is_stub is False

    async def test_stub_group_with_null_short_name_falls_back_to_name(self) -> None:
        """`_handle_record_group` creates a stub with no `shortName` before
        the real sync populates it — vocabulary must not crash or drop it,
        just flag it so the PRE hook won't resolve a display name onto it
        as if it were a safe query token."""
        service, graph = _service()
        graph.list_record_groups = AsyncMock(return_value=[
            {"groupName": "10099", "shortName": None, "externalGroupId": "10099", "groupType": "PROJECT"},
        ])
        entries = await service.record_groups("org1", "conn1")
        assert len(entries) == 1
        assert entries[0].is_stub is True
        assert entries[0].key == "10099"

    async def test_group_types_filter_is_forwarded_to_provider(self) -> None:
        service, graph = _service()
        graph.list_record_groups = AsyncMock(return_value=[])
        await service.record_groups("org1", "conn1", group_types=[RecordGroupType.PROJECT], limit=10)
        graph.list_record_groups.assert_awaited_once_with(
            org_id="org1", connector_id="conn1", group_types=["PROJECT"], query=None, limit=10,
        )

    async def test_provider_failure_returns_empty_list_not_raise(self) -> None:
        service, graph = _service()
        graph.list_record_groups = AsyncMock(side_effect=RuntimeError("boom"))
        assert await service.record_groups("org1", "conn1") == []

    async def test_second_call_uses_cache_not_a_second_provider_call(self) -> None:
        service, graph = _service()
        graph.list_record_groups = AsyncMock(return_value=[
            {"groupName": "Core", "shortName": "CORE", "externalGroupId": "1", "groupType": "PROJECT"},
        ])
        await service.record_groups("org1", "conn1")
        await service.record_groups("org1", "conn1")
        assert graph.list_record_groups.await_count == 1


class TestPeople:
    async def test_people_without_source_user_id_are_excluded(self) -> None:
        service, graph = _service()
        graph.get_app_users = AsyncMock(return_value=[
            {"fullName": "Alice", "email": "alice@x.com", "sourceUserId": "U1"},
            {"fullName": "Bob", "email": "bob@x.com"},  # no sourceUserId -> no edge
        ])
        people = await service.people("org1", "conn1")
        assert len(people) == 1
        assert people[0].source_user_id == "U1"

    async def test_people_query_filters_by_name_or_email_case_insensitively(self) -> None:
        service, graph = _service()
        graph.get_app_users = AsyncMock(return_value=[
            {"fullName": "Alice Smith", "email": "alice@x.com", "sourceUserId": "U1"},
            {"fullName": "Bob Jones", "email": "bob@x.com", "sourceUserId": "U2"},
        ])
        people = await service.people("org1", "conn1", query="ALICE")
        assert [p.source_user_id for p in people] == ["U1"]

    async def test_people_provider_failure_returns_empty_list(self) -> None:
        service, graph = _service()
        graph.get_app_users = AsyncMock(side_effect=RuntimeError("boom"))
        assert await service.people("org1", "conn1") == []


class TestRoles:
    async def test_confluence_reports_roles_not_tracked_as_none(self) -> None:
        """Distinguishes "not tracked for this connector" from "[] configured
        roles" — callers must render these differently."""
        service, graph = _service()
        result = await service.roles("org1", "conn1", connector_type="CONFLUENCE")
        assert result is None
        graph.list_roles.assert_not_called()

    async def test_jira_roles_are_fetched_and_mapped(self) -> None:
        service, graph = _service()
        graph.list_roles = AsyncMock(return_value=[{"name": "Administrator", "externalRoleId": "10002"}])
        result = await service.roles("org1", "conn1", connector_type="JIRA")
        assert result == [{"name": "Administrator", "key": "10002", "external_id": "10002"}]

    async def test_roles_provider_failure_returns_empty_list_not_none(self) -> None:
        service, graph = _service()
        graph.list_roles = AsyncMock(side_effect=RuntimeError("boom"))
        assert await service.roles("org1", "conn1", connector_type="JIRA") == []


class TestInvalidate:
    async def test_invalidate_clears_only_that_connector(self) -> None:
        service, graph = _service()
        graph.list_record_groups = AsyncMock(return_value=[])
        await service.record_groups("org1", "conn1")
        await service.record_groups("org1", "conn2")
        service.invalidate("org1", "conn1")
        await service.record_groups("org1", "conn1")
        await service.record_groups("org1", "conn2")
        # conn1 re-fetched (invalidated), conn2 still cached from its one call
        assert graph.list_record_groups.await_count == 3
