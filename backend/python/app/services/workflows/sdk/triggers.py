"""Declarative trigger helpers usable inside `@workflow(triggers=[...])`.

These are deliberately inert at runtime: the scheduler never imports the
workflow module to decide when to fire. `ir/extractor.extract_trigger_specs`
reads the same calls statically from the source at generation time and
`TaskEngine.add_trigger` persists them. The objects exist so that generated
code importing `cron`/`interval`/`once_at`/`on_event` is importable and
introspectable, instead of raising `NameError` the first time it runs.
"""
from __future__ import annotations

import re
from typing import Any

__all__ = [
    "TriggerSpec",
    "canonical_event_type",
    "confluence",
    "cron",
    "github",
    "interval",
    "jira",
    "on_event",
    "once_at",
    "slack",
]

_CAMEL_BOUNDARY_RE = re.compile(r"(?<!^)(?=[A-Z])")


def canonical_event_type(source: str, attribute: str) -> str:
    """`("slack", "MessagePosted")` -> `"slack.message.posted"`.

    Generated code reads more naturally as `slack.MessagePosted`, but the
    event catalog (`app.services.events.catalog`) keys everything by the
    dotted lowercase form, and a trigger stored under any other spelling
    silently never matches an incoming event.
    """
    if "_" in attribute:
        tail = attribute.lower().replace("_", ".")
    else:
        tail = ".".join(part.lower() for part in _CAMEL_BOUNDARY_RE.split(attribute))
    return f"{source}.{tail}"


class TriggerSpec:
    """Inert description of one trigger, mirroring the spec dict shape that
    `extract_trigger_specs` produces from the same call written in source."""

    __slots__ = ("kind", "options")

    def __init__(self, kind: str, **options: Any) -> None:
        self.kind = kind
        self.options = {k: v for k, v in options.items() if v is not None}

    def to_spec(self) -> dict[str, Any]:
        return {"kind": self.kind, **self.options}

    def __repr__(self) -> str:
        return f"TriggerSpec({self.to_spec()!r})"

    def __eq__(self, other: object) -> bool:
        return isinstance(other, TriggerSpec) and other.to_spec() == self.to_spec()

    def __hash__(self) -> int:
        return hash(repr(self))


def cron(expression: str, *, tz: str = "UTC") -> TriggerSpec:
    """Fire on a 5-field cron schedule, evaluated in `tz` (IANA name)."""
    return TriggerSpec("cron", cron_expression=expression, timezone=tz)


def interval(seconds: int) -> TriggerSpec:
    """Fire every `seconds` seconds."""
    return TriggerSpec("interval", interval_seconds=int(seconds))


def once_at(when: str) -> TriggerSpec:
    """Fire exactly once at an ISO-8601 instant (must be in the future)."""
    return TriggerSpec("one_time", fire_at=when)


def on_event(event_type: Any, **filters: Any) -> TriggerSpec:
    """Fire when an app event matching `event_type` (plus optional equality
    `filters`) arrives, e.g. `on_event(slack.MessagePosted, channel="C123")`."""
    event_filter: dict[str, Any] = {"event_type": str(event_type)}
    event_filter.update({k: v for k, v in filters.items() if v is not None})
    return TriggerSpec("event", event_filter=event_filter)


class _EventNamespace:
    """`slack.MessagePosted` -> the string `"slack.MessagePosted"`.

    Attribute access is open rather than an enum so a newly published event
    type works without an SDK release; the catalog in
    `app.services.events.catalog` is what actually validates the name when
    the event arrives.
    """

    __slots__ = ("_source",)

    def __init__(self, source: str) -> None:
        self._source = source

    def __getattr__(self, name: str) -> str:
        if name.startswith("_"):
            raise AttributeError(name)
        return canonical_event_type(self._source, name)

    def __str__(self) -> str:
        return self._source

    def __repr__(self) -> str:
        return f"<events {self._source}>"


confluence = _EventNamespace("confluence")
github = _EventNamespace("github")
jira = _EventNamespace("jira")
slack = _EventNamespace("slack")
