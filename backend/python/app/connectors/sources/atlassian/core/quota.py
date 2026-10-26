"""Rate limits for Atlassian Cloud REST calls, and the stop when the hourly OAuth quota is spent.

API-token traffic has short burst limits only. OAuth (3LO) apps also have a points quota per
hour; once it is spent every call gets a 429 whose Retry-After runs to the top of the hour
(https://developer.atlassian.com/cloud/confluence/rate-limiting/). Waiting that out would hold
a sync for up to an hour, so the policy stops instead and the next sync resumes.
"""

import time
from datetime import datetime, timezone

from app.sources.client.resilience import ResiliencePolicy

# Calls are mostly one at a time and take 0.4-1.2 s each on Cloud, so neither cap slows a sync;
# they bound the parts that run in parallel.
API_TOKEN_REQUESTS_PER_SECOND = 10
# About 3 points a call keeps one connector under the 65,000 points an hour of the default quota.
OAUTH_REQUESTS_PER_SECOND = 6


class AtlassianQuotaExhausted(Exception):
    """Atlassian asked for a wait longer than a sync should hold: the hourly quota is spent."""

    def __init__(self, reset_at: float) -> None:
        self.reset_at = reset_at
        super().__init__(f"Atlassian's API quota is used up until {quota_reset_text(reset_at)}")


def quota_reset_text(reset_at: float) -> str:
    return datetime.fromtimestamp(reset_at, tz=timezone.utc).strftime("%H:%M UTC")


class QuotaAwarePolicy(ResiliencePolicy):
    """A retry policy that fails fast, instead of retrying, once the quota is spent.

    A Retry-After longer than ``max_delay`` marks the quota spent until it ends; every
    request after that raises ``AtlassianQuotaExhausted`` without being sent.
    """

    def __init__(self, **kwargs: object) -> None:
        super().__init__(**kwargs)  # type: ignore[arg-type]
        self.quota_reset_at: float | None = None
        self._last_status: int | None = None

    def note_response_status(self, status: int) -> None:
        """The status of the response the next ``note_retry`` is about (set by the transport)."""
        self._last_status = status

    def _quota_spent(self) -> bool:
        return self.quota_reset_at is not None and time.time() < self.quota_reset_at

    def note_retry(self, attempt: int, retry_after: float | None = None) -> float:
        # Only a 429 is the quota; Atlassian says a 503 may carry Retry-After and is not one.
        if retry_after is not None and retry_after > self.max_delay and self._last_status == 429:
            self.quota_reset_at = time.time() + retry_after
            raise AtlassianQuotaExhausted(self.quota_reset_at)
        return super().note_retry(attempt, retry_after)

    async def acquire(self) -> None:
        if self._quota_spent():
            raise AtlassianQuotaExhausted(self.quota_reset_at)  # type: ignore[arg-type]
        await super().acquire()
