"""Which addresses an org-configured MCP URL may reach with ``MCP_HUB`` on (P2.8).

Main's URL guard (``app.agents.mcp.url_guard``) resolves every MCP URL once, refuses loopback, link-local and
metadata addresses in every mode, and pins the connection to the checked addresses. With the hub on it also applies
this deployment rule: a private address follows the deployment (allowed unless ``PIPESHUB_BLOCK_PRIVATE_ADDRESSES``
is on, as for the model endpoints an admin configures; with it on, only inside ``MCP_PRIVATE_NETWORKS``), and
multicast and reserved addresses are refused.
"""

from __future__ import annotations

import ipaddress
import os
from dataclasses import dataclass

from app.harness.mcp_hub.settings import hub_enabled
from app.utils.url_fetcher import (
    IPAddress,
    is_never_allowed_address,
    private_addresses_blocked,
)

PRIVATE_NETWORKS_ENV = "MCP_PRIVATE_NETWORKS"

Network = ipaddress.IPv4Network | ipaddress.IPv6Network


def private_networks() -> tuple[Network, ...]:
    """``MCP_PRIVATE_NETWORKS``: the private networks a shared deployment's operator lets MCP servers sit on.
    The process environment only (an org admin can write a configuration key). A value that is not a list of
    networks raises: the deployment is not run on a guess."""
    raw = os.getenv(PRIVATE_NETWORKS_ENV, "")
    networks: list[Network] = []
    for part in raw.split(","):
        if not part.strip():
            if raw.strip():
                raise ValueError(f"{PRIVATE_NETWORKS_ENV}={raw!r} has an empty entry")
            continue
        try:
            networks.append(ipaddress.ip_network(part.strip(), strict=False))
        except ValueError as exc:
            raise ValueError(f"{PRIVATE_NETWORKS_ENV}={raw!r}: {part.strip()!r} is not a network (CIDR)") from exc
    return tuple(networks)


def _plain(ip: IPAddress) -> IPAddress:
    return ip.ipv4_mapped if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped else ip


@dataclass(frozen=True, slots=True)
class AddressRule:
    """What one deployment lets an MCP URL reach. ``pinned`` is the hub's stricter mode."""

    pinned: bool
    private_blocked: bool = False
    networks: tuple[Network, ...] = ()

    def listed(self, ip: IPAddress) -> Network | None:
        return next((n for n in self.networks if ip.version == n.version and ip in n), None)

    def refuses(self, ip: IPAddress) -> bool:
        ip = _plain(ip)
        if is_never_allowed_address(ip):
            return True
        if not self.pinned:
            return False
        if ip.is_loopback or ip.is_multicast or ip.is_reserved:
            return True
        if not ip.is_private:
            return False
        return self.private_blocked and self.listed(ip) is None


def address_rule() -> AddressRule:
    """The deployment's rule now."""
    strict = hub_enabled()
    blocked = strict and private_addresses_blocked()
    return AddressRule(strict, blocked, private_networks() if blocked else ())


__all__ = ["PRIVATE_NETWORKS_ENV", "AddressRule", "address_rule", "private_networks"]
