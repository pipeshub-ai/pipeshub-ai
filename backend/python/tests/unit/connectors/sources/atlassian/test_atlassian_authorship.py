"""Who wrote and last changed Atlassian records, from the payloads the sync
already fetches: Confluence pages, blog posts and attachments; Jira tickets
(by account id when emails are hidden) and attachments."""
from __future__ import annotations

import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.connectors.sources.atlassian.confluence_cloud.connector import (
    ConfluenceConnector,
)
from app.connectors.sources.atlassian.confluence_datacenter.connector import (
    ConfluenceDataCenterConnector,
)
from app.connectors.sources.atlassian.confluence_datacenter_personal.connector import (
    ConfluenceDataCenterPersonalConnector,
)
from app.connectors.sources.atlassian.jira_cloud.connector import JiraConnector
from app.connectors.sources.atlassian.jira_cloud_personal.connector import (
    JiraCloudPersonalConnector,
)
from app.connectors.sources.atlassian.jira_data_center.connector import (
    JiraDataCenterConnector,
)
from app.connectors.sources.atlassian.jira_data_center_personal.connector import (
    JiraDataCenterPersonalConnector,
)
from app.models.entities import (
    AppUser,
    FileRecord,
    RecordGroupType,
    RecordType,
    SourcePerson,
    TicketRecord,
)

CLOUD_ANN = {
    "type": "known", "accountId": "557058:ann", "accountType": "atlassian",
    "email": "", "publicName": "Ann Lee", "displayName": "Ann Lee",
}
CLOUD_BOB = {
    "type": "known", "accountId": "557058:bob", "accountType": "atlassian",
    "email": "bob@acme.com", "publicName": "Bob", "displayName": "Bob Stone",
}
DC_ANN = {"type": "known", "username": "ann", "userKey": "8a8a0001", "displayName": "Ann Lee"}
DC_BOB = {"type": "known", "username": "bob", "userKey": "8a8a0002", "displayName": "Bob Stone"}


def _deps() -> tuple[logging.Logger, MagicMock, MagicMock, MagicMock]:
    dep = MagicMock()
    dep.org_id = "org-1"
    dep.get_record_by_external_id = AsyncMock(return_value=None)
    dep.get_all_app_users = AsyncMock(return_value=[])
    cs = MagicMock()
    cs.get_config = AsyncMock()
    return logging.getLogger("test.atlassian.authorship"), dep, MagicMock(), cs


def _connector(cls: type) -> Any:  # noqa: ANN401
    logger, dep, dsp, cs = _deps()
    return cls(logger, dep, dsp, cs, "conn-1", "team", "creator-1")


def _v1_page(created_by: dict, last_by: dict) -> dict[str, Any]:
    return {
        "id": "98765", "type": "page", "title": "Runbook",
        "space": {"id": 11, "key": "ENG"},
        "history": {
            "latest": True, "createdBy": created_by, "createdDate": "2024-01-02T10:00:00.000Z",
            "lastUpdated": {"by": last_by, "when": "2024-03-04T10:00:00.000Z", "number": 7},
        },
        "_links": {"webui": "/spaces/ENG/pages/98765", "self": "https://acme.atlassian.net/wiki/rest/api/content/98765"},
    }


def _v1_attachment(created_by: dict, version_by: dict, number: int = 2) -> dict[str, Any]:
    return {
        "id": "att123", "type": "attachment", "title": "diagram.png",
        "history": {"createdBy": created_by, "createdDate": "2024-01-02T10:00:00.000Z"},
        "version": {"by": version_by, "when": "2024-03-04T10:00:00.000Z", "number": number},
        "extensions": {"mediaType": "image/png", "fileSize": 10},
        "_links": {"webui": "/download/attachments/98765/diagram.png"},
    }


class TestConfluencePages:
    @pytest.mark.parametrize("record_type", [RecordType.CONFLUENCE_PAGE, RecordType.CONFLUENCE_BLOGPOST])
    def test_cloud_v1_author_and_last_editor(self, record_type: RecordType) -> None:
        record = _connector(ConfluenceConnector)._transform_to_webpage_record(_v1_page(CLOUD_ANN, CLOUD_BOB), record_type)
        assert record.authored_by == SourcePerson(source_id="557058:ann", display_name="Ann Lee")
        assert record.last_modified_by == SourcePerson(
            source_id="557058:bob", email="bob@acme.com", display_name="Bob Stone",
        )
        assert record.created_by is None

    def test_cloud_v2_author_and_version_author(self) -> None:
        page = {
            "id": "98765", "title": "Runbook", "spaceId": "11", "authorId": "557058:ann", "ownerId": "557058:zed",
            "createdAt": "2024-01-02T10:00:00.000Z",
            "version": {"number": 7, "authorId": "557058:bob", "createdAt": "2024-03-04T10:00:00.000Z"},
            "_links": {"webui": "/spaces/ENG/pages/98765", "base": "https://acme.atlassian.net/wiki"},
        }
        record = _connector(ConfluenceConnector)._transform_to_webpage_record(page, RecordType.CONFLUENCE_PAGE)
        assert record.authored_by == SourcePerson(source_id="557058:ann")
        assert record.last_modified_by == SourcePerson(source_id="557058:bob")

    def test_an_app_editor_is_a_service_account(self) -> None:
        app = {"type": "known", "accountId": "557058:bot", "accountType": "app", "displayName": "Automation"}
        record = _connector(ConfluenceConnector)._transform_to_webpage_record(
            _v1_page(CLOUD_ANN, app), RecordType.CONFLUENCE_PAGE,
        )
        assert record.last_modified_by.is_service_account

    def test_an_anonymous_creator_names_nobody(self) -> None:
        record = _connector(ConfluenceConnector)._transform_to_webpage_record(
            _v1_page({"type": "anonymous"}, CLOUD_BOB), RecordType.CONFLUENCE_PAGE,
        )
        assert record.authored_by is None

    @pytest.mark.parametrize("cls", [ConfluenceDataCenterConnector, ConfluenceDataCenterPersonalConnector])
    def test_data_center_names_people_by_user_key(self, cls: type) -> None:
        record = _connector(cls)._transform_to_webpage_record(_v1_page(DC_ANN, DC_BOB), RecordType.CONFLUENCE_PAGE)
        assert record.authored_by == SourcePerson(source_id="8a8a0001", display_name="Ann Lee")
        assert record.last_modified_by == SourcePerson(source_id="8a8a0002", display_name="Bob Stone")


class TestConfluenceAttachments:
    def test_cloud_v1_attachment(self) -> None:
        record = _connector(ConfluenceConnector)._transform_to_attachment_file_record(
            _v1_attachment(CLOUD_ANN, CLOUD_BOB), "98765", "11",
        )
        assert (record.authored_by.source_id, record.last_modified_by.source_id) == ("557058:ann", "557058:bob")

    def test_cloud_v2_attachment_first_version_author_is_the_author(self) -> None:
        attachment = {
            "id": "att123", "title": "diagram.png", "mediaType": "image/png", "fileSize": 10,
            "createdAt": "2024-01-02T10:00:00.000Z",
            "version": {"number": 1, "authorId": "557058:ann", "createdAt": "2024-01-02T10:00:00.000Z"},
        }
        record = _connector(ConfluenceConnector)._transform_to_attachment_file_record(attachment, "98765", "11")
        assert record.authored_by == record.last_modified_by == SourcePerson(source_id="557058:ann")

        attachment["version"] = {**attachment["version"], "number": 2}
        record = _connector(ConfluenceConnector)._transform_to_attachment_file_record(attachment, "98765", "11")
        assert (record.authored_by, record.last_modified_by) == (None, SourcePerson(source_id="557058:ann"))

    @pytest.mark.parametrize("cls", [ConfluenceDataCenterConnector, ConfluenceDataCenterPersonalConnector])
    def test_data_center_attachment(self, cls: type) -> None:
        record = _connector(cls)._transform_to_attachment_file_record(_v1_attachment(DC_ANN, DC_BOB), "98765", "11")
        assert (record.authored_by.source_id, record.last_modified_by.source_id) == ("8a8a0001", "8a8a0002")


def _jira_cloud_user(account_id: str, name: str, email: str | None = None) -> dict[str, Any]:
    user = {"accountId": account_id, "accountType": "atlassian", "displayName": name, "active": True}
    if email:
        user["emailAddress"] = email
    return user


def _issue(creator: dict, reporter: dict, assignee: dict | None, attachments: list | None = None) -> dict[str, Any]:
    return {
        "id": "10001", "key": "ENG-1",
        "fields": {
            "summary": "Fix login", "issuetype": {"name": "Task"}, "status": {"name": "To Do"},
            "priority": {"name": "Medium"}, "project": {"id": "10000", "key": "ENG"},
            "created": "2024-05-01T09:00:00.000+0000", "updated": "2024-05-02T09:00:00.000+0000",
            "creator": creator, "reporter": reporter, "assignee": assignee,
            "attachment": attachments or [],
        },
    }


def _app_user(source_id: str, email: str) -> AppUser:
    return AppUser(app_name="JIRA", connector_id="conn-1", source_user_id=source_id, email=email, full_name=email)


async def _tickets(connector: Any, issue: dict, users: list[AppUser]) -> tuple[TicketRecord, list[FileRecord]]:  # noqa: ANN401
    built = await connector._build_issue_records([issue], "10000", users)
    records = [entry[0] for entry in (built[0] if isinstance(built, tuple) else built)]
    (ticket,) = [r for r in records if isinstance(r, TicketRecord)]
    return ticket, [r for r in records if isinstance(r, FileRecord)]


ANN = _jira_cloud_user("acc-ann", "Ann Lee")
BOB = _jira_cloud_user("acc-bob", "Bob Stone")
CAT = _jira_cloud_user("acc-cat", "Cat Diaz")


class TestJiraTicketPeople:
    async def test_with_every_email_known_the_ticket_keeps_its_emails(self) -> None:
        users = [_app_user("acc-ann", "ann@acme.com"), _app_user("acc-bob", "bob@acme.com"),
                 _app_user("acc-cat", "cat@acme.com")]
        ticket, _ = await _tickets(_connector(JiraConnector), _issue(ANN, BOB, CAT), users)
        assert (ticket.creator_email, ticket.reporter_email, ticket.assignee_email) == (
            "ann@acme.com", "bob@acme.com", "cat@acme.com",
        )
        assert not ticket.is_email_hidden
        assert ticket.created_by is None

    async def test_a_hidden_email_switches_the_ticket_to_account_ids(self) -> None:
        users = [_app_user("acc-ann", "ann@acme.com"), _app_user("acc-bob", "bob@acme.com")]
        ticket, _ = await _tickets(_connector(JiraConnector), _issue(ANN, BOB, CAT), users)
        assert ticket.is_email_hidden
        assert (ticket.reporter_source_id, ticket.assignee_source_id) == ("acc-bob", ["acc-cat"])
        assert ticket.created_by == SourcePerson(source_id="acc-ann", email="ann@acme.com", display_name="Ann Lee")
        assert ticket.reporter_email == "bob@acme.com"

    async def test_an_inline_email_counts_as_known(self) -> None:
        issue = _issue(_jira_cloud_user("acc-ann", "Ann Lee", "ann@acme.com"), _jira_cloud_user("acc-bob", "Bob", "bob@acme.com"), None)
        ticket, _ = await _tickets(_connector(JiraConnector), issue, [])
        assert (ticket.creator_email, ticket.reporter_email) == ("ann@acme.com", "bob@acme.com")
        assert not ticket.is_email_hidden

    async def test_an_app_reporter_neither_hides_emails_nor_is_named(self) -> None:
        bot = {"accountId": "acc-bot", "accountType": "app", "displayName": "Automation for Jira"}
        users = [_app_user("acc-ann", "ann@acme.com")]
        ticket, _ = await _tickets(_connector(JiraConnector), _issue(ANN, bot, None), users)
        assert not ticket.is_email_hidden

        ticket, _ = await _tickets(_connector(JiraConnector), _issue(bot, bot, CAT), users)
        assert ticket.is_email_hidden
        assert (ticket.reporter_source_id, ticket.assignee_source_id, ticket.created_by) == (None, ["acc-cat"], None)

    async def test_personal_connector_names_everyone_by_account_id(self) -> None:
        ticket, _ = await _tickets(_connector(JiraCloudPersonalConnector), _issue(ANN, BOB, CAT), [])
        assert ticket.is_email_hidden
        assert (ticket.reporter_source_id, ticket.reporter_name) == ("acc-bob", "Bob Stone")
        assert (ticket.assignee_source_id, ticket.assignee) == (["acc-cat"], "Cat Diaz")
        assert ticket.created_by == SourcePerson(source_id="acc-ann", display_name="Ann Lee")

    @pytest.mark.parametrize("cls", [JiraDataCenterConnector, JiraDataCenterPersonalConnector])
    async def test_data_center_names_people_by_user_key(self, cls: type) -> None:
        def dc_user(key: str, name: str) -> dict[str, Any]:
            return {"key": key, "name": key.removeprefix("JIRAUSER"), "displayName": name}

        connector = _connector(cls)
        issue = _issue(dc_user("JIRAUSER1", "Ann Lee"), dc_user("JIRAUSER2", "Bob Stone"), dc_user("JIRAUSER3", "Cat"))
        ticket, _ = await _tickets(connector, issue, [_app_user("JIRAUSER1", "ann@acme.com")])
        assert ticket.is_email_hidden
        assert (ticket.reporter_source_id, ticket.assignee_source_id) == ("JIRAUSER2", ["JIRAUSER3"])
        assert ticket.created_by == SourcePerson(source_id="JIRAUSER1", email="ann@acme.com", display_name="Ann Lee")


class TestJiraAttachments:
    ATTACHMENT = {
        "id": "20001", "filename": "trace.log", "mimeType": "text/plain", "size": 42,
        "created": "2024-05-01T09:30:00.000+0000",
        "author": _jira_cloud_user("acc-cat", "Cat Diaz", "cat@acme.com"),
    }

    async def test_cloud_attachment_is_authored_by_its_uploader(self) -> None:
        records = await _connector(JiraConnector)._fetch_issue_attachments(
            "10001", "ENG-1", {"attachment": [self.ATTACHMENT]}, [], "10000", RecordGroupType.PROJECT,
        )
        (record, _, _) = records[0]
        assert record.authored_by == SourcePerson(source_id="acc-cat", email="cat@acme.com", display_name="Cat Diaz")

    async def test_data_center_attachment_is_authored_by_its_uploader(self) -> None:
        attachment = {**self.ATTACHMENT, "author": {"key": "JIRAUSER3", "name": "cat", "displayName": "Cat Diaz"}}
        records = await _connector(JiraDataCenterConnector)._fetch_issue_attachments(
            "10001", "ENG-1", {"attachment": [attachment]}, [], "10000", RecordGroupType.PROJECT,
        )
        (record, _) = records[0]
        assert record.authored_by == SourcePerson(source_id="JIRAUSER3", display_name="Cat Diaz")
