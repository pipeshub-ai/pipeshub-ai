"""Per-connector `@tool(args_summary=..., result_summary=...)` declarations
(App Tool Summarizers plan, Tier 2). Each class targets the formatters
declared directly on a representative pair of tools (one list-returning,
one single-entity) for its connector via `_agent_tool_meta`, exactly what
`tool_loop.py`'s `Tool.summarize_args`/`summarize_result` calls at
runtime — happy path, error envelope, and (where applicable) empty list.
"""

from __future__ import annotations

from app.agent_loop_lib.core.types import ToolResult
from app.agents.actions.clickup.clickup import ClickUp
from app.agents.actions.confluence.confluence import Confluence
from app.agents.actions.github.github import GitHub
from app.agents.actions.google.calendar.calendar import GoogleCalendar
from app.agents.actions.google.drive.drive import GoogleDrive
from app.agents.actions.google.gmail.gmail import Gmail
from app.agents.actions.jira.jira import Jira
from app.agents.actions.microsoft.outlook.outlook import Outlook
from app.agents.actions.microsoft.sharepoint.sharepoint import SharePoint
from app.agents.actions.microsoft.teams.teams import Teams
from app.agents.actions.slack.slack import Slack


def _result(content: object, *, is_error: bool = False) -> ToolResult:
    return ToolResult(tool_call_id="call-1", name="tool", content=content, is_error=is_error)


class TestJiraSummaries:
    def test_search_issues_args(self) -> None:
        meta = Jira.search_issues._agent_tool_meta
        assert meta.args_summary({"jql": "project = PA"}) == 'Searching Jira issues: "project = PA"'

    def test_search_issues_result_happy_path(self) -> None:
        meta = Jira.search_issues._agent_tool_meta
        content = {"data": {"issues": [{"key": "PA-1", "fields": {"summary": "Fix bug"}}]}}
        assert meta.result_summary({}, _result(content)) == "Found 1 issue\n- PA-1: Fix bug"

    def test_search_issues_result_empty(self) -> None:
        meta = Jira.search_issues._agent_tool_meta
        content = {"data": {"issues": []}}
        assert meta.result_summary({}, _result(content)) == "No issues found"

    def test_search_issues_result_error(self) -> None:
        meta = Jira.search_issues._agent_tool_meta
        content = {"error": "invalid JQL"}
        summary = meta.result_summary({}, _result(content, is_error=True))
        assert summary == "Failed: invalid JQL"

    def test_create_issue_args_and_result(self) -> None:
        meta = Jira.create_issue._agent_tool_meta
        assert (
            meta.args_summary({"project_key": "PA", "summary": "Fix bug"})
            == 'Creating Jira issue in PA: "Fix bug"'
        )
        content = {"data": {"key": "PA-42"}}
        assert meta.result_summary({}, _result(content)) == "Created PA-42"

    def test_create_issue_result_error(self) -> None:
        meta = Jira.create_issue._agent_tool_meta
        summary = meta.result_summary({}, _result({"error": "missing field"}, is_error=True))
        assert summary == "Failed: missing field"


class TestConfluenceSummaries:
    def test_search_pages_args_and_result(self) -> None:
        meta = Confluence.search_pages._agent_tool_meta
        assert meta.args_summary({"title": "Runbook"}) == 'Searching Confluence pages: "Runbook"'
        content = {"data": {"results": [{"title": "Runbook A"}, {"title": "Runbook B"}]}}
        assert meta.result_summary({}, _result(content)) == "Found 2 pages\n- Runbook A\n- Runbook B"

    def test_search_pages_result_empty(self) -> None:
        meta = Confluence.search_pages._agent_tool_meta
        content = {"data": {"results": []}}
        assert meta.result_summary({}, _result(content)) == "No pages found"

    def test_search_pages_result_error(self) -> None:
        meta = Confluence.search_pages._agent_tool_meta
        summary = meta.result_summary({}, _result({"error": "not found"}, is_error=True))
        assert summary == "Failed: not found"

    def test_create_page_args_and_result(self) -> None:
        meta = Confluence.create_page._agent_tool_meta
        assert meta.args_summary({"page_title": "New Doc"}) == 'Creating Confluence page "New Doc"'
        content = {"data": {"title": "New Doc"}}
        assert meta.result_summary({}, _result(content)) == "Created page: New Doc"


class TestGitHubSummaries:
    def test_list_issues_args_and_result(self) -> None:
        meta = GitHub.list_issues._agent_tool_meta
        assert meta.args_summary({"owner": "acme", "repo": "widgets"}) == "Listing issues in acme/widgets"
        content = {"data": [{"number": 1, "title": "Bug"}, {"number": 2, "title": "Feature"}]}
        assert meta.result_summary({}, _result(content)) == "Found 2 issues\n- #1: Bug\n- #2: Feature"

    def test_list_issues_result_empty(self) -> None:
        meta = GitHub.list_issues._agent_tool_meta
        assert meta.result_summary({}, _result({"data": []})) == "No issues found"

    def test_list_issues_result_error(self) -> None:
        meta = GitHub.list_issues._agent_tool_meta
        summary = meta.result_summary({}, _result({"error": "repo not found"}, is_error=True))
        assert summary == "Failed: repo not found"

    def test_create_pull_request_args_and_result(self) -> None:
        meta = GitHub.create_pull_request._agent_tool_meta
        assert meta.args_summary({"owner": "acme", "repo": "widgets"}) == "Creating pull request in acme/widgets"
        content = {"data": {"number": 7, "title": "Add feature"}}
        assert meta.result_summary({}, _result(content)) == "Created PR: #7: Add feature"

    def test_get_pull_request_uses_nested_pr_path(self) -> None:
        meta = GitHub.get_pull_request._agent_tool_meta
        content = {"data": {"pr": {"number": 7, "title": "Add feature"}}}
        assert meta.result_summary({}, _result(content)) == "Fetched PR: #7: Add feature"


class TestSlackSummaries:
    def test_send_message_args_and_confirmation(self) -> None:
        meta = Slack.send_message._agent_tool_meta
        assert meta.args_summary({"channel": "#general"}) == "Sending Slack message to #general"
        assert meta.result_summary({"channel": "#general"}, _result('{"ok": true}')) == "Message sent to #general"

    def test_send_message_result_error(self) -> None:
        meta = Slack.send_message._agent_tool_meta
        summary = meta.result_summary({"channel": "#general"}, _result('{"error": "not_in_channel"}', is_error=True))
        assert summary == "Failed: not_in_channel"

    def test_get_channel_history_args_and_result(self) -> None:
        meta = Slack.get_channel_history._agent_tool_meta
        assert meta.args_summary({"channel": "#general"}) == "Fetching history for Slack channel #general"
        content = {"data": {"messages": [{"user": "alice", "text": "hi there"}]}}
        assert meta.result_summary({}, _result(content)) == "Found 1 message\n- alice: hi there"

    def test_get_channel_history_result_empty(self) -> None:
        meta = Slack.get_channel_history._agent_tool_meta
        content = {"data": {"messages": []}}
        assert meta.result_summary({}, _result(content)) == "No messages found"


class TestGoogleSummaries:
    def test_drive_search_files_args_and_result(self) -> None:
        meta = GoogleDrive.search_files._agent_tool_meta
        assert meta.args_summary({"query": "budget"}) == 'Searching Google Drive: "budget"'
        content = {"files": [{"name": "Budget.xlsx"}]}
        assert meta.result_summary({}, _result(content)) == "Found 1 file\n- Budget.xlsx"

    def test_drive_search_files_result_empty(self) -> None:
        meta = GoogleDrive.search_files._agent_tool_meta
        assert meta.result_summary({}, _result({"files": []})) == "No files found"

    def test_gmail_search_emails_args_and_result(self) -> None:
        meta = Gmail.search_emails._agent_tool_meta
        assert meta.args_summary({"query": "invoice"}) == 'Searching Gmail: "invoice"'
        content = {"messages": [{"subject": "Q3 report"}]}
        assert meta.result_summary({}, _result(content)) == "Found 1 email\n- Q3 report"

    def test_gmail_send_email_args_and_confirmation(self) -> None:
        meta = Gmail.send_email._agent_tool_meta
        assert meta.args_summary({"mail_to": ["a@example.com", "b@example.com"]}) == "Sending email to a@example.com, b@example.com"
        assert meta.result_summary({}, _result('{"ok": true}')) == "Email sent"

    def test_gmail_send_email_result_error(self) -> None:
        meta = Gmail.send_email._agent_tool_meta
        summary = meta.result_summary({}, _result('{"error": "invalid recipient"}', is_error=True))
        assert summary == "Failed: invalid recipient"

    def test_calendar_get_events_args_and_result(self) -> None:
        meta = GoogleCalendar.get_calendar_events._agent_tool_meta
        assert meta.args_summary({}) == "Fetching calendar events"
        assert meta.args_summary({"query": "standup"}) == 'Searching calendar: "standup"'
        content = {"items": [{"summary": "Standup"}]}
        assert meta.result_summary({}, _result(content)) == "Found 1 event\n- Standup"

    def test_calendar_create_event_args_and_result(self) -> None:
        meta = GoogleCalendar.create_calendar_event._agent_tool_meta
        assert meta.args_summary({"event_title": "Kickoff"}) == 'Creating calendar event "Kickoff"'
        content = {"event_title": "Kickoff"}
        assert meta.result_summary({}, _result(content)) == "Event created: Kickoff"


class TestClickUpSummaries:
    def test_get_tasks_args_and_result(self) -> None:
        meta = ClickUp.get_tasks._agent_tool_meta
        assert meta.args_summary({"team_id": "123"}) == "Fetching ClickUp tasks for workspace 123"
        content = {"data": {"tasks": [{"id": "1", "name": "Fix bug"}]}}
        assert meta.result_summary({}, _result(content)) == "Found 1 task\n- Fix bug"

    def test_get_tasks_result_empty(self) -> None:
        meta = ClickUp.get_tasks._agent_tool_meta
        content = {"data": {"tasks": []}}
        assert meta.result_summary({}, _result(content)) == "No tasks found"

    def test_get_tasks_result_error(self) -> None:
        meta = ClickUp.get_tasks._agent_tool_meta
        summary = meta.result_summary({}, _result({"error": "team not found"}, is_error=True))
        assert summary == "Failed: team not found"

    def test_create_task_args_and_result(self) -> None:
        meta = ClickUp.create_task._agent_tool_meta
        assert meta.args_summary({"name": "New Task"}) == 'Creating ClickUp task "New Task"'
        content = {"data": {"id": "2", "name": "New Task"}}
        assert meta.result_summary({}, _result(content)) == "Created task: New Task"


class TestMicrosoftSummaries:
    def test_teams_get_teams_args_and_result(self) -> None:
        meta = Teams.get_teams._agent_tool_meta
        assert meta.args_summary({}) == "Fetching Microsoft Teams"
        content = {"data": {"results": [{"displayName": "Engineering"}]}}
        assert meta.result_summary({}, _result(content)) == "Found 1 team\n- Engineering"

    def test_teams_get_teams_result_empty(self) -> None:
        meta = Teams.get_teams._agent_tool_meta
        content = {"data": {"results": []}}
        assert meta.result_summary({}, _result(content)) == "No teams found"

    def test_teams_send_channel_message_args_and_confirmation(self) -> None:
        meta = Teams.send_channel_message._agent_tool_meta
        assert meta.args_summary({"channel_id": "c1"}) == "Sending Teams message to channel c1"
        assert (
            meta.result_summary({"channel_id": "c1"}, _result('{"message": "ok"}'))
            == "Message sent to channel c1"
        )

    def test_teams_create_event_args_and_result(self) -> None:
        meta = Teams.create_event._agent_tool_meta
        assert meta.args_summary({"subject": "Kickoff"}) == 'Creating Teams event "Kickoff"'
        content = {"message": "Event created successfully", "subject": "Kickoff"}
        assert meta.result_summary({}, _result(content)) == "Event created: Kickoff"

    def test_outlook_search_messages_args_and_result(self) -> None:
        meta = Outlook.search_messages._agent_tool_meta
        assert meta.args_summary({}) == "Fetching Outlook messages"
        assert meta.args_summary({"search": "invoice"}) == 'Searching Outlook: "invoice"'
        content = {"messages": [{"subject": "Q3 report"}]}
        assert meta.result_summary({}, _result(content)) == "Found 1 message\n- Q3 report"

    def test_outlook_search_messages_result_empty(self) -> None:
        meta = Outlook.search_messages._agent_tool_meta
        assert meta.result_summary({}, _result({"messages": []})) == "No messages found"

    def test_sharepoint_search_files_args_and_result(self) -> None:
        meta = SharePoint.search_files._agent_tool_meta
        assert meta.args_summary({"query": "budget"}) == 'Searching SharePoint: "budget"'
        content = {"files": [{"name": "Budget2026.xlsx"}]}
        assert meta.result_summary({}, _result(content)) == "Found 1 file\n- Budget2026.xlsx"

    def test_sharepoint_search_files_result_error(self) -> None:
        meta = SharePoint.search_files._agent_tool_meta
        summary = meta.result_summary({}, _result({"error": "access denied"}, is_error=True))
        assert summary == "Failed: access denied"
