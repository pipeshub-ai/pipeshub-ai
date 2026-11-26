"""PipesHub's own addresses as the egress proxy sees them (G-21, G-38): refused as destinations whatever is listed.

Three sources, read again on every refresh because containers move:

- this process's own interface addresses, and the Docker host's address on each
  network it is on (the bridge gateway: Docker's first address in the subnet,
  and the default route's gateway), so no entry reaches the proxy itself or a
  port the host publishes;
- the addresses PipesHub's services resolve to by the names the proxy is
  configured with (the gateways, Node's internal API, the broker, the KV store);
- the sandbox subnet pools, which only sandboxes use.

What the proxy cannot see from inside (the host's LAN address, a Kubernetes
cluster's pod and service ranges) the deployment lists in ``EGRESS_BLOCKED_CIDRS``.
"""

from __future__ import annotations

import contextlib
import fcntl
import ipaddress
import socket
import struct
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from harness_core.sandbox.egress.policy import parse_networks

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from harness_core.sandbox.egress.policy import Network
    from harness_core.sandbox.egress.ports import HostResolver

_SIOCGIFADDR = 0x8915
_SIOCGIFNETMASK = 0x891B
_ROUTE = Path("/proc/net/route")
_INET6 = Path("/proc/net/if_inet6")


def _interface(name: str) -> ipaddress.IPv4Interface | None:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        request = struct.pack("256s", name.encode()[:15])
        try:
            address = fcntl.ioctl(sock.fileno(), _SIOCGIFADDR, request)[20:24]
            netmask = fcntl.ioctl(sock.fileno(), _SIOCGIFNETMASK, request)[20:24]
        except OSError:
            return None
    return ipaddress.IPv4Interface(f"{socket.inet_ntoa(address)}/{socket.inet_ntoa(netmask)}")


def own_interfaces() -> tuple[ipaddress.IPv4Interface, ...]:
    """This process's IPv4 interfaces (address and subnet), loopback left out (it is refused anyway)."""
    found: list[ipaddress.IPv4Interface] = []
    for _, name in socket.if_nameindex():
        interface = _interface(name)
        if interface is not None and not interface.ip.is_loopback:
            found.append(interface)
    return tuple(found)


def own_ipv6_addresses(path: Path = _INET6) -> tuple[ipaddress.IPv6Address, ...]:
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return ()
    found: list[ipaddress.IPv6Address] = []
    for line in lines:
        hexed = line.split()[0] if line.split() else ""
        with contextlib.suppress(ValueError):
            ip = ipaddress.IPv6Address(bytes.fromhex(hexed))
            if not ip.is_loopback and not ip.is_link_local:
                found.append(ip)
    return tuple(found)


def route_gateways(path: Path = _ROUTE) -> tuple[ipaddress.IPv4Address, ...]:
    """Every gateway in the IPv4 routing table (``/proc/net/route``, little-endian hex)."""
    try:
        lines = path.read_text().splitlines()[1:]
    except OSError:
        return ()
    found: list[ipaddress.IPv4Address] = []
    for line in lines:
        fields = line.split()
        if len(fields) < 3:
            continue
        with contextlib.suppress(ValueError):
            gateway = ipaddress.IPv4Address(struct.unpack("<I", bytes.fromhex(fields[2]))[0])
            if not gateway.is_unspecified:
                found.append(gateway)
    return tuple(found)


def host_networks(
    interfaces: Iterable[ipaddress.IPv4Interface],
    gateways: Iterable[ipaddress.IPv4Address] = (),
    ipv6: Iterable[ipaddress.IPv6Address] = (),
) -> tuple[Network, ...]:
    """Each own address, the first address of each own subnet (Docker's bridge gateway: the host) and each route's
    gateway, as single-address networks."""
    addresses: list[ipaddress.IPv4Address | ipaddress.IPv6Address] = []
    for interface in interfaces:
        addresses.append(interface.ip)
        if interface.network.num_addresses > 2:
            addresses.append(interface.network.network_address + 1)
    addresses.extend(gateways)
    addresses.extend(ipv6)
    return tuple(ipaddress.ip_network(a) for a in dict.fromkeys(addresses))


def discover_host_networks() -> tuple[Network, ...]:
    return host_networks(own_interfaces(), route_gateways(), own_ipv6_addresses())


async def resolved_networks(names: Iterable[str], resolver: HostResolver) -> tuple[Network, ...]:
    """The addresses ``names`` resolve to now, as single-address networks; a name that does not resolve adds none."""
    found: list[Network] = []
    for name in dict.fromkeys(n for n in names if n):
        try:
            addresses = await resolver.resolve(name)
        except Exception:
            continue
        for address in addresses:
            with contextlib.suppress(ValueError):
                found.append(ipaddress.ip_network(address))
    return tuple(found)


def merged(*groups: Sequence[Network]) -> tuple[Network, ...]:
    return tuple(dict.fromkeys(net for group in groups for net in group))


def host_of(url_or_host: str) -> str:
    """The host of a URL (``http://redis:6379`` -> ``redis``), or the value itself when it is a bare host."""
    value = url_or_host.strip()
    if "://" not in value:
        return value.rsplit(":", 1)[0] if value.count(":") == 1 else value.strip("[]")
    return urlsplit(value).hostname or ""


async def internal_networks(
    names: Iterable[str], resolver: HostResolver, pools: Iterable[str] = ()
) -> tuple[Network, ...]:
    """PipesHub's own addresses now: this process and the Docker host, the named services, the sandbox pools."""
    named = await resolved_networks((host_of(n) for n in names if n), resolver)
    return merged(discover_host_networks(), named, parse_networks(pools))



__all__ = [
    "discover_host_networks",
    "host_networks",
    "host_of",
    "internal_networks",
    "merged",
    "own_interfaces",
    "own_ipv6_addresses",
    "resolved_networks",
    "route_gateways",
]
