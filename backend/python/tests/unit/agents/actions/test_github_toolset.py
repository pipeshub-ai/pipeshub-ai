"""
Unit tests for app.agents.actions.github.github

The GitHub toolset runs through the real GitHubDataSource and the real
PyGithub SDK. Only PyGithub's HTTP connection class is replaced, through the
SDK's own Requester.injectConnectionClasses hook, so every test sees the exact
method, path, query and JSON body GitHub would receive, and responses are
parsed into real PyGithub objects.
"""

import json
import re
from collections.abc import Iterator
from dataclasses import dataclass, field
from urllib.parse import parse_qs, urlsplit

import pytest
from github.Requester import Requester

from app.agents.actions.github.github import (
    GitHub,
    _github_commit_label,
    _github_review_label,
)
from app.sources.client.github.github import GitHubClient, GitHubClientViaToken

API = "https://api.github.com"


@dataclass
class RecordedRequest:
    method: str
    path: str
    query: dict[str, list[str]]
    body: object


class _Response:
    def __init__(self, status: int, payload: object) -> None:
        self.status = status
        self._text = json.dumps(payload)

    def getheaders(self) -> list[tuple[str, str]]:
        return [("content-type", "application/json; charset=utf-8")]

    def read(self) -> str:
        return self._text


@dataclass
class FakeGitHubAPI:
    """Replays canned JSON for PyGithub and records every request it makes."""

    requests: list[RecordedRequest] = field(default_factory=list)
    routes: list[tuple[str, re.Pattern, list[tuple[int, object]]]] = field(default_factory=list)

    def on(self, method: str, path_regex: str, *responses: tuple[int, object]) -> "FakeGitHubAPI":
        self.routes.append((method, re.compile(rf"^{path_regex}$"), list(responses)))
        return self

    def calls(self, method: str | None = None, path_regex: str | None = None) -> list[RecordedRequest]:
        return [
            r for r in self.requests
            if (method is None or r.method == method)
            and (path_regex is None or re.fullmatch(path_regex, r.path))
        ]

    def writes(self) -> list[RecordedRequest]:
        return [r for r in self.requests if r.method in {"POST", "PUT", "PATCH", "DELETE"}]

    def respond(self, verb: str, url: str, body: str | None) -> _Response:
        parts = urlsplit(url)
        recorded = RecordedRequest(verb, parts.path, parse_qs(parts.query), json.loads(body) if body else None)
        self.requests.append(recorded)
        for method, pattern, responses in self.routes:
            if method == verb and pattern.match(parts.path):
                status, payload = responses.pop(0) if len(responses) > 1 else responses[0]
                return _Response(status, payload)
        return _Response(404, {"message": "Not Found", "documentation_url": "https://docs.github.com/rest"})


class _FakeConnection:
    """Stands in for PyGithub's HTTPS connection class; `api` is set by the fixture."""

    api: FakeGitHubAPI

    def __init__(self, host: str, port: int | None = None, strict: bool = False, timeout: int | None = None,
                 retry: object = None, pool_size: int | None = None, **kwargs: object) -> None:
        self.host = host

    def request(self, verb: str, url: str, body: str | None, headers: dict[str, str], stream: bool = False) -> None:
        self._pending = (verb, url, body)

    def getresponse(self) -> _Response:
        return self.api.respond(*self._pending)

    def close(self) -> None:
        return None


@pytest.fixture
def api() -> Iterator[FakeGitHubAPI]:
    fake = FakeGitHubAPI()
    _FakeConnection.api = fake
    Requester.injectConnectionClasses(_FakeConnection, _FakeConnection)
    yield fake
    Requester.resetConnectionClasses()


@pytest.fixture
def github(api: FakeGitHubAPI) -> GitHub:
    client = GitHubClientViaToken("ghp_test")
    client.create_client()
    requester = client.get_sdk().requester
    # PyGithub spaces requests 0.25s apart (1s for writes); pointless against a fake.
    requester._Requester__seconds_between_requests = 0
    requester._Requester__seconds_between_writes = 0
    return GitHub(GitHubClient(client))


def ok(result: tuple[bool, str]) -> dict:
    success, payload = result
    assert success is True, f"expected success, got: {payload}"
    return json.loads(payload)


def err(result: tuple[bool, str]) -> str:
    success, payload = result
    assert success is False, f"expected failure, got success: {payload}"
    return json.loads(payload)["error"]


def repo(owner: str = "acme", name: str = "web") -> dict:
    return {
        "id": 1, "name": name, "full_name": f"{owner}/{name}", "private": True, "owner": {"login": owner},
        "url": f"{API}/repos/{owner}/{name}", "html_url": f"https://github.com/{owner}/{name}",
        "default_branch": "main", "node_id": "R_1", "permissions": {"admin": True}, "stargazers_count": 3,
    }


def issue(number: int, title: str = "Bug", *, is_pr: bool = False, owner: str = "acme", name: str = "web") -> dict:
    kind = "pull" if is_pr else "issues"
    return {
        "number": number, "title": title, "state": "open",
        "url": f"{API}/repos/{owner}/{name}/issues/{number}",
        "html_url": f"https://github.com/{owner}/{name}/{kind}/{number}",
        "user": {"login": "ann"}, "assignees": [], "labels": [],
    }


def pull(number: int, owner: str = "acme", name: str = "web") -> dict:
    return {
        "number": number, "title": "Add login", "state": "open",
        "url": f"{API}/repos/{owner}/{name}/pulls/{number}",
        "html_url": f"https://github.com/{owner}/{name}/pull/{number}",
        "head": {"ref": "feature"}, "base": {"ref": "main"},
    }


REPO_PATH = r"/repos/acme/web"


# ===========================================================================
# Owners and repositories
# ===========================================================================


class TestOwnersAndRepositories:
    @pytest.mark.asyncio
    async def test_me_reads_the_authenticated_user(self, github, api) -> None:
        api.on("GET", r"/user", (200, {"login": "ann", "id": 7, "url": f"{API}/users/ann"}))
        assert ok(await github.get_owner("me"))["data"]["login"] == "ann"
        assert [r.path for r in api.requests] == ["/user"]

    @pytest.mark.asyncio
    async def test_organization_owner(self, github, api) -> None:
        api.on("GET", r"/orgs/acme", (200, {"login": "acme", "url": f"{API}/orgs/acme"}))
        ok(await github.get_owner("acme", owner_type=" Organization "))
        assert api.requests[0].path == "/orgs/acme"

    @pytest.mark.asyncio
    async def test_unknown_owner_type_falls_back_to_user(self, github, api) -> None:
        api.on("GET", r"/users/bo", (200, {"login": "bo", "url": f"{API}/users/bo"}))
        ok(await github.get_owner("bo", owner_type="team"))
        assert api.requests[0].path == "/users/bo"

    @pytest.mark.asyncio
    async def test_missing_repository_reports_github_message(self, github, api) -> None:
        assert "Not Found" in err(await github.get_repository("acme", "nope"))

    @pytest.mark.asyncio
    async def test_bad_credentials_are_reported(self, github, api) -> None:
        api.on("GET", REPO_PATH, (401, {"message": "Bad credentials"}))
        assert "Bad credentials" in err(await github.get_repository("acme", "web"))

    @pytest.mark.asyncio
    async def test_create_repository_posts_defaults(self, github, api) -> None:
        api.on("GET", r"/user", (200, {"login": "ann", "url": f"{API}/users/ann"}))
        api.on("POST", r"/user/repos", (201, repo("ann", "notes")))
        data = ok(await github.create_repository("notes", description="Scratch"))
        assert api.calls("POST")[0].body == {"name": "notes", "private": True, "auto_init": True, "description": "Scratch"}
        assert data["data"]["full_name"] == "ann/notes"

    @pytest.mark.asyncio
    async def test_create_repository_name_clash_is_reported(self, github, api) -> None:
        api.on("GET", r"/user", (200, {"login": "ann", "url": f"{API}/users/ann"}))
        api.on("POST", r"/user/repos", (422, {"message": "Repository creation failed.", "errors": [{"message": "name already exists on this account"}]}))
        assert "name already exists" in err(await github.create_repository("notes"))

    @pytest.mark.asyncio
    async def test_list_repositories_pages_and_trims_payload(self, github, api) -> None:
        repos = [repo("ann", f"r{i}") for i in range(25)]
        api.on("GET", r"/users/ann", (200, {"login": "ann", "url": f"{API}/users/ann"}))
        api.on("GET", r"/users/ann/repos", (200, repos))
        data = ok(await github.list_repositories("ann", per_page=10, page=2))
        assert [r["name"] for r in data["data"]] == [f"r{i}" for i in range(10, 20)]
        assert "permissions" not in data["data"][0] and "node_id" not in data["data"][0]
        assert api.calls("GET", r"/users/ann/repos")[0].query["type"] == ["owner"]

    @pytest.mark.asyncio
    async def test_list_repositories_clamps_page_size(self, github, api) -> None:
        api.on("GET", r"/users/ann", (200, {"login": "ann", "url": f"{API}/users/ann"}))
        api.on("GET", r"/users/ann/repos", (200, [repo("ann", f"r{i}") for i in range(80)]))
        assert len(ok(await github.list_repositories("ann", per_page=500, page=0))["data"]) == 50

    @pytest.mark.asyncio
    async def test_search_repositories(self, github, api) -> None:
        api.on("GET", r"/search/repositories", (200, {"total_count": 1, "items": [repo("acme", "ml")]}))
        data = ok(await github.search_repositories("ml in:name", per_page=5))
        assert api.requests[0].query["q"] == ["ml in:name"]
        assert data["data"][0]["full_name"] == "acme/ml"


# ===========================================================================
# Issues
# ===========================================================================


class TestIssues:
    @pytest.mark.asyncio
    async def test_create_issue_posts_fields(self, github, api) -> None:
        api.on("GET", REPO_PATH, (200, repo()))
        api.on("POST", rf"{REPO_PATH}/issues", (201, issue(42, "Login fails")))
        data = ok(await github.create_issue("acme", "web", "Login fails", body="500 on submit", assignees=["ann"], labels=["bug"]))
        assert api.calls("POST")[0].body == {"title": "Login fails", "body": "500 on submit", "assignees": ["ann"], "labels": ["bug"]}
        assert data["data"]["number"] == 42

    @pytest.mark.asyncio
    async def test_get_issue(self, github, api) -> None:
        api.on("GET", REPO_PATH, (200, repo()))
        api.on("GET", rf"{REPO_PATH}/issues/42", (200, issue(42)))
        assert ok(await github.get_issue("acme", "web", 42))["data"]["title"] == "Bug"

    @pytest.mark.asyncio
    async def test_list_issues_excludes_pull_requests(self, github, api) -> None:
        api.on("GET", REPO_PATH, (200, repo()))
        api.on("GET", rf"{REPO_PATH}/issues", (200, [issue(1), issue(2, is_pr=True), issue(3)]))
        data = ok(await github.list_issues("acme", "web", state="all", labels=[], assignee="  "))
        assert [i["number"] for i in data["data"]] == [1, 3]
        query = api.calls("GET", rf"{REPO_PATH}/issues")[0].query
        assert query["state"] == ["all"]
        assert "assignee" not in query and "labels" not in query

    @pytest.mark.asyncio
    async def test_list_issues_second_page(self, github, api) -> None:
        api.on("GET", REPO_PATH, (200, repo()))
        api.on("GET", rf"{REPO_PATH}/issues", (200, [issue(n) for n in range(1, 8)]))
        data = ok(await github.list_issues("acme", "web", assignee="ann", per_page=3, page=2))
        assert [i["number"] for i in data["data"]] == [4, 5, 6]
        assert api.calls("GET", rf"{REPO_PATH}/issues")[0].query["assignee"] == ["ann"]

    @pytest.mark.asyncio
    async def test_close_issue_patches_state(self, github, api) -> None:
        api.on("GET", REPO_PATH, (200, repo()))
        api.on("GET", rf"{REPO_PATH}/issues/42", (200, issue(42)))
        api.on("PATCH", rf"{REPO_PATH}/issues/42", (200, {**issue(42), "state": "closed"}))
        ok(await github.close_issue("acme", "web", 42))
        assert api.writes()[0].body == {"state": "closed"}

    @pytest.mark.asyncio
    async def test_close_issue_returns_the_issue_as_closed(self, github, api) -> None:
        # PyGithub's edit() leaves raw_data as it was before the change.
        api.on("GET", REPO_PATH, (200, repo()))
        api.on("GET", rf"{REPO_PATH}/issues/42", (200, issue(42)), (200, {**issue(42), "state": "closed"}))
        api.on("PATCH", rf"{REPO_PATH}/issues/42", (200, {**issue(42), "state": "closed"}))
        assert ok(await github.close_issue("acme", "web", 42))["data"]["state"] == "closed"

    @pytest.mark.asyncio
    async def test_update_issue_returns_the_updated_issue(self, github, api) -> None:
        api.on("GET", REPO_PATH, (200, repo()))
        api.on("GET", rf"{REPO_PATH}/issues/42", (200, issue(42, "Bug")), (200, issue(42, "Renamed")))
        api.on("PATCH", rf"{REPO_PATH}/issues/42", (200, issue(42, "Renamed")))
        assert ok(await github.update_issue("acme", "web", 42, title="Renamed"))["data"]["title"] == "Renamed"

    @pytest.mark.asyncio
    async def test_close_is_reported_as_done_when_reloading_the_issue_fails(self, github, api) -> None:
        api.on("GET", REPO_PATH, (200, repo()))
        api.on("GET", rf"{REPO_PATH}/issues/42", (200, issue(42)), (502, {"message": "Server Error"}))
        api.on("PATCH", rf"{REPO_PATH}/issues/42", (200, {**issue(42), "state": "closed"}))
        data = ok(await github.close_issue("acme", "web", 42))["data"]
        assert (data["number"], data["state"]) == (42, "closed")
        assert "could not be reloaded" in data["note"]

    @pytest.mark.asyncio
    async def test_update_is_reported_as_done_when_reloading_the_issue_fails(self, github, api) -> None:
        api.on("GET", REPO_PATH, (200, repo()))
        api.on("GET", rf"{REPO_PATH}/issues/42", (200, issue(42, "Bug")), (502, {"message": "Server Error"}))
        api.on("PATCH", rf"{REPO_PATH}/issues/42", (200, issue(42, "Renamed")))
        data = ok(await github.update_issue("acme", "web", 42, title="Renamed"))["data"]
        assert data["title"] == "Renamed"
        assert "could not be reloaded" in data["note"]

    @pytest.mark.asyncio
    async def test_update_issue_sends_only_given_fields(self, github, api) -> None:
        api.on("GET", REPO_PATH, (200, repo()))
        api.on("GET", rf"{REPO_PATH}/issues/42", (200, issue(42)))
        api.on("PATCH", rf"{REPO_PATH}/issues/42", (200, issue(42, "Renamed")))
        ok(await github.update_issue("acme", "web", 42, title="Renamed", labels=["bug", "p1"]))
        assert api.writes()[0].body == {"title": "Renamed", "labels": ["bug", "p1"]}

    @pytest.mark.asyncio
    async def test_update_issue_can_clear_assignees(self, github, api) -> None:
        api.on("GET", REPO_PATH, (200, repo()))
        api.on("GET", rf"{REPO_PATH}/issues/42", (200, issue(42)))
        api.on("PATCH", rf"{REPO_PATH}/issues/42", (200, issue(42)))
        ok(await github.update_issue("acme", "web", 42, assignees=[]))
        assert api.writes()[0].body == {"assignees": []}

    @pytest.mark.asyncio
    async def test_update_issue_accepts_assignees_and_labels_as_returned_by_get_issue(self, github, api) -> None:
        api.on("GET", REPO_PATH, (200, repo()))
        api.on("GET", rf"{REPO_PATH}/issues/42", (200, issue(42)))
        api.on("PATCH", rf"{REPO_PATH}/issues/42", (200, issue(42)))
        ok(await github.update_issue(
            "acme", "web", 42, assignees=[{"login": "ann", "id": 1}], labels=[{"name": "bug", "color": "f00"}],
        ))
        assert api.writes()[0].body == {"assignees": ["ann"], "labels": ["bug"]}

    @pytest.mark.asyncio
    async def test_blank_title_is_left_unchanged(self, github, api) -> None:
        api.on("GET", REPO_PATH, (200, repo()))
        api.on("GET", rf"{REPO_PATH}/issues/42", (200, issue(42)))
        api.on("PATCH", rf"{REPO_PATH}/issues/42", (200, issue(42)))
        ok(await github.update_issue("acme", "web", 42, title="   ", state="closed"))
        assert api.writes()[0].body == {"state": "closed"}

    @pytest.mark.asyncio
    async def test_update_issue_with_nothing_to_change_is_refused(self, github, api) -> None:
        api.on("GET", REPO_PATH, (200, repo()))
        api.on("GET", rf"{REPO_PATH}/issues/42", (200, issue(42)))
        assert err(await github.update_issue("acme", "web", 42, title="")).startswith("No fields provided to update")
        assert api.requests == []

    @pytest.mark.asyncio
    async def test_blank_state_alone_is_nothing_to_change(self, github, api) -> None:
        assert err(await github.update_issue("acme", "web", 42, state="  ")).startswith("No fields provided to update")
        assert api.requests == []

    @pytest.mark.asyncio
    async def test_unknown_state_is_refused_before_github(self, github, api) -> None:
        assert err(await github.update_issue("acme", "web", 42, state="resolved")) == (
            "state 'resolved' is not valid. Use 'open' to reopen the issue or 'closed' to close it."
        )
        assert api.requests == []

    @pytest.mark.asyncio
    async def test_state_is_case_insensitive(self, github, api) -> None:
        api.on("GET", REPO_PATH, (200, repo()))
        api.on("GET", rf"{REPO_PATH}/issues/42", (200, issue(42)))
        api.on("PATCH", rf"{REPO_PATH}/issues/42", (200, {**issue(42), "state": "closed"}))
        ok(await github.update_issue("acme", "web", 42, state=" Closed "))
        assert api.writes()[0].body == {"state": "closed"}

    @pytest.mark.asyncio
    async def test_create_issue_accepts_label_objects(self, github, api) -> None:
        api.on("GET", REPO_PATH, (200, repo()))
        api.on("POST", rf"{REPO_PATH}/issues", (201, issue(43)))
        ok(await github.create_issue("acme", "web", "Crash", assignees=[{"login": "bo"}], labels=[{"name": "p1"}]))
        assert api.writes()[0].body == {"title": "Crash", "assignees": ["bo"], "labels": ["p1"]}

    @pytest.mark.asyncio
    async def test_update_issue_error_is_reported(self, github, api) -> None:
        api.on("GET", REPO_PATH, (200, repo()))
        api.on("GET", rf"{REPO_PATH}/issues/42", (200, issue(42)))
        api.on("PATCH", rf"{REPO_PATH}/issues/42", (403, {"message": "Must have admin rights to Repository."}))
        assert "admin rights" in err(await github.update_issue("acme", "web", 42, state="closed"))


class TestIssueComments:
    @pytest.mark.asyncio
    async def test_list_comments(self, github, api) -> None:
        api.on("GET", REPO_PATH, (200, repo()))
        api.on("GET", rf"{REPO_PATH}/issues/42", (200, issue(42)))
        api.on("GET", rf"{REPO_PATH}/issues/42/comments", (200, [{"id": 9, "body": "Same here", "user": {"login": "bo"}}]))
        assert ok(await github.list_issue_comments("acme", "web", 42))["data"][0]["body"] == "Same here"

    @pytest.mark.asyncio
    async def test_get_comment_by_id(self, github, api) -> None:
        api.on("GET", REPO_PATH, (200, repo()))
        api.on("GET", rf"{REPO_PATH}/issues/42", (200, issue(42)))
        api.on("GET", rf"{REPO_PATH}/issues/comments/9", (200, {"id": 9, "body": "Same here"}))
        assert ok(await github.get_issue_comment("acme", "web", 42, 9))["data"]["id"] == 9

    @pytest.mark.asyncio
    async def test_create_comment(self, github, api) -> None:
        api.on("GET", REPO_PATH, (200, repo()))
        api.on("GET", rf"{REPO_PATH}/issues/42", (200, issue(42)))
        api.on("POST", rf"{REPO_PATH}/issues/42/comments", (201, {"id": 10, "body": "Fixed in #43"}))
        ok(await github.create_issue_comment("acme", "web", 42, "Fixed in #43"))
        assert api.writes()[0].body == {"body": "Fixed in #43"}

    @pytest.mark.asyncio
    async def test_comment_on_locked_issue_is_reported(self, github, api) -> None:
        api.on("GET", REPO_PATH, (200, repo()))
        api.on("GET", rf"{REPO_PATH}/issues/42", (200, issue(42)))
        api.on("POST", rf"{REPO_PATH}/issues/42/comments", (403, {"message": "Unable to create comment because issue is locked."}))
        assert "issue is locked" in err(await github.create_issue_comment("acme", "web", 42, "x" * 150))


# ===========================================================================
# Pull requests
# ===========================================================================


class TestPullRequests:
    @pytest.mark.asyncio
    async def test_create_pull_request(self, github, api) -> None:
        api.on("GET", REPO_PATH, (200, repo()))
        api.on("POST", rf"{REPO_PATH}/pulls", (201, pull(7)))
        ok(await github.create_pull_request("acme", "web", "Add login", head="feature", base="main", draft=True))
        assert api.writes()[0].body == {"title": "Add login", "head": "feature", "base": "main", "draft": True}

    @pytest.mark.asyncio
    async def test_create_pull_request_without_commits_is_reported(self, github, api) -> None:
        api.on("GET", REPO_PATH, (200, repo()))
        api.on("POST", rf"{REPO_PATH}/pulls", (422, {"message": "Validation Failed", "errors": [{"message": "No commits between main and feature"}]}))
        assert "No commits between main and feature" in err(
            await github.create_pull_request("acme", "web", "Add login", head="feature", base="main")
        )

    @pytest.mark.asyncio
    async def test_get_pull_request_includes_conversation(self, github, api) -> None:
        api.on("GET", REPO_PATH, (200, repo()))
        api.on("GET", rf"{REPO_PATH}/pulls/7", (200, pull(7)))
        api.on("GET", rf"{REPO_PATH}/issues/7", (200, issue(7, is_pr=True)))
        api.on("GET", rf"{REPO_PATH}/issues/7/comments", (200, [{"id": 1, "body": "LGTM"}]))
        data = ok(await github.get_pull_request("acme", "web", 7))["data"]
        assert data["pr"]["head"]["ref"] == "feature"
        assert data["conversation_comments"] == [{"id": 1, "body": "LGTM"}]

    @pytest.mark.asyncio
    async def test_unreadable_conversation_is_flagged_not_shown_as_empty(self, github, api) -> None:
        api.on("GET", REPO_PATH, (200, repo()))
        api.on("GET", rf"{REPO_PATH}/pulls/7", (200, pull(7)))
        api.on("GET", rf"{REPO_PATH}/issues/7", (403, {"message": "API rate limit exceeded"}))
        payload = ok(await github.get_pull_request("acme", "web", 7))
        assert payload["data"]["pr"]["number"] == 7
        assert payload["data"]["conversation_comments"] == []
        assert "API rate limit exceeded" in payload["data"]["conversation_comments_error"]
        assert "could not be loaded" in payload["message"]

    @pytest.mark.asyncio
    async def test_missing_pull_request_is_reported(self, github, api) -> None:
        api.on("GET", REPO_PATH, (200, repo()))
        assert "Not Found" in err(await github.get_pull_request("acme", "web", 999))

    @pytest.mark.asyncio
    async def test_commits_expose_last_commit_sha(self, github, api) -> None:
        api.on("GET", REPO_PATH, (200, repo()))
        api.on("GET", rf"{REPO_PATH}/pulls/7", (200, pull(7)))
        api.on("GET", rf"{REPO_PATH}/pulls/7/commits", (200, [
            {"sha": "aaa111", "commit": {"message": "first"}}, {"sha": "bbb222", "commit": {"message": "second"}},
        ]))
        data = ok(await github.get_pull_request_commits("acme", "web", 7))
        assert data["length"] == 2
        assert data["last_commit_sha"] == "bbb222"

    @pytest.mark.asyncio
    async def test_pull_request_without_commits_has_no_last_sha(self, github, api) -> None:
        api.on("GET", REPO_PATH, (200, repo()))
        api.on("GET", rf"{REPO_PATH}/pulls/7", (200, pull(7)))
        api.on("GET", rf"{REPO_PATH}/pulls/7/commits", (200, []))
        data = ok(await github.get_pull_request_commits("acme", "web", 7))
        assert (data["length"], data["last_commit_sha"]) == (0, None)

    @pytest.mark.asyncio
    async def test_list_pull_requests_filters_by_branch(self, github, api) -> None:
        api.on("GET", REPO_PATH, (200, repo()))
        api.on("GET", rf"{REPO_PATH}/pulls", (200, [pull(7), pull(8)]))
        data = ok(await github.list_pull_requests("acme", "web", head=" acme:feature ", base="  ", per_page=1))
        query = api.calls("GET", rf"{REPO_PATH}/pulls")[0].query
        assert query["head"] == ["acme:feature"]
        assert "base" not in query
        assert [p["number"] for p in data["data"]] == [7]

    @pytest.mark.asyncio
    async def test_merge_sends_method_and_message(self, github, api) -> None:
        api.on("GET", REPO_PATH, (200, repo()))
        api.on("GET", rf"{REPO_PATH}/pulls/7", (200, pull(7)))
        api.on("PUT", rf"{REPO_PATH}/pulls/7/merge", (200, {"sha": "ccc333", "merged": True, "message": "Pull Request successfully merged"}))
        ok(await github.merge_pull_request("acme", "web", 7, commit_message="Ship", merge_method="squash"))
        assert api.writes()[0].body == {"commit_message": "Ship", "merge_method": "squash"}

    @pytest.mark.asyncio
    async def test_unmergeable_pull_request_is_reported(self, github, api) -> None:
        api.on("GET", REPO_PATH, (200, repo()))
        api.on("GET", rf"{REPO_PATH}/pulls/7", (200, pull(7)))
        api.on("PUT", rf"{REPO_PATH}/pulls/7/merge", (405, {"message": "Pull Request is not mergeable"}))
        assert "not mergeable" in err(await github.merge_pull_request("acme", "web", 7))

    @pytest.mark.asyncio
    async def test_file_changes_quick_overview(self, github, api) -> None:
        api.on("GET", REPO_PATH, (200, repo()))
        api.on("GET", rf"{REPO_PATH}/pulls/7", (200, pull(7)))
        api.on("GET", rf"{REPO_PATH}/pulls/7/files", (200, [
            {"filename": "app.py", "status": "modified", "additions": 1, "deletions": 1, "changes": 2,
             "patch": "@@ -1 +1 @@\n-old\n+new", "sha": "f1"},
        ]))
        data = ok(await github.get_pull_request_file_changes("acme", "web", 7, fetch_full_content=False))
        assert data["data"][0]["filename"] == "app.py"


class TestReviews:
    @pytest.mark.asyncio
    async def test_get_reviews(self, github, api) -> None:
        api.on("GET", REPO_PATH, (200, repo()))
        api.on("GET", rf"{REPO_PATH}/pulls/7", (200, pull(7)))
        api.on("GET", rf"{REPO_PATH}/pulls/7/reviews", (200, [{"id": 1, "state": "APPROVED", "user": {"login": "bo"}}]))
        assert ok(await github.get_pull_request_reviews("acme", "web", 7))["data"][0]["state"] == "APPROVED"

    @pytest.mark.asyncio
    async def test_submit_review(self, github, api) -> None:
        api.on("GET", REPO_PATH, (200, repo()))
        api.on("GET", rf"{REPO_PATH}/pulls/7", (200, pull(7)))
        api.on("POST", rf"{REPO_PATH}/pulls/7/reviews", (200, {"id": 2, "state": "CHANGES_REQUESTED"}))
        ok(await github.create_pull_request_review("acme", "web", 7, event="REQUEST_CHANGES", body="Add tests"))
        assert api.writes()[0].body == {"event": "REQUEST_CHANGES", "body": "Add tests", "comments": []}

    @pytest.mark.asyncio
    async def test_review_comments(self, github, api) -> None:
        api.on("GET", REPO_PATH, (200, repo()))
        api.on("GET", rf"{REPO_PATH}/pulls/7", (200, pull(7)))
        api.on("GET", rf"{REPO_PATH}/pulls/7/comments", (200, [{"id": 5, "path": "app.py", "line": 3, "body": "nit"}]))
        assert ok(await github.list_pull_request_comments("acme", "web", 7))["data"][0]["path"] == "app.py"

    @pytest.mark.asyncio
    async def test_line_comment_posts_commit_path_and_line(self, github, api) -> None:
        api.on("GET", REPO_PATH, (200, repo()))
        api.on("GET", rf"{REPO_PATH}/pulls/7", (200, pull(7)))
        api.on("POST", rf"{REPO_PATH}/pulls/7/comments", (201, {"id": 6, "body": "Use a constant"}))
        ok(await github.create_pull_request_review_comment(
            "acme", "web", 7, body="Use a constant", commit_id="bbb222", path="app.py", line=3, side="RIGHT",
        ))
        assert api.writes()[0].body == {"body": "Use a constant", "commit_id": "bbb222", "path": "app.py", "line": 3, "side": "RIGHT"}


# ===========================================================================
# Helpers
# ===========================================================================


class TestLabels:
    def test_commit_label_uses_short_sha_and_first_line(self) -> None:
        assert _github_commit_label({"sha": "abcdef123", "commit": {"message": "Fix\n\nbody"}}) == "abcdef1: Fix"

    def test_review_label(self) -> None:
        assert _github_review_label({"user": {"login": "bo"}, "state": "APPROVED"}) == "bo: APPROVED"
