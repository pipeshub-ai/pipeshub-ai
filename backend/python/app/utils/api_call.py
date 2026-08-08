from typing import TYPE_CHECKING

import aiohttp  # type: ignore
from tenacity import (  # type: ignore
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from app.config.constants.http_status_code import HttpStatusCode
from app.utils.request_context import inject_request_headers

if TYPE_CHECKING:
    from app.services.resource_governor.budget import BytesBudget

# 4xx responses that *are* worth retrying — caller may be rate-limited
# (429) or the upstream timed out servicing the request (408). Every
# other 4xx is a permanent client error (404 deleted, 403 forbidden,
# 400 malformed) and retrying just burns ~25s of exponential backoff
# for a result that cannot change.
_RETRYABLE_4XX_STATUSES = {408, HttpStatusCode.TOO_MANY_REQUESTS.value}


class ApiCallError(Exception):
    """Raised when an upstream HTTP call fails.

    Distinct from generic ``Exception`` so callers (and tenacity) can
    distinguish application-level failures from transport-level retryables.
    Carries ``status_code`` (when known) so callers can branch on the
    upstream HTTP status without parsing the message.
    """

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


def _should_retry(exc: BaseException) -> bool:
    # A truncated/half-written chunked response is an upstream application
    # bug, not a transient transport error — retrying it just multiplies
    # log spam and delays the real failure surfacing to the caller.
    if isinstance(exc, aiohttp.ClientPayloadError):
        return False
    if isinstance(exc, ApiCallError):
        status = exc.status_code
        if status is None:
            # Transport-level failure with no HTTP status — treat as transient.
            return True
        if 400 <= status < 500 and status not in _RETRYABLE_4XX_STATUSES:
            return False
        return True
    # Unknown exception type — let tenacity's default policy decide
    # (here: do not retry, since we only wrap our own errors).
    return False


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=4, max=15),
    retry=retry_if_exception(_should_retry),
)
async def make_api_call(
    route: str, token: str, *, byte_budget: "BytesBudget | None" = None
) -> dict:
    """
    Make an API call with the JWT token.

    Args:
        route (str): The route to send the request to
        token (str): The JWT token to use for authentication
        byte_budget: Optional reservation against the resource governor's
            ``DOWNLOAD_BYTES`` pool (plan section 1.3 / phase 2). When given,
            reserved once the response headers arrive and before the body is
            buffered; released by this function on every failure path, but
            deliberately **not** on success — the caller holds the
            reservation for as long as the returned bytes stay resident, not
            just for the duration of this request.

    Returns:
        dict: The response from the API
    """
    try:
        async with aiohttp.ClientSession() as session:
            url = route

            headers = inject_request_headers({
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            })

            # Make the request
            async with session.get(url, headers=headers) as response:
                # Check status code FIRST - before reading content
                if response.status != HttpStatusCode.SUCCESS.value:
                    error_text = await response.text()
                    raise ApiCallError(
                        f"API call to {url} failed with status {response.status}: {error_text}",
                        status_code=response.status,
                    )

                content_type = response.headers.get("Content-Type", "").lower()

                if byte_budget is not None:
                    content_length = response.headers.get("Content-Length")
                    expected_bytes = int(content_length) if content_length else None
                    await byte_budget.reserve(expected_bytes)

                if response.status == HttpStatusCode.SUCCESS.value and "application/json" in content_type:
                    data = await response.json()
                    return {"is_json": True, "data": data}
                else:
                    data = await response.read()
                    return {"is_json": False, "data": data}
    except aiohttp.ClientPayloadError:
        # Server committed to 200/chunked and then died mid-stream. Don't
        # retry — surface the real cause unchanged so the upstream handler
        # logs the original parser error (e.g. TransferEncodingError) with
        # its traceback intact.
        if byte_budget is not None:
            byte_budget.release()
        raise
    except ApiCallError:
        if byte_budget is not None:
            byte_budget.release()
        raise
    except Exception as e:
        if byte_budget is not None:
            byte_budget.release()
        raise ApiCallError(f"Failed to make API call to {route}: {e}") from e
