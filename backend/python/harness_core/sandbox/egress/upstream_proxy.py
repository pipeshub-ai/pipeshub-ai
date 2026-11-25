"""The deployment's own egress (G-21, SEC-EG-13): an upstream HTTP proxy and explicit private destinations.

A deployment may route every public destination through its own forward proxy
(``EGRESS_UPSTREAM_PROXY``), and may open private ``host:port`` entries
(``EGRESS_PRIVATE_ALLOW``: an internal Git server, an artifact mirror) that the
public-address rule would otherwise refuse. Both are the deployment's alone: the
proxy is shared by every org, and a sandbox still reaches only what its own
policy lists, so an org narrows by what it configures, never widens.

What a listed entry never opens: loopback, link-local and cloud metadata
addresses, and PipesHub's own addresses (``internals``), which the caller adds
to the networks it refuses. The upstream proxy is given the address this proxy
resolved and checked, never the name, so a second lookup there cannot move the
connection somewhere this proxy would refuse; the name still travels in
``Host`` and the TLS client hello.
"""

from __future__ import annotations

import asyncio
import base64
import ipaddress
import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, override
from urllib.parse import urlsplit

from harness_core.sandbox.egress.errors import EgressRefusalReason as R
from harness_core.sandbox.egress.errors import EgressRefused
from harness_core.sandbox.egress.policy import (
    is_host_name,
    is_public_address,
    normalize_host,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping, Sequence

    from harness_core.sandbox.egress.policy import Network
    from harness_core.sandbox.egress.types import Target

    Dial = Callable[[str, int], Awaitable[tuple[asyncio.StreamReader, asyncio.StreamWriter]]]

ENV_UPSTREAM_PROXY = "EGRESS_UPSTREAM_PROXY"
ENV_UPSTREAM_PROXY_AUTH = "EGRESS_UPSTREAM_PROXY_AUTH"
ENV_UPSTREAM_NO_PROXY = "EGRESS_UPSTREAM_NO_PROXY"
ENV_PRIVATE_ALLOW = "EGRESS_PRIVATE_ALLOW"
PROXY_AUTHORIZATION = "Proxy-Authorization"
UPSTREAM_CONNECT_TIMEOUT_S = 10.0
_MAX_PROXY_HEAD = 16 * 1024

# Cloud metadata endpoints no range rule catches (Alibaba in CGNAT space, Azure's WireServer on a public address, AWS's
# IPv6 IMDS in ULA space). ``app.utils.url_fetcher`` holds the same list for P2.8 (a cohesion test keeps them equal).
METADATA_ADDRESSES: frozenset[ipaddress.IPv4Address | ipaddress.IPv6Address] = frozenset(
    {
        ipaddress.ip_address("100.100.100.200"),
        ipaddress.ip_address("168.63.129.16"),
        ipaddress.ip_address("fd00:ec2::254"),
    }
)
_NAT64 = ipaddress.IPv6Network("64:ff9b::/96")


class UpstreamEgressInvalid(ValueError):
    """A setting the proxy will not start with; the message names the setting and the entry, never a credential."""


def never_allowed(address: str) -> bool:
    """Addresses no entry opens: loopback, link-local (metadata), multicast, reserved, unspecified, the known metadata
    endpoints, and anything not an address. IPv4 carried in IPv6 (mapped, NAT64) is judged as that IPv4."""
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return True
    if isinstance(ip, ipaddress.IPv6Address):
        if ip.ipv4_mapped is not None:
            ip = ip.ipv4_mapped
        elif ip in _NAT64:
            ip = ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
    return bool(
        ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
        or ip in METADATA_ADDRESSES
    )


@dataclass(frozen=True, slots=True)
class PrivateAllow:
    """One ``EGRESS_PRIVATE_ALLOW`` entry: a host name, an address or a network, and the one port it opens."""

    port: int
    host: str | None = None
    network: Network | None = None

    def names(self, target: Target) -> bool:
        return self.host is not None and self.host == target.host and self.port == target.port

    def covers(self, address: str, port: int) -> bool:
        if self.network is None or port != self.port:
            return False
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:
            return False
        return ip.version == self.network.version and ip in self.network

    @override
    def __str__(self) -> str:
        where = self.host if self.host is not None else str(self.network)
        return f"[{where}]:{self.port}" if ":" in where else f"{where}:{self.port}"


@dataclass(frozen=True, slots=True)
class UpstreamProxy:
    """The deployment's forward proxy: plain HTTP to it, ``CONNECT`` through it for TLS. ``authorization`` is the
    whole ``Proxy-Authorization`` value; it goes only to the upstream proxy, never toward a sandbox or a log."""

    host: str
    port: int
    authorization: str | None = field(default=None, repr=False)
    no_proxy: tuple[str, ...] = ()
    no_proxy_networks: tuple[Network, ...] = ()

    @property
    def url(self) -> str:
        host = f"[{self.host}]" if ":" in self.host else self.host
        return f"http://{host}:{self.port}"

    def headers(self) -> tuple[tuple[str, str], ...]:
        return () if self.authorization is None else ((PROXY_AUTHORIZATION, self.authorization),)

    def bypassed(self, host: str, address: str | None = None) -> bool:
        """Whether ``host`` (dialled at ``address``) goes direct: listed in ``EGRESS_UPSTREAM_NO_PROXY`` by name, by a
        ``.suffix`` (which also matches the bare domain), or by a network holding the address."""
        name = normalize_host(host)
        for entry in self.no_proxy:
            if entry.startswith("."):
                if name.endswith(entry) or name == entry[1:]:
                    return True
            elif name == entry:
                return True
        if address is None or not self.no_proxy_networks:
            return False
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:
            return False
        return any(ip.version == net.version and ip in net for net in self.no_proxy_networks)


@dataclass(frozen=True, slots=True)
class UpstreamEgress:
    upstream: UpstreamProxy | None = None
    private_allow: tuple[PrivateAllow, ...] = ()

    def entries_for(self, target: Target) -> tuple[PrivateAllow, ...]:
        """The entries that could open ``target``: its name and port, or any address entry on its port."""
        return tuple(e for e in self.private_allow if e.names(target) or (e.network is not None and e.port == target.port))


def _port(raw: str, entry: str, setting: str) -> int:
    if not raw.isdigit() or not 0 < int(raw) < 65536:
        raise UpstreamEgressInvalid(f"{setting}: {entry!r} needs a port in 1..65535")
    return int(raw)


def _private_entry(entry: str) -> PrivateAllow:
    if entry.startswith("["):
        where, sep, rest = entry[1:].partition("]")
        if not sep or not rest.startswith(":"):
            raise UpstreamEgressInvalid(f"{ENV_PRIVATE_ALLOW}: {entry!r} is not [address]:port")
        port_text = rest[1:]
    else:
        where, sep, port_text = entry.rpartition(":")
        if not sep or not where:
            raise UpstreamEgressInvalid(f"{ENV_PRIVATE_ALLOW}: {entry!r} is not host:port")
    port = _port(port_text, entry, ENV_PRIVATE_ALLOW)
    try:
        network = ipaddress.ip_network(where, strict=False)
    except ValueError:
        network = None
    if network is not None:
        if any(never_allowed(str(a)) for a in (network.network_address, network.broadcast_address)):
            raise UpstreamEgressInvalid(
                f"{ENV_PRIVATE_ALLOW}: {entry!r} names a loopback, link-local, metadata or reserved address"
            )
        return PrivateAllow(port=port, network=network)
    name = normalize_host(where)
    if not is_host_name(name):
        raise UpstreamEgressInvalid(f"{ENV_PRIVATE_ALLOW}: {entry!r} is not an ASCII host name, address or network")
    return PrivateAllow(port=port, host=name)


def parse_private_allow(raw: str | None) -> tuple[PrivateAllow, ...]:
    """``host:port``, ``address:port``, ``[ipv6]:port`` or ``network:port`` entries, comma-separated."""
    entries = [e.strip() for e in (raw or "").split(",") if e.strip()]
    return tuple(dict.fromkeys(_private_entry(e) for e in entries))


def _no_proxy(raw: str | None) -> tuple[tuple[str, ...], tuple[Network, ...]]:
    names: list[str] = []
    networks: list[Network] = []
    for entry in (e.strip() for e in (raw or "").split(",")):
        if not entry:
            continue
        try:
            networks.append(ipaddress.ip_network(entry.strip("[]"), strict=False))
            continue
        except ValueError:
            pass
        name = normalize_host(entry.removeprefix("*"))
        if entry == "*" or not is_host_name(name.removeprefix(".")):
            # ``*`` would send everything direct: the deployment then has no upstream proxy at all.
            raise UpstreamEgressInvalid(f"{ENV_UPSTREAM_NO_PROXY}: {entry!r} is not a host name, .domain or network")
        names.append(name)
    return tuple(names), tuple(networks)


def parse_upstream_proxy(url: str | None, auth: str | None = None, no_proxy: str | None = None) -> UpstreamProxy | None:
    """``http://host:port`` (no credentials in it, so it may be logged); ``auth`` is ``user:password`` for Basic."""
    if not (url or "").strip():
        if (auth or "").strip():
            raise UpstreamEgressInvalid(f"{ENV_UPSTREAM_PROXY_AUTH} is set without {ENV_UPSTREAM_PROXY}")
        return None
    try:
        parts = urlsplit((url or "").strip())
        port = parts.port
    except ValueError as exc:
        raise UpstreamEgressInvalid(f"{ENV_UPSTREAM_PROXY} has a malformed port") from exc
    if parts.username is not None or parts.password is not None:
        raise UpstreamEgressInvalid(
            f"{ENV_UPSTREAM_PROXY} carries credentials: put them in {ENV_UPSTREAM_PROXY_AUTH} as user:password"
        )
    if parts.scheme != "http" or not parts.hostname or parts.path not in ("", "/") or parts.query or parts.fragment:
        raise UpstreamEgressInvalid(f"{ENV_UPSTREAM_PROXY} must be http://host:port")
    authorization = None
    credential = (auth or "").strip()
    if credential:
        if ":" not in credential:
            raise UpstreamEgressInvalid(f"{ENV_UPSTREAM_PROXY_AUTH} must be user:password")
        authorization = "Basic " + base64.b64encode(credential.encode("utf-8")).decode("ascii")
    names, networks = _no_proxy(no_proxy)
    return UpstreamProxy(
        host=normalize_host(parts.hostname),
        port=port or 3128,
        authorization=authorization,
        no_proxy=names,
        no_proxy_networks=networks,
    )


def upstream_egress_from_env(env: Mapping[str, str] | None = None) -> UpstreamEgress:
    """The deployment's settings; a malformed one raises ``UpstreamEgressInvalid`` so the proxy does not start."""
    source = env if env is not None else {
        ENV_UPSTREAM_PROXY: os.getenv(ENV_UPSTREAM_PROXY, ""),
        ENV_UPSTREAM_PROXY_AUTH: os.getenv(ENV_UPSTREAM_PROXY_AUTH, ""),
        ENV_UPSTREAM_NO_PROXY: os.getenv(ENV_UPSTREAM_NO_PROXY, ""),
        ENV_PRIVATE_ALLOW: os.getenv(ENV_PRIVATE_ALLOW, ""),
    }
    return UpstreamEgress(
        upstream=parse_upstream_proxy(
            source.get(ENV_UPSTREAM_PROXY), source.get(ENV_UPSTREAM_PROXY_AUTH), source.get(ENV_UPSTREAM_NO_PROXY)
        ),
        private_allow=parse_private_allow(source.get(ENV_PRIVATE_ALLOW)),
    )


def _in(address: str, networks: Sequence[Network]) -> bool:
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return any(ip.version == net.version and ip in net for net in networks)


def private_addresses(
    target: Target, addresses: Sequence[str], entries: Sequence[PrivateAllow], refused: Sequence[Network]
) -> tuple[str, ...]:
    """The addresses a private entry lets the proxy dial, in order; one answer it cannot open refuses all.

    An answer passes when it is public, or when an entry names the target (its name and port) or covers the address
    (its network and port). ``refused`` (blocked ranges and PipesHub's own addresses) and ``never_allowed`` win over
    every entry."""
    if not addresses:
        raise EgressRefused(R.DESTINATION_UNRESOLVABLE, "destination has no address", host=target.host)
    named = any(e.names(target) for e in entries)
    for address in addresses:
        if never_allowed(address) or _in(address, refused):
            raise EgressRefused(
                R.DESTINATION_PRIVATE_ADDRESS, "destination resolves to an address no entry opens", host=target.host
            )
        if is_public_address(address) or named or any(e.covers(address, target.port) for e in entries):
            continue
        raise EgressRefused(
            R.DESTINATION_PRIVATE_ADDRESS, "destination resolves to a non-public address", host=target.host
        )
    return tuple(addresses)


async def connect_through(
    upstream: UpstreamProxy,
    address: str,
    port: int,
    *,
    dial: Dial,
    timeout_s: float = UPSTREAM_CONNECT_TIMEOUT_S,
) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    """A TCP stream to ``address:port`` through the upstream proxy's ``CONNECT``; ``OSError`` when it refuses."""
    reader, writer = await asyncio.wait_for(dial(upstream.host, upstream.port), timeout_s)
    authority = f"[{address}]:{port}" if ":" in address else f"{address}:{port}"
    head = f"CONNECT {authority} HTTP/1.1\r\nHost: {authority}\r\n"
    head += "".join(f"{k}: {v}\r\n" for k, v in upstream.headers())
    try:
        writer.write((head + "\r\n").encode("latin-1"))
        await writer.drain()
        answer = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout_s)
    except (asyncio.IncompleteReadError, asyncio.LimitOverrunError, TimeoutError) as exc:
        writer.close()
        raise OSError("the upstream proxy did not answer CONNECT") from exc
    status = answer.split(b"\r\n", 1)[0].split(b" ")
    if len(status) < 2 or not status[1].startswith(b"2") or len(answer) > _MAX_PROXY_HEAD:
        writer.close()
        raise OSError(f"the upstream proxy refused CONNECT ({status[1].decode('latin-1') if len(status) > 1 else '?'})")
    return reader, writer


__all__ = [
    "ENV_PRIVATE_ALLOW",
    "ENV_UPSTREAM_NO_PROXY",
    "ENV_UPSTREAM_PROXY",
    "ENV_UPSTREAM_PROXY_AUTH",
    "METADATA_ADDRESSES",
    "PROXY_AUTHORIZATION",
    "UpstreamEgress",
    "UpstreamEgressInvalid",
    "PrivateAllow",
    "UpstreamProxy",
    "connect_through",
    "upstream_egress_from_env",
    "never_allowed",
    "parse_private_allow",
    "parse_upstream_proxy",
    "private_addresses",
]
