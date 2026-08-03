"""Unit tests for github_teams IssuesSync.

Covers:
- fetch_issues_batched: splits issues vs. PR stubs (via ``pull_request`` attr)
  and delegates PR stubs to PullRequestsSync instead of double-processing them.
- _process_issue_to_ticket: field mapping (labels, assignees, external ids).
- parse_repo_id_and_number_from_record: external id parsing round-trip.
- check_and_fetch_updated_ticket_for_reindex: unchanged revision -> None;
  changed revision -> returns a fresh (record, permissions) pair.
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.connectors.sources.github_teams.issues import IssuesSync
from app.models.entities import TicketRecord

from .conftest import failed_response, make_mock_connector, make_repo, ok_response

pytestmark = pytest.mark.anyio


@pytest.fixture()
def anyio_backend() -> str:
    return "asyncio"


def _issue(*, number: int = 1, is_pr: bool = False, title: str = "Bug", state: str = "open") -> SimpleNamespace:
    return SimpleNamespace(
        number=number,
        title=title,
        state=state,
        body="issue body",
        labels=[SimpleNamespace(name="bug")],
        assignees=[SimpleNamespace(login="alice")],
        html_url=f"https://github.com/acme/widgets/issues/{number}",
        created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        updated_at=datetime(2024, 1, 2, tzinfo=timezone.utc),
        pull_request=SimpleNamespace() if is_pr else None,
    )


class TestFetchIssuesBatched:
    async def test_splits_issues_and_prs_and_delegates_prs(self) -> None:
        c = make_mock_connector()
        repo = make_repo(repo_id=1)
        c.runtime.ds_call.side_effect = _dispatch(c, {
            "list_issues": ok_response([_issue(number=1, is_pr=False), _issue(number=2, is_pr=True)]),
            "list_issue_comments": ok_response([]),
        })
        c.pull_requests.process_pull_request_stub = AsyncMock(return_value=None)
        c.comments.clean_github_content = AsyncMock(return_value=("", []))
        c.indexing_filters = None

        sync = IssuesSync(c)
        await sync.fetch_issues_batched(repo)

        c.pull_requests.process_pull_request_stub.assert_awaited_once()
        called_issue = c.pull_requests.process_pull_request_stub.call_args.args[1]
        assert called_issue.number == 2

        persisted = c.data_entities_processor.on_new_records.call_args.args[0]
        assert len(persisted) == 1
        assert persisted[0][0].record_name == "Bug"

    async def test_no_issues_is_noop(self) -> None:
        c = make_mock_connector()
        repo = make_repo(repo_id=1)
        c.runtime.ds_call.return_value = ok_response([])

        sync = IssuesSync(c)
        await sync.fetch_issues_batched(repo)

        c.data_entities_processor.on_new_records.assert_not_awaited()

    async def test_fetch_failure_logs_and_returns(self) -> None:
        c = make_mock_connector()
        repo = make_repo(repo_id=1)
        c.runtime.ds_call.return_value = failed_response("500 error")

        sync = IssuesSync(c)
        await sync.fetch_issues_batched(repo)

        c.data_entities_processor.on_new_records.assert_not_awaited()


class TestProcessIssueToTicket:
    async def test_maps_fields_and_marks_new(self) -> None:
        c = make_mock_connector()
        repo = make_repo(repo_id=42)
        issue = _issue(number=7, title="Crash on startup")

        sync = IssuesSync(c)
        ru = await sync._process_issue_to_ticket(repo, issue)

        assert ru is not None
        assert ru.is_new is True
        assert ru.record.external_record_id == "42/issues/7"
        assert ru.record.external_record_group_id == "42-work-items"
        assert ru.record.labels == ["bug"]
        assert ru.record.assignee_source_id == ["alice"]
        assert ru.record.status == "open"

    async def test_existing_record_with_title_change_marks_metadata_changed(self) -> None:
        c = make_mock_connector()
        repo = make_repo(repo_id=42)
        issue = _issue(number=7, title="New Title")
        c.tx_store.get_record_by_external_id = AsyncMock(
            return_value=SimpleNamespace(id="rec-existing", record_name="Old Title")
        )

        sync = IssuesSync(c)
        ru = await sync._process_issue_to_ticket(repo, issue)

        assert ru is not None
        assert ru.is_new is False
        assert ru.is_updated is True
        assert ru.metadata_changed is True
        assert ru.record.id == "rec-existing"


class TestParseRepoIdAndNumber:
    def test_round_trip(self) -> None:
        c = make_mock_connector()
        sync = IssuesSync(c)
        record = TicketRecord(
            id="r1", org_id="org-1", record_name="x", record_type="TICKET",
            version=0, origin="CONNECTOR", connector_name="GITHUB TEAMS", connector_id="c-1",
            external_record_id="42/issues/7", external_record_group_id="42-work-items",
        )
        assert sync.parse_repo_id_and_number_from_record(record) == (42, 7)

    def test_missing_group_id_returns_none(self) -> None:
        c = make_mock_connector()
        sync = IssuesSync(c)
        record = TicketRecord(
            id="r1", org_id="org-1", record_name="x", record_type="TICKET",
            version=0, origin="CONNECTOR", connector_name="GITHUB TEAMS", connector_id="c-1",
            external_record_id="42/issues/7",
        )
        assert sync.parse_repo_id_and_number_from_record(record) is None


class TestReindexCheck:
    async def test_unchanged_revision_returns_none(self) -> None:
        c = make_mock_connector()
        repo = make_repo(repo_id=42)
        issue = _issue(number=7)
        c.runtime.ds_call.side_effect = _dispatch(c, {
            "get_repo_by_id": ok_response(repo),
            "get_issue": ok_response(issue),
        })
        sync = IssuesSync(c)
        unchanged_rev = str(sync._datetime_to_epoch_ms(issue.updated_at))
        record = TicketRecord(
            id="r1", org_id="org-1", record_name="x", record_type="TICKET",
            version=0, origin="CONNECTOR", connector_name="GITHUB TEAMS", connector_id="c-1",
            external_record_id="42/issues/7", external_record_group_id="42-work-items",
            external_revision_id=unchanged_rev,
        )

        result = await sync.check_and_fetch_updated_ticket_for_reindex(record)
        assert result is None

    async def test_changed_revision_returns_fresh_record(self) -> None:
        c = make_mock_connector()
        repo = make_repo(repo_id=42)
        issue = _issue(number=7, title="Updated title")
        c.runtime.ds_call.side_effect = _dispatch(c, {
            "get_repo_by_id": ok_response(repo),
            "get_issue": ok_response(issue),
        })
        sync = IssuesSync(c)
        record = TicketRecord(
            id="r1", org_id="org-1", record_name="x", record_type="TICKET",
            version=0, origin="CONNECTOR", connector_name="GITHUB TEAMS", connector_id="c-1",
            external_record_id="42/issues/7", external_record_group_id="42-work-items",
            external_revision_id="stale-rev",
        )

        result = await sync.check_and_fetch_updated_ticket_for_reindex(record)
        assert result is not None
        fresh_record, _perms = result
        assert fresh_record.record_name == "Updated title"

    async def test_malformed_external_ids_returns_none(self) -> None:
        c = make_mock_connector()
        sync = IssuesSync(c)
        record = TicketRecord(
            id="r1", org_id="org-1", record_name="x", record_type="TICKET",
            version=0, origin="CONNECTOR", connector_name="GITHUB TEAMS", connector_id="c-1",
            external_record_id="not-a-number",
        )
        result = await sync.check_and_fetch_updated_ticket_for_reindex(record)
        assert result is None


def _dispatch(c: object, mapping: dict[str, object]) -> object:
    by_identity = {getattr(c.data_source, name): response for name, response in mapping.items()}

    def _fn(method: object, *args: object, **kwargs: object) -> object:
        if method in by_identity:
            return by_identity[method]
        raise AssertionError(f"unmocked ds_call for {method!r}")

    return _fn
