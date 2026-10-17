"""Where a ticket or pull request stands, and since when, in a few words.

Asked "was it merged, and when?", the model had the right pull request in
hand but no merge date: the graph stored only "merged". The same held for a
ticket's resolution date. Those dates are now stored, shown in the record's
header, and summed up in one short phrase that search entries and linked
records carry.
"""

from collections.abc import Iterator

import jsonschema
import pytest

from app.config.constants.arangodb import CollectionNames, Connectors, OriginTypes
from app.models.entities import (
    Priority,
    ProjectRecord,
    PullRequestRecord,
    Record,
    RecordType,
    Status,
    TicketRecord,
)
from app.schema.arango.documents import pull_request_record_schema
from app.schema.node_schema_registry import get_node_schema
from app.utils.time_conversion import (
    format_day_for_llm,
    reset_llm_time_zone,
    set_llm_time_zone,
)

# 2026-09-25 23:30 UTC: still the 25th in UTC, already the 26th in Kolkata.
MERGED = 1790379000000
# 2026-06-10 00:00 UTC, a due date.
DUE = 1781049600000
RESOLVED = 1780876800000  # 2026-06-08 00:00 UTC


def _kwargs(**overrides: object) -> dict:
    return {
        "record_name": "Record",
        "external_record_id": "ext-1",
        "version": 1,
        "origin": OriginTypes.CONNECTOR,
        "connector_id": "conn-1",
        **overrides,
    }


def _ticket(**overrides: object) -> TicketRecord:
    return TicketRecord(**_kwargs(**{
        "record_type": RecordType.TICKET, "connector_name": Connectors.JIRA, **overrides,
    }))


def _pr(**overrides: object) -> PullRequestRecord:
    return PullRequestRecord(**_kwargs(**{
        "record_type": RecordType.PULL_REQUEST, "connector_name": Connectors.GITHUB, **overrides,
    }))


@pytest.fixture
def kolkata() -> Iterator[None]:
    token = set_llm_time_zone("Asia/Kolkata")
    yield
    reset_llm_time_zone(token)


class TestFormatDay:
    def test_a_moment_is_the_day_in_utc_by_default(self) -> None:
        assert format_day_for_llm(MERGED) == "2026-09-25"

    def test_a_moment_is_the_day_in_the_users_zone(self, kolkata) -> None:
        assert format_day_for_llm(MERGED) == "2026-09-26"

    def test_a_calendar_date_is_the_same_day_in_any_zone(self) -> None:
        token = set_llm_time_zone("America/Los_Angeles")
        try:
            assert format_day_for_llm(DUE) == "2026-06-09"
            assert format_day_for_llm(DUE, calendar_date=True) == "2026-06-10"
        finally:
            reset_llm_time_zone(token)


class TestPullRequestStatus:
    def test_merged_with_date_and_merger(self) -> None:
        pr = _pr(status=Status.DONE.value, merged_at=MERGED, closed_at=MERGED, merged_by="alice")
        assert pr.status_summary() == "Merged 2026-09-25 by alice"

    def test_closed_without_merging_carries_the_close_date(self) -> None:
        pr = _pr(status=Status.CANCELLED.value, closed_at=MERGED)
        assert pr.status_summary() == "Closed without merging 2026-09-25"

    def test_open(self) -> None:
        assert _pr(status=Status.OPEN.value).status_summary() == "Open"

    def test_stored_before_the_dates_shows_the_state_alone(self) -> None:
        assert _pr(status=Status.DONE.value).status_summary() == "Merged"

    def test_a_gitlab_state_word_is_kept(self) -> None:
        pr = _pr(connector_name=Connectors.GITLAB, status="merged", merged_at=MERGED)
        assert pr.status_summary() == "merged 2026-09-25"

    def test_no_state_no_summary(self) -> None:
        assert _pr().status_summary() is None

    def test_header_shows_the_merge_date(self) -> None:
        ctx = _pr(status=Status.DONE.value, merged_at=MERGED, closed_at=MERGED).to_llm_context()
        assert "* Status: Merged\n" in ctx
        assert "* Merged: 2026-09-25" in ctx
        assert "* Closed:" not in ctx

    def test_header_shows_the_close_date_of_an_unmerged_pr(self) -> None:
        ctx = _pr(status=Status.CANCELLED.value, closed_at=MERGED).to_llm_context()
        assert "* Closed: 2026-09-25" in ctx

    def test_dates_survive_the_graph_round_trip(self) -> None:
        pr = _pr(id="pr-1", org_id="org-1", status=Status.DONE.value, merged_at=MERGED, closed_at=MERGED)
        doc = pr.to_arango_record()
        assert doc["mergedAtTimestamp"] == MERGED
        assert doc["closedAtTimestamp"] == MERGED
        jsonschema.validate(instance=doc, schema=pull_request_record_schema["rule"])
        back = PullRequestRecord.from_arango_record(doc, _record_doc("pr-1", "PULL_REQUEST", "GITHUB"))
        assert back.merged_at == MERGED
        assert back.closed_at == MERGED


class TestTicketStatus:
    def test_done_with_resolution_date_assignee_and_priority(self) -> None:
        ticket = _ticket(
            status=Status.DONE, source_status="Done", resolved_at=RESOLVED,
            assignee="Bo", priority=Priority.HIGH,
        )
        assert ticket.status_summary() == "DONE, resolved 2026-06-08, assignee Bo, priority HIGH"

    def test_source_status_name_is_kept(self) -> None:
        ticket = _ticket(status=Status.NEW, source_status="Backlog")
        assert ticket.status_summary() == "NEW (Jira: Backlog)"

    def test_open_ticket_shows_its_due_date(self) -> None:
        ticket = _ticket(status=Status.IN_PROGRESS, due_date=DUE)
        assert ticket.status_summary() == "IN_PROGRESS, due 2026-06-10"

    def test_closed_ticket_says_closed(self) -> None:
        ticket = _ticket(connector_name=Connectors.GITHUB, status=Status.CLOSED, resolved_at=RESOLVED)
        assert ticket.status_summary() == "CLOSED, closed 2026-06-08"

    def test_unknown_priority_and_email_only_assignee(self) -> None:
        ticket = _ticket(status=Status.OPEN, priority=Priority.UNKNOWN, assignee_email="bo@example.com")
        assert ticket.status_summary() == "OPEN, assignee bo@example.com"

    def test_header_shows_resolution_and_due_dates(self) -> None:
        ctx = _ticket(status=Status.DONE, resolved_at=RESOLVED, due_date=DUE).to_llm_context()
        assert "* Status: DONE\n* Resolved: 2026-06-08\n* Due Date: 2026-06-10" in ctx

    def test_header_without_the_dates_is_unchanged(self) -> None:
        ctx = _ticket(status=Status.DONE).to_llm_context()
        assert "* Resolved:" not in ctx
        assert "* Due Date:" not in ctx

    def test_live_jira_fields_do_not_repeat_the_dates(self) -> None:
        """A fetch adds Jira's live fields under their own labels; the header
        uses the same ones, so a cancelled ticket keeps one line of each."""
        from app.connectors.sources.atlassian.jira.enrichment.value_formatter import (
            merge_enrichment_context,
        )

        ctx = _ticket(status=Status.CANCELLED, resolved_at=RESOLVED, due_date=DUE).to_llm_context()
        merged = merge_enrichment_context(ctx, {
            "Resolved": "* Resolved: 2026-06-07T23:00:00.000-0700",
            "Due Date": "* Due Date: 2026-06-10",
        })
        lines = merged.splitlines()
        assert len([line for line in lines if line.startswith(("* Resolved", "* Closed"))]) == 1
        assert len([line for line in lines if line.startswith("* Due")]) == 1
        assert "* Resolved: 2026-06-08" in merged

    def test_dates_survive_the_graph_round_trip(self) -> None:
        ticket = _ticket(id="t-1", org_id="org-1", status=Status.DONE, resolved_at=RESOLVED, due_date=DUE)
        doc = ticket.to_arango_record()
        assert doc["resolvedAtTimestamp"] == RESOLVED
        assert doc["dueDateTimestamp"] == DUE
        jsonschema.validate(instance=doc, schema=get_node_schema(CollectionNames.TICKETS.value))
        back = TicketRecord.from_arango_record(doc, _record_doc("t-1", "TICKET", "JIRA"))
        assert back.resolved_at == RESOLVED
        assert back.due_date == DUE


class TestOtherRecords:
    def test_project_summary_is_its_status(self) -> None:
        project = ProjectRecord(**_kwargs(
            record_type=RecordType.PROJECT, connector_name=Connectors.JIRA, status="In Progress",
        ))
        assert project.status_summary() == "In Progress"

    def test_a_page_has_no_status(self) -> None:
        page = Record(**_kwargs(record_type=RecordType.WEBPAGE, connector_name=Connectors.CONFLUENCE))
        assert page.status_summary() is None


def _record_doc(key: str, record_type: str, connector: str) -> dict:
    return {
        "_key": key, "orgId": "org-1", "recordName": "Record", "recordType": record_type,
        "externalRecordId": "ext-1", "version": 1, "origin": "CONNECTOR",
        "connectorName": connector, "connectorId": "conn-1",
        "createdAtTimestamp": 1, "updatedAtTimestamp": 1,
    }
