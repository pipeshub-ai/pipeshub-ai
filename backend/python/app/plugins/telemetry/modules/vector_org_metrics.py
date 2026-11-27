"""Vector maintenance passes that skipped a virtual record naming no organisation."""

from app.plugins.telemetry.backend import METRICS_BACKEND

# A maintenance pass met a virtual record whose rows name no org; it is left alone, never run for "any".
ORG_SKIPPED = METRICS_BACKEND.counter(
    "pipeshub_vector_org_skipped_total",
    "Virtual records a maintenance pass skipped because none of their records names an organisation, by site",
    ["site"],
)
