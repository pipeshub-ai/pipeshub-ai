"""Let a connector's HTTP client reach a fake server of the test's own, which listens on loopback.

The address guard (``app.sources.client.http.address_guard``) refuses loopback for every connector and action, as
it must: their server is an address a user typed. A fake vendor on ``127.0.0.1`` is admitted here by its exact
origin (scheme, literal host and port), pinned to that address; every other URL gets the real check, so loopback
stays refused in any other spelling and on any other port.
"""

from __future__ import annotations

import ipaddress
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING
from unittest.mock import patch
from urllib.parse import urlsplit

from app.agents.mcp import url_guard
from app.sources.client.http import address_guard
from app.utils import url_fetcher

if TYPE_CHECKING:
    from collections.abc import Iterator

    from app.harness.mcp_hub.ssrf import AddressRule
    from app.utils.url_fetcher import IPAddress


def _origin(url: str) -> tuple[str, str | None, int | None]:
    parts = urlsplit(url)
    return parts.scheme, parts.hostname, parts.port


@contextmanager
def admitted(*bases: str) -> Iterator[None]:
    """While open, the guard admits the origin of each of ``bases`` (``http://127.0.0.1:<port>``) and only those."""
    origins = {_origin(base) for base in bases}
    real = url_fetcher.resolve_public_http_target

    def resolve(url: str, **policy: bool) -> url_fetcher.PublicTarget:
        scheme, host, port = _origin(url)
        if (scheme, host, port) in origins and host is not None and port is not None:
            return url_fetcher.PublicTarget(scheme, host, port, (ipaddress.ip_address(host),))
        return real(url, **policy)

    real_rule = url_guard.address_rule
    hosts = {ipaddress.ip_address(host) for _, host, _ in origins if host is not None}

    def rule() -> _Admitting:
        # With MCP_HUB on the deployment's rule refuses loopback again; only an origin ``resolve`` admitted (its exact
        # port) reaches this with one of these addresses.
        return _Admitting(real_rule(), frozenset(hosts))

    # The two places a client's requests are checked: the httpx guard and the requests adapter / URL check.
    with ExitStack() as stack:
        _ = stack.enter_context(patch.object(address_guard, "resolve_public_http_target", resolve))
        _ = stack.enter_context(patch.object(url_guard, "resolve_public_http_target", resolve))
        _ = stack.enter_context(patch.object(url_guard, "address_rule", rule))
        yield


@dataclass(frozen=True)
class _Admitting:
    inner: AddressRule
    admitted: frozenset[IPAddress]

    @property
    def pinned(self) -> bool:
        return self.inner.pinned

    def refuses(self, ip: IPAddress) -> bool:
        return ip not in self.admitted and self.inner.refuses(ip)


__all__ = ["admitted"]
