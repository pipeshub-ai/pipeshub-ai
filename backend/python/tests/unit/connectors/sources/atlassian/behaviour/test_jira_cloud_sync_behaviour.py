"""Jira Cloud issue sync, driven over a fake Atlassian API with its real OAuth client.

The connector, Jira client, request builder and HTTP client are real; every HTTP
request is answered by an in-memory stub and our databases are in-memory fakes.
Retry waits are recorded rather than slept (see conftest ``backoff_sleeps``).
"""

import json
import logging
from collections.abc import Callable, Iterator
from datetime import datetime
from typing import Any, Optional
from zoneinfo import ZoneInfo

import httpx
import pytest
from atlassian_behaviour_fakes import (
    AtlassianApiStub,
    FakeCheckpointStore,
    FakeConfigService,
    json_response,
    run_full_sync,
)
from atlassian_cloud_fakes import (
    CLOUD_ID,
    SITE,
    CloudRecordsDb,
    RecordingNotifications,
    bearer,
    drain_notifications,
    oauth_config,
    one_site,
    route_every_http_client,
)
from fastapi import HTTPException

from app.config.constants.arangodb import Connectors
from app.connectors.core.base.connector.connector_service import BaseConnector
from app.connectors.sources.atlassian.core.read_outcome import StoredAccessReadError
from app.connectors.sources.atlassian.jira_cloud.connector import JiraConnector
from app.models.entities import (
    FileRecord,
    RecordGroup,
    RecordGroupType,
    RecordType,
    TicketRecord,
)
from app.sources.client.jira.jira import JiraRESTClientViaToken
from app.sources.external.jira.jira import JiraDataSource

CONNECTOR_ID = "jira-cloud-1"
JIRA = f"/ex/jira/{CLOUD_ID}/rest/api/3"
SEARCH = f"{JIRA}/search/jql"


def issue(num: int, updated: str, project: str = "ENG", attachments: Optional[list] = None) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "summary": f"Issue {num}",
        "issuetype": {"name": "Task"},
        "status": {"name": "To Do"},
        "priority": {"name": "Medium"},
        "created": "2024-05-01T09:00:00.000+0000",
        "updated": updated,
        "project": {"id": "10000", "key": project},
    }
    if attachments:
        fields["attachment"] = attachments
    return {"id": str(num), "key": f"{project}-{num}", "fields": fields}


def project(key: str, pid: str) -> tuple[RecordGroup, list]:
    group = RecordGroup(
        org_id="org-1", name=key, short_name=key, external_group_id=pid,
        connector_name=Connectors.JIRA, connector_id=CONNECTOR_ID, group_type=RecordGroupType.PROJECT,
    )
    return group, []


class IssueSearch:
    """Answers the JQL search per project and page token; remembers each request body."""

    def __init__(self) -> None:
        self.pages: dict[tuple[str, Optional[str]], Any] = {}
        self.bodies: list[dict[str, Any]] = []
        self.tokens: list[str] = []

    def add(self, project_key: str, token: Optional[str], response: object) -> None:
        self.pages[(project_key, token)] = response

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.bodies.append(body)
        self.tokens.append(bearer(request))
        key = body["jql"].split('project = "')[1].split('"')[0]
        answer = self.pages.get((key, body.get("nextPageToken")), {"issues": []})
        if isinstance(answer, list):
            answer = answer.pop(0) if len(answer) > 1 else answer[0]
        return answer if isinstance(answer, httpx.Response) else json_response(answer)


@pytest.fixture
def db() -> CloudRecordsDb:
    return CloudRecordsDb()


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch) -> AtlassianApiStub:
    stub = AtlassianApiStub()
    route_every_http_client(monkeypatch, stub)
    one_site(stub)
    stub.on("GET", f"{JIRA}/myself", {"accountId": "me", "emailAddress": "sync@acme.com", "timeZone": "America/New_York"})
    return stub


@pytest.fixture
def search(api: AtlassianApiStub) -> IssueSearch:
    handler = IssueSearch()
    api.on("POST", SEARCH, handler)
    return handler


async def ready_connector(db, checkpoints: FakeCheckpointStore) -> tuple[JiraConnector, FakeConfigService]:
    config_service = FakeConfigService(CONNECTOR_ID, oauth_config())
    connector = JiraConnector(logging.getLogger("test.jira_cloud"), db, checkpoints, config_service, CONNECTOR_ID, "team", "creator-1")
    connector._notification_service = RecordingNotifications()
    assert await connector.init() is True
    return connector, config_service


def tickets(db: CloudRecordsDb) -> dict[str, Any]:
    return {k: r for k, r in db.records.items() if isinstance(r, TicketRecord)}


class TestOAuthClient:
    async def test_init_uses_the_real_client_and_learns_the_account_timezone(self, api, db, checkpoints) -> None:
        connector, _ = await ready_connector(db, checkpoints)

        assert type(connector.data_source) is JiraDataSource
        assert type(connector.external_client.get_client()) is JiraRESTClientViaToken
        assert connector.site_url == SITE
        assert connector._jql_timezone == ZoneInfo("America/New_York")
        assert bearer(api.calls("GET", f"{JIRA}/myself")[0]) == "Bearer fake-access-1"

    async def test_a_refreshed_token_is_used_for_the_next_search(self, api, db, checkpoints, search) -> None:
        connector, config_service = await ready_connector(db, checkpoints)
        config_service.config["credentials"]["access_token"] = "fake-access-2"

        await connector._sync_all_project_issues([project("ENG", "10000")], [], None)

        assert search.tokens == ["Bearer fake-access-2"]

    async def test_a_lost_token_fails_the_project_without_calling_jira(self, api, db, checkpoints, search) -> None:
        connector, config_service = await ready_connector(db, checkpoints)
        config_service.config["credentials"]["access_token"] = ""

        stats = await connector._sync_all_project_issues([project("ENG", "10000")], [], None)

        assert stats["failed_project_keys"] == ["ENG"]
        assert search.bodies == []
        with pytest.raises(HTTPException) as err:
            await connector._get_fresh_datasource()
        assert err.value.status_code == 409 and "Check its settings" in err.value.detail


class TestIssuePagination:
    async def test_all_pages_are_read_and_the_next_run_starts_after_the_last_issue(self, api, db, checkpoints, search) -> None:
        search.add("ENG", None, {"issues": [issue(1, "2024-05-01T10:00:00.000+0000"), issue(2, "2024-05-01T11:00:00.000+0000")], "nextPageToken": "T2"})
        search.add("ENG", "T2", {"issues": [issue(3, "2024-05-02T15:30:00.000+0000", attachments=[
            {"id": "900", "filename": "log.txt", "mimeType": "text/plain", "size": 12, "created": "2024-05-02T15:00:00.000+0000"},
        ])]})
        connector, _ = await ready_connector(db, checkpoints)

        stats = await connector._sync_all_project_issues([project("ENG", "10000")], [], None)

        assert set(tickets(db)) == {"1", "2", "3"}
        assert stats["total_synced"] >= 3 and stats["failed_project_keys"] == []
        assert [b.get("nextPageToken") for b in search.bodies] == [None, "T2"]
        assert search.bodies[0]["jql"] == 'project = "ENG" ORDER BY updated ASC, id ASC'
        attachment = db.records["attachment_900"]
        assert isinstance(attachment, FileRecord) and attachment.parent_external_record_id == "3"
        assert db.records["1"].weburl == f"{SITE}/browse/ENG-1"

        saved = checkpoints.values_for("project_ENG")
        last = int(datetime.fromisoformat("2024-05-02T15:30:00+00:00").timestamp() * 1000)
        assert saved["last_issue_updated"] == last

        search.bodies.clear()
        await connector._sync_all_project_issues([project("ENG", "10000")], [], None)
        assert search.bodies[0]["jql"] == 'project = "ENG" AND updated > "2024-05-02 11:28" ORDER BY updated ASC, id ASC', (
            "the cut is written in the Jira account's timezone (New York), not UTC, "
            "and reads 2 minutes back for issues the search index returned late"
        )

    async def test_an_unchanged_issue_seen_again_is_not_rewritten(self, api, db, checkpoints, search) -> None:
        search.add("ENG", None, {"issues": [issue(1, "2024-05-01T10:00:00.000+0000")]})
        connector, _ = await ready_connector(db, checkpoints)
        await connector._sync_all_project_issues([project("ENG", "10000")], [], None)
        batches_after_first = len(db.record_batches)
        first_id = db.records["1"].id

        await connector._sync_all_project_issues([project("ENG", "10000")], [], None)

        assert db.records["1"].id == first_id
        assert len(db.record_batches) == batches_after_first, "nothing re-saved for an unchanged issue"
        assert db.content_updates == []

    async def test_an_edited_issue_is_sent_as_an_update(self, api, db, checkpoints, search) -> None:
        search.add("ENG", None, [
            {"issues": [issue(1, "2024-05-01T10:00:00.000+0000")]},
            {"issues": [issue(1, "2024-05-03T10:00:00.000+0000")]},
        ])
        connector, _ = await ready_connector(db, checkpoints)
        await connector._sync_all_project_issues([project("ENG", "10000")], [], None)
        await connector._sync_all_project_issues([project("ENG", "10000")], [], None)

        (updated,) = db.content_updates
        assert updated.external_record_id == "1" and updated.version == 1

    async def test_one_malformed_issue_does_not_drop_the_page(self, api, db, checkpoints, search) -> None:
        broken = {"id": "2", "key": "ENG-2", "fields": {"summary": "x", "issuetype": "not-an-object", "updated": "2024-05-01T10:00:00.000+0000"}}
        search.add("ENG", None, {"issues": [issue(1, "2024-05-01T10:00:00.000+0000"), broken, issue(3, "2024-05-01T10:01:00.000+0000")]})
        connector, _ = await ready_connector(db, checkpoints)

        stats = await connector._sync_all_project_issues([project("ENG", "10000")], [], None)

        assert set(tickets(db)) == {"1", "3"}
        assert stats["failed_project_keys"] == []

    async def test_a_page_that_times_out_is_asked_for_in_halves(self, api, db, checkpoints, search) -> None:
        sizes = slow_above(api, search, 25)
        search.add("ENG", None, {"issues": [issue(1, "2024-05-01T10:00:00.000+0000")]})
        connector, _ = await ready_connector(db, checkpoints)

        stats = await connector._sync_all_project_issues([project("ENG", "10000")], [], None)

        assert stats["failed_project_keys"] == [] and "1" in db.records
        assert list(dict.fromkeys(sizes)) == [100, 50, 25]

    async def test_a_page_that_never_arrives_fails_the_project_at_the_smallest_page_size(
        self, api, db, checkpoints, search
    ) -> None:
        sizes = slow_above(api, search, 0)
        connector, _ = await ready_connector(db, checkpoints)

        stats = await connector._sync_all_project_issues([project("ENG", "10000")], [], None)

        assert stats["failed_project_keys"] == ["ENG"]
        assert list(dict.fromkeys(sizes)) == [100, 50, 25, 12, 10]


def slow_above(api: AtlassianApiStub, search: "IssueSearch", page_size: int) -> list[int]:
    """Searches for more than ``page_size`` issues time out; returns every page size asked for."""
    sizes: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sizes.append(json.loads(request.content)["maxResults"])
        if sizes[-1] > page_size:
            raise httpx.ReadTimeout("timed out", request=request)
        return search(request)

    api.on("POST", SEARCH, handler)
    return sizes


class TestRateLimits:
    async def test_retry_after_is_honoured_then_the_search_succeeds(self, api, db, checkpoints, search, backoff_sleeps) -> None:
        search.add("ENG", None, [
            json_response({"message": "slow down"}, status=429, headers={"Retry-After": "7"}),
            {"issues": [issue(1, "2024-05-01T10:00:00.000+0000")]},
        ])
        connector, _ = await ready_connector(db, checkpoints)

        await connector._sync_all_project_issues([project("ENG", "10000")], [], None)

        assert backoff_sleeps == [7.0]
        assert "1" in db.records

    async def test_a_huge_retry_after_is_capped_so_the_sync_does_not_stall(self, api, db, checkpoints, search, backoff_sleeps) -> None:
        search.add("ENG", None, [
            json_response({}, status=429, headers={"Retry-After": "3600"}),
            {"issues": []},
        ])
        connector, _ = await ready_connector(db, checkpoints)

        await connector._sync_all_project_issues([project("ENG", "10000")], [], None)

        assert backoff_sleeps == [60.0]

    async def test_persistent_throttling_fails_only_that_project_and_keeps_its_checkpoint(
        self, api, db, checkpoints, search, backoff_sleeps
    ) -> None:
        search.add("ENG", None, json_response({}, status=429))
        search.add("OPS", None, {"issues": [issue(5, "2024-05-01T10:00:00.000+0000", project="OPS")]})
        connector, _ = await ready_connector(db, checkpoints)

        stats = await connector._sync_all_project_issues([project("ENG", "10000"), project("OPS", "10001")], [], None)

        assert stats["failed_project_keys"] == ["ENG"]
        assert "5" in db.records
        eng_attempts = [b for b in search.bodies if '"ENG"' in b["jql"]]
        assert len(eng_attempts) == 4, "gives up after the retry cap"
        assert len(backoff_sleeps) == 3 and all(0 < s <= 60 for s in backoff_sleeps)
        assert checkpoints.values_for("project_ENG") is None

    async def test_a_failure_part_way_resumes_from_the_last_saved_page(self, api, db, checkpoints, search) -> None:
        search.add("ENG", None, {"issues": [issue(1, "2024-05-01T10:00:00.000+0000")], "nextPageToken": "T2"})
        search.add("ENG", "T2", json_response({"errorMessages": ["boom"]}, status=500))
        connector, _ = await ready_connector(db, checkpoints)

        stats = await connector._sync_all_project_issues([project("ENG", "10000")], [], None)

        assert stats["failed_project_keys"] == ["ENG"]
        assert "1" in db.records
        resume_at = checkpoints.values_for("project_ENG")["last_issue_updated"]
        assert resume_at == int(datetime.fromisoformat("2024-05-01T10:00:00+00:00").timestamp() * 1000)

        search.bodies.clear()
        search.add("ENG", None, {"issues": []})
        await connector._sync_all_project_issues([project("ENG", "10000")], [], None)
        assert 'updated > "2024-05-01 05:58"' in search.bodies[0]["jql"], "the saved cursor, less the 2-minute overlap"

    async def test_network_drops_are_retried(self, api, db, checkpoints, search, backoff_sleeps) -> None:
        calls = {"n": 0}

        def flaky(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            if calls["n"] == 1:
                raise httpx.ReadError("connection reset", request=request)
            return search(request)

        api.on("POST", SEARCH, flaky)
        search.add("ENG", None, {"issues": [issue(1, "2024-05-01T10:00:00.000+0000")]})
        connector, _ = await ready_connector(db, checkpoints)

        stats = await connector._sync_all_project_issues([project("ENG", "10000")], [], None)

        assert stats["failed_project_keys"] == [] and "1" in db.records
        assert backoff_sleeps == [0.5]

    @pytest.mark.parametrize("status", [502, 503, 504])
    async def test_a_gateway_error_is_retried_like_a_rate_limit(self, api, site_db, checkpoints, search, status) -> None:
        stub_site(api)
        api.on("GET", f"{JIRA}/group/bulk", [
            json_response({"message": "gateway"}, status=status),
            {"values": [{"groupId": "grp-dev", "name": "devs"}], "isLast": True},
        ])
        connector, _ = await ready_connector(site_db, checkpoints)

        await connector.run_sync()

        assert len(api.calls("GET", f"{JIRA}/group/bulk")) == 2
        assert saved_members(site_db, "grp-dev") == ["ana@acme.com"]


class SiteDb(CloudRecordsDb):
    """Adds the role writes and the PipesHub user a full Jira Cloud sync needs."""

    def __init__(self) -> None:
        super().__init__()
        self.active_users = [type("U", (), {"email": "ana@acme.com"})()]
        self.app_roles: dict[str, list[Any]] = {}

    async def on_new_app_roles(self, roles: list[tuple[Any, list[Any]]]) -> None:
        for role, members in roles:
            self.app_roles[role.source_role_id] = list(members)

    async def get_nodes_by_filters(
        self, collection: str, filters: dict[str, Any], return_fields: list[str] | None = None
    ) -> list[dict[str, Any]]:
        """The stored project and role nodes, with the fields the connector asks for."""
        if collection == "roles":
            return [{"externalRoleId": role_id} for role_id in self.app_roles]
        assert collection == "recordGroups", collection
        return [{"externalGroupId": g.external_group_id, "shortName": g.short_name} for g in self.record_groups.values()]

    async def on_app_role_deleted(self, external_role_id: str, connector_id: str) -> None:
        self.app_roles.pop(external_role_id, None)


def stub_site(api: AtlassianApiStub) -> None:
    """One project (ENG) readable by one group (devs, whose only member is Ana), also its Developers role."""
    ana = {"accountId": "acc-ana", "accountType": "atlassian", "active": True, "emailAddress": "ana@acme.com", "displayName": "Ana"}
    api.on("GET", f"{JIRA}/users/search", lambda r: json_response([ana] if AtlassianApiStub.query(r).get("startAt") == "0" else []))
    api.on("GET", f"{JIRA}/group/bulk", {"values": [{"groupId": "grp-dev", "name": "devs"}], "isLast": True})
    api.on("GET", f"{JIRA}/group/member", {"values": [{"accountId": "acc-ana"}], "isLast": True})
    api.on("GET", f"{JIRA}/project/search", {"values": [{"id": "10000", "key": "ENG", "name": "Engineering"}], "isLast": True})
    api.on("GET", f"{JIRA}/project/ENG/permissionscheme", {"id": 1})
    api.on("GET", f"{JIRA}/permissionscheme/1/permission", {"permissions": [
        {"permission": "BROWSE_PROJECTS", "holder": {"type": "group", "parameter": "devs", "value": "grp-dev"}},
    ]})
    api.on("GET", f"{JIRA}/project/ENG/role", {"Developers": f"{SITE}{JIRA}/project/ENG/role/10002"})
    api.on("GET", f"{JIRA}/project/ENG/role/10002", {
        "name": "Developers", "actors": [{"type": "atlassian-group-role-actor", "name": "devs", "groupId": "grp-dev"}],
    })


def groups_in_two_pages(api: AtlassianApiStub, second_page: object) -> None:
    """A full first page of other groups; devs (the Developers role's group) is on the second."""
    first = {"values": [{"groupId": f"grp-{i}", "name": f"team-{i}"} for i in range(50)], "isLast": False}

    def bulk(request: httpx.Request) -> httpx.Response:
        if AtlassianApiStub.query(request).get("startAt") == "0":
            return json_response(first)
        return second_page if isinstance(second_page, httpx.Response) else json_response(second_page)

    api.on("GET", f"{JIRA}/group/bulk", bulk)


def by_start(pages: dict[str, object]) -> Callable[[httpx.Request], httpx.Response]:
    """Answer each startAt with its page; a page may be a payload or a prepared response."""
    def handler(request: httpx.Request) -> httpx.Response:
        page = pages[AtlassianApiStub.query(request).get("startAt", "0")]
        return page if isinstance(page, httpx.Response) else json_response(page)
    return handler


def two_users(api: AtlassianApiStub) -> None:
    people = [
        {"accountId": "acc-ana", "accountType": "atlassian", "active": True, "emailAddress": "ana@acme.com", "displayName": "Ana"},
        {"accountId": "acc-bo", "accountType": "atlassian", "active": True, "emailAddress": "bo@acme.com", "displayName": "Bo"},
    ]
    api.on("GET", f"{JIRA}/users/search", lambda r: json_response(people if AtlassianApiStub.query(r).get("startAt") == "0" else []))


@pytest.fixture
def site_db() -> SiteDb:
    return SiteDb()


def acl(db: SiteDb) -> list[tuple[str, Optional[str]]]:
    return sorted((str(p.entity_type), p.external_id or p.email) for p in db.record_group_permissions["10000"])


def saved_members(db: SiteDb, group_id: str) -> list[str]:
    """Members from the last time the group was saved."""
    return [[u.email for u in members] for g, members in db.user_groups if g.source_user_group_id == group_id][-1]


class TestAccessControlSafety:
    async def test_a_failed_permission_scheme_read_does_not_wipe_the_project_acl(self, api, site_db, checkpoints, search) -> None:
        stub_site(api)
        connector, _ = await ready_connector(site_db, checkpoints)
        await connector.run_sync()
        before = acl(site_db)
        assert before, "the first sync grants access"

        api.on("GET", f"{JIRA}/project/ENG/permissionscheme", json_response({"errorMessages": ["oops"]}, status=500))
        await connector.run_sync()

        assert acl(site_db) == before

    async def test_a_forbidden_scheme_with_no_jira_account_email_keeps_the_project_acl(self, api, site_db, checkpoints, search) -> None:
        stub_site(api)
        connector, _ = await ready_connector(site_db, checkpoints)
        await connector.run_sync()
        before = acl(site_db)

        connector._authenticated_jira_email = None
        api.on("GET", f"{JIRA}/project/ENG/permissionscheme", json_response({"errorMessages": ["no"]}, status=403))
        await connector.run_sync()

        assert acl(site_db) == before, "a 403 doesn't mean no one can see the project"

    async def test_a_forbidden_scheme_falls_back_to_the_jira_account_when_its_email_is_known(self, api, site_db, checkpoints, search) -> None:
        stub_site(api)
        api.on("GET", f"{JIRA}/project/ENG/permissionscheme", json_response({"errorMessages": ["no"]}, status=403))
        connector, _ = await ready_connector(site_db, checkpoints)

        await connector.run_sync()

        assert acl(site_db) == [("EntityType.USER", "sync@acme.com")]

    async def test_an_unreadable_permission_scheme_still_syncs_the_projects_issues(self, api, site_db, checkpoints, search) -> None:
        stub_site(api)
        api.on("GET", f"{JIRA}/project/ENG/permissionscheme", json_response({"errorMessages": ["oops"]}, status=500))
        search.add("ENG", None, {"issues": [issue(1, "2024-05-01T10:00:00.000+0000")]})
        connector, _ = await ready_connector(site_db, checkpoints)

        await connector.run_sync()

        assert "10000" not in site_db.record_group_permissions, "no empty access list is written"
        assert "1" in tickets(site_db)

    async def test_a_failed_member_read_keeps_the_group_and_the_roles_that_include_it(self, api, site_db, checkpoints, search) -> None:
        stub_site(api)
        connector, _ = await ready_connector(site_db, checkpoints)
        await connector.run_sync()
        assert saved_members(site_db, "grp-dev") == ["ana@acme.com"]
        assert [m.email for m in site_db.app_roles["ENG_10002"]] == ["ana@acme.com"], "Ana is in the role only through devs"

        api.on("GET", f"{JIRA}/group/member", json_response({"errorMessages": ["busy"]}, status=503))
        await connector.run_sync()

        assert saved_members(site_db, "grp-dev") == ["ana@acme.com"]
        assert [m.email for m in site_db.app_roles["ENG_10002"]] == ["ana@acme.com"]

    async def test_a_failed_later_page_of_groups_keeps_the_roles(self, api, site_db, checkpoints, search) -> None:
        stub_site(api)
        groups_in_two_pages(api, {"values": [{"groupId": "grp-dev", "name": "devs"}], "isLast": True})
        connector, _ = await ready_connector(site_db, checkpoints)
        await connector.run_sync()
        assert [m.email for m in site_db.app_roles["ENG_10002"]] == ["ana@acme.com"], "Ana is in the role only through devs"

        groups_in_two_pages(api, json_response({"errorMessages": ["busy"]}, status=503))
        await connector.run_sync()

        assert [m.email for m in site_db.app_roles["ENG_10002"]] == ["ana@acme.com"]

    async def test_an_unreadable_group_list_skips_role_sync(self, api, site_db, checkpoints, search) -> None:
        stub_site(api)
        connector, _ = await ready_connector(site_db, checkpoints)
        await connector.run_sync()
        role_reads = len(api.calls("GET", f"{JIRA}/project/ENG/role"))

        api.on("GET", f"{JIRA}/group/bulk", json_response({"errorMessages": ["busy"]}, status=503))
        await connector.run_sync()

        assert [m.email for m in site_db.app_roles["ENG_10002"]] == ["ana@acme.com"]
        assert len(api.calls("GET", f"{JIRA}/project/ENG/role")) == role_reads, "roles are not synced this run"

    async def test_a_group_that_fails_to_process_keeps_the_roles_that_include_it(self, api, site_db, checkpoints, search) -> None:
        stub_site(api)
        connector, _ = await ready_connector(site_db, checkpoints)
        await connector.run_sync()

        api.on("GET", f"{JIRA}/group/bulk", {"values": [{"groupId": "grp-dev", "name": 404}], "isLast": True})
        await connector.run_sync()

        assert [m.email for m in site_db.app_roles["ENG_10002"]] == ["ana@acme.com"]
        assert saved_members(site_db, "grp-dev") == ["ana@acme.com"], "the group is not saved again"

    async def test_a_left_out_addons_group_does_not_hold_back_the_role(self, api, site_db, checkpoints, search) -> None:
        stub_site(api)
        api.on("GET", f"{JIRA}/group/bulk", {"values": [
            {"groupId": "grp-dev", "name": "devs"}, {"groupId": "grp-addons", "name": "atlassian-addons-admin"},
        ], "isLast": True})
        api.on("GET", f"{JIRA}/project/ENG/role/10002", {"name": "Developers", "actors": [
            {"type": "atlassian-group-role-actor", "name": "devs", "groupId": "grp-dev"},
            {"type": "atlassian-group-role-actor", "name": "atlassian-addons-admin", "groupId": "grp-addons"},
        ]})
        connector, _ = await ready_connector(site_db, checkpoints)

        await connector.run_sync()

        assert [m.email for m in site_db.app_roles["ENG_10002"]] == ["ana@acme.com"]

    async def test_a_short_group_page_that_is_not_the_last_is_followed(self, api, site_db, checkpoints, search) -> None:
        stub_site(api)
        api.on("GET", f"{JIRA}/group/bulk", by_start({
            "0": {"values": [{"groupId": "grp-ops", "name": "ops"}], "isLast": False},
            "1": {"values": [{"groupId": "grp-dev", "name": "devs"}], "isLast": True},
        }))
        connector, _ = await ready_connector(site_db, checkpoints)

        await connector.run_sync()

        assert [m.email for m in site_db.app_roles["ENG_10002"]] == ["ana@acme.com"], "devs, on the second page, is read"

    async def test_a_short_member_page_that_is_not_the_last_is_followed(self, api, site_db, checkpoints, search) -> None:
        stub_site(api)
        two_users(api)
        api.on("GET", f"{JIRA}/group/member", by_start({
            "0": {"values": [{"accountId": "acc-ana"}], "isLast": False},
            "1": {"values": [{"accountId": "acc-bo"}], "isLast": True},
        }))
        connector, _ = await ready_connector(site_db, checkpoints)

        await connector.run_sync()

        assert sorted(saved_members(site_db, "grp-dev")) == ["ana@acme.com", "bo@acme.com"]

    async def test_a_failure_after_a_short_page_keeps_the_stored_roles_and_members(self, api, site_db, checkpoints, search) -> None:
        stub_site(api)
        two_users(api)
        api.on("GET", f"{JIRA}/group/member", {"values": [{"accountId": "acc-ana"}, {"accountId": "acc-bo"}], "isLast": True})
        connector, _ = await ready_connector(site_db, checkpoints)
        await connector.run_sync()
        assert sorted(m.email for m in site_db.app_roles["ENG_10002"]) == ["ana@acme.com", "bo@acme.com"]

        busy = json_response({"errorMessages": ["busy"]}, status=503)
        api.on("GET", f"{JIRA}/group/bulk", by_start({
            "0": {"values": [{"groupId": "grp-dev", "name": "devs"}], "isLast": False}, "1": busy,
        }))
        api.on("GET", f"{JIRA}/group/member", by_start({
            "0": {"values": [{"accountId": "acc-ana"}], "isLast": False}, "1": busy,
        }))
        await connector.run_sync()

        assert sorted(m.email for m in site_db.app_roles["ENG_10002"]) == ["ana@acme.com", "bo@acme.com"]
        assert sorted(saved_members(site_db, "grp-dev")) == ["ana@acme.com", "bo@acme.com"]

    async def test_a_group_that_disappears_part_way_through_its_members_ends_up_empty(self, api, site_db, checkpoints, search) -> None:
        stub_site(api)
        first_page = {"values": [{"accountId": "acc-ana"}] * 50, "isLast": False}
        api.on("GET", f"{JIRA}/group/member", lambda r: (
            json_response(first_page) if AtlassianApiStub.query(r).get("startAt") == "0"
            else json_response({"errorMessages": ["no group"]}, status=404)
        ))
        connector, _ = await ready_connector(site_db, checkpoints)

        await connector.run_sync()

        assert saved_members(site_db, "grp-dev") == [], "a deleted group keeps no members"


AUDIT = f"{JIRA}/auditing/record"
FREE_PLAN_REFUSAL = {
    "errorMessages": [
        "Audit logs aren't available for this site as all of its Jira Cloud products are on Free plans."
    ],
    "errors": {},
}
LAST_SYNC_MS = 1_717_000_000_000


@pytest.fixture
def fresh_notification_memory() -> Iterator[Callable[[], None]]:
    """The suppression cache is class-wide; clearing it is what a connector service restart does."""
    BaseConnector._notification_cache.clear()
    yield BaseConnector._notification_cache.clear
    BaseConnector._notification_cache.clear()


class TestDeletedIssuesOnAFreePlan:
    async def test_a_free_plan_refusal_says_deletions_are_found_by_comparison_with_nothing_to_do(
        self, api, db, checkpoints, fresh_notification_memory
    ) -> None:
        api.on("GET", AUDIT, json_response(FREE_PLAN_REFUSAL, status=403))
        connector, _ = await ready_connector(db, checkpoints)

        await connector._handle_issue_deletions(LAST_SYNC_MS)
        await drain_notifications(connector)

        (note,) = connector._notification_service.sent
        assert "Free plan" in note["title"]
        assert "permission" not in note["title"] + note["message"]
        assert "compares the issues in each synced project" in note["message"]
        assert "slower" in note["message"]
        assert "No action is needed." in note["message"]
        assert "remove this connector" not in note["message"]
        assert checkpoints.values_for("issues_audit_deletions") is None, "the deletion window is kept for a later upgrade"

    async def test_the_free_plan_notice_is_sent_once_even_across_syncs_and_restarts(
        self, api, db, checkpoints, fresh_notification_memory
    ) -> None:
        api.on("GET", AUDIT, json_response(FREE_PLAN_REFUSAL, status=403))
        connector, _ = await ready_connector(db, checkpoints)
        await connector._handle_issue_deletions(LAST_SYNC_MS)
        await connector._handle_issue_deletions(LAST_SYNC_MS)
        await drain_notifications(connector)

        fresh_notification_memory()
        restarted, _ = await ready_connector(db, checkpoints)
        await restarted._handle_issue_deletions(LAST_SYNC_MS)
        await drain_notifications(restarted)

        assert len(api.calls("GET", AUDIT)) == 3, "every sync still asks Jira"
        assert len(connector._notification_service.sent) + len(restarted._notification_service.sent) == 1

    async def test_the_marker_is_written_only_once_the_notice_is_sent(
        self, api, db, checkpoints, fresh_notification_memory
    ) -> None:
        api.on("GET", AUDIT, json_response(FREE_PLAN_REFUSAL, status=403))
        connector, _ = await ready_connector(db, checkpoints)

        await connector._handle_issue_deletions(LAST_SYNC_MS)

        assert len(connector._notification_service.sent) == 1
        assert (checkpoints.values_for("issues_audit_free_plan_notice") or {}).get("sent") is True

    async def test_a_notice_the_broker_refused_is_sent_again_later(
        self, api, db, checkpoints, fresh_notification_memory
    ) -> None:
        api.on("GET", AUDIT, json_response(FREE_PLAN_REFUSAL, status=403))
        connector, _ = await ready_connector(db, checkpoints)
        connector._notification_service = RecordingNotifications(broker_answers=[False])

        await connector._handle_issue_deletions(LAST_SYNC_MS)
        await drain_notifications(connector)
        assert checkpoints.values_for("issues_audit_free_plan_notice") is None

        fresh_notification_memory()
        await connector._handle_issue_deletions(LAST_SYNC_MS)
        await drain_notifications(connector)

        assert len(connector._notification_service.refused) == 1
        assert len(connector._notification_service.sent) == 1

    async def test_a_refused_notice_does_not_hold_back_the_next_sync(
        self, api, db, checkpoints, fresh_notification_memory
    ) -> None:
        api.on("GET", AUDIT, json_response(FREE_PLAN_REFUSAL, status=403))
        connector, _ = await ready_connector(db, checkpoints)
        connector._notification_service = RecordingNotifications(broker_answers=[False])

        await connector._handle_issue_deletions(LAST_SYNC_MS)
        await connector._handle_issue_deletions(LAST_SYNC_MS)

        assert len(connector._notification_service.refused) == 1
        assert len(connector._notification_service.sent) == 1, "the backoff only starts once a notice is out"
        assert (checkpoints.values_for("issues_audit_free_plan_notice") or {}).get("sent") is True

    async def test_a_suppressed_notice_is_not_marked_sent(
        self, api, db, checkpoints, fresh_notification_memory
    ) -> None:
        api.on("GET", AUDIT, json_response(FREE_PLAN_REFUSAL, status=403))
        connector, _ = await ready_connector(db, checkpoints)
        await connector._handle_issue_deletions(LAST_SYNC_MS)
        await drain_notifications(connector)

        checkpoints.sync_points.clear()  # a full resync deletes every sync point
        resynced, _ = await ready_connector(db, checkpoints)
        await resynced._handle_issue_deletions(LAST_SYNC_MS)
        await drain_notifications(resynced)
        assert resynced._notification_service.sent == [], "the in-memory backoff still holds it back"
        assert checkpoints.values_for("issues_audit_free_plan_notice") is None

        fresh_notification_memory()
        await resynced._handle_issue_deletions(LAST_SYNC_MS)
        await drain_notifications(resynced)
        assert len(resynced._notification_service.sent) == 1

    async def test_a_real_permission_refusal_still_asks_for_the_permission(
        self, api, db, checkpoints, fresh_notification_memory
    ) -> None:
        api.on("GET", AUDIT, json_response(
            {"errorMessages": ["You do not have permission to view the audit log."], "errors": {}}, status=403,
        ))
        connector, _ = await ready_connector(db, checkpoints)

        await connector._handle_issue_deletions(LAST_SYNC_MS)
        await drain_notifications(connector)

        (note,) = connector._notification_service.sent
        assert note["title"].endswith("is missing the audit log permission")


ID_FIELDS = ["id"]
ATTACHMENT = {"id": "900", "filename": "log.txt", "mimeType": "text/plain", "size": 12, "created": "2024-05-02T15:00:00.000+0000"}


class IssueSearchWithIdListing(IssueSearch):
    """The issue search, plus the id-only listing answered by page token from ``id_pages``."""

    def __init__(self) -> None:
        super().__init__()
        self.id_pages: dict[str | None, Any] = {}
        self.id_bodies: list[dict[str, Any]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if body.get("fields") != ID_FIELDS:
            return super().__call__(request)
        self.id_bodies.append(body)
        answer = self.id_pages.get(body.get("nextPageToken"), {"issues": []})
        return answer if isinstance(answer, httpx.Response) else json_response(answer)


def ids(*nums: int) -> list[dict[str, str]]:
    return [{"id": str(n)} for n in nums]


def created_on(num: int, created: str) -> dict[str, Any]:
    ticket = issue(num, "2024-05-01T10:00:00.000+0000")
    ticket["fields"]["created"] = created
    return ticket


class SiteDbWithIssueKeys(SiteDb):
    async def get_record_by_issue_key(self, connector_id: str, issue_key: str) -> TicketRecord | None:
        """Matches the stores: the ticket whose web URL contains ``/browse/<key>``."""
        for record in self.records.values():
            if isinstance(record, TicketRecord) and f"/browse/{issue_key}" in (record.weburl or ""):
                return record
        return None


@pytest.fixture
def listing(api: AtlassianApiStub) -> IssueSearchWithIdListing:
    handler = IssueSearchWithIdListing()
    api.on("POST", SEARCH, handler)
    return handler


@pytest.fixture
def keyed_db() -> SiteDbWithIssueKeys:
    return SiteDbWithIssueKeys()


async def synced_three_issues(
    api: AtlassianApiStub, db: SiteDb, checkpoints: FakeCheckpointStore, listing: IssueSearchWithIdListing,
) -> JiraConnector:
    """A first sync stores ENG-1, ENG-2 (with an attachment) and ENG-3; later searches find no changes."""
    stub_site(api)
    listing.add("ENG", None, {"issues": [
        issue(1, "2024-05-01T10:00:00.000+0000"),
        issue(2, "2024-05-01T11:00:00.000+0000", attachments=[ATTACHMENT]),
        issue(3, "2024-05-01T12:00:00.000+0000"),
    ]})
    listing.id_pages[None] = {"issues": ids(1, 2, 3), "isLast": True}
    connector, _ = await ready_connector(db, checkpoints)
    await connector.run_sync()
    assert set(tickets(db)) == {"1", "2", "3"} and "attachment_900" in db.records
    listing.add("ENG", None, {"issues": []})
    return connector


def issue_reads(api: AtlassianApiStub, ref: int | str) -> int:
    return len(api.calls("GET", f"{JIRA}/issue/{ref}"))


def gone(api: AtlassianApiStub, *refs: int | str) -> None:
    for ref in refs:
        api.on("GET", f"{JIRA}/issue/{ref}", json_response(
            {"errorMessages": ["Issue does not exist or you do not have permission to see it."]}, status=404,
        ))


ENG_2_DELETED = {
    "records": [{"objectItem": {"typeName": "ISSUE_DELETE", "name": "ENG-2"}, "created": "2024-05-03T10:00:00.000+0000"}],
    "total": 1,
}


class TestDeletedIssuesFoundByComparingIds:
    async def test_an_issue_deleted_in_jira_is_removed_by_the_next_sync(
        self, api, site_db, checkpoints, listing, fresh_notification_memory
    ) -> None:
        api.on("GET", AUDIT, json_response(FREE_PLAN_REFUSAL, status=403))
        connector = await synced_three_issues(api, site_db, checkpoints, listing)
        stub = site_db.records["1"].model_copy(update={"id": "stub-9", "external_record_id": "9", "is_placeholder": True})
        site_db.records["9"] = stub
        listing.id_pages[None] = {"issues": ids(1), "isLast": True}
        gone(api, 2)
        api.on("GET", f"{JIRA}/issue/3", {"id": "3", "key": "OPS-3"})

        await connector.run_sync()

        assert set(tickets(site_db)) == {"1", "3", "9"}
        assert "attachment_900" not in site_db.records, "the attachment goes with its issue"
        assert issue_reads(api, 3) == 1 and "3" in tickets(site_db), "an issue Jira still has (moved, or not yet searchable) stays"
        assert issue_reads(api, 1) == 0 and issue_reads(api, 9) == 0, "listed issues and placeholders are not checked"
        assert len(listing.id_bodies) == 2, "one listing per sync"
        body = listing.id_bodies[-1]
        assert body["jql"] == 'project = "ENG" ORDER BY id ASC'
        assert body["maxResults"] == 5000 and "expand" not in body

    async def test_the_first_sync_after_a_full_resync_still_removes_deleted_issues(
        self, api, site_db, checkpoints, listing, fresh_notification_memory
    ) -> None:
        api.on("GET", AUDIT, json_response(FREE_PLAN_REFUSAL, status=403))
        connector = await synced_three_issues(api, site_db, checkpoints, listing)
        checkpoints.sync_points.clear()  # a full resync deletes every sync point, not the records
        listing.add("ENG", None, {"issues": [issue(1, "2024-05-01T10:00:00.000+0000"), issue(3, "2024-05-01T12:00:00.000+0000")]})
        listing.id_pages[None] = {"issues": ids(1, 3), "isLast": True}
        gone(api, 2)

        await connector.run_sync()

        assert set(tickets(site_db)) == {"1", "3"}
        assert checkpoints.values_for("issues_audit_deletions") is None

    @pytest.mark.parametrize("broken_listing", [
        pytest.param({None: {"issues": ids(1), "nextPageToken": "T2"}, "T2": json_response({"errorMessages": ["boom"]}, status=500)}, id="a-later-page-fails"),
        pytest.param({None: {"issues": ids(1), "nextPageToken": "T2"}, "T2": {"issues": ids(1), "nextPageToken": "T3"}}, id="the-pages-repeat"),
        pytest.param({None: {"issues": ids(1), "isLast": False}}, id="more-pages-but-no-token"),
        pytest.param({None: {"issues": [{"key": "ENG-1"}], "isLast": True}}, id="an-issue-without-an-id"),
        pytest.param({None: json_response({"errorMessages": ["no"]}, status=400)}, id="the-first-page-fails"),
    ])
    async def test_an_unfinished_listing_removes_nothing_and_a_later_full_one_does(
        self, api, site_db, checkpoints, listing, fresh_notification_memory, broken_listing
    ) -> None:
        api.on("GET", AUDIT, json_response(FREE_PLAN_REFUSAL, status=403))
        connector = await synced_three_issues(api, site_db, checkpoints, listing)
        gone(api, 2, 3)
        listing.id_pages = dict(broken_listing)

        await connector.run_sync()

        assert set(tickets(site_db)) == {"1", "2", "3"}
        assert issue_reads(api, 2) == issue_reads(api, 3) == 0

        listing.id_pages = {None: {"issues": ids(1), "nextPageToken": "T2"}, "T2": {"issues": ids(3), "isLast": True}}
        api.on("GET", f"{JIRA}/issue/3", {"id": "3", "key": "ENG-3"})
        await connector.run_sync()

        assert set(tickets(site_db)) == {"1", "3"}

    async def test_a_paid_plan_site_acts_on_the_audit_log_in_the_first_sync_after_a_full_resync(
        self, api, keyed_db, checkpoints, listing, fresh_notification_memory
    ) -> None:
        api.on("GET", AUDIT, {"records": [], "total": 0})
        connector = await synced_three_issues(api, keyed_db, checkpoints, listing)
        api.on("GET", AUDIT, ENG_2_DELETED)
        checkpoints.sync_points.clear()  # a full resync deletes every sync point, not the records
        listing.add("ENG", None, {"issues": [issue(1, "2024-05-01T10:00:00.000+0000"), issue(3, "2024-05-01T12:00:00.000+0000")]})
        gone(api, "ENG-2")

        await connector.run_sync()

        assert set(tickets(keyed_db)) == {"1", "3"}
        assert checkpoints.values_for("issues_audit_deletions"), "the next sync's audit window starts here"

def person(account_id: str, email: Optional[str]) -> dict[str, Any]:
    row: dict[str, Any] = {"accountId": account_id, "accountType": "atlassian", "active": True, "displayName": account_id}
    if email:
        row["emailAddress"] = email
    return row



class TestUnfinishedUserRead:
    """A user list that stops part-way must not shrink who can see what.

    Bo is on the second page of ``/users/search``. He is in the devs group, in the
    Developers role by name, he leads ENG and the ENG scheme names him directly.
    """

    FIRST_PAGE = [person("acc-ana", "ana@acme.com")] + [person(f"acc-{i}", f"u{i}@elsewhere.example") for i in range(49)]

    @staticmethod
    def _stub_bo_everywhere(api: AtlassianApiStub) -> None:
        stub_site(api)
        api.on("GET", f"{JIRA}/group/member", {"values": [{"accountId": "acc-ana"}, {"accountId": "acc-bo"}], "isLast": True})
        api.on("GET", f"{JIRA}/project/search", {"values": [
            {"id": "10000", "key": "ENG", "name": "Engineering", "lead": {"accountId": "acc-bo", "displayName": "Bo"}},
        ], "isLast": True})
        api.on("GET", f"{JIRA}/permissionscheme/1/permission", {"permissions": [
            {"permission": "BROWSE_PROJECTS", "holder": {"type": "group", "parameter": "devs", "value": "grp-dev"}},
            {"permission": "BROWSE_PROJECTS", "holder": {"type": "user", "parameter": "acc-bo"}},
            {"permission": "BROWSE_PROJECTS", "holder": {"type": "projectLead"}},
        ]})
        api.on("GET", f"{JIRA}/project/ENG/role/10002", {"name": "Developers", "actors": [
            {"type": "atlassian-user-role-actor", "actorUser": {"accountId": "acc-bo"}},
        ]})
        api.on("GET", f"{JIRA}/user/search", json_response([]))

    def _second_page(self, api: AtlassianApiStub, answer: Callable[[], httpx.Response]) -> None:
        def users(request: httpx.Request) -> httpx.Response:
            start = AtlassianApiStub.query(request).get("startAt")
            if start == "0":
                return json_response(self.FIRST_PAGE)
            if start == "50":
                return answer()
            return json_response([])

        api.on("GET", f"{JIRA}/users/search", users)

    @staticmethod
    def _access(db: SiteDb) -> dict[str, Any]:
        return {
            "devs": saved_members(db, "grp-dev"),
            "developers": sorted(m.email for m in db.app_roles["ENG_10002"]),
            "lead": [m.email for m in db.app_roles["ENG_projectLead"]],
            "project": acl(db),
        }

    @pytest.mark.parametrize(
        "answer",
        [
            pytest.param(lambda: json_response({"errorMessages": ["expired"]}, status=401), id="401"),
            pytest.param(lambda: json_response({"errorMessages": ["denied"]}, status=403), id="403"),
            pytest.param(lambda: httpx.Response(200, content=b"<html>proxy error</html>"), id="unparseable"),
            pytest.param(lambda: json_response({"unexpected": True}), id="not-a-page"),
        ],
    )
    async def test_a_user_page_failing_part_way_leaves_groups_roles_and_grants_unchanged(
        self, api, site_db, checkpoints, search, answer
    ) -> None:
        self._stub_bo_everywhere(api)
        self._second_page(api, lambda: json_response([person("acc-bo", "bo@acme.com")]))
        search.add("ENG", None, {"issues": [issue(1, "2024-05-01T10:00:00.000+0000")]})
        connector, _ = await ready_connector(site_db, checkpoints)
        await connector.run_sync()
        before = self._access(site_db)
        assert before["devs"] == ["ana@acme.com", "bo@acme.com"]
        assert before["developers"] == ["bo@acme.com"]
        assert before["lead"] == ["bo@acme.com"]
        assert ("EntityType.USER", "bo@acme.com") in before["project"]

        self._second_page(api, answer)
        groups_saved = len(site_db.user_groups)
        await connector.run_sync()

        assert self._access(site_db) == before
        assert len(site_db.user_groups) == groups_saved, "no group is saved again"
        assert "1" in tickets(site_db), "issues are still synced"

    @pytest.mark.parametrize("status", [503, 408])
    async def test_a_failed_reverse_lookup_leaves_the_group_unchanged(self, api, site_db, checkpoints, search, status) -> None:
        self._stub_bo_everywhere(api)
        site_db.active_users.append(type("U", (), {"email": "bo@acme.com"})())
        # Bo's email is hidden, so only the lookup by his PipesHub email finds him.
        self._second_page(api, lambda: json_response([person("acc-bo", None)]))
        api.on("GET", f"{JIRA}/user/search", lambda r: json_response(
            [person("acc-bo", None)] if AtlassianApiStub.query(r).get("query") == "bo@acme.com" else []
        ))
        connector, _ = await ready_connector(site_db, checkpoints)
        await connector.run_sync()
        assert saved_members(site_db, "grp-dev") == ["ana@acme.com", "bo@acme.com"]

        site_db.app_users.clear()  # nothing cached from the last sync, so the lookup is needed again
        api.on("GET", f"{JIRA}/user/search", json_response({"errorMessages": ["busy"]}, status=status))
        await connector.run_sync()

        assert saved_members(site_db, "grp-dev") == ["ana@acme.com", "bo@acme.com"]

    async def test_a_complete_read_that_drops_a_user_removes_them(self, api, site_db, checkpoints, search) -> None:
        self._stub_bo_everywhere(api)
        self._second_page(api, lambda: json_response([person("acc-bo", "bo@acme.com")]))
        connector, _ = await ready_connector(site_db, checkpoints)
        await connector.run_sync()
        assert "bo@acme.com" in self._access(site_db)["devs"]

        # Bo leaves Jira: the user list no longer has him, though older answers still name him.
        self._second_page(api, lambda: json_response([]))
        await connector.run_sync()

        after = self._access(site_db)
        assert after["devs"] == ["ana@acme.com"]
        assert after["developers"] == []
        assert after["lead"] == []
        assert ("EntityType.USER", "bo@acme.com") not in after["project"]

    async def test_a_user_list_refused_from_the_first_page_fails_closed(self, api, site_db, checkpoints, search) -> None:
        self._stub_bo_everywhere(api)
        self._second_page(api, lambda: json_response([person("acc-bo", "bo@acme.com")]))
        connector, _ = await ready_connector(site_db, checkpoints)
        await connector.run_sync()
        assert saved_members(site_db, "grp-dev") == ["ana@acme.com", "bo@acme.com"]

        # The account cannot list users at all, so no one is kept on stale access.
        api.on("GET", f"{JIRA}/users/search", json_response({"errorMessages": ["denied"]}, status=403))
        await connector.run_sync()

        assert saved_members(site_db, "grp-dev") == []

    async def test_a_first_sync_with_a_short_user_read_saves_what_was_read(self, api, site_db, checkpoints, search) -> None:
        self._stub_bo_everywhere(api)
        self._second_page(api, lambda: json_response({"errorMessages": ["expired"]}, status=401))
        search.add("ENG", None, {"issues": [issue(1, "2024-05-01T10:00:00.000+0000")]})
        connector, _ = await ready_connector(site_db, checkpoints)

        await connector.run_sync()

        # Nothing was stored before, so holding back would leave everyone without access.
        access = self._access(site_db)
        assert access["devs"] == ["ana@acme.com"]
        assert access["developers"] == []
        assert access["lead"] == []
        assert ("EntityType.GROUP", "grp-dev") in access["project"], "the grants that were read are saved"

    async def test_a_full_sync_whose_prep_deleted_the_grants_saves_what_was_read(self, api, site_db, checkpoints, search) -> None:
        self._stub_bo_everywhere(api)
        self._second_page(api, lambda: json_response([person("acc-bo", "bo@acme.com")]))
        search.add("ENG", None, {"issues": [issue(1, "2024-05-01T10:00:00.000+0000")]})
        connector, _ = await ready_connector(site_db, checkpoints)
        await connector.run_sync()
        assert "bo@acme.com" in self._access(site_db)["devs"]

        # A full sync's prep deletes the sync points and every stored grant before the connector runs.
        checkpoints.sync_points.clear()
        site_db.user_groups.clear()
        site_db.app_roles.clear()
        site_db.record_group_permissions.clear()
        self._second_page(api, lambda: json_response({"errorMessages": ["expired"]}, status=401))
        await connector.run_sync()

        access = self._access(site_db)
        assert access["devs"] == ["ana@acme.com"]
        assert ("EntityType.GROUP", "grp-dev") in access["project"]

    async def test_a_failed_lookup_after_the_user_list_is_refused_outright_still_fails_closed(
        self, api, site_db, checkpoints, search
    ) -> None:
        self._stub_bo_everywhere(api)
        self._second_page(api, lambda: json_response([person("acc-bo", "bo@acme.com")]))
        search.add("ENG", None, {"issues": [issue(1, "2024-05-01T10:00:00.000+0000")]})
        connector, _ = await ready_connector(site_db, checkpoints)
        await connector.run_sync()
        assert saved_members(site_db, "grp-dev") == ["ana@acme.com", "bo@acme.com"]

        api.on("GET", f"{JIRA}/users/search", json_response({"errorMessages": ["denied"]}, status=403))
        api.on("GET", f"{JIRA}/user/search", json_response({"errorMessages": ["busy"]}, status=503))
        await connector.run_sync()

        assert saved_members(site_db, "grp-dev") == [], "a refused user list is not kept open by a lookup that failed"

    async def test_a_first_sync_cut_short_after_saving_groups_keeps_them_on_a_later_short_read(
        self, api, site_db, checkpoints, search
    ) -> None:
        self._stub_bo_everywhere(api)
        self._second_page(api, lambda: json_response([person("acc-bo", "bo@acme.com")]))
        api.on("GET", f"{JIRA}/project/search", json_response({"errorMessages": ["boom"]}, status=500))
        connector, _ = await ready_connector(site_db, checkpoints)
        with pytest.raises(Exception):  # noqa: B017 - any failure that stops the sync after the groups are saved
            await connector.run_sync()
        assert saved_members(site_db, "grp-dev") == ["ana@acme.com", "bo@acme.com"]
        assert checkpoints.values_for("issues_global") is None, "the sync stopped before its checkpoint"

        self._stub_bo_everywhere(api)
        search.add("ENG", None, {"issues": [issue(1, "2024-05-01T10:00:00.000+0000")]})
        groups_saved = len(site_db.user_groups)
        self._second_page(api, lambda: json_response({"errorMessages": ["expired"]}, status=401))
        await connector.run_sync()

        assert len(site_db.user_groups) == groups_saved, "groups the first sync saved are kept"
        assert ("EntityType.GROUP", "grp-dev") in acl(site_db), "a project it never saved is saved with what was read"

    async def test_a_group_list_that_fails_on_the_first_sync_is_saved_from_a_later_short_read(
        self, api, site_db, checkpoints, search
    ) -> None:
        self._stub_bo_everywhere(api)
        self._second_page(api, lambda: json_response([person("acc-bo", "bo@acme.com")]))
        api.on("GET", f"{JIRA}/group/bulk", json_response({"errorMessages": ["busy"]}, status=503))
        connector, _ = await ready_connector(site_db, checkpoints)
        await connector.run_sync()
        assert site_db.user_groups == [], "the group list could not be read, so no group is saved"

        api.on("GET", f"{JIRA}/group/bulk", {"values": [{"groupId": "grp-dev", "name": "devs"}], "isLast": True})
        self._second_page(api, lambda: json_response({"errorMessages": ["expired"]}, status=401))
        await connector.run_sync()

        assert saved_members(site_db, "grp-dev") == ["ana@acme.com"], "nothing was stored, so what was read is saved"

    async def test_a_project_whose_access_was_never_saved_is_saved_from_a_short_read(
        self, api, site_db, checkpoints, search
    ) -> None:
        self._stub_bo_everywhere(api)
        api.on("GET", f"{JIRA}/project/search", {"values": [
            {"id": "10000", "key": "ENG", "name": "Engineering", "lead": {"accountId": "acc-bo", "displayName": "Bo"}},
            {"id": "20000", "key": "OPS", "name": "Operations"},
        ], "isLast": True})
        api.on("GET", f"{JIRA}/project/OPS/role", {})
        api.on("GET", f"{JIRA}/project/OPS/permissionscheme", json_response({"errorMessages": ["oops"]}, status=500))
        self._second_page(api, lambda: json_response([person("acc-bo", "bo@acme.com")]))
        connector, _ = await ready_connector(site_db, checkpoints)
        await connector.run_sync()
        eng_before = acl(site_db)
        assert "20000" not in site_db.record_group_permissions, "OPS's scheme could not be read, so nothing is saved"

        api.on("GET", f"{JIRA}/project/OPS/permissionscheme", {"id": 2})
        api.on("GET", f"{JIRA}/permissionscheme/2/permission", {"permissions": [
            {"permission": "BROWSE_PROJECTS", "holder": {"type": "group", "parameter": "devs", "value": "grp-dev"}},
            {"permission": "BROWSE_PROJECTS", "holder": {"type": "user", "parameter": "acc-bo"}},
        ]})
        self._second_page(api, lambda: json_response({"errorMessages": ["expired"]}, status=401))
        await connector.run_sync()

        assert acl(site_db) == eng_before, "ENG's saved access is kept"
        ops = sorted((str(p.entity_type), p.external_id or p.email) for p in site_db.record_group_permissions["20000"])
        assert ops == [("EntityType.GROUP", "grp-dev")], "OPS had nothing stored, so the grants that were read are saved"

    async def test_a_renamed_project_keeps_its_saved_access_on_a_short_read(self, api, site_db, checkpoints, search) -> None:
        self._stub_bo_everywhere(api)
        self._second_page(api, lambda: json_response([person("acc-bo", "bo@acme.com")]))
        connector, _ = await ready_connector(site_db, checkpoints)
        await connector.run_sync()
        before = acl(site_db)
        assert ("EntityType.USER", "bo@acme.com") in before

        # The same project (id 10000) now has the key NEW.
        api.on("GET", f"{JIRA}/project/search", {"values": [{"id": "10000", "key": "NEW", "name": "Engineering"}], "isLast": True})
        api.on("GET", f"{JIRA}/project/NEW/permissionscheme", {"id": 1})
        api.on("GET", f"{JIRA}/project/NEW/role", {})
        self._second_page(api, lambda: json_response({"errorMessages": ["expired"]}, status=401))
        await connector.run_sync()

        assert acl(site_db) == before, "the project is known by its id, so its saved access is kept"

    @pytest.mark.parametrize(
        ("holder", "grant"),
        [
            pytest.param({"type": "projectRole", "parameter": "10002"}, "ENG_10002", id="role"),
            pytest.param({"type": "projectLead"}, "ENG_projectLead", id="lead"),
        ],
    )
    async def test_a_renamed_project_granted_through_a_role_or_its_lead_keeps_its_access_on_a_short_read(
        self, api, site_db, checkpoints, search, holder, grant
    ) -> None:
        self._stub_bo_everywhere(api)
        self._second_page(api, lambda: json_response([person("acc-bo", "bo@acme.com")]))
        api.on("GET", f"{JIRA}/permissionscheme/1/permission", {"permissions": [
            {"permission": "BROWSE_PROJECTS", "holder": holder},
        ]})
        connector, _ = await ready_connector(site_db, checkpoints)
        await connector.run_sync()
        assert [p.external_id for p in site_db.record_group_permissions["10000"]] == [grant]

        # The same project (id 10000) now has the key NEW; its roles under NEW are not synced on a short read.
        api.on("GET", f"{JIRA}/project/search", {"values": [
            {"id": "10000", "key": "NEW", "name": "Engineering", "lead": {"accountId": "acc-ana", "displayName": "Ana"}},
        ], "isLast": True})
        api.on("GET", f"{JIRA}/project/NEW/permissionscheme", {"id": 1})
        api.on("GET", f"{JIRA}/project/NEW/role", {})
        self._second_page(api, lambda: json_response({"errorMessages": ["expired"]}, status=401))
        await connector.run_sync()

        assert [p.external_id for p in site_db.record_group_permissions["10000"]] == [grant], (
            "the access list saved under the old key, whose roles have members, is kept"
        )

    async def test_a_renamed_project_granted_through_groups_a_role_and_its_lead_keeps_its_access_on_a_short_read(
        self, api, site_db, checkpoints, search
    ) -> None:
        self._stub_bo_everywhere(api)
        self._second_page(api, lambda: json_response([person("acc-bo", "bo@acme.com")]))
        api.on("GET", f"{JIRA}/permissionscheme/1/permission", {"permissions": [
            {"permission": "BROWSE_PROJECTS", "holder": {"type": "group", "parameter": "devs", "value": "grp-dev"}},
            {"permission": "BROWSE_PROJECTS", "holder": {"type": "projectRole", "parameter": "10002"}},
            {"permission": "BROWSE_PROJECTS", "holder": {"type": "projectLead"}},
        ]})
        connector, _ = await ready_connector(site_db, checkpoints)
        await connector.run_sync()
        before = acl(site_db)
        assert before == [("EntityType.GROUP", "grp-dev"), ("EntityType.ROLE", "ENG_10002"), ("EntityType.ROLE", "ENG_projectLead")]
        assert [m.email for m in site_db.app_roles["ENG_projectLead"]] == ["bo@acme.com"]

        # The same project (id 10000) now has the key NEW; Bo, its lead, is on the page that fails.
        api.on("GET", f"{JIRA}/project/search", {"values": [
            {"id": "10000", "key": "NEW", "name": "Engineering", "lead": {"accountId": "acc-bo", "displayName": "Bo"}},
        ], "isLast": True})
        api.on("GET", f"{JIRA}/project/NEW/permissionscheme", {"id": 1})
        api.on("GET", f"{JIRA}/project/NEW/role", {"Developers": f"{SITE}{JIRA}/project/NEW/role/10002"})
        self._second_page(api, lambda: json_response({"errorMessages": ["expired"]}, status=401))
        await connector.run_sync()

        assert acl(site_db) == before, "the Developers and project-lead grants under the old key stay"
        assert [m.email for m in site_db.app_roles["ENG_projectLead"]] == ["bo@acme.com"]
        assert "NEW_projectLead" not in site_db.app_roles, "no empty lead is saved for a project whose access is stored"

    async def test_a_full_sync_after_its_prep_kept_the_grants_keeps_them_on_a_short_read(
        self, api, site_db, checkpoints, search
    ) -> None:
        self._stub_bo_everywhere(api)
        self._second_page(api, lambda: json_response([person("acc-bo", "bo@acme.com")]))
        search.add("ENG", None, {"issues": [issue(1, "2024-05-01T10:00:00.000+0000")]})
        connector, _ = await ready_connector(site_db, checkpoints)
        await connector.run_sync()
        before = self._access(site_db)

        # A full sync's prep deletes the sync points but leaves the stored grants in place (#4119).
        checkpoints.sync_points.clear()
        self._second_page(api, lambda: json_response({"errorMessages": ["expired"]}, status=401))
        await connector.run_sync()

        assert self._access(site_db) == before, "stored members, roles, lead and project access are kept"

    async def test_a_stored_access_read_that_fails_stops_the_sync_before_replacing_anything(
        self, api, site_db, checkpoints, search
    ) -> None:
        self._stub_bo_everywhere(api)
        self._second_page(api, lambda: json_response([person("acc-bo", "bo@acme.com")]))
        connector, _ = await ready_connector(site_db, checkpoints)
        await connector.run_sync()
        before = self._access(site_db)
        groups_saved = len(site_db.user_groups)

        site_db.fail_stored_permission_reads = True
        self._second_page(api, lambda: json_response({"errorMessages": ["expired"]}, status=401))
        with pytest.raises(StoredAccessReadError):
            await connector.run_sync()

        assert self._access(site_db) == before, "a failed read is not taken for 'nothing stored'"
        assert len(site_db.user_groups) == groups_saved

    async def test_a_full_sync_with_a_short_read_keeps_what_it_held_after_the_unlink(
        self, api, site_db, checkpoints, search
    ) -> None:
        self._stub_bo_everywhere(api)
        self._second_page(api, lambda: json_response([person("acc-bo", "bo@acme.com")]))
        search.add("ENG", None, {"issues": [issue(1, "2024-05-01T10:00:00.000+0000")]})
        connector, _ = await ready_connector(site_db, checkpoints)
        await connector.run_sync()
        before = self._access(site_db)
        assert "bo@acme.com" in before["devs"] and before["lead"] == ["bo@acme.com"]

        # A full sync (#4119): sync points go, stored edges stay, and after the sync returns
        # whatever it did not write is unlinked, unless it reports an incomplete read.
        checkpoints.sync_points.clear()
        self._second_page(api, lambda: json_response({"errorMessages": ["expired"]}, status=401))
        await run_full_sync(connector, site_db)

        assert self._access(site_db) == before, "devs, Developers, the lead and the project keep what they had"

    async def test_a_complete_full_sync_still_unlinks_what_it_did_not_see(self, api, site_db, checkpoints, search) -> None:
        self._stub_bo_everywhere(api)
        self._second_page(api, lambda: json_response([person("acc-bo", "bo@acme.com")]))
        search.add("ENG", None, {"issues": [issue(1, "2024-05-01T10:00:00.000+0000")]})
        connector, _ = await ready_connector(site_db, checkpoints)
        await connector.run_sync()

        graph = await run_full_sync(connector, site_db)

        graph.unlink_nodes_missed_by_full_sync.assert_awaited_once()
        assert connector.full_sync_coverage().complete is True

    async def test_a_full_sync_with_an_unreadable_scheme_keeps_the_project_access_after_the_unlink(
        self, api, site_db, checkpoints, search
    ) -> None:
        self._stub_bo_everywhere(api)  # ENG has no issues, so nothing but the kept project marks it seen
        self._second_page(api, lambda: json_response([person("acc-bo", "bo@acme.com")]))
        connector, _ = await ready_connector(site_db, checkpoints)
        await connector.run_sync()
        before = acl(site_db)

        checkpoints.sync_points.clear()
        api.on("GET", f"{JIRA}/project/ENG/permissionscheme", json_response({"errorMessages": ["oops"]}, status=500))
        await run_full_sync(connector, site_db)

        assert acl(site_db) == before, "the stored grants survive the unlink"

    async def test_a_full_sync_with_a_short_read_still_unlinks_what_is_gone(self, api, site_db, checkpoints, search) -> None:
        self._stub_bo_everywhere(api)
        self._second_page(api, lambda: json_response([person("acc-bo", "bo@acme.com")]))
        search.add("ENG", None, {"issues": [issue(1, "2024-05-01T10:00:00.000+0000")]})
        connector, _ = await ready_connector(site_db, checkpoints)
        await connector.run_sync()

        checkpoints.sync_points.clear()
        self._second_page(api, lambda: json_response({"errorMessages": ["expired"]}, status=401))
        graph = await run_full_sync(connector, site_db)

        # What this sync held is noted as seen, so the unlink still runs for what Jira no longer has.
        assert connector.full_sync_coverage().complete is True
        graph.unlink_nodes_missed_by_full_sync.assert_awaited_once()

    async def test_a_renamed_project_keeps_its_old_key_roles_through_a_full_sync_unlink(
        self, api, site_db, checkpoints, search
    ) -> None:
        self._stub_bo_everywhere(api)
        self._second_page(api, lambda: json_response([person("acc-bo", "bo@acme.com")]))
        api.on("GET", f"{JIRA}/permissionscheme/1/permission", {"permissions": [
            {"permission": "BROWSE_PROJECTS", "holder": {"type": "group", "parameter": "devs", "value": "grp-dev"}},
            {"permission": "BROWSE_PROJECTS", "holder": {"type": "projectRole", "parameter": "10002"}},
            {"permission": "BROWSE_PROJECTS", "holder": {"type": "projectLead"}},
        ]})
        connector, _ = await ready_connector(site_db, checkpoints)
        await connector.run_sync()
        before = acl(site_db)
        assert before == [("EntityType.GROUP", "grp-dev"), ("EntityType.ROLE", "ENG_10002"), ("EntityType.ROLE", "ENG_projectLead")]

        # The same project (id 10000) now has the key NEW; a full sync (#4119) with a short read.
        api.on("GET", f"{JIRA}/project/search", {"values": [
            {"id": "10000", "key": "NEW", "name": "Engineering", "lead": {"accountId": "acc-bo", "displayName": "Bo"}},
        ], "isLast": True})
        api.on("GET", f"{JIRA}/project/NEW/permissionscheme", {"id": 1})
        api.on("GET", f"{JIRA}/project/NEW/role", {"Developers": f"{SITE}{JIRA}/project/NEW/role/10002"})
        checkpoints.sync_points.clear()
        self._second_page(api, lambda: json_response({"errorMessages": ["expired"]}, status=401))
        await run_full_sync(connector, site_db)

        assert acl(site_db) == before, "the project still grants the old-key roles"
        assert [m.email for m in site_db.app_roles["ENG_10002"]] == ["bo@acme.com"], "the old-key role keeps its members"
        assert [m.email for m in site_db.app_roles["ENG_projectLead"]] == ["bo@acme.com"]

    async def test_a_full_sync_whose_group_list_fails_keeps_the_stored_members(
        self, api, site_db, checkpoints, search
    ) -> None:
        self._stub_bo_everywhere(api)
        self._second_page(api, lambda: json_response([person("acc-bo", "bo@acme.com")]))
        search.add("ENG", None, {"issues": [issue(1, "2024-05-01T10:00:00.000+0000")]})
        connector, _ = await ready_connector(site_db, checkpoints)
        await connector.run_sync()
        before = self._access(site_db)

        # A full sync (#4119) with a short user read whose group list then fails: the groups and the
        # roles that need them are kept unread, so they look no different from ones Jira removed.
        checkpoints.sync_points.clear()
        self._second_page(api, lambda: json_response({"errorMessages": ["expired"]}, status=401))
        api.on("GET", f"{JIRA}/group/bulk", json_response({"errorMessages": ["busy"]}, status=503))
        graph = await run_full_sync(connector, site_db)

        assert connector.full_sync_coverage().complete is False
        graph.unlink_nodes_missed_by_full_sync.assert_not_awaited()
        assert self._access(site_db) == before, "the stored group members and role members survive"

    async def test_a_full_sync_whose_role_list_is_refused_keeps_the_roles_and_still_unlinks(
        self, api, site_db, checkpoints, search
    ) -> None:
        self._stub_bo_everywhere(api)
        self._second_page(api, lambda: json_response([person("acc-bo", "bo@acme.com")]))
        api.on("GET", f"{JIRA}/permissionscheme/1/permission", {"permissions": [
            {"permission": "BROWSE_PROJECTS", "holder": {"type": "projectRole", "parameter": "10002"}},
        ]})
        search.add("ENG", None, {"issues": [issue(1, "2024-05-01T10:00:00.000+0000")]})
        connector, _ = await ready_connector(site_db, checkpoints)
        await connector.run_sync()
        assert [m.email for m in site_db.app_roles["ENG_10002"]] == ["bo@acme.com"]

        # A lasting refusal (403, no Administer Projects) repeats on every full sync.
        checkpoints.sync_points.clear()
        api.on("GET", f"{JIRA}/project/ENG/role", json_response({"errorMessages": ["denied"]}, status=403))
        graph = await run_full_sync(connector, site_db)

        assert connector.full_sync_coverage().complete is True, "a refusal does not make every full sync incomplete"
        graph.unlink_nodes_missed_by_full_sync.assert_awaited_once()
        assert [m.email for m in site_db.app_roles["ENG_10002"]] == ["bo@acme.com"], "the kept role keeps its members"
        assert ("EntityType.ROLE", "ENG_10002") in acl(site_db)

    async def test_a_full_sync_with_one_unreadable_role_keeps_it_and_reports_incomplete(
        self, api, site_db, checkpoints, search
    ) -> None:
        self._stub_bo_everywhere(api)
        self._second_page(api, lambda: json_response([person("acc-bo", "bo@acme.com")]))
        api.on("GET", f"{JIRA}/project/ENG/role", {
            "Developers": f"{SITE}{JIRA}/project/ENG/role/10002",
            "Testers": f"{SITE}{JIRA}/project/ENG/role/10003",
        })
        api.on("GET", f"{JIRA}/project/ENG/role/10003", {"name": "Testers", "actors": [
            {"type": "atlassian-user-role-actor", "actorUser": {"accountId": "acc-ana"}},
        ]})
        search.add("ENG", None, {"issues": [issue(1, "2024-05-01T10:00:00.000+0000")]})
        connector, _ = await ready_connector(site_db, checkpoints)
        await connector.run_sync()
        assert [m.email for m in site_db.app_roles["ENG_10003"]] == ["ana@acme.com"]

        checkpoints.sync_points.clear()
        api.on("GET", f"{JIRA}/project/ENG/role/10003", json_response({"errorMessages": ["boom"]}, status=500))
        graph = await run_full_sync(connector, site_db)

        assert connector.full_sync_coverage().complete is False
        graph.unlink_nodes_missed_by_full_sync.assert_not_awaited()
        assert [m.email for m in site_db.app_roles["ENG_10003"]] == ["ana@acme.com"], "the unread role keeps its members"
        assert [m.email for m in site_db.app_roles["ENG_10002"]] == ["bo@acme.com"], "the other role is read as usual"

    async def test_a_full_sync_whose_group_members_are_refused_keeps_the_group_and_still_unlinks(
        self, api, site_db, checkpoints, search
    ) -> None:
        self._stub_bo_everywhere(api)
        self._second_page(api, lambda: json_response([person("acc-bo", "bo@acme.com")]))
        search.add("ENG", None, {"issues": [issue(1, "2024-05-01T10:00:00.000+0000")]})
        connector, _ = await ready_connector(site_db, checkpoints)
        await connector.run_sync()
        before = saved_members(site_db, "grp-dev")

        checkpoints.sync_points.clear()
        api.on("GET", f"{JIRA}/group/member", json_response({"errorMessages": ["denied"]}, status=403))
        graph = await run_full_sync(connector, site_db)

        assert connector.full_sync_coverage().complete is True
        graph.unlink_nodes_missed_by_full_sync.assert_awaited_once()
        assert saved_members(site_db, "grp-dev") == before, "the kept group keeps its members"

    async def test_a_full_sync_whose_group_list_is_refused_keeps_what_projects_grant_and_still_unlinks(
        self, api, site_db, checkpoints, search
    ) -> None:
        self._stub_bo_everywhere(api)
        self._second_page(api, lambda: json_response([person("acc-bo", "bo@acme.com")]))
        search.add("ENG", None, {"issues": [issue(1, "2024-05-01T10:00:00.000+0000")]})
        connector, _ = await ready_connector(site_db, checkpoints)
        await connector.run_sync()
        before = self._access(site_db)

        # A refusal repeats on every full sync; the groups are unknown, so what the projects grant is kept.
        checkpoints.sync_points.clear()
        api.on("GET", f"{JIRA}/group/bulk", json_response({"errorMessages": ["denied"]}, status=403))
        graph = await run_full_sync(connector, site_db)

        assert connector.full_sync_coverage().complete is True
        graph.unlink_nodes_missed_by_full_sync.assert_awaited_once()
        assert self._access(site_db)["devs"] == before["devs"], "the group the project grants keeps its members"
        assert self._access(site_db)["project"] == before["project"]

    @pytest.mark.parametrize(
        "failing", [None, "group listing", "role list", "role list refused", "role list not a mapping", "role with no URL"]
    )
    async def test_a_refused_group_list_keeps_stored_groups_and_roles_no_grant_names(
        self, api, site_db, checkpoints, search, failing
    ) -> None:
        self._stub_bo_everywhere(api)
        self._second_page(api, lambda: json_response([person("acc-bo", "bo@acme.com")]))
        api.on("GET", f"{JIRA}/group/bulk", {"values": [
            {"groupId": "grp-dev", "name": "devs"}, {"groupId": "grp-qa", "name": "qa"},
        ], "isLast": True})
        search.add("ENG", None, {"issues": [issue(1, "2024-05-01T10:00:00.000+0000")]})
        connector, _ = await ready_connector(site_db, checkpoints)
        await connector.run_sync()
        granted = [p.external_id for p in site_db.record_group_permissions["10000"]]
        assert "grp-qa" not in granted and "ENG_10002" not in granted, "no grant names qa or Developers"
        before = self._access(site_db)
        qa = saved_members(site_db, "grp-qa")
        assert qa and before["developers"]

        checkpoints.sync_points.clear()
        site_db.fail_group_listing = failing == "group listing"
        if failing == "role list":
            api.on("GET", f"{JIRA}/project/ENG/role", json_response({"errorMessages": ["busy"]}, status=503))
        elif failing == "role list refused":
            api.on("GET", f"{JIRA}/project/ENG/role", json_response({"errorMessages": ["denied"]}, status=403))
        elif failing == "role list not a mapping":
            api.on("GET", f"{JIRA}/project/ENG/role", json_response([]))
        elif failing == "role with no URL":
            api.on("GET", f"{JIRA}/project/ENG/role", {"Developers": f"{SITE}{JIRA}/project/ENG/role/10002", "Testers": None})
        api.on("GET", f"{JIRA}/group/bulk", json_response({"errorMessages": ["denied"]}, status=403))
        graph = await run_full_sync(connector, site_db)

        after = self._access(site_db)
        if failing in ("group listing", "role list", "role list not a mapping", "role with no URL"):
            # What could not be listed cannot be kept by name, so nothing is unlinked.
            assert connector.full_sync_coverage().complete is False
            graph.unlink_nodes_missed_by_full_sync.assert_not_awaited()
        else:
            # A refused role list is lasting; a role no grant names gives no one access.
            assert connector.full_sync_coverage().complete is True
            graph.unlink_nodes_missed_by_full_sync.assert_awaited_once()
        assert saved_members(site_db, "grp-qa") == qa, "the stored group keeps its members"
        if failing != "role list refused":
            assert after["developers"] == before["developers"], "so does the stored role"
        assert after["devs"] == before["devs"]
        assert after["project"] == before["project"]

    async def test_a_refused_group_list_keeps_a_stored_group_this_sync_grants_for_the_first_time(
        self, api, site_db, checkpoints, search
    ) -> None:
        self._stub_bo_everywhere(api)
        self._second_page(api, lambda: json_response([person("acc-bo", "bo@acme.com")]))
        api.on("GET", f"{JIRA}/group/bulk", {"values": [
            {"groupId": "grp-dev", "name": "devs"}, {"groupId": "grp-qa", "name": "qa"},
        ], "isLast": True})
        search.add("ENG", None, {"issues": [issue(1, "2024-05-01T10:00:00.000+0000")]})
        connector, _ = await ready_connector(site_db, checkpoints)
        await connector.run_sync()
        assert "grp-qa" not in [p.external_id for p in site_db.record_group_permissions["10000"]]
        qa = saved_members(site_db, "grp-qa")
        assert qa

        # The scheme now grants qa, and the group list is refused.
        checkpoints.sync_points.clear()
        api.on("GET", f"{JIRA}/permissionscheme/1/permission", {"permissions": [
            {"permission": "BROWSE_PROJECTS", "holder": {"type": "group", "parameter": "devs", "value": "grp-dev"}},
            {"permission": "BROWSE_PROJECTS", "holder": {"type": "group", "parameter": "qa", "value": "grp-qa"}},
            {"permission": "BROWSE_PROJECTS", "holder": {"type": "user", "parameter": "acc-bo"}},
            {"permission": "BROWSE_PROJECTS", "holder": {"type": "projectLead"}},
        ]})
        api.on("GET", f"{JIRA}/group/bulk", json_response({"errorMessages": ["denied"]}, status=403))
        graph = await run_full_sync(connector, site_db)

        assert connector.full_sync_coverage().complete is True
        graph.unlink_nodes_missed_by_full_sync.assert_awaited_once()
        assert "grp-qa" in [p.external_id for p in site_db.record_group_permissions["10000"]], "the new grant stays"
        assert saved_members(site_db, "grp-qa") == qa, "and so do the group's members"

    @pytest.mark.parametrize("refused", ["role list", "group list and role list"])
    async def test_a_role_the_scheme_newly_grants_keeps_its_grant_and_members_when_roles_are_not_synced(
        self, api, site_db, checkpoints, search, refused
    ) -> None:
        self._stub_bo_everywhere(api)
        self._second_page(api, lambda: json_response([person("acc-bo", "bo@acme.com")]))
        search.add("ENG", None, {"issues": [issue(1, "2024-05-01T10:00:00.000+0000")]})
        connector, _ = await ready_connector(site_db, checkpoints)
        await connector.run_sync()
        assert "ENG_10002" not in [p.external_id for p in site_db.record_group_permissions["10000"]]
        developers = self._access(site_db)["developers"]
        assert developers

        # The scheme now grants Developers, and its role list is refused, so roles are not synced.
        checkpoints.sync_points.clear()
        api.on("GET", f"{JIRA}/permissionscheme/1/permission", {"permissions": [
            {"permission": "BROWSE_PROJECTS", "holder": {"type": "group", "parameter": "devs", "value": "grp-dev"}},
            {"permission": "BROWSE_PROJECTS", "holder": {"type": "projectRole", "parameter": "10002"}},
            {"permission": "BROWSE_PROJECTS", "holder": {"type": "user", "parameter": "acc-bo"}},
            {"permission": "BROWSE_PROJECTS", "holder": {"type": "projectLead"}},
        ]})
        api.on("GET", f"{JIRA}/project/ENG/role", json_response({"errorMessages": ["denied"]}, status=403))
        if refused == "group list and role list":
            api.on("GET", f"{JIRA}/group/bulk", json_response({"errorMessages": ["denied"]}, status=403))
        graph = await run_full_sync(connector, site_db)

        assert connector.full_sync_coverage().complete is True
        graph.unlink_nodes_missed_by_full_sync.assert_awaited_once()
        assert "ENG_10002" in [p.external_id for p in site_db.record_group_permissions["10000"]], "the new grant stays"
        assert self._access(site_db)["developers"] == developers, "and so do the role's members"

    async def test_a_role_list_that_is_not_a_mapping_is_an_incomplete_read(
        self, api, site_db, checkpoints, search
    ) -> None:
        self._stub_bo_everywhere(api)
        self._second_page(api, lambda: json_response([person("acc-bo", "bo@acme.com")]))
        search.add("ENG", None, {"issues": [issue(1, "2024-05-01T10:00:00.000+0000")]})
        connector, _ = await ready_connector(site_db, checkpoints)
        await connector.run_sync()
        developers = self._access(site_db)["developers"]
        assert developers

        checkpoints.sync_points.clear()
        api.on("GET", f"{JIRA}/project/ENG/role", json_response([]))
        graph = await run_full_sync(connector, site_db)

        assert connector.full_sync_coverage().complete is False, "an empty list is not a project with no roles"
        graph.unlink_nodes_missed_by_full_sync.assert_not_awaited()
        assert self._access(site_db)["developers"] == developers

    @pytest.mark.parametrize(
        "page",
        [
            pytest.param({"values": None, "isLast": True}, id="null list"),
            pytest.param({"isLast": True}, id="no list"),
            pytest.param({"values": [{"groupId": "grp-dev", "name": "devs"}, {"name": "qa"}], "isLast": True}, id="row without an id"),
            pytest.param({"values": [{"groupId": "grp-dev", "name": "devs"}, "qa"], "isLast": True}, id="row not an object"),
            pytest.param([{"groupId": "grp-dev", "name": "devs"}], id="not a page"),
        ],
    )
    async def test_a_malformed_group_page_is_an_incomplete_read(self, api, site_db, checkpoints, search, page) -> None:
        self._stub_bo_everywhere(api)
        self._second_page(api, lambda: json_response([person("acc-bo", "bo@acme.com")]))
        api.on("GET", f"{JIRA}/group/bulk", {"values": [
            {"groupId": "grp-dev", "name": "devs"}, {"groupId": "grp-qa", "name": "qa"},
        ], "isLast": True})
        search.add("ENG", None, {"issues": [issue(1, "2024-05-01T10:00:00.000+0000")]})
        connector, _ = await ready_connector(site_db, checkpoints)
        await connector.run_sync()
        qa = saved_members(site_db, "grp-qa")
        assert qa

        checkpoints.sync_points.clear()
        api.on("GET", f"{JIRA}/group/bulk", json_response(page))
        graph = await run_full_sync(connector, site_db)

        assert connector.full_sync_coverage().complete is False, "a group left out is not a group Jira removed"
        graph.unlink_nodes_missed_by_full_sync.assert_not_awaited()
        assert saved_members(site_db, "grp-qa") == qa

    async def test_a_refused_issue_search_keeps_the_project_its_records_belong_to(
        self, api, site_db, checkpoints, search
    ) -> None:
        self._stub_bo_everywhere(api)
        self._second_page(api, lambda: json_response([person("acc-bo", "bo@acme.com")]))
        api.on("GET", f"{JIRA}/permissionscheme/1/permission", {"permissions": []})  # no grants of its own
        search.add("ENG", None, {"issues": [issue(1, "2024-05-01T10:00:00.000+0000")]})
        connector, _ = await ready_connector(site_db, checkpoints)
        await connector.run_sync()
        assert site_db.record_group_permissions["10000"] == []

        checkpoints.sync_points.clear()
        api.on("GET", f"{JIRA}/project/ENG/permissionscheme", json_response({"errorMessages": ["oops"]}, status=500))
        search.add("ENG", None, json_response({"errorMessages": ["denied"]}, status=403))
        graph = await run_full_sync(connector, site_db)

        graph.unlink_nodes_missed_by_full_sync.assert_awaited_once()
        assert site_db.kept_records, "the project's records are kept"
        assert "10000" not in site_db.unlinked_record_groups, "so is the project, so the records keep their link into it"

    async def test_a_full_sync_whose_issue_search_is_refused_keeps_the_projects_records_and_still_unlinks(
        self, api, site_db, checkpoints, search
    ) -> None:
        self._stub_bo_everywhere(api)
        self._second_page(api, lambda: json_response([person("acc-bo", "bo@acme.com")]))
        search.add("ENG", None, {"issues": [issue(1, "2024-05-01T10:00:00.000+0000")]})
        connector, _ = await ready_connector(site_db, checkpoints)
        await connector.run_sync()
        stored = sorted(r.id for r in site_db.records.values() if r.external_record_group_id == "10000")
        assert stored

        checkpoints.sync_points.clear()
        search.add("ENG", None, json_response({"errorMessages": ["denied"]}, status=403))
        graph = await run_full_sync(connector, site_db)

        assert connector.full_sync_coverage().complete is True
        graph.unlink_nodes_missed_by_full_sync.assert_awaited_once()
        assert sorted(site_db.kept_records) == stored, "the project's stored records are noted as seen"

    @pytest.mark.parametrize(("status", "complete"), [(500, False), (400, True), (404, True)])
    async def test_a_full_sync_whose_issue_search_fails_is_classed_by_its_status(
        self, api, site_db, checkpoints, search, status, complete
    ) -> None:
        self._stub_bo_everywhere(api)
        self._second_page(api, lambda: json_response([person("acc-bo", "bo@acme.com")]))
        search.add("ENG", None, {"issues": [issue(1, "2024-05-01T10:00:00.000+0000")]})
        connector, _ = await ready_connector(site_db, checkpoints)
        await connector.run_sync()

        checkpoints.sync_points.clear()
        search.add("ENG", None, json_response({"errorMessages": ["no"]}, status=status))
        await run_full_sync(connector, site_db)

        assert connector.full_sync_coverage().complete is complete
        assert site_db.kept_records == [], "only a refusal keeps the records"

    def test_the_connector_class_keeps_its_registration(self) -> None:
        # The helpers live in the same module; the builder decorator must still land on the connector.
        assert "_connector_metadata" in JiraConnector.__dict__

class TestUnreadApplicationRoles:
    """ENG's scheme also grants the jira-software application role, which Jira maps to grp-sw."""

    @staticmethod
    def _stub_application_role(api: AtlassianApiStub) -> None:
        stub_site(api)
        api.on("GET", f"{JIRA}/applicationrole", json_response([{"key": "jira-software", "groupDetails": [{"groupId": "grp-sw", "name": "jira-software-users"}]}]))
        api.on("GET", f"{JIRA}/permissionscheme/1/permission", {"permissions": [
            {"permission": "BROWSE_PROJECTS", "holder": {"type": "group", "parameter": "devs", "value": "grp-dev"}},
            {"permission": "BROWSE_PROJECTS", "holder": {"type": "applicationRole", "parameter": "jira-software"}},
        ]})

    @pytest.mark.parametrize(
        "answer",
        [
            pytest.param(json_response({"errorMessages": ["busy"]}, status=503), id="5xx"),
            pytest.param(json_response({"errorMessages": ["timeout"]}, status=408), id="408"),
            pytest.param(httpx.ConnectError("reset"), id="network"),
            pytest.param(httpx.Response(200, content=b"<html>proxy error</html>"), id="unparseable"),
            pytest.param(json_response({"roles": []}), id="wrong-shape"),
            pytest.param(json_response(["jira-software"]), id="malformed-entry"),
            pytest.param(json_response([{"groupDetails": [{"groupId": "grp-sw"}]}]), id="role-without-key"),
        ],
    )
    async def test_an_unread_application_role_list_keeps_the_project_access(self, api, site_db, checkpoints, search, answer) -> None:
        self._stub_application_role(api)
        search.add("ENG", None, {"issues": [issue(1, "2024-05-01T10:00:00.000+0000")]})
        connector, _ = await ready_connector(site_db, checkpoints)
        await connector.run_sync()
        before = acl(site_db)
        assert ("EntityType.GROUP", "grp-sw") in before

        def roles(request: httpx.Request) -> httpx.Response:
            if isinstance(answer, Exception):
                raise answer
            return answer

        api.on("GET", f"{JIRA}/applicationrole", roles)
        await connector.run_sync()

        assert acl(site_db) == before
        assert "1" in tickets(site_db), "the project's issues are still synced"

    async def test_an_unread_application_role_list_still_updates_projects_that_do_not_use_one(
        self, api, site_db, checkpoints, search
    ) -> None:
        stub_site(api)
        connector, _ = await ready_connector(site_db, checkpoints)
        await connector.run_sync()

        api.on("GET", f"{JIRA}/applicationrole", json_response({"errorMessages": ["busy"]}, status=503))
        api.on("GET", f"{JIRA}/permissionscheme/1/permission", {"permissions": [
            {"permission": "BROWSE_PROJECTS", "holder": {"type": "group", "parameter": "qa", "value": "grp-qa"}},
        ]})
        await connector.run_sync()

        assert acl(site_db) == [("EntityType.GROUP", "grp-qa")]

    async def test_a_first_sync_with_unread_application_roles_saves_the_grants_that_were_read(
        self, api, site_db, checkpoints, search
    ) -> None:
        self._stub_application_role(api)
        api.on("GET", f"{JIRA}/applicationrole", json_response({"errorMessages": ["busy"]}, status=503))
        connector, _ = await ready_connector(site_db, checkpoints)

        await connector.run_sync()

        # Nothing was stored before, so holding the project back would leave it with no access.
        assert acl(site_db) == [("EntityType.GROUP", "grp-dev")]

    async def test_a_first_sync_cut_short_after_saving_the_project_keeps_its_access(
        self, api, site_db, checkpoints, search, monkeypatch
    ) -> None:
        self._stub_application_role(api)
        connector, _ = await ready_connector(site_db, checkpoints)

        async def stopped(*_: object) -> None:
            raise RuntimeError("sync stopped")

        monkeypatch.setattr(connector, "_update_issues_sync_checkpoint", stopped)
        with pytest.raises(RuntimeError, match="sync stopped"):
            await connector.run_sync()
        before = acl(site_db)
        assert checkpoints.values_for("issues_global") is None, "the sync stopped before its checkpoint"
        monkeypatch.undo()

        api.on("GET", f"{JIRA}/applicationrole", json_response({"errorMessages": ["busy"]}, status=503))
        await connector.run_sync()

        assert acl(site_db) == before

    async def test_a_project_whose_access_was_never_saved_is_saved_with_what_was_read(
        self, api, site_db, checkpoints, search
    ) -> None:
        self._stub_application_role(api)
        api.on("GET", f"{JIRA}/project/search", {"values": [
            {"id": "10000", "key": "ENG", "name": "Engineering"},
            {"id": "20000", "key": "OPS", "name": "Operations"},
        ], "isLast": True})
        api.on("GET", f"{JIRA}/project/OPS/role", {})
        api.on("GET", f"{JIRA}/project/OPS/permissionscheme", json_response({"errorMessages": ["oops"]}, status=500))
        connector, _ = await ready_connector(site_db, checkpoints)
        await connector.run_sync()
        eng_before = acl(site_db)
        assert "20000" not in site_db.record_group_permissions, "OPS's scheme could not be read, so nothing is saved"

        api.on("GET", f"{JIRA}/project/OPS/permissionscheme", {"id": 2})
        api.on("GET", f"{JIRA}/permissionscheme/2/permission", {"permissions": [
            {"permission": "BROWSE_PROJECTS", "holder": {"type": "group", "parameter": "devs", "value": "grp-dev"}},
            {"permission": "BROWSE_PROJECTS", "holder": {"type": "applicationRole", "parameter": "jira-software"}},
        ]})
        api.on("GET", f"{JIRA}/applicationrole", json_response({"errorMessages": ["busy"]}, status=503))
        await connector.run_sync()

        assert acl(site_db) == eng_before, "ENG's saved access is kept"
        ops = sorted((str(p.entity_type), p.external_id or p.email) for p in site_db.record_group_permissions["20000"])
        assert ops == [("EntityType.GROUP", "grp-dev")], "OPS had nothing stored, so the grants that were read are saved"

    async def test_a_full_sync_after_its_prep_kept_the_grants_keeps_the_roles_groups(
        self, api, site_db, checkpoints, search
    ) -> None:
        self._stub_application_role(api)
        connector, _ = await ready_connector(site_db, checkpoints)
        await connector.run_sync()
        before = acl(site_db)

        # A full sync's prep deletes the sync points but leaves the stored grants in place (#4119).
        checkpoints.sync_points.clear()
        api.on("GET", f"{JIRA}/applicationrole", json_response({"errorMessages": ["busy"]}, status=503))
        await connector.run_sync()

        assert acl(site_db) == before, "the role's group stays"

    async def test_a_stored_access_read_that_fails_stops_the_sync_before_the_project_is_saved(
        self, api, site_db, checkpoints, search
    ) -> None:
        self._stub_application_role(api)
        connector, _ = await ready_connector(site_db, checkpoints)
        await connector.run_sync()
        before = acl(site_db)

        site_db.fail_stored_permission_reads = True
        api.on("GET", f"{JIRA}/applicationrole", json_response({"errorMessages": ["busy"]}, status=503))
        with pytest.raises(StoredAccessReadError):
            await connector.run_sync()

        assert acl(site_db) == before, "a failed read is not taken for 'nothing stored'"

    async def test_a_full_sync_with_unread_roles_keeps_the_project_access_after_the_unlink(
        self, api, site_db, checkpoints, search
    ) -> None:
        self._stub_application_role(api)  # ENG has no issues, so nothing but the held project marks it seen
        connector, _ = await ready_connector(site_db, checkpoints)
        await connector.run_sync()
        before = acl(site_db)
        assert ("EntityType.GROUP", "grp-sw") in before

        # A full sync (#4119): sync points go, stored edges stay, and after the sync returns
        # whatever it did not write is unlinked, unless it reports an incomplete read.
        checkpoints.sync_points.clear()
        api.on("GET", f"{JIRA}/applicationrole", json_response({"errorMessages": ["busy"]}, status=503))
        await run_full_sync(connector, site_db)

        assert acl(site_db) == before, "the role's group stays"
        assert connector.full_sync_coverage().complete is True, "the unlink still runs for what is really gone"

    async def test_a_complete_full_sync_still_unlinks_what_it_did_not_see(self, api, site_db, checkpoints, search) -> None:
        self._stub_application_role(api)
        search.add("ENG", None, {"issues": [issue(1, "2024-05-01T10:00:00.000+0000")]})
        connector, _ = await ready_connector(site_db, checkpoints)
        await connector.run_sync()

        graph = await run_full_sync(connector, site_db)

        graph.unlink_nodes_missed_by_full_sync.assert_awaited_once()
        assert connector.full_sync_coverage().complete is True

    async def test_a_full_sync_with_an_unreadable_scheme_keeps_the_project_access_after_the_unlink(
        self, api, site_db, checkpoints, search
    ) -> None:
        self._stub_application_role(api)  # ENG has no issues, so nothing but the kept project marks it seen
        connector, _ = await ready_connector(site_db, checkpoints)
        await connector.run_sync()
        before = acl(site_db)

        checkpoints.sync_points.clear()
        api.on("GET", f"{JIRA}/project/ENG/permissionscheme", json_response({"errorMessages": ["oops"]}, status=500))
        await run_full_sync(connector, site_db)

        assert acl(site_db) == before, "the stored grants survive the unlink"

    async def test_a_complete_application_role_list_rewrites_the_project_access(self, api, site_db, checkpoints, search) -> None:
        self._stub_application_role(api)
        connector, _ = await ready_connector(site_db, checkpoints)
        await connector.run_sync()

        api.on("GET", f"{JIRA}/applicationrole", json_response([{"key": "jira-software", "groupDetails": [{"groupId": "grp-new", "name": "new-licensees"}]}]))
        await connector.run_sync()

        assert acl(site_db) == [("EntityType.GROUP", "grp-dev"), ("EntityType.GROUP", "grp-new")]

    async def test_a_forbidden_application_role_list_still_falls_back_to_the_jira_account(
        self, api, site_db, checkpoints, search
    ) -> None:
        self._stub_application_role(api)
        connector, _ = await ready_connector(site_db, checkpoints)
        await connector.run_sync()

        api.on("GET", f"{JIRA}/applicationrole", json_response({"errorMessages": ["no"]}, status=403))
        await connector.run_sync()

        assert acl(site_db) == [("EntityType.GROUP", "grp-dev"), ("EntityType.USER", "sync@acme.com")]

    def test_the_connector_class_keeps_its_registration(self) -> None:
        # The helper exception lives in the same module; the builder decorator must still land on the connector.
        assert "_connector_metadata" in JiraConnector.__dict__



def now_in_project(api: AtlassianApiStub, ref: int, project_id: str, key: str) -> None:
    """Jira still has the issue, and says which project it is in now."""
    api.on("GET", f"{JIRA}/issue/{ref}", {
        "id": str(ref), "key": key, "fields": {"project": {"id": project_id, "key": key.split("-")[0]}},
    })


class TestIssuesThatMovedOutOfTheSyncedProjects:
    async def test_an_issue_the_search_does_not_list_yet_is_kept(
        self, api, site_db, checkpoints, listing, fresh_notification_memory
    ) -> None:
        api.on("GET", AUDIT, {"records": [], "total": 0})
        connector = await synced_three_issues(api, site_db, checkpoints, listing)
        listing.id_pages[None] = {"issues": ids(1, 3), "isLast": True}
        now_in_project(api, 2, "10000", "ENG-2")

        await connector.run_sync()

        assert set(tickets(site_db)) == {"1", "2", "3"}

    async def test_an_issue_that_moved_is_not_served_under_its_old_project(
        self, api, site_db, checkpoints, listing, fresh_notification_memory
    ) -> None:
        api.on("GET", AUDIT, {"records": [], "total": 0})
        connector = await synced_three_issues(api, site_db, checkpoints, listing)
        api.on("GET", f"{JIRA}/issue/1", opened_issue(1, project_id="20000"))

        with pytest.raises(HTTPException) as err:
            await connector._process_issue_blockgroups_for_streaming(site_db.records["1"])

        assert err.value.status_code == 404
        assert await connector._check_and_fetch_updated_issue(site_db.records["1"]) is None, "a reindex leaves it to the sync"


SALARIES = {"id": "901", "filename": "salaries.txt", "mimeType": "text/plain", "size": 20, "created": "2024-05-02T15:00:00.000+0000"}
NOTES = {"id": "902", "filename": "notes.txt", "mimeType": "text/plain", "size": 20, "created": "2024-05-02T15:00:00.000+0000"}
ADMINS_ONLY = {"visibility": {"type": "role", "value": "Administrators", "identifier": "Administrators"}}


def comment(comment_id: str, text: str, *, file: dict[str, Any] | None = None, **restriction: object) -> dict[str, Any]:
    """A comment as Jira returns it with ``expand=renderedBody``; ``file`` is an attachment shown in it.

    As on a real site, the ADF names the file by a media id only: the attachment id is in the rendered HTML.
    """
    content: list[dict[str, Any]] = [{"type": "paragraph", "content": [{"type": "text", "text": text}]}]
    html = f"<p>{text}</p>"
    if file:
        content.append({"type": "mediaGroup", "content": [
            {"type": "media", "attrs": {"type": "file", "id": f"media-uuid-{file['id']}", "collection": ""}},
        ]})
        html += (
            f'<p><a href="/rest/api/3/attachment/content/{file["id"]}" data-attachment-type="file" '
            f'data-attachment-name="{file["filename"]}">{file["filename"]}</a></p>'
        )
    return {
        "id": comment_id, "author": {"displayName": "Ana"}, "created": "2024-05-02T10:00:00.000+0000",
        "jsdPublic": True, "body": {"type": "doc", "version": 1, "content": content}, "renderedBody": html, **restriction,
    }


def embedded(comments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The same comments as an issue embeds them: without the rendered HTML."""
    return [{k: v for k, v in c.items() if k != "renderedBody"} for c in comments]


def commented(num: int, updated: str, comments: list[dict[str, Any]], *, total: int | None = None) -> dict[str, Any]:
    """An issue as the search returns it: two attachments and the comments Jira embeds."""
    ticket = issue(num, updated, attachments=[SALARIES, NOTES])
    ticket["fields"]["comment"] = {"comments": embedded(comments), "total": len(comments) if total is None else total}
    return ticket


def jira_has_comments(api: AtlassianApiStub, num: int, comments: list[dict[str, Any]]) -> None:
    """The comment listing of an issue, and its rendered description."""
    api.on("GET", f"{JIRA}/issue/{num}/comment", {"comments": comments, "total": len(comments)})
    api.on("GET", f"{JIRA}/issue/{num}", {"id": str(num), "key": f"ENG-{num}", "renderedFields": {"description": "<p>the description</p>"}})


def opened_issue(num: int, comments: list[dict[str, Any]] | None = None, *, project_id: str = "10000") -> dict[str, Any]:
    """An issue as GET issue returns it when a record is opened."""
    comments = comments or []
    return {
        "id": str(num), "key": f"ENG-{num}",
        "fields": {
            "summary": f"Issue {num}", "project": {"id": project_id, "key": "ENG"}, "updated": "2024-05-03T10:00:00.000+0000",
            "attachment": [SALARIES, NOTES], "comment": {"comments": embedded(comments), "total": len(comments)},
        },
        "renderedFields": {
            "description": "<p>the description</p>",
            "comment": {"comments": [{"id": c["id"], "body": c["renderedBody"]} for c in comments]},
        },
    }


class TestRestrictedComments:
    async def test_restricted_comments_and_internal_notes_are_left_out_of_what_is_served(
        self, api, db, checkpoints, search
    ) -> None:
        search.add("ENG", None, {"issues": [issue(1, "2024-05-01T10:00:00.000+0000")]})
        connector, _ = await ready_connector(db, checkpoints)
        await connector._sync_all_project_issues([project("ENG", "10000")], [], None)
        api.on("GET", f"{JIRA}/issue/1", opened_issue(1, [
            comment("c1", "public update"),
            comment("c2", "ROOT CAUSE: one engineer", **ADMINS_ONLY),
            comment("c3", "INTERNAL: customer is overdue", jsdPublic=False),
            comment("c4", "reply to the customer"),
        ]))

        served = (await connector._process_issue_blockgroups_for_streaming(db.records["1"])).decode()

        assert "public update" in served and "reply to the customer" in served and "the description" in served
        assert "ROOT CAUSE" not in served, "a comment for Administrators only is served to every reader of the issue"
        assert "INTERNAL" not in served, "an internal note is served to every reader of the issue"

    async def test_a_file_attached_in_a_restricted_comment_gets_no_record(self, api, db, checkpoints, search) -> None:
        comments = [comment("c1", "see the figures", file=SALARIES, **ADMINS_ONLY)]
        search.add("ENG", None, {"issues": [commented(1, "2024-05-01T10:00:00.000+0000", comments)]})
        jira_has_comments(api, 1, comments)
        connector, _ = await ready_connector(db, checkpoints)

        await connector._sync_all_project_issues([project("ENG", "10000")], [], None)

        assert "1" in tickets(db)
        assert "attachment_901" not in db.records, "readable by fewer people than the issue"
        assert "attachment_902" in db.records, "a file attached to the issue itself stays"

    async def test_an_issue_without_restricted_comments_costs_no_extra_request(self, api, db, checkpoints, search) -> None:
        search.add("ENG", None, {"issues": [commented(1, "2024-05-01T10:00:00.000+0000", [comment("c1", "figures", file=SALARIES)])]})
        connector, _ = await ready_connector(db, checkpoints)

        await connector._sync_all_project_issues([project("ENG", "10000")], [], None)

        assert "attachment_901" in db.records and "attachment_902" in db.records
        assert api.calls("GET", f"{JIRA}/issue/1/comment") == [] and api.calls("GET", f"{JIRA}/issue/1") == []

    async def test_a_file_a_public_comment_also_shows_keeps_its_record(self, api, db, checkpoints, search) -> None:
        comments = [
            comment("c1", "see the figures", file=SALARIES, **ADMINS_ONLY),
            comment("c2", "shared with everyone", file=SALARIES),
        ]
        search.add("ENG", None, {"issues": [commented(1, "2024-05-01T10:00:00.000+0000", comments)]})
        jira_has_comments(api, 1, comments)
        connector, _ = await ready_connector(db, checkpoints)

        await connector._sync_all_project_issues([project("ENG", "10000")], [], None)

        assert "attachment_901" in db.records

    async def test_a_file_stored_before_its_comment_was_restricted_is_removed(self, api, db, checkpoints, search) -> None:
        restricted = [comment("c1", "figures", file=SALARIES, **ADMINS_ONLY)]
        search.add("ENG", None, [
            {"issues": [commented(1, "2024-05-01T10:00:00.000+0000", [comment("c1", "figures", file=SALARIES)])]},
            {"issues": [commented(1, "2024-05-02T10:00:00.000+0000", restricted)]},
        ])
        jira_has_comments(api, 1, restricted)
        connector, _ = await ready_connector(db, checkpoints)
        await connector._sync_all_project_issues([project("ENG", "10000")], [], None)
        assert "attachment_901" in db.records

        await connector._sync_all_project_issues([project("ENG", "10000")], [], None)

        assert "attachment_901" not in db.records and "attachment_902" in db.records

    async def test_a_restricted_comment_the_search_did_not_embed_is_still_read(self, api, db, checkpoints, search) -> None:
        restricted = comment("c1", "figures", file=SALARIES, **ADMINS_ONLY)
        latest = comment("c2", "thanks")
        search.add("ENG", None, {"issues": [commented(1, "2024-05-01T10:00:00.000+0000", [latest], total=2)]})
        jira_has_comments(api, 1, [restricted, latest])
        connector, _ = await ready_connector(db, checkpoints)

        await connector._sync_all_project_issues([project("ENG", "10000")], [], None)

        (listing_call,) = api.calls("GET", f"{JIRA}/issue/1/comment")
        assert AtlassianApiStub.query(listing_call)["expand"] == "renderedBody"
        assert "attachment_901" not in db.records and "attachment_902" in db.records

    async def test_comments_that_cannot_be_read_leave_the_attachments_as_they_are(self, api, db, checkpoints, search) -> None:
        search.add("ENG", None, {"issues": [commented(1, "2024-05-01T10:00:00.000+0000", [comment("c2", "thanks")], total=2)]})
        api.on("GET", f"{JIRA}/issue/1/comment", json_response({"errorMessages": ["boom"]}, status=500))
        connector, _ = await ready_connector(db, checkpoints)

        await connector._sync_all_project_issues([project("ENG", "10000")], [], None)

        assert "1" in tickets(db), "the issue itself is still synced"
        assert "attachment_901" not in db.records and "attachment_902" not in db.records, (
            "no attachment is given a record while it is unknown which of them a restricted comment holds"
        )

    async def test_opening_an_issue_does_not_offer_a_file_from_a_restricted_comment(self, api, db, checkpoints, search) -> None:
        search.add("ENG", None, {"issues": [issue(1, "2024-05-01T10:00:00.000+0000")]})
        connector, _ = await ready_connector(db, checkpoints)
        await connector._sync_all_project_issues([project("ENG", "10000")], [], None)
        api.on("GET", f"{JIRA}/issue/1", opened_issue(1, [comment("c1", "see the figures", file=SALARIES, **ADMINS_ONLY)]))

        served = (await connector._process_issue_blockgroups_for_streaming(db.records["1"])).decode()

        assert "attachment_901" not in db.records, "opening the issue must not create the record either"
        assert "attachment_902" in db.records and "notes.txt" in served
        assert "salaries.txt" not in served

    async def test_opening_an_issue_with_more_comments_than_jira_embeds_reads_them_all_and_serves_the_public_ones(
        self, api, db, checkpoints, search
    ) -> None:
        search.add("ENG", None, {"issues": [issue(1, "2024-05-01T10:00:00.000+0000")]})
        connector, _ = await ready_connector(db, checkpoints)
        await connector._sync_all_project_issues([project("ENG", "10000")], [], None)
        latest = comment("c3", "latest reply")
        jira_has_comments(api, 1, [comment("c1", "first answer"), comment("c2", "ROOT CAUSE", **ADMINS_ONLY), latest])
        opened = opened_issue(1, [latest])
        opened["fields"]["comment"]["total"] = 3
        api.on("GET", f"{JIRA}/issue/1", opened)

        served = (await connector._process_issue_blockgroups_for_streaming(db.records["1"])).decode()

        assert "first answer" in served and "latest reply" in served, "a comment Jira did not embed is still served"
        assert "ROOT CAUSE" not in served


def titles(connector: JiraConnector) -> list[str]:
    return [note["title"] for note in connector._notification_service.sent]


class TestWhatASyncKeptAsStored:
    # A kept project access or a short user read is noted as seen instead (see TestUnfinishedUserRead).
    @pytest.mark.parametrize("break_jira", [
        pytest.param(lambda api, search: search.add("ENG", None, json_response({"errorMessages": ["boom"]}, status=500)), id="an-issue-read-failed"),
        pytest.param(lambda api, search: groups_in_two_pages(api, json_response({}, status=503)), id="the-group-list-was-cut-short"),
        pytest.param(lambda api, search: api.on("GET", f"{JIRA}/project/ENG/role/10002", json_response({}, status=500)), id="a-role-read-failed"),
    ])
    async def test_a_full_sync_that_kept_part_of_jira_as_stored_reports_itself_incomplete(
        self, api, site_db, checkpoints, search, break_jira
    ) -> None:
        stub_site(api)
        connector, _ = await ready_connector(site_db, checkpoints)
        await connector.run_sync()
        assert connector.full_sync_coverage().complete is True, "a clean run read everything"

        break_jira(api, search)
        await connector.run_sync()

        assert connector.full_sync_coverage().complete is False, "so nothing it did not see is unlinked"

    @pytest.mark.parametrize(("break_jira", "title"), [
        pytest.param(
            lambda api: groups_in_two_pages(api, json_response({}, status=503)),
            "couldn't read all user groups", id="the-group-list-was-cut-short",
        ),
        pytest.param(
            lambda api: api.on("GET", f"{JIRA}/project/ENG/role/10002", json_response({}, status=500)),
            "couldn't sync project roles", id="a-role-read-failed",
        ),
    ])
    async def test_the_admin_is_told_when_groups_or_roles_keep_their_stored_members(
        self, api, site_db, checkpoints, search, fresh_notification_memory, break_jira, title
    ) -> None:
        stub_site(api)
        break_jira(api)
        connector, _ = await ready_connector(site_db, checkpoints)

        await connector.run_sync()
        await drain_notifications(connector)

        assert any(t.endswith(title) for t in titles(connector)), titles(connector)


def browse_by_product_access(api: AtlassianApiStub, parameter: str | None) -> None:
    """ENG grants Browse to an application role (none: any logged-in user); two products have a group each."""
    holder: dict[str, Any] = {"type": "applicationRole", "parameter": parameter}
    api.on("GET", f"{JIRA}/permissionscheme/1/permission", {"permissions": [{"permission": "BROWSE_PROJECTS", "holder": holder}]})
    api.on("GET", f"{JIRA}/applicationrole", json_response([
        {"key": "jira-software", "groupDetails": [{"groupId": "grp-sw", "name": "jira-software-users"}]},
        {"key": "jira-servicedesk", "groupDetails": [{"groupId": "grp-sd", "name": "jira-servicedesk-users"}]},
    ]))


class TestWhoMayBrowse:
    async def test_any_logged_in_user_is_the_groups_with_product_access(self, api, site_db, checkpoints, search) -> None:
        # As on main (#4383): the union of the application roles' groups, not an org grant.
        stub_site(api)
        browse_by_product_access(api, None)
        connector, _ = await ready_connector(site_db, checkpoints)

        await connector.run_sync()

        assert acl(site_db) == [("EntityType.GROUP", "grp-sd"), ("EntityType.GROUP", "grp-sw")]

    async def test_unreadable_application_roles_keep_the_access_of_a_project_granted_through_one(
        self, api, site_db, checkpoints, search
    ) -> None:
        stub_site(api)
        browse_by_product_access(api, "jira-software")
        connector, _ = await ready_connector(site_db, checkpoints)
        await connector.run_sync()
        assert acl(site_db) == [("EntityType.GROUP", "grp-sw")]

        api.on("GET", f"{JIRA}/applicationrole", json_response({"errorMessages": ["boom"]}, status=500))
        await connector.run_sync()

        assert acl(site_db) == [("EntityType.GROUP", "grp-sw")], "not granted to nobody while the roles are unknown"

    async def test_a_deactivated_user_named_in_the_scheme_gets_no_grant(self, api, site_db, checkpoints, search) -> None:
        stub_site(api)
        api.on("GET", f"{JIRA}/permissionscheme/1/permission", {"permissions": [
            {"permission": "BROWSE_PROJECTS", "holder": {"type": "group", "parameter": "devs", "value": "grp-dev"}},
            {"permission": "BROWSE_PROJECTS", "holder": {"type": "user", "parameter": "acc-old", "user": {
                "accountId": "acc-old", "emailAddress": "old@acme.com", "active": False,
            }}},
        ]})
        connector, _ = await ready_connector(site_db, checkpoints)

        await connector.run_sync()

        assert acl(site_db) == [("EntityType.GROUP", "grp-dev")]


class TestAccountTimezone:
    async def test_an_unknown_timezone_widens_the_date_bounds_so_nothing_is_cut_off(
        self, api, site_db, checkpoints, search
    ) -> None:
        stub_site(api)
        api.on("GET", f"{JIRA}/myself", {"accountId": "me", "emailAddress": "sync@acme.com"})
        connector, config_service = await ready_connector(site_db, checkpoints)
        start = int(datetime.fromisoformat("2024-05-15T00:00:00+00:00").timestamp() * 1000)
        end = int(datetime.fromisoformat("2024-05-20T00:00:00+00:00").timestamp() * 1000)
        config_service.config["filters"] = {"sync": {"values": {
            "created": {"operator": "is_between", "type": "datetime", "value": {"start": start, "end": end}},
        }}}

        await connector.run_sync()

        jql = search.bodies[0]["jql"]
        assert 'created >= "2024-05-14 12:00"' in jql, "12 hours earlier: the furthest west a zone can be"
        assert 'created <= "2024-05-20 14:00"' in jql, "14 hours later: the furthest east"

    async def test_a_timezone_changed_in_jira_applies_from_the_next_issue_sync(self, api, db, checkpoints, search) -> None:
        search.add("ENG", None, {"issues": [issue(1, "2024-05-02T15:30:00.000+0000")]})
        connector, _ = await ready_connector(db, checkpoints)
        await connector._sync_all_project_issues([project("ENG", "10000")], [], None)

        api.on("GET", f"{JIRA}/myself", {"accountId": "me", "emailAddress": "sync@acme.com", "timeZone": "Asia/Kolkata"})
        search.bodies.clear()
        await connector._sync_all_project_issues([project("ENG", "10000")], [], None)

        assert 'updated > "2024-05-02 20:58"' in search.bodies[0]["jql"], "15:28 UTC written in Kolkata time"


OPS_ONLY = {"values": [{"id": "10001", "key": "OPS", "name": "Operations"}], "isLast": True}


class TestProjectsThatLeftTheSync:
    @pytest.mark.parametrize(("answer", "selected"), [
        pytest.param(json_response({"errorMessages": ["No project could be found"]}, status=404), None, id="gone"),
        pytest.param({"id": "10000", "key": "ENG", "archived": True}, None, id="archived"),
    ])
    async def test_a_project_the_sync_no_longer_covers_is_removed_with_its_issues(
        self, api, site_db, checkpoints, search, fresh_notification_memory, answer, selected
    ) -> None:
        stub_site(api)
        search.add("ENG", None, {"issues": [issue(1, "2024-05-01T10:00:00.000+0000")]})
        connector, config_service = await ready_connector(site_db, checkpoints)
        await connector.run_sync()
        assert "1" in tickets(site_db) and "10000" in site_db.record_groups

        api.on("GET", f"{JIRA}/project/search", OPS_ONLY)
        api.on("GET", f"{JIRA}/project/10000", answer)
        if selected:
            config_service.config["filters"] = {"sync": {"values": {
                "project_keys": {"operator": "in", "type": "list", "value": selected},
            }}}
        await connector.run_sync()
        await drain_notifications(connector)

        assert "1" not in tickets(site_db) and "10000" not in site_db.record_groups
        assert any(t.endswith("removed projects it no longer syncs") for t in titles(connector))

    async def test_a_removed_projects_epic_takes_no_story_of_a_synced_project_with_it(
        self, api, site_db, checkpoints, search, fresh_notification_memory
    ) -> None:
        stub_site(api)
        search.add("ENG", None, {"issues": [issue(1, "2024-05-01T10:00:00.000+0000")]})
        connector, _ = await ready_connector(site_db, checkpoints)
        await connector.run_sync()
        # A story in OPS stored under the ENG epic while both were synced.
        site_db.records["2"] = site_db.records["1"].model_copy(update={
            "id": "story-2", "external_record_id": "2", "external_record_group_id": "10001",
            "parent_external_record_id": "1", "parent_record_type": RecordType.TICKET,
        })

        api.on("GET", f"{JIRA}/project/search", OPS_ONLY)
        api.on("GET", f"{JIRA}/project/10000", json_response({"errorMessages": ["No project could be found"]}, status=404))
        await connector.run_sync()

        assert "1" not in tickets(site_db) and "2" in tickets(site_db), "the story is in a project still synced"

    async def test_a_project_the_filter_leaves_out_is_kept_and_only_unlinked_by_the_full_sync(
        self, api, site_db, checkpoints, search
    ) -> None:
        stub_site(api)
        search.add("ENG", None, {"issues": [issue(1, "2024-05-01T10:00:00.000+0000")]})
        connector, config_service = await ready_connector(site_db, checkpoints)
        await connector.run_sync()

        api.on("GET", f"{JIRA}/project/search", OPS_ONLY)
        config_service.config["filters"] = {"sync": {"values": {
            "project_keys": {"operator": "in", "type": "list", "value": ["OPS"]},
        }}}
        checkpoints.sync_points.clear()
        await run_full_sync(connector, site_db)

        assert "1" in tickets(site_db) and "10000" in site_db.record_groups, "kept for when it is selected again"
        assert "ENG_10002" in site_db.app_roles, "its roles are kept with it"
        assert "10000" in site_db.unlinked_record_groups, "the full sync unlinks it, so it is not shown"

    async def test_a_selected_key_that_was_renamed_is_followed_to_the_new_key(
        self, api, site_db, checkpoints, search, fresh_notification_memory
    ) -> None:
        stub_site(api)
        api.on("GET", f"{JIRA}/project/search", {"values": [], "isLast": True})
        api.on("GET", f"{JIRA}/project/OLD", {"id": "10000", "key": "ENG", "name": "Engineering"})
        search.add("ENG", None, {"issues": [issue(1, "2024-05-01T10:00:00.000+0000")]})
        connector, config_service = await ready_connector(site_db, checkpoints)
        config_service.config["filters"] = {"sync": {"values": {
            "project_keys": {"operator": "in", "type": "list", "value": ["OLD"]},
        }}}

        await connector.run_sync()
        await drain_notifications(connector)

        assert "1" in tickets(site_db) and acl(site_db) == [("EntityType.GROUP", "grp-dev")]
        assert any(t.endswith("found selected projects under new keys") for t in titles(connector))

    async def test_roles_of_a_project_the_sync_no_longer_covers_are_removed(self, api, site_db, checkpoints, search) -> None:
        stub_site(api)
        site_db.app_roles["OLD_10002"] = []
        connector, _ = await ready_connector(site_db, checkpoints)

        await connector.run_sync()

        assert "OLD_10002" not in site_db.app_roles and "ENG_10002" in site_db.app_roles

    async def test_no_project_is_removed_while_a_selected_key_cannot_be_looked_up(
        self, api, site_db, checkpoints, search
    ) -> None:
        stub_site(api)
        api.on("GET", f"{JIRA}/project/search", OPS_ONLY)
        api.on("GET", f"{JIRA}/project/OLD", {"id": "10000", "key": "ENG", "name": "Engineering"})
        search.add("ENG", None, {"issues": [issue(1, "2024-05-01T10:00:00.000+0000")]})
        connector, config_service = await ready_connector(site_db, checkpoints)
        config_service.config["filters"] = {"sync": {"values": {
            "project_keys": {"operator": "in", "type": "list", "value": ["OLD", "OPS"]},
        }}}
        await connector.run_sync()
        assert "1" in tickets(site_db) and "10000" in site_db.record_groups

        api.on("GET", f"{JIRA}/project/OLD", json_response({"errorMessages": ["boom"]}, status=500))
        api.on("GET", f"{JIRA}/project/10000", {"id": "10000", "key": "ENG", "name": "Engineering"})
        await connector.run_sync()

        assert "1" in tickets(site_db) and "10000" in site_db.record_groups, "ENG may be what OLD is called now"


CHART = {"id": "903", "filename": "chart.png", "mimeType": "image/png", "size": 2048, "created": "2024-05-02T15:00:00.000+0000"}


class TestAttachments:
    async def test_a_file_removed_beyond_the_embedded_changelog_is_found_by_comparing_ids(
        self, api, db, checkpoints, search
    ) -> None:
        edited = issue(1, "2024-05-03T10:00:00.000+0000", attachments=[NOTES])
        edited["changelog"] = {"total": 41, "histories": [{"items": [{"field": "status", "fromString": "To Do", "toString": "Done"}]}]}
        search.add("ENG", None, [
            {"issues": [issue(1, "2024-05-01T10:00:00.000+0000", attachments=[SALARIES, NOTES])]},
            {"issues": [edited]},
        ])
        connector, _ = await ready_connector(db, checkpoints)
        await connector._sync_all_project_issues([project("ENG", "10000")], [], None)
        assert "attachment_901" in db.records

        await connector._sync_all_project_issues([project("ENG", "10000")], [], None)

        assert "attachment_901" not in db.records, "its removal is among the changelog entries Jira left out"
        assert "attachment_902" in db.records

    async def test_a_removal_both_in_the_changelog_and_found_by_comparing_ids_is_deleted_once(
        self, api, db, checkpoints, search
    ) -> None:
        search.add("ENG", None, {"issues": [issue(1, "2024-05-01T10:00:00.000+0000", attachments=[SALARIES, NOTES])]})
        connector, _ = await ready_connector(db, checkpoints)
        await connector._sync_all_project_issues([project("ENG", "10000")], [], None)
        edited = issue(1, "2024-05-03T10:00:00.000+0000", attachments=[NOTES])
        edited["changelog"] = {"total": 41, "histories": [{"items": [
            {"field": "Attachment", "fieldId": "attachment", "from": "901", "to": None}]}]}

        delete_ids = await connector._handle_attachment_deletions_from_changelog(edited)

        assert delete_ids == [db.records["attachment_901"].id]

    async def test_an_image_only_the_rendered_description_shows_is_inlined_not_given_a_record(
        self, api, db, checkpoints, search
    ) -> None:
        ticket = issue(1, "2024-05-01T10:00:00.000+0000", attachments=[CHART, NOTES])
        ticket["fields"]["description"] = {"type": "doc", "version": 1, "content": [{"type": "mediaSingle", "content": [
            {"type": "media", "attrs": {"type": "file", "id": "media-uuid-903", "collection": ""}},
        ]}]}
        ticket["renderedFields"] = {"description": '<p><img src="/rest/api/3/attachment/content/903" alt="chart.png"></p>'}
        search.add("ENG", None, {"issues": [ticket]})
        connector, _ = await ready_connector(db, checkpoints)

        await connector._sync_all_project_issues([project("ENG", "10000")], [], None)

        assert search.bodies[0]["expand"] == "changelog,renderedFields"
        assert "attachment_903" not in db.records and "attachment_902" in db.records


class TestPlaceholderParents:
    async def test_a_parent_in_a_project_that_is_not_synced_stays_a_bare_stub(self, api, db, checkpoints, search) -> None:
        search.add("ENG", None, {"issues": [issue(1, "2024-05-01T10:00:00.000+0000")]})
        connector, _ = await ready_connector(db, checkpoints)
        await connector._sync_all_project_issues([project("ENG", "10000")], [], None)
        stub = db.records["1"].model_copy(update={
            "id": "stub-77", "external_record_id": "77", "record_name": "77", "external_revision_id": None, "is_placeholder": True,
        })
        db.records["77"] = stub
        db.placeholders = [stub]
        api.on("POST", f"{JIRA}/issue/bulkfetch", {"issues": [{"id": "77", "key": "OPS-77", "fields": {
            "summary": "Confidential roadmap", "project": {"id": "20000", "key": "OPS"}, "updated": "2024-05-01T10:00:00.000+0000",
        }}]})

        await connector._sweep_placeholder_records(synced_project_ids={"10000"})

        assert len(api.calls("POST", f"{JIRA}/issue/bulkfetch")) == 1
        assert db.records["77"].record_name == "77" and db.records["77"].is_placeholder
        assert "Confidential" not in db.records["77"].model_dump_json()

    async def test_an_issue_whose_parent_is_in_a_project_this_sync_does_not_cover_hangs_off_its_project(
        self, api, db, checkpoints, search,
    ) -> None:
        # A placeholder for that parent would never sync and would hide the issue for good.
        child = issue(1, "2024-05-01T10:00:00.000+0000")
        child["fields"]["parent"] = {"id": "77", "key": "OPS-77", "fields": {"summary": "Roadmap"}}
        search.add("ENG", None, {"issues": [child]})
        connector, _ = await ready_connector(db, checkpoints)
        connector._synced_project_keys = {"ENG"}

        await connector._sync_all_project_issues([project("ENG", "10000")], [], None)

        assert db.records["1"].parent_external_record_id is None
        assert "77" not in db.records, "no placeholder is made for a parent nobody syncs"

    async def test_the_synced_projects_are_forgotten_once_the_sync_ends(self, api, site_db, checkpoints, search) -> None:
        stub_site(api)
        connector, _ = await ready_connector(site_db, checkpoints)
        await connector.run_sync()
        assert connector._synced_project_keys is None, "a reindex afterwards goes by what is stored, not a stale project set"

    @pytest.mark.parametrize("parent_key", ["ENG-77", "OPS-77"])
    async def test_a_parent_in_its_own_or_another_synced_project_stays_its_parent(
        self, api, db, checkpoints, search, parent_key,
    ) -> None:
        child = issue(1, "2024-05-01T10:00:00.000+0000")
        child["fields"]["parent"] = {"id": "77", "key": parent_key, "fields": {"summary": "Roadmap"}}
        search.add("ENG", None, {"issues": [child]})
        connector, _ = await ready_connector(db, checkpoints)
        connector._synced_project_keys = {"ENG", "OPS"}

        await connector._sync_all_project_issues([project("ENG", "10000")], [], None)

        assert db.records["1"].parent_external_record_id == "77"
