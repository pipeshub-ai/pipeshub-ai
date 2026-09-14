"""SSRF-safe async HTTP GET for URLs a user supplies (e.g. skill package imports).

Every hop, redirects included, is resolved and checked against the shared policy in
``app.utils.url_fetcher`` and then connected to the validated address, so a DNS answer
that changes between the check and the connect cannot steer the request into the
network. When a configured proxy applies to the host, the hop goes through that proxy
unpinned instead: the proxy resolves DNS itself, so the check is advisory on that route.
"""

from __future__ import annotations

import asyncio
import urllib.request
from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from urllib.parse import urljoin

import httpx

from app.utils.url_fetcher import FetchError, PublicTarget, resolve_public_http_target

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
    proxy: str | None = None


def plan_hop(
    url: str, target: PublicTarget, proxies: Mapping[str, str] | None = None
) -> HopPlan:
    """Decide how to send one request to the already-validated ``target``.

    Direct: the URL host becomes the first validated address, while the original host is
    kept for the ``Host`` header and, for https, for SNI and certificate verification.
    Proxied (a proxy is configured for the scheme and ``no_proxy`` does not bypass the
    host): the hostname URL is sent through that proxy. ``proxies`` defaults to
    ``urllib.request.getproxies()``.
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

    if proxies is None:
        proxies = urllib.request.getproxies()
    proxy = proxies.get(target.scheme) or proxies.get("all")
    if proxy and not urllib.request.proxy_bypass(target.host):
        return HopPlan(request_url=request_url, proxy=proxy)

    extensions: dict[str, str] = {}
    if target.scheme == "https":
        extensions["sni_hostname"] = request_url.raw_host.decode("ascii")
    return HopPlan(
        request_url=request_url.copy_with(host=str(target.addresses[0])),
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
            plan = plan_hop(current_url, await _resolve(current_url))
            try:
                async with httpx.AsyncClient(
                    trust_env=False,
                    follow_redirects=False,
                    timeout=limits.timeout_s,
                    proxy=plan.proxy,
                    transport=self._transport,
                ) as client:
                    request = client.build_request(
                        "GET",
                        plan.request_url,
                        headers=dict(plan.headers),
                        extensions=dict(plan.extensions),
                    )
                    response = await client.send(request, stream=True)
                    try:
                        if response.is_redirect:
                            current_url = urljoin(current_url, response.headers["location"])
                            continue
                        content = await _read_capped(response, limits.max_bytes)
                    finally:
                        await response.aclose()
            except httpx.HTTPError as e:
                raise PublicFetchError(
                    f"GET {current_url} failed: {type(e).__name__}: {e}"
                ) from e
            return PublicFetchResponse(
                url=current_url,
                status_code=response.status_code,
                headers=response.headers,
                content=content,
            )
        raise TooManyRedirectsError(f"More than {limits.max_redirects} redirects from {url}")
