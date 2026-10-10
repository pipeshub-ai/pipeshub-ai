"""Wait out a rate-limit window that a test exhausted on purpose.

The Node API's limiters keep one count per user, or per IP address when the
request carries no user (the OAuth token, introspect and revoke routes). Every
test in a run comes from the same address and the same test user, so a test that
bursts a limiter until it answers 429 leaves it closed for whatever runs next on
that worker -- the published MCP package's token exchange, for one, failed with
"Too many OAuth client requests" because it started 54 seconds after a burst into
a 60-second window. A burst test calls this before it returns, so it hands the
next test the limiter the way it found it.
"""

from __future__ import annotations

import math
import time
from typing import Callable, Optional

import requests

# The limiters' window (``windowMs: 60 * 1000`` in rate-limit.middleware.ts).
WINDOW_SECONDS = 60
# Whole seconds in the headers are rounded, so a little extra makes sure the
# window has actually rolled over by the time the next request lands.
MARGIN_SECONDS = 1.5
FALLBACK_WAIT_SECONDS = WINDOW_SECONDS + MARGIN_SECONDS


def _seconds_hint(resp: requests.Response) -> Optional[float]:
    for header in ("Retry-After", "RateLimit-Reset"):
        value = resp.headers.get(header)
        if value is not None:
            try:
                return float(value)
            except ValueError:
                pass
    try:
        payload = resp.json()
    except ValueError:
        return None
    error = payload.get("error") if isinstance(payload, dict) else None
    retry_after = error.get("retryAfter") if isinstance(error, dict) else None
    if isinstance(retry_after, (int, float)) and not isinstance(retry_after, bool):
        return float(retry_after)
    return None


def wait_out_rate_limit(
    last_throttled: Optional[requests.Response],
    *,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """Sleep until the window behind ``last_throttled`` (a 429) has reset.

    ``None`` means the burst never reached the limit, so there is nothing to
    wait for. A hint longer than one window cannot be this limiter's, so it is
    capped rather than trusted.
    """
    if last_throttled is None:
        return
    hint = _seconds_hint(last_throttled)
    if hint is None or hint < 0 or hint > WINDOW_SECONDS:
        sleep(FALLBACK_WAIT_SECONDS)
        return
    sleep(math.ceil(hint) + MARGIN_SECONDS)
