"""aiohttp resolver that refuses addresses an SSRF host policy forbids."""

import ipaddress
import socket
from typing import override

import aiohttp.abc
import aiohttp.resolver

from app.utils.url_fetcher import NO_LOCAL, HostPolicy, ip_is_blocked


def _address_is_allowed(address: str, policy: HostPolicy) -> bool:
    try:
        # Link-local IPv6 results carry a scope ("fe80::1%eth0").
        ip = ipaddress.ip_address(address.split("%", 1)[0])
    except ValueError:
        return False
    return not ip_is_blocked(ip, policy)


class PolicyResolver(aiohttp.abc.AbstractResolver):
    """Resolve hostnames, dropping every address *policy* forbids.

    Vetting at connect time also covers DNS rebinding and redirect targets,
    which a one-off check of the first URL never sees. Literal-address URLs
    skip resolution entirely, so callers still check each hop's URL.
    """

    def __init__(self, policy: HostPolicy = NO_LOCAL) -> None:
        super().__init__()
        self._policy: HostPolicy = policy
        self._inner = aiohttp.resolver.DefaultResolver()

    @override
    async def resolve(
        self,
        host: str,
        port: int = 0,
        family: socket.AddressFamily = socket.AF_INET,
    ) -> list[aiohttp.abc.ResolveResult]:
        results = await self._inner.resolve(host, port, family)
        allowed = [r for r in results if _address_is_allowed(r["host"], self._policy)]
        if not allowed:
            raise OSError(f"{host} resolves only to addresses that may not be fetched")
        return allowed

    @override
    async def close(self) -> None:
        await self._inner.close()
