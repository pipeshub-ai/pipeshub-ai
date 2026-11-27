"""Knowledge Hub requests past the bound of one list of cross-connector grants."""

from app.plugins.telemetry.backend import METRICS_BACKEND

# Whether the App opens everything to whoever is gated in; `app` must be in scope.
# A Knowledge Hub request whose cross-connector grants passed the bound of one list (`Neo4jProvider._kh_v3_cross`).
KH_CROSS_FALLBACKS = METRICS_BACKEND.counter(
    "pipeshub_kh_cross_grant_fallbacks_total",
    "Knowledge Hub requests past the bound of one list of cross-connector grants: probe (tested on each node) or cut (no user to probe for)",
    ["outcome"],
)
