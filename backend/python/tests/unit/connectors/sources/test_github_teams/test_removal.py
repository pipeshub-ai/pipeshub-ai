"""Removal of what left GitHub or the sync's scope: RecordRemover, RepoScopeReconciler,
IssueReconciler, and the connector/projects code that decides when they may act."""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import ANY, AsyncMock, MagicMock

import httpx
import pytest

from app.connectors.core.registry.filters import FilterCollection
from app.connectors.sources.github_teams.comments import (
    CommentsHelper,
    attachment_key,
    attachment_url,
)
from app.connectors.sources.github_teams.connector import GitHubTeamsConnector
from app.connectors.sources.github_teams.issue_reconciler import IssueReconciler
from app.connectors.sources.github_teams.issues import IssuesSync
from app.connectors.sources.github_teams.projects import ProjectsSync
from app.connectors.sources.github_teams.pull_requests import PullRequestsSync
from app.connectors.sources.github_teams.record_removal import RecordRemover
from app.connectors.sources.github_teams.repo_scope import RepoScopeReconciler
from app.connectors.sources.github_teams.runtime import RuntimeHelper
from app.models.entities import RecordType
from app.sources.client.github.github import GitHubResponse
from app.sources.external.github.github_async import GitHubAsyncDataSource
from tests.unit.connectors.sources.test_github_teams.conftest import (
    failed_response,
    make_mock_connector,
    make_repo,
    ok_response,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


def _record(rid: str, external_id: str, record_type: str = RecordType.TICKET.value,
            updated_ms: int = 0) -> SimpleNamespace:
    return SimpleNamespace(id=rid, external_record_id=external_id, record_type=record_type,
                           source_updated_at=updated_ms)


def _processor() -> MagicMock:
    p = MagicMock()
    p.get_records_by_parent = AsyncMock(return_value=[])
    p.on_records_detached_from_parent = AsyncMock()
    p.on_records_deleted_cascade = AsyncMock(return_value={"success": True})
    p.on_record_group_deleted = AsyncMock(return_value=True)
    p.get_records_in_record_group = AsyncMock(return_value=[])
    p.get_nodes_by_filters = AsyncMock(return_value=[])
    return p


# --------------------------------------------------------------------- RecordRemover


class TestRecordRemover:
    async def test_sub_issues_survive_and_lose_their_parent(self) -> None:
        p = _processor()
        p.get_records_by_parent = AsyncMock(return_value=[
            _record("child", "1/issues/2"), _record("file", "https://x#1/issues/1", RecordType.FILE.value)])
        await RecordRemover(p, "conn", MagicMock()).remove_records([_record("parent", "1/issues/1")])
        p.on_records_detached_from_parent.assert_awaited_once_with(["child"])
        args, kwargs = p.on_records_deleted_cascade.call_args
        assert args[0] == ["parent"] and kwargs["cascade_children"] is False

    async def test_a_refused_delete_raises(self) -> None:
        p = _processor()
        p.on_records_deleted_cascade = AsyncMock(return_value={"success": False, "reason": "locked"})
        with pytest.raises(RuntimeError):
            await RecordRemover(p, "conn", MagicMock()).remove_records([_record("a", "1/issues/1")])

    async def test_group_removal_pages_until_empty_then_drops_the_group(self) -> None:
        p = _processor()
        p.get_records_in_record_group = AsyncMock(side_effect=[[_record("a", "x")], [_record("b", "y")], []])
        removed = await RecordRemover(p, "conn", MagicMock()).remove_group("1-work-items")
        assert removed == 2
        p.on_record_group_deleted.assert_awaited_once_with("1-work-items", "conn")

    async def test_group_removal_stops_when_deletes_do_not_take_effect(self) -> None:
        p = _processor()
        p.get_records_in_record_group = AsyncMock(return_value=[_record("a", "x")])
        with pytest.raises(RuntimeError):
            await RecordRemover(p, "conn", MagicMock()).remove_group("1-work-items")


# --------------------------------------------------------------------- RepoScopeReconciler


class TestRepoScopeReconciler:
    def _subject(self, groups: list[dict]) -> tuple[RepoScopeReconciler, MagicMock, MagicMock, MagicMock]:
        p = _processor()
        p.get_nodes_by_filters = AsyncMock(return_value=groups)
        remover = MagicMock()
        remover.remove_group = AsyncMock(return_value=0)
        sync_point = MagicMock()
        sync_point.delete_sync_point = AsyncMock()
        return RepoScopeReconciler(p, remover, sync_point, "conn", MagicMock()), p, remover, sync_point

    async def test_out_of_scope_repo_is_removed_with_children_and_checkpoints(self) -> None:
        subject, p, remover, sync_point = self._subject([
            {"externalGroupId": "1"}, {"externalGroupId": "1-work-items"}, {"externalGroupId": "2"},
            {"externalGroupId": "org-9"}, {"externalGroupId": "org-8"}])
        assert await subject.remove_out_of_scope({2}) is True
        removed = [c.args[0] for c in remover.remove_group.await_args_list]
        assert removed == ["1-work-items", "1-pull-requests", "1-code-repository", "1"]
        assert sync_point.delete_sync_point.await_count == 3
        p.on_record_group_deleted.assert_not_awaited()  # org groups go only after the repos synced
        await subject.remove_empty_orgs({9})
        p.on_record_group_deleted.assert_awaited_once_with("org-8", "conn")

    async def test_a_repository_still_served_is_kept_with_its_org_group(self) -> None:
        subject, p, remover, _ = self._subject([{"externalGroupId": "1"}, {"externalGroupId": "org-8"}])
        assert await subject.remove_out_of_scope(set(), AsyncMock(return_value=True)) is False
        remover.remove_group.assert_not_awaited()
        p.on_record_group_deleted.assert_not_awaited()

    async def test_in_scope_and_trash_marked_groups_are_left_alone(self) -> None:
        subject, p, remover, _ = self._subject([
            {"externalGroupId": "2"}, {"externalGroupId": "3", "isDeletedAtSource": True}, {"externalGroupId": "org-9"}])
        await subject.remove_out_of_scope({2})
        await subject.remove_empty_orgs({9})
        remover.remove_group.assert_not_awaited()
        p.on_record_group_deleted.assert_not_awaited()


# --------------------------------------------------------------------- IssueReconciler


def _reconciler(responses: dict, stored: list) -> tuple[IssueReconciler, MagicMock, MagicMock]:
    ds = MagicMock()
    runtime = MagicMock()

    async def ds_call(method: object, *args: object, **kwargs: object) -> GitHubResponse:
        if method is ds.graphql_query:
            return responses["graphql"].pop(0)
        return responses["get_issue"][args[2]]

    runtime.ds_call = AsyncMock(side_effect=ds_call)
    p = _processor()
    p.get_records_in_record_group = AsyncMock(return_value=stored)
    remover = MagicMock()
    remover.remove_records = AsyncMock()
    return IssueReconciler(runtime, lambda: ds, p, remover, "conn", MagicMock()), remover, runtime


def _numbers_page(numbers: list[int], more: bool = False, updated: dict[int, str] | None = None) -> GitHubResponse:
    return GitHubResponse(success=True, data={"repository": {"issues": {
        "nodes": [{"number": n, "updatedAt": (updated or {}).get(n, "1970-01-01T00:00:00Z")} for n in numbers],
        "pageInfo": {"hasNextPage": more, "endCursor": "c"}}}})


def _issue_at(full_name: str, number: int) -> GitHubResponse:
    return GitHubResponse(success=True, data=SimpleNamespace(html_url=f"https://github.com/{full_name}/issues/{number}"))


class TestIssueReconciler:
    async def test_source_total(self) -> None:
        subject, _, _ = _reconciler({"graphql": [GitHubResponse(success=True, data={"repository": {"issues": {"totalCount": 7}}})]}, [])
        assert await subject.source_total("acme", "widgets") == 7

    async def test_deleted_and_transferred_issues_are_removed_existing_ones_kept(self) -> None:
        stored = [_record("r1", "1001/issues/1"), _record("r2", "1001/issues/2"), _record("r3", "1001/issues/3"),
                  _record("r4", "1001/issues/4")]
        subject, remover, _ = _reconciler({
            "graphql": [_numbers_page([1])],
            "get_issue": {
                2: GitHubResponse(success=False, status_code=410, error="gone"),
                3: _issue_at("acme/elsewhere", 9),  # 301 followed to the new home
                4: _issue_at("acme/widgets", 4),  # GraphQL lagging; REST says it is here
            }}, stored)
        total = await subject.reconcile(make_repo(), "1001-work-items", AsyncMock(return_value=True))
        assert total == 1
        removed = [r.id for r in remover.remove_records.call_args.args[0]]
        assert removed == ["r2", "r3"]

    async def test_an_unclear_answer_removes_nothing(self) -> None:
        stored = [_record("r1", "1001/issues/1"), _record("r2", "1001/issues/2")]
        subject, remover, _ = _reconciler({
            "graphql": [_numbers_page([1])],
            "get_issue": {2: GitHubResponse(success=False, status_code=502, error="bad gateway")}}, stored)
        assert await subject.reconcile(make_repo(), "1001-work-items", AsyncMock(return_value=True)) is None
        remover.remove_records.assert_not_awaited()

    async def test_a_failed_sweep_page_removes_nothing(self) -> None:
        subject, remover, _ = _reconciler({
            "graphql": [_numbers_page([1], more=True), GitHubResponse(success=False, status_code=502, error="x")]},
            [_record("r2", "1001/issues/2")])
        assert await subject.reconcile(make_repo(), "1001-work-items", AsyncMock(return_value=True)) is None
        remover.remove_records.assert_not_awaited()

    async def test_a_sweep_whose_cursor_does_not_move_stops(self) -> None:
        subject, remover, _ = _reconciler({
            "graphql": [_numbers_page([1], more=True), _numbers_page([], more=True), _numbers_page([2])]},
            [_record("r2", "1001/issues/2")])
        assert await subject.reconcile(make_repo(), "1001-work-items", AsyncMock(return_value=True)) is None
        remover.remove_records.assert_not_awaited()

    async def test_issues_the_listing_missed_are_read_again(self) -> None:
        """Changed after the stored copy, or never stored: both are fetched and stored."""
        stored = [_record("r1", "1001/issues/1", updated_ms=1_000_000), _record("r2", "1001/issues/2", updated_ms=2_000_000)]
        refresh = AsyncMock(return_value=True)
        subject, remover, _ = _reconciler({
            "graphql": [_numbers_page([1, 2, 3], updated={
                1: "1970-01-01T00:16:40Z",   # == stored, same second
                2: "1970-01-01T00:40:00Z",   # newer than stored
                3: "1970-01-01T00:00:05Z"})],  # never stored
            "get_issue": {2: _issue_at("acme/widgets", 2), 3: _issue_at("acme/widgets", 3)}}, stored)
        assert await subject.reconcile(make_repo(), "1001-work-items", refresh) == 3
        assert [i.html_url.rsplit("/", 1)[1] for i in refresh.call_args.args[0]] == ["2", "3"]
        remover.remove_records.assert_not_awaited()

    async def test_issues_older_than_the_sync_window_are_not_read(self) -> None:
        refresh = AsyncMock(return_value=True)
        subject, _, _ = _reconciler({"graphql": [_numbers_page([3])], "get_issue": {}}, [])
        assert await subject.reconcile(make_repo(), "1001-work-items", refresh, since_ms=10_000) == 1
        refresh.assert_not_awaited()

    async def test_remove_all_takes_every_stored_ticket(self) -> None:
        stored = [_record("r1", "1001/issues/1"), _record("r2", "1001/issues/2")]
        subject, remover, _ = _reconciler({"graphql": []}, stored)
        await subject.remove_all(make_repo(), "1001-work-items")
        assert [r.id for r in remover.remove_records.call_args.args[0]] == ["r1", "r2"]

    async def test_a_missed_issue_that_cannot_be_read_makes_the_sweep_unclear(self) -> None:
        subject, remover, _ = _reconciler({
            "graphql": [_numbers_page([1, 2])],
            "get_issue": {1: GitHubResponse(success=False, status_code=502, error="bad gateway")}},
            [_record("r2", "1001/issues/2"), _record("r9", "1001/issues/9")])
        assert await subject.reconcile(make_repo(), "1001-work-items", AsyncMock(return_value=True)) is None
        remover.remove_records.assert_not_awaited()


# --------------------------------------------------------------------- issues paging


def _gh_issue(number: int, updated: int) -> SimpleNamespace:
    ts = datetime.fromtimestamp(updated, tz=timezone.utc)
    return SimpleNamespace(number=number, updated_at=ts, created_at=ts, title=f"I{number}", body="",
                           html_url=f"https://github.com/acme/widgets/issues/{number}", labels=[], assignees=[],
                           user=None, state="open", state_reason=None)


class TestIssuePaging:
    async def test_unchanged_total_skips_the_sweep(self) -> None:
        c = make_mock_connector()
        c.runtime.ds_call = AsyncMock(return_value=ok_response([]))
        c.issue_reconciler.source_total = AsyncMock(return_value=5)
        sync = IssuesSync(c)
        sync._read_checkpoint = AsyncMock(return_value={"last_sync_time": 1, "issue_total": 5})
        sync._write_checkpoint = AsyncMock()
        assert await sync.fetch_issues_batched(make_repo()) is True
        c.issue_reconciler.reconcile.assert_not_awaited()
        sync._write_checkpoint.assert_not_awaited()

    async def test_unknown_total_fails_the_step_and_removes_nothing(self) -> None:
        c = make_mock_connector()
        c.runtime.ds_call = AsyncMock(return_value=ok_response([]))
        c.issue_reconciler.source_total = AsyncMock(return_value=None)
        sync = IssuesSync(c)
        sync._read_checkpoint = AsyncMock(return_value={})
        sync._write_checkpoint = AsyncMock()
        assert await sync.fetch_issues_batched(make_repo()) is False
        c.issue_reconciler.reconcile.assert_not_awaited()


async def test_stored_attachments_are_kept_through_a_full_sync_and_url_keys_removed() -> None:
    """A full sync unlinks what it did not write, and no listing writes a file linked from a
    comment: those are noted as kept. Records keyed by the URL alone are removed."""
    c = make_mock_connector()
    url = "https://github.com/user-attachments/files/1/notes.txt"
    keyed = _record("a1", f"{url}#1001/issues/5", RecordType.FILE.value)
    legacy = _record("a2", url, RecordType.FILE.value)
    other = _record("f1", "/1001/blob/README.md", RecordType.FILE.value)
    ticket = _record("t1", "1001/issues/5")
    legacy.parent_external_record_id = "1001/issues/5"
    c.data_entities_processor.get_records_in_record_group = AsyncMock(return_value=[keyed, legacy, other, ticket])
    c.data_entities_processor.note_kept_records = MagicMock()
    order: list[str] = []
    c.data_entities_processor.reindex_existing_records = AsyncMock(side_effect=lambda r: order.append("reindex"))
    c.record_remover.remove_records = AsyncMock(side_effect=lambda r: order.append("remove"))
    await CommentsHelper(c).settle_stored_attachments("1001-work-items")
    c.data_entities_processor.note_kept_records.assert_called_once_with(c.connector_id, ["a1"])
    c.record_remover.remove_records.assert_awaited_once_with([legacy])
    # The parent is streamed again, which writes the keyed record; queued first, so a
    # failure leaves the old record for the next sync to try again.
    c.data_entities_processor.reindex_existing_records.assert_awaited_once_with([ticket])
    assert order == ["reindex", "remove"]


async def test_a_full_sync_or_the_first_sync_of_this_version_settles_attachments() -> None:
    c = make_mock_connector()
    c.runtime.ds_call = AsyncMock(return_value=ok_response([]))
    c.issue_reconciler.source_total = AsyncMock(return_value=0)
    sync = IssuesSync(c)
    sync._read_checkpoint = AsyncMock(return_value={})  # no issue total: full sync or upgrade
    sync._write_checkpoint = AsyncMock()
    await sync.fetch_issues_batched(make_repo(repo_id=1001))
    groups = [call.args[0] for call in c.comments.settle_stored_attachments.await_args_list]
    assert groups == ["1001-work-items", "1001-pull-requests"]


async def test_an_ordinary_incremental_sync_does_not_scan_attachments() -> None:
    c = make_mock_connector()
    c.runtime.ds_call = AsyncMock(return_value=ok_response([]))
    c.issue_reconciler.source_total = AsyncMock(return_value=5)
    sync = IssuesSync(c)
    sync._read_checkpoint = AsyncMock(return_value={"last_sync_time": 1, "issue_total": 5})
    sync._write_checkpoint = AsyncMock()
    await sync.fetch_issues_batched(make_repo())
    c.comments.settle_stored_attachments.assert_not_awaited()


def test_full_sync_coverage_names_the_sync_window() -> None:
    """An issue last updated before the window is not listed, not gone."""
    from app.connectors.core.registry.filters import Filter, SyncFilterKey

    c = GitHubTeamsConnector.__new__(GitHubTeamsConnector)
    c.sync_filters = FilterCollection()
    assert GitHubTeamsConnector.full_sync_coverage(c).window_start_ms is None
    start = 1_790_000_000_000
    c.sync_filters = FilterCollection(filters=[Filter(
        key=SyncFilterKey.MODIFIED.value, type="datetime", operator="is_after", value={"start": start, "end": None})])
    coverage = GitHubTeamsConnector.full_sync_coverage(c)
    assert coverage.complete is True and coverage.window_start_ms == start


async def test_issues_turned_off_hide_every_issue_without_failing_the_sync() -> None:
    """GitHub answers 410 for the listing of a repository with issues turned off."""
    c = make_mock_connector()
    c.issue_reconciler.remove_all = AsyncMock()
    c.record_sync_point.read_sync_point = AsyncMock(return_value={"last_sync_time": 1})
    c.record_sync_point.update_sync_point = AsyncMock()
    c.comments.settle_stored_attachments = AsyncMock()
    repo = make_repo()
    repo.has_issues = False
    sync = IssuesSync(c)
    assert await sync.fetch_issues_batched(repo) is True
    c.issue_reconciler.remove_all.assert_awaited_once_with(repo, f"{repo.id}-work-items")
    # The watermark goes, so turning issues on reads them all; the PR group is settled once.
    c.record_sync_point.update_sync_point.assert_awaited_once_with(ANY, {"issue_total": 0})
    c.comments.settle_stored_attachments.assert_awaited_once_with(f"{repo.id}-pull-requests")
    c.runtime.ds_call.assert_not_called()

    c.record_sync_point.read_sync_point.return_value = {"issue_total": 0}
    assert await sync.fetch_issues_batched(repo) is True
    c.comments.settle_stored_attachments.assert_awaited_once()


def test_pull_request_watermark_is_inclusive() -> None:
    sync = PullRequestsSync(make_mock_connector())
    at = datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert sync._is_after(SimpleNamespace(updated_at=at), at) is True


# --------------------------------------------------------------------- scope resolution


def _filters(**values: list[str]) -> FilterCollection:
    return FilterCollection.from_dict({k: {"operator": "in", "type": "select", "value": v} for k, v in values.items()})


class TestScopeResolution:
    async def test_a_404_is_a_definite_answer(self) -> None:
        c = make_mock_connector()
        c.sync_filters = _filters(repo_ids=["acme/widgets"])
        c.runtime.ds_call = AsyncMock(return_value=failed_response("Not Found", status_code=404))
        repos, complete = await ProjectsSync(c).resolve_repo_scope()
        assert repos == [] and complete is True

    async def test_a_5xx_leaves_the_scope_unknown(self) -> None:
        c = make_mock_connector()
        c.sync_filters = _filters(repo_ids=["acme/widgets"])
        c.runtime.ds_call = AsyncMock(return_value=failed_response("Bad Gateway", status_code=502))
        repos, complete = await ProjectsSync(c).resolve_repo_scope()
        assert repos == [] and complete is False

    async def test_removal_runs_only_on_a_complete_scope(self) -> None:
        c = make_mock_connector()
        sync = ProjectsSync(c)
        sync.resolve_repo_scope = AsyncMock(return_value=([], False))
        await sync.sync_all_repos()
        c.repo_scope.remove_out_of_scope.assert_not_awaited()
        assert sync.failures
        sync.resolve_repo_scope = AsyncMock(return_value=([], True))
        await sync.sync_all_repos()
        c.repo_scope.remove_out_of_scope.assert_awaited_once_with(set(), sync._still_in_scope)
        c.repo_scope.remove_empty_orgs.assert_awaited_once_with(set())

    async def test_a_failed_removal_still_syncs_the_repositories_in_scope(self) -> None:
        c = make_mock_connector()
        sync = ProjectsSync(c)
        sync.resolve_repo_scope = AsyncMock(return_value=([make_repo()], True))
        sync._sync_repo = AsyncMock(return_value=True)
        c.repo_scope.remove_out_of_scope = AsyncMock(side_effect=RuntimeError("cascade delete failed"))
        await sync.sync_all_repos()
        sync._sync_repo.assert_awaited_once()
        assert any("cascade delete failed" in f for f in sync.failures)
        c.repo_scope.remove_empty_orgs.assert_not_awaited()

    async def test_old_org_groups_go_only_after_every_repository_synced(self) -> None:
        """A repository transferred to another org still sits under its old org group
        until its own sync moves it; deleting that group first took the repository."""
        c = make_mock_connector()
        sync = ProjectsSync(c)
        order: list[str] = []
        sync.resolve_repo_scope = AsyncMock(return_value=([make_repo()], True))
        sync._sync_repo = AsyncMock(side_effect=lambda r: order.append("repo") or True)
        sync._flush_org_record_groups = AsyncMock()
        c.repo_scope.remove_empty_orgs = AsyncMock(side_effect=lambda ids: order.append("orgs"))
        await sync.sync_all_repos()
        assert order == ["repo", "orgs"]

    async def test_old_org_groups_stay_when_a_repository_did_not_sync(self) -> None:
        c = make_mock_connector()
        sync = ProjectsSync(c)
        sync.resolve_repo_scope = AsyncMock(return_value=([make_repo()], True))
        sync._sync_repo = AsyncMock(return_value=False)
        sync._flush_org_record_groups = AsyncMock()
        await sync.sync_all_repos()
        c.repo_scope.remove_empty_orgs.assert_not_awaited()

    async def test_a_repository_the_listing_dropped_but_github_still_serves_is_kept(self) -> None:
        c = make_mock_connector()
        c.sync_filters = _filters(org_ids=["acme"])
        c.runtime.ds_call = AsyncMock(return_value=ok_response(make_repo(repo_id=7, name="live")))
        assert await ProjectsSync(c)._still_in_scope(7) is True

    async def test_a_repository_the_filter_no_longer_names_is_out(self) -> None:
        c = make_mock_connector()
        c.sync_filters = _filters(repo_ids=["acme/other"])
        c.runtime.ds_call = AsyncMock(return_value=ok_response(make_repo(repo_id=7, name="widgets")))
        assert await ProjectsSync(c)._still_in_scope(7) is False

    @pytest.mark.parametrize(("filters", "kept"), [
        # The org filter still applies when a repository is excluded.
        ({"org_ids": ("in", ["acme"]), "repo_ids": ("not_in", ["acme/x"])}, False),
        ({"org_ids": ("not_in", ["beta"]), "repo_ids": ("not_in", ["acme/x"])}, False),
        ({"org_ids": ("in", ["beta"]), "repo_ids": ("not_in", ["acme/x"])}, True),
        ({"org_ids": ("in", ["beta"]), "repo_ids": ("not_in", ["beta/foo"])}, False),
        ({"repo_ids": ("not_in", ["acme/x"])}, True),
    ])
    async def test_both_filters_decide_whether_a_repository_is_still_in_scope(
        self, filters: dict[str, tuple[str, list[str]]], kept: bool,
    ) -> None:
        c = make_mock_connector()
        # An exclusion needs a list type; older configurations carry one.
        types = {"org_ids": "multiselect", "repo_ids": "list"}
        c.sync_filters = FilterCollection.from_dict(
            {k: {"operator": op, "type": types[k], "value": v} for k, (op, v) in filters.items()})
        assert len(c.sync_filters.filters) == len(filters)
        foo = make_repo(repo_id=7, name="foo")
        foo.full_name, foo.owner.login = "beta/foo", "beta"
        c.runtime.ds_call = AsyncMock(return_value=ok_response(foo))
        assert await ProjectsSync(c)._still_in_scope(7) is kept

    async def test_no_answer_is_not_a_removal(self) -> None:
        c = make_mock_connector()
        c.sync_filters = _filters(repo_ids=["acme/widgets"])
        c.runtime.ds_call = AsyncMock(return_value=failed_response("Bad Gateway", status_code=502))
        assert await ProjectsSync(c)._still_in_scope(7) is None
        c.runtime.ds_call = AsyncMock(return_value=failed_response("Not Found", status_code=404))
        assert await ProjectsSync(c)._still_in_scope(7) is False


class TestConnectorGuards:
    def _connector(self, filters: FilterCollection | None) -> GitHubTeamsConnector:
        c = GitHubTeamsConnector.__new__(GitHubTeamsConnector)
        c.sync_filters = filters
        return c

    def test_no_repository_filter_refuses_to_sync_everything(self) -> None:
        with pytest.raises(RuntimeError):
            self._connector(FilterCollection())._require_scope_filter()
        with pytest.raises(RuntimeError):
            self._connector(None)._require_scope_filter()

    def test_a_repository_filter_is_enough(self) -> None:
        self._connector(_filters(repo_ids=["acme/widgets"]))._require_scope_filter()

    @pytest.mark.parametrize(("public_email", "bound", "linked"), [
        (None, "it@acme.com", "it@acme.com"),  # private email: the one user sync bound
        ("me@gmail.example", "it@acme.com", "it@acme.com"),  # the bound one holds the grants
        ("me@acme.com", None, "me@acme.com"),  # nothing bound: the public one, as before
    ])
    async def test_the_connected_account_is_linked_through_the_email_its_grants_carry(
        self, public_email, bound, linked,
    ) -> None:
        c = self._connector(None)
        c.data_source = MagicMock()
        c.runtime = MagicMock()
        c.runtime.ds_call = AsyncMock(return_value=ok_response(SimpleNamespace(email=public_email, id=77)))
        c.users = MagicMock()
        c.users.resolved_email = MagicMock(return_value=bound)
        c.register_authenticated_source_user = AsyncMock()
        await c._register_authenticated_identity()
        c.users.resolved_email.assert_called_once_with(77)
        c.register_authenticated_source_user.assert_awaited_once_with(linked, "77")


async def test_a_code_prune_that_fails_holds_the_checkpoint_back() -> None:
    from app.connectors.sources.github_teams.repos import ReposSync
    c = make_mock_connector()
    sync = ReposSync(c)
    sync._list_code_records_by_path = AsyncMock(side_effect=[{"a.py": "r1", "b.py": "r2"}, {}])
    sync._is_excluded_path = MagicMock(return_value=False)
    c.data_entities_processor.on_records_deleted_cascade = AsyncMock(side_effect=RuntimeError("store down"))
    assert await sync._prune_deleted_paths(make_repo(), {"a.py"}) is False
    c.data_entities_processor.on_records_deleted_cascade = AsyncMock(return_value={"success": True})
    sync._list_code_records_by_path = AsyncMock(side_effect=[{"a.py": "r1", "b.py": "r2"}, {}])
    assert await sync._prune_deleted_paths(make_repo(), {"a.py"}) is True


# --------------------------------------------------------------------- transport, images, attachments


def test_dropped_connections_are_retried() -> None:
    for name in ("ConnectError", "ReadTimeout", "RemoteProtocolError"):
        assert RuntimeHelper._is_retryable_error(GitHubResponse(success=False, exception_type=name)) is True
    assert RuntimeHelper._is_retryable_error(GitHubResponse(success=False, exception_type="TimeoutError")) is False
    assert RuntimeHelper._is_retryable_error(GitHubResponse(success=False, status_code=404, exception_type="X")) is False


async def test_image_download_stops_at_the_limit(monkeypatch) -> None:
    sent = {"bytes": 0}

    async def body() -> AsyncIterator[bytes]:
        for _ in range(64):
            sent["bytes"] += 65536
            yield b"\x00" * 65536

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body())

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        "app.sources.external.github.github_async.httpx.AsyncClient",
        lambda *a, **k: real_client(*a, **{**k, "transport": httpx.MockTransport(handler)}),
    )
    client = MagicMock()
    client.get_client.return_value.get_token.return_value = "t"
    res = await GitHubAsyncDataSource(client).get_img_bytes("https://github.com/user-attachments/assets/x", max_bytes=200_000)
    assert res.success is False
    assert sent["bytes"] < 1_000_000


def test_attachment_key_names_url_and_parent() -> None:
    key = attachment_key("https://github.com/user-attachments/files/1/a.pdf", "1001/issues/7")
    assert key == "https://github.com/user-attachments/files/1/a.pdf#1001/issues/7"
    assert attachment_url(key) == "https://github.com/user-attachments/files/1/a.pdf"
    assert attachment_url("https://github.com/user-attachments/files/1/a.pdf") == "https://github.com/user-attachments/files/1/a.pdf"


async def test_unlinked_and_url_keyed_attachments_are_removed() -> None:
    c = make_mock_connector()
    url = "https://github.com/user-attachments/files/1/a.pdf"
    kept = _record("k", attachment_key(url, "1001/issues/1"), RecordType.FILE.value)
    legacy = _record("l", url, RecordType.FILE.value)
    unlinked = _record("u", attachment_key("https://github.com/user-attachments/files/2/b.pdf", "1001/issues/1"), RecordType.FILE.value)
    sub_issue = _record("s", "1001/issues/2")
    c.data_entities_processor.get_records_by_parent = AsyncMock(return_value=[kept, legacy, unlinked, sub_issue])
    await CommentsHelper(c).remove_unlinked_attachments(_record("p", "1001/issues/1"), {kept.external_record_id})
    removed = [r.id for r in c.record_remover.remove_records.call_args.args[0]]
    assert removed == ["l", "u"]


async def test_large_pull_request_inlines_a_bounded_number_of_files() -> None:
    from app.connectors.sources.github_teams.constants import PR_FILE_INLINE_MAX_FILES

    c = make_mock_connector()
    # Only a changed file GitHub gives no diff for (binary or too large) is fetched.
    files = [SimpleNamespace(filename=f"f{n}.bin", status="modified", patch=None, additions=0, deletions=0)
             for n in range(PR_FILE_INLINE_MAX_FILES + 5)]
    content = SimpleNamespace(size=3, decoded_content=b"abc", path="x")

    async def ds_call(method: object, *args: object, **kwargs: object) -> GitHubResponse:
        if method is c.data_source.get_pull_file_changes:
            return ok_response(files)
        if method is c.data_source.get_file_contents:
            return ok_response(content)
        return ok_response([])

    c.runtime.ds_call = AsyncMock(side_effect=ds_call)
    record = SimpleNamespace(external_record_id="1001/pull/1", id="r", record_type=RecordType.PULL_REQUEST.value)
    pr = SimpleNamespace(head=SimpleNamespace(sha="h"), html_url="https://github.com/acme/widgets/pull/1")
    groups, _ = await CommentsHelper(c).build_pr_comment_and_diff_blocks("acme", "widgets", 1, pr, 0, record)
    fetched = [call for call in c.runtime.ds_call.await_args_list if call.args[0] is c.data_source.get_file_contents]
    assert len(fetched) == PR_FILE_INLINE_MAX_FILES
    assert len(groups) == PR_FILE_INLINE_MAX_FILES + 5
    assert "omitted" in groups[-1].data


async def test_attachments_and_images_are_fetched_without_the_token(monkeypatch) -> None:
    """github.com/user-attachments answers an API token with 404; the token must not go there."""
    seen: list[str | None] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers.get("Authorization"))
        return httpx.Response(200, content=b"data")

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        "app.sources.external.github.github_async.httpx.AsyncClient",
        lambda *a, **k: real_client(*a, **{**k, "transport": httpx.MockTransport(handler)}),
    )
    client = MagicMock()
    client.get_client.return_value.get_token.return_value = "secret-token"
    ds = GitHubAsyncDataSource(client)
    assert (await ds.get_img_bytes("https://github.com/user-attachments/assets/x")).data == b"data"
    chunks = [c async for c in ds.get_attachment_files_content("https://github.com/user-attachments/files/1/a.txt")]
    assert b"".join(chunks) == b"data"
    assert seen == [None, None]
