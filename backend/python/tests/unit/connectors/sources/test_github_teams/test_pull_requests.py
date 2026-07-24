"""Unit tests for github_teams PullRequestsSync.

Covers:
- process_pull_request_stub: field mapping (labels, assignees, merge state,
  last_commit_sha) and external id construction.
- Merged PR status override ("merged" vs. raw pr.state).
- check_and_fetch_updated_pr_for_reindex: unchanged revision -> None; changed
  revision -> delegates to process_pull_request_stub via a synthetic stub.
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.connectors.sources.github_teams.pull_requests import PullRequestsSync
from app.models.entities import PullRequestRecord

from .conftest import failed_response, make_mock_connector, make_repo, ok_response

pytestmark = pytest.mark.anyio


@pytest.fixture()
def anyio_backend() -> str:
    return "asyncio"


def _pr(
    *, number: int = 1, title: str = "Add feature", state: str = "open", merged: bool = False,
    mergeable: bool | None = True, head_sha: str = "sha-head",
) -> SimpleNamespace:
    return SimpleNamespace(
        number=number, title=title, state=state, merged=merged, mergeable=mergeable,
        merged_by=SimpleNamespace(login="maintainer") if merged else None,
        labels=[SimpleNamespace(name="enhancement")],
        assignees=[SimpleNamespace(login="bob")],
        html_url=f"https://github.com/acme/widgets/pull/{number}",
        created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        updated_at=datetime(2024, 1, 3, tzinfo=timezone.utc),
        head=SimpleNamespace(sha=head_sha),
        body="pr body",
    )


class TestProcessPullRequestStub:
    async def test_maps_fields_for_open_pr(self) -> None:
        c = make_mock_connector()
        repo = make_repo(repo_id=10)
        pr = _pr(number=5)
        c.runtime.ds_call.return_value = ok_response(pr)

        sync = PullRequestsSync(c)
        ru = await sync.process_pull_request_stub(repo, SimpleNamespace(number=5))

        assert ru is not None
        assert isinstance(ru.record, PullRequestRecord)
        assert ru.record.external_record_id == "10/pull/5"
        assert ru.record.external_record_group_id == "10-pull-requests"
        assert ru.record.status == "open"
        assert ru.record.labels == ["enhancement"]
        assert ru.record.assignee == ["bob"]
        assert ru.record.last_commit_sha == "sha-head"
        assert ru.record.mergeable == "True"
        assert ru.record.merged_by is None

    async def test_merged_pr_status_overridden(self) -> None:
        c = make_mock_connector()
        repo = make_repo(repo_id=10)
        pr = _pr(number=5, state="closed", merged=True)
        c.runtime.ds_call.return_value = ok_response(pr)

        sync = PullRequestsSync(c)
        ru = await sync.process_pull_request_stub(repo, SimpleNamespace(number=5))

        assert ru.record.status == "merged"
        assert ru.record.merged_by == "maintainer"

    async def test_get_pull_failure_returns_none(self) -> None:
        c = make_mock_connector()
        repo = make_repo(repo_id=10)
        c.runtime.ds_call.return_value = failed_response("404")

        sync = PullRequestsSync(c)
        ru = await sync.process_pull_request_stub(repo, SimpleNamespace(number=5))

        assert ru is None

    async def test_indexing_disabled_sets_auto_index_off(self) -> None:
        c = make_mock_connector()
        repo = make_repo(repo_id=10)
        pr = _pr(number=5)
        c.runtime.ds_call.return_value = ok_response(pr)
        c.indexing_filters = SimpleNamespace(is_enabled=lambda _key: False)

        sync = PullRequestsSync(c)
        ru = await sync.process_pull_request_stub(repo, SimpleNamespace(number=5))

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
        unchanged_rev = str(sync._datetime_to_epoch_ms(pr.updated_at))
        record = PullRequestRecord(
            id="r1", org_id="org-1", record_name="x", record_type="PULL_REQUEST",
            version=0, origin="CONNECTOR", connector_name="GITHUB TEAMS", connector_id="c-1",
            external_record_id="10/pull/5", external_record_group_id="10-pull-requests",
            external_revision_id=unchanged_rev,
        )

        result = await sync.check_and_fetch_updated_pr_for_reindex(record)
        assert result is None

    async def test_changed_revision_delegates_to_process_stub(self) -> None:
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


def _dispatch(c: object, mapping: dict[str, object]) -> object:
    by_identity = {getattr(c.data_source, name): response for name, response in mapping.items()}

    def _fn(method: object, *args: object, **kwargs: object) -> object:
        if method in by_identity:
            return by_identity[method]
        raise AssertionError(f"unmocked ds_call for {method!r}")

    return _fn
