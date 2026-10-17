"""Metrics for the web_search and fetch_url tools.

``outcome`` is ``ok`` or the failure's ``error_type``, so a provider that starts blocking or
rate limiting shows up as its own series. Labels never carry a query or a URL.
"""

from app.telemetry.backend import METRICS_BACKEND

_BUCKETS = (0.25, 0.5, 1.0, 2.0, 4.0, 8.0, 15.0, 20.0, 30.0)

SEARCHES = METRICS_BACKEND.counter(
    "pipeshub_web_search_requests_total",
    "web_search tool calls, by provider and outcome",
    ["provider", "outcome"],
)

SEARCH_SECONDS = METRICS_BACKEND.histogram(
    "pipeshub_web_search_duration_seconds",
    "Time a web_search tool call took, retries included",
    ["provider"],
    buckets=_BUCKETS,
)

FETCHES = METRICS_BACKEND.counter(
    "pipeshub_web_fetch_requests_total",
    "fetch_url tool calls, by outcome",
    ["outcome"],
)

FETCH_SECONDS = METRICS_BACKEND.histogram(
    "pipeshub_web_fetch_duration_seconds",
    "Time a fetch_url tool call took",
    ["outcome"],
    buckets=_BUCKETS,
)


def record_search(provider: str, outcome: str, seconds: float) -> None:
    SEARCHES.inc(provider, outcome)
    SEARCH_SECONDS.observe(provider, value=seconds)


def record_fetch(outcome: str, seconds: float) -> None:
    FETCHES.inc(outcome)
    FETCH_SECONDS.observe(outcome, value=seconds)
