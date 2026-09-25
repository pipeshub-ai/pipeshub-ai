"""Jira Data Center sync, driven end to end over a fake Jira REST API.

The connector, its Jira client and the generated request builder are all real;
the Jira server is an in-memory stub and our databases are in-memory fakes.
"""

import base64
import json
import logging
import time
from typing import Any, Optional

import httpx
import pytest
from atlassian_behaviour_fakes import (
    AtlassianApiStub,
    FakeCheckpointStore,
    FakeConfigService,
    FakeRecordsDb,
    json_response,
)
from fastapi import HTTPException

from app.connectors.sources.atlassian.jira_data_center.connector import (
    DC_EPIC_LINK_SCHEMA_CUSTOM,
    JiraDataCenterConnector,
    _parse_jira_dc_user_list_page,
)
from app.models.entities import AppUser, FileRecord, RecordType, TicketRecord
from app.models.permission import EntityType, PermissionType
from app.sources.client.http.http_client import HTTPClient
from app.sources.client.jira.jira import JiraRESTClientViaToken
from app.sources.external.jira.jira import JiraDataSource

CONNECTOR_ID = "jira-dc-1"
BASE = "https://jira.example.com"
API = "/rest/api/2"
FAKE_PAT = "fake-jira-pat-for-tests"
EPIC_FIELD = "customfield_10100"


class JiraRecordsDb(FakeRecordsDb):
    """Adds the users/roles side of ``DataSourceEntitiesProcessor`` that Jira uses."""

    def __init__(self) -> None:
        super().__init__()
        self.platform_users = [
            type("U", (), {"email": e})()
            for e in ("alice@example.com", "bob@example.com", "carol@example.com", "dave@example.com", "eve@example.com")
        ]
        self.cached_app_users: list[AppUser] = []
        self.app_roles: dict[str, list[Any]] = {}
        self.groups_saved: dict[str, list[Any]] = {}

    async def get_all_active_users(self) -> list[Any]:
        return self.platform_users

    async def get_all_app_users(self, connector_id: str) -> list[AppUser]:
        return self.cached_app_users

    async def on_new_app_roles(self, roles: list[tuple[Any, list[Any]]]) -> None:
        for role, members in roles:
            self.app_roles[role.source_role_id] = list(members)

    async def on_new_user_groups(self, groups: list[tuple[Any, list[Any]]]) -> None:
        await super().on_new_user_groups(groups)
        for group, members in groups:
            self.groups_saved[group.source_user_group_id] = list(members)

    async def get_placeholder_records(self, connector_id: str) -> list[Any]:
        return [r for r in self.records.values() if getattr(r, "is_placeholder", False)]


class JiraStore(FakeCheckpointStore):
    """Checkpoint store plus the record lookups the deletion pass runs in a transaction."""

    def __init__(self, db: JiraRecordsDb) -> None:
        super().__init__()
        self.db = db
        self.hard_deleted: list[str] = []

    async def get_record_by_issue_key(self, connector_id: str, issue_key: str) -> Optional[TicketRecord]:
        for record in self.db.records.values():
            if record.record_type == RecordType.TICKET and (record.weburl or "").endswith(f"/browse/{issue_key}"):
                return record
        return None

    async def get_records_by_parent(self, connector_id: str, parent_external_record_id: str, record_type: str) -> list[Any]:
        return [
            r for r in self.db.records.values()
            if r.parent_external_record_id == parent_external_record_id and r.record_type.value == record_type
        ]

    async def delete_records_and_relations(self, record_key: str, hard_delete: bool = False) -> None:
        self.hard_deleted.append(record_key)
        for ext_id, record in list(self.db.records.items()):
            if record.id == record_key:
                del self.db.records[ext_id]


def ts(day: int, hour: int = 10) -> str:
    return f"2024-05-{day:02d}T{hour:02d}:00:00.000+0000"


def user(key: str, name: str, email: Optional[str]) -> dict[str, Any]:
    row: dict[str, Any] = {"key": key, "name": name, "displayName": name.title(), "active": True}
    if email:
        row["emailAddress"] = email
    return row


def issue(
    iid: str,
    key: str,
    updated: str = ts(1),
    parent: Optional[str] = None,
    attachments: Optional[list[dict[str, Any]]] = None,
    epic: Optional[object] = None,
) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "summary": f"Summary of {key}",
        "status": {"name": "Open"},
        "priority": {"name": "High"},
        "issuetype": {"name": "Sub-task" if parent else "Task", "subtask": bool(parent)},
        "project": {"id": "10000", "key": key.split("-")[0]},
        "created": ts(1, 8),
        "updated": updated,
        "creator": {"key": "alice-key", "name": "alice", "displayName": "Alice"},
        "reporter": {"key": "carol-key", "name": "carol", "displayName": "Carol"},
        "assignee": None,
        "attachment": attachments or [],
    }
    if parent:
        fields["parent"] = {"id": parent, "key": "ENG-1"}
    if epic is not None:
        fields[EPIC_FIELD] = epic
    return {"id": iid, "key": key, "fields": fields}


def attachment(aid: str, filename: str = "diagram.png", mime: str = "image/png") -> dict[str, Any]:
    return {"id": aid, "filename": filename, "mimeType": mime, "size": 10, "created": ts(1, 9)}


class IssueSearch:
    """Answers ``POST /search`` per (project, startAt) and remembers each JQL."""

    def __init__(self) -> None:
        self.pages: dict[tuple[str, int], Any] = {}
        self.jql: list[str] = []

    def add(self, project: str, start: int, issues: object, total: Optional[int] = None) -> None:
        if isinstance(issues, (httpx.Response, list)) and not (isinstance(issues, list) and issues and isinstance(issues[0], dict)):
            self.pages[(project, start)] = issues
        else:
            self.pages[(project, start)] = {"issues": issues, "total": total if total is not None else len(issues)}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        jql = body["jql"]
        self.jql.append(jql)
        project = jql.split('project = "')[1].split('"')[0] if 'project = "' in jql else "*"
        page = self.pages.get((project, int(body.get("startAt", 0))), {"issues": [], "total": 0})
        if isinstance(page, list):
            page = page.pop(0) if len(page) > 1 else page[0]
        return page if isinstance(page, httpx.Response) else json_response(page)


def stub_site(api: AtlassianApiStub, search: IssueSearch) -> None:
    """A small but complete Jira DC site: users, groups, roles, one project, its ACL."""
    api.on("GET", f"{API}/field", json_response([{"id": EPIC_FIELD, "name": "Epic Link", "schema": {"custom": DC_EPIC_LINK_SCHEMA_CUSTOM}}]))

    def user_list(request: httpx.Request) -> httpx.Response:
        if AtlassianApiStub.query(request).get("cursor") == "abc":
            return json_response({"values": [user("carol-key", "carol", "carol@example.com"), user("dave-key", "dave", None)], "isLast": True})
        return json_response({
            "values": [user("alice-key", "alice", "alice@example.com"), user("bob-key", "bob", "bob@example.com")],
            "nextPage": f"{BASE}{API}/user/list?cursor=abc&maxResults=100",
            "isLast": False,
        })

    api.on("GET", f"{API}/user/list", user_list)

    def user_search(request: httpx.Request) -> httpx.Response:
        who = AtlassianApiStub.query(request).get("username")
        if who == "dave@example.com":
            return json_response([user("dave-key", "dave", None)])
        if who == "eve@example.com":
            return json_response([user("evelyn-key", "evelyn", "evelyn@example.com")])
        return json_response([])

    api.on("GET", f"{API}/user/search", user_search)
    api.on("GET", f"{API}/groups/picker", {"groups": [{"name": "devs"}, {"name": "jira-software-users"}], "total": 2})

    def members(request: httpx.Request) -> httpx.Response:
        q = AtlassianApiStub.query(request)
        if q["groupname"] == "jira-software-users":
            return json_response({"values": [{"key": "bob-key"}], "isLast": True})
        if q.get("startAt", "0") == "0":
            ghosts = [{"key": f"ghost-{i}"} for i in range(49)]
            return json_response({"values": [*ghosts, {"key": "alice-key"}], "isLast": False})
        return json_response({"values": [{"key": "carol-key"}], "isLast": True})

    api.on("GET", f"{API}/group/member", members)
    api.on("GET", f"{API}/applicationrole", json_response([{"key": "jira-software", "groups": ["jira-software-users"]}]))
    api.on("GET", f"{API}/project", json_response([{"id": "10000", "key": "ENG", "name": "Engineering", "lead": {"key": "alice-key", "displayName": "Alice"}}]))
    api.on("GET", f"{API}/project/ENG/permissionscheme", {"id": 7})
    browse = "BROWSE_PROJECTS"
    api.on("GET", f"{API}/permissionscheme/7/permission", {"permissions": [
        {"permission": browse, "holder": {"type": "group", "parameter": "devs"}},
        {"permission": browse, "holder": {"type": "group", "parameter": "devs"}},
        {"permission": browse, "holder": {"type": "applicationRole", "parameter": "jira-software"}},
        {"permission": browse, "holder": {"type": "projectRole", "parameter": "10002"}},
        {"permission": browse, "holder": {"type": "user", "parameter": "bob-key"}},
        {"permission": browse, "holder": {"type": "projectLead"}},
        {"permission": browse, "holder": {"type": "sd.customer.portal.only"}},
        {"permission": "EDIT_ISSUES", "holder": {"type": "group", "parameter": "admins"}},
    ]})
    api.on("GET", f"{API}/project/ENG/role", {
        "Developers": f"{BASE}{API}/project/ENG/role/10002",
        "atlassian-addons-project-access": f"{BASE}{API}/project/ENG/role/10003",
    })
    api.on("GET", f"{API}/project/ENG/role/10002", {"name": "Developers", "actors": [
        {"type": "atlassian-user-role-actor", "name": "carol"},
        {"type": "atlassian-group-role-actor", "name": "devs"},
    ]})
    api.on("GET", f"{API}/issue/ENG-1", {"id": "1001", "key": "ENG-1"})
    api.on("POST", f"{API}/search", search)


class Notifications:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    async def __call__(self, **kwargs: object) -> None:
        self.sent.append(kwargs)

    def titles(self) -> list[str]:
        return [n["title"] for n in self.sent]


@pytest.fixture
def db() -> JiraRecordsDb:
    return JiraRecordsDb()


@pytest.fixture
def store(db: JiraRecordsDb) -> JiraStore:
    return JiraStore(db)


@pytest.fixture
def search() -> IssueSearch:
    return IssueSearch()


@pytest.fixture
def jira(atlassian_api: AtlassianApiStub, monkeypatch: pytest.MonkeyPatch) -> AtlassianApiStub:
    """Every real ``HTTPClient`` talks to the stub (init already makes a request)."""

    async def _ensure_client(self: HTTPClient) -> httpx.AsyncClient:
        if self.client is None:
            atlassian_api.install(self)
        return self.client

    monkeypatch.setattr(HTTPClient, "_ensure_client", _ensure_client)
    return atlassian_api


async def make_connector(db: JiraRecordsDb, store: JiraStore, filters: Optional[dict[str, Any]] = None) -> tuple[JiraDataCenterConnector, Notifications]:
    config = {"auth": {"authType": "API_TOKEN", "baseUrl": f"{BASE}/", "apiToken": FAKE_PAT}, "filters": filters or {}}
    connector = JiraDataCenterConnector(
        logging.getLogger("test.jira_dc"), db, store, FakeConfigService(CONNECTOR_ID, config),
        CONNECTOR_ID, "team", "creator-user-1",
    )
    notes = Notifications()
    connector.notify = notes  # the notification broker is outside the connector
    assert await connector.init() is True
    return connector, notes


def tickets(db: FakeRecordsDb) -> dict[str, TicketRecord]:
    return {k: r for k, r in db.records.items() if r.record_type == RecordType.TICKET}


def first_page_eng(search: IssueSearch) -> None:
    search.add("ENG", 0, [issue("1001", "ENG-1", ts(1), attachments=[attachment("200")]), issue("1002", "ENG-2", ts(2), parent="1001")], total=3)
    search.add("ENG", 2, [issue("1003", "ENG-3", ts(3), epic="ENG-1")], total=3)


class TestRealClientStack:
    async def test_connector_uses_real_jira_client_and_request_builder(self, jira, db, store, search) -> None:
        stub_site(jira, search)
        connector, _ = await make_connector(db, store)

        assert type(connector.data_source) is JiraDataSource
        assert type(connector.external_client.get_client()) is JiraRESTClientViaToken
        assert isinstance(connector.external_client.get_client().client, httpx.AsyncClient)
        field_call = jira.calls("GET", f"{API}/field")[0]
        assert field_call.headers["Authorization"] == f"Bearer {FAKE_PAT}"
        assert str(field_call.url) == f"{BASE}{API}/field"
        assert EPIC_FIELD in connector._get_issue_search_fields()

    async def test_init_without_auth_type_fails_loudly(self, jira, db, store) -> None:
        connector = JiraDataCenterConnector(
            logging.getLogger("t"), db, store,
            FakeConfigService(CONNECTOR_ID, {"auth": {"baseUrl": BASE, "apiToken": FAKE_PAT}}),
            CONNECTOR_ID, "team", "creator-user-1",
        )
        with pytest.raises(Exception, match="authType is required"):
            await connector.init()


class TestFullSync:
    async def test_users_groups_roles_acl_and_issues_are_synced_to_the_end(self, jira, db, store, search) -> None:
        stub_site(jira, search)
        first_page_eng(search)
        connector, _ = await make_connector(db, store)

        await connector.run_sync()

        emails = {u.email for u in db.app_users}
        assert emails == {"alice@example.com", "bob@example.com", "carol@example.com", "dave@example.com"}, (
            "second /user/list page is read, and a hidden-email user is found by reverse lookup; "
            "a fuzzy match with a different visible email (evelyn for eve) is rejected"
        )
        devs = {m.email for m in db.groups_saved["devs"]}
        assert devs == {"alice@example.com", "carol@example.com"}, "second page of group members is read"
        assert jira.calls("GET", f"{API}/group/member")[1].url.params["startAt"] == "50"

        (acl,) = db.record_group_permissions.values()
        grants = {(p.entity_type, p.external_id or p.email) for p in acl}
        assert grants == {
            (EntityType.GROUP, "devs"),
            (EntityType.GROUP, "jira-software-users"),
            (EntityType.ROLE, "ENG_10002"),
            (EntityType.USER, "bob@example.com"),
            (EntityType.ROLE, "ENG_projectLead"),
        }
        assert len(acl) == 5, "duplicate grants collapse; non-browse grants (EDIT_ISSUES to admins) are ignored"
        assert all(p.type == PermissionType.READ for p in acl)

        assert {m.email for m in db.app_roles["ENG_10002"]} == {"alice@example.com", "carol@example.com"}
        assert "ENG_10003" not in db.app_roles, "the add-on access role is not synced"
        assert [m.email for m in db.app_roles["ENG_projectLead"]] == ["alice@example.com"]

        synced = tickets(db)
        assert set(synced) == {"1001", "1002", "1003"}
        assert synced["1002"].parent_external_record_id == "1001"
        assert synced["1003"].parent_external_record_id == "1001", "Epic Link key is resolved to the epic's id"
        assert synced["1001"].weburl == f"{BASE}/browse/ENG-1"
        assert synced["1001"].creator_email == "alice@example.com"
        assert synced["1001"].reporter_email == "carol@example.com", "user refs without accountId resolve by key"
        file = db.records["attachment_200"]
        assert isinstance(file, FileRecord)
        assert file.parent_external_record_id == "1001"
        assert file.parent_node_id == synced["1001"].id

        starts = [json.loads(r.content)["startAt"] for r in jira.calls("POST", f"{API}/search")]
        assert starts == [0, 2]
        assert "updated >=" not in search.jql[0]
        assert search.jql[0].endswith("ORDER BY updated ASC, id ASC")

        project_cp = store.values_for("project_ENG")
        assert project_cp["last_issue_updated"] == connector._parse_jira_timestamp(ts(3))
        assert store.values_for("issues_global")["last_sync_time"] > 0

    async def test_nothing_is_synced_when_pipeshub_has_no_users(self, jira, db, store, search) -> None:
        stub_site(jira, search)
        db.platform_users = []
        connector, _ = await make_connector(db, store)

        await connector.run_sync()

        assert jira.calls("GET", f"{API}/user/list") == []
        assert store.sync_points == {}


class TestIncrementalSync:
    async def test_second_run_asks_for_recent_changes_and_updates_in_place(self, jira, db, store, search) -> None:
        stub_site(jira, search)
        first_page_eng(search)
        connector, _ = await make_connector(db, store)
        await connector.run_sync()
        before = {k: (r.id, r.version) for k, r in tickets(db).items()}
        batches_before = len(db.record_batches)

        search.pages.clear()
        search.add("ENG", 0, [issue("1002", "ENG-2", ts(2), parent="1001"), issue("1003", "ENG-3", ts(4), epic="ENG-1")])
        search.jql.clear()
        await connector.run_sync()

        assert "updated >= -" in search.jql[0]
        after = tickets(db)
        assert set(after) == {"1001", "1002", "1003"}, "no duplicates"
        assert (after["1003"].id, after["1003"].version) == (before["1003"][0], before["1003"][1] + 1)
        assert (after["1002"].id, after["1002"].version) == before["1002"]
        new_batches = db.record_batches[batches_before:]
        saved_again = {r.external_record_id for batch in new_batches for r in batch}
        assert "1002" not in saved_again, "an unchanged issue is not written again"
        assert store.values_for("project_ENG")["last_issue_updated"] == connector._parse_jira_timestamp(ts(4))

    async def test_a_failing_later_page_keeps_the_checkpoint_at_the_last_saved_page(self, jira, db, store, search) -> None:
        stub_site(jira, search)
        search.add("ENG", 0, [issue("1001", "ENG-1", ts(1)), issue("1002", "ENG-2", ts(2))], total=3)
        search.add("ENG", 2, json_response({"errorMessages": ["boom"]}, status=500))
        connector, notes = await make_connector(db, store)

        await connector.run_sync()

        assert set(tickets(db)) == {"1001", "1002"}
        assert store.values_for("project_ENG")["last_issue_updated"] == connector._parse_jira_timestamp(ts(2))
        assert any("couldn't sync some projects" in t for t in notes.titles())

        search.pages.clear()
        search.jql.clear()
        search.add("ENG", 0, [issue("1003", "ENG-3", ts(3))])
        await connector.run_sync()
        assert "updated >= -" in search.jql[0], "the next run resumes from the saved page, not from scratch"
        assert "1003" in tickets(db)

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "Bug, left alone because an open PR edits this connector: one issue that fails to "
            "process aborts its whole page and project, so the healthy issues next to it are not "
            "saved, and every later run fails on the same issue."
        ),
    )
    async def test_one_bad_issue_does_not_stop_the_rest_of_the_project(self, jira, db, store, search) -> None:
        stub_site(jira, search)
        search.add("ENG", 0, [issue("1001", "ENG-1", ts(1)), issue("1002", "ENG-2", ts(2)), issue("1004", "ENG-4", ts(3))])
        db.fail_lookup_for = {"1002"}
        connector, _ = await make_connector(db, store)

        await connector.run_sync()

        assert {"1001", "1004"} <= set(tickets(db))

    @pytest.mark.xfail(
        strict=True,
        reason=(
            "Bug, left alone because an open PR edits this connector: issue search is not retried "
            "when Jira answers 429 (rate limited), so the project is skipped for this run instead "
            "of waiting and continuing."
        ),
    )
    async def test_a_rate_limited_issue_search_is_retried(self, jira, db, store, search) -> None:
        stub_site(jira, search)
        limited = httpx.Response(429, headers={"Retry-After": "1"}, content=b"{}")
        search.add("ENG", 0, [limited, {"issues": [issue("1001", "ENG-1")], "total": 1}])
        connector, _ = await make_connector(db, store)

        await connector.run_sync()

        assert "1001" in tickets(db)


def acl_summary(permissions: list[Any]) -> list[tuple[str, str, Optional[str], Optional[str]]]:
    return sorted((str(p.entity_type), str(p.type), p.external_id, p.email) for p in permissions)


class TestAccessControlSafety:
    async def test_a_failed_member_lookup_does_not_empty_the_group(self, jira, db, store, search) -> None:
        stub_site(jira, search)
        connector, _ = await make_connector(db, store)
        await connector.run_sync()
        before = sorted(m.email for m in db.groups_saved["devs"])
        assert before == ["alice@example.com", "carol@example.com"]

        jira.on("GET", f"{API}/group/member", json_response({"errorMessages": ["busy"]}, status=503))
        await connector.run_sync()

        assert sorted(m.email for m in db.groups_saved["devs"]) == before

    async def test_a_failed_member_lookup_keeps_the_roles_that_include_the_group(self, jira, db, store, search) -> None:
        stub_site(jira, search)
        connector, notes = await make_connector(db, store)
        await connector.run_sync()
        before = sorted(m.email for m in db.app_roles["ENG_10002"])
        assert "alice@example.com" in before, "alice is in the role only through the devs group"

        jira.on("GET", f"{API}/group/member", json_response({"errorMessages": ["busy"]}, status=503))
        await connector.run_sync()

        assert sorted(m.email for m in db.app_roles["ENG_10002"]) == before
        assert any("couldn't sync project roles" in t for t in notes.titles())

    async def test_a_failed_permission_scheme_read_does_not_wipe_the_project_acl(self, jira, db, store, search) -> None:
        stub_site(jira, search)
        connector, _ = await make_connector(db, store)
        await connector.run_sync()
        before = acl_summary(db.record_group_permissions["10000"])
        assert before, "the first sync grants access"

        jira.on("GET", f"{API}/project/ENG/permissionscheme", json_response({"errorMessages": ["oops"]}, status=500))
        await connector.run_sync()

        assert acl_summary(db.record_group_permissions["10000"]) == before

    async def test_an_unreadable_permission_scheme_still_syncs_the_projects_issues(self, jira, db, store, search) -> None:
        stub_site(jira, search)
        jira.on("GET", f"{API}/project/ENG/permissionscheme", json_response({"errorMessages": ["oops"]}, status=500))
        search.add("ENG", 0, [issue("1001", "ENG-1")])
        connector, _ = await make_connector(db, store)

        await connector.run_sync()

        assert "10000" not in db.record_group_permissions, "no empty access list is written"
        assert "1001" in db.records

    async def test_forbidden_permission_scheme_falls_back_to_the_configuring_user(self, jira, db, store, search) -> None:
        stub_site(jira, search)
        jira.on("GET", f"{API}/project/ENG/permissionscheme", json_response({"errorMessages": ["no"]}, status=403))
        connector, _ = await make_connector(db, store)

        await connector.run_sync()

        (acl,) = db.record_group_permissions.values()
        assert [(p.entity_type, p.email) for p in acl] == [(EntityType.USER, "owner@example.com")]

    async def test_a_forbidden_scheme_with_no_owner_email_keeps_the_project_acl(self, jira, db, store, search) -> None:
        stub_site(jira, search)
        connector, _ = await make_connector(db, store)
        await connector.run_sync()
        before = acl_summary(db.record_group_permissions["10000"])

        connector.creator_email = None
        jira.on("GET", f"{API}/project/ENG/permissionscheme", json_response({"errorMessages": ["no"]}, status=403))
        await connector.run_sync()

        assert acl_summary(db.record_group_permissions["10000"]) == before, "a 403 doesn't mean no one can see the project"

    async def test_application_roles_forbidden_grants_only_the_configuring_user(self, jira, db, store, search) -> None:
        stub_site(jira, search)
        jira.on("GET", f"{API}/applicationrole", json_response({}, status=403))
        connector, notes = await make_connector(db, store)

        await connector.run_sync()

        (acl,) = db.record_group_permissions.values()
        users = {p.email for p in acl if p.entity_type == EntityType.USER}
        assert users == {"bob@example.com", "owner@example.com"}
        assert not any(p.external_id == "jira-software-users" for p in acl)
        assert not any(p.entity_type == EntityType.ORG for p in acl), "never widen to the whole org"
        assert any("admin permission" in t for t in notes.titles())

    async def test_an_unreadable_group_list_skips_role_sync(self, jira, db, store, search) -> None:
        stub_site(jira, search)
        connector, _ = await make_connector(db, store)
        await connector.run_sync()
        before = sorted(m.email for m in db.app_roles["ENG_10002"])
        assert "alice@example.com" in before, "alice is in the role only through the devs group"
        role_reads = len(jira.calls("GET", f"{API}/project/ENG/role"))

        jira.on("GET", f"{API}/groups/picker", json_response({"errorMessages": ["busy"]}, status=503))
        await connector.run_sync()

        assert sorted(m.email for m in db.app_roles["ENG_10002"]) == before
        assert len(jira.calls("GET", f"{API}/project/ENG/role")) == role_reads, "roles are not synced this run"

    @staticmethod
    def _stub_more_roles(jira, reviewer: str) -> None:
        """Adds a role made only of the jira-software-users group and a role made only of one user."""
        jira.on("GET", f"{API}/project/ENG/role", {
            "Developers": f"{BASE}{API}/project/ENG/role/10002",
            "Testers": f"{BASE}{API}/project/ENG/role/10004",
            "Reviewers": f"{BASE}{API}/project/ENG/role/10005",
        })
        jira.on("GET", f"{API}/project/ENG/role/10004", {"name": "Testers", "actors": [
            {"type": "atlassian-group-role-actor", "name": "jira-software-users"},
        ]})
        jira.on("GET", f"{API}/project/ENG/role/10005", {"name": "Reviewers", "actors": [
            {"type": "atlassian-user-role-actor", "name": reviewer},
        ]})

    async def test_a_group_list_cut_off_at_the_picker_limit_holds_back_only_the_roles_that_need_a_missing_group(
        self, jira, db, store, search
    ) -> None:
        stub_site(jira, search)
        self._stub_more_roles(jira, reviewer="alice")
        connector, notes = await make_connector(db, store)
        await connector.run_sync()
        developers_before = sorted(m.email for m in db.app_roles["ENG_10002"])
        assert "alice@example.com" in developers_before, "alice is in the role only through the devs group"
        devs_before = sorted(m.email for m in db.groups_saved["devs"])
        assert [m.email for m in db.app_roles["ENG_10004"]] == ["bob@example.com"]
        assert [m.email for m in db.app_roles["ENG_10005"]] == ["alice@example.com"]

        jira.on("GET", f"{API}/groups/picker", {"groups": [{"name": "jira-software-users"}], "total": 2})

        def members(request: httpx.Request) -> httpx.Response:
            assert AtlassianApiStub.query(request)["groupname"] == "jira-software-users"
            return json_response({"values": [{"key": "bob-key"}, {"key": "carol-key"}], "isLast": True})

        jira.on("GET", f"{API}/group/member", members)
        self._stub_more_roles(jira, reviewer="bob")
        await connector.run_sync()

        assert sorted(m.email for m in db.groups_saved["jira-software-users"]) == ["bob@example.com", "carol@example.com"], (
            "the group the picker returned is saved with its new members"
        )
        assert sorted(m.email for m in db.groups_saved["devs"]) == devs_before, "the group past the limit is left as stored"
        assert sorted(m.email for m in db.app_roles["ENG_10004"]) == ["bob@example.com", "carol@example.com"], (
            "a role made only of a returned group is updated"
        )
        assert [m.email for m in db.app_roles["ENG_10005"]] == ["bob@example.com"], "a role of users only is updated"
        assert sorted(m.email for m in db.app_roles["ENG_10002"]) == developers_before, (
            "a role that includes the group past the limit keeps its stored members"
        )
        assert not any("couldn't sync project roles" in t for t in notes.titles()), (
            "a group past the limit is not reported as a role-sync failure"
        )

    async def test_a_cut_off_group_list_that_normalises_to_no_groups_keeps_the_roles_that_need_a_group(
        self, jira, db, store, search
    ) -> None:
        stub_site(jira, search)
        connector, notes = await make_connector(db, store)
        await connector.run_sync()
        before = sorted(m.email for m in db.app_roles["ENG_10002"])
        assert "alice@example.com" in before

        jira.on("GET", f"{API}/groups/picker", {"groups": [{"html": "a row without a name"}], "total": 2})
        await connector.run_sync()

        assert sorted(m.email for m in db.app_roles["ENG_10002"]) == before
        assert not any("couldn't sync project roles" in t for t in notes.titles())

    async def test_a_group_list_of_unexpected_shape_keeps_the_roles(self, jira, db, store, search) -> None:
        stub_site(jira, search)
        connector, _ = await make_connector(db, store)
        await connector.run_sync()
        before = sorted(m.email for m in db.app_roles["ENG_10002"])

        jira.on("GET", f"{API}/groups/picker", json_response(["devs", "jira-software-users"]))
        await connector.run_sync()

        assert sorted(m.email for m in db.app_roles["ENG_10002"]) == before

    async def test_a_group_that_fails_to_process_keeps_the_roles_that_include_it(self, jira, db, store, search) -> None:
        stub_site(jira, search)
        connector, _ = await make_connector(db, store)
        await connector.run_sync()
        before = sorted(m.email for m in db.app_roles["ENG_10002"])
        devs_before = sorted(m.email for m in db.groups_saved["devs"])

        def members(request: httpx.Request) -> httpx.Response:
            if AtlassianApiStub.query(request)["groupname"] == "devs":
                return json_response({"values": [{"key": ["not", "a", "key"]}], "isLast": True})
            return json_response({"values": [{"key": "bob-key"}], "isLast": True})

        jira.on("GET", f"{API}/group/member", members)
        await connector.run_sync()

        assert sorted(m.email for m in db.app_roles["ENG_10002"]) == before
        assert sorted(m.email for m in db.groups_saved["devs"]) == devs_before, "the group is not saved again"

    async def test_a_group_missing_from_the_list_does_not_hold_back_the_role(self, jira, db, store, search) -> None:
        stub_site(jira, search)
        jira.on("GET", f"{API}/project/ENG/role/10002", {"name": "Developers", "actors": [
            {"type": "atlassian-group-role-actor", "name": "devs"},
            {"type": "atlassian-group-role-actor", "name": "retired-team"},
        ]})
        connector, _ = await make_connector(db, store)

        await connector.run_sync()

        assert "alice@example.com" in {m.email for m in db.app_roles["ENG_10002"]}

    async def test_a_failed_role_keeps_the_others_and_warns(self, jira, db, store, search) -> None:
        stub_site(jira, search)
        jira.on("GET", f"{API}/project/ENG/role", {
            "Developers": f"{BASE}{API}/project/ENG/role/10002",
            "Testers": f"{BASE}{API}/project/ENG/role/10004",
        })
        jira.on("GET", f"{API}/project/ENG/role/10004", json_response({}, status=500))
        connector, notes = await make_connector(db, store)

        await connector.run_sync()

        assert "ENG_10002" in db.app_roles
        assert "ENG_10004" not in db.app_roles, "a role that could not be read keeps its previous members"
        assert any("couldn't sync project roles" in t for t in notes.titles())


class TestUserDirectoryFallbacks:
    async def test_older_jira_without_user_list_pages_through_user_search(self, jira, db, store, search) -> None:
        stub_site(jira, search)
        jira.on("GET", f"{API}/user/list", json_response({}, status=404))
        page1 = [user(f"u{i}-key", f"u{i}", f"u{i}@corp.example") for i in range(49)] + [user("alice-key", "alice", "alice@example.com")]

        def directory(request: httpx.Request) -> httpx.Response:
            q = AtlassianApiStub.query(request)
            if q.get("username") != ".":
                return json_response([])
            return json_response(page1 if q.get("startAt") == "0" else [user("bob-key", "bob", "bob@example.com")])

        jira.on("GET", f"{API}/user/search", directory)
        connector, _ = await make_connector(db, store)

        users = await connector._fetch_users()

        assert {"alice@example.com", "bob@example.com"} <= {u.email for u in users}

    async def test_forbidden_user_list_warns_and_still_resolves_by_email(self, jira, db, store, search) -> None:
        stub_site(jira, search)
        jira.on("GET", f"{API}/user/list", json_response({}, status=403))
        connector, notes = await make_connector(db, store)

        users = await connector._fetch_users()

        assert {u.email for u in users} == {"dave@example.com"}
        assert any("couldn't list users" in t for t in notes.titles())

    @pytest.mark.parametrize(
        ("payload", "expected_keys", "expected_cursor"),
        [
            ([{"key": "a"}, "junk"], ["a"], None),
            ({"users": [{"key": "a"}], "nextCursor": "c1"}, ["a"], "c1"),
            ({"values": [{"key": "a"}], "nextPage": f"{BASE}{API}/user/list?cursor=c2"}, ["a"], "c2"),
            ({"values": [], "nextPage": "c3"}, [], "c3"),
            ({"values": [{"key": "a"}], "nextCursor": "c4", "isLast": True}, ["a"], None),
            ("not json", [], None),
        ],
    )
    def test_user_list_page_shapes(self, payload, expected_keys, expected_cursor) -> None:
        users, cursor = _parse_jira_dc_user_list_page(payload)
        assert [u["key"] for u in users] == expected_keys
        assert cursor == expected_cursor


class TestDeletions:
    async def _synced(self, jira, db, store, search) -> tuple[JiraDataCenterConnector, Notifications]:
        stub_site(jira, search)
        search.add("ENG", 0, [
            issue("1001", "ENG-1", ts(1)),
            issue("1002", "ENG-2", ts(2), attachments=[attachment("201", "spec.pdf", "application/pdf")]),
            issue("1003", "ENG-3", ts(3)),
        ])
        connector, notes = await make_connector(db, store)
        await connector.run_sync()
        search.pages.clear()
        return connector, notes

    async def test_issues_deleted_in_jira_are_removed_with_their_attachments(self, jira, db, store, search) -> None:
        connector, _ = await self._synced(jira, db, store, search)
        audit_pages = [
            {"entities": [{"affectedObjects": [{"type": "ISSUE", "name": "ENG-2"}]}], "pagingInfo": {"lastPage": False, "nextPageOffset": 1}},
            {"entities": [{"affectedObjects": [{"type": "ISSUE", "name": "ENG-3"}]}], "pagingInfo": {"lastPage": True}},
        ]
        jira.on("GET", "/rest/auditing/1.0/events", audit_pages)
        jira.on("GET", f"{API}/issue/ENG-2", json_response({"errorMessages": ["Issue Does Not Exist"]}, status=404))
        jira.on("GET", f"{API}/issue/ENG-3", {"id": "1003", "key": "ENG-3"})

        await connector.run_sync()

        assert "1002" not in db.records
        assert "attachment_201" not in db.records
        assert {"1001", "1003"} <= set(db.records), "an issue that still exists (moved, not deleted) is kept"
        offsets = [AtlassianApiStub.query(r).get("offset") for r in jira.calls("GET", "/rest/auditing/1.0/events")]
        assert offsets == ["0", "1"]
        assert store.values_for("issues_audit_deletions")

    async def _synced_with_audit_checkpoint(self, jira, db, store, search, monkeypatch) -> tuple[JiraDataCenterConnector, dict[str, Any]]:
        """Sync twice so the connector itself writes an audit checkpoint, then pin later clock readings."""
        connector, _ = await self._synced(jira, db, store, search)
        jira.on("GET", "/rest/auditing/1.0/events", {"entities": [], "pagingInfo": {"lastPage": True}})
        await connector.run_sync()
        saved = dict(store.values_for("issues_audit_deletions") or {})
        assert saved.get("last_sync_time"), "a clean audit pass writes the checkpoint"
        later = saved["last_sync_time"] + 3_600_000
        monkeypatch.setattr(
            "app.connectors.sources.atlassian.jira_data_center.connector.get_epoch_timestamp_in_ms", lambda: later
        )
        return connector, saved

    async def test_audit_log_without_admin_rights_warns_the_owner(self, jira, db, store, search) -> None:
        connector, notes = await self._synced(jira, db, store, search)
        jira.on("GET", "/rest/auditing/1.0/events", json_response({}, status=403))

        await connector.run_sync()

        assert "1002" in db.records
        assert any("audit log permission" in t for t in notes.titles())

    async def test_a_failed_audit_read_does_not_skip_past_those_deletions(self, jira, db, store, search, monkeypatch) -> None:
        connector, before = await self._synced_with_audit_checkpoint(jira, db, store, search, monkeypatch)
        jira.on("GET", "/rest/auditing/1.0/events", json_response({}, status=500))

        await connector.run_sync()

        assert store.values_for("issues_audit_deletions") == before

    async def test_a_forbidden_audit_read_does_not_skip_past_those_deletions(self, jira, db, store, search, monkeypatch) -> None:
        connector, before = await self._synced_with_audit_checkpoint(jira, db, store, search, monkeypatch)
        jira.on("GET", "/rest/auditing/1.0/events", json_response({}, status=403))

        await connector.run_sync()

        assert store.values_for("issues_audit_deletions") == before


class TestPlaceholderAncestors:
    async def test_out_of_scope_parent_stub_is_filled_in_from_jira(self, jira, db, store, search) -> None:
        stub_site(jira, search)
        stub = TicketRecord(
            id="stub-1", org_id="org-1", external_record_id="900", record_name="", record_type=RecordType.TICKET,
            origin="CONNECTOR", connector_name="JIRA DATA CENTER", connector_id=CONNECTOR_ID,
            external_record_group_id="10000", version=0, is_placeholder=True, mime_type="text/plain",
        )
        db.records["900"] = stub
        jira.on("POST", f"{API}/search", lambda r: json_response({"errorMessages": ["bad id"]}, status=400)
                if "id in" in json.loads(r.content)["jql"] else search(r))
        jira.on("GET", f"{API}/issue/900", issue("900", "ENG-900", ts(5)))
        connector, _ = await make_connector(db, store)

        await connector.run_sync()

        filled = db.records["900"]
        assert filled.id == "stub-1"
        assert filled.record_name == "[ENG-900] Summary of ENG-900"
        assert filled.is_placeholder is True
        assert filled.weburl == f"{BASE}/browse/ENG-900"


class TestStreaming:
    async def test_issue_is_streamed_with_images_inlined_and_every_comment(self, jira, db, store, search) -> None:
        stub_site(jira, search)
        rendered = (
            '<p>See <span class="image-wrap"><a href="/secure/attachment/200/diagram.png">'
            '<img src="/secure/attachment/200/diagram.png"></a></span>'
            ' and <img src="/secure/attachment/202/broken.png">'
            ' <img class="rendericon" src="/images/icons/link.png"></p>'
        )
        jira.on("GET", f"{API}/issue/1001", {
            "id": "1001", "key": "ENG-1",
            "fields": {
                "summary": "Crash on save",
                "attachment": [attachment("200"), attachment("202", "broken.png"), attachment("203", "log.txt", "text/plain")],
                "comment": {"comments": [{"id": "c1", "author": {"displayName": "Bob"}}], "total": 2},
                "project": {"id": "10000"},
            },
            "renderedFields": {"description": rendered, "comment": {"comments": [
                {"id": "c1", "body": '<p>first, see <a href="/secure/attachment/203/log.txt">log</a></p>'},
            ]}},
        })
        jira.on("GET", f"{API}/issue/1001/comment", {"comments": [{"id": "c2", "author": {"displayName": "Carol"}, "renderedBody": "<p>second</p>"}]})
        jira.on("GET", "/secure/attachment/200/diagram.png", httpx.Response(200, content=b"PNGDATA"))
        jira.on("GET", "/secure/attachment/202/broken.png", httpx.Response(500, content=b"err"))
        connector, _ = await make_connector(db, store)
        record = TicketRecord(
            id="t-1", org_id="org-1", external_record_id="1001", record_name="[ENG-1] Crash on save", record_type=RecordType.TICKET,
            origin="CONNECTOR", connector_name="JIRA DATA CENTER", connector_id=CONNECTOR_ID, external_record_group_id="10000",
            version=0, mime_type="text/plain", weburl=f"{BASE}/browse/ENG-1",
        )

        response = await connector.stream_record(record)
        body = b"".join([chunk async for chunk in response.body_iterator])
        container = json.loads(body)

        groups = container["block_groups"]
        description = groups[0]["data"]
        assert f"data:image/png;base64,{base64.b64encode(b'PNGDATA').decode()}" in description
        assert 'alt="Image_1"' in description
        assert "image-wrap" not in description.split("broken.png")[0], "the inlined image is unwrapped"
        assert "/secure/attachment/202/broken.png" in description, "a failed image leaves the link, not a broken issue"
        assert "rendericon" not in description
        comments = [g for g in groups if g.get("sub_type") == "comment"]
        assert [c["source_group_id"] for c in comments] == ["c1", "c2"], "comments past the embedded page are fetched"
        assert comments[0]["children_records"][0]["child_name"] == "log.txt"
        assert {"attachment_200", "attachment_202", "attachment_203"} <= set(db.records), "attachments added since sync are recorded"
        image_call = jira.calls("GET", "/secure/attachment/200/diagram.png")[0]
        assert image_call.headers["Authorization"] == f"Bearer {FAKE_PAT}"

    async def test_attachment_download_and_missing_attachment(self, jira, db, store, search) -> None:
        stub_site(jira, search)
        jira.on("GET", "/secure/attachment/300/report.pdf", httpx.Response(200, content=b"%PDF-1"))
        jira.on("GET", "/secure/attachment/301/gone.pdf", httpx.Response(404, content=b"missing"))
        connector, _ = await make_connector(db, store)

        def file_record(aid: str, name: str) -> FileRecord:
            return FileRecord(
                id=f"f-{aid}", org_id="org-1", external_record_id=f"attachment_{aid}", record_name=name, record_type=RecordType.FILE,
                origin="CONNECTOR", connector_name="JIRA DATA CENTER", connector_id=CONNECTOR_ID, version=0,
                mime_type="application/pdf", is_file=True, parent_external_record_id="1001",
            )

        response = await connector.stream_record(file_record("300", "report.pdf"))
        body = b"".join([chunk async for chunk in response.body_iterator])
        assert body == b"%PDF-1"

        with pytest.raises(HTTPException) as err:
            await connector.stream_record(file_record("301", "gone.pdf"))
        assert err.value.status_code == 404

    async def test_placeholder_records_cannot_be_opened(self, jira, db, store, search) -> None:
        stub_site(jira, search)
        connector, _ = await make_connector(db, store)
        stub = TicketRecord(
            id="s", org_id="org-1", external_record_id="900", record_name="", record_type=RecordType.TICKET, origin="CONNECTOR",
            connector_name="JIRA DATA CENTER", connector_id=CONNECTOR_ID, version=0, is_placeholder=True, mime_type="text/plain",
        )
        with pytest.raises(HTTPException):
            await connector.stream_record(stub)


class TestReindex:
    async def test_changed_issue_is_rewritten_and_removed_attachment_is_just_reindexed(self, jira, db, store, search) -> None:
        stub_site(jira, search)
        connector, _ = await make_connector(db, store)
        ticket = TicketRecord(
            id="t-1", org_id="org-1", external_record_id="1001", record_name="[ENG-1] old", record_type=RecordType.TICKET,
            origin="CONNECTOR", connector_name="JIRA DATA CENTER", connector_id=CONNECTOR_ID, external_record_group_id="10000",
            version=2, mime_type="text/plain", source_updated_at=connector._parse_jira_timestamp(ts(1)),
        )
        db.records["1001"] = ticket
        gone = FileRecord(
            id="f-1", org_id="org-1", external_record_id="attachment_250", record_name="old.png", record_type=RecordType.FILE,
            origin="CONNECTOR", connector_name="JIRA DATA CENTER", connector_id=CONNECTOR_ID, version=0, mime_type="image/png",
            is_file=True, parent_external_record_id="1001",
        )
        changed = FileRecord(
            id="f-2", org_id="org-1", external_record_id="attachment_200", record_name="diagram.png", record_type=RecordType.FILE,
            origin="CONNECTOR", connector_name="JIRA DATA CENTER", connector_id=CONNECTOR_ID, version=0, mime_type="image/png",
            is_file=True, parent_external_record_id="1001", source_updated_at=1,
        )
        jira.on("GET", f"{API}/issue/1001", issue("1001", "ENG-1", ts(6), attachments=[attachment("200")]))

        await connector.reindex_records([ticket, gone, changed])

        rewritten = {r.external_record_id: r for batch in db.record_batches for r in batch}
        assert rewritten["1001"].id == "t-1" and rewritten["1001"].version == 3
        assert rewritten["attachment_200"].id == "f-2" and rewritten["attachment_200"].version == 1
        assert rewritten["attachment_200"].parent_node_id == "t-1"
        assert [r.id for r in db.reindexed] == ["f-1"]


class TestHierarchyLinks:
    async def test_epic_and_parent_links_resolve_once_and_missing_targets_leave_no_parent(self, jira, db, store, search) -> None:
        stub_site(jira, search)
        jira.on("GET", f"{API}/field", json_response([
            "junk",
            {"name": "no id"},
            {"id": EPIC_FIELD, "name": "Epic Link"},
            {"id": "customfield_10200", "name": "Parent Link", "schema": {"custom": "x"}},
        ]))
        jira.on("GET", f"{API}/issue/ENG-404", json_response({"errorMessages": ["gone"]}, status=404))
        jira.on("GET", f"{API}/issue/ENG-500", json_response({}, status=500))
        jira.on("GET", f"{API}/issue/INIT-1", {"id": "5001", "key": "INIT-1"})
        with_parent_link = issue("1005", "ENG-5", ts(4))
        with_parent_link["fields"]["customfield_10200"] = {"data": {"key": "INIT-1"}}
        search.add("ENG", 0, [
            issue("1003", "ENG-3", ts(1), epic="ENG-1"),
            issue("1004", "ENG-4", ts(2), epic={"key": "ENG-1"}),
            issue("1006", "ENG-6", ts(3), epic="ENG-404"),
            issue("1007", "ENG-7", ts(3), epic="ENG-500"),
            with_parent_link,
        ])
        connector, _ = await make_connector(db, store)

        await connector.run_sync()

        synced = tickets(db)
        assert synced["1003"].parent_external_record_id == synced["1004"].parent_external_record_id == "1001"
        assert len(jira.calls("GET", f"{API}/issue/ENG-1")) == 1, "the epic key is looked up once per project"
        assert synced["1005"].parent_external_record_id == "5001"
        assert synced["1006"].parent_external_record_id is None
        assert synced["1007"].parent_external_record_id is None

    async def test_filters_become_jql_date_bounds(self, jira, db, store, search) -> None:
        stub_site(jira, search)
        day = 86_400_000
        now = time.time() * 1000
        filters = {"sync": {"values": {
            "modified": {"operator": "is_between", "type": "datetime", "value": {"start": int(now - 10 * day), "end": int(now - day)}},
            "created": {"operator": "is_between", "type": "datetime", "value": {"start": int(now - 20 * day), "end": int(now - 2 * day)}},
        }}}
        connector, _ = await make_connector(db, store, filters=filters)

        await connector.run_sync()

        jql = search.jql[0]
        for clause in ("updated >= -", "updated <= -", "created >= -", "created <= -"):
            assert clause in jql, jql


class TestRoleActors:
    async def test_every_actor_shape_resolves_to_a_synced_user(self, jira, db, store, search) -> None:
        stub_site(jira, search)
        jira.on("GET", f"{API}/project/ENG/role/10002", {"name": "Developers", "actors": [
            {"type": "atlassian-user-role-actor", "actorUser": "not-a-dict", "key": "alice-key"},
            {"type": "atlassian-user-role-actor", "actorUser": {"emailAddress": "BOB@example.com"}},
            {"type": "atlassian-user-role-actor", "actorUser": {"key": "nobody"}},
            {"type": "atlassian-group-role-actor", "groupId": "grp-1", "name": "renamed"},
        ]})
        connector, _ = await make_connector(db, store)
        users = [
            AppUser(app_name="JIRA DATA CENTER", connector_id=CONNECTOR_ID, source_user_id=k, org_id="org-1", email=e, full_name=k)
            for k, e in (("alice-key", "alice@example.com"), ("bob-key", "bob@example.com"), ("carol-key", "carol@example.com"))
        ]

        await connector._sync_project_roles(["ENG"], users, {"grp-1": [users[2]]})

        assert {m.email for m in db.app_roles["ENG_10002"]} == {"alice@example.com", "bob@example.com", "carol@example.com"}

    async def test_unreadable_role_list_is_reported_not_fatal(self, jira, db, store, search) -> None:
        stub_site(jira, search)
        jira.on("GET", f"{API}/project/ENG/role", httpx.Response(200, content=b"not json"))
        connector, notes = await make_connector(db, store)

        await connector._sync_project_roles(["ENG"], [])

        assert db.app_roles == {}
        assert any("couldn't sync project roles" in t for t in notes.titles())


class TestGroupMemberPaging:
    async def test_members_on_later_pages_are_read_when_jira_says_more_follow(self, jira, db, store, search) -> None:
        stub_site(jira, search)
        pages = {
            "0": {"values": [{"key": "alice-key"}], "isLast": False},
            "1": {"values": [{"key": "carol-key"}], "isLast": True},
        }

        def members(request: httpx.Request) -> httpx.Response:
            q = AtlassianApiStub.query(request)
            if q["groupname"] != "devs":
                return json_response({"values": [], "isLast": True})
            return json_response(pages[q.get("startAt", "0")])

        jira.on("GET", f"{API}/group/member", members)
        connector, _ = await make_connector(db, store)

        await connector.run_sync()

        devs_calls = [r for r in jira.calls("GET", f"{API}/group/member") if AtlassianApiStub.query(r)["groupname"] == "devs"]
        assert [AtlassianApiStub.query(r).get("startAt") for r in devs_calls] == ["0", "1"]
        assert sorted(m.email for m in db.groups_saved["devs"]) == ["alice@example.com", "carol@example.com"]

    async def test_a_group_that_disappears_part_way_through_its_members_ends_up_empty(self, jira, db, store, search) -> None:
        stub_site(jira, search)

        def members(request: httpx.Request) -> httpx.Response:
            q = AtlassianApiStub.query(request)
            if q["groupname"] != "devs":
                return json_response({"values": [], "isLast": True})
            if q.get("startAt", "0") == "0":
                return json_response({"values": [{"key": "alice-key"}], "isLast": False})
            return json_response({"errorMessages": ["no group"]}, status=404)

        jira.on("GET", f"{API}/group/member", members)
        connector, _ = await make_connector(db, store)

        await connector.run_sync()

        assert db.groups_saved["devs"] == [], "a deleted group keeps no members"


class TestDirectoryEdgeCases:
    async def test_group_members_as_plain_list_and_short_pages(self, jira, db, store, search) -> None:
        stub_site(jira, search)
        connector, _ = await make_connector(db, store)

        jira.on("GET", f"{API}/group/member", json_response([{"key": "a"}, {"name": "b"}]))
        assert await connector._fetch_group_members("g", "g") == ["a", "b"]

        jira.on("GET", f"{API}/group/member", json_response("odd"))
        assert await connector._fetch_group_members("g", "g") == []
        assert await connector._fetch_group_members("g", "") == []

    async def test_user_list_errors(self, jira, db, store, search) -> None:
        stub_site(jira, search)
        connector, _ = await make_connector(db, store)

        jira.on("GET", f"{API}/user/list", json_response({"errorMessages": ["boom"]}, status=500))
        with pytest.raises(Exception, match="Failed to fetch users via /user/list"):
            await connector._fetch_users_via_list()

        jira.on("GET", f"{API}/user/list", httpx.Response(200, content=b"<html>"))
        assert await connector._fetch_users_via_list() == []

        jira.on("GET", f"{API}/user/list", {"values": []})
        assert await connector._fetch_users_via_list() == []

    async def test_reverse_lookup_skips_keyless_and_failing_matches(self, jira, db, store, search) -> None:
        stub_site(jira, search)
        connector, _ = await make_connector(db, store)

        def lookups(request: httpx.Request) -> httpx.Response:
            who = AtlassianApiStub.query(request).get("username")
            if who == "nokey@example.com":
                return json_response([{"emailAddress": "nokey@example.com"}])
            if who == "bad@example.com":
                raise httpx.ConnectError("reset")
            return json_response({"not": "a list"})

        jira.on("GET", f"{API}/user/search", lookups)
        resolved: dict[str, AppUser] = {}
        found = await connector._resolve_private_email_users(
            {"nokey@example.com", "bad@example.com", "other@example.com"}, {"x"}, resolved
        )
        assert found == 0 and resolved == {}

    @pytest.mark.parametrize(
        "response",
        [json_response({}, status=500), json_response({"roles": []}), json_response([{"groups": ["g"]}, "junk", {"key": "empty"}])],
    )
    async def test_application_role_edge_cases_map_to_nothing(self, jira, db, store, search, response) -> None:
        stub_site(jira, search)
        jira.on("GET", f"{API}/applicationrole", response)
        connector, _ = await make_connector(db, store)

        assert await connector._fetch_application_roles_to_groups_mapping() == {}


class TestInlineMedia:
    async def test_media_is_matched_by_id_then_filename_and_failures_return_nothing(self, jira, db, store, search) -> None:
        stub_site(jira, search)
        jira.on("GET", f"{API}/issue/1001", {"fields": {"attachment": [
            {"id": "200", "filename": "diagram-final.png", "mimeType": "image/png"},
            {"id": "201", "filename": "photo.jpg", "mimeType": "image/jpeg"},
            {"id": "", "filename": ""},
        ]}})
        jira.on("GET", f"{API}/issue/1002", {"fields": {"attachment": []}})
        jira.on("GET", f"{API}/issue/1003", json_response({}, status=403))
        jira.on("GET", "/secure/attachment/200/diagram-final.png", httpx.Response(200, content=b"A"))
        jira.on("GET", "/secure/attachment/201/photo.jpg", httpx.Response(200, content=b"B"))
        connector, _ = await make_connector(db, store)

        assert await connector._fetch_media_as_base64("1001", "", "photo.jpg") == "data:image/jpeg;base64,Qg=="
        assert await connector._fetch_media_as_base64("1001", "999", "diagram") == "data:image/png;base64,QQ=="
        assert await connector._fetch_media_as_base64("1002", "200", "x.png") is None
        assert await connector._fetch_media_as_base64("1003", "200", "x.png") is None
        assert len(jira.calls("GET", f"{API}/issue/1001")) == 1, "issue attachments are cached per issue"


class TestPlaceholderChains:
    async def test_stub_chain_is_walked_upwards_through_jql(self, jira, db, store, search) -> None:
        stub_site(jira, search)

        def stub(ext_id: str) -> TicketRecord:
            return TicketRecord(
                id=f"stub-{ext_id}", org_id="org-1", external_record_id=ext_id, record_name="", record_type=RecordType.TICKET,
                origin="CONNECTOR", connector_name="JIRA DATA CENTER", connector_id=CONNECTOR_ID,
                external_record_group_id="10000", version=0, is_placeholder=True, mime_type="text/plain",
            )

        db.records["900"] = stub("900")
        db.records["901"] = stub("901")
        child = issue("900", "ENG-900", ts(5))
        child["fields"]["parent"] = {"id": "901", "key": "ENG-901"}
        by_id = {"900": child, "901": issue("901", "ENG-901", ts(4))}

        def searching(request: httpx.Request) -> httpx.Response:
            jql = json.loads(request.content)["jql"]
            if jql.startswith("id in ("):
                wanted = jql[len("id in ("):].split(")")[0].split(", ")
                return json_response({"issues": [by_id[i] for i in wanted], "total": len(wanted)})
            return search(request)

        jira.on("POST", f"{API}/search", searching)
        connector, _ = await make_connector(db, store)

        await connector.run_sync()

        assert db.records["900"].record_name == "[ENG-900] Summary of ENG-900"
        assert db.records["900"].parent_external_record_id == "901"
        assert db.records["901"].record_name == "[ENG-901] Summary of ENG-901"
        assert all(db.records[k].is_placeholder for k in ("900", "901"))


class TestStreamingErrors:
    async def test_bad_requests_surface_as_clear_http_errors(self, jira, db, store, search) -> None:
        stub_site(jira, search)
        connector, _ = await make_connector(db, store)
        nameless = FileRecord(
            id="f", org_id="org-1", external_record_id="attachment_1", record_name="", record_type=RecordType.FILE,
            origin="CONNECTOR", connector_name="JIRA DATA CENTER", connector_id=CONNECTOR_ID, version=0, mime_type="x", is_file=True,
        )
        with pytest.raises(HTTPException) as err:
            await connector.stream_record(nameless)
        assert err.value.status_code == 400

        jira.on("GET", f"{API}/issue/1001", json_response({}, status=200))
        ticket = TicketRecord(
            id="t", org_id="org-1", external_record_id="1001", record_name="x", record_type=RecordType.TICKET, origin="CONNECTOR",
            connector_name="JIRA DATA CENTER", connector_id=CONNECTOR_ID, version=0, mime_type="text/plain",
        )
        with pytest.raises(HTTPException) as err:
            await connector.stream_record(ticket)
        assert err.value.status_code == 404

        jira.on("GET", f"{API}/issue/1001", json_response({"errorMessages": ["no"]}, status=403))
        with pytest.raises(HTTPException) as err:
            await connector.stream_record(ticket)
        assert err.value.status_code == 403


class TestReindexEdgeCases:
    async def test_attachment_reindex_without_changes_or_with_missing_parent(self, jira, db, store, search) -> None:
        stub_site(jira, search)
        connector, _ = await make_connector(db, store)
        created = connector._parse_jira_timestamp(ts(1, 9))

        def f(ext_id: str, parent: str, updated: Optional[int] = None) -> FileRecord:
            return FileRecord(
                id=f"f-{ext_id}", org_id="org-1", external_record_id=ext_id, record_name="diagram.png", record_type=RecordType.FILE,
                origin="CONNECTOR", connector_name="JIRA DATA CENTER", connector_id=CONNECTOR_ID, version=0, mime_type="image/png",
                is_file=True, parent_external_record_id=parent, source_updated_at=updated,
            )

        jira.on("GET", f"{API}/issue/1001", issue("1001", "ENG-1", attachments=[attachment("200")]))
        jira.on("GET", f"{API}/issue/1404", json_response({}, status=400))
        jira.on("GET", f"{API}/issue/1500", json_response({}, status=500))
        unchanged, bare_id, gone_parent, broken_parent = f("attachment_200", "1001", created), f("200", "1001", created), f("attachment_7", "1404"), f("attachment_8", "1500")

        await connector.reindex_records([unchanged, bare_id, gone_parent, broken_parent])

        assert db.record_batches == []
        assert {r.id for r in db.reindexed} == {"f-attachment_200", "f-200", "f-attachment_7", "f-attachment_8"}
