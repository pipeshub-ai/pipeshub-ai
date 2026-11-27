"""Org-scoped accesses made with no organisation in context, or with another one."""

from app.plugins.telemetry.backend import METRICS_BACKEND

VIOLATIONS = METRICS_BACKEND.counter(
    "pipeshub_org_context_violations_total",
    "Org-scoped accesses with no ambient org (missing) or a different one (mismatch)",
    ["kind", "site"],
)
