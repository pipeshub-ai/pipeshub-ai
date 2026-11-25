"""Jira Data Center comments restricted to a group or a project role, over a fake Jira REST API.

Jira shows such a comment to the readers of its issue who are in the group, or hold the role in the issue's project.
The issue's content is served to everyone who opens the issue, so it holds only public comments; each restricted one is
a record of its own under the issue (PARENT_AND_GRANT), granted to that group or role. Data Center names both by name
(``visibility: {type: group|role, value}``, the same in the 10.2, 10.7, 11.0 and 11.3 specifications), so each test runs
against the oldest, a middle and the newest supported release.
"""

import base64
import json
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
from atlassian_behaviour_fakes import (
    AtlassianApiStub,
    FakeCheckpointStore,
    json_response,
    run_full_sync,
)
from test_jira_dc_sync_behaviour import (  # the stub site and fakes the sync tests use
    API,
    IssueSearch,
    JiraRecordsDb,
    issue,
    make_connector,
    stub_site,
    tickets,
    ts,
)

from app.config.constants.arangodb import AccessRule, CollectionNames
from app.connectors.sources.atlassian.jira_data_center import connector as dc
from app.models.entities import CommentRecord, RecordType
from app.models.permission import EntityType
from app.sources.client.http.http_client import HTTPClient

VERSIONS = {"10.2": [10, 2, 0], "10.7": [10, 7, 4], "11.3": [11, 3, 13]}


@pytest.fixture
def db() -> JiraRecordsDb:
    return JiraRecordsDb()


@pytest.fixture
def store() -> FakeCheckpointStore:
    return FakeCheckpointStore()


@pytest.fixture
def search() -> IssueSearch:
    return IssueSearch()


@pytest.fixture
def jira(atlassian_api: AtlassianApiStub, monkeypatch: pytest.MonkeyPatch) -> AtlassianApiStub:
    """Every real ``HTTPClient`` talks to the stub, as in the sync tests."""

    async def _ensure_client(self: HTTPClient) -> httpx.AsyncClient:
        if self.client is None:
            atlassian_api.install(self)
        return self.client

    monkeypatch.setattr(HTTPClient, "_ensure_client", _ensure_client)
    return atlassian_api


@pytest.fixture(params=sorted(VERSIONS))
def site(request: pytest.FixtureRequest, jira: AtlassianApiStub, search: IssueSearch) -> AtlassianApiStub:
    stub_site(jira, search)
    numbers = VERSIONS[request.param]
    jira.on("GET", f"{API}/serverInfo", {"version": ".".join(map(str, numbers)), "versionNumbers": numbers})
    return jira


def comment(cid: str, *, group: str | None = None, role: str | None = None, updated: str = ts(1)) -> dict[str, Any]:
    out: dict[str, Any] = {"id": cid, "author": {"key": "bob-key", "name": "bob", "displayName": "Bob"},
                           "body": f"comment {cid}", "created": ts(1, 9), "updated": updated}
    if group:
        out["visibility"] = {"type": "group", "value": group}
    if role:
        out["visibility"] = {"type": "role", "value": role}
    return out


def with_comments(updated: str, comments: list[dict[str, Any]], total: int | None = None) -> dict[str, Any]:
    out = issue("1001", "ENG-1", updated)
    out["fields"]["comment"] = {"comments": comments, "maxResults": len(comments), "startAt": 0,
                                "total": len(comments) if total is None else total}
    return out


def comment_records(db) -> dict[str, CommentRecord]:
    return {k: r for k, r in db.records.items() if r.record_type == RecordType.COMMENT}


def grants(db, ext: str) -> set[tuple[EntityType, str]]:
    return {(p.entity_type, p.external_id) for p in db.record_permissions[ext]}


def held(store) -> dict[str, int]:
    """Issues holding the cursor (the failed-issue budget)."""
    return json.loads(store.values_for("project_ENG").get("failed_issue_attempts") or "{}")


def to_retry(store) -> list[str]:
    """Issues whose restricted comments the next sync writes again (no budget, the cursor moves on)."""
    return json.loads(store.values_for("project_ENG").get("comment_retry") or "[]")


async def test_each_restricted_comment_is_a_record_under_its_issue_granted_to_its_group_or_role(site, db, store, search) -> None:
    search.add("ENG", 0, [with_comments(ts(2), [comment("500"), comment("501", group="devs"),
                                                 comment("502", role="Developers")])])
    connector, _ = await make_connector(db, store)

    await connector.run_sync()

    assert "comment" in search.fields_asked[0], "the issue search reads the comments"
    records = comment_records(db)
    assert set(records) == {"comment_501", "comment_502"}, "a public comment stays in its issue's content only"
    ticket = tickets(db)["1001"]
    for record in records.values():
        assert record.access_rule == AccessRule.PARENT_AND_GRANT and record.inherit_permissions
        assert (record.parent_external_record_id, record.parent_node_id) == ("1001", ticket.id)
        assert record.external_record_group_id == "10000"
    assert grants(db, "comment_501") == {(EntityType.GROUP, "devs")}
    # Developers is role 10002 in the project's role list; a role is held per project.
    assert grants(db, "comment_502") == {(EntityType.ROLE, "ENG_10002")}
    batches = [{type(r).__name__ for r in b} for b in db.record_batches]
    assert batches.index({"CommentRecord"}) > batches.index({"TicketRecord"}), "comments are written after their issue"
    assert held(store) == {} and to_retry(store) == []


async def test_a_comment_made_public_or_deleted_loses_its_record_and_one_restricted_again_gets_it_back(
    site, db, store, search,
) -> None:
    search.add("ENG", 0, [with_comments(ts(2), [comment("501", group="devs"), comment("502", role="Developers")])])
    connector, _ = await make_connector(db, store)
    await connector.run_sync()
    assert set(comment_records(db)) == {"comment_501", "comment_502"}

    search.pages.clear()
    search.add("ENG", 0, [with_comments(ts(3), [comment("501", updated=ts(3))])])
    await connector.run_sync()
    assert comment_records(db) == {}

    search.pages.clear()
    search.add("ENG", 0, [with_comments(ts(4), [comment("501", role="Developers", updated=ts(4))])])
    await connector.run_sync()
    assert set(comment_records(db)) == {"comment_501"}
    assert grants(db, "comment_501") == {(EntityType.ROLE, "ENG_10002")}


async def test_comments_that_cannot_be_read_in_full_take_no_audience_and_the_issue_is_read_again(
    site, db, store, search,
) -> None:
    search.add("ENG", 0, [with_comments(ts(2), [comment("501", group="devs")])])
    connector, _ = await make_connector(db, store)
    await connector.run_sync()
    assert set(comment_records(db)) == {"comment_501"}

    # Moved to the role, and past the embedded page, whose next page Jira refuses.
    site.on("GET", f"{API}/issue/1001/comment", json_response({"errorMessages": ["no"]}, status=403))
    search.pages.clear()
    search.add("ENG", 0, [with_comments(ts(3), [comment("500")], total=2)])
    await connector.run_sync()

    assert comment_records(db) == {}, "a stored comment is not kept with an audience it may no longer have"
    assert to_retry(store) == ["1001"] and held(store) == {}

    # Nothing changes at the source: the issue is read again by id.
    site.on("GET", f"{API}/issue/1001/comment", {"comments": [comment("501", role="Developers", updated=ts(3))],
                                                 "startAt": 1, "maxResults": 100, "total": 2})
    await connector.run_sync()
    assert grants(db, "comment_501") == {(EntityType.ROLE, "ENG_10002")}
    assert to_retry(store) == []


@pytest.mark.parametrize("unread", ["role list", "role"])
async def test_a_role_that_cannot_be_read_grants_nobody_until_a_sync_reads_it(site, db, store, search, unread) -> None:
    """The role's name is read from the role itself (ProjectRoleBean ``name``), which needs both reads."""
    path = f"{API}/project/ENG/role" + ("/10002" if unread == "role" else "")
    good = next(r for m, p, r in site._routes if (m, p) == ("GET", path))  # the stub site's answer
    site.on("GET", path, json_response({"errorMessages": ["no"]}, status=403))
    search.add("ENG", 0, [with_comments(ts(2), [comment("502", role="Developers")])])
    connector, _ = await make_connector(db, store)

    await connector.run_sync()

    assert grants(db, "comment_502") == set(), "an unresolved role grants nobody"
    assert to_retry(store) == ["1001"]

    for _ in range(6):  # more syncs than the failed-issue budget: never given up
        await connector.run_sync()
        assert to_retry(store) == ["1001"] and held(store) == {}
    assert json.loads(store.values_for("project_ENG").get("given_up_issues") or "{}") == {}

    site.on("GET", path, good)
    await connector.run_sync()
    assert grants(db, "comment_502") == {(EntityType.ROLE, "ENG_10002")}, "back with no change at the source"
    assert to_retry(store) == []


async def test_a_restricted_comment_streams_alone_and_its_issue_streams_without_it(site, db, store, search) -> None:
    search.add("ENG", 0, [with_comments(ts(2), [comment("500"), comment("501", group="devs")])])
    connector, _ = await make_connector(db, store)
    await connector.run_sync()
    site.on("GET", f"{API}/issue/1001", {
        "id": "1001", "key": "ENG-1",
        "fields": {"summary": "Crash on save", "description": "secret plan", "attachment": [], "project": {"id": "10000"},
                   "comment": {"comments": [comment("500"), comment("501", group="devs")], "total": 2}},
        "renderedFields": {"description": "<p>secret plan</p>", "comment": {"comments": [
            {"id": "500", "body": "<p>public words</p>"}, {"id": "501", "body": "<p>devs only</p>"}]}},
    })

    async def streamed(record) -> list[dict[str, Any]]:
        response = await connector.stream_record(record)
        return json.loads(b"".join([c async for c in response.body_iterator]))["block_groups"]

    issue_groups = await streamed(tickets(db)["1001"])
    assert [g["source_group_id"] for g in issue_groups if g.get("sub_type") == "comment"] == ["500"]

    comment_groups = await streamed(comment_records(db)["comment_501"])
    assert [g["source_group_id"] for g in comment_groups if g.get("sub_type") == "comment"] == ["501"]
    assert "devs only" in json.dumps(comment_groups)
    assert "secret plan" not in json.dumps(comment_groups), "the issue's description is its own record's content"


async def test_a_reindexed_issue_with_a_restricted_comment_is_left_changed_for_the_next_sync(site, db, store, search) -> None:
    """The comment moved to another group before the reindex: the next sync must not skip the issue as unchanged."""
    search.add("ENG", 0, [with_comments(ts(2), [comment("501", group="devs")])])
    connector, _ = await make_connector(db, store)
    await connector.run_sync()
    stored = tickets(db)["1001"]
    site.on("GET", f"{API}/issue/1001", with_comments(ts(3), [comment("501", group="jira-software-users", updated=ts(3))]))

    await connector.reindex_records([stored])
    assert tickets(db)["1001"].source_updated_at == stored.source_updated_at

    search.pages.clear()
    search.add("ENG", 0, [with_comments(ts(3), [comment("501", group="jira-software-users", updated=ts(3))])])
    await connector.run_sync()
    assert grants(db, "comment_501") == {(EntityType.GROUP, "jira-software-users")}


async def test_a_deleted_issue_takes_its_comment_records_with_it(site, db, store, search) -> None:
    search.add("ENG", 0, [with_comments(ts(2), [comment("501", group="devs")])])
    connector, _ = await make_connector(db, store)
    await connector.run_sync()
    site.on("GET", f"{API}/issue/ENG-1", json_response({"errorMessages": ["gone"]}, status=404))

    await connector._handle_deleted_issue("ENG-1")

    assert "1001" not in db.records and comment_records(db) == {}


async def test_an_issue_with_no_comment_field_keeps_no_comment_record(site, db, store, search) -> None:
    """Jira returns the field when asked; without it no comment is known, so a stored one goes (never kept open)."""
    search.add("ENG", 0, [with_comments(ts(2), [comment("501", group="devs")])])
    connector, _ = await make_connector(db, store)
    await connector.run_sync()

    search.pages.clear()
    search.add("ENG", 0, [issue("1001", "ENG-1", ts(3))])
    await connector.run_sync()
    assert comment_records(db) == {}


@pytest.mark.parametrize("response", [httpx.Response(200, json={"comments": "x"}), httpx.Response(500, json={})])
async def test_a_comment_page_that_is_not_a_list_of_comments_is_an_unread_page(site, db, store, search, response) -> None:
    site.on("GET", f"{API}/issue/1001/comment", response)
    search.add("ENG", 0, [with_comments(ts(2), [comment("500")], total=2)])
    connector, _ = await make_connector(db, store)

    await connector.run_sync()

    assert to_retry(store) == ["1001"]


async def test_on_an_unknown_version_a_visibility_of_another_shape_grants_nobody(jira, db, store, search) -> None:
    """The version cannot be read: comments are read as the supported releases document them, and a visibility the
    documents do not describe grants nobody, with the issue read again."""
    stub_site(jira, search)
    jira.on("GET", f"{API}/serverInfo", json_response({}, status=500))
    odd = {**comment("503"), "visibility": {"type": "securitylevel", "value": "devs"}}
    search.add("ENG", 0, [with_comments(ts(2), [comment("501", group="devs"), odd])])
    connector, _ = await make_connector(db, store)

    await connector.run_sync()

    assert not connector._dc_profile.supported
    assert grants(db, "comment_501") == {(EntityType.GROUP, "devs")}
    assert grants(db, "comment_503") == set()
    assert to_retry(store) == ["1001"]


async def test_a_stale_comment_record_that_cannot_be_deleted_holds_its_issue_until_it_is(site, db, store, search) -> None:
    search.add("ENG", 0, [with_comments(ts(2), [comment("501", group="devs")])])
    connector, _ = await make_connector(db, store)
    await connector.run_sync()
    stored_time = tickets(db)["1001"].source_updated_at
    delete = db.on_records_deleted_cascade
    db.on_records_deleted_cascade = AsyncMock(return_value={"success": False, "reason": "db"})

    search.pages.clear()
    search.add("ENG", 0, [with_comments(ts(3), [comment("501", updated=ts(3))])])  # made public
    await connector.run_sync()

    assert held(store) == {"1001": 1}, "read again by the next sync"
    assert tickets(db)["1001"].source_updated_at == stored_time, "the issue is left as stored, not taken as read"

    db.on_records_deleted_cascade = delete
    await connector.run_sync()
    assert comment_records(db) == {} and held(store) == {}


async def test_a_comment_page_that_ends_short_of_the_total_is_an_unread_list(site, db, store, search) -> None:
    """Jira reports 3 comments, the embedded page holds 1, and the next page answers 200 with only 1 more."""
    search.add("ENG", 0, [with_comments(ts(2), [comment("501", group="devs"), comment("502", group="devs")])])
    connector, _ = await make_connector(db, store)
    await connector.run_sync()
    assert set(comment_records(db)) == {"comment_501", "comment_502"}

    site.on("GET", f"{API}/issue/1001/comment", {"comments": [comment("501", group="devs", updated=ts(3))],
                                                 "startAt": 1, "maxResults": 100, "total": 3})
    search.pages.clear()
    search.add("ENG", 0, [with_comments(ts(3), [comment("500")], total=3)])
    await connector.run_sync()

    assert to_retry(store) == ["1001"], "a short list is not taken for the whole list"


async def test_a_comment_field_of_another_shape_is_an_unread_list(site, db, store, search) -> None:
    bad = issue("1001", "ENG-1", ts(2))
    bad["fields"]["comment"] = "not a comment page"
    search.add("ENG", 0, [bad])
    connector, _ = await make_connector(db, store)

    await connector.run_sync()

    assert to_retry(store) == ["1001"]


async def test_a_comment_batch_that_fails_leaves_its_issue_to_be_read_again(site, db, store, search) -> None:
    """The comment moves to another group; the issue is written, then its comment record's write fails. The next sync,
    nothing changed at the source since, must not skip the issue as unchanged: the record would keep the old group."""
    search.add("ENG", 0, [with_comments(ts(2), [comment("501", group="devs")])])
    connector, _ = await make_connector(db, store)
    await connector.run_sync()
    write = db.on_new_records

    async def fail_comments(items: list) -> None:
        if any(r.record_type == RecordType.COMMENT for r, _ in items):
            raise RuntimeError("database unavailable")
        await write(items)

    moved = with_comments(ts(3), [comment("501", group="jira-software-users", updated=ts(3))])
    search.pages.clear()
    search.add("ENG", 0, [moved])
    db.on_new_records = fail_comments
    await connector.run_sync()  # the project's sync fails, is reported, and its cursor stays
    assert grants(db, "comment_501") == {(EntityType.GROUP, "devs")}

    db.on_new_records = write
    await connector.run_sync()
    assert grants(db, "comment_501") == {(EntityType.GROUP, "jira-software-users")}
    assert tickets(db)["1001"].source_updated_at == connector._parse_jira_timestamp(ts(3)), "settled once written"


async def test_a_restricted_comment_streams_its_inline_image_and_files_nothing(site, db, store, search) -> None:
    search.add("ENG", 0, [with_comments(ts(2), [comment("501", group="devs")])])
    connector, _ = await make_connector(db, store)
    await connector.run_sync()
    image = {"id": "200", "filename": "diagram.png", "mimeType": "image/png", "size": 10, "created": ts(1, 9)}
    site.on("GET", f"{API}/issue/1001", {
        "id": "1001", "key": "ENG-1",
        "fields": {"summary": "Crash", "attachment": [image, {**image, "id": "201", "filename": "log.txt",
                                                              "mimeType": "text/plain"}],
                   "project": {"id": "10000"}, "comment": {"comments": [comment("501", group="devs")], "total": 1}},
        "renderedFields": {"comment": {"comments": [
            {"id": "501", "body": '<p>see <img src="/secure/attachment/200/diagram.png"></p>'}]}},
    })
    site.on("GET", "/secure/attachment/200/diagram.png", httpx.Response(200, content=b"PNGDATA"))
    files_before = {k for k, r in db.records.items() if r.record_type == RecordType.FILE}

    response = await connector.stream_record(comment_records(db)["comment_501"])
    body = json.loads(b"".join([c async for c in response.body_iterator]))

    assert f"data:image/png;base64,{base64.b64encode(b'PNGDATA').decode()}" in json.dumps(body)
    assert {k for k, r in db.records.items() if r.record_type == RecordType.FILE} == files_before, (
        "the issue's stream files the attachments, not its comment's")


async def test_a_reindex_reads_restricted_comments_given_as_a_bare_list(site, db, store, search) -> None:
    search.add("ENG", 0, [issue("1001", "ENG-1", ts(2))])
    connector, _ = await make_connector(db, store)
    await connector.run_sync()
    stored = tickets(db)["1001"]
    listed = issue("1001", "ENG-1", ts(3))
    listed["fields"]["comment"] = [comment("501", group="devs", updated=ts(3))]
    site.on("GET", f"{API}/issue/1001", listed)

    await connector.reindex_records([stored])

    assert tickets(db)["1001"].source_updated_at == stored.source_updated_at, "left for the sync to write the comment"


async def test_an_issue_gone_from_jira_leaves_the_retry_list_with_its_comment_records(site, db, store, search) -> None:
    """Jira rejects a whole ``id in`` clause (400) naming an id it does not know: the clause is split, the issue
    that is gone leaves the list with its comment records, and the others are read."""
    role = next(r for m, p, r in site._routes if (m, p) == ("GET", f"{API}/project/ENG/role"))
    site.on("GET", f"{API}/project/ENG/role", json_response({"errorMessages": ["no"]}, status=403))
    second = with_comments(ts(2), [comment("602", role="Developers"), comment("603", group="devs")])
    second["id"], second["key"] = "1002", "ENG-2"
    search.add("ENG", 0, [with_comments(ts(2), [comment("502", role="Developers")]), second])
    connector, _ = await make_connector(db, store)
    await connector.run_sync()
    assert to_retry(store) == ["1001", "1002"] and grants(db, "comment_603") == {(EntityType.GROUP, "devs")}

    def rejects_gone_ids(request: httpx.Request) -> httpx.Response:
        if "1002" in json.loads(request.content)["jql"]:
            return json_response({"errorMessages": ["A value with ID '1002' does not exist for the field 'id'."],
                                  "errors": {}}, status=400)
        return search(request)

    site.on("POST", f"{API}/search", rejects_gone_ids)
    site.on("GET", f"{API}/project/ENG/role", role)
    search.pages.clear()
    search.add("ENG", 0, [with_comments(ts(2), [comment("502", role="Developers")])])  # 1002 deleted in Jira
    notes_before = len(db.records)
    await connector.run_sync()

    assert grants(db, "comment_502") == {(EntityType.ROLE, "ENG_10002")}, "the issue Jira still has is read"
    assert to_retry(store) == []
    assert "comment_602" not in db.records and "comment_603" not in db.records, "no comment of a gone issue stays granted"
    assert len(db.records) == notes_before - 2


async def test_the_retry_list_is_saved_as_the_project_ends_after_the_last_cursor_move(
    site, db, store, search, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A pass after the walk's last cursor write (a re-read for a security change) puts an issue back on the list:
    the list as the project ends is saved, even when it equals the one the sync started with."""
    path = f"{API}/project/ENG/role"
    role = next(r for m, p, r in site._routes if (m, p) == ("GET", path))
    site.on("GET", path, json_response({"errorMessages": ["no"]}, status=403))
    search.add("ENG", 0, [with_comments(ts(2), [comment("502", role="Developers")])])
    connector, _ = await make_connector(db, store)
    await connector.run_sync()
    assert to_retry(store) == ["1001"]

    site.on("GET", path, role)
    walked = dc.levels_after_walk

    def and_a_later_pass_fails_it_again(*args: object) -> object:
        connector._comment_retry.add("1001")  # after the walk's last cursor write, which saved the list empty
        return walked(*args)

    monkeypatch.setattr(dc, "levels_after_walk", and_a_later_pass_fails_it_again)
    await connector.run_sync()

    assert to_retry(store) == ["1001"]


@pytest.mark.parametrize("page", [
    {"comments": "not a list", "total": 1},
    {"comments": [7], "total": 1},
    {"total": 2},
])
async def test_an_embedded_comment_page_that_is_not_a_list_of_comments_is_unread(site, db, store, search, page) -> None:
    bad = issue("1001", "ENG-1", ts(2))
    bad["fields"]["comment"] = page
    search.add("ENG", 0, [bad])
    connector, _ = await make_connector(db, store)

    await connector.run_sync()

    assert to_retry(store) == ["1001"]


@pytest.mark.parametrize("page", [{"comments": [], "total": 0}, {"total": 0}, []])
async def test_an_empty_comment_list_is_a_complete_read(site, db, store, search, page) -> None:
    empty = issue("1001", "ENG-1", ts(2))
    empty["fields"]["comment"] = page
    search.add("ENG", 0, [empty])
    connector, _ = await make_connector(db, store)

    await connector.run_sync()

    assert to_retry(store) == [] and comment_records(db) == {}


async def test_an_unread_comment_list_is_saved_for_retry_before_its_records_go(site, db, store, search) -> None:
    """A re-read past the cursor (as the security and naming passes after the walk are) gets a comment page that is
    not a list: the issue is on the saved retry list before its comment records are deleted, so a write that then
    fails (the sync stops before the project's last save) does not lose it, and the next sync restores the grant."""
    search.add("ENG", 0, [with_comments(ts(2), [comment("501", group="devs")])])
    connector, _ = await make_connector(db, store)
    await connector.run_sync()
    assert to_retry(store) == [] and grants(db, "comment_501") == {(EntityType.GROUP, "devs")}

    delete = db.on_records_deleted_cascade
    saved_at_delete: list[list[str]] = []

    async def deleting(record_ids: list[str], *args: object, **kwargs: object) -> dict:
        saved_at_delete.append(to_retry(store))
        return await delete(record_ids, *args, **kwargs)

    db.on_records_deleted_cascade = deleting
    bad = with_comments(ts(2), [])
    bad["fields"]["comment"] = {"comments": "not a list", "total": 1}
    built = await connector._build_issue_records([bad], "10000", list(db.app_users), is_new_project=True, stored_only=True)
    assert built and "comment_501" not in db.records, "the comment records went: their audience is unknown"
    assert saved_at_delete == [["1001"]], "saved before the delete"
    db.on_records_deleted_cascade = delete
    # The write of the batch fails here (the next issue's write raises): nothing of this pass is written.

    assert to_retry(store) == ["1001"]
    await connector.run_sync()  # nothing changed at the source since
    assert grants(db, "comment_501") == {(EntityType.GROUP, "devs")}
    assert to_retry(store) == []


def two_issues_with_role_comments(updated: str = ts(2)) -> list[dict[str, Any]]:
    first = with_comments(updated, [comment("502", role="Developers")])
    second = with_comments(updated, [comment("602", role="Developers")])
    second["id"], second["key"] = "1002", "ENG-2"
    return [first, second]


async def retry_list_of_two(site, search, db, store) -> dc.JiraDataCenterConnector:
    """Both issues on the saved retry list: their role could not be read. Returns the connector and the role route."""
    path = f"{API}/project/ENG/role"
    role = next(r for m, p, r in site._routes if (m, p) == ("GET", path))
    site.on("GET", path, json_response({"errorMessages": ["no"]}, status=403))
    search.add("ENG", 0, two_issues_with_role_comments())
    connector, _ = await make_connector(db, store)
    await connector.run_sync()
    assert to_retry(store) == ["1001", "1002"]
    site.on("GET", path, role)
    return connector


async def test_every_save_of_the_retry_list_names_the_issues_not_yet_written(site, db, store, search) -> None:
    """1001's comments cannot be read on the retry pass: its save (before its comment records go) still names 1002,
    which is not written yet, and 1002 leaves the list only once its comment record is written."""
    connector = await retry_list_of_two(site, search, db, store)
    unread = two_issues_with_role_comments()
    unread[0]["fields"]["comment"] = {"comments": "not a list", "total": 1}
    search.pages.clear()
    search.add("ENG", 0, unread)
    saved_at: dict[str, list[str]] = {}
    delete, write = db.on_records_deleted_cascade, db.on_new_records

    async def deleting(record_ids: list[str], *args: object, **kwargs: object) -> dict:
        saved_at.setdefault("delete", to_retry(store))
        return await delete(record_ids, *args, **kwargs)

    async def writing(items: list) -> None:
        if any(r.external_record_id == "comment_602" for r, _ in items):
            saved_at.setdefault("write 1002", to_retry(store))
        await write(items)

    db.on_records_deleted_cascade, db.on_new_records = deleting, writing
    await connector.run_sync()

    assert saved_at["delete"] == ["1001", "1002"], "1002 is not dropped by 1001's save"
    assert saved_at["write 1002"] == ["1001", "1002"], "1002 stays until its comment record is written"
    assert to_retry(store) == ["1001"]
    assert grants(db, "comment_602") == {(EntityType.ROLE, "ENG_10002")}


async def test_a_retry_pass_that_stops_keeps_every_issue_for_the_next_sync(site, db, store, search) -> None:
    connector = await retry_list_of_two(site, search, db, store)
    unread = two_issues_with_role_comments()
    unread[0]["fields"]["comment"] = {"comments": "not a list", "total": 1}
    search.pages.clear()
    search.add("ENG", 0, unread)
    write = db.on_new_records

    async def failing(items: list) -> None:
        raise RuntimeError("database unavailable")

    db.on_new_records = failing
    await connector.run_sync()  # the project's sync stops in the retry pass, reported
    assert to_retry(store) == ["1001", "1002"]

    db.on_new_records = write
    search.pages.clear()
    search.add("ENG", 0, two_issues_with_role_comments())  # nothing changed at the source
    await connector.run_sync()
    assert grants(db, "comment_502") == grants(db, "comment_602") == {(EntityType.ROLE, "ENG_10002")}
    assert to_retry(store) == []


async def test_an_issue_moved_out_of_the_project_leaves_its_retry_list(site, db, store, search) -> None:
    """Jira answers the retry search without it: it is in another project now, whose sync reads it."""
    connector = await retry_list_of_two(site, search, db, store)
    search.pages.clear()
    search.add("ENG", 0, two_issues_with_role_comments()[:1])
    await connector.run_sync()
    assert to_retry(store) == []


async def test_a_search_that_ends_short_of_its_total_drops_no_issue_from_the_retry_list(site, db, store, search) -> None:
    """Jira counts 2 issues but gives one and then an empty page: the other is unread, not gone, so it stays on the
    saved retry list (and the project's sync fails as an unread read, its cursor kept)."""
    connector = await retry_list_of_two(site, search, db, store)
    search.pages.clear()
    search.add("ENG", 0, two_issues_with_role_comments()[:1], total=2)
    search.pages[("ENG", 1)] = {"issues": [], "total": 2}

    await connector.run_sync()

    assert to_retry(store) == ["1001", "1002"]
    assert grants(db, "comment_602") == set(), "1002 was not read"


@pytest.mark.parametrize(("pages", "kept"), [
    # An opening empty page while Jira counts 2: nothing was read, so nothing leaves the list.
    ({0: {"issues": [], "total": 2}}, ["1001", "1002"]),
    # Jira counts what it gave: a complete answer, and the id it did not return (moved away) leaves the list.
    ({0: {"total": 1}}, []),
    # A total of 0 is a complete answer of none: both leave the list.
    ({0: {"issues": [], "total": 0}}, []),
])
async def test_the_retry_list_keeps_what_a_search_did_not_answer_for(site, db, store, search, pages, kept) -> None:
    connector = await retry_list_of_two(site, search, db, store)
    search.pages.clear()
    for start, page in pages.items():
        # Without an issues list: the first issue alone, with the given total.
        search.pages[("ENG", start)] = page if "issues" in page else {"issues": two_issues_with_role_comments()[:1], **page}

    await connector.run_sync()

    assert to_retry(store) == kept


async def test_a_role_that_is_not_stored_yet_keeps_its_comment_on_the_retry_list(site, db, store, search) -> None:
    """First sync: the Developers role names a group whose members cannot be read, so the role is not saved (it keeps
    what is stored, and nothing is). The comment's grant to it would be dropped by the write: the issue stays on the
    retry list until the role is stored."""
    members = next(r for m, p, r in site._routes if (m, p) == ("GET", f"{API}/group/member"))

    def devs_unreadable(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("groupname") == "devs":
            return json_response({"errorMessages": ["boom"]}, status=500)
        return members(request)

    site.on("GET", f"{API}/group/member", devs_unreadable)
    search.add("ENG", 0, [with_comments(ts(2), [comment("502", role="Developers")])])
    connector, _ = await make_connector(db, store)

    await connector.run_sync()

    assert "ENG_10002" not in db.app_roles, "the role was not saved"
    assert to_retry(store) == ["1001"], "a grant to a role that is not stored grants nobody"

    site.on("GET", f"{API}/group/member", members)
    await connector.run_sync()  # nothing changed at the source

    assert "ENG_10002" in db.app_roles
    assert grants(db, "comment_502") == {(EntityType.ROLE, "ENG_10002")}
    assert to_retry(store) == []


async def test_a_group_that_is_not_stored_keeps_its_comment_on_the_retry_list(site, db, store, search) -> None:
    """A group the sync did not read (past the picker, or hidden) is not stored: its grant would be dropped."""
    search.add("ENG", 0, [with_comments(ts(2), [comment("503", group="not-listed")])])
    connector, _ = await make_connector(db, store)

    await connector.run_sync()

    assert to_retry(store) == ["1001"]


async def test_a_full_sync_keeps_the_comment_records_of_an_issue_it_closed_for_failing_to_build(
    site, db, store, search,
) -> None:
    """An issue whose build fails is closed and kept by a full sync, and so are its restricted comments: they read
    through it (``PARENT_AND_GRANT``), so they are hidden with it, and the next sync writes both again."""
    search.add("ENG", 0, [with_comments(ts(2), [comment("501", group="devs")])])
    connector, _ = await make_connector(db, store)
    await connector.run_sync()
    comment_id = comment_records(db)["comment_501"].id
    store.sync_points.clear()  # a full sync's prep
    db.kept_records.clear()
    db.fail_lookup_for = {"1001"}

    graph = await run_full_sync(connector, db)

    assert tickets(db)["1001"].access_rule == AccessRule.RESTRICTED and db.record_permissions["1001"] == []
    seen = graph.unlink_nodes_missed_by_full_sync.await_args.args[1][CollectionNames.RECORDS.value]
    assert "comment_501" in seen or comment_id in db.kept_records, "the comment record keeps its edges"
