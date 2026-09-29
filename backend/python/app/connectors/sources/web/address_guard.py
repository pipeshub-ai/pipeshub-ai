"""Keeps the Web and RSS connectors' requests off loopback, private, link-local and cloud
metadata addresses, redirects included, using the blocked-address policy in ``app.utils.url_fetcher``.
Each check resolves the host once and the request is sent to that answer, so a second DNS answer
(rebinding) can't move it somewhere the check never saw.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

import aiohttp
from aiohttp.abc import AbstractResolver, ResolveResult
from aiohttp.resolver import DefaultResolver

from app.utils.url_fetcher import (
    FetchError,
    PublicTarget,
    _hostname_is_blocked,
    _ip_is_blocked,
    resolve_public_http_target,
)

if TYPE_CHECKING:
    from aiohttp import ClientHandlerType, ClientRequest, ClientResponse


class UnsafeAddressError(aiohttp.ClientConnectionError):
    """The URL isn't http(s), or its host is or resolves to an address the connectors may not reach."""


async def resolve_target(url: str) -> PublicTarget | None:
    """The checked address to send a request for ``url`` to; None when its host doesn't resolve.

    Raises:
        UnsafeAddressError: if the URL can't be fetched or any address it resolves to is blocked.
    """
    try:
        return await asyncio.to_thread(resolve_public_http_target, url)
    except FetchError as e:
        if isinstance(e.__cause__, socket.gaierror):
            return None
        raise UnsafeAddressError(str(e)) from e


async def is_unsafe_url(url: str) -> bool:
    """Whether ``url`` must not be requested; a host that doesn't resolve is left to the fetch to fail."""
    try:
        await resolve_target(url)
    except UnsafeAddressError:
        return True
    return False


def _check(host: str, address: str) -> None:
    try:
        ip = ipaddress.ip_address(address)
    except ValueError as e:
        raise UnsafeAddressError(f"{host!r} resolves to an unusable address") from e
    if _hostname_is_blocked(host) or _ip_is_blocked(ip):
        raise UnsafeAddressError(f"{host!r} is not a public address")


class GuardedResolver(AbstractResolver):
    """aiohttp resolver that refuses a host resolving to a blocked address. The connection is made
    to the addresses returned here, so the check and the connect see the same answer."""

    def __init__(self) -> None:
        self._resolver = DefaultResolver()

    async def resolve(self, host: str, port: int = 0, family: socket.AddressFamily = socket.AF_INET) -> list[ResolveResult]:
        results = await self._resolver.resolve(host, port, family)
        for result in results:
            _check(host, result["host"])
        return results

    async def close(self) -> None:
        await self._resolver.close()


async def _guard_request(request: ClientRequest, handler: ClientHandlerType) -> ClientResponse:
    """Runs for every request the session sends, redirects included. aiohttp connects to an IP
    literal without asking the resolver, so literals are checked here."""
    parts = urlsplit(str(request.url))
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise UnsafeAddressError(f"Only http and https URLs can be fetched, not {parts.scheme!r}")
    try:
        ipaddress.ip_address(parts.hostname)
    except ValueError:
        return await handler(request)
    _check(parts.hostname, parts.hostname)
    return await handler(request)


def create_guarded_session(**kwargs: object) -> aiohttp.ClientSession:
    """An aiohttp session whose requests, and the redirects it follows, reach only public addresses."""
    return aiohttp.ClientSession(
        connector=aiohttp.TCPConnector(resolver=GuardedResolver()),
        middlewares=(_guard_request,),
        **kwargs,  # type: ignore[arg-type]
    )
