"""Slack event catalog — AppEventDescriptors for Slack app events."""
from __future__ import annotations

from app.services.events.catalog.registry import AppEventDescriptor, EventFilterSpec, register_descriptor

_SLACK_DESCRIPTORS = [
    AppEventDescriptor(
        event_type="slack.message.posted",
        provider_event="message.channels",
        source_app="slack",
        payload_schema={
            "type": "object",
            "properties": {
                "channel": {"type": "object", "properties": {"id": {"type": "string"}, "name": {"type": "string"}}},
                "user": {"type": "object", "properties": {"id": {"type": "string"}, "name": {"type": "string"}}},
                "text": {"type": "string"},
                "ts": {"type": "string"},
                "thread_ts": {"type": ["string", "null"]},
            }
        },
        filterable_fields=[
            EventFilterSpec(
                field="channel.id",
                filter_type="STRING",
                operators=["eq", "in", "not_in"],
                description="Slack channel ID (e.g. C0123). Use slack/list_channels to resolve names.",
                options_tool="slack/list_channels",
            ),
            EventFilterSpec(
                field="user.id",
                filter_type="STRING",
                operators=["eq", "neq", "in", "not_in"],
                description="Slack user ID. Use slack/list_users to resolve.",
                options_tool="slack/list_users",
            ),
            EventFilterSpec(
                field="text",
                filter_type="STRING",
                operators=["contains", "prefix"],
                description="Message text (case-insensitive substring or prefix match).",
                options_tool=None,
            ),
        ],
        example_payload={
            "channel": {"id": "C0123ABC", "name": "eng-alerts"},
            "user": {"id": "U0XYZDEF", "name": "alice"},
            "text": "URGENT: Production is down",
            "ts": "1700000000.000001",
            "thread_ts": None,
        },
        description="A message was posted to a Slack channel.",
    ),
    AppEventDescriptor(
        event_type="slack.reaction.added",
        provider_event="reaction_added",
        source_app="slack",
        payload_schema={
            "type": "object",
            "properties": {
                "reaction": {"type": "string"},
                "user": {"type": "object"},
                "item_channel": {"type": "string"},
                "item_ts": {"type": "string"},
            }
        },
        filterable_fields=[
            EventFilterSpec(
                field="reaction",
                filter_type="STRING",
                operators=["eq", "in"],
                description="Emoji name without colons (e.g. 'thumbsup').",
                options_tool=None,
            ),
            EventFilterSpec(
                field="item_channel",
                filter_type="STRING",
                operators=["eq", "in", "not_in"],
                description="Channel where reaction was added.",
                options_tool="slack/list_channels",
            ),
        ],
        example_payload={
            "reaction": "thumbsup",
            "user": {"id": "U0XYZDEF", "name": "alice"},
            "item_channel": "C0123ABC",
            "item_ts": "1700000000.000001",
        },
        description="A user added a reaction emoji to a Slack message.",
    ),
    AppEventDescriptor(
        event_type="slack.channel.created",
        provider_event="channel_created",
        source_app="slack",
        payload_schema={
            "type": "object",
            "properties": {
                "channel": {"type": "object"},
                "creator": {"type": "object"},
                "created": {"type": "integer"},
            }
        },
        filterable_fields=[],
        example_payload={
            "channel": {"id": "C0NEWCHAN", "name": "new-channel"},
            "creator": {"id": "U0XYZDEF", "name": "alice"},
            "created": 1700000000,
        },
        description="A new Slack channel was created.",
    ),
]

# Auto-register on import
for _descriptor in _SLACK_DESCRIPTORS:
    register_descriptor(_descriptor)
