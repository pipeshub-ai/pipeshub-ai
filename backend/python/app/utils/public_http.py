"""SSRF-safe async HTTP GET for URLs a user supplies (e.g. skill package imports).

Every hop, redirects included, is resolved and checked against the shared policy in
``app.utils.url_fetcher`` and then connected to the validated address, so a DNS answer
that changes between the check and the connect cannot steer the request into the
network. Environment proxies are never used: a proxy resolves the hostname again itself,
which would undo the pin.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING
from urllib.parse import urljoin

import httpx

from app.utils.url_fetcher import FetchError, PublicTarget, resolve_public_http_target
from app.utils.url_redaction import redact_url

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = [
    "HopPlan",
    "PublicFetchError",
    "PublicFetchLimits",
    "PublicFetchResponse",
    "PublicUrlFetcher",
    "ResponseTooLargeError",
    "TooManyRedirectsError",
    "UnsafeUrlError",
    "plan_hop",
]


@dataclass(frozen=True)
class PublicFetchLimits:
    max_bytes: int
    timeout_s: float = 15.0
    max_redirects: int = 3


@dataclass(frozen=True)
class PublicFetchResponse:
    url: str
    status_code: int
    headers: Mapping[str, str]
    content: bytes


class PublicFetchError(Exception):
    """Base error. Messages can name internal hosts/addresses: log them, never show them."""


class UnsafeUrlError(PublicFetchError):
    """The URL, or a redirect hop, is not an http(s) URL on a public address."""


class ResponseTooLargeError(PublicFetchError):
    """The response body is larger than ``PublicFetchLimits.max_bytes``."""


class TooManyRedirectsError(PublicFetchError):
    """More redirects than ``PublicFetchLimits.max_redirects``."""


@dataclass(frozen=True)
class HopPlan:
    request_url: httpx.URL
    headers: Mapping[str, str] = field(default_factory=dict[str, str])
    extensions: Mapping[str, str] = field(default_factory=dict[str, str])


def plan_hop(url: str, target: PublicTarget) -> HopPlan:
    """Decide how to send one request to the already-validated ``target``.

    The URL host becomes the pinned address, while the original host is kept for the
    ``Host`` header and, for https, for SNI and certificate verification.
    """
    try:
        request_url = httpx.URL(url)
    except httpx.InvalidURL as e:
        raise UnsafeUrlError(f"Invalid URL: {e}") from e
    # The address check parsed the URL with urllib; a parser differential must not let
    # httpx talk to a host other than the one that was validated.
    if request_url.host != target.host:
        raise UnsafeUrlError(
            f"URL host {request_url.host!r} does not match validated host {target.host!r}"
        )
    # Basic-auth credentials in a plain-HTTP URL would cross the network in the clear.
    if target.scheme == "http" and (request_url.username or request_url.password):
        raise UnsafeUrlError("Credentials in the URL are not allowed over plain HTTP")

    extensions: dict[str, str] = {}
    if target.scheme == "https":
        extensions["sni_hostname"] = request_url.raw_host.decode("ascii")
    return HopPlan(
        request_url=request_url.copy_with(host=str(target.pinned_address)),
        headers={"Host": request_url.netloc.decode("ascii")},
        extensions=extensions,
    )


async def _resolve(url: str) -> PublicTarget:
    try:
        return await asyncio.to_thread(resolve_public_http_target, url)
    except FetchError as e:
        raise UnsafeUrlError(str(e)) from e


async def _read_capped(response: httpx.Response, max_bytes: int) -> bytes:
    declared = response.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > max_bytes:
        raise ResponseTooLargeError(f"Content-Length {declared} exceeds {max_bytes} bytes")
    chunks: list[bytes] = []
    received = 0
    async for chunk in response.aiter_bytes():
        received += len(chunk)
        if received > max_bytes:
            raise ResponseTooLargeError(f"Response body exceeds {max_bytes} bytes")
        chunks.append(chunk)
    return b"".join(chunks)


class PublicUrlFetcher:
    """GET a user-supplied URL without letting it reach private addresses.

    ``transport`` exists for tests; production uses httpx's default transport.
    """

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._transport = transport

    async def get(self, url: str, limits: PublicFetchLimits) -> PublicFetchResponse:
        current_url = url
        for _ in range(limits.max_redirects + 1):
            target = await _resolve(current_url)
            client, response = await self._connect_any(current_url, target, limits.timeout_s)
            try:
                # A 3xx without a usable Location (300, 304, ...) is a final response.
                if response.has_redirect_location:
                    current_url = urljoin(current_url, response.headers["location"])
                    continue
                content = await _read_capped(response, limits.max_bytes)
            except httpx.HTTPError as e:
                raise PublicFetchError(
                    f"GET {redact_url(current_url)} failed: {type(e).__name__}: {e}"
                ) from e
            finally:
                await response.aclose()
                await client.aclose()
            return PublicFetchResponse(
                url=current_url,
                status_code=response.status_code,
                headers=response.headers,
                content=content,
            )
        raise TooManyRedirectsError(
            f"More than {limits.max_redirects} redirects from {redact_url(url)}"
        )

    async def _connect_any(
        self, url: str, target: PublicTarget, timeout_s: float
    ) -> tuple[httpx.AsyncClient, httpx.Response]:
        """Send the request to each validated address until one responds; the caller closes
        both returned objects. A dual-stack host can list an unreachable address first, and
        every address passed the SSRF check, so transport failures fail over to the next.
        Retry stops as soon as an address returns a response (never after one)."""
        last_error: httpx.HTTPError | None = None
        for address in target.addresses:
            plan = plan_hop(url, replace(target, addresses=(address,)))
            client = httpx.AsyncClient(
                trust_env=False,
                follow_redirects=False,
                timeout=timeout_s,
                transport=self._transport,
            )
            try:
                request = client.build_request(
                    "GET",
                    plan.request_url,
                    headers=dict(plan.headers),
                    extensions=dict(plan.extensions),
                )
                response = await client.send(request, stream=True)
            except httpx.HTTPError as e:
                await client.aclose()
                last_error = e
                continue
            return client, response
        raise PublicFetchError(
            f"GET {redact_url(url)} failed: {type(last_error).__name__}: {last_error}"
        ) from last_error
