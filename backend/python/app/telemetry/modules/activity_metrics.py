"""Service-side counter for high-value pipeline events a generic HTTP middleware
can't name (document_indexed, agent_run, connector_sync, ...).

Distinct from the Node.js ``recordActivity`` (``app_activity_total``), which is
user-centric (userId/orgId/email). This counter (``pipeshub_activity_total``) is
service-centric — keyed by service/connector/status/kb — and powers the pipeline
dashboards. Keep the two separate; they are not interchangeable.
"""

from app.telemetry.backend import METRICS_BACKEND

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
    ACTIVITY.inc(service, activity, connector, status, org, kb, domain, mimetype)
