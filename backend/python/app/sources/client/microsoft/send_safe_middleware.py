"""The Microsoft Graph clients' middleware, minus re-sending a write that may already have landed (QA24-O2).

kiota's default ``RetryHandler`` sends ``POST``, ``PATCH``, ``PUT`` and ``DELETE`` again on 503 and 504 (up to three
times), so an Outlook send or a Teams message answered by a gateway timeout could land more than once; and no Graph
failure was reported as possibly sent. Here a write is sent again only on a 429 (Graph refused it before processing);
the innermost handler sees every attempt and reports one that may have been processed (a transport failure after
the send, a 502 or 504) to the running tool call. Reads keep kiota's retries.

It also carries the vendor rate budget (G-31): a syncing connector's ``ResiliencePolicy`` takes a token per attempt
(``ResilienceHandler``), and a throttle any caller meets (429, or 503 with Retry-After) pauses the account it spends.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast, override

from kiota_authentication_azure.azure_identity_authentication_provider import (  # type: ignore
    AzureIdentityAuthenticationProvider,
)
from kiota_http.httpx_request_adapter import HttpxRequestAdapter  # type: ignore
from kiota_http.kiota_client_factory import KiotaClientFactory  # type: ignore
from kiota_http.middleware import BaseMiddleware, RetryHandler  # type: ignore

from app.services.rate_budget import is_throttle, note_throttled
from app.sources.client.http.send_ambiguity import (
    classify_status,
    is_unsafe,
    note_httpx_failure,
    note_status,
    resend_permitted,
)
from app.sources.client.resilience import parse_retry_after

GRAPH_DEFAULT_SCOPE = "https://graph.microsoft.com/.default"

if TYPE_CHECKING:
    import httpx  # type: ignore
    from azure.core.credentials_async import AsyncTokenCredential  # type: ignore
    from kiota_abstractions.authentication import AuthenticationProvider  # type: ignore
    from kiota_http.middleware.options import RetryHandlerOption  # type: ignore

    from app.sources.client.resilience import ResiliencePolicy


class SendSafeRetryHandler(RetryHandler):
    @override
    def should_retry(self, request: httpx.Request, options: RetryHandlerOption, response: httpx.Response) -> bool:
        unsafe = is_unsafe(request.method)
        if not resend_permitted(classify_status(request.method, response.status_code), unsafe=unsafe):
            return False
        return cast("bool", super().should_retry(request, options, response))  # pyright: ignore[reportUnknownMemberType]


class ResilienceHandler(BaseMiddleware):
    """Below the retry handler, so every attempt is paced, as ``ResilientHTTPTransport`` paces an httpx client."""

    def __init__(self, policy: ResiliencePolicy) -> None:
        super().__init__()
        self._policy = policy

    @override
    async def send(self, request: httpx.Request, transport: httpx.AsyncBaseTransport) -> httpx.Response:
        await self._policy.acquire()
        response = cast("httpx.Response", await super().send(request, transport))  # pyright: ignore[reportUnknownMemberType]
        retry_after = parse_retry_after(response.headers)
        if is_throttle(response.status_code, retry_after):
            # The retry handler above waits this attempt out; the gate holds the connector's other callers back.
            self._policy.pause(self._policy.base_delay if retry_after is None else retry_after)
        return response


class SendReportHandler(BaseMiddleware):
    """Last in the pipeline, so it sees each attempt rather than the retry handler's final answer."""

    @override
    async def send(self, request: httpx.Request, transport: httpx.AsyncBaseTransport) -> httpx.Response:
        try:
            response = cast("httpx.Response", await super().send(request, transport))  # pyright: ignore[reportUnknownMemberType]
        except Exception as exc:
            note_httpx_failure(request.method, exc)
            raise
        note_status(request.method, response.status_code)
        retry_after = parse_retry_after(response.headers)
        if is_throttle(response.status_code, retry_after):
            # The account this call spends, a sync's or an agent tool call's, backs off as a whole (G-31).
            await note_throttled(retry_after)
        return response


def send_safe_graph_adapter(
    authentication_provider: AuthenticationProvider, *, resilience: ResiliencePolicy | None = None
) -> HttpxRequestAdapter:
    """``HttpxRequestAdapter(authentication_provider)`` with kiota's default middleware under the send-safe rule;
    with ``resilience`` (a syncing connector's), every attempt is paced by it."""
    middleware: list[BaseMiddleware] = [
        SendSafeRetryHandler() if isinstance(handler, RetryHandler) else handler
        for handler in KiotaClientFactory.get_default_middleware(None)
    ]
    if resilience is not None:
        middleware.append(ResilienceHandler(resilience))
    client = KiotaClientFactory.create_with_custom_middleware([*middleware, SendReportHandler()])
    return HttpxRequestAdapter(authentication_provider=authentication_provider, http_client=client)


def credential_graph_adapter(
    credential: AsyncTokenCredential, *, resilience: ResiliencePolicy | None = None
) -> HttpxRequestAdapter:
    """What ``GraphServiceClient(credential, scopes=[".default"])`` builds, through ``send_safe_graph_adapter``: for
    a connector that holds an azure-identity credential, so its sync is paced and reports its throttles too."""
    provider = AzureIdentityAuthenticationProvider(credential, scopes=[GRAPH_DEFAULT_SCOPE])
    return send_safe_graph_adapter(provider, resilience=resilience)


__all__ = [
    "GRAPH_DEFAULT_SCOPE",
    "ResilienceHandler",
    "SendReportHandler",
    "SendSafeRetryHandler",
    "credential_graph_adapter",
    "send_safe_graph_adapter",
]
