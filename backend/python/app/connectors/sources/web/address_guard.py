"""Keeps the Web and RSS connectors' requests off loopback, private, link-local and cloud
metadata addresses, redirects included, using the blocked-address policy in ``app.utils.url_fetcher``.
Each check resolves the host once and the request is sent to that answer, so a second DNS answer
(rebinding) can't move it somewhere the check never saw.

Operators list internal hosts the connectors may crawl in ``WEB_CONNECTOR_ALLOWED_HOSTS``
(comma-separated host names). Link-local and cloud metadata addresses stay blocked for them.
"""

from __future__ import annotations

import asyncio
import contextlib
import functools
import ipaddress
import os
import socket
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

import aiohttp
from aiohttp.abc import AbstractResolver, ResolveResult
from aiohttp.resolver import DefaultResolver

from app.utils.url_fetcher import (
    _CLOUD_METADATA_ADDRESSES,
    FetchError,
    IPAddress,
    PublicTarget,
    _hostname_is_blocked,
    _ip_is_blocked,
    resolve_public_http_target,
)

if TYPE_CHECKING:
    from aiohttp import ClientHandlerType, ClientRequest, ClientResponse


ALLOWED_HOSTS_ENV = "WEB_CONNECTOR_ALLOWED_HOSTS"
_DEFAULT_PORTS = {"http": 80, "https": 443}
# AWS's IPv6 metadata endpoint is a private (ULA) address, so only this list keeps it closed to an allowed host.
_NEVER_ALLOWED = _CLOUD_METADATA_ADDRESSES | {ipaddress.ip_address("fd00:ec2::254")}


class UnsafeAddressError(aiohttp.ClientConnectionError):
    """The URL isn't http(s), or its host is or resolves to an address the connectors may not reach."""


@functools.cache
def _allowed_hosts() -> frozenset[str]:
    return frozenset(
        host.strip().lower().removesuffix(".") for host in os.getenv(ALLOWED_HOSTS_ENV, "").split(",") if host.strip()
    )


def _host_allowed(host: str | None) -> bool:
    return bool(host) and host.lower().removesuffix(".") in _allowed_hosts()


def _refuse_never_allowed(host: str, ip: IPAddress) -> None:
    if ip.is_link_local or ip in _NEVER_ALLOWED:
        raise UnsafeAddressError(f"{host!r} resolves to a link-local or cloud metadata address")


def _resolve_allowed_host(url: str) -> PublicTarget:
    parts = urlsplit(url)
    host = parts.hostname or ""
    port = parts.port or _DEFAULT_PORTS[parts.scheme]
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    addresses = tuple(dict.fromkeys(ipaddress.ip_address(info[4][0]) for info in infos))
    for ip in addresses:
        _refuse_never_allowed(host, ip)
    return PublicTarget(parts.scheme, host, port, addresses)


async def resolve_target(url: str) -> PublicTarget | None:
    """The checked address to send a request for ``url`` to; None when its host doesn't resolve.

    Raises:
        UnsafeAddressError: if the URL can't be fetched or any address it resolves to is blocked.
    """
    parts = urlsplit(url)
    if parts.scheme in _DEFAULT_PORTS and _host_allowed(parts.hostname):
        try:
            return await asyncio.to_thread(_resolve_allowed_host, url)
        except socket.gaierror:
            return None
        except ValueError as e:
            raise UnsafeAddressError(f"{url!r} has an invalid port or address") from e
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
    if _host_allowed(host):
        _refuse_never_allowed(host, ip)
        return
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


_PROXY_HEAD_LIMIT = 64 * 1024
_PROXY_REFUSED = b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"


async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while data := await reader.read(65536):
            writer.write(data)
            await writer.drain()
    except (ConnectionError, OSError):
        pass
    finally:
        writer.close()


def _origin_form(method: str, target: str, version: str, header_lines: list[bytes]) -> bytes:
    """A proxied plain-http request as the origin server expects it. Unless it's an upgrade
    (WebSocket), the connection closes after one answer, so Chromium can't reuse it for another
    host that this connection was never checked for."""
    parts = urlsplit(target)
    path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
    upgrade = any(line.lower().startswith(b"upgrade:") for line in header_lines)
    kept = [
        line for line in header_lines
        if upgrade or not line.lower().startswith((b"connection:", b"proxy-connection:", b"keep-alive:"))
    ]
    if not upgrade:
        kept.append(b"Connection: close")
    return f"{method} {path} {version}\r\n".encode("latin-1") + b"".join(line + b"\r\n" for line in kept) + b"\r\n"


async def _connect_checked(pin: PublicTarget) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    """Connect to the first reachable address of ``pin``; every one of them passed the check."""
    for address in pin.addresses[:-1]:
        with contextlib.suppress(OSError):
            return await asyncio.open_connection(str(address), pin.port)
    return await asyncio.open_connection(str(pin.addresses[-1]), pin.port)


async def _serve_proxy_client(client_reader: asyncio.StreamReader, client_writer: asyncio.StreamWriter) -> None:
    try:
        head = await asyncio.wait_for(client_reader.readuntil(b"\r\n\r\n"), timeout=30)
        request_line, *header_lines = head[:-4].split(b"\r\n")
        method, target, version = request_line.decode("latin-1").split(" ", 2)
        url = f"https://{target}/" if method == "CONNECT" else target
        pin = await resolve_target(url)
        if pin is None:
            raise UnsafeAddressError(f"{url!r} did not resolve")
        upstream_reader, upstream_writer = await _connect_checked(pin)
    except (UnsafeAddressError, ValueError, OSError, asyncio.TimeoutError, asyncio.LimitOverrunError, asyncio.IncompleteReadError):
        with contextlib.suppress(ConnectionError, OSError):
            client_writer.write(_PROXY_REFUSED)
            await client_writer.drain()
        client_writer.close()
        return
    if method == "CONNECT":
        client_writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
    else:
        upstream_writer.write(_origin_form(method, target, version, header_lines))
    await asyncio.gather(_pipe(client_reader, upstream_writer), _pipe(upstream_reader, client_writer))


async def start_guard_proxy() -> asyncio.Server:
    """An HTTP proxy on loopback for the headless browser. Chromium sends every request through it,
    redirect hops, subresources and WebSockets included, and it connects only to an address that
    passed the check, so the browser can't reach an internal address or be moved by a second DNS answer."""
    return await asyncio.start_server(_serve_proxy_client, "127.0.0.1", 0, limit=_PROXY_HEAD_LIMIT)
