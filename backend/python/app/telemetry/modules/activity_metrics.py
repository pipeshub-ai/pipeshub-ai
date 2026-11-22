"""Service-side counter for high-value pipeline events a generic HTTP middleware
can't name (document_indexed, agent_run, connector_sync, ...).

Distinct from the Node.js ``recordActivity`` (``app_activity_total``), which is
user-centric (userId/orgId/email). This counter (``pipeshub_activity_total``) is
service-centric — keyed by service/connector/status/kb — and powers the pipeline
dashboards. Keep the two separate; they are not interchangeable.
"""

import threading

from app.telemetry.backend import METRICS_BACKEND

# Every distinct label value is a time series that lives until the process
# exits, so values that come from data (a mime type, a KB id, an email domain)
# are mapped to a bounded set before they reach the counter.
OTHER = "other"
_MAX_VALUES_PER_LABEL = 500

_KNOWN_MIMETYPES = frozenset({
    "application/pdf",
    "application/json",
    "application/xml",
    "application/zip",
    "application/msword",
    "application/vnd.ms-excel",
    "application/vnd.ms-powerpoint",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    "application/vnd.google-apps.document",
    "application/vnd.google-apps.spreadsheet",
    "application/vnd.google-apps.presentation",
    "text/plain", "text/html", "text/markdown", "text/csv", "text/xml",
    "image/png", "image/jpeg", "image/gif", "image/webp", "image/svg+xml",
})
_PASSTHROUGH = frozenset({"none", "unknown", OTHER})

_seen: dict[str, set[str]] = {"org": set(), "kb": set(), "domain": set()}
_seen_lock = threading.Lock()


def bound_mimetype(value: str | None) -> str:
    """Known mime types as-is, everything else (incl. parameters) as ``other``."""
    if not value:
        return "none"
    base = value.split(";", 1)[0].strip().lower()
    return base if base in _KNOWN_MIMETYPES or base in _PASSTHROUGH else OTHER


def _bound_value(label: str, value: str) -> str:
    """Admit at most ``_MAX_VALUES_PER_LABEL`` distinct values per label."""
    if value in _PASSTHROUGH:
        return value
    with _seen_lock:
        seen = _seen[label]
        if value in seen:
            return value
        if len(seen) >= _MAX_VALUES_PER_LABEL:
            return OTHER
        seen.add(value)
        return value

ACTIVITY = METRICS_BACKEND.counter(
    "pipeshub_activity_total",
    "Total service activities recorded",
    ["service", "activity", "connector", "status", "org", "kb", "domain", "mimetype"],
)


def record_service_activity(
    service: str,
    activity: str,
    connector: str = "none",
    status: str = "ok",
    org: str = "unknown",
    kb: str = "none",
    domain: str = "unknown",
    mimetype: str = "none",
) -> None:
    """Increment a service activity counter from a pipeline call site."""
    ACTIVITY.inc(
        service, activity, connector, status,
        _bound_value("org", org), _bound_value("kb", kb),
        _bound_value("domain", domain), bound_mimetype(mimetype),
    )
