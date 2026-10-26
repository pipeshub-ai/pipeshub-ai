"""Vector search retries, by provider and how the retry ended.

``outcome`` is one of ``recovered`` (the retry answered), ``failed`` (it did
not) and ``skipped_budget`` (the first attempt used up the time a retry is
allowed). Nothing about the query or the collection is recorded.
"""

from app.telemetry.backend import METRICS_BACKEND

SEARCH_RETRIES = METRICS_BACKEND.counter(
    "pipeshub_vector_search_retries_total",
    "Vector searches that hit a transient store error, by provider and retry outcome",
    ["provider", "outcome"],
)


def record_search_retry(provider: str, outcome: str) -> None:
    SEARCH_RETRIES.inc(provider, outcome)
