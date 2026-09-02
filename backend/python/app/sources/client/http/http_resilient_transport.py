"""httpx transport that applies a connector's :class:`ResiliencePolicy`.

Sits below every ``HTTPClient.execute()`` call, so a connector gets rate limiting
and retries across its whole generated datasource without touching call sites.
"""

import asyncio
import contextlib
from typing import Any

import httpx  # type: ignore

from app.sources.client.http.http_retry import is_retryable_status
from app.sources.client.resilience import (
    RETRYABLE_NETWORK_ERRORS,
    ResiliencePolicy,
    parse_retry_after,
)


class ResilientHTTPTransport(httpx.AsyncHTTPTransport):
    """Rate limits and retries requests according to a shared policy.

    The policy is shared with every other request on the same connector, so a 429
    on one request pauses the rest rather than letting each discover the throttle
    independently.
    """

    def __init__(self, policy: ResiliencePolicy, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._policy = policy

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        policy = self._policy
        # One limiter token per HTTP attempt, retries included: the upstream
        # budget counts retries as requests, so exempting them would push the
        # real send rate above the configured limit and cause more throttling.
        await policy.acquire()

        last_exc: BaseException | None = None

        for attempt in range(policy.max_retries + 1):
            try:
                response = await super().handle_async_request(request)
            except RETRYABLE_NETWORK_ERRORS as exc:
                last_exc = exc
                if attempt >= policy.max_retries:
                    break
                delay = policy.note_retry(attempt)
                policy.logger.warning(
                    "%s %s — %s (attempt %s/%s), retrying in %.2fs",
                    request.method, request.url, type(exc).__name__,
                    attempt + 1, policy.max_retries + 1, delay,
                )
                await asyncio.sleep(delay)
                await policy.acquire()
                continue

            if attempt >= policy.max_retries or not is_retryable_status(response.status_code):
                return response

            delay = policy.note_retry(attempt, parse_retry_after(response.headers))
            policy.logger.warning(
                "%s %s — HTTP %s (attempt %s/%s), retrying in %.2fs",
                request.method, request.url, response.status_code,
                attempt + 1, policy.max_retries + 1, delay,
            )
            # The response body is still unread and holding a pooled connection;
            # leaking one per retry exhausts the pool and surfaces later as a
            # PoolTimeout that looks like an unrelated network fault. A close that
            # itself fails must not cost us the retry it was preparing for.
            with contextlib.suppress(Exception):
                await response.aclose()
            await asyncio.sleep(delay)
            await policy.acquire()

        # Only the network-error branch breaks out of the loop; every other path
        # returns a response from inside it.
        if last_exc is None:
            raise RuntimeError("Retry loop exited without a response or an exception")
        policy.logger.error(
            "%s %s failed after %s attempts: %s",
            request.method, request.url, policy.max_retries + 1, type(last_exc).__name__,
        )
        raise last_exc
