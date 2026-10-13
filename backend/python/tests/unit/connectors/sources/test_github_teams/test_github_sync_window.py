"""The "updated since" window on issues and pull requests, counted in API calls.

Without a window a sync lists every issue and PR a repository ever had, and
each stored PR then costs its own requests when it is indexed (commits,
comments, reviews, files). These tests run the real listing and streaming code
(``IssuesSync``, ``PullRequestsSync``, ``CommentsHelper``, ``RuntimeHelper``)
against a fake repository with three years of history and count every request
by endpoint, as ``test_github_call_budget.py`` does. They pin that:

- with no window the sync behaves as before: the full history, every page;
- with a window, out-of-window issues are never listed (GitHub's ``since``),
  the PR listing stops at the window, and nothing is fetched for an
  out-of-window item;
- in-window items are stored newest first;
- the window and the incremental checkpoint combine as the later of the two,
  and narrowing the window removes nothing.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest

from app.connectors.core.registry.filters import Filter, FilterCollection, SyncFilterKey
from app.connectors.sources.github_teams.comments import CommentsHelper
from app.connectors.sources.github_teams.issues import IssuesSync
from app.connectors.sources.github_teams.pull_requests import PullRequestsSync
from app.connectors.sources.github_teams.runtime import RuntimeHelper
from app.models.entities import PullRequestRecord, TicketRecord
from tests.unit.connectors.sources.test_github_teams.conftest import make_mock_connector
from tests.unit.connectors.sources.test_github_teams.test_github_call_budget import (
    CountingGitHub,
)

if TYPE_CHECKING:
    from collections import Counter
    from unittest.mock import MagicMock

    from app.sources.client.github.github import GitHubResponse

pytestmark = pytest.mark.anyio


@pytest.fixture()
def anyio_backend() -> str:
    return "asyncio"


NOW = datetime(2026, 10, 1, tzinfo=timezone.utc)
HISTORY_DAYS = 1_095  # three years: one issue and one PR updated each day
WINDOW_DAYS = 180
PAGE = 100


def _ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def _window(start: datetime | None = None, *, operator: str = "is_after") -> FilterCollection:
    value = {"start": _ms(start), "end": None} if start is not None else None
    return FilterCollection(filters=[Filter(
        key=SyncFilterKey.MODIFIED.value, type="datetime", operator=operator, value=value,
    )])


class _Listed(SimpleNamespace):
    @property
    def raw_data(self) -> dict:  # a listing object completes itself on raw_data: one GET each
        raise AssertionError("raw_data completes a list-derived object (one GET each)")


def _listed(number: int, updated_at: datetime, *, is_pr: bool) -> _Listed:
    kind = "pull" if is_pr else "issues"
    return _Listed(
        number=number, title=f"{kind} {number}", state="open", state_reason=None, _rawData={},
        user=SimpleNamespace(login="octocat", id=7), body="text", labels=[], assignees=[],
        requested_reviewers=[], merged_at=None, head=SimpleNamespace(sha="head-sha"),
        html_url=f"https://github.com/acme/widgets/{kind}/{number}",
        created_at=updated_at - timedelta(days=1), updated_at=updated_at,
    )


class RepoWithHistory(CountingGitHub):
    """A repository with one issue and one PR updated per day for three years.

    ``/issues`` returns PRs as well, like GitHub, and honours ``since``,
    ``sort``, ``direction`` and paging. ``/pulls`` has no ``since``, like GitHub.
    """

    def __init__(self) -> None:
        super().__init__(files=5)
        self.issues = [
            _listed(2 * day + 1, NOW - timedelta(days=day), is_pr=False) for day in range(HISTORY_DAYS)
        ]
        self.pulls = [
            _listed(2 * day + 2, NOW - timedelta(days=day, hours=12), is_pr=True)
            for day in range(HISTORY_DAYS)
        ]
        self.list_issues_since: list[datetime | None] = []

    @staticmethod
    def _page(items: list[Any], direction: str | None, per_page: int, page: int) -> list[Any]:
        ordered = sorted(items, key=lambda i: i.updated_at, reverse=(direction == "desc"))
        return ordered[(page - 1) * per_page: page * per_page]

    async def list_issues(
        self, owner: str, repo: str, state: str = "open", since: datetime | None = None,
        sort: str | None = None, direction: str | None = None,
        per_page: int = 30, page: int = 1, **_: object,
    ) -> GitHubResponse:
        self.list_issues_since.append(since)
        items = [i for i in self.issues + self.pulls if since is None or i.updated_at >= since]
        return self._ok("list_issues", self._page(items, direction, per_page, page))

    async def list_pulls(
        self, owner: str, repo: str, state: str = "open", sort: str | None = None,
        direction: str | None = None, per_page: int = 30, page: int = 1, **_: object,
    ) -> GitHubResponse:
        return self._ok("list_pulls", self._page(self.pulls, direction, per_page, page))


class _SyncPoints:
    def __init__(self) -> None:
        self.data: dict[str, dict] = {}

    async def read_sync_point(self, key: str) -> dict | None:
        return self.data.get(key)

    async def update_sync_point(self, key: str, value: dict) -> None:
        self.data[key] = value


def _connector(gh: RepoWithHistory, sync_filters: FilterCollection | None) -> MagicMock:
    c = make_mock_connector()
    c._github_executor = None
    c.data_source = gh
    c.sync_filters = sync_filters
    c.record_sync_point = _SyncPoints()
    c.runtime = RuntimeHelper(c)
    c.comments = CommentsHelper(c)
    c.issues = IssuesSync(c)
    c.pull_requests = PullRequestsSync(c)
    return c


def _stored(c: MagicMock) -> list[Any]:
    return [rec for call in c.data_entities_processor.on_new_records.await_args_list for rec, _ in call.args[0]]


async def _sync(c: MagicMock, gh: RepoWithHistory) -> None:
    await c.issues.fetch_issues_batched(gh.repo)
    await c.pull_requests.fetch_prs_batched(gh.repo)


async def _index(c: MagicMock, records: list[Any]) -> None:
    """Fetch each stored record's content, as indexing does."""
    for record in records:
        if isinstance(record, PullRequestRecord):
            await c.pull_requests.build_pull_request_blocks(record)
        elif isinstance(record, TicketRecord):
            await c.issues.build_ticket_blocks(record)


async def _full_cost(sync_filters: FilterCollection | None) -> tuple[Counter[str], list[Any]]:
    gh = RepoWithHistory()
    c = _connector(gh, sync_filters)
    await _sync(c, gh)
    stored = _stored(c)
    await _index(c, stored)
    return gh.calls, stored


def _in_window(items: list[Any], start: datetime) -> int:
    return sum(1 for i in items if i.updated_at >= start)


class TestNoWindow:
    async def test_the_full_history_is_listed_as_before(self) -> None:
        gh = RepoWithHistory()
        c = _connector(gh, None)

        await _sync(c, gh)

        stored = _stored(c)
        assert sum(isinstance(r, TicketRecord) for r in stored) == HISTORY_DAYS
        assert sum(isinstance(r, PullRequestRecord) for r in stored) == HISTORY_DAYS
        assert gh.list_issues_since == [None] * len(gh.list_issues_since)
        # /issues carries PRs too, so it pages over both.
        assert gh.calls["list_issues"] == math.ceil(2 * HISTORY_DAYS / PAGE)
        assert gh.calls["list_pulls"] == math.ceil(HISTORY_DAYS / PAGE)

    async def test_an_empty_filter_is_no_window(self) -> None:
        from app.connectors.sources.github_teams.common.utils import sync_window_start

        assert sync_window_start(None) is None
        assert sync_window_start(FilterCollection()) is None
        assert sync_window_start(_window(None)) is None


class TestWindow:
    START = NOW - timedelta(days=WINDOW_DAYS)

    async def test_out_of_window_issues_are_never_listed(self) -> None:
        gh = RepoWithHistory()
        c = _connector(gh, _window(self.START))

        await c.issues.fetch_issues_batched(gh.repo)

        assert set(gh.list_issues_since) == {self.START}
        tickets = [r for r in _stored(c) if isinstance(r, TicketRecord)]
        assert len(tickets) == _in_window(gh.issues, self.START)
        assert min(t.source_updated_at for t in tickets) >= _ms(self.START)
        in_window_listing = _in_window(gh.issues + gh.pulls, self.START)
        assert gh.calls["list_issues"] == math.ceil(in_window_listing / PAGE)

    async def test_pull_request_paging_stops_at_the_window(self) -> None:
        gh = RepoWithHistory()
        c = _connector(gh, _window(self.START))

        await c.pull_requests.fetch_prs_batched(gh.repo)

        prs = [r for r in _stored(c) if isinstance(r, PullRequestRecord)]
        in_window = _in_window(gh.pulls, self.START)
        assert len(prs) == in_window
        assert min(p.source_updated_at for p in prs) >= _ms(self.START)
        # The page that crosses the window start is the last one requested.
        assert gh.calls["list_pulls"] == in_window // PAGE + 1

    async def test_nothing_is_fetched_for_an_out_of_window_item(self) -> None:
        calls, stored = await _full_cost(_window(self.START))

        prs = _in_window(RepoWithHistory().pulls, self.START)
        issues = _in_window(RepoWithHistory().issues, self.START)
        assert sum(isinstance(r, PullRequestRecord) for r in stored) == prs
        # Each PR's content requests run for in-window PRs only.
        for endpoint in ("get_pull", "get_pull_commits", "get_pull_reviews",
                         "get_pull_review_comments", "get_pull_file_changes"):
            assert calls[endpoint] == prs, endpoint
        assert calls["get_file_contents"] == 5 * prs
        assert calls["get_issue"] == issues
        assert calls["list_issue_comments"] == prs + issues

    async def test_in_window_items_are_stored_newest_first(self) -> None:
        gh = RepoWithHistory()
        c = _connector(gh, _window(self.START))

        await _sync(c, gh)

        stored = _stored(c)
        for kind in (TicketRecord, PullRequestRecord):
            times = [r.source_updated_at for r in stored if isinstance(r, kind)]
            assert times == sorted(times, reverse=True), kind.__name__
        # The first issue stored is the most recently updated one.
        assert stored[0].external_record_id == f"{gh.repo.id}/issues/1"

    async def test_a_pull_request_updated_at_the_window_start_is_included(self) -> None:
        gh = RepoWithHistory()
        start = gh.pulls[10].updated_at
        c = _connector(gh, _window(start))

        await c.pull_requests.fetch_prs_batched(gh.repo)

        assert len([r for r in _stored(c) if isinstance(r, PullRequestRecord)]) == 11

    async def test_a_rolling_window_without_a_date_counts_back_from_now(self) -> None:
        from app.connectors.sources.github_teams.common.utils import sync_window_start

        start = sync_window_start(_window(None, operator="last_180_days"))

        assert start is not None
        assert abs((datetime.now(timezone.utc) - start) - timedelta(days=180)) < timedelta(minutes=1)


class TestWindowAndCheckpoint:
    START = NOW - timedelta(days=WINDOW_DAYS)

    async def _second_sync_after(self, checkpoint: datetime, sync_filters: FilterCollection) -> tuple[RepoWithHistory, MagicMock]:
        gh = RepoWithHistory()
        c = _connector(gh, sync_filters)
        for group in (f"{gh.repo.id}-work-items", f"{gh.repo.id}-pull-requests"):
            await c.issues._update_sync_checkpoint(group, _ms(checkpoint))
        await _sync(c, gh)
        return gh, c

    async def test_a_checkpoint_inside_the_window_wins(self) -> None:
        checkpoint = NOW - timedelta(days=3)
        gh, c = await self._second_sync_after(checkpoint, _window(self.START))

        assert set(gh.list_issues_since) == {checkpoint}
        prs = [r for r in _stored(c) if isinstance(r, PullRequestRecord)]
        assert len(prs) == _in_window(gh.pulls, checkpoint + timedelta(milliseconds=1))
        assert gh.calls["list_pulls"] == 1

    async def test_a_window_start_after_the_checkpoint_wins_and_removes_nothing(self) -> None:
        checkpoint = NOW - timedelta(days=700)
        gh, c = await self._second_sync_after(checkpoint, _window(self.START))

        assert set(gh.list_issues_since) == {self.START}
        prs = [r for r in _stored(c) if isinstance(r, PullRequestRecord)]
        assert len(prs) == _in_window(gh.pulls, self.START)
        dep = c.data_entities_processor
        dep.on_record_deleted.assert_not_awaited()
        dep.on_records_deleted_cascade.assert_not_awaited()

    async def test_the_checkpoint_still_advances_with_a_window(self) -> None:
        gh = RepoWithHistory()
        c = _connector(gh, _window(self.START))

        await _sync(c, gh)

        newest_issue = max(i.updated_at for i in gh.issues)
        newest_pr = max(p.updated_at for p in gh.pulls)
        assert await c.issues._get_sync_checkpoint(f"{gh.repo.id}-work-items") == _ms(newest_issue)
        assert await c.issues._get_sync_checkpoint(f"{gh.repo.id}-pull-requests") == _ms(newest_pr)


class TestSavings:
    async def test_a_six_month_window_on_three_years_of_history(self) -> None:
        unwindowed, _ = await _full_cost(None)
        windowed, _ = await _full_cost(_window(NOW - timedelta(days=WINDOW_DAYS)))

        listing_before = unwindowed["list_issues"] + unwindowed["list_pulls"]
        listing_after = windowed["list_issues"] + windowed["list_pulls"]
        total_before = sum(unwindowed.values())
        total_after = sum(windowed.values())

        # Listing: /issues pages over issues and PRs (2,190 -> 361 items), and
        # /pulls stops on the page that crosses the window (11 pages -> 2).
        assert (listing_before, listing_after) == (22 + 11, 4 + 2)
        # Indexing: a PR costs 11 requests (with 5 changed files) and an issue
        # 2; the repository lookup is made once. 1,095 of each before; 180 PRs
        # and 181 issues after.
        assert total_before == 33 + 1_095 * 11 + 1_095 * 2 + 1 == 14_269
        assert total_after == 6 + 180 * 11 + 181 * 2 + 1 == 2_349


class TestFilterField:
    """Both GitHub connectors offer the window as an ordinary date filter."""

    @staticmethod
    def _sync_fields(connector_cls: type) -> dict[str, dict]:
        fields = connector_cls._connector_metadata["config"]["filters"]["sync"]["schema"]["fields"]
        return {f["name"]: f for f in fields}

    @pytest.mark.parametrize("module, cls", [
        ("app.connectors.sources.github_teams.connector", "GitHubTeamsConnector"),
        ("app.connectors.sources.github.connector", "GithubConnector"),
    ])
    async def test_the_window_is_an_optional_date_filter(self, module: str, cls: str) -> None:
        import importlib

        field = self._sync_fields(getattr(importlib.import_module(module), cls)).get("modified")

        assert field is not None
        assert field["displayName"] == "Sync issues and pull requests updated since"
        assert field["filterType"] == "datetime"
        assert field["required"] is False
        assert field["defaultValue"] is None and field["defaultOperator"] is None
        assert field["noImplicitOperatorDefault"] is True
        # A start, never an end: an end date would hide all new activity.
        assert "is_after" in field["operators"]
        assert "is_before" not in field["operators"] and "is_between" not in field["operators"]
