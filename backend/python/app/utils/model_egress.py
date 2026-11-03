"""Connections to the AI model servers an admin configures.

An endpoint's text is checked when a model is built and its name is looked up when a config
is saved, but the HTTP client resolves the name again for every new connection, and the DNS
answer can change in between. The network backends here make that one lookup the check: they
resolve the name, refuse it if any answer is an address the deployment must not reach, and
dial the checked address. TLS still uses the URL's host for SNI and certificate checks.

Link-local and cloud metadata addresses are always refused. Private addresses are refused
only with ``PIPESHUB_BLOCK_PRIVATE_ADDRESSES`` on, and never for the deployment's own Ollama
(``OLLAMA_API_URL``).

A request that goes through an ``HTTP(S)_PROXY`` is not pinned: the proxy resolves the name
itself, so egress rules for proxied model traffic belong on the proxy.
"""

from __future__ import annotations

import contextlib
import functools
import ipaddress
import os
import re
import socket
from collections.abc import Iterable
from typing import Any

import anyio
import httpcore
import httpx

# Private httpx API, pinned by tests/unit/utils/test_model_egress.py.
from httpx._utils import URLPattern, get_environment_proxies

from app.utils.logger import create_logger
from app.utils.url_fetcher import (
    PRIVATE_ADDRESS_SWITCH_ENV,
    IPAddress,
    _ip_is_blocked,
    is_never_allowed_address,
    literal_ip,
    private_addresses_blocked,
)

logger = create_logger(__name__)

_URL_SCHEME = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.\-]*://")

SocketOptions = Iterable[tuple[int, int, int] | tuple[int, int, int | bytes] | tuple[int, int, None, int]]


class EndpointRefused(httpcore.ConnectError):
    """The model endpoint's host is, or resolves to, an address this deployment refuses."""


def is_platform_endpoint(endpoint: str | None) -> bool:
    """Whether *endpoint* is the Ollama address the deployment itself supplies."""
    platform = (os.getenv("OLLAMA_API_URL") or "").rstrip("/")
    return bool(platform) and endpoint is not None and endpoint.rstrip("/") == platform


def private_allowed_for(endpoint: str | None) -> bool:
    return not private_addresses_blocked() or is_platform_endpoint(endpoint)


def address_refusal(addresses: Iterable[IPAddress], *, allow_private: bool) -> str | None:
    """Why a host with these addresses may not be called, or None. Every address must pass: one
    bad answer among good ones is enough, since the client may dial any of them."""
    addresses = list(addresses)
    if any(is_never_allowed_address(ip) for ip in addresses):
        return "a link-local, unspecified or cloud metadata address, which is never allowed"
    if not allow_private and any(_ip_is_blocked(ip) for ip in addresses):
        return f"a private or internal address, which this deployment does not allow ({PRIVATE_ADDRESS_SWITCH_ENV})"
    return None


def parse_answers(infos: Iterable[tuple[Any, ...]]) -> list[IPAddress]:
    addresses: list[IPAddress] = []
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            continue
        if ip not in addresses:
            addresses.append(ip)
    return addresses


def _vetted(host: str, addresses: list[IPAddress], *, allow_private: bool) -> list[IPAddress]:
    if not addresses:
        raise httpcore.ConnectError(f"Model endpoint {host!r} does not resolve to any address")
    reason = address_refusal(addresses, allow_private=allow_private)
    if reason is not None:
        # The SDKs reword connect errors, so this log line is where an admin sees the reason.
        # It leaves out the resolved addresses, which describe the deployment's network.
        logger.warning("Refused to connect to model endpoint %r: it resolves to %s", host, reason)
        raise EndpointRefused(f"Model endpoint {host!r} resolves to {reason}.")
    return addresses


def _lookup_error(host: str, error: socket.gaierror) -> httpcore.ConnectError:
    if error.errno == socket.EAI_AGAIN:
        return httpcore.ConnectError(f"Temporary DNS failure looking up model endpoint {host!r}")
    return httpcore.ConnectError(f"Model endpoint {host!r} does not resolve")


class VettedAsyncBackend(httpcore.AsyncNetworkBackend):
    """Resolves once per new connection, refuses a refused address, dials the checked one."""

    def __init__(self, inner: httpcore.AsyncNetworkBackend, *, allow_private: bool) -> None:
        self._inner = inner
        self._allow_private = allow_private

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: SocketOptions | None = None,
    ) -> httpcore.AsyncNetworkStream:
        literal = literal_ip(host)
        if literal is not None:
            addresses = [literal]
        else:
            try:
                with anyio.fail_after(timeout):
                    infos = await anyio.getaddrinfo(host, port, type=socket.SOCK_STREAM)
            except TimeoutError as e:
                raise httpcore.ConnectTimeout(f"Timed out looking up model endpoint {host!r}") from e
            except socket.gaierror as e:
                raise _lookup_error(host, e) from e
            addresses = parse_answers(infos)
        *fallbacks, last = _vetted(host, addresses, allow_private=self._allow_private)
        options = {"timeout": timeout, "local_address": local_address, "socket_options": socket_options}
        for ip in fallbacks:
            with contextlib.suppress(httpcore.ConnectError, httpcore.ConnectTimeout):
                return await self._inner.connect_tcp(str(ip), port, **options)
        return await self._inner.connect_tcp(str(last), port, **options)

    async def connect_unix_socket(
        self, path: str, timeout: float | None = None, socket_options: SocketOptions | None = None
    ) -> httpcore.AsyncNetworkStream:
        raise EndpointRefused("A model endpoint cannot be a unix socket.")

    async def sleep(self, seconds: float) -> None:
        await self._inner.sleep(seconds)


class VettedSyncBackend(httpcore.NetworkBackend):
    """The blocking twin of ``VettedAsyncBackend``."""

    def __init__(self, inner: httpcore.NetworkBackend, *, allow_private: bool) -> None:
        self._inner = inner
        self._allow_private = allow_private

    def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: SocketOptions | None = None,
    ) -> httpcore.NetworkStream:
        literal = literal_ip(host)
        if literal is not None:
            addresses = [literal]
        else:
            try:
                addresses = parse_answers(socket.getaddrinfo(host, port, type=socket.SOCK_STREAM))
            except socket.gaierror as e:
                raise _lookup_error(host, e) from e
        *fallbacks, last = _vetted(host, addresses, allow_private=self._allow_private)
        options = {"timeout": timeout, "local_address": local_address, "socket_options": socket_options}
        for ip in fallbacks:
            with contextlib.suppress(httpcore.ConnectError, httpcore.ConnectTimeout):
                return self._inner.connect_tcp(str(ip), port, **options)
        return self._inner.connect_tcp(str(last), port, **options)

    def connect_unix_socket(
        self, path: str, timeout: float | None = None, socket_options: SocketOptions | None = None
    ) -> httpcore.NetworkStream:
        raise EndpointRefused("A model endpoint cannot be a unix socket.")

    def sleep(self, seconds: float) -> None:
        self._inner.sleep(seconds)


def _as_url(endpoint: str) -> httpx.URL:
    return httpx.URL(endpoint if _URL_SCHEME.match(endpoint) else f"http://{endpoint}")


def env_proxy_applies(endpoint: str | None) -> bool:
    """Whether an httpx client that trusts the environment sends requests for *endpoint*
    through an ``HTTP(S)_PROXY``, honouring ``NO_PROXY`` the way httpx does."""
    if not endpoint:
        return False
    try:
        url = _as_url(endpoint)
    except httpx.InvalidURL:
        return False
    mounts = sorted((URLPattern(pattern), proxy) for pattern, proxy in get_environment_proxies().items())
    for pattern, proxy in mounts:
        if pattern.matches(url):
            return proxy is not None
    return False


@functools.cache
def _note_proxied(host: str) -> None:
    logger.info(
        "Model endpoint %r is reached through an HTTP(S)_PROXY; its address is not checked by "
        "PipesHub, so the proxy must enforce egress rules",
        host,
    )


def guard_httpx_client(client: httpx.Client | httpx.AsyncClient, endpoint: str | None) -> None:
    """Stop *client* following redirects and make its direct connections dial only checked addresses.

    Only the client's own transport is guarded. Proxy mounts are left alone so
    ``HTTP(S)_PROXY`` keeps working; the proxy does the lookup for those requests.

    Something that is not an httpx client (an SDK that changed what it holds, or a test
    double) is logged and left alone; tests/unit/utils/test_model_egress.py fails if a real
    SDK client stops being guarded.

    Raises:
        TypeError: when an httpx client's transport is not httpx's own connection pool, so the
            guard cannot be installed. Failing here beats calling the endpoint unguarded.
    """
    if not isinstance(client, (httpx.Client, httpx.AsyncClient)):
        logger.error("Cannot guard the connections of %s for model endpoint %r", type(client).__name__, endpoint)
        return
    client.follow_redirects = False
    pool = getattr(client._transport, "_pool", None)
    allow_private = private_allowed_for(endpoint)
    if isinstance(pool, httpcore.AsyncConnectionPool):
        if not isinstance(pool._network_backend, VettedAsyncBackend):
            pool._network_backend = VettedAsyncBackend(pool._network_backend, allow_private=allow_private)
    elif isinstance(pool, httpcore.ConnectionPool):
        if not isinstance(pool._network_backend, VettedSyncBackend):
            pool._network_backend = VettedSyncBackend(pool._network_backend, allow_private=allow_private)
    else:
        raise TypeError(f"Cannot guard the connections of {type(client).__name__}: unexpected transport")
    if endpoint:
        try:
            url = _as_url(endpoint)
        except httpx.InvalidURL:
            return
        if client._transport_for_url(url) is not client._transport:
            _note_proxied(url.host)


def guarded_async_client(endpoint: str | None, *, timeout: float) -> httpx.AsyncClient:
    """An ``httpx.AsyncClient`` for calling *endpoint*, guarded by ``guard_httpx_client``."""
    client = httpx.AsyncClient(timeout=timeout)
    guard_httpx_client(client, endpoint)
    return client
