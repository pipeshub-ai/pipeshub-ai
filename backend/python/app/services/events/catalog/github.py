"""GitHub event catalog."""
from __future__ import annotations

from app.services.events.catalog.registry import AppEventDescriptor, EventFilterSpec, register_descriptor

_GITHUB_DESCRIPTORS = [
    AppEventDescriptor(
        event_type="github.push",
        provider_event="push",
        source_app="github",
        payload_schema={
            "type": "object",
            "properties": {
                "repository": {"type": "object", "properties": {"full_name": {"type": "string"}}},
                "ref": {"type": "string"},
                "pusher": {"type": "object"},
                "commits": {"type": "array"},
            }
        },
        filterable_fields=[
            EventFilterSpec(
                field="repository.full_name",
                filter_type="STRING",
                operators=["eq", "in", "prefix"],
                description="Repository full name (org/repo).",
                options_tool=None,
            ),
            EventFilterSpec(
                field="ref",
                filter_type="STRING",
                operators=["eq", "prefix", "contains"],
                description="Git ref (e.g. 'refs/heads/main').",
                options_tool=None,
            ),
        ],
        example_payload={
            "repository": {"full_name": "myorg/myrepo"},
            "ref": "refs/heads/main",
            "pusher": {"name": "alice"},
            "commits": [{"id": "abc123", "message": "Fix bug"}],
        },
        description="Code was pushed to a GitHub repository.",
    ),
    AppEventDescriptor(
        event_type="github.pull_request.opened",
        provider_event="pull_request.opened",
        source_app="github",
        payload_schema={
            "type": "object",
            "properties": {
                "repository": {"type": "object"},
                "pull_request": {"type": "object"},
                "action": {"type": "string"},
            }
        },
        filterable_fields=[
            EventFilterSpec(
                field="repository.full_name",
                filter_type="STRING",
                operators=["eq", "in"],
                description="Repository full name.",
                options_tool=None,
            ),
            EventFilterSpec(
                field="pull_request.base.ref",
                filter_type="STRING",
                operators=["eq", "in"],
                description="Target branch (e.g. 'main').",
                options_tool=None,
            ),
        ],
        example_payload={
            "repository": {"full_name": "myorg/myrepo"},
            "pull_request": {"title": "Add feature X", "base": {"ref": "main"}},
            "action": "opened",
        },
        description="A GitHub pull request was opened.",
    ),
]

for _descriptor in _GITHUB_DESCRIPTORS:
    register_descriptor(_descriptor)
