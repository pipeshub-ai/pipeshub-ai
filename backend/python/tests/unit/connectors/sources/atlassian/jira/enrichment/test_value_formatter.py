"""Tests for Jira enrichment value formatter and merge logic."""

from app.connectors.sources.atlassian.jira.enrichment.value_formatter import (
    _format_custom_value,
    _format_environment,
    _format_issuelinks,
    _format_parent,
    _format_person,
    _format_priority,
    _format_progress,
    _format_project,
    _format_resolution,
    _format_seconds,
    _format_security,
    _format_sprint_value,
    _format_status,
    _format_status_category,
    _format_string_list,
    _format_subtasks,
    _format_system_field,
    _format_timetracking,
    _format_versions,
    _format_watches,
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

    def test_sprint_dict_with_numeric_dates(self):
        from app.connectors.sources.atlassian.jira.enrichment.value_formatter import (
            _format_sprint_value,
        )

        result = _format_sprint_value(
            {"name": "Sprint 1", "state": "active", "startDate": 1710000000000, "endDate": 1712000000000},
        )
        assert result == "Sprint 1 active 1710000000000 1712000000000"

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


class TestFormatHelpers:
    def test_format_person_variants(self):
        assert _format_person(None) is None
        assert _format_person({"displayName": "Alice", "emailAddress": "a@example.com"}) == "Alice (a@example.com)"
        assert _format_person({"displayName": "Bob"}) == "Bob"
        assert _format_person({"emailAddress": "c@example.com"}) == "c@example.com"
        assert _format_person({"name": "Legacy"}) == "Legacy"

    def test_format_status_priority_resolution_scalars(self):
        assert _format_status({"name": "Open"}) == "Open"
        assert _format_status("Open") == "Open"
        assert _format_priority({"name": "High"}) == "High"
        assert _format_priority("High") == "High"
        assert _format_resolution({"name": "Done"}) == "Done"
        assert _format_resolution(None) is None

    def test_format_issuetype(self):
        from app.connectors.sources.atlassian.jira.enrichment.value_formatter import _format_issuetype

        assert _format_issuetype({"name": "Bug"}) == "Bug"
        assert _format_issuetype("Story") == "Story"

    def test_format_project_and_parent(self):
        assert _format_project({"key": "PROJ", "name": "Project"}) == "PROJ — Project"
        assert _format_project({"key": "PROJ"}) == "PROJ"
        assert _format_project("PROJ") == "PROJ"
        assert _format_parent({
            "key": "PROJ-1",
            "fields": {"summary": "Parent issue"},
        }) == "PROJ-1 — Parent issue"
        assert _format_parent({"key": "PROJ-1"}) == "PROJ-1"
        assert _format_parent("PROJ-1") == "PROJ-1"

    def test_format_string_list_and_versions(self):
        assert _format_string_list(None) is None
        assert _format_string_list([{"name": "A"}, {"value": "B"}, "C"]) == "A, B, C"
        assert _format_string_list([]) is None
        assert _format_string_list("single") == "single"
        assert _format_versions([{"name": "v1"}, {"name": "v2"}]) == "v1, v2"
        assert _format_versions([]) is None

    def test_format_security(self):
        assert _format_security({"name": "Restricted"}) == "Restricted"
        assert _format_security("Public") == "Public"

    def test_format_environment(self):
        assert _format_environment(None) is None
        assert _format_environment({
            "content": [{"content": [{"text": "Staging"}, {"text": "EU"}]}],
        }) == "Staging EU"
        assert _format_environment({"content": []}) == "{'content': []}"
        assert _format_environment("  prod  ") == "prod"
        assert _format_environment("   ") is None

    def test_format_issuelinks(self):
        links = [{
            "type": {"outward": "blocks"},
            "outwardIssue": {"key": "PROJ-2"},
        }, {
            "inwardIssue": {"key": "PROJ-3"},
        }, "bad"]
        assert _format_issuelinks(links) == "blocks PROJ-2, PROJ-3"
        assert _format_issuelinks([{
            "type": {"name": "Relates"},
            "inwardIssue": {"key": "PROJ-4"},
        }]) == "Relates PROJ-4"
        assert _format_issuelinks(None) is None

    def test_format_subtasks(self):
        assert _format_subtasks([{"key": "PROJ-10"}, {"key": "PROJ-11"}]) == "PROJ-10, PROJ-11"
        assert _format_subtasks([{}]) is None
        assert _format_subtasks(None) is None

    def test_format_timetracking(self):
        assert _format_timetracking({
            "originalEstimate": "2h",
            "remainingEstimate": "1h",
            "timeSpent": "30m",
        }) == "original: 2h; remaining: 1h; spent: 30m"
        assert _format_timetracking("bad") is None

    def test_format_seconds(self):
        assert _format_seconds(None) is None
        assert _format_seconds("bad") == "bad"
        assert _format_seconds(0) == "0m"
        assert _format_seconds(5400) == "1h 30m"
        assert _format_seconds(3600) == "1h"
        assert _format_seconds(120) == "2m"

    def test_format_progress_and_watches(self):
        assert _format_progress({"progress": 25, "total": 100}) == "25%"
        assert _format_progress({"progress": 10, "total": 0}) is None
        assert _format_progress("bad") is None
        assert _format_watches({"watchCount": 3}) == "3"
        assert _format_watches({}) is None
        assert _format_watches(None) is None

    def test_format_status_category(self):
        assert _format_status_category({"name": "In Progress"}) == "In Progress"
        assert _format_status_category("done") == "done"

    def test_format_sprint_value_variants(self):
        assert _format_sprint_value(None) is None
        assert _format_sprint_value([
            {"name": "Sprint 1", "state": "active"},
            "com.atlassian.greenhopper.service.sprint:[id=2,name=Sprint 2,state=CLOSED]",
        ]) == "Sprint 1 active; Sprint 2"
        assert _format_sprint_value(
            "com.atlassian.greenhopper.service.sprint:[id=1,name=Sprint 42,state=ACTIVE]",
        ) == "Sprint 42"
        assert _format_sprint_value("com.atlassian.greenhopper.service.sprint:[...,Readable Tail]") == "Readable Tail"
        assert _format_sprint_value("plain sprint text") == "plain sprint text"

    def test_format_custom_value_variants(self):
        assert _format_custom_value(None) is None
        assert _format_custom_value({"value": "Critical"}) == "Critical"
        assert _format_custom_value({"displayName": "Alice", "emailAddress": "a@example.com"}) == "Alice (a@example.com)"
        assert _format_custom_value({"name": "Option A"}) == "Option A"
        assert _format_custom_value({"key": "EPIC-1"}) == "EPIC-1"
        assert _format_custom_value({"unknown": "shape"}) == "{'unknown': 'shape'}"
        assert _format_custom_value([{"value": "A"}, {"value": "B"}]) == "A, B"
        assert _format_custom_value(42) == "42"

    def test_format_system_field_unknown_key_and_assignee(self):
        assert _format_system_field("unknown", "  value  ") == "value"
        assert _format_system_field("assignee", {"displayName": "Alice"}) == "Alice"
        assert _format_system_field("workratio", 75) == "75"
        assert _format_system_field("created", "2026-01-01") == "2026-01-01"


class TestMergeAndFormatLiveLines:
    def test_merge_appends_ticket_section_when_missing(self):
        base = "Record: PROJ-1\nSome other section."
        merged = merge_enrichment_context(base, {"Due Date": "* Due Date: 2026-06-15"})
        assert "Ticket Information:" in merged
        assert "* Due Date: 2026-06-15" in merged

    def test_merge_appends_ticket_section_with_blank_line_before_header(self):
        base = "Record: PROJ-1"
        merged = merge_enrichment_context(base, {"Due Date": "* Due Date: 2026-06-15"})
        assert merged.endswith("Ticket Information:\n* Due Date: 2026-06-15")

    def test_merge_skips_existing_labels(self):
        base = "Record: PROJ-1\nTicket Information:\n* Labels: graph\n"
        live = {"Labels": "* Labels: live", "Due Date": "* Due Date: 2026-06-15"}
        merged = merge_enrichment_context(base, live)
        assert "* Labels: graph" in merged
        assert "* Labels: live" not in merged
        assert "* Due Date: 2026-06-15" in merged

    def test_format_live_lines_skips_unknown_registry_keys(self):
        issue = {"id": "1", "fields": {"customfield_999": "value"}}
        lines = format_live_lines(issue, {"unknown_key": "customfield_999"})
        assert lines == {}

    def test_format_live_lines_sprint_prefers_sprint_formatter(self):
        issue = {
            "id": "1",
            "fields": {
                "customfield_10110": [
                    {"name": "Sprint A", "state": "active"},
                ],
            },
        }
        lines = format_live_lines(issue, {"sprint": "customfield_10110"})
        assert "Sprint A active" in lines["Sprint"]

    def test_format_live_lines_full_system_fields(self):
        issue = {
            "id": "10324",
            "fields": {
                "resolution": {"name": "Fixed"},
                "labels": ["backend"],
                "components": [{"name": "API"}],
                "fixVersions": [{"name": "1.0"}],
                "versions": [{"name": "0.9"}],
                "environment": "Production",
                "security": {"name": "Internal"},
                "project": {"key": "PROJ", "name": "Project"},
                "parent": {"key": "PROJ-0", "fields": {"summary": "Epic"}},
                "issuelinks": [{"outwardIssue": {"key": "PROJ-2"}}],
                "subtasks": [{"key": "PROJ-10"}],
                "created": "2026-01-01T00:00:00.000Z",
                "timetracking": {"timeSpent": "1h"},
                "aggregatetimeoriginalestimate": 7200,
                "aggregatetimeestimate": 3600,
                "aggregatetimespent": 1800,
                "watches": {"watchCount": 2},
            },
        }
        lines = format_live_lines(issue, {})
        assert "Resolution" in lines
        assert "Labels" in lines
        assert "Components" in lines
        assert "Fix Versions" in lines
        assert "Affects Versions" in lines
        assert "Environment" in lines
        assert "Security" in lines
        assert "Project" in lines
        assert "Parent" in lines
        assert "Linked Issues" in lines
        assert "Sub-tasks" in lines
        assert "Created" in lines
        assert "Time Tracking" in lines
        assert "Aggregate Original Estimate" in lines
        assert "Aggregate Remaining Estimate" in lines
        assert "Aggregate Time Spent" in lines
        assert "Watches" in lines
