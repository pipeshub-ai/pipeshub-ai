"""Jira Data Center comments restricted to a group or a project role, over a fake Jira REST API.

Jira shows such a comment to the readers of its issue who are in the group, or hold the role in the issue's project.
The issue's content is served to everyone who opens the issue, so it holds only public comments; each restricted one is
a record of its own under the issue (PARENT_AND_GRANT), granted to that group or role. Data Center names both by name
(``visibility: {type: group|role, value}``, the same in the 10.2, 10.7, 11.0 and 11.3 specifications), so each test runs
against the oldest, a middle and the newest supported release.
"""

import json
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
from atlassian_behaviour_fakes import (
    AtlassianApiStub,
    FakeCheckpointStore,
    json_response,
)
from test_jira_dc_sync_behaviour import (  # the stub site and fakes the sync tests use
    API,
    BASE,
    IssueSearch,
    JiraRecordsDb,
    issue,
    make_connector,
    stub_site,
    tickets,
    ts,
)

from app.config.constants.arangodb import AccessRule
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
    return json.loads(store.values_for("project_ENG").get("failed_issue_attempts") or "{}")


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
    assert held(store) == {}


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
    stored_time = tickets(db)["1001"].source_updated_at

    # Moved to the role, and past the embedded page, whose next page Jira refuses.
    site.on("GET", f"{API}/issue/1001/comment", json_response({"errorMessages": ["no"]}, status=403))
    search.pages.clear()
    search.add("ENG", 0, [with_comments(ts(3), [comment("500")], total=2)])
    await connector.run_sync()

    assert comment_records(db) == {}, "a stored comment is not kept with an audience it may no longer have"
    assert held(store) == {"1001": 1}, "the cursor waits at the issue"
    assert tickets(db)["1001"].source_updated_at == stored_time, "stored as changed, so the next read rewrites it"

    site.on("GET", f"{API}/issue/1001/comment", {"comments": [comment("501", role="Developers", updated=ts(3))],
                                                 "startAt": 1, "maxResults": 100, "total": 2})
    await connector.run_sync()
    assert grants(db, "comment_501") == {(EntityType.ROLE, "ENG_10002")}
    assert held(store) == {}


async def test_a_role_whose_list_cannot_be_read_grants_nobody_until_a_sync_reads_it(site, db, store, search) -> None:
    site.on("GET", f"{API}/project/ENG/role", json_response({"errorMessages": ["no"]}, status=403))
    search.add("ENG", 0, [with_comments(ts(2), [comment("502", role="Developers")])])
    connector, _ = await make_connector(db, store)

    await connector.run_sync()

    assert grants(db, "comment_502") == set(), "an unresolved role grants nobody"
    assert held(store) == {"1001": 1}

    site.on("GET", f"{API}/project/ENG/role", {"Developers": f"{BASE}{API}/project/ENG/role/10002"})
    await connector.run_sync()
    assert grants(db, "comment_502") == {(EntityType.ROLE, "ENG_10002")}
    assert held(store) == {}


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

    assert held(store) == {"1001": 1}


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
    assert held(store) == {"1001": 1}


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
