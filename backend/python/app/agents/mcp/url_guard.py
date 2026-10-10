"""SSRF guard for every server-side request to an MCP server or its OAuth token endpoint.

Built on the shared blocked-address policy in `app.utils.url_fetcher` and the address
pinning in `app.utils.public_http`. Two MCP-specific rules on top:

- Admin-configured servers may sit on the deployment's own network (RFC1918, ULA, CGNAT)
  unless `MCP_ALLOW_PRIVATE_NETWORK_URLS=false`. Loopback, link-local and cloud metadata
  addresses are always refused.
- Every request stays on the configured origin. A custom auth header would otherwise be
  handed to whatever host a redirect points at: httpx only strips `Authorization` on a
  cross-origin hop. (The mcp SDK refuses cross-origin redirects too; the guard doesn't rely
  on it.)

Behind an environment proxy (`HTTPS_PROXY`, honouring `NO_PROXY`) the proxy resolves and
connects, so nothing can be pinned. A name that resolves here is still checked address by
address; one that doesn't — pods that reach the internet only through the proxy — gets the
static checks (scheme, literal addresses, blocked hostnames) and the rest is the proxy's to
police.
"""
from __future__ import annotations

import asyncio
import functools
import logging
import os
import urllib.request
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, Optional
from urllib.parse import urlsplit

import httpx
import httpx2

from app.agents.mcp import wire
from app.agents.mcp.errors import MCPUrlBlockedError
from app.utils.env_utils import env_bool
from app.utils.public_http import UnsafeUrlError, plan_hop
from app.utils.url_fetcher import (
    FetchError,
    PublicTarget,
    UnresolvableHostError,
    check_http_url_without_resolving,
    resolve_public_http_target,
)
from app.utils.url_redaction import redact_url
from mcp.shared._httpx_utils import MCP_DEFAULT_SSE_READ_TIMEOUT, MCP_DEFAULT_TIMEOUT

if TYPE_CHECKING:
    import ssl
    from collections.abc import Callable

logger = logging.getLogger(__name__)

__all__ = [
    "MCPUrlBlockedError",
    "GuardedTransport",
    "MCPUrlPolicy",
    "McpGuardedTransport",
    "PerOriginGuardedTransport",
    "assert_mcp_url_allowed",
    "check_mcp_url",
    "check_mcp_url_at_save",
    "guarded_mcp_http_client",
    "guarded_mcp_http_client_factory",
    "private_network_allowed",
]

PRIVATE_NETWORK_ENV = "MCP_ALLOW_PRIVATE_NETWORK_URLS"


def private_network_allowed(instance: Any = None) -> bool:  # noqa: ANN401
    """Whether `instance`'s URLs may resolve to private-network addresses.

    `instance` is the stored record (dict) or an `MCPServerConfig`. A user's personal
    instance never may: any user can create one, so it gets the public-only policy. An
    admin-created instance follows the deployment setting.
    """
    scope = instance.get("scope") if isinstance(instance, dict) else getattr(instance, "scope", None)
    if scope == "personal":
        return False
    return env_bool(PRIVATE_NETWORK_ENV, True)


def _hostname(url: Optional[str]) -> Optional[str]:
    try:
        host = urlsplit(url or "").hostname
    except ValueError:
        return None
    return host.lower().removesuffix(".") if host else None


@dataclass(frozen=True)
class MCPUrlPolicy:
    """Which URLs may resolve to private-network addresses.

    Only hosts an instance was configured with — its server URL and, for OAuth, the
    authorization and token URLs typed in (or fixed by the catalog template). A URL the
    remote server hands back in its OAuth metadata (an authorization server, a registration
    or token endpoint) is public-only unless it is on one of those hosts, so a server can't
    steer our requests into the deployment's network. A private MCP server whose OAuth server
    sits on another private host needs that host entered as the authorization/token URL.
    """

    allow_private: bool
    trusted_hosts: frozenset[str] = frozenset()

    @classmethod
    def for_instance(cls, instance: Any) -> "MCPUrlPolicy":  # noqa: ANN401
        record = instance if isinstance(instance, dict) else {}
        hosts = {_hostname(record.get(key)) for key in ("url", "authorizationUrl", "tokenUrl")}
        return cls(private_network_allowed(instance), frozenset(h for h in hosts if h))

    def private_ok(self, url: str) -> bool:
        return self.allow_private and _hostname(url) in self.trusted_hosts


_ADDRESS_NOT_ALLOWED = {
    True: (
        "The MCP server URL is not allowed: it must be an http(s) URL that does not point to "
        "a loopback, link-local or cloud metadata address."
    ),
    False: "The MCP server URL is not allowed: it must be a public http(s) URL.",
}
_LEFT_ORIGIN = "The MCP server redirected to a different host, which is not allowed."
# The stand-in status for a request that failed in the transport (see `McpGuardedTransport`).
_STAND_IN_STATUS = 502


def _blocked(url: str, reason: str, message: str) -> MCPUrlBlockedError:
    logger.warning("Blocked MCP request to %s: %s", redact_url(url), reason)
    return MCPUrlBlockedError(message)


async def assert_mcp_url_allowed(url: str, *, allow_private: bool) -> PublicTarget:
    """Resolve `url` and check every address against the policy; the returned target is
    what a connection should be pinned to."""
    try:
        # Public-only also means globally routable: CGNAT (100.64/10, used by Tailscale)
        # reaches the deployment's network as surely as RFC1918 does.
        return await asyncio.to_thread(
            resolve_public_http_target, url, block_non_global=not allow_private, allow_private=allow_private,
        )
    except FetchError as e:
        raise _blocked(url, str(e), _ADDRESS_NOT_ALLOWED[allow_private]) from e


async def check_mcp_url(url: str, *, allow_private: bool) -> None:
    """The check before a request whose connection may go through an environment proxy: a
    name we can't resolve is left to the proxy, having passed the static checks."""
    try:
        await assert_mcp_url_allowed(url, allow_private=allow_private)
    except MCPUrlBlockedError as e:
        if not (isinstance(e.__cause__, UnresolvableHostError) and _env_proxy_for(httpx.URL(url))):
            raise


def check_mcp_url_at_save(url: str, *, allow_private: bool) -> None:
    """Save-time check with no DNS lookup, so a resolver hiccup never blocks saving an
    instance. Every connection is still checked (`check_mcp_url`, `GuardedTransport`)."""
    try:
        check_http_url_without_resolving(url, block_non_global=not allow_private, allow_private=allow_private)
    except FetchError as e:
        raise _blocked(url, str(e), _ADDRESS_NOT_ALLOWED[allow_private]) from e


def _origin(url: Any) -> tuple[str, str, Optional[int]]:  # noqa: ANN401
    default_port = {"http": 80, "https": 443}.get(url.scheme)
    return url.scheme, url.host, url.port or default_port


def _env_proxy_for(url: Any) -> Optional[str]:  # noqa: ANN401
    """The environment proxy httpx would have used for `url`, if any. Proxied deployments
    keep working; the proxy resolves the host itself, so such requests are checked but
    not pinned."""
    proxies = urllib.request.getproxies()
    if not proxies or urllib.request.proxy_bypass(url.host):
        return None
    return proxies.get(url.scheme) or proxies.get("all")


@functools.lru_cache(maxsize=4)
def _ssl_context_for(cert_file: Optional[str], cert_dir: Optional[str]) -> ssl.SSLContext:
    return httpx.create_ssl_context()


def _verify_context() -> ssl.SSLContext:
    """Certificates checked the way httpx checks them by default (certifi, or `SSL_CERT_FILE` /
    `SSL_CERT_DIR`): httpx2 would otherwise use the OS trust store, which a container may not have
    or may not hold a company's CA. Loading the bundle takes about 0.2 s, on the event loop, so
    the context is built once per certificate setting and shared."""
    return _ssl_context_for(os.environ.get("SSL_CERT_FILE"), os.environ.get("SSL_CERT_DIR"))


def _inner_transport(lib: Any, proxy: Optional[str]) -> Any:  # noqa: ANN401
    """The real transport behind a guard."""
    return lib.AsyncHTTPTransport(proxy=proxy, verify=_verify_context())


def _guarded_transport_class(lib: Any) -> type:  # noqa: ANN401
    """The guard for one HTTP library: `httpx` for our own OAuth and discovery requests,
    `httpx2` for MCP traffic (the mcp SDK's library). Their classes don't mix, so each gets
    its own transport; the logic is this one."""

    class _GuardedTransport(lib.AsyncBaseTransport):
        """Refuses any request off `base_url`'s origin and pins the rest to the address the
        policy validated, resolved once per client."""

        def __init__(
            self,
            base_url: str,
            *,
            allow_private: bool,
            inner: Optional[Any] = None,  # noqa: ANN401
        ) -> None:
            self._base_url = base_url
            self._origin = _origin(lib.URL(base_url))
            self._allow_private = allow_private
            proxy = _env_proxy_for(lib.URL(base_url))
            self._pin = proxy is None
            self._inner = inner or _inner_transport(lib, proxy)
            self._target: Optional[PublicTarget] = None
            self._proxy_checked = False
            self._resolve_lock = asyncio.Lock()

        async def _resolved_target(self) -> PublicTarget:
            async with self._resolve_lock:
                if self._target is None:
                    self._target = await assert_mcp_url_allowed(self._base_url, allow_private=self._allow_private)
                return self._target

        async def _checked_for_proxy(self) -> None:
            async with self._resolve_lock:
                if not self._proxy_checked:
                    await check_mcp_url(self._base_url, allow_private=self._allow_private)
                    self._proxy_checked = True

        async def handle_async_request(self, request: Any) -> Any:  # noqa: ANN401
            if _origin(request.url) != self._origin:
                raise _blocked(str(request.url), f"left the configured origin {redact_url(self._base_url)}", _LEFT_ORIGIN)
            if not self._pin:
                await self._checked_for_proxy()
                return await self._inner.handle_async_request(request)
            target = await self._resolved_target()
            # Every address passed the check; a dual-stack host can list an unreachable one
            # first, so a failed connect (nothing sent yet) moves on to the next.
            last_error: Optional[Exception] = None
            for address in target.addresses:
                try:
                    return await self._inner.handle_async_request(self._pinned(request, replace(target, addresses=(address,))))
                except (lib.ConnectError, lib.ConnectTimeout) as e:
                    last_error = e
            raise last_error or lib.ConnectError("no address to connect to", request=request)

        def _pinned(self, request: Any, target: PublicTarget) -> Any:  # noqa: ANN401
            try:
                plan = plan_hop(str(request.url), target)
            except UnsafeUrlError as e:
                raise _blocked(str(request.url), str(e), _ADDRESS_NOT_ALLOWED[self._allow_private]) from e
            headers = lib.Headers(request.headers)
            headers.update(plan.headers)
            return lib.Request(
                request.method,
                str(plan.request_url),
                headers=headers,
                stream=request.stream,
                extensions={**request.extensions, **plan.extensions},
            )

        async def aclose(self) -> None:
            await self._inner.aclose()

    return _GuardedTransport


GuardedTransport = _guarded_transport_class(httpx)
GuardedTransport.__name__ = GuardedTransport.__qualname__ = "GuardedTransport"
_McpGuardedBase = _guarded_transport_class(httpx2)


# Failures before any byte of the request left: nothing can have run.
_NOT_SENT = (
    MCPUrlBlockedError,
    httpx2.ConnectError,
    httpx2.ConnectTimeout,
    httpx2.PoolTimeout,
    httpx2.ProxyError,
    httpx2.UnsupportedProtocol,
    httpx2.LocalProtocolError,
)


class McpGuardedTransport(_McpGuardedBase):
    """The guard for MCP traffic. A request that fails in the transport is answered with a
    stand-in 502 and its cause goes into the operation's wire record, as never sent (the guard
    refused it, no connection) or as lost (the connection failed after it went out). Raised, it
    would escape the SDK's request task and end the whole session, every other call on it
    included: a legacy server's call that outlives its timeout still hits the read timeout later.

    A failure while a response body is read happens after this returns, and still ends the
    session; the client reports that as a lost connection."""

    async def handle_async_request(self, request: "httpx2.Request") -> "httpx2.Response":
        try:
            return await super().handle_async_request(request)
        except _NOT_SENT as e:
            wire.record_unsent(e)
        except httpx2.TransportError as e:
            wire.record_lost(e)
        return httpx2.Response(_STAND_IN_STATUS, request=request)


class PerOriginGuardedTransport(httpx.AsyncBaseTransport):
    """For OAuth discovery, which legitimately crosses origins (a server's metadata names
    its authorization server): every origin is checked and pinned by its own guard, under
    `policy` for that origin's host."""

    def __init__(self, policy: MCPUrlPolicy, *, inner: Optional[httpx.AsyncBaseTransport] = None) -> None:
        self._policy = policy
        self._inner = inner
        self._by_origin: dict[tuple[str, str, Optional[int]], GuardedTransport] = {}

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        origin = _origin(request.url)
        transport = self._by_origin.get(origin)
        if transport is None:
            url = str(request.url)
            transport = self._by_origin[origin] = GuardedTransport(
                url, allow_private=self._policy.private_ok(url), inner=self._inner,
            )
        return await transport.handle_async_request(request)

    async def aclose(self) -> None:
        for transport in self._by_origin.values():
            await transport.aclose()


def guarded_mcp_http_client(
    base_url: str,
    *,
    allow_private: bool,
    headers: Optional[dict[str, str]] = None,
    auth: "Optional[httpx2.Auth]" = None,
    timeout: "Optional[httpx2.Timeout]" = None,
    read_timeout: Optional[float] = None,
    inner: "Optional[httpx2.AsyncBaseTransport]" = None,
) -> "httpx2.AsyncClient":
    """The httpx2 client MCP traffic goes through: the guard, the MCP SDK's own default
    timeouts (30 s, with `read_timeout` or 300 s for reads; httpx2's own default is 5 s), no
    environment settings, and every response noted in the operation's wire record."""
    return httpx2.AsyncClient(
        timeout=timeout or httpx2.Timeout(MCP_DEFAULT_TIMEOUT, read=read_timeout or MCP_DEFAULT_SSE_READ_TIMEOUT),
        headers=headers,
        auth=auth,
        trust_env=False,
        transport=McpGuardedTransport(base_url, allow_private=allow_private, inner=inner),
        event_hooks={"response": [wire.record_response]},
    )


def guarded_mcp_http_client_factory(
    base_url: str,
    *,
    allow_private: bool,
    read_timeout: Optional[float] = None,
    inner: "Optional[httpx2.AsyncBaseTransport]" = None,
) -> "Callable[..., httpx2.AsyncClient]":
    """`guarded_mcp_http_client` as the `httpx_client_factory` the SDK's SSE transport asks for."""

    def _factory(
        headers: Optional[dict[str, str]] = None,
        timeout: "Optional[httpx2.Timeout]" = None,
        auth: "Optional[httpx2.Auth]" = None,
        **_: Any,  # noqa: ANN401
    ) -> "httpx2.AsyncClient":
        return guarded_mcp_http_client(
            base_url, allow_private=allow_private, headers=headers, auth=auth, timeout=timeout,
            read_timeout=read_timeout, inner=inner,
        )

    return _factory

