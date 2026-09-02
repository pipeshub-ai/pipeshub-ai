"""HTTP request metrics, emitted automatically by MetricsMiddleware for every
request across every service. ``route`` is always a route template
(``/api/v1/agent/{id}``), never a raw path, to keep label cardinality bounded.
"""

from app.telemetry.backend import METRICS_BACKEND

HTTP_REQUESTS = METRICS_BACKEND.counter(
    "pipeshub_http_requests_total",
    "Total HTTP requests handled by the service",
    ["service", "route", "method", "status", "org", "domain"],
)

# Buckets span fast cache hits to slow LLM/RAG calls.
HTTP_REQUEST_DURATION = METRICS_BACKEND.histogram(
    "pipeshub_http_request_duration_seconds",
    "HTTP request latency in seconds",
    ["service", "route", "method"],
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0),
)

# Unbounded route values grow the push payload until the collector rejects it.
_MAX_UNIQUE_ROUTES = 300
_seen_routes: set[str] = set()


def bound_route(route: str) -> str:
    if route in _seen_routes:
        return route
    if len(_seen_routes) >= _MAX_UNIQUE_ROUTES:
        return "other"
    _seen_routes.add(route)
    return route
