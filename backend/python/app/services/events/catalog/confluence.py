"""Confluence event catalog."""
from __future__ import annotations

from app.services.events.catalog.registry import AppEventDescriptor, EventFilterSpec, register_descriptor

_CONFLUENCE_DESCRIPTORS = [
    AppEventDescriptor(
        event_type="confluence.page.created",
        provider_event="page_created",
        source_app="confluence",
        payload_schema={
            "type": "object",
            "properties": {
                "page": {"type": "object", "properties": {"id": {"type": "string"}, "title": {"type": "string"}}},
                "space": {"type": "object", "properties": {"key": {"type": "string"}, "name": {"type": "string"}}},
                "creator": {"type": "object"},
            }
        },
        filterable_fields=[
            EventFilterSpec(
                field="space.key",
                filter_type="STRING",
                operators=["eq", "in", "not_in"],
                description="Confluence space key (e.g. ENG).",
                options_tool="confluence/list_spaces",
            ),
        ],
        example_payload={
            "page": {"id": "12345", "title": "New Architecture Doc"},
            "space": {"key": "ENG", "name": "Engineering"},
            "creator": {"name": "alice"},
        },
        description="A new Confluence page was created.",
    ),
    AppEventDescriptor(
        event_type="confluence.page.updated",
        provider_event="page_updated",
        source_app="confluence",
        payload_schema={
            "type": "object",
            "properties": {
                "page": {"type": "object"},
                "space": {"type": "object"},
                "updater": {"type": "object"},
            }
        },
        filterable_fields=[
            EventFilterSpec(
                field="space.key",
                filter_type="STRING",
                operators=["eq", "in", "not_in"],
                description="Confluence space key.",
                options_tool="confluence/list_spaces",
            ),
        ],
        example_payload={
            "page": {"id": "12345", "title": "Architecture Doc"},
            "space": {"key": "ENG", "name": "Engineering"},
            "updater": {"name": "bob"},
        },
        description="A Confluence page was updated.",
    ),
]

for _descriptor in _CONFLUENCE_DESCRIPTORS:
    register_descriptor(_descriptor)
