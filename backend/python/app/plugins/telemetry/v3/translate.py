"""What v3 sends: the legacy series and events that the usage catalogue names, renamed.

One table for both runtimes (the Node service holds the same one), so the rows
for series only Node records are listed here too. Anything not in a table is not
sent on v3, and no row keeps an email, a name, a user id, an org id or a domain.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any, NamedTuple

COUNTER = "counter"
GAUGE = "gauge"

# A registry sample: (sample name, "counter" | "gauge", labels, value).
Sample = tuple[str, str, Mapping[str, str], float]
Labels = tuple[tuple[str, str], ...]


class Point(NamedTuple):
    name: str
    type: str
    labels: Labels
    value: float


@dataclass(frozen=True)
class MetricRule:
    name: str
    type: str
    # legacy label -> v3 label; every other label is dropped and summed over.
    labels: Mapping[str, str]
    constant: Mapping[str, str] = field(default_factory=dict)


def _keep(*names: str, **renamed: str) -> dict[str, str]:
    return {**{n: n for n in names}, **renamed}


METRICS: Mapping[str, MetricRule] = {
    "pipeshub_connector_active": MetricRule(
        "pipeshub.usage.connectors.active", GAUGE, _keep(connector="connector_type")
    ),
    "pipeshub_usage_records": MetricRule(
        "pipeshub.usage.records", GAUGE, _keep("connector_type", "indexing_status")
    ),
    "pipeshub_activity_total": MetricRule(
        "pipeshub.usage.activity", COUNTER, _keep("activity", "status", connector="connector_type")
    ),
    # Recorded by the Node service only.
    "pipeshub_install_info": MetricRule(
        "pipeshub.usage.install.info", GAUGE, _keep("graph_db", "vector_db", "message_broker", "kv_store")
    ),
    "pipeshub_orgs_total": MetricRule("pipeshub.usage.orgs", GAUGE, _keep("account_type")),
    "pipeshub_org_users": MetricRule("pipeshub.usage.users", GAUGE, {}, {"user_state": "total"}),
    "pipeshub_org_auth_methods": MetricRule(
        "pipeshub.usage.auth_methods", GAUGE, _keep(method="auth_method")
    ),
    "pipeshub_usage_sdk_requests_total": MetricRule(
        "pipeshub.usage.sdk.requests", COUNTER, _keep("channel", "sdk_language", "sdk_version", "outcome")
    ),
}

STRING = "string"
INTEGER = "integer"
BOOLEAN = "boolean"


@dataclass(frozen=True)
class EventRule:
    name: str
    # legacy prop -> (v3 attribute, type); every other prop is dropped.
    attributes: Mapping[str, tuple[str, str]]


def _attrs(**types: str) -> dict[str, tuple[str, str]]:
    return {name: (name, kind) for name, kind in types.items()}


EVENTS: Mapping[str, EventRule] = {
    "search_performed": EventRule(
        "search.performed", _attrs(search_type=STRING, status_code=INTEGER, num_queries=INTEGER)
    ),
    "chat_session_started": EventRule("chat.session.started", _attrs(mode=STRING, search_type=STRING)),
    "agent_run": EventRule("agent.run.started", _attrs(has_tools=BOOLEAN, streaming=BOOLEAN)),
    "connector_added": EventRule(
        "connector.added",
        {"connector": ("connector_type", STRING), "scope": ("connector_scope", STRING)},
    ),
    # Recorded by the Node service only.
    "login": EventRule("user.logged_in", _attrs(auth_method=STRING, first_login=BOOLEAN)),
    "signup": EventRule("user.signed_up", {}),
    "org_created": EventRule("org.created", {}),
    "sdk.client.seen": EventRule(
        "sdk.client.seen",
        _attrs(channel=STRING, sdk_language=STRING, sdk_version=STRING, mcp_server_version=STRING),
    ),
    "mcp_connected": EventRule(
        "mcp.client.connected",
        {"client_name": ("mcp_client_name", STRING), "client_version": ("mcp_client_version", STRING)},
    ),
}

# Labels whose values the catalogue spells in lower case.
_LOWER_CASED = frozenset({"indexing_status", "connector_type"})
_NOT_ALLOWED = re.compile(r"[^a-z0-9_.-]")
UNKNOWN = "unknown"


def label_value(label: str, raw: object) -> str:
    """The value as v3 spells it: ``indexing_status`` and ``connector_type`` in lower case and
    limited to ``[a-z0-9_.-]``; an empty one is ``unknown``."""
    value = "" if raw is None else str(raw)
    if label not in _LOWER_CASED:
        return value
    return _NOT_ALLOWED.sub("_", value.lower()) or UNKNOWN


def translate_metrics(samples: Iterable[Sample]) -> list[Point]:
    """The v3 points for a registry snapshot: counters first, each kind sorted, one point per
    v3 series with the values of the dropped labels added up. Counter values are still
    cumulative here; :class:`~app.plugins.telemetry.v3.delta.DeltaTracker` turns them into deltas."""
    sums: dict[tuple[str, str, Labels], float] = {}
    for name, _kind, labels, value in samples:
        rule = METRICS.get(name)
        if rule is None:
            continue
        if not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
            continue
        kept = {new: label_value(new, labels[old]) for old, new in rule.labels.items() if old in labels}
        kept.update(rule.constant)
        key = (rule.name, rule.type, tuple(sorted(kept.items())))
        sums[key] = sums.get(key, 0.0) + float(value)
    points = [Point(name, kind, labels, value) for (name, kind, labels), value in sums.items()]
    points.sort(key=lambda p: (p.type != COUNTER, p.name, p.labels))
    return points


def _attribute(kind: str, name: str, raw: object) -> str | int | bool | None:
    if raw is None:
        return None
    if kind == BOOLEAN:
        if isinstance(raw, bool):
            return raw
        if raw in ("true", "false"):
            return raw == "true"
        return None
    if kind == INTEGER:
        if isinstance(raw, bool):
            return None
        if isinstance(raw, int):
            return raw
        if isinstance(raw, float) and raw.is_integer():
            return int(raw)
        if isinstance(raw, str) and re.fullmatch(r"-?\d{1,12}", raw.strip()):
            return int(raw)
        return None
    if isinstance(raw, bool) or not isinstance(raw, (str, int, float)):
        return None
    return label_value(name, raw)


def translate_event(event: Mapping[str, Any]) -> dict[str, Any] | None:
    """The v3 event for a buffered legacy event, or ``None`` when v3 has no such event."""
    rule = EVENTS.get(str(event.get("event")))
    if rule is None:
        return None
    props = event.get("props")
    props = props if isinstance(props, Mapping) else {}
    attributes: dict[str, Any] = {}
    for old, (new, kind) in rule.attributes.items():
        value = _attribute(kind, new, props.get(old))
        if value is not None:
            attributes[new] = value
    out: dict[str, Any] = {"name": rule.name}
    ts = event.get("timestamp")
    if isinstance(ts, str) and ts:
        out["ts"] = ts
    out["attributes"] = attributes
    return out
