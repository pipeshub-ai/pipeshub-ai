"""Tests for Jira enrichment value formatter and merge logic."""

from app.connectors.sources.atlassian.jira.enrichment.value_formatter import (
    enrich_from_issue,
    format_live_lines,
    merge_enrichment_context,
)


def _sample_issue(**field_overrides):
    fields = {
        "status": {"name": "In Progress"},
        "priority": {"name": "High"},
        "issuetype": {"name": "Bug"},
        "labels": ["backend", "urgent"],
        "duedate": "2026-06-15",
    }
    fields.update(field_overrides)
    return {"id": "10324", "fields": fields}


class TestValueFormatter:
    def test_graph_status_preserved_live_only_appended(self):
        base = (
            "Record: PROJ-1\n"
            "Ticket Information:\n"
            "* Status: Open\n"
            "* Priority: Medium\n"
        )
        issue = _sample_issue()
        merged = enrich_from_issue(base, issue, {})
        assert "* Status: Open" in merged
        assert "* Priority: Medium" in merged
        assert "* Status: In Progress" not in merged
        assert "* Priority: High" not in merged

    def test_live_only_labels_appended_when_missing_from_graph(self):
        base = (
            "Record: PROJ-1\n"
            "Ticket Information:\n"
            "* Status: Open\n"
        )
        issue = _sample_issue()
        merged = enrich_from_issue(base, issue, {})
        assert "* Labels: backend, urgent" in merged
        assert "* Due Date: 2026-06-15" in merged

    def test_tier2_style_fields_appended(self):
        base = "Record: PROJ-1\nTicket Information:\n* Status: Open\n"
        live_lines = {
            "Due Date": "* Due Date: 2026-06-15",
            "Status Category": "* Status Category: In Progress",
        }
        merged = merge_enrichment_context(base, live_lines)
        assert "* Due Date:" in merged
        assert "Status Category" in merged

    def test_sprint_custom_field_formatted(self):
        issue = {
            "id": "10324",
            "fields": {
                "customfield_10110": "com.atlassian.greenhopper.service.sprint:[id=1,name=Sprint 42,state=ACTIVE]",
            },
        }
        lines = format_live_lines(issue, {"sprint": "customfield_10110"})
        assert "Sprint 42" in lines["Sprint"]

    def test_resolution_system_field_formatted(self):
        issue = _sample_issue(resolution={"name": "Fixed"})
        lines = format_live_lines(issue, {})
        assert lines["Resolution"] == "* Resolution: Fixed"

    def test_severity_custom_field_formatted(self):
        issue = {
            "id": "10324",
            "fields": {"customfield_10001": {"value": "Critical"}},
        }
        lines = format_live_lines(issue, {"severity": "customfield_10001"})
        assert lines["Severity"] == "* Severity: Critical"

    def test_approvers_custom_field_formatted(self):
        issue = {
            "id": "10324",
            "fields": {
                "customfield_10002": [
                    {"displayName": "Alice Smith", "emailAddress": "alice@example.com"},
                    {"displayName": "Bob Jones"},
                ],
            },
        }
        lines = format_live_lines(issue, {"approvers": "customfield_10002"})
        assert lines["Approvers"] == "* Approvers: Alice Smith (alice@example.com), Bob Jones"
