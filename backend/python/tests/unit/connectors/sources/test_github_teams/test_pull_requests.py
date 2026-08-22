"""Unit tests for github_teams PullRequestsSync.

Covers:
- process_pull_request: field mapping (labels, assignees, merge state,
  last_commit_sha) and external id construction.
- Merged PR status override ("merged" vs. raw pr.state).
- check_and_fetch_updated_pr_for_reindex: unchanged revision -> None; changed
  revision -> maps the full PR returned by ``get_pull``.
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.connectors.sources.github_teams.comments import CommentsHelper
from app.connectors.sources.github_teams.common.utils import epoch_ms_or_now
from app.connectors.sources.github_teams.pull_requests import PullRequestsSync
from app.models.blocks import BlocksContainer
from app.models.entities import PullRequestRecord

from tests.unit.connectors.sources.test_github_teams.conftest import (
    failed_response,
    make_mock_connector,
    make_repo,
    ok_response,
)

pytestmark = pytest.mark.anyio


@pytest.fixture()
def anyio_backend() -> str:
    return "asyncio"


class _ListedPR(SimpleNamespace):
    """A PullRequest carrying only what the response already stored.

    ``raw_data`` raises because on a list-derived object it completes the
    object — ``GET /pulls/{n}`` per PR, the very call dropping ``get_pull`` was
    meant to remove. The stored payload is reached through ``_rawData``, which
    is what the completed reindex object exposes too.
    """

    @property
    def raw_data(self) -> dict:
        raise AssertionError(
            "raw_data completes a list-derived PR (one GET each); "
            "use listing_payload() to read the stored payload"
        )


def _pr(
    *, number: int = 1, title: str = "Add feature", state: str = "open", merged: bool = False,
    mergeable: bool | None = None, head_sha: str = "sha-head", full_payload: bool = False,
) -> SimpleNamespace:
    """A PullRequest as the LIST endpoint returns it by default.

    `mergeable`/`merged_by` live only on the single-PR payload, so they are
    absent here unless ``full_payload`` says this object came from
    ``get_pull``. Deliberately no ``merged`` attribute: on a list-derived
    object reading it fires a per-PR fetch, so the code must use ``merged_at``.
    """
    raw: dict = {}
    if full_payload:
        raw["mergeable"] = mergeable
        raw["merged_by"] = {"login": "maintainer"} if merged else None
    return _ListedPR(
        number=number, title=title, state=state, _rawData=raw,
        merged_at=datetime(2024, 1, 2, tzinfo=timezone.utc) if merged else None,
        labels=[SimpleNamespace(name="enhancement")],
        assignees=[SimpleNamespace(login="bob")],
        html_url=f"https://github.com/acme/widgets/pull/{number}",
        created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        updated_at=datetime(2024, 1, 3, tzinfo=timezone.utc),
        head=SimpleNamespace(sha=head_sha),
        body="pr body",
        user=SimpleNamespace(login="author-carol", id=701),
        requested_reviewers=[
            SimpleNamespace(login="rev-dave", id=702),
            SimpleNamespace(login="rev-erin", id=703),
        ],
    )


class TestFetchPrsBatched:
    """The PR listing replaced a get_pull per PR — 2,950 sequential calls on a
    busy repo. These pin the properties that make the replacement safe."""

    def _sync(self, c: object) -> PullRequestsSync:
        sync = PullRequestsSync(c)
        c.issues.process_new_records = AsyncMock(return_value=True)
        c.issues._get_sync_checkpoint = AsyncMock(return_value=None)
        c.issues._update_sync_checkpoint = AsyncMock()
        c.comments.clean_github_content = AsyncMock(return_value=("", []))
        return sync

    async def test_pages_until_short_page_and_never_fetches_a_single_pr(self) -> None:
        c = make_mock_connector()
        repo = make_repo(repo_id=10)
        full_page = [_pr(number=n) for n in range(100)]
        c.runtime.ds_call = AsyncMock(side_effect=[
            ok_response(full_page),
            ok_response([_pr(number=101)]),   # short page -> stop
        ])
        sync = self._sync(c)

        await sync.fetch_prs_batched(repo)

        assert c.runtime.ds_call.await_count == 2
        methods = [call.args[0] for call in c.runtime.ds_call.await_args_list]
        assert all(m is c.data_source.list_pulls for m in methods)

    async def test_descending_order_stops_at_the_checkpoint(self) -> None:
        """/pulls has no `since`, so sort=updated&direction=desc plus an early
        break is what makes an incremental sync cheap: the first PR older than
        the watermark means every later one is older too."""
        c = make_mock_connector()
        repo = make_repo(repo_id=10)
        fresh = _pr(number=2)
        fresh.updated_at = datetime(2024, 6, 1, tzinfo=timezone.utc)
        stale = _pr(number=1)
        stale.updated_at = datetime(2020, 1, 1, tzinfo=timezone.utc)
        c.runtime.ds_call = AsyncMock(return_value=ok_response([fresh, stale]))
        sync = self._sync(c)
        c.issues._get_sync_checkpoint = AsyncMock(
            return_value=int(datetime(2024, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
        )

        await sync.fetch_prs_batched(repo)

        assert c.runtime.ds_call.await_count == 1  # stopped, did not page on
        persisted = c.issues.process_new_records.call_args.args[0]
        assert [ru.record.external_record_id for ru in persisted] == ["10/pull/2"]

    async def test_sort_and_direction_are_requested(self) -> None:
        c = make_mock_connector()
        repo = make_repo(repo_id=10)
        c.runtime.ds_call = AsyncMock(return_value=ok_response([]))
        await self._sync(c).fetch_prs_batched(repo)

        kwargs = c.runtime.ds_call.await_args.kwargs
        assert kwargs["sort"] == "updated"
        assert kwargs["direction"] == "desc"
        assert kwargs["state"] == "all"

    async def test_failed_page_does_not_advance_the_checkpoint(self) -> None:
        c = make_mock_connector()
        repo = make_repo(repo_id=10)
        c.runtime.ds_call = AsyncMock(return_value=failed_response("500", status_code=500))
        sync = self._sync(c)

        await sync.fetch_prs_batched(repo)

        c.issues._update_sync_checkpoint.assert_not_awaited()

    async def test_persist_failure_stops_before_advancing_past_it(self) -> None:
        """Descending pages mean a later page is OLDER, so committing its
        watermark would jump the failure and those PRs would never return."""
        c = make_mock_connector()
        repo = make_repo(repo_id=10)
        c.runtime.ds_call = AsyncMock(return_value=ok_response([_pr(number=n) for n in range(100)]))
        sync = self._sync(c)
        c.issues.process_new_records = AsyncMock(return_value=False)

        await sync.fetch_prs_batched(repo)

        assert c.runtime.ds_call.await_count == 1
        c.issues._update_sync_checkpoint.assert_not_awaited()


class TestProcessPullRequest:
    async def test_populates_author_and_requested_reviewers(self) -> None:
        """creator_name/review_name exist on PullRequestRecord and were left
        empty, so a PR carried no author and no reviewers."""
        c = make_mock_connector()
        repo = make_repo(repo_id=10)
        sync = PullRequestsSync(c)
        ru = await sync.process_pull_request(repo, _pr(number=5))

        assert ru.record.creator_name == "author-carol"
        assert ru.record.review_name == ["rev-dave", "rev-erin"]

    async def test_maps_fields_for_open_pr(self) -> None:
        c = make_mock_connector()
        repo = make_repo(repo_id=10)
        sync = PullRequestsSync(c)
        ru = await sync.process_pull_request(repo, _pr(number=5))

        assert ru is not None
        assert isinstance(ru.record, PullRequestRecord)
        assert ru.record.external_record_id == "10/pull/5"
        assert ru.record.external_record_group_id == "10-pull-requests"
        assert ru.record.status == "OPEN"
        assert ru.record.labels == ["enhancement"]
        assert ru.record.assignee == ["bob"]
        assert ru.record.last_commit_sha == "sha-head"
        # Both live only on the single-PR payload; a listing entry leaves them
        # empty rather than paying a GET per PR to fill them in.
        assert ru.record.mergeable is None
        assert ru.record.merged_by is None

    async def test_pr_status_is_normalised_like_tickets(self) -> None:
        """Tickets store Status enum values; PRs must use the same convention or
        status filtering sees two vocabularies from one connector. Merged->DONE
        vs closed-unmerged->CANCELLED keeps the old 'merged' distinction."""
        c = make_mock_connector()
        repo = make_repo(repo_id=10)
        sync = PullRequestsSync(c)

        merged = await sync.process_pull_request(
            repo, _pr(number=5, state="closed", merged=True))
        abandoned = await sync.process_pull_request(
            repo, _pr(number=6, state="closed", merged=False))

        # merged_at, not `.merged` — the latter is unset on a listing entry
        # and reading it would fire a per-PR fetch.
        assert merged.record.status == "DONE"
        assert abandoned.record.status == "CANCELLED"

    async def test_full_payload_still_yields_merge_state(self) -> None:
        """The reindex path fetches the whole PR, so raw_data carries the
        fields the listing omits and they must survive the same mapper."""
        c = make_mock_connector()
        repo = make_repo(repo_id=10)

        sync = PullRequestsSync(c)
        ru = await sync.process_pull_request(
            repo, _pr(number=5, state="closed", merged=True, mergeable=True, full_payload=True))

        assert ru.record.mergeable == "True"
        assert ru.record.merged_by == "maintainer"

    async def test_indexing_disabled_sets_auto_index_off(self) -> None:
        c = make_mock_connector()
        repo = make_repo(repo_id=10)
        c.indexing_filters = SimpleNamespace(is_enabled=lambda _key: False)

        sync = PullRequestsSync(c)
        ru = await sync.process_pull_request(repo, _pr(number=5))

        assert ru is not None
        assert ru.record.indexing_status == "AUTO_INDEX_OFF"


class TestReindexCheck:
    async def test_unchanged_revision_returns_none(self) -> None:
        c = make_mock_connector()
        repo = make_repo(repo_id=10)
        pr = _pr(number=5)
        c.runtime.ds_call.side_effect = _dispatch(c, {
            "get_repo_by_id": ok_response(repo),
            "get_pull": ok_response(pr),
        })
        sync = PullRequestsSync(c)
        unchanged_rev = str(epoch_ms_or_now(pr.updated_at))
        record = PullRequestRecord(
            id="r1", org_id="org-1", record_name="x", record_type="PULL_REQUEST",
            version=0, origin="CONNECTOR", connector_name="GITHUB TEAMS", connector_id="c-1",
            external_record_id="10/pull/5", external_record_group_id="10-pull-requests",
            external_revision_id=unchanged_rev,
        )

        result = await sync.check_and_fetch_updated_pr_for_reindex(record)
        assert result is None

    async def test_changed_revision_maps_full_pull(self) -> None:
        c = make_mock_connector()
        repo = make_repo(repo_id=10)
        pr = _pr(number=5, title="New title")
        c.runtime.ds_call.side_effect = _dispatch(c, {
            "get_repo_by_id": ok_response(repo),
            "get_pull": ok_response(pr),
        })
        sync = PullRequestsSync(c)
        record = PullRequestRecord(
            id="r1", org_id="org-1", record_name="x", record_type="PULL_REQUEST",
            version=0, origin="CONNECTOR", connector_name="GITHUB TEAMS", connector_id="c-1",
            external_record_id="10/pull/5", external_record_group_id="10-pull-requests",
            external_revision_id="stale-rev",
        )

        result = await sync.check_and_fetch_updated_pr_for_reindex(record)
        assert result is not None
        fresh_record, _perms = result
        assert fresh_record.record_name == "New title"

    async def test_missing_group_id_returns_none(self) -> None:
        c = make_mock_connector()
        sync = PullRequestsSync(c)
        record = PullRequestRecord(
            id="r1", org_id="org-1", record_name="x", record_type="PULL_REQUEST",
            version=0, origin="CONNECTOR", connector_name="GITHUB TEAMS", connector_id="c-1",
            external_record_id="10/pull/5",
        )
        result = await sync.check_and_fetch_updated_pr_for_reindex(record)
        assert result is None

    async def test_malformed_ids_return_none(self) -> None:
        c = make_mock_connector()
        record = PullRequestRecord(
            id="r1", org_id="org-1", record_name="x", record_type="PULL_REQUEST",
            version=0, origin="CONNECTOR", connector_name="GITHUB TEAMS", connector_id="c-1",
            external_record_id="not-int", external_record_group_id="abc-pull-requests",
        )
        assert await PullRequestsSync(c).check_and_fetch_updated_pr_for_reindex(record) is None

    async def test_repo_lookup_failure_returns_none(self) -> None:
        c = make_mock_connector()
        c.runtime.ds_call.side_effect = _dispatch(c, {"get_repo_by_id": failed_response("404")})
        record = PullRequestRecord(
            id="r1", org_id="org-1", record_name="x", record_type="PULL_REQUEST",
            version=0, origin="CONNECTOR", connector_name="GITHUB TEAMS", connector_id="c-1",
            external_record_id="10/pull/5", external_record_group_id="10-pull-requests",
        )
        assert await PullRequestsSync(c).check_and_fetch_updated_pr_for_reindex(record) is None

    async def test_pr_lookup_failure_returns_none(self) -> None:
        c = make_mock_connector()
        c.runtime.ds_call.side_effect = _dispatch(c, {
            "get_repo_by_id": ok_response(make_repo(repo_id=10)),
            "get_pull": failed_response("404"),
        })
        record = PullRequestRecord(
            id="r1", org_id="org-1", record_name="x", record_type="PULL_REQUEST",
            version=0, origin="CONNECTOR", connector_name="GITHUB TEAMS", connector_id="c-1",
            external_record_id="10/pull/5", external_record_group_id="10-pull-requests",
        )
        assert await PullRequestsSync(c).check_and_fetch_updated_pr_for_reindex(record) is None


class TestPrMappingAndFetchEdges:
    def test_is_after_treats_naive_and_missing_as_fresh(self) -> None:
        sync = PullRequestsSync(make_mock_connector())
        since = datetime(2024, 1, 1, tzinfo=timezone.utc)
        assert sync._is_after(SimpleNamespace(updated_at=None), since) is True
        naive = datetime(2024, 6, 1)
        assert sync._is_after(SimpleNamespace(updated_at=naive), since) is True
        assert sync._is_after(SimpleNamespace(updated_at=datetime(2020, 1, 1)), since) is False

    async def test_process_exception_returns_none(self) -> None:
        c = make_mock_connector()

        class _BadPR:
            number = 1
            labels: list = []
            assignees: list = []
            requested_reviewers: list = []
            user = None
            head = None
            updated_at = None
            created_at = None
            html_url = "https://github.com/acme/widgets/pull/1"
            _rawData: dict = {}

            @property
            def title(self) -> str:
                raise RuntimeError("bad")

        assert await PullRequestsSync(c).process_pull_request(make_repo(repo_id=10), _BadPR()) is None

    async def test_attachments_follow_indexing_flag(self) -> None:
        """The PR is stamped in process_pull_request; the attachment is stamped
        inside the real comments helper (single construction point, so the
        stream-time comment-attachment path is covered too)."""
        from app.connectors.sources.github_teams.comments import CommentsHelper

        c = make_mock_connector()
        c.indexing_filters = SimpleNamespace(is_enabled=lambda _key: False)
        c.pull_requests._prs_indexing_enabled = lambda: False
        c.comments = CommentsHelper(c)
        c.comments.clean_github_content = AsyncMock(return_value=("", [
            {"type": "pdf", "href": "https://github.com/user-attachments/files/1/x.pdf"},
        ]))

        updates = await PullRequestsSync(c)._build_pr_records(make_repo(repo_id=10), [_pr(number=1)])

        assert updates[0].record.indexing_status == "AUTO_INDEX_OFF"
        attachment_updates = updates[1:]
        assert attachment_updates
        assert all(u.record.indexing_status == "AUTO_INDEX_OFF" for u in attachment_updates)

    async def test_build_blocks_requires_group_id(self) -> None:
        record = PullRequestRecord(
            id="r1", org_id="org-1", record_name="x", record_type="PULL_REQUEST",
            version=0, origin="CONNECTOR", connector_name="GITHUB TEAMS", connector_id="c-1",
            external_record_id="10/pull/5",
        )
        with pytest.raises(Exception, match="Repository id not found"):
            await PullRequestsSync(make_mock_connector()).build_pull_request_blocks(record)

    async def test_build_blocks_repo_failure_raises(self) -> None:
        c = make_mock_connector()
        c.runtime.ds_call.side_effect = _dispatch(c, {"get_repo_by_id": failed_response("404")})
        record = PullRequestRecord(
            id="r1", org_id="org-1", record_name="x", record_type="PULL_REQUEST",
            version=0, origin="CONNECTOR", connector_name="GITHUB TEAMS", connector_id="c-1",
            external_record_id="10/pull/5", external_record_group_id="10-pull-requests",
        )
        with pytest.raises(Exception, match="Failed to resolve repo"):
            await PullRequestsSync(c).build_pull_request_blocks(record)


class TestBuildPullRequestBlocks:
    async def test_commits_and_comments_have_distinct_block_group_indices(self) -> None:
        """Regression test: when a PR has both a commits section and conversation
        comments, the commits BlockGroup and the first comment BlockGroup must not
        share an index — they previously both landed on index=1 because the comment
        numbering started at parent_index + 1 (0 + 1) without accounting for the
        commits group already occupying index 1."""
        c = make_mock_connector()
        c.comments = CommentsHelper(c)
        repo = make_repo(repo_id=10)
        pr = _pr(number=5)

        c.runtime.ds_call.side_effect = _dispatch(c, {
            "get_repo_by_id": ok_response(repo),
            "get_pull": ok_response(pr),
            "get_pull_commits": ok_response([
                SimpleNamespace(
                    commit=SimpleNamespace(message="fix bug", committer=SimpleNamespace(date=None)),
                    html_url="https://github.com/acme/widgets/commit/abc", sha="abc",
                ),
            ]),
            "list_issue_comments": ok_response([
                SimpleNamespace(
                    body="looks good", user=SimpleNamespace(login="reviewer"),
                    html_url="https://github.com/acme/widgets/pull/5#issuecomment-1",
                    updated_at=None, id=1,
                ),
            ]),
            "get_pull_reviews": ok_response([]),
            "get_pull_review_comments": ok_response([]),
            "get_pull_file_changes": ok_response([]),
        })

        record = PullRequestRecord(
            id="r1", org_id="org-1", record_name="PR #5", record_type="PULL_REQUEST",
            version=0, origin="CONNECTOR", connector_name="GITHUB TEAMS", connector_id="c-1",
            external_record_id="10/pull/5", external_record_group_id="10-pull-requests",
            weburl="https://github.com/acme/widgets/pull/5",
        )

        sync = PullRequestsSync(c)
        blocks_json = await sync.build_pull_request_blocks(record)
        container = BlocksContainer.model_validate_json(blocks_json)

        indices = [bg.index for bg in container.block_groups]
        assert len(indices) == len(set(indices)), f"duplicate block group indices: {indices}"

        commits_bg = next(bg for bg in container.block_groups if bg.name == "Commits")
        comment_bg = next(bg for bg in container.block_groups if bg.name and bg.name.startswith("Comment by"))
        assert commits_bg.index != comment_bg.index
        # Comments remain declared children of bg_0 (the description), not of
        # the commits group, even though the commits group was numbered first.
        assert comment_bg.parent_index == 0


def _dispatch(c: object, mapping: dict[str, object]) -> object:
    by_identity = {getattr(c.data_source, name): response for name, response in mapping.items()}

    def _fn(method: object, *args: object, **kwargs: object) -> object:
        if method in by_identity:
            return by_identity[method]
        raise AssertionError(f"unmocked ds_call for {method!r}")

    return _fn
