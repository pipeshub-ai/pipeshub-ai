"""The guard a connector's or action's HTTP traffic goes through: its server is an address a
user typed, so it must not be able to reach this server or the platform around it.

Every origin a client reaches, redirects included, is resolved and checked, then connected to
the address that was checked, so a DNS answer that changes after the check can't steer the
client elsewhere. Behind an environment proxy too: the proxy is asked for the checked address,
and only hosts listed in PIPESHUB_PROXY_ALLOWED_HOSTS go to it by name. The rule is the deployment's rule for hosts users type: loopback, link-local
and cloud metadata addresses never; private ones only while PIPESHUB_BLOCK_PRIVATE_ADDRESSES is
off. A client that must reach public addresses only (a personal action) asks for that.

``AddressGuardTransport`` is the httpx transport every ``HTTPClient`` sends through;
``AddressGuardAdapter`` is the same guard for a ``requests`` session.
"""

from __future__ import annotations

import threading
import time
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

import httpx
import requests
from requests.adapters import HTTPAdapter
from requests.utils import select_proxy
from urllib3.exceptions import NewConnectionError

from app.agents.mcp.errors import MCPUrlBlockedError
from app.agents.mcp.url_guard import (
    _RESOLVE_TTL_SECONDS,
    GuardedTransport,
    ProxyNameRefusedError,
    _env_proxy_for,
    _origin,
    _proxy_name_refused,
    bracket_ipv6_through_proxy,
    proxy_gateway_failure,
    proxy_sends_name,
    proxy_tls_context,
)
from app.sources.client.http.http_resilient_transport import ResilientHTTPTransport
from app.utils.url_fetcher import (
    PRIVATE_ADDRESS_SWITCH_ENV,
    FetchError,
    PublicTarget,
    UnresolvableHostError,
    private_addresses_blocked,
    resolve_public_http_target,
)

if TYPE_CHECKING:
    from ipaddress import IPv4Address, IPv6Address

    from requests import PreparedRequest, Response

    from app.sources.client.resilience import ResiliencePolicy


class AddressNotAllowedError(Exception):
    """A request went to an address this client may not reach."""


def address_rule(*, allow_private: bool) -> str:
    if not allow_private:
        rule = "it must be a public address this server can reach"
        if private_addresses_blocked():
            rule += f" ({PRIVATE_ADDRESS_SWITCH_ENV} is on)"
        return rule
    return "it must not point to this server (loopback), a link-local or a cloud metadata address"



def check_url_allowed(url: str, *, allow_private: bool | None = None) -> None:
    """Check the host of ``url`` now, for a client whose library connects by itself (a cloud
    SDK). Blocking: it looks the name up.

    shortcut: nothing is pinned, so the library's own lookup could get a different answer
    (DNS rebinding). Give the library a guarded transport where it accepts one.

    Raises:
        AddressNotAllowedError: the URL may not be used, or its host doesn't resolve here and
            no environment proxy may resolve it instead (PIPESHUB_PROXY_ALLOWED_HOSTS).
    """
    allow_private = not private_addresses_blocked() if allow_private is None else allow_private
    try:
        resolve_public_http_target(url, block_non_global=not allow_private, allow_private=allow_private)
    except UnresolvableHostError as e:
        host = urlsplit(url).hostname or ""
        if not _env_proxy_for(httpx.URL(url)):
            raise AddressNotAllowedError(f"Could not find a server named {host!r}.") from e
        if not proxy_sends_name(host):
            raise AddressNotAllowedError(str(_proxy_name_refused(url, host))) from e
    except FetchError as e:
        raise AddressNotAllowedError(
            f"The address {urlsplit(url).hostname} isn't allowed: {address_rule(allow_private=allow_private)}."
        ) from e

class AddressGuardTransport(httpx.AsyncBaseTransport):
    """Checks and pins each origin with its own guard. ``allow_private`` defaults to the
    deployment's setting, read when the transport is built."""

    def __init__(
        self,
        *,
        allow_private: bool | None = None,
        resilience: ResiliencePolicy | None = None,
        inner: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._allow_private = not private_addresses_blocked() if allow_private is None else allow_private
        self._resilience = resilience
        self._inner = inner
        self._by_origin: dict[tuple[str, str, int | None], GuardedTransport] = {}

    def _guard_for(self, request: httpx.Request) -> GuardedTransport:
        origin = _origin(request.url)
        guard = self._by_origin.get(origin)
        if guard is None:
            inner = self._inner
            if inner is None and self._resilience is not None:
                # The guard connects through the environment proxy itself, so the inner transport
                # needs it, and the certificate check that names the host through it.
                proxy = _env_proxy_for(request.url)
                extra = {"verify": proxy_tls_context(request.url)} if proxy else {}
                inner = ResilientHTTPTransport(self._resilience, proxy=proxy, **extra)
                if proxy:
                    inner = bracket_ipv6_through_proxy(inner)
            guard = self._by_origin[origin] = GuardedTransport(
                str(request.url), allow_private=self._allow_private, inner=inner
            )
        return guard

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        try:
            return await self._guard_for(request).handle_async_request(request)
        except ProxyNameRefusedError as e:
            raise AddressNotAllowedError(str(e)) from e
        except MCPUrlBlockedError as e:
            if isinstance(e.__cause__, UnresolvableHostError):
                # A name that doesn't resolve is a server not reached, not an address refused.
                raise httpx.ConnectError(str(e.__cause__), request=request) from e
            # The guard's message names MCP servers; this client isn't one.
            raise AddressNotAllowedError(
                f"The address {request.url.host} isn't allowed: {address_rule(allow_private=self._allow_private)}."
            ) from e

    async def aclose(self) -> None:
        for guard in self._by_origin.values():
            await guard.aclose()



def guarded_async_client(
    *,
    allow_private: bool | None = None,
    resilience: ResiliencePolicy | None = None,
    **kwargs: Any,  # noqa: ANN401
) -> httpx.AsyncClient:
    """An ``httpx.AsyncClient`` that sends through ``AddressGuardTransport``; ``kwargs`` go to the client."""
    return httpx.AsyncClient(
        transport=AddressGuardTransport(allow_private=allow_private, resilience=resilience),
        # The guard applies the environment proxy itself; httpx's own would route around it.
        trust_env=False,
        **kwargs,
    )


def _not_connected(error: requests.ConnectionError) -> bool:
    """Whether ``error`` came before any byte of the request was sent: no connection, or a
    CONNECT the proxy refused."""
    if isinstance(error, (requests.exceptions.ProxyError, requests.exceptions.ConnectTimeout)):
        return True
    cause = error.args[0] if error.args else None
    return isinstance(getattr(cause, "reason", None), NewConnectionError)


class AddressGuardAdapter(HTTPAdapter):
    """``AddressGuardTransport`` for a ``requests`` session; mount it for both schemes.

    Sessions are used from several threads, so the checked addresses are kept by origin and
    the one a request is being sent to is kept per thread while its connection pool is chosen. Behind an environment proxy the pin
    holds too: urllib3 tunnels to the pinned address and keeps the name for TLS, and a plain-http
    request names the address in the URL it hands the proxy. A host listed in
    PIPESHUB_PROXY_ALLOWED_HOSTS goes to the proxy by name, checked but not pinned.
    """

    def __init__(self, *, allow_private: bool | None = None, **kwargs: Any) -> None:  # noqa: ANN401
        super().__init__(**kwargs)
        self._allow_private = not private_addresses_blocked() if allow_private is None else allow_private
        self._pins: dict[tuple[str, str, int], tuple[float, PublicTarget]] = {}
        self._lock = threading.Lock()
        # The checked address this thread's request is being sent to, one at a time.
        self._attempt = threading.local()

    @staticmethod
    def _key(url: str) -> tuple[str, str, int]:
        parts = urlsplit(url)
        return parts.scheme, (parts.hostname or "").lower(), parts.port or {"http": 80, "https": 443}.get(parts.scheme, 0)

    def _pin(self, url: str) -> tuple[float, PublicTarget] | None:
        with self._lock:
            return self._pins.get(self._key(url))

    def send(self, request: PreparedRequest, *args: Any, **kwargs: Any) -> Response:  # noqa: ANN401
        url = request.url or ""
        host = urlsplit(url).hostname or ""
        by_name = bool(select_proxy(url, kwargs.get("proxies") or {})) and proxy_sends_name(host)
        pin = self._pin(url)
        if by_name or pin is None or time.monotonic() - pin[0] > _RESOLVE_TTL_SECONDS:
            try:
                target = resolve_public_http_target(
                    url, block_non_global=not self._allow_private, allow_private=self._allow_private,
                )
            except UnresolvableHostError as e:
                # Pods that reach the internet only through the proxy can't resolve its names;
                # a listed one is the proxy's to resolve.
                if by_name:
                    return super().send(request, *args, **kwargs)
                if select_proxy(url, kwargs.get("proxies") or {}):
                    raise AddressNotAllowedError(str(_proxy_name_refused(url, host))) from e
                raise requests.ConnectionError(str(e), request=request) from e
            except FetchError as e:
                raise AddressNotAllowedError(
                    f"The address {urlsplit(url).hostname} isn't allowed: {address_rule(allow_private=self._allow_private)}."
                ) from e
            if not by_name:
                pin = (time.monotonic(), target)
                with self._lock:
                    self._pins[self._key(url)] = pin
        if by_name or pin is None:
            return super().send(request, *args, **kwargs)
        # Every address passed the check; a dual-stack host can list an unreachable one first,
        # so a failed connect, or a CONNECT the proxy refuses, moves on to the next. Neither
        # has sent a byte of the request. For plain http the proxy answers 502 or 504 instead
        # (see `proxy_gateway_failure`).
        proxied = bool(select_proxy(url, kwargs.get("proxies") or {}))
        last_error: requests.RequestException | None = None
        addresses = pin[1].addresses
        for n, address in enumerate(addresses, start=1):
            self._attempt.address = address
            try:
                response = super().send(request, *args, **kwargs)
            except requests.ConnectionError as e:
                if not _not_connected(e):
                    raise
                last_error = e
                continue
            finally:
                self._attempt.address = None
            if n < len(addresses) and proxied and proxy_gateway_failure(
                request.method or "", urlsplit(url).scheme, response.status_code,
            ):
                response.close()
                continue
            return response
        raise last_error or requests.ConnectionError("no address to connect to", request=request)

    def _address(self) -> IPv4Address | IPv6Address | None:
        return getattr(self._attempt, "address", None)

    def request_url(self, request: PreparedRequest, proxies: dict[str, str]) -> str:
        # A plain-http request through a proxy names the server in the URL the proxy fetches;
        # the pinned address goes there, so the proxy can't look the name up itself.
        url = super().request_url(request, proxies)
        address = self._address()
        # requests returns a full URL only for plain http through a proxy; otherwise a path.
        if address is None or not url.startswith("http://"):
            return url
        parts = urlsplit(url)
        if parts.hostname is None or proxy_sends_name(parts.hostname):
            return url
        literal = f"[{address}]" if address.version == 6 else str(address)
        netloc = literal if parts.port is None else f"{literal}:{parts.port}"
        return parts._replace(netloc=netloc).geturl()

    def build_connection_pool_key_attributes(
        self, request: PreparedRequest, verify: bool | str, cert: tuple[str, str] | str | None = None,  # noqa: FBT001 - requests' signature
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        host_params, pool_kwargs = super().build_connection_pool_key_attributes(request, verify, cert)
        address = self._address()
        if address is not None:
            if host_params["scheme"] == "https":
                pool_kwargs["server_hostname"] = host_params["host"].rstrip(".")
            host_params["host"] = str(address)
        return host_params, pool_kwargs

    def add_headers(self, request: PreparedRequest, **kwargs: Any) -> None:  # noqa: ANN401
        # The pool connects to the pinned address; the server still needs its own name.
        request.headers["Host"] = urlsplit(request.url or "").netloc.rpartition("@")[2]
