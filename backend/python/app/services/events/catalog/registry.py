"""EventCatalog registry — IEventCatalog implementation.

One AppEventDescriptor per (source_app, event_type) pair, registered
by catalog modules (catalog/slack.py, catalog/jira.py, etc.).

The registry is a process-level singleton populated at import time.
"""
from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel

__all__ = ["EventFilterSpec", "AppEventDescriptor", "EventCatalog", "get_catalog"]

logger = logging.getLogger(__name__)


class EventFilterSpec(BaseModel):
    field: str              # dot path into normalized payload: "channel.id"
    filter_type: str        # STRING, LIST, DATETIME, BOOLEAN, NUMBER, MULTISELECT
    operators: list[str]    # allowed operators for this field
    description: str
    options_tool: str | None = None  # e.g. "slack/list_channels"


class AppEventDescriptor(BaseModel):
    event_type: str                   # normalized: "slack.message.posted"
    provider_event: str               # raw provider name: "message.channels"
    source_app: str
    payload_schema: dict[str, Any]    # JSON Schema of the normalized payload
    filterable_fields: list[EventFilterSpec]
    example_payload: dict[str, Any]
    description: str


class ValidationError(BaseModel):
    code: str
    field: str
    fix_hint: str


class EventCatalog:
    """IEventCatalog implementation."""

    def __init__(self) -> None:
        self._descriptors: dict[str, AppEventDescriptor] = {}

    def register(self, descriptor: AppEventDescriptor) -> None:
        self._descriptors[descriptor.event_type] = descriptor

    def descriptors(self, source_app: str | None = None) -> list[AppEventDescriptor]:
        if source_app is None:
            return list(self._descriptors.values())
        return [d for d in self._descriptors.values() if d.source_app == source_app]

    def get(self, event_type: str) -> AppEventDescriptor | None:
        return self._descriptors.get(event_type)

    def source_apps(self) -> set[str]:
        """The apps this catalog describes.

        Lets a caller tell "typo in a Slack event type" from "an event type the
        catalog simply does not cover" -- the catalog is a description of the
        built-in providers, not an exhaustive registry of everything the
        platform can emit.
        """
        return {descriptor.source_app for descriptor in self._descriptors.values()}

    @staticmethod
    def _spec_for_object_field(
        field: str, valid_fields: dict[str, "EventFilterSpec"],
    ) -> "EventFilterSpec | None":
        """Accept `channel` when the catalog lists `channel.id`.

        The documented subscription names the entity (`on_event(...,
        channel="C123")`) while the catalog describes the normalized payload's
        leaves. `domain/matching` resolves the object to its identity field at
        fire time, so rejecting the shorter form here would fail creation for
        filters that match perfectly well.
        """
        prefix = f"{field}."
        for name, spec in valid_fields.items():
            if name.startswith(prefix):
                return spec
        return None

    def validate_filter(self, event_type: str, filter_predicates: list[dict]) -> list[ValidationError]:
        """Validate filter predicates against the catalog. Returns errors (empty = valid)."""
        descriptor = self.get(event_type)
        if descriptor is None:
            return [ValidationError(
                code="UNKNOWN_EVENT_TYPE",
                field="event_type",
                fix_hint=f"Unknown event type '{event_type}'. Available: {', '.join(self._descriptors.keys())}",
            )]

        valid_fields = {spec.field: spec for spec in descriptor.filterable_fields}
        errors = []
        for pred in filter_predicates:
            field = pred.get("field", "")
            op = pred.get("op", "")
            spec = valid_fields.get(field) or self._spec_for_object_field(field, valid_fields)
            if spec is None:
                errors.append(ValidationError(
                    code="UNKNOWN_FILTER_FIELD",
                    field=field,
                    fix_hint=f"Field '{field}' is not filterable for '{event_type}'. "
                             f"Valid fields: {', '.join(valid_fields.keys())}",
                ))
                continue
            if op not in spec.operators:
                errors.append(ValidationError(
                    code="INVALID_OPERATOR",
                    field=field,
                    fix_hint=f"Operator '{op}' not allowed for field '{field}'. "
                             f"Valid operators: {', '.join(spec.operators)}",
                ))
        return errors


# Process-level singleton
_catalog = EventCatalog()


def get_catalog() -> EventCatalog:
    """The process catalog, with built-in descriptors loaded.

    Loading here rather than at module import keeps `catalog/<provider>.py`
    free to import this module for `register_descriptor` without a cycle,
    while still guaranteeing no caller can observe an empty catalog and
    conclude every event type is unknown.
    """
    from app.services.events.catalog import load_builtin_descriptors

    load_builtin_descriptors()
    return _catalog


def register_descriptor(descriptor: AppEventDescriptor) -> None:
    """Register a descriptor in the global catalog."""
    _catalog.register(descriptor)
    logger.debug("Registered event type: %s", descriptor.event_type)
