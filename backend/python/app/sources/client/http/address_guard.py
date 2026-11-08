"""The guard a connector's or action's HTTP traffic goes through: its server is an address a
user typed, so it must not be able to reach this server or the platform around it.

Every origin a client reaches, redirects included, is resolved and checked, then connected to
the address that was checked, so a DNS answer that changes after the check can't steer the
client elsewhere. The rule is the deployment's rule for hosts users type: loopback, link-local
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

from app.agents.mcp.errors import MCPUrlBlockedError
from app.agents.mcp.url_guard import (
    _RESOLVE_TTL_SECONDS,
    GuardedTransport,
    _env_proxy_for,
    _origin,
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
            no environment proxy would resolve it instead.
    """
    allow_private = not private_addresses_blocked() if allow_private is None else allow_private
    try:
        resolve_public_http_target(url, block_non_global=not allow_private, allow_private=allow_private)
    except UnresolvableHostError as e:
        if not _env_proxy_for(httpx.URL(url)):
            raise AddressNotAllowedError(f"Could not find a server named {urlsplit(url).hostname!r}.") from e
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
                # The guard connects through the environment proxy itself, so the inner transport needs it.
                inner = ResilientHTTPTransport(self._resilience, proxy=_env_proxy_for(request.url))
            guard = self._by_origin[origin] = GuardedTransport(
                str(request.url), allow_private=self._allow_private, inner=inner
            )
        return guard

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        try:
            return await self._guard_for(request).handle_async_request(request)
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

class AddressGuardAdapter(HTTPAdapter):
    """``AddressGuardTransport`` for a ``requests`` session; mount it for both schemes.

    Sessions are used from several threads, so the pin is looked up by origin when the
    connection pool is chosen rather than kept per request. Behind an environment proxy the
    proxy resolves and connects: a name that resolves here is still checked, nothing is pinned.
    """

    def __init__(self, *, allow_private: bool | None = None, **kwargs: Any) -> None:  # noqa: ANN401
        super().__init__(**kwargs)
        self._allow_private = not private_addresses_blocked() if allow_private is None else allow_private
        self._pins: dict[tuple[str, str, int], tuple[float, PublicTarget]] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _key(url: str) -> tuple[str, str, int]:
        parts = urlsplit(url)
        return parts.scheme, (parts.hostname or "").lower(), parts.port or {"http": 80, "https": 443}.get(parts.scheme, 0)

    def _pin(self, url: str) -> tuple[float, PublicTarget] | None:
        with self._lock:
            return self._pins.get(self._key(url))

    def send(self, request: PreparedRequest, *args: Any, **kwargs: Any) -> Response:  # noqa: ANN401
        url = request.url or ""
        proxied = bool(select_proxy(url, kwargs.get("proxies") or {}))
        pin = self._pin(url)
        if proxied or pin is None or time.monotonic() - pin[0] > _RESOLVE_TTL_SECONDS:
            try:
                target = resolve_public_http_target(
                    url, block_non_global=not self._allow_private, allow_private=self._allow_private,
                )
            except UnresolvableHostError as e:
                # Pods that reach the internet only through the proxy can't resolve its names.
                if not proxied:
                    raise requests.ConnectionError(str(e), request=request) from e
            except FetchError as e:
                raise AddressNotAllowedError(
                    f"The address {urlsplit(url).hostname} isn't allowed: {address_rule(allow_private=self._allow_private)}."
                ) from e
            else:
                if not proxied:
                    with self._lock:
                        self._pins[self._key(url)] = (time.monotonic(), target)
        return super().send(request, *args, **kwargs)

    def build_connection_pool_key_attributes(
        self, request: PreparedRequest, verify: bool | str, cert: tuple[str, str] | str | None = None,  # noqa: FBT001 - requests' signature
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        host_params, pool_kwargs = super().build_connection_pool_key_attributes(request, verify, cert)
        pin = self._pin(request.url or "")
        if pin is not None:
            if host_params["scheme"] == "https":
                pool_kwargs["server_hostname"] = host_params["host"].rstrip(".")
            host_params["host"] = str(pin[1].pinned_address)
        return host_params, pool_kwargs

    def add_headers(self, request: PreparedRequest, **kwargs: Any) -> None:  # noqa: ANN401
        # The pool connects to the pinned address; the server still needs its own name.
        request.headers["Host"] = urlsplit(request.url or "").netloc.rpartition("@")[2]
