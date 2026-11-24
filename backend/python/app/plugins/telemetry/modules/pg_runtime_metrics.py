"""PostgreSQL runtime: statements, units of work, the connection pool and capability downgrades."""

from app.plugins.telemetry.backend import METRICS_BACKEND

_BUCKETS = (0.001, 0.005, 0.02, 0.1, 0.5, 2, 10, 60)

DOWNGRADES = METRICS_BACKEND.counter("pipeshub_capability_downgrade_total", "Capabilities downgraded in non-strict mode", ["from", "to"])

QUERY_SECONDS = METRICS_BACKEND.histogram("pipeshub_pg_query_seconds", "Statement duration", ["service", "site", "class"], _BUCKETS)

UOW_SECONDS = METRICS_BACKEND.histogram("pipeshub_pg_uow_seconds", "Unit of work duration", ["service", "outcome"], _BUCKETS)

POOL_WAIT = METRICS_BACKEND.histogram("pipeshub_pg_pool_wait_seconds", "Wait for a pooled connection", ["service"], _BUCKETS)

RETRIES = METRICS_BACKEND.counter("pipeshub_pg_retries_total", "Unit-of-work retries", ["code"])

ROLE_LEAKS = METRICS_BACKEND.counter("pipeshub_pg_session_role_leak_total", "Session-level SET ROLE found on a pooled connection", ["service"])

ORG_MISSING = METRICS_BACKEND.counter("pipeshub_org_context_missing_total", "Unit of work started without a usable org context", ["service"])
