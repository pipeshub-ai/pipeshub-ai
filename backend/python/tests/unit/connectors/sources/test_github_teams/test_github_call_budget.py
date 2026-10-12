"""How many GitHub API calls indexing spends per record.

GitHub's core allowance is 5,000 requests an hour per token, shared with
everything else that token does, so the calls each streamed record costs
decide how much a sync can index in an hour.

These tests run the real streaming code (``RuntimeHelper``, ``PullRequestsSync``,
``IssuesSync``, ``CommentsHelper``, ``ReposSync``) against a fake data source
that counts every request by endpoint. They pin two things:

- the per-PR breakdown, so a change that adds a request per file or per
  comment shows up here rather than on someone's rate-limit dashboard;
- that the repository behind a record is looked up once, not once per record.
  Records name their repository only by numeric id, and each one used to pay a
  ``GET /repositories/{id}`` before its real request: one extra call per PR or
  issue, and half of every code file's cost.
"""
from __future__ import annotations

import base64
from collections import Counter
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app.connectors.sources.github_teams.comments import CommentsHelper
from app.connectors.sources.github_teams.issues import IssuesSync
from app.connectors.sources.github_teams.pull_requests import PullRequestsSync
from app.connectors.sources.github_teams.repos import ReposSync
from app.connectors.sources.github_teams.runtime import RuntimeHelper
from app.models.entities import CodeFileRecord, PullRequestRecord, TicketRecord
from app.sources.client.github.github import GitHubResponse
from tests.unit.connectors.sources.test_github_teams.conftest import (
    make_mock_connector,
    make_repo,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

pytestmark = pytest.mark.anyio


@pytest.fixture()
def anyio_backend() -> str:
    return "asyncio"


_WHEN = datetime(2026, 10, 1, tzinfo=timezone.utc)


def _user(login: str = "octocat") -> SimpleNamespace:
    return SimpleNamespace(login=login, id=7)


class CountingGitHub:
    """A data source that answers like GitHub and counts each request."""

    def __init__(self, *, files: int, commits: int = 2, comments: int = 1) -> None:
        self.calls: Counter[str] = Counter()
        self.repo = make_repo(repo_id=10)
        self._files = files
        self._commits = commits
        self._comments = comments

    def _ok(self, name: str, data: object) -> GitHubResponse:
        self.calls[name] += 1
        return GitHubResponse(success=True, data=data)

    async def get_repo_by_id(self, repo_id: int) -> GitHubResponse:
        return self._ok("get_repo_by_id", self.repo)

    async def get_pull(self, owner: str, repo: str, number: int) -> GitHubResponse:
        return self._ok("get_pull", SimpleNamespace(
            number=number, title=f"PR {number}", body="Fixes the thing.",
            updated_at=_WHEN, html_url=f"https://github.com/acme/widgets/pull/{number}",
            head=SimpleNamespace(sha="head-sha"),
        ))

    async def get_pull_commits(self, owner: str, repo: str, number: int) -> GitHubResponse:
        return self._ok("get_pull_commits", [
            SimpleNamespace(
                sha=f"c{i}", html_url=None,
                commit=SimpleNamespace(message=f"commit {i}", committer=SimpleNamespace(date=_WHEN)),
            )
            for i in range(self._commits)
        ])

    async def list_issue_comments(self, owner: str, repo: str, number: int, since: datetime | None = None) -> GitHubResponse:
        return self._ok("list_issue_comments", [
            SimpleNamespace(id=i, body=f"comment {i}", user=_user(), created_at=_WHEN,
                            updated_at=_WHEN, html_url=None)
            for i in range(self._comments)
        ])

    async def get_pull_reviews(self, owner: str, repo: str, number: int) -> GitHubResponse:
        return self._ok("get_pull_reviews", [])

    async def get_pull_review_comments(self, owner: str, repo: str, number: int) -> GitHubResponse:
        return self._ok("get_pull_review_comments", [])

    async def get_pull_file_changes(self, owner: str, repo: str, number: int, fetch_full_content: bool = True) -> GitHubResponse:
        return self._ok("get_pull_file_changes", [
            SimpleNamespace(filename=f"src/f{i}.py", status="modified", patch="@@ -1 +1 @@\n-a\n+b")
            for i in range(self._files)
        ])

    async def get_file_contents(self, owner: str, repo: str, path: str, ref: str | None = None) -> GitHubResponse:
        return self._ok("get_file_contents", SimpleNamespace(
            path=path, size=12, encoding="base64", sha="blob-sha",
            content=base64.b64encode(b"print('hi')\n").decode(), decoded_content=b"print('hi')\n",
        ))

    async def get_issue(self, owner: str, repo: str, number: int) -> GitHubResponse:
        return self._ok("get_issue", SimpleNamespace(
            number=number, title=f"Issue {number}", body="It broke.", updated_at=_WHEN,
            html_url=f"https://github.com/acme/widgets/issues/{number}",
        ))


def _connector(gh: CountingGitHub) -> MagicMock:
    """A connector whose GitHub-facing helpers are the real ones."""
    c = make_mock_connector()
    c._github_executor = None
    c.data_source = gh
    c.runtime = RuntimeHelper(c)
    c.comments = CommentsHelper(c)
    c.pull_requests = PullRequestsSync(c)
    c.repos = ReposSync(c)
    issues = IssuesSync(c)
    issues.process_new_records = AsyncMock(return_value=True)
    c.issues = issues
    return c


def _pr_record(number: int) -> PullRequestRecord:
    return PullRequestRecord(
        id=f"pr-{number}", org_id="org-1", record_name=f"PR {number}", record_type="PULL_REQUEST",
        version=0, origin="CONNECTOR", connector_name="GITHUB TEAMS", connector_id="c-1",
        external_record_id=f"10/pull/{number}", external_record_group_id="10-pull-requests",
        weburl=f"https://github.com/acme/widgets/pull/{number}",
    )


def _ticket_record(number: int) -> TicketRecord:
    return TicketRecord(
        id=f"is-{number}", org_id="org-1", record_name=f"Issue {number}", record_type="TICKET",
        version=0, origin="CONNECTOR", connector_name="GITHUB TEAMS", connector_id="c-1",
        external_record_id=f"10/issues/{number}", external_record_group_id="10-issues",
        weburl=f"https://github.com/acme/widgets/issues/{number}",
    )


def _code_record(path: str) -> CodeFileRecord:
    return CodeFileRecord(
        id=f"code-{path}", org_id="org-1", record_name=path, record_type="CODE_FILE",
        version=0, origin="CONNECTOR", connector_name="GITHUB TEAMS", connector_id="c-1",
        external_record_id=f"10/{path}", external_record_group_id="10-code",
        file_path=path, file_hash="blob-sha", is_file=True,
    )


class TestPullRequestCallBudget:
    async def test_one_pull_request_costs_seven_calls_plus_one_per_changed_file(self) -> None:
        # The breakdown, for anyone wondering where a PR's calls go. Every
        # listing is one page here; each extra 100 items costs one more call.
        gh = CountingGitHub(files=5)
        c = _connector(gh)

        await c.pull_requests.build_pull_request_blocks(_pr_record(1))

        assert gh.calls == Counter({
            "get_repo_by_id": 1,
            "get_pull": 1,
            "get_pull_commits": 1,
            "list_issue_comments": 1,
            "get_pull_reviews": 1,
            "get_pull_review_comments": 1,
            "get_pull_file_changes": 1,
            # The full text of every changed file is indexed, one request each.
            "get_file_contents": 5,
        })

    async def test_pull_requests_in_one_repository_look_it_up_once(self) -> None:
        gh = CountingGitHub(files=5)
        c = _connector(gh)

        for number in range(1, 11):
            await c.pull_requests.build_pull_request_blocks(_pr_record(number))

        assert gh.calls["get_repo_by_id"] == 1
        # Nothing else changed: each PR still fetches its own content.
        assert gh.calls["get_pull"] == 10
        assert gh.calls["get_file_contents"] == 50


class TestIssueCallBudget:
    async def test_issues_in_one_repository_look_it_up_once(self) -> None:
        gh = CountingGitHub(files=0)
        c = _connector(gh)

        for number in range(1, 11):
            await c.issues.build_ticket_blocks(_ticket_record(number))

        assert gh.calls["get_repo_by_id"] == 1
        assert gh.calls["get_issue"] == 10
        assert gh.calls["list_issue_comments"] == 10


class TestCodeFileCallBudget:
    async def test_code_files_in_one_repository_look_it_up_once(self) -> None:
        # A code file is otherwise a single request, so the lookup was half
        # of what every code file cost to index.
        gh = CountingGitHub(files=0)
        c = _connector(gh)

        for i in range(10):
            await c.repos.fetch_code_file_content(_code_record(f"src/f{i}.py"))

        assert gh.calls == Counter({"get_repo_by_id": 1, "get_file_contents": 10})


class TestRepositoryLookupCache:
    async def test_a_failed_lookup_is_not_remembered(self) -> None:
        # A transient failure must not stick: the next record asks again.
        gh = CountingGitHub(files=0)
        c = _connector(gh)
        results = [
            GitHubResponse(success=False, error="502 Bad Gateway", status_code=502),
            GitHubResponse(success=True, data=gh.repo),
        ]

        async def flaky(repo_id: int) -> GitHubResponse:
            gh.calls["get_repo_by_id"] += 1
            return results.pop(0)

        async def no_retries(method: Callable[..., Awaitable[GitHubResponse]], *args: object, **kwargs: object) -> GitHubResponse:
            return await method(*args, **kwargs)

        gh.get_repo_by_id = flaky  # type: ignore[method-assign]
        # Skip ds_call's retries so each lookup is exactly one request.
        c.runtime.ds_call = no_retries  # type: ignore[method-assign]

        first = await c.runtime.get_repo_by_id(10)
        second = await c.runtime.get_repo_by_id(10)
        third = await c.runtime.get_repo_by_id(10)

        assert first.success is False
        assert second.success is True and third.success is True
        assert gh.calls["get_repo_by_id"] == 2

    async def test_the_lookup_expires(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Bounded, so a renamed or transferred repository is picked up again.
        from app.connectors.sources.github_teams import runtime as runtime_module

        gh = CountingGitHub(files=0)
        c = _connector(gh)
        now = [1_000.0]
        monkeypatch.setattr(runtime_module.time, "monotonic", lambda: now[0])

        await c.runtime.get_repo_by_id(10)
        now[0] += runtime_module._REPO_LOOKUP_TTL_SECONDS - 1
        await c.runtime.get_repo_by_id(10)
        assert gh.calls["get_repo_by_id"] == 1

        now[0] += 2
        await c.runtime.get_repo_by_id(10)
        assert gh.calls["get_repo_by_id"] == 2


class TestRenamedDefaultBranch:
    """A remembered lookup can name a default branch that has since been renamed.

    Code files are fetched at the default branch, so the old name 404s. That
    404 must not fail the record for good while the cache still holds the old
    name: the stream asks again and retries once on the new branch.
    """

    @staticmethod
    def _branch_aware(gh: CountingGitHub, live_branch: str) -> None:
        serve = gh.get_file_contents

        async def get_file_contents(owner: str, repo: str, path: str, ref: str | None = None) -> GitHubResponse:
            if ref != live_branch:
                gh.calls["get_file_contents"] += 1
                return GitHubResponse(success=False, error="Not Found", status_code=404)
            return await serve(owner, repo, path, ref)

        gh.get_file_contents = get_file_contents  # type: ignore[method-assign]

    async def test_a_renamed_branch_is_looked_up_again_and_the_file_served(self) -> None:
        gh = CountingGitHub(files=0)
        c = _connector(gh)
        await c.runtime.get_repo_by_id(10)  # remembered while the branch was "main"

        gh.repo = make_repo(repo_id=10, default_branch="develop")
        self._branch_aware(gh, "develop")

        content = await c.repos.fetch_code_file_content(_code_record("src/f0.py"))

        assert content == b"print('hi')\n"
        assert gh.calls == Counter({"get_repo_by_id": 2, "get_file_contents": 2})

        # The fresh answer is remembered: the next file pays no lookup and no 404.
        await c.repos.fetch_code_file_content(_code_record("src/f1.py"))
        assert gh.calls == Counter({"get_repo_by_id": 2, "get_file_contents": 3})

    async def test_a_file_that_is_gone_is_not_fetched_twice(self) -> None:
        # Same branch after the second lookup: the 404 is real, so the stream
        # fails as before without a second contents request.
        gh = CountingGitHub(files=0)
        c = _connector(gh)
        self._branch_aware(gh, "no-such-branch")

        with pytest.raises(HTTPException) as raised:
            await c.repos.fetch_code_file_content(_code_record("src/gone.py"))

        assert raised.value.status_code == 404
        assert gh.calls == Counter({"get_repo_by_id": 2, "get_file_contents": 1})
