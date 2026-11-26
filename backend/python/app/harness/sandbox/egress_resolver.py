"""The egress proxy's ``HostResolver`` in PipesHub: P2.8's one SSRF policy for every name the proxy dials.

``url_fetcher.resolve_public_http_target`` (strict: ``block_non_global=True``)
adds what the proxy's own range check cannot know: blocked host names
(``localhost``, ``*.local``, ``metadata.google.internal``), cloud metadata
addresses that sit in public or CGNAT space (Azure WireServer, Alibaba) and
NAT64-wrapped private addresses. The proxy still checks every answer itself
(``policy.public_addresses``) and dials only what was checked.
"""

from __future__ import annotations

import asyncio
import ipaddress

from app.utils.url_fetcher import FetchError, resolve_public_http_target
from harness_core.sandbox.egress.errors import AddressRefused, HostUnresolvable

# resolve_public_http_target's refusals by policy all start with this; anything else is "no address".
_REFUSED_PREFIX = "Blocked"


def _url(host: str) -> str:
    bare = host.strip("[]")
    try:
        literal = ipaddress.ip_address(bare)
    except ValueError:
        return f"https://{bare}/"
    return f"https://[{literal}]/" if literal.version == 6 else f"https://{literal}/"


class PublicHostResolver:
    """``allow_private``: the answers a deployment's private entry may open (G-21): RFC1918, ULA and CGNAT pass, while
    loopback, link-local, metadata and the blocked names stay refused; the proxy checks the entries itself."""

    def __init__(self, *, allow_private: bool = False) -> None:
        self._allow_private = allow_private

    async def resolve(self, host: str) -> tuple[str, ...]:
        try:
            target = await asyncio.to_thread(
                resolve_public_http_target,
                _url(host),
                block_non_global=not self._allow_private,
                allow_private=self._allow_private,
            )
        except FetchError as exc:
            if str(exc).startswith(_REFUSED_PREFIX):
                raise AddressRefused(host) from exc
            raise HostUnresolvable(host) from exc
        return tuple(str(address) for address in target.addresses)


__all__ = ["PublicHostResolver"]
