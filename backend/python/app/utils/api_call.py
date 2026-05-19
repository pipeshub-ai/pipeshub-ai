import aiohttp  # type: ignore
from tenacity import (  # type: ignore
    retry,
    retry_if_not_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from app.config.constants.http_status_code import HttpStatusCode


class ApiCallError(Exception):
    """Raised when an upstream HTTP call fails.

    Distinct from generic ``Exception`` so callers (and tenacity) can
    distinguish application-level failures from transport-level retryables.
    """


# A truncated/half-written chunked response is an upstream application bug,
# not a transient transport error — retrying it just multiplies log spam and
# delays the real failure surfacing to the caller.
_NON_RETRYABLE = (aiohttp.ClientPayloadError,)


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=4, max=15),
    retry=retry_if_not_exception_type(_NON_RETRYABLE),
)
async def make_api_call(route: str, token: str) -> dict:
    """
    Make an API call with the JWT token.

    Args:
        route (str): The route to send the request to
        token (str): The JWT token to use for authentication

    Returns:
        dict: The response from the API
    """
    try:
        async with aiohttp.ClientSession() as session:
            url = route

            # Add the JWT to the Authorization header
            headers = {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            }

            # Make the request
            async with session.get(url, headers=headers) as response:
                # Check status code FIRST - before reading content
                if response.status != HttpStatusCode.SUCCESS.value:
                    error_text = await response.text()
                    raise ApiCallError(
                        f"API call to {url} failed with status {response.status}: {error_text}"
                    )

                content_type = response.headers.get("Content-Type", "").lower()

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
        raise
    except ApiCallError:
        raise
    except Exception as e:
        raise ApiCallError(f"Failed to make API call to {route}: {e}") from e
