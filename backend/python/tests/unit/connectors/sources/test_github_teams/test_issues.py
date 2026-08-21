"""Unit tests for github_teams IssuesSync.

Covers:
- fetch_issues_batched: drops PR stubs when ``html_url`` contains ``/pull/``
  (``PullRequestsSync`` pages ``/pulls`` on its own).
- _process_issue_to_ticket: field mapping (labels, assignees, external ids).
- parse_repo_id_and_number_from_record: external id parsing round-trip.
- check_and_fetch_updated_ticket_for_reindex: unchanged revision -> None;
  changed revision -> returns a fresh (record, permissions) pair.
"""
from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.connectors.sources.github_teams.common.utils import epoch_ms_or_now
from app.connectors.sources.github_teams.issues import IssuesSync
from app.models.entities import TicketRecord

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


class _ListedIssue(SimpleNamespace):
    """An issue as it comes out of a ``PaginatedList``.

    Real list-derived objects are built with ``completed=False``, so reading
    ``.raw_data`` calls ``_completeIfNeeded()`` and fires ``GET /issues/{n}`` —
    one blocking request per issue. Raising turns that N+1 into a test failure
    rather than a sync that is merely slow.
    """

    @property
    def raw_data(self) -> dict:
        raise AssertionError(
            "raw_data completes a list-derived issue (one GET each); "
            "use listing_payload() to read the stored listing payload"
        )


def _issue(
    *, number: int = 1, is_pr: bool = False, title: str = "Bug", state: str = "open",
    state_reason: str | None = None, issue_type: str | None = None,
    parent_issue_url: str | None = None,
    field_priority: str | None = None,
    blocked_by: int | None = None,
    blocking: int | None = None,
) -> SimpleNamespace:
    # type, parent_issue_url and issue_field_values ride the listing payload but
    # are not parsed by PyGithub 2.8, so they are read off _rawData directly.
    raw_data: dict = {}
    if issue_type:
        raw_data["type"] = {"name": issue_type}
    if parent_issue_url:
        raw_data["parent_issue_url"] = parent_issue_url
    if field_priority:
        raw_data["issue_field_values"] = [{
            "issue_field_id": 45257346,
            "data_type": "single_select",
            "issue_field_name": "Priority",
            "value": 79211123,
            "single_select_option": {"id": 79211123, "name": field_priority, "color": "red"},
        }]
    if blocked_by is not None or blocking is not None:
        raw_data["issue_dependencies_summary"] = {
            "blocked_by": blocked_by or 0, "total_blocked_by": blocked_by or 0,
            "blocking": blocking or 0, "total_blocking": blocking or 0,
        }
    return _ListedIssue(
        number=number,
        title=title,
        state=state,
        state_reason=state_reason,
        _rawData=raw_data,
        user=SimpleNamespace(login="reporter-bob", id=901),
        body="issue body",
        labels=[SimpleNamespace(name="bug")],
        assignees=[SimpleNamespace(login="alice", id=801)],
        # GitHub gives PR items a /pull/ html_url; that, not `.pull_request`,
        # is what distinguishes them without triggering a lazy fetch.
        html_url=(
            f"https://github.com/acme/widgets/pull/{number}" if is_pr
            else f"https://github.com/acme/widgets/issues/{number}"
        ),
        created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        updated_at=datetime(2024, 1, 2, tzinfo=timezone.utc),
        pull_request=SimpleNamespace() if is_pr else None,
    )


class TestFetchIssuesBatched:
    async def test_pr_stubs_are_skipped_entirely(self) -> None:
        """The /issues listing returns PR stubs too, but they lack head refs and
        reviewers — recovering those cost one get_pull per PR, the single
        largest expense in a sync. PRs now page from /pulls instead, so these
        stubs must be dropped here rather than mapped."""
        c = make_mock_connector()
        repo = make_repo(repo_id=1)
        c.runtime.ds_call.side_effect = _dispatch(c, {
            "list_issues": ok_response([_issue(number=1, is_pr=False), _issue(number=2, is_pr=True)]),
            "list_issue_comments": ok_response([]),
        })
        c.comments.clean_github_content = AsyncMock(return_value=("", []))
        c.indexing_filters = None

        sync = IssuesSync(c)
        await sync.fetch_issues_batched(repo)

        persisted = c.data_entities_processor.on_new_records.call_args.args[0]
        assert len(persisted) == 1
        assert persisted[0][0].record_name == "Bug"
        assert persisted[0][0].record_type.value == "TICKET"

    async def test_checkpoint_not_advanced_when_a_batch_fails(self) -> None:
        """The listing is sorted `updated asc`, so advancing on a later batch's
        success would commit past an earlier failure and those issues would
        never be re-fetched."""
        c = make_mock_connector()
        c.batch_size = 1
        repo = make_repo(repo_id=1)
        c.runtime.ds_call.side_effect = _dispatch(c, {
            "list_issues": ok_response([_issue(number=1), _issue(number=2)]),
        })
        c.comments.clean_github_content = AsyncMock(return_value=("", []))
        c.indexing_filters = None
        # First batch fails, second succeeds.
        c.data_entities_processor.on_new_records = AsyncMock(side_effect=[Exception("boom"), None])

        sync = IssuesSync(c)
        sync._get_sync_checkpoint = AsyncMock(return_value=None)
        sync._update_sync_checkpoint = AsyncMock()

        await sync.fetch_issues_batched(repo)

        sync._update_sync_checkpoint.assert_not_awaited()

    async def test_checkpoint_advances_once_when_all_batches_succeed(self) -> None:
        c = make_mock_connector()
        c.batch_size = 1
        repo = make_repo(repo_id=1)
        c.runtime.ds_call.side_effect = _dispatch(c, {
            "list_issues": ok_response([_issue(number=1), _issue(number=2)]),
        })
        c.comments.clean_github_content = AsyncMock(return_value=("", []))
        c.indexing_filters = None

        sync = IssuesSync(c)
        sync._get_sync_checkpoint = AsyncMock(return_value=None)
        sync._update_sync_checkpoint = AsyncMock()

        await sync.fetch_issues_batched(repo)

        # One commit per record group, at the end — not one per batch.
        sync._update_sync_checkpoint.assert_awaited_once()

    async def test_pages_and_persists_each_page_as_one_batch(self) -> None:
        """The page IS the batch: one build, one persist call, one transaction.
        Re-chunking by a constant only split each page into a full batch plus
        an under-filled one, doubling the write transactions."""
        c = make_mock_connector()
        repo = make_repo(repo_id=1)
        full_page = [_issue(number=n) for n in range(100)]
        c.runtime.ds_call.side_effect = _dispatch(c, {
            "list_issues": ok_response(full_page),
            "list_issue_comments": ok_response([]),
        })
        c.comments.clean_github_content = AsyncMock(return_value=("", []))
        c.indexing_filters = None

        sync = IssuesSync(c)
        sync._get_sync_checkpoint = AsyncMock(return_value=None)
        sync._update_sync_checkpoint = AsyncMock()
        # Second page short -> listing exhausted.
        pages = [ok_response(full_page), ok_response([_issue(number=999)])]
        c.runtime.ds_call.side_effect = lambda m, *a, **k: pages.pop(0) if not pages == [] else None

        await sync.fetch_issues_batched(repo)

        # 100 issues in one page -> a single on_new_records call, not two.
        first_call = c.data_entities_processor.on_new_records.call_args_list[0]
        assert len(first_call.args[0]) == 100

    async def test_page_failure_does_not_advance_the_checkpoint(self) -> None:
        """The listing is sorted `updated asc`, so committing past a failed
        page would skip those issues permanently."""
        c = make_mock_connector()
        repo = make_repo(repo_id=1)
        c.runtime.ds_call.side_effect = _dispatch(c, {
            "list_issues": failed_response("500", status_code=500),
        })

        sync = IssuesSync(c)
        sync._get_sync_checkpoint = AsyncMock(return_value=None)
        sync._update_sync_checkpoint = AsyncMock()

        await sync.fetch_issues_batched(repo)

        sync._update_sync_checkpoint.assert_not_awaited()

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
        assert ru.record.assignee_source_id == ["801"]
        assert ru.record.status == "OPEN"

    async def test_populates_people_and_type_fields(self) -> None:
        """These all exist on TicketRecord and were being left empty, so a
        ticket carried no author, no assignee name and no type."""
        c = make_mock_connector()
        repo = make_repo(repo_id=42)
        issue = _issue(number=7, issue_type="Bug")

        sync = IssuesSync(c)
        ru = await sync._process_issue_to_ticket(repo, issue)

        assert ru.record.type == "BUG"
        assert ru.record.creator_name == "reporter-bob"
        assert ru.record.reporter_name == "reporter-bob"
        # Numeric ids, not logins — AppUser.source_user_id is the numeric
        # GitHub id and these fields exist to join against it.
        assert ru.record.reporter_source_id == "901"
        assert ru.record.assignee_source_id == ["801"]
        assert ru.record.assignee == "alice"
        # GitHub exposes logins, not emails; the flag marks the ids as identity.
        assert ru.record.is_email_hidden is True
        assert ru.record.creator_source_timestamp is not None

    async def test_emails_resolved_from_bound_app_users(self) -> None:
        """GitHub never exposes other users' emails, but user sync bound some
        principals to PipesHub identities — those emails must reach the ticket."""
        c = make_mock_connector()
        c.data_entities_processor.get_all_app_users.return_value = [
            SimpleNamespace(source_user_id="901", email="bob@corp.com"),
            SimpleNamespace(source_user_id="801", email="alice@corp.com"),
        ]

        ru = await IssuesSync(c)._process_issue_to_ticket(make_repo(repo_id=42), _issue(number=7))

        assert ru.record.creator_email == "bob@corp.com"
        assert ru.record.reporter_email == "bob@corp.com"
        assert ru.record.assignee_email == "alice@corp.com"

    async def test_multiple_assignees_keep_a_coherent_primary_and_every_id(self) -> None:
        """GitHub allows up to 10 assignees; TicketRecord.assignee_email is
        single-valued and the processor feeds it to a user lookup to build the
        ASSIGNED_TO edge. A joined string would match no user and lose the edge
        entirely, so the paired fields carry GitHub's primary (first) assignee
        and the list field carries everyone."""
        c = make_mock_connector()
        c.data_entities_processor.get_all_app_users.return_value = [
            SimpleNamespace(source_user_id="801", email="alice@corp.com"),
            SimpleNamespace(source_user_id="802", email="carol@corp.com"),
        ]
        issue = _issue(number=7)
        issue.assignees = [
            SimpleNamespace(login="alice", id=801),
            SimpleNamespace(login="carol", id=802),
        ]

        ru = await IssuesSync(c)._process_issue_to_ticket(make_repo(repo_id=42), issue)

        assert ru.record.assignee == "alice"
        assert ru.record.assignee_email == "alice@corp.com"
        assert ru.record.assignee_source_id == ["801", "802"]

    async def test_primary_assignee_without_an_email_does_not_borrow_a_co_assignees(self) -> None:
        """Name and email must describe the same person: an unresolved primary
        leaves the email empty rather than pairing 'alice' with carol's address."""
        c = make_mock_connector()
        c.data_entities_processor.get_all_app_users.return_value = [
            SimpleNamespace(source_user_id="802", email="carol@corp.com"),
        ]
        issue = _issue(number=7)
        issue.assignees = [
            SimpleNamespace(login="alice", id=801),
            SimpleNamespace(login="carol", id=802),
        ]

        ru = await IssuesSync(c)._process_issue_to_ticket(make_repo(repo_id=42), issue)

        assert ru.record.assignee == "alice"
        assert ru.record.assignee_email is None
        assert ru.record.assignee_source_id == ["801", "802"]

    async def test_unbound_users_leave_emails_empty(self) -> None:
        c = make_mock_connector()
        c.data_entities_processor.get_all_app_users.return_value = []

        ru = await IssuesSync(c)._process_issue_to_ticket(make_repo(repo_id=42), _issue(number=7))

        assert ru.record.creator_email is None
        assert ru.record.assignee_email is None

    async def test_sub_issue_links_to_its_parent_in_the_same_repo(self) -> None:
        """parent_issue_url is on the payload (via raw_data — no extra API
        call); same-repo parents convert to the repo-id-keyed external id
        without any lookup."""
        c = make_mock_connector()
        repo = make_repo(repo_id=42, owner_login="acme", name="widgets")
        issue = _issue(
            number=7,
            parent_issue_url="https://api.github.com/repos/acme/widgets/issues/3",
        )

        sync = IssuesSync(c)
        ru = await sync._process_issue_to_ticket(repo, issue)

        assert ru.record.parent_external_record_id == "42/issues/3"
        assert ru.record.parent_record_type == "TICKET"
        # No explicit issue type -> the sub-issue marker becomes the type.
        assert ru.record.type == "SUBTASK"
        c.runtime.ds_call.assert_not_awaited()

    async def test_cross_repo_parent_resolves_via_one_cached_lookup(self) -> None:
        c = make_mock_connector()
        repo = make_repo(repo_id=42, owner_login="acme", name="widgets")
        c.runtime.ds_call.return_value = ok_response(SimpleNamespace(id=99))
        url = "https://api.github.com/repos/acme/tracker/issues/5"

        sync = IssuesSync(c)
        ru1 = await sync._process_issue_to_ticket(repo, _issue(number=7, parent_issue_url=url))
        ru2 = await sync._process_issue_to_ticket(repo, _issue(number=8, parent_issue_url=url))

        assert ru1.record.parent_external_record_id == "99/issues/5"
        assert ru2.record.parent_external_record_id == "99/issues/5"
        c.runtime.ds_call.assert_awaited_once()

    async def test_unresolvable_parent_repo_degrades_to_no_link(self) -> None:
        """A failed lookup is cached too — an unreachable parent repo must cost
        one call per sync, not one per sub-issue, and must not fail the ticket."""
        c = make_mock_connector()
        repo = make_repo(repo_id=42, owner_login="acme", name="widgets")
        c.runtime.ds_call.return_value = failed_response("404 not found")
        url = "https://api.github.com/repos/acme/gone/issues/5"

        sync = IssuesSync(c)
        ru1 = await sync._process_issue_to_ticket(repo, _issue(number=7, parent_issue_url=url))
        ru2 = await sync._process_issue_to_ticket(repo, _issue(number=8, parent_issue_url=url))

        assert ru1.record.parent_external_record_id is None
        assert ru2.record.parent_external_record_id is None
        c.runtime.ds_call.assert_awaited_once()

    async def test_priority_from_inline_issue_field(self) -> None:
        """issue_field_values arrives inlined on the listing payload (additive,
        not version-gated — verified live), so the structured Priority costs
        zero extra calls. It is the ONLY priority source — labels are not
        interpreted."""
        c = make_mock_connector()
        issue = _issue(number=7, field_priority="High")
        issue.labels = [SimpleNamespace(name="low")]  # must NOT influence priority

        ru = await IssuesSync(c)._process_issue_to_ticket(make_repo(repo_id=42), issue)

        assert ru.record.priority == "HIGH"
        c.runtime.ds_call.assert_not_awaited()

    async def test_unrecognised_field_option_is_preserved_verbatim(self) -> None:
        c = make_mock_connector()
        ru = await IssuesSync(c)._process_issue_to_ticket(
            make_repo(repo_id=42), _issue(number=7, field_priority="Someday"),
        )
        assert ru.record.priority == "Someday"

    async def test_priority_labels_are_not_interpreted(self) -> None:
        """No issue field -> no priority, even when a label looks like one."""
        c = make_mock_connector()
        issue = _issue(number=7)
        issue.labels = [SimpleNamespace(name="priority: high")]

        ru = await IssuesSync(c)._process_issue_to_ticket(make_repo(repo_id=42), issue)

        assert ru.record.priority is None

    async def test_no_dependencies_costs_zero_calls(self) -> None:
        """issue_dependencies_summary on the listing payload is the free gate:
        an issue with zero counts must trigger no dependency calls."""
        c = make_mock_connector()
        ru = await IssuesSync(c)._process_issue_to_ticket(
            make_repo(repo_id=42), _issue(number=7, blocked_by=0, blocking=0),
        )
        assert ru.record.related_external_records == []
        c.runtime.ds_call.assert_not_awaited()

    async def test_blocked_by_side_is_not_fetched(self) -> None:
        """GitHub reports one dependency from both ends. Reading only the
        blocker's side keeps it to one edge per user-visible link (and one
        call): the blocked issue emits nothing and pays nothing."""
        c = make_mock_connector()
        c.runtime.ds_call.side_effect = _dispatch(c, {
            "list_issue_blocked_by": ok_response([
                {"number": 2, "title": "Fix user syncing", "repository": {"id": 42}},
            ]),
        })

        ru = await IssuesSync(c)._process_issue_to_ticket(
            make_repo(repo_id=42), _issue(number=5, blocked_by=1, blocking=0),
        )

        assert ru.record.related_external_records == []
        c.runtime.ds_call.assert_not_awaited()

    async def test_blocking_becomes_blocks_link_with_cross_repo_target(self) -> None:
        """The dependency payload embeds repository.id, so a cross-repo target
        maps to its own repo id with no extra lookup."""
        c = make_mock_connector()
        c.runtime.ds_call.side_effect = _dispatch(c, {
            "list_issue_blocking": ok_response([
                {"number": 9, "title": "Downstream", "repository": {"id": 777}},
            ]),
        })

        ru = await IssuesSync(c)._process_issue_to_ticket(
            make_repo(repo_id=42), _issue(number=2, blocked_by=0, blocking=1),
        )

        link = ru.record.related_external_records[0]
        assert link.external_record_id == "777/issues/9"
        assert link.relation_type.value == "BLOCKS"

    async def test_dependency_fetch_failure_does_not_fail_the_ticket(self) -> None:
        c = make_mock_connector()
        c.runtime.ds_call.side_effect = _dispatch(c, {})  # everything unmocked -> failed

        ru = await IssuesSync(c)._process_issue_to_ticket(
            make_repo(repo_id=42), _issue(number=5, blocked_by=1, blocking=1),
        )

        assert ru is not None
        assert ru.record.related_external_records == []

    async def test_custom_issue_type_is_preserved_verbatim(self) -> None:
        """Issue types are org-defined; dropping an unrecognised one loses more
        than not normalising it."""
        c = make_mock_connector()
        ru = await IssuesSync(c)._process_issue_to_ticket(
            make_repo(repo_id=42), _issue(number=7, issue_type="Spike"),
        )
        assert ru.record.type == "Spike"

    @pytest.mark.parametrize(("state", "reason", "expected"), [
        ("open", None, "OPEN"),
        ("reopened", None, "REOPENED"),
        ("closed", "completed", "DONE"),
        ("closed", "not_planned", "CANCELLED"),
        ("closed", "duplicate", "CANCELLED"),
        ("closed", None, "DONE"),
    ])
    async def test_state_reason_distinguishes_done_from_abandoned(
        self, state: str, reason: str | None, expected: str,
    ) -> None:
        """GitHub only has open/closed; without state_reason, work that was
        finished and work that was abandoned look identical."""
        c = make_mock_connector()
        ru = await IssuesSync(c)._process_issue_to_ticket(
            make_repo(repo_id=42), _issue(number=7, state=state, state_reason=reason),
        )
        assert ru.record.status == expected

    async def test_does_not_look_up_the_existing_record(self) -> None:
        """Identity and versioning belong to the processor: _process_record
        queries the same (connector_id, external_record_id) key and overwrites
        record.id with what it finds. Doing it here too cost one Neo4j
        transaction per issue — ~0.68s each, ~33 minutes on a 3k-item repo —
        to produce an id that gets discarded."""
        c = make_mock_connector()
        c.tx_store.get_record_by_external_id = AsyncMock(
            return_value=SimpleNamespace(id="rec-existing", record_name="Old Title")
        )

        ru = await IssuesSync(c)._process_issue_to_ticket(
            make_repo(repo_id=42), _issue(number=7, title="New Title"),
        )

        assert ru is not None
        c.tx_store.get_record_by_external_id.assert_not_awaited()
        # The stable key the processor resolves identity by.
        assert ru.record.external_record_id == "42/issues/7"
        # Left at 0 so the processor derives it from external_revision_id.
        assert ru.record.version == 0


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
        unchanged_rev = str(epoch_ms_or_now(issue.updated_at))
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

    async def test_repo_lookup_failure_returns_none(self) -> None:
        c = make_mock_connector()
        c.runtime.ds_call.side_effect = _dispatch(c, {"get_repo_by_id": failed_response("404")})
        record = TicketRecord(
            id="r1", org_id="org-1", record_name="x", record_type="TICKET",
            version=0, origin="CONNECTOR", connector_name="GITHUB TEAMS", connector_id="c-1",
            external_record_id="42/issues/7", external_record_group_id="42-work-items",
        )
        assert await IssuesSync(c).check_and_fetch_updated_ticket_for_reindex(record) is None

    async def test_issue_lookup_failure_returns_none(self) -> None:
        c = make_mock_connector()
        c.runtime.ds_call.side_effect = _dispatch(c, {
            "get_repo_by_id": ok_response(make_repo(repo_id=42)),
            "get_issue": failed_response("404"),
        })
        record = TicketRecord(
            id="r1", org_id="org-1", record_name="x", record_type="TICKET",
            version=0, origin="CONNECTOR", connector_name="GITHUB TEAMS", connector_id="c-1",
            external_record_id="42/issues/7", external_record_group_id="42-work-items",
        )
        assert await IssuesSync(c).check_and_fetch_updated_ticket_for_reindex(record) is None


class TestIndexingFlags:
    async def test_attachments_follow_indexing_flag(self) -> None:
        """The ticket is stamped here; the attachment is stamped inside the
        real comments helper (single construction point, so the stream-time
        comment-attachment path is covered too)."""
        from app.connectors.sources.github_teams.comments import CommentsHelper

        c = make_mock_connector()
        c.indexing_filters = SimpleNamespace(is_enabled=lambda _key: False)
        c.issues._issues_indexing_enabled = lambda: False
        c.comments = CommentsHelper(c)
        c.comments.clean_github_content = AsyncMock(return_value=("", [
            {"type": "pdf", "href": "https://github.com/user-attachments/files/1/x.pdf", "filename": "x.pdf"},
        ]))

        updates = await IssuesSync(c)._build_issue_records(make_repo(repo_id=1), [_issue(number=1)])

        assert updates[0].record.indexing_status == "AUTO_INDEX_OFF"
        attachment_updates = updates[1:]
        assert attachment_updates
        assert all(u.record.indexing_status == "AUTO_INDEX_OFF" for u in attachment_updates)

    async def test_processing_error_skips_the_issue(self) -> None:
        c = make_mock_connector()

        class _BadIssue:
            number = 9
            html_url = "https://github.com/acme/widgets/issues/9"

            @property
            def title(self) -> str:
                raise RuntimeError("bad title")

        updates = await IssuesSync(c)._build_issue_records(make_repo(repo_id=1), [_BadIssue()])
        assert updates == []


class TestRelatedAndParentEdges:
    async def test_blocking_list_failure_response_degrades(self) -> None:
        c = make_mock_connector()
        c.runtime.ds_call.side_effect = _dispatch(c, {
            "list_issue_blocking": failed_response("403"),
        })
        related = await IssuesSync(c)._related_from_dependencies(
            make_repo(repo_id=42), 2, {"blocking": 1}
        )
        assert related == []

    async def test_blocking_list_exception_degrades(self) -> None:
        c = make_mock_connector()
        c.runtime.ds_call = AsyncMock(side_effect=RuntimeError("timeout"))
        related = await IssuesSync(c)._related_from_dependencies(
            make_repo(repo_id=42), 2, {"blocking": 1}
        )
        assert related == []

    async def test_non_dict_dependency_items_are_skipped(self) -> None:
        c = make_mock_connector()
        c.runtime.ds_call.side_effect = _dispatch(c, {
            "list_issue_blocking": ok_response(["nope", {"title": "no number"}]),
        })
        related = await IssuesSync(c)._related_from_dependencies(
            make_repo(repo_id=42), 2, {"blocking": 1}
        )
        assert related == []

    async def test_unrecognised_parent_url_returns_none(self) -> None:
        sync = IssuesSync(make_mock_connector())
        assert await sync._parent_ticket_external_id(make_repo(repo_id=1), "not-a-url") is None


class TestProcessNewRecordsAndCheckpoints:
    async def test_empty_batch_is_success(self) -> None:
        assert await IssuesSync(make_mock_connector()).process_new_records([]) is True

    async def test_persist_failure_returns_false(self) -> None:
        c = make_mock_connector()
        c.data_entities_processor.on_new_records = AsyncMock(side_effect=RuntimeError("db"))
        ru = await IssuesSync(c)._process_issue_to_ticket(make_repo(repo_id=1), _issue(number=1))
        assert await IssuesSync(c).process_new_records([ru]) is False

    async def test_watermarks_accumulate_per_group(self) -> None:
        c = make_mock_connector()
        ru = await IssuesSync(c)._process_issue_to_ticket(make_repo(repo_id=1), _issue(number=1))
        marks: dict[str, int] = {}
        assert await IssuesSync(c).process_new_records([ru], marks) is True
        assert "1-work-items" in marks

    async def test_checkpoint_read_failure_returns_none(self) -> None:
        c = make_mock_connector()
        c.record_sync_point.read_sync_point = AsyncMock(side_effect=RuntimeError("missing"))
        assert await IssuesSync(c)._get_sync_checkpoint("1-work-items") is None

    def test_malformed_group_id_parse_returns_none(self) -> None:
        record = TicketRecord(
            id="r1", org_id="org-1", record_name="x", record_type="TICKET",
            version=0, origin="CONNECTOR", connector_name="GITHUB TEAMS", connector_id="c-1",
            external_record_id="not-int", external_record_group_id="abc-work-items",
        )
        assert IssuesSync(make_mock_connector()).parse_repo_id_and_number_from_record(record) is None


class TestBuildTicketBlocks:
    async def test_builds_description_and_comments(self) -> None:
        c = make_mock_connector()
        repo = make_repo(repo_id=42)
        issue = _issue(number=7, title="Crash")
        c.runtime.ds_call.side_effect = _dispatch(c, {
            "get_repo_by_id": ok_response(repo),
            "get_issue": ok_response(issue),
        })
        c.comments.embed_images_as_base64 = AsyncMock(return_value="body")
        c.comments.make_child_records_of_attachments = AsyncMock(return_value=([], []))
        c.comments.build_issue_comment_blocks = AsyncMock(return_value=([], []))
        record = TicketRecord(
            id="r1", org_id="org-1", record_name="Crash", record_type="TICKET",
            version=0, origin="CONNECTOR", connector_name="GITHUB TEAMS", connector_id="c-1",
            external_record_id="42/issues/7", external_record_group_id="42-work-items",
            weburl="https://github.com/acme/widgets/issues/7",
        )

        payload = await IssuesSync(c).build_ticket_blocks(record)

        assert b"Crash" in payload
        c.comments.build_issue_comment_blocks.assert_awaited_once()

    async def test_repo_resolve_failure_raises(self) -> None:
        c = make_mock_connector()
        c.runtime.ds_call.side_effect = _dispatch(c, {"get_repo_by_id": failed_response("404")})
        record = TicketRecord(
            id="r1", org_id="org-1", record_name="x", record_type="TICKET",
            version=0, origin="CONNECTOR", connector_name="GITHUB TEAMS", connector_id="c-1",
            external_record_id="42/issues/7", external_record_group_id="42-work-items",
        )
        with pytest.raises(Exception, match="Failed to resolve repo"):
            await IssuesSync(c).build_ticket_blocks(record)

    async def test_issue_fetch_failure_raises(self) -> None:
        c = make_mock_connector()
        c.runtime.ds_call.side_effect = _dispatch(c, {
            "get_repo_by_id": ok_response(make_repo(repo_id=42)),
            "get_issue": failed_response("404"),
        })
        record = TicketRecord(
            id="r1", org_id="org-1", record_name="x", record_type="TICKET",
            version=0, origin="CONNECTOR", connector_name="GITHUB TEAMS", connector_id="c-1",
            external_record_id="42/issues/7", external_record_group_id="42-work-items",
        )
        with pytest.raises(Exception, match="Failed to fetch issue"):
            await IssuesSync(c).build_ticket_blocks(record)

    async def test_missing_group_id_raises(self) -> None:
        record = TicketRecord(
            id="r1", org_id="org-1", record_name="x", record_type="TICKET",
            version=0, origin="CONNECTOR", connector_name="GITHUB TEAMS", connector_id="c-1",
            external_record_id="42/issues/7",
        )
        with pytest.raises(Exception, match="Repository id not found"):
            await IssuesSync(make_mock_connector()).build_ticket_blocks(record)


class TestAppUserEmails:
    async def test_directory_failure_returns_empty_map(self) -> None:
        c = make_mock_connector()
        c.data_entities_processor.get_all_app_users = AsyncMock(side_effect=RuntimeError("db"))
        emails = await IssuesSync(c).get_app_user_emails()
        assert emails == {}


def _dispatch(c: object, mapping: dict[str, object]) -> object:
    by_identity = {getattr(c.data_source, name): response for name, response in mapping.items()}

    def _fn(method: object, *args: object, **kwargs: object) -> object:
        if method in by_identity:
            return by_identity[method]
        raise AssertionError(f"unmocked ds_call for {method!r}")

    return _fn
