"""Jira Data Center issue security: the parts of the connector that are not a whole sync."""

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import AccessRule
from app.connectors.sources.atlassian.core.jira_issue_security import (
    IssueSecurityContext,
    SchemeKnowledge,
)
from app.connectors.sources.atlassian.jira_data_center.connector import (
    JiraDataCenterConnector,
)
from app.connectors.sources.atlassian.jira_data_center_personal.connector import (
    JiraDataCenterPersonalConnector,
)
from app.models.entities import AppUser, RecordType, TicketRecord


def _connector(cls=JiraDataCenterConnector) -> JiraDataCenterConnector:
    dep = MagicMock()
    dep.org_id = "org"
    dep.on_new_records = AsyncMock()
    dep.on_record_content_update = AsyncMock()
    dep.get_record_by_external_id = AsyncMock(return_value=None)
    cs = MagicMock()
    cs.get_config = AsyncMock()
    return cls(logging.getLogger("t"), dep, MagicMock(), cs, "c", "team", "u")


def _ticket(**overrides) -> TicketRecord:
    fields = {
        "id": "t1", "org_id": "org", "record_name": "[P-1] x", "record_type": RecordType.TICKET,
        "external_record_id": "1", "origin": "CONNECTOR", "connector_name": "JIRA DATA CENTER",
        "connector_id": "c", "version": 1, "mime_type": "text/plain", "external_record_group_id": "10",
    }
    return TicketRecord(**{**fields, **overrides})


def _user(key: str, email: str) -> AppUser:
    return AppUser(app_name="JIRA DATA CENTER", connector_id="c", source_user_id=key, org_id="org",
                   email=email, full_name=key, is_active=True)


class TestPersonalConnector:
    async def test_a_personal_connector_applies_no_level(self) -> None:
        connector = _connector(JiraDataCenterPersonalConnector)
        context = await connector._load_issue_security("P")
        assert context.enforce is False and context.knowledge == SchemeKnowledge.NONE

    def test_a_team_connector_does(self) -> None:
        assert _connector()._enforces_issue_security is True


class TestUsernames:
    def test_a_user_holder_named_by_username_resolves_to_the_user_by_key(self) -> None:
        connector = _connector()
        connector._dc_name_to_source_id = {"carol": "carol-key"}
        users = {"carol-key": _user("carol-key", "carol@example.com")}
        resolved = connector._users_with_usernames(users)
        assert resolved["carol"].email == "carol@example.com" and resolved["carol-key"] is users["carol-key"]


class TestStoredGrantsKept:
    """A leveled issue whose level could not be read yet keeps the grants it has: a full sync's first
    write of an existing record would otherwise replace them with none."""

    async def test_a_closed_stored_issue_without_grants_goes_through_the_content_update(self) -> None:
        connector = _connector()
        connector.data_entities_processor.get_record_by_external_id.return_value = _ticket(is_placeholder=False)
        record = _ticket(access_rule=AccessRule.RESTRICTED, rewrite_permissions=False)

        await connector._process_new_records([(record, [])], "P", {"new_count": 0, "updated_count": 0})

        connector.data_entities_processor.on_record_content_update.assert_awaited_once_with(record)
        connector.data_entities_processor.on_new_records.assert_not_awaited()

    @pytest.mark.parametrize(
        ("record", "stored"),
        [
            pytest.param(_ticket(access_rule=AccessRule.RESTRICTED, rewrite_permissions=True), True, id="grants-replaced"),
            pytest.param(_ticket(access_rule=AccessRule.RESTRICTED, rewrite_permissions=False), False, id="new-issue"),
            pytest.param(_ticket(), True, id="no-level-involved"),
        ],
    )
    async def test_everything_else_is_written_as_before(self, record, stored) -> None:
        connector = _connector()
        connector.data_entities_processor.get_record_by_external_id.return_value = _ticket() if stored else None

        await connector._process_new_records([(record, [])], "P", {"new_count": 0, "updated_count": 0})

        connector.data_entities_processor.on_new_records.assert_awaited_once()
        connector.data_entities_processor.on_record_content_update.assert_not_awaited()


class TestAncestorStub:
    def test_a_stub_of_a_leveled_issue_is_granted_to_the_level_only(self) -> None:
        connector = _connector()
        connector._issue_security["10"] = IssueSecurityContext(
            knowledge=SchemeKnowledge.PRESENT, members_readable=True,
            members_by_level={"1": [{"type": "user", "parameter": "alice-key", "user": {"key": "alice-key"}}]},
        )
        issue = {"id": "9", "key": "P-9", "fields": {
            "project": {"id": "10", "key": "P"}, "security": {"id": "1"}, "issuetype": {"name": "Epic"}}}
        stub = _ticket(is_placeholder=True)

        grants = connector._secure_ancestor_stub(stub, issue, {"alice-key": _user("alice-key", "alice@example.com")})

        assert stub.access_rule == AccessRule.RESTRICTED
        assert [g.email for g in grants] == ["alice@example.com"]

    def test_a_stub_in_a_project_this_sync_did_not_read_is_left_alone(self) -> None:
        connector = _connector()
        issue = {"id": "9", "fields": {"project": {"id": "99", "key": "Q"}, "security": {"id": "1"}}}
        assert connector._secure_ancestor_stub(_ticket(), issue, {}) == []


class TestParentGate:
    async def test_a_project_without_a_scheme_files_as_before_and_reads_nothing(self) -> None:
        connector = _connector()
        connector._get_issue_with_retry = AsyncMock()
        none = IssueSecurityContext(knowledge=SchemeKnowledge.NONE)
        assert await connector._files_under_parent({}, "5", none) is True
        connector._get_issue_with_retry.assert_not_awaited()

    async def test_a_parent_that_cannot_be_read_holds_the_story_instead_of_guessing(self) -> None:
        connector = _connector()
        connector._get_issue_with_retry = AsyncMock(return_value=MagicMock(status=503))
        security = IssueSecurityContext(knowledge=SchemeKnowledge.PRESENT, members_readable=True)
        with pytest.raises(Exception, match="parent issue 5"):
            await connector._files_under_parent({"project": {"id": "10"}}, "5", security)

    async def test_a_parent_the_account_cannot_see_is_not_a_parent(self) -> None:
        connector = _connector()
        connector._get_issue_with_retry = AsyncMock(return_value=MagicMock(status=404))
        security = IssueSecurityContext(knowledge=SchemeKnowledge.PRESENT, members_readable=True)
        assert await connector._files_under_parent({"project": {"id": "10"}}, "5", security) is False
