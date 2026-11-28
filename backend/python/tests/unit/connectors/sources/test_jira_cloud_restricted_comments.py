"""Jira Cloud comments only some readers of their issue may read.

Jira shows a comment restricted to a project role or a group (``visibility``) to the people who
can open its issue and are in that role or group, and a Service Management internal note
(``jsdPublic`` false) to those who open the issue in Jira itself rather than through the
customer portal. The issue's content is served to every reader of the issue, so such a comment
is a record of its own under the issue (PARENT_AND_GRANT), read by those people only.
"""

from __future__ import annotations

import json
import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app.config.constants.arangodb import AccessRule, Connectors
from app.ee.permission_sync.jira_issue_security import (
    IssueSecurityContext,
    SchemeKnowledge,
)
from app.ee.permission_sync.jira_comment_access import (
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
        group_ids_by_name=groups if groups is not None else {"jira-admins": "g-admins"},
        product_access_groups=list(product) if product is not None else None)}


@pytest.mark.parametrize(("comment", "kwargs", "expected"), [
    (ROLE_COMMENT, {}, {("ROLE", "PROJ_10002")}),
    ({**ROLE_COMMENT, "visibility": {"type": "role", "value": "Administrators", "identifier": "10002"}}, {"roles": {}},
     {("ROLE", "PROJ_10002")}),
    # A role this sync could not name grants nobody, never the project.
    (ROLE_COMMENT, {"roles": {}}, set()),
    (GROUP_COMMENT, {}, {("GROUP", "g-admins")}),
    # Named by its name alone (Jira gives the id; an older payload may not), through this sync's read only.
    ({**GROUP_COMMENT, "visibility": {"type": "group", "value": "jira-admins"}}, {}, {("GROUP", "g-admins")}),
    ({**GROUP_COMMENT, "visibility": {"type": "group", "value": "jira-admins"}}, {"groups": {}}, set()),
    # The explicit id is enough when the group list could not be read.
    (GROUP_COMMENT, {"groups": {}}, {("GROUP", "g-admins")}),
    # The read gives the name another id (renamed, or re-created): neither is trusted.
    (GROUP_COMMENT, {"groups": {"jira-admins": "g-new"}}, set()),
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
    dep.skips_permission_writes = AsyncMock(return_value=False)
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
    connector._group_ids_by_name = {"jira-admins": "g-admins"}
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


def _stored_below(connector: JiraConnector, below: dict[tuple[str, str], list]) -> None:
    """Stored records by (parent external id, record type), as get_records_by_parent answers."""
    async def by_parent(*args: str, **kwargs: str) -> list:
        parent = kwargs.get("parent_external_record_id", args[1] if len(args) > 1 else None)
        record_type = kwargs.get("record_type", args[2] if len(args) > 2 else None)
        return below.get((parent, record_type), [])
    connector.data_entities_processor.get_records_by_parent = AsyncMock(side_effect=by_parent)


def _stored_comment(comment_id: str, record_id: str, updated: int) -> CommentRecord:
    return CommentRecord(
        id=record_id, org_id="org-1", record_name="c", record_type=RecordType.COMMENT,
        external_record_id=f"comment_{comment_id}", version=2, connector_id="conn-1", mime_type="text/html", **SOURCE,
        parent_external_record_id="100", source_updated_at=updated, author_source_id="acc-a")


async def test_each_restricted_comment_is_a_record_under_its_issue_that_needs_the_issue_and_its_own_grant() -> None:
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
        assert record.access_rule == AccessRule.PARENT_AND_GRANT
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
    _stored_below(connector, {
        ("100", "COMMENT"): [_stored_comment("11", "rec-11", updated), _stored_comment("14", "rec-14", 1),
                             _stored_comment("99", "rec-99", 1)],
        ("comment_99", "FILE"): [MagicMock(id="file-in-99")]})

    records, delete_ids = await _build(connector, _issue([ROLE_COMMENT, PUBLIC_COMMENT]))

    kept = [(r, changed) for r, _, changed in records if isinstance(r, CommentRecord)]
    assert [(r.id, r.version, changed) for r, changed in kept] == [("rec-11", 2, False)]
    # A file filed under a comment that goes, goes with it: left behind it would no longer be held
    # to the comment's readers. One still on the issue is filed again by this read.
    assert sorted(delete_ids) == ["file-in-99", "rec-14", "rec-99"]


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
    _stored_below(connector, {("100", "COMMENT"): [_stored_comment("11", "rec-11", 1)],
                              ("comment_11", "FILE"): [MagicMock(id="file-in-11")]})
    connector._read_issue_comments = AsyncMock(side_effect=RuntimeError("HTTP 503"))

    records, delete_ids = await _build(connector, _issue([PUBLIC_COMMENT], total=30))
    assert not [r for r, _, _ in records if isinstance(r, CommentRecord)]
    assert delete_ids == ["rec-11", "file-in-11"]


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
    _stored_below(connector, {("100", "COMMENT"): [_stored_comment("11", "rec-11", 1)],
                              ("comment_11", "FILE"): [MagicMock(id="file-in-11")]})
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


@pytest.mark.parametrize(("shown_in", "offered"), [(("11", "12"), set()), (("12",), {"901"})],
                         ids=["shown_by_two_restricted_comments", "shown_by_this_comment_alone"])
async def test_a_comment_record_inlines_only_the_files_no_other_restricted_comment_shows(shown_in, offered) -> None:
    """Jira applies a restricted comment's restriction to a file attached in it. A file another restricted comment
    also shows may be restricted to other people, so this comment's content does not embed it."""
    connector = _connector()
    connector._rendered_description = AsyncMock(return_value="")
    img = '<p><img src="/rest/api/3/attachment/content/901"/></p>'
    comments = [{**c, "renderedBody": img if c["id"] in shown_in else "<p>no file</p>"} for c in (ROLE_COMMENT, GROUP_COMMENT)]
    issue = {"id": "100", "key": "PROJ-1", "fields": {
        "summary": "Broken login", "description": None, "project": {"id": "10000", "key": "PROJ"},
        "attachment": [{"id": "901", "filename": "shot.png", "mimeType": "image/png"}],
        "comment": {"comments": comments, "total": 2}}}
    seen: list[set[str]] = []
    parse = connector._parse_issue_to_blocks

    async def spy(**kwargs: Any) -> Any:  # noqa: ANN401
        seen.append({str(a["id"]) for a in kwargs["issue_data"]["fields"]["attachment"]})
        return await parse(**kwargs)

    connector._parse_issue_to_blocks = spy
    connector._download_attachment_base64 = AsyncMock(return_value=None)
    await _stream(connector, _comment_record(), issue, {**GROUP_COMMENT, "renderedBody": img})
    assert seen == [offered]


async def test_a_role_name_read_by_an_earlier_sync_is_not_kept_when_this_sync_could_not_list_roles() -> None:
    """A role an earlier sync read as Administrators -> 10002 may since be renamed and the name given to another role:
    a comment naming the role by name alone must then grant nobody (and be read again), not role 10002."""
    connector = _connector()
    connector.data_source = MagicMock()
    connector.notify = AsyncMock()
    connector.data_entities_processor.on_new_app_roles = AsyncMock()
    connector._call_with_retry = AsyncMock(return_value=_response(500, {}))
    assert connector._role_ids == {"Administrators": "10002"}
    await connector._sync_project_roles(["PROJ"], [], {})
    assert connector._role_ids == {}


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


async def test_a_file_removed_from_an_issue_goes_when_it_was_filed_under_a_comment() -> None:
    """The changelog's id comparison also covers files filed under the issue's comment records."""
    connector = _connector()
    in_comment = MagicMock(id="file-in-11", external_record_id="attachment_777", record_name="figures.txt")
    _stored_below(connector, {("100", "COMMENT"): [_stored_comment("11", "rec-11", 1)],
                              ("comment_11", "FILE"): [in_comment]})
    issue = _issue([PUBLIC_COMMENT])
    issue["changelog"] = {"histories": [{"items": []}], "total": 2}  # truncated: the ids are compared
    assert await connector._handle_attachment_deletions_from_changelog(issue) == ["file-in-11"]


async def test_a_group_name_resolves_only_through_the_sync_that_read_every_group() -> None:
    connector = _connector()
    connector._fetch_groups = AsyncMock(return_value=([], True))  # the group list could not be read
    connector._group_bulk_forbidden = False
    connector._group_fetch_interrupted = False
    connector.notify = AsyncMock()
    await connector._sync_user_groups([])
    assert connector._group_ids_by_name == {}


def _reindexable(connector: JiraConnector, issue: dict[str, Any]) -> None:
    ds = MagicMock()
    ds.get_issue = AsyncMock(return_value=_response(200, issue))
    connector._get_fresh_datasource = AsyncMock(return_value=ds)
    connector.indexing_filters = MagicMock()
    connector._attach_browse_holders = AsyncMock(return_value=IssueSecurityContext(knowledge=SchemeKnowledge.NONE))
    connector._learn_parent_levels = AsyncMock()


def _stored_issue() -> TicketRecord:
    return TicketRecord(id="ticket-1", org_id="org-1", record_name="i", record_type=RecordType.TICKET,
                        external_record_id="100", version=1, connector_id="conn-1", mime_type="text/html",
                        external_record_group_id="10000", source_updated_at=1, **SOURCE)


@pytest.mark.parametrize(("comments", "stored", "left_to_the_sync"), [
    # A comment given to another group (or deleted) since the sync: its stored record must be rebuilt.
    ([GROUP_COMMENT], [_stored_comment("12", "rec-12", 1)], True),
    ([PUBLIC_COMMENT], [_stored_comment("12", "rec-12", 1)], True),
    # A restricted comment added since the sync has no record yet.
    ([GROUP_COMMENT], [], True),
    # No restricted comment, none stored: the reindex writes the issue as before.
    ([PUBLIC_COMMENT], [], False),
])
async def test_a_reindex_writes_the_issue_and_leaves_its_restricted_comments_to_the_sync(
    comments: list[dict[str, Any]], stored: list[CommentRecord], left_to_the_sync: bool,
) -> None:
    """The issue is written now; with restricted comments it keeps its stored timestamp, so the next sync still reads
    it as changed and rebuilds the comment records (the moved timestamp would make that sync skip it)."""
    connector = _connector()
    _stored_below(connector, {("100", "COMMENT"): stored})
    _reindexable(connector, _issue(comments))
    result = await connector._check_and_fetch_updated_issue(_stored_issue(), {})
    assert result is not None
    assert result[0].source_updated_at == (1 if left_to_the_sync else 1705708800000)
    assert all(not isinstance(r, CommentRecord) for r in result[:1])  # the issue alone: no comment record is written


async def test_a_level_set_on_an_issue_with_a_restricted_comment_closes_the_issue_at_once_on_a_reindex() -> None:
    """A level is set at the source on an issue that has a restricted comment, and the issue is reindexed before the
    next sync: its record is closed to everyone outside the level now (no one the source just removed keeps reading
    it); its comment records are left to that sync and inherit the issue, so they close with it."""
    connector = _connector()
    _stored_below(connector, {("100", "COMMENT"): [_stored_comment("12", "rec-12", 1)]})
    leveled = _issue([GROUP_COMMENT])
    leveled["fields"]["security"] = {"id": "10102", "name": "Leadership"}
    _reindexable(connector, leveled)
    connector._attach_browse_holders = AsyncMock(return_value=IssueSecurityContext(
        knowledge=SchemeKnowledge.PRESENT, scheme_id="1", members_readable=True,
        members_by_level={"10102": [{"type": "group", "value": "g-lead", "group": {"groupId": "g-lead"}}]}))
    connector.data_entities_processor.on_new_records = AsyncMock()
    connector.data_entities_processor.on_new_record_groups = AsyncMock()
    connector.data_entities_processor.skips_permission_writes = AsyncMock(return_value=False)
    result = await connector._check_and_fetch_updated_issue(_stored_issue(), {})
    assert result is not None
    record, grants = result
    # The level's people read it through the level's group, and only through it.
    assert (record.access_rule, record.access_group_external_id) == (AccessRule.STRICT, "level:10000:10102")
    assert record.rewrite_permissions is True and grants == []
    (group, group_grants), = connector.data_entities_processor.on_new_record_groups.await_args.args[0]
    assert group.external_group_id == "level:10000:10102"
    assert {(g.entity_type, g.external_id) for g in group_grants} == {(EntityType.GROUP, "g-lead")}
    assert record.source_updated_at == 1  # still changed for the next sync, which rebuilds the comment records
    connector.data_entities_processor.on_new_records.assert_not_awaited()


def _stored_ticket(source_updated_at: int = 1) -> MagicMock:
    return MagicMock(id="ticket-1", is_placeholder=False, source_updated_at=source_updated_at, version=3, parent_external_record_id=None)


def _ticket(records: list) -> TicketRecord:
    return next(r for r, _, _ in records if isinstance(r, TicketRecord))


async def test_an_issue_whose_comments_could_not_be_read_stays_changed_for_the_next_sync() -> None:
    """The stored comment records go (fail closed) and the issue keeps the time of its last complete read, so the next sync reads it and its
    comments again rather than skipping it as unchanged."""
    connector = _connector()
    connector.data_entities_processor.get_record_by_external_id = AsyncMock(return_value=_stored_ticket(1))
    connector._read_issue_comments = AsyncMock(side_effect=RuntimeError("HTTP 503"))
    records, _ = await _build(connector, _issue([PUBLIC_COMMENT], total=30))
    assert _ticket(records).source_updated_at == 1
    assert "100" not in connector._settle_after_dependents


async def test_an_issue_whose_comment_grants_could_not_be_resolved_stays_changed_for_the_next_sync() -> None:
    """A fresh connector that could not read the project roles writes the comment with no grant (fail closed) and leaves the issue changed: the
    next sync, which may read the roles, rebuilds the comment instead of skipping the issue."""
    connector = _connector()
    connector._role_ids = {}
    connector.data_entities_processor.get_record_by_external_id = AsyncMock(return_value=_stored_ticket(1))
    records, _ = await _build(connector, _issue([ROLE_COMMENT]))
    comment = next((r, p) for r, p, _ in records if isinstance(r, CommentRecord))
    assert comment[1] == []
    assert _ticket(records).source_updated_at == 1 and "100" not in connector._settle_after_dependents
    # a new issue in the same case is stored as never read
    connector.data_entities_processor.get_record_by_external_id = AsyncMock(return_value=None)
    records, _ = await _build(connector, _issue([ROLE_COMMENT]))
    assert _ticket(records).source_updated_at == 0


async def test_an_issue_takes_the_time_of_its_read_only_once_its_comments_are_written() -> None:
    """The issue is written first (its comments name it), stored as changed, and takes this read's time by a metadata write after every comment
    and file of the call is written. A comment batch that raises leaves it changed, so the next sync writes the comments again."""
    read_at = JiraConnector._parse_jira_timestamp(_connector(), "2024-01-20T00:00:00.000+0000")
    connector = _connector()
    connector.data_entities_processor.get_record_by_external_id = AsyncMock(return_value=_stored_ticket(1))
    records, _ = await _build(connector, _issue([GROUP_COMMENT]))
    assert _ticket(records).source_updated_at == 1 and connector._settle_after_dependents == {"100": read_at}
    dep = connector.data_entities_processor
    dep.on_new_records, dep.on_record_content_update, dep.on_record_metadata_update = AsyncMock(), AsyncMock(), AsyncMock()
    await connector._process_new_records(records, "PROJ", {"new_count": 0, "updated_count": 0})
    dep.on_record_metadata_update.assert_awaited_once()
    assert dep.on_record_metadata_update.await_args.args[0].source_updated_at == read_at and not connector._settle_after_dependents

    connector = _connector()
    connector.data_entities_processor.get_record_by_external_id = AsyncMock(return_value=_stored_ticket(1))
    records, _ = await _build(connector, _issue([GROUP_COMMENT]))
    dep = connector.data_entities_processor

    async def comments_fail(batch: list) -> None:
        if any(isinstance(r, CommentRecord) for r, _ in batch):
            raise RuntimeError("store down")
    dep.on_new_records, dep.on_record_content_update, dep.on_record_metadata_update = AsyncMock(side_effect=comments_fail), AsyncMock(), AsyncMock()
    with pytest.raises(RuntimeError):
        await connector._process_new_records(records, "PROJ", {"new_count": 0, "updated_count": 0})
    written = [r for call in dep.on_record_content_update.await_args_list for r in call.args[:1]] + [
        r for call in dep.on_new_records.await_args_list for r, _ in call.args[0]]
    assert next(r for r in written if isinstance(r, TicketRecord)).source_updated_at == 1
    dep.on_record_metadata_update.assert_not_awaited()


async def test_an_unlicensed_sync_does_not_hold_an_issue_back_for_comment_grants_it_never_reads() -> None:
    """Without a licence key no role or group is read, so a role comment has no grant by design, not by failure: the
    comment is still written (the processor keeps its stored grants) and the issue settles, not retried every sync."""
    read_at = JiraConnector._parse_jira_timestamp(_connector(), "2024-01-20T00:00:00.000+0000")
    connector = _connector()
    connector.data_entities_processor.skips_permission_writes = AsyncMock(return_value=True)
    connector._role_ids = {}
    connector.data_entities_processor.get_record_by_external_id = AsyncMock(return_value=_stored_ticket(1))
    records, _ = await _build(connector, _issue([ROLE_COMMENT]))
    assert [p for r, p, _ in records if isinstance(r, CommentRecord)] == [[]]
    assert connector._settle_after_dependents == {"100": read_at}
