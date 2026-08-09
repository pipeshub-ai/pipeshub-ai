"""Jira event catalog — AppEventDescriptors for Jira app events."""
from __future__ import annotations

from app.services.events.catalog.registry import AppEventDescriptor, EventFilterSpec, register_descriptor

_JIRA_DESCRIPTORS = [
    AppEventDescriptor(
        event_type="jira.issue.created",
        provider_event="jira:issue_created",
        source_app="jira",
        payload_schema={
            "type": "object",
            "properties": {
                "issue": {
                    "type": "object",
                    "properties": {
                        "key": {"type": "string"},
                        "id": {"type": "string"},
                        "summary": {"type": "string"},
                        "status": {"type": "string"},
                        "priority": {"type": ["string", "null"]},
                        "assignee": {"type": ["string", "null"]},
                        "type": {"type": "string"},
                    }
                },
                "project_key": {"type": "string"},
            }
        },
        filterable_fields=[
            EventFilterSpec(
                field="project_key",
                filter_type="STRING",
                operators=["eq", "in", "not_in"],
                description="Jira project key (e.g. MYPROJ).",
                options_tool="jira/list_projects",
            ),
            EventFilterSpec(
                field="issue.type",
                filter_type="STRING",
                operators=["eq", "in", "not_in"],
                description="Issue type (Bug, Task, Story, Epic, etc.).",
                options_tool=None,
            ),
            EventFilterSpec(
                field="issue.priority",
                filter_type="STRING",
                operators=["eq", "in", "not_in"],
                description="Issue priority (Critical, High, Medium, Low).",
                options_tool=None,
            ),
            EventFilterSpec(
                field="issue.assignee",
                filter_type="STRING",
                operators=["eq", "neq", "exists"],
                description="Assignee user name. exists checks if assigned at all.",
                options_tool=None,
            ),
        ],
        example_payload={
            "issue": {
                "key": "MYPROJ-42",
                "id": "10042",
                "summary": "Production database is returning errors",
                "status": "Open",
                "priority": "Critical",
                "assignee": "alice",
                "type": "Bug",
            },
            "project_key": "MYPROJ",
        },
        description="A new Jira issue was created.",
    ),
    AppEventDescriptor(
        event_type="jira.issue.updated",
        provider_event="jira:issue_updated",
        source_app="jira",
        payload_schema={
            "type": "object",
            "properties": {
                "issue": {"type": "object"},
                "project_key": {"type": "string"},
                "changelog": {"type": "object"},
            }
        },
        filterable_fields=[
            EventFilterSpec(
                field="project_key",
                filter_type="STRING",
                operators=["eq", "in", "not_in"],
                description="Jira project key.",
                options_tool="jira/list_projects",
            ),
            EventFilterSpec(
                field="issue.status",
                filter_type="STRING",
                operators=["eq", "in", "not_in"],
                description="New status after update.",
                options_tool=None,
            ),
        ],
        example_payload={
            "issue": {"key": "MYPROJ-42", "status": "In Progress"},
            "project_key": "MYPROJ",
            "changelog": {"status": {"from": "Open", "to": "In Progress"}},
        },
        description="A Jira issue was updated.",
    ),
    AppEventDescriptor(
        event_type="jira.issue.commented",
        provider_event="jira:issue_commented",
        source_app="jira",
        payload_schema={
            "type": "object",
            "properties": {
                "issue": {"type": "object"},
                "project_key": {"type": "string"},
                "comment": {"type": "string"},
                "commenter": {"type": "string"},
            }
        },
        filterable_fields=[
            EventFilterSpec(
                field="project_key",
                filter_type="STRING",
                operators=["eq", "in", "not_in"],
                description="Jira project key.",
                options_tool="jira/list_projects",
            ),
        ],
        example_payload={
            "issue": {"key": "MYPROJ-42"},
            "project_key": "MYPROJ",
            "comment": "Fixed in PR #123",
            "commenter": "bob",
        },
        description="A comment was added to a Jira issue.",
    ),
]

for _descriptor in _JIRA_DESCRIPTORS:
    register_descriptor(_descriptor)
