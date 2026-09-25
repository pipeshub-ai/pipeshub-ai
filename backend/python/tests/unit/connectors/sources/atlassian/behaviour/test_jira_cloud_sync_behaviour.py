"""Jira Cloud issue sync, driven over a fake Atlassian API with its real OAuth client.

The connector, Jira client, request builder and HTTP client are real; every HTTP
request is answered by an in-memory stub and our databases are in-memory fakes.
Retry waits are recorded rather than slept (see conftest ``backoff_sleeps``).
"""

import json
import logging
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
)
from atlassian_cloud_fakes import (
    CLOUD_ID,
    SITE,
    CloudRecordsDb,
    RecordingNotifications,
    bearer,
    oauth_config,
    one_site,
    route_every_http_client,
)
from fastapi import HTTPException

from app.config.constants.arangodb import Connectors
from app.connectors.sources.atlassian.jira_cloud.connector import JiraConnector
from app.models.entities import FileRecord, RecordGroup, RecordGroupType, TicketRecord
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
        assert search.bodies[0]["jql"] == 'project = "ENG" AND updated > "2024-05-02 11:30" ORDER BY updated ASC, id ASC', (
            "the cut is written in the Jira account's timezone (New York), not UTC"
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
        assert 'updated > "2024-05-01 06:00"' in search.bodies[0]["jql"]

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
