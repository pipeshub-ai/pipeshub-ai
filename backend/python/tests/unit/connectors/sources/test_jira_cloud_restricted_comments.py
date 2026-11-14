"""Jira Cloud comments only some readers of their issue may read.

Jira shows a comment restricted to a project role or a group (``visibility``) to the people who
can open its issue and are in that role or group, and a Service Management internal note
(``jsdPublic`` false) to those who open the issue in Jira itself rather than through the
customer portal. The issue's content is served to every reader of the issue, so such a comment
is a record of its own under the issue, RESTRICTED to those people.
"""

from __future__ import annotations

import json
import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app.config.constants.arangodb import AccessRule, Connectors
from app.connectors.sources.atlassian.core.jira_issue_security import (
    IssueSecurityContext,
    SchemeKnowledge,
)
from app.connectors.sources.atlassian.jira_cloud.comment_access import (
    restricted_comment_grants,
)
from app.connectors.sources.atlassian.jira_cloud.connector import JiraConnector
from app.connectors.sources.atlassian.jira_cloud_personal.connector import (
    JiraCloudPersonalConnector,
)
from app.models.entities import CommentRecord, OriginTypes, RecordType, TicketRecord
from app.models.permission import EntityType

ROLE_COMMENT = {"id": "11", "author": {"accountId": "acc-a", "displayName": "Ann"},
                "created": "2024-01-15T10:00:00.000+0000", "updated": "2024-01-15T10:00:00.000+0000",
                "visibility": {"type": "role", "value": "Administrators", "identifier": "Administrators"}}
GROUP_COMMENT = {"id": "12", "author": {"accountId": "acc-b", "displayName": "Bo"},
                 "created": "2024-01-16T10:00:00.000+0000", "updated": "2024-01-16T10:00:00.000+0000",
                 "visibility": {"type": "group", "value": "jira-admins", "identifier": "g-admins"}}
INTERNAL_NOTE = {"id": "13", "author": {"accountId": "acc-c", "displayName": "Cy"},
                 "created": "2024-01-17T10:00:00.000+0000", "updated": "2024-01-17T10:00:00.000+0000",
                 "jsdPublic": False}
PUBLIC_COMMENT = {"id": "14", "author": {"accountId": "acc-d", "displayName": "Di"},
                  "created": "2024-01-18T10:00:00.000+0000", "updated": "2024-01-18T10:00:00.000+0000",
                  "jsdPublic": True}

SOURCE: dict[str, Any] = {"origin": OriginTypes.CONNECTOR, "connector_name": Connectors.JIRA}


def _grants(comment: dict[str, Any], *, roles=None, groups=None, product=("g-sw",)) -> set[tuple[str, str]]:
    return {(p.entity_type.value, p.external_id) for p in restricted_comment_grants(
        comment, project_key="PROJ", role_ids=roles if roles is not None else {"Administrators": "10002"},
        group_ids=groups if groups is not None else {"g-admins": "g-admins", "jira-admins": "g-admins"},
        product_access_groups=list(product) if product is not None else None)}


@pytest.mark.parametrize(("comment", "kwargs", "expected"), [
    (ROLE_COMMENT, {}, {("ROLE", "PROJ_10002")}),
    ({**ROLE_COMMENT, "visibility": {"type": "role", "value": "Administrators", "identifier": "10002"}}, {"roles": {}},
     {("ROLE", "PROJ_10002")}),
    # A role this sync could not name grants nobody, never the project.
    (ROLE_COMMENT, {"roles": {}}, set()),
    (GROUP_COMMENT, {}, {("GROUP", "g-admins")}),
    # Named by its name alone (Jira gives the id; an older payload may not).
    ({**GROUP_COMMENT, "visibility": {"type": "group", "value": "jira-admins"}}, {}, {("GROUP", "g-admins")}),
    ({**GROUP_COMMENT, "visibility": {"type": "group", "value": "jira-admins"}}, {"groups": {}}, set()),
    # The id is enough when the group list could not be read.
    (GROUP_COMMENT, {"groups": {}}, {("GROUP", "g-admins")}),
    ({**GROUP_COMMENT, "visibility": {"type": "user", "value": "x"}}, {}, set()),
    ({**GROUP_COMMENT, "visibility": "Administrators"}, {}, set()),
    (INTERNAL_NOTE, {}, {("GROUP", "g-sw")}),
    # Product access unknown: the note stays closed.
    (INTERNAL_NOTE, {"product": None}, set()),
    (INTERNAL_NOTE, {"product": ()}, set()),
])
def test_who_jira_shows_a_restricted_comment_to(comment, kwargs, expected) -> None:
    assert _grants(comment, **kwargs) == expected


def _connector(cls: type[JiraConnector] = JiraConnector) -> JiraConnector:
    dep = MagicMock()
    dep.org_id = "org-1"
    dep.get_record_by_external_id = AsyncMock(return_value=None)
    dep.get_records_by_parent = AsyncMock(return_value=[])
    dep.on_records_deleted_cascade = AsyncMock(return_value={"deleted_records": []})
    config = MagicMock()
    config.get_config = AsyncMock()
    connector = cls(logging.getLogger("test.jira.comments"), dep, MagicMock(), config, "conn-1", "team", "u-1")
    connector._load_issue_security = AsyncMock(return_value=IssueSecurityContext(knowledge=SchemeKnowledge.NONE))
    connector.site_url = "https://site.atlassian.net"
    connector._role_ids = {"Administrators": "10002"}
    connector._group_ids = {"g-admins": "g-admins", "jira-admins": "g-admins"}
    connector._app_roles_mapping = {"jira-software": [{"groupId": "g-sw"}], "jira-servicedesk": [{"groupId": "g-sd"}]}
    return connector


def _issue(comments: list[dict[str, Any]] | None, *, total: int | None = None) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "summary": "Broken login", "description": None, "issuetype": {"name": "Task", "hierarchyLevel": 0},
        "status": {"name": "Open"}, "priority": {"name": "High"}, "creator": None, "reporter": None, "assignee": None,
        "created": "2024-01-01T00:00:00.000+0000", "updated": "2024-01-20T00:00:00.000+0000", "parent": None,
        "attachment": [], "issuelinks": [], "project": {"id": "10000", "key": "PROJ"},
    }
    if comments is not None:
        fields["comment"] = {"comments": comments, "total": len(comments) if total is None else total}
    return {"id": "100", "key": "PROJ-1", "fields": fields}


async def _build(connector: JiraConnector, issue: dict[str, Any]) -> tuple[list, list[str]]:
    records, delete_ids, _ = await connector._build_records_for_issue(issue, "10000", {}, "https://site.atlassian.net", False)
    return records, delete_ids


def _stored_comment(comment_id: str, record_id: str, updated: int) -> CommentRecord:
    return CommentRecord(
        id=record_id, org_id="org-1", record_name="c", record_type=RecordType.COMMENT,
        external_record_id=f"comment_{comment_id}", version=2, connector_id="conn-1", mime_type="text/html", **SOURCE,
        parent_external_record_id="100", source_updated_at=updated, author_source_id="acc-a")


async def test_each_restricted_comment_is_a_restricted_record_under_its_issue() -> None:
    connector = _connector()
    records, delete_ids = await _build(connector, _issue([ROLE_COMMENT, GROUP_COMMENT, INTERNAL_NOTE, PUBLIC_COMMENT]))

    ticket = next(r for r, _, _ in records if isinstance(r, TicketRecord))
    comments = {r.external_record_id: (r, {(p.entity_type, p.external_id) for p in perms}, changed)
                for r, perms, changed in records if isinstance(r, CommentRecord)}
    assert set(comments) == {"comment_11", "comment_12", "comment_13"}  # the public one stays in the issue
    assert comments["comment_11"][1] == {(EntityType.ROLE, "PROJ_10002")}
    assert comments["comment_12"][1] == {(EntityType.GROUP, "g-admins")}
    assert comments["comment_13"][1] == {(EntityType.GROUP, "g-sd"), (EntityType.GROUP, "g-sw")}
    for record, _, changed in comments.values():
        assert record.access_rule == AccessRule.RESTRICTED
        assert record.inherit_permissions and record.rewrite_permissions
        assert record.parent_external_record_id == "100" and record.parent_record_type == RecordType.TICKET
        assert record.parent_node_id == ticket.id and record.is_dependent_node
        assert record.external_record_group_id == "10000" and record.version == 0 and changed
    assert "focusedCommentId=11" in comments["comment_11"][0].weburl
    assert delete_ids == []
    connector.data_entities_processor.get_records_by_parent.assert_not_awaited()  # a new issue has none stored


async def test_a_stored_comment_record_goes_when_its_comment_is_public_or_gone() -> None:
    connector = _connector()
    connector.data_entities_processor.get_record_by_external_id = AsyncMock(return_value=MagicMock(
        id="ticket-1", is_placeholder=False, source_updated_at=1, version=3, parent_external_record_id=None))
    updated = connector._parse_jira_timestamp(ROLE_COMMENT["updated"])
    connector.data_entities_processor.get_records_by_parent = AsyncMock(return_value=[
        _stored_comment("11", "rec-11", updated), _stored_comment("14", "rec-14", 1), _stored_comment("99", "rec-99", 1)])

    records, delete_ids = await _build(connector, _issue([ROLE_COMMENT, PUBLIC_COMMENT]))

    kept = [(r, changed) for r, _, changed in records if isinstance(r, CommentRecord)]
    assert [(r.id, r.version, changed) for r, changed in kept] == [("rec-11", 2, False)]
    assert sorted(delete_ids) == ["rec-14", "rec-99"]
    connector.data_entities_processor.get_records_by_parent.assert_awaited_with("conn-1", "100", "COMMENT")


async def test_comments_beyond_those_embedded_are_read_in_full() -> None:
    connector = _connector()
    connector._read_issue_comments = AsyncMock(return_value=[PUBLIC_COMMENT, GROUP_COMMENT])
    records, _ = await _build(connector, _issue([PUBLIC_COMMENT], total=2))
    assert [r.external_record_id for r, _, _ in records if isinstance(r, CommentRecord)] == ["comment_12"]


async def test_unread_comments_remove_the_stored_comment_records() -> None:
    """A stored comment may now name another role or group: it is closed until the issue is read again."""
    connector = _connector()
    connector.data_entities_processor.get_record_by_external_id = AsyncMock(return_value=MagicMock(
        id="ticket-1", is_placeholder=False, source_updated_at=1, version=3, parent_external_record_id=None))
    connector.data_entities_processor.get_records_by_parent = AsyncMock(return_value=[_stored_comment("11", "rec-11", 1)])
    connector._read_issue_comments = AsyncMock(side_effect=RuntimeError("HTTP 503"))

    records, delete_ids = await _build(connector, _issue([PUBLIC_COMMENT], total=30))
    assert not [r for r, _, _ in records if isinstance(r, CommentRecord)]
    assert delete_ids == ["rec-11"]


async def test_an_issue_read_without_its_comments_keeps_its_comment_records() -> None:
    connector = _connector()
    connector.data_entities_processor.get_record_by_external_id = AsyncMock(return_value=MagicMock(
        id="ticket-1", is_placeholder=False, source_updated_at=1, version=3, parent_external_record_id=None))
    records, delete_ids = await _build(connector, _issue(None))
    assert not [r for r, _, _ in records if isinstance(r, CommentRecord)] and delete_ids == []
    connector.data_entities_processor.get_records_by_parent.assert_not_awaited()


async def test_a_personal_connector_writes_no_comment_record() -> None:
    connector = _connector(JiraCloudPersonalConnector)
    records, _ = await _build(connector, _issue([ROLE_COMMENT]))
    assert not [r for r, _, _ in records if isinstance(r, CommentRecord)]


async def test_a_comment_record_names_the_id_its_issue_was_written_under() -> None:
    """Writing an issue another record left a stub for keeps the stub's id; the comment written after
    it names that id, not the one the issue was built with, even when it sorts before its issue."""
    connector = _connector()
    calls: list[list[tuple[str, str | None]]] = []

    async def write(pairs: list) -> None:
        calls.append([(r.id, getattr(r, "parent_node_id", None)) for r, _ in pairs])
        for r, _ in pairs:
            if isinstance(r, TicketRecord):
                r.id = f"stored-{r.external_record_id}"

    connector.data_entities_processor.on_new_records = AsyncMock(side_effect=write)
    issue = TicketRecord(id="built-9", org_id="org-1", record_name="i", record_type=RecordType.TICKET, external_record_id="9",
                         version=0, connector_id="conn-1", mime_type="text/html", parent_external_record_id="500", **SOURCE)
    comment = _stored_comment("1", "c-1", 1)
    comment.parent_external_record_id, comment.version, comment.parent_node_id = "9", 0, "built-9"
    await connector._process_new_records([(comment, [], True), (issue, [], True)], "PROJ", {"new_count": 0, "updated_count": 0})
    assert calls == [[("built-9", None)], [("c-1", "stored-9")]]


async def test_deleting_an_issue_deletes_its_comment_records_and_their_files() -> None:
    connector = _connector()
    below = {("100", "COMMENT"): [_stored_comment("11", "rec-11", 1)], ("comment_11", "FILE"): [MagicMock(id="file-in-11")]}
    connector.data_entities_processor.get_records_by_parent = AsyncMock(
        side_effect=lambda _connector, parent, record_type: below.get((parent, record_type), []))
    connector.data_entities_processor.on_records_deleted_cascade = AsyncMock(return_value={
        "deleted_records": ["ticket-1", "rec-11", "file-in-11", "file-1"]})
    issue = MagicMock(id="ticket-1", external_record_id="100")
    assert await connector._delete_issue_record(issue, "PROJ-1") == 1
    assert connector.data_entities_processor.on_records_deleted_cascade.await_args.args[0] == [
        "ticket-1", "rec-11", "file-in-11"]


def _response(status: int, body: dict[str, Any]) -> MagicMock:
    resp = MagicMock(status=status)
    resp.json = MagicMock(return_value=body)
    resp.text = MagicMock(return_value=json.dumps(body))
    return resp


async def _stream(
    connector: JiraConnector, record: CommentRecord, issue: dict[str, Any], comment: dict[str, Any],
) -> dict[str, Any]:
    ds = MagicMock()
    ds.get_issue = AsyncMock(return_value=_response(200, issue))
    ds.get_comment = AsyncMock(return_value=_response(200, comment))
    connector._get_fresh_datasource = AsyncMock(return_value=ds)
    connector.data_source = ds
    response = await connector.stream_record(record)
    return json.loads(b"".join([c async for c in response.body_iterator]))


def _comment_record() -> CommentRecord:
    record = _stored_comment("12", "rec-12", 1)
    record.external_record_group_id = "10000"
    return record


async def test_a_comment_record_streams_its_issue_title_and_its_comment() -> None:
    connector = _connector()
    issue = {"id": "100", "key": "PROJ-1", "fields": {"summary": "Broken login", "attachment": [],
                                                       "project": {"id": "10000", "key": "PROJ"}}}
    body = await _stream(connector, _comment_record(), issue, {**GROUP_COMMENT, "renderedBody": "<p>admins only</p>"})
    groups = body["block_groups"]
    assert groups[0]["data"] == "<h1>[PROJ-1] Broken login</h1>"
    assert [g["data"] for g in groups if g["source_group_id"] == "12"] == ["<p>admins only</p>"]


async def test_a_comment_record_of_a_moved_issue_is_not_served() -> None:
    connector = _connector()
    issue = {"id": "100", "key": "OPS-1", "fields": {"summary": "Broken login", "attachment": [],
                                                      "project": {"id": "20000", "key": "OPS"}}}
    with pytest.raises(HTTPException) as caught:
        await _stream(connector, _comment_record(), issue, {**GROUP_COMMENT, "renderedBody": "<p>admins only</p>"})
    assert caught.value.status_code == 404


async def test_the_issue_content_holds_no_restricted_comment_or_internal_note() -> None:
    connector = _connector()
    comments = [{**c, "renderedBody": f"<p>text {c['id']}</p>"} for c in (ROLE_COMMENT, GROUP_COMMENT, INTERNAL_NOTE, PUBLIC_COMMENT)]
    issue = {"id": "100", "key": "PROJ-1", "fields": {
        "summary": "Broken login", "description": None, "attachment": [], "project": {"id": "10000", "key": "PROJ"},
        "comment": {"comments": comments, "total": 4}}, "renderedFields": {"description": ""}}
    ds = MagicMock()
    ds.get_issue = AsyncMock(return_value=_response(200, issue))
    connector._get_fresh_datasource = AsyncMock(return_value=ds)
    ticket = TicketRecord(id="t-1", org_id="org-1", record_name="i", record_type=RecordType.TICKET, external_record_id="100",
                          version=0, connector_id="conn-1", mime_type="text/html", external_record_group_id="10000",
                          **SOURCE)
    body = json.loads(await connector._process_issue_blockgroups_for_streaming(ticket))
    assert {g["source_group_id"] for g in body["block_groups"]} & {"11", "12", "13", "14"} == {"14"}
