"""Jira Cloud Personal: removing what the account can no longer see, over a fake Jira site.

The connector, Jira client, request builder and HTTP client are real; every HTTP
request is answered by ``FakeJiraSite``, which keeps projects and issues in memory
and answers the way Jira Cloud does for one account. Our databases are in-memory fakes.
"""

import json
import logging
from typing import Any
from urllib.parse import parse_qs, urlparse

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
    CloudRecordsDb,
    RecordingNotifications,
    oauth_config,
    one_site,
    route_every_http_client,
)

from app.connectors.sources.atlassian.jira_cloud_personal.connector import (
    JiraCloudPersonalConnector,
)
from app.models.entities import TicketRecord

CONNECTOR_ID = "jira-personal-1"
JIRA = f"/ex/jira/{CLOUD_ID}/rest/api/3"
ID_FIELDS = ["id"]
ATTACHMENT = {"id": "900", "filename": "log.txt", "mimeType": "text/plain", "size": 12, "created": "2024-05-02T15:00:00.000+0000"}
NOT_FOUND = {"errorMessages": ["Issue does not exist or you do not have permission to see it."]}


class FakeJiraSite:
    """A Jira Cloud site as one account sees it.

    Like Jira, it answers 404 both for what was deleted and for what the account
    can't see, refuses (400) to search a project the account can't see, and treats
    a token it no longer accepts as an anonymous visitor: no projects, 404 for every
    issue and project, 401 only for ``/myself``.
    """

    def __init__(self, api: AtlassianApiStub) -> None:
        self.api = api
        self.signed_in = True
        self.projects: dict[str, dict[str, str]] = {}
        self.issues: dict[str, dict[str, Any]] = {}
        self.hidden_projects: set[str] = set()
        self.hidden_issues: set[str] = set()
        # Still answered by id, but left out of every search (moved, or not yet in the search index).
        self.unsearchable_issues: set[str] = set()
        # In no project list, but still answered by id.
        self.unlisted_projects: set[str] = set()
        # Ids the incremental search (``updated > ...``) returns.
        self.changed_issues: set[str] = set()
        # Staged pages of a project's id listing, by page token; unset, every visible id comes in one page.
        self.id_pages: dict[str, dict[str | None, Any]] = {}
        self.failing_issue_search: set[str] = set()
        self.project_answers: dict[str, httpx.Response] = {}
        self.id_bodies: list[dict[str, Any]] = []
        self.search_bodies: list[dict[str, Any]] = []
        api.on("GET", f"{JIRA}/myself", self._myself)
        api.on("GET", f"{JIRA}/project/search", self._project_search)
        api.on("POST", f"{JIRA}/search/jql", self._search)

    def add_project(self, key: str, project_id: str) -> None:
        self.projects[key] = {"id": project_id, "key": key, "name": f"{key} project"}
        self.api.on("GET", f"{JIRA}/project/{project_id}", lambda _r: self._project(key))

    def add_issue(self, num: int, project: str, created: str = "2024-05-01T09:00:00.000+0000", attachments: list | None = None) -> None:
        fields: dict[str, Any] = {
            "summary": f"Issue {num}",
            "issuetype": {"name": "Task"},
            "status": {"name": "To Do"},
            "priority": {"name": "Medium"},
            "created": created,
            "updated": f"2024-05-02T10:{num:02d}:00.000+0000",
            "project": {"id": self.projects[project]["id"], "key": project},
        }
        if attachments:
            fields["attachment"] = attachments
        self.issues[str(num)] = {"id": str(num), "key": f"{project}-{num}", "fields": fields}
        self.api.on("GET", f"{JIRA}/issue/{num}", lambda _r: self._issue(str(num)))

    def _visible_project(self, key: str) -> bool:
        return self.signed_in and key in self.projects and key not in self.hidden_projects

    def _visible_issue(self, issue_id: str) -> bool:
        ticket = self.issues.get(issue_id)
        return (
            ticket is not None and issue_id not in self.hidden_issues
            and self._visible_project(ticket["fields"]["project"]["key"])
        )

    def _myself(self, _request: httpx.Request) -> httpx.Response:
        if not self.signed_in:
            return json_response({"message": "Client must be authenticated to access this resource."}, status=401)
        return json_response({"accountId": "me", "emailAddress": "me@acme.com", "timeZone": "UTC"})

    def _project_search(self, request: httpx.Request) -> httpx.Response:
        wanted = parse_qs(urlparse(str(request.url)).query).get("keys")
        listed = [
            p for key, p in self.projects.items()
            if self._visible_project(key) and key not in self.unlisted_projects and (not wanted or key in wanted)
        ]
        return json_response({"values": listed, "isLast": True, "total": len(listed)})

    def _project(self, key: str) -> httpx.Response:
        if key in self.project_answers:
            return self.project_answers[key]
        if not self._visible_project(key):
            return json_response({"errorMessages": [f"No project could be found with key '{key}'."]}, status=404)
        return json_response(self.projects[key])

    def _issue(self, issue_id: str) -> httpx.Response:
        if not self._visible_issue(issue_id):
            return json_response(NOT_FOUND, status=404)
        return json_response({"id": issue_id, "key": self.issues[issue_id]["key"]})

    def _search(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        key = body["jql"].split('project = "')[1].split('"')[0]
        listing_ids = body.get("fields") == ID_FIELDS
        (self.id_bodies if listing_ids else self.search_bodies).append(body)
        if not self._visible_project(key):
            return json_response(
                {"errorMessages": [f"The value '{key}' does not exist for the field 'project'."]}, status=400,
            )
        found = [
            ticket for issue_id, ticket in self.issues.items()
            if ticket["fields"]["project"]["key"] == key and self._visible_issue(issue_id)
            and issue_id not in self.unsearchable_issues
        ]
        if listing_ids:
            if key in self.id_pages:
                page = self.id_pages[key].get(body.get("nextPageToken"), {"issues": []})
                return page if isinstance(page, httpx.Response) else json_response(page)
            return json_response({"issues": [{"id": t["id"]} for t in found], "isLast": True})
        if key in self.failing_issue_search:
            return json_response({"errorMessages": ["boom"]}, status=500)
        if "updated >" in body["jql"]:
            found = [t for t in found if t["id"] in self.changed_issues]
        return json_response({"issues": found})

    def project_reads(self, project_id: str) -> int:
        return len(self.api.calls("GET", f"{JIRA}/project/{project_id}"))

    def issue_reads(self, num: int) -> int:
        return len(self.api.calls("GET", f"{JIRA}/issue/{num}"))


class PersonalDb(CloudRecordsDb):
    def __init__(self) -> None:
        super().__init__()
        self.fail_group_read = False
        self.groups_kept_for_the_trash: set[str] = set()

    async def get_nodes_by_filters(
        self, collection: str, filters: dict[str, Any], return_fields: list[str] | None = None
    ) -> list[dict[str, Any]]:
        """Record group nodes; like both graph providers, a failed read answers [] instead of raising."""
        assert collection == "recordGroups", collection
        if self.fail_group_read:
            return []
        nodes = [
            {
                **g.to_arango_base_record_group(), "id": g.id,
                "isDeletedAtSource": g.external_group_id in self.groups_kept_for_the_trash,
            }
            for g in self.record_groups.values()
        ]
        matching = [n for n in nodes if all(n.get(k) == v for k, v in filters.items())]
        return [{f: n.get(f) for f in return_fields} if return_fields else n for n in matching]


@pytest.fixture
def db() -> PersonalDb:
    return PersonalDb()


@pytest.fixture
def site(monkeypatch: pytest.MonkeyPatch) -> FakeJiraSite:
    """ENG holds issues 1, 2 (with an attachment) and 3; OPS holds issue 5."""
    api = AtlassianApiStub()
    route_every_http_client(monkeypatch, api)
    one_site(api)
    jira = FakeJiraSite(api)
    jira.add_project("ENG", "10000")
    jira.add_project("OPS", "10001")
    jira.add_issue(1, "ENG")
    jira.add_issue(2, "ENG", attachments=[ATTACHMENT])
    jira.add_issue(3, "ENG")
    jira.add_issue(5, "OPS")
    return jira


def tickets(db: PersonalDb) -> set[str]:
    return {k for k, r in db.records.items() if isinstance(r, TicketRecord)}


def project_filter(operator: str, keys: list[str]) -> dict[str, Any]:
    return {"sync": {"values": {"project_keys": {"operator": operator, "type": "list", "value": keys}}}}


async def synced(
    db: PersonalDb, checkpoints: FakeCheckpointStore, filters: dict[str, Any] | None = None,
) -> tuple[JiraCloudPersonalConnector, FakeConfigService]:
    """A connector whose first sync has stored every issue of the site."""
    config_service = FakeConfigService(CONNECTOR_ID, oauth_config())
    if filters:
        config_service.config["filters"] = filters
    connector = JiraCloudPersonalConnector(
        logging.getLogger("test.jira_cloud_personal"), db, checkpoints, config_service, CONNECTOR_ID, "personal", "creator-1",
    )
    connector._notification_service = RecordingNotifications()
    assert await connector.init() is True
    await connector.run_sync()
    assert tickets(db) == {"1", "2", "3", "5"} and "attachment_900" in db.records
    return connector, config_service


class TestDeletedIssues:
    async def test_an_issue_deleted_in_jira_is_removed_with_its_attachment_by_the_next_sync(self, site, db, checkpoints) -> None:
        connector, _ = await synced(db, checkpoints)
        del site.issues["2"]
        site.unsearchable_issues.add("3")

        await connector.run_sync()

        assert tickets(db) == {"1", "3", "5"}
        assert "attachment_900" not in db.records, "the attachment goes with its issue"
        assert site.issue_reads(3) == 1, "an issue Jira still has (moved, or not yet searchable) is checked and stays"
        assert site.issue_reads(1) == 0 and site.issue_reads(5) == 0, "listed issues are not checked"
        eng_listings = [b for b in site.id_bodies if '"ENG"' in b["jql"]]
        assert len(eng_listings) == 2, "one listing per project per sync"
        assert eng_listings[-1]["jql"] == 'project = "ENG" ORDER BY id ASC'
        assert eng_listings[-1]["maxResults"] == 5000 and "expand" not in eng_listings[-1]
        assert site.api.calls("GET", f"{JIRA}/auditing/record") == [], "a personal account is never asked for the audit log"

    async def test_the_first_sync_after_a_full_resync_removes_deleted_issues(self, site, db, checkpoints) -> None:
        connector, _ = await synced(db, checkpoints)
        del site.issues["2"]
        checkpoints.sync_points.clear()  # a full resync deletes every sync point, not the records

        await connector.run_sync()

        assert tickets(db) == {"1", "3", "5"}
        assert "attachment_900" not in db.records

    @pytest.mark.parametrize("broken_listing", [
        pytest.param({None: {"issues": [{"id": "1"}], "nextPageToken": "T2"}, "T2": json_response({"errorMessages": ["boom"]}, status=500)}, id="a-later-page-fails"),
        pytest.param({None: {"issues": [{"id": "1"}], "nextPageToken": "T2"}, "T2": {"issues": [{"id": "1"}], "nextPageToken": "T3"}}, id="the-pages-repeat"),
        pytest.param({None: {"issues": [{"id": "1"}], "isLast": False}}, id="more-pages-but-no-token"),
        pytest.param({None: {"issues": [{"key": "ENG-1"}], "isLast": True}}, id="an-issue-without-an-id"),
        pytest.param({None: json_response({"errorMessages": ["no"]}, status=400)}, id="the-first-page-fails"),
    ])
    async def test_an_unfinished_listing_removes_nothing_and_a_later_full_one_does(
        self, site, db, checkpoints, broken_listing
    ) -> None:
        connector, _ = await synced(db, checkpoints)
        del site.issues["2"]
        site.id_pages["ENG"] = dict(broken_listing)

        await connector.run_sync()

        assert tickets(db) == {"1", "2", "3", "5"}
        assert site.issue_reads(2) == site.issue_reads(3) == 0

        site.id_pages["ENG"] = {None: {"issues": [{"id": "1"}], "nextPageToken": "T2"}, "T2": {"issues": [{"id": "3"}], "isLast": True}}
        await connector.run_sync()

        assert tickets(db) == {"1", "3", "5"}

    async def test_an_issue_a_narrowed_date_filter_leaves_out_is_not_removed(self, site, db, checkpoints) -> None:
        site.issues["1"]["fields"]["created"] = "2024-06-01T09:00:00.000+0000"
        site.issues["2"]["fields"]["created"] = "2024-06-01T09:00:00.000+0000"
        connector, config_service = await synced(db, checkpoints)
        config_service.config["filters"] = {
            "sync": {"values": {"created": {"operator": "is_after", "type": "datetime", "value": {"start": 1_715_731_200_000}}}},
        }
        del site.issues["2"]

        await connector.run_sync()

        assert tickets(db) == {"1", "3", "5"}, "issue 3 was created before the new cutoff, and Jira still has it"
        assert site.issue_reads(3) == 0
        assert all("created" in b["jql"] for b in site.search_bodies[-2:]), "the issue search applies the narrowed filter"
        assert all("created" not in b["jql"] for b in site.id_bodies), "the id listing does not"

    async def test_a_project_whose_issue_sync_failed_is_not_compared_in_that_sync(self, site, db, checkpoints) -> None:
        connector, _ = await synced(db, checkpoints)
        del site.issues["2"]
        site.failing_issue_search.add("ENG")
        listings_before = len(site.id_bodies)

        await connector.run_sync()

        assert tickets(db) == {"1", "2", "3", "5"}
        assert [b["jql"] for b in site.id_bodies[listings_before:]] == ['project = "OPS" ORDER BY id ASC']

        site.failing_issue_search.clear()
        await connector.run_sync()

        assert tickets(db) == {"1", "3", "5"}


class TestLostAccess:
    async def test_an_issue_the_account_can_no_longer_see_is_removed_and_returns_when_it_changes(
        self, site, db, checkpoints
    ) -> None:
        connector, _ = await synced(db, checkpoints)
        site.hidden_issues.add("2")

        await connector.run_sync()

        assert tickets(db) == {"1", "3", "5"}
        assert "attachment_900" not in db.records

        site.hidden_issues.clear()
        site.changed_issues.add("2")
        await connector.run_sync()

        assert tickets(db) == {"1", "2", "3", "5"}

    async def test_a_token_jira_no_longer_accepts_removes_nothing(self, site, db, checkpoints) -> None:
        connector, _ = await synced(db, checkpoints)
        site.signed_in = False

        await connector.run_sync()

        assert tickets(db) == {"1", "2", "3", "5"}, "to an anonymous visitor everything looks gone; none of it is"
        assert set(db.record_groups) == {"10000", "10001"}
        assert site.project_reads("10000") == 0 and site.issue_reads(1) == 0
        assert checkpoints.values_for("project_ENG"), "the project is not read from the start again either"

        site.signed_in = True
        await connector.run_sync()

        assert tickets(db) == {"1", "2", "3", "5"}


class TestProjectsOutOfView:
    async def test_a_project_the_account_can_no_longer_see_is_removed_with_its_issues(self, site, db, checkpoints) -> None:
        connector, _ = await synced(db, checkpoints)
        site.hidden_projects.add("ENG")
        listings_before = len(site.id_bodies)

        await connector.run_sync()

        assert tickets(db) == {"5"}
        assert "attachment_900" not in db.records
        assert set(db.record_groups) == {"10001"}, "the emptied project goes too"
        assert checkpoints.values_for("project_ENG") is None
        assert site.project_reads("10000") == 1
        assert [site.issue_reads(n) for n in (1, 2, 3)] == [1, 1, 1], "each issue is still confirmed with Jira"
        assert all('"ENG"' not in b["jql"] for b in site.id_bodies[listings_before:]), (
            "Jira refuses to search a project the account can't see, so its ids are not listed"
        )

        await connector.run_sync()

        assert site.project_reads("10000") == 1, "nothing is left to ask about"

    async def test_a_project_that_comes_back_into_view_is_read_in_full_again(self, site, db, checkpoints) -> None:
        connector, _ = await synced(db, checkpoints)
        site.hidden_projects.add("ENG")
        await connector.run_sync()
        assert tickets(db) == {"5"}

        site.hidden_projects.clear()
        await connector.run_sync()

        assert tickets(db) == {"1", "2", "3", "5"} and "attachment_900" in db.records
        assert "10000" in db.record_groups

    async def test_a_deleted_project_is_removed_like_a_hidden_one(self, site, db, checkpoints) -> None:
        connector, _ = await synced(db, checkpoints)
        del site.projects["OPS"]

        await connector.run_sync()

        assert tickets(db) == {"1", "2", "3"}
        assert set(db.record_groups) == {"10000"}

    async def test_a_filtered_in_project_that_left_the_view_is_removed(self, site, db, checkpoints) -> None:
        connector, _ = await synced(db, checkpoints, project_filter("in", ["ENG", "OPS"]))
        site.hidden_projects.add("ENG")

        await connector.run_sync()

        assert tickets(db) == {"5"}

    @pytest.mark.parametrize("narrowed", [
        pytest.param(project_filter("in", ["OPS"]), id="kept-projects-no-longer-name-it"),
        pytest.param(project_filter("not_in", ["ENG"]), id="left-out-by-name"),
    ])
    async def test_a_project_the_project_filter_now_leaves_out_is_not_touched(
        self, site, db, checkpoints, narrowed
    ) -> None:
        connector, config_service = await synced(db, checkpoints)
        config_service.config["filters"] = narrowed
        site.hidden_projects.add("ENG")

        await connector.run_sync()

        assert tickets(db) == {"1", "2", "3", "5"}
        assert site.project_reads("10000") == 0 and site.issue_reads(1) == 0
        assert checkpoints.values_for("project_ENG")

    async def test_a_project_missing_from_the_list_that_jira_still_answers_for_is_kept(self, site, db, checkpoints) -> None:
        connector, _ = await synced(db, checkpoints)
        site.unlisted_projects.add("ENG")

        await connector.run_sync()

        assert tickets(db) == {"1", "2", "3", "5"}
        assert site.project_reads("10000") == 1
        assert [site.issue_reads(n) for n in (1, 2, 3)] == [0, 0, 0]
        assert checkpoints.values_for("project_ENG")

    async def test_a_project_check_that_fails_removes_nothing_and_a_later_one_does(self, site, db, checkpoints) -> None:
        connector, _ = await synced(db, checkpoints)
        site.hidden_projects.add("ENG")
        site.project_answers["ENG"] = json_response({"errorMessages": ["busy"]}, status=503)

        await connector.run_sync()

        assert tickets(db) == {"1", "2", "3", "5"}
        assert [site.issue_reads(n) for n in (1, 2, 3)] == [0, 0, 0]

        site.project_answers.clear()
        await connector.run_sync()

        assert tickets(db) == {"5"}

    async def test_an_issue_jira_still_answers_for_keeps_its_hidden_project(self, site, db, checkpoints) -> None:
        connector, _ = await synced(db, checkpoints)
        site.hidden_projects.add("ENG")
        site.api.on("GET", f"{JIRA}/issue/3", {"id": "3", "key": "OPS-3"})

        await connector.run_sync()

        assert tickets(db) == {"3", "5"}
        assert "10000" in db.record_groups, "a project that still holds a record is kept"

    @pytest.mark.parametrize(("jira_still_has_the_parent", "left"), [
        pytest.param(False, set(), id="a-parent-jira-no-longer-shows-goes-with-the-project"),
        pytest.param(True, {"9"}, id="a-parent-jira-still-answers-for-keeps-the-project"),
    ])
    async def test_a_placeholder_parent_in_a_hidden_project_is_checked_like_its_issues(
        self, site, db, checkpoints, jira_still_has_the_parent, left
    ) -> None:
        connector, _ = await synced(db, checkpoints)
        db.records["9"] = db.records["1"].model_copy(update={"id": "stub-9", "external_record_id": "9", "is_placeholder": True})
        site.add_issue(9, "ENG")
        site.hidden_projects.add("ENG")
        if jira_still_has_the_parent:
            site.api.on("GET", f"{JIRA}/issue/9", {"id": "9", "key": "OPS-9"})

        await connector.run_sync()

        assert site.issue_reads(9) == 1
        assert tickets(db) == {"5"} | left
        assert ("10000" in db.record_groups) is jira_still_has_the_parent

    async def test_a_placeholder_parent_in_a_project_still_in_view_is_left_alone(self, site, db, checkpoints) -> None:
        connector, _ = await synced(db, checkpoints)
        db.records["9"] = db.records["1"].model_copy(update={"id": "stub-9", "external_record_id": "9", "is_placeholder": True})

        await connector.run_sync()

        assert tickets(db) == {"1", "2", "3", "5", "9"}
        assert site.issue_reads(9) == 0

    async def test_an_issue_whose_check_fails_is_kept_and_removed_by_a_later_sync(self, site, db, checkpoints) -> None:
        connector, _ = await synced(db, checkpoints)
        site.hidden_projects.add("ENG")
        site.api.on("GET", f"{JIRA}/issue/3", [json_response({"errorMessages": ["busy"]}, status=503), json_response(NOT_FOUND, status=404)])

        await connector.run_sync()

        assert tickets(db) == {"3", "5"}
        assert "10000" in db.record_groups

        await connector.run_sync()

        assert tickets(db) == {"5"}
        assert set(db.record_groups) == {"10001"}

    async def test_a_failed_read_of_the_stored_projects_removes_nothing_and_a_later_one_does(self, site, db, checkpoints) -> None:
        connector, _ = await synced(db, checkpoints)
        site.hidden_projects.add("ENG")
        db.fail_group_read = True

        await connector.run_sync()

        assert tickets(db) == {"1", "2", "3", "5"}

        db.fail_group_read = False
        await connector.run_sync()

        assert tickets(db) == {"5"}

    async def test_a_project_kept_only_for_its_records_in_the_trash_is_not_asked_about_again(self, site, db, checkpoints) -> None:
        connector, _ = await synced(db, checkpoints)
        site.hidden_projects.add("ENG")
        db.groups_kept_for_the_trash.add("10000")

        await connector.run_sync()

        assert site.project_reads("10000") == 0
