"""Per-sandbox egress subnets from the broker's own pool (P5.4 §1.1).

Each sandbox with an allowlist gets a small subnet holding only itself and the
proxy. The pool is the broker's, not the container runtime's default address
pools, which run out after a few dozen networks.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Collection

DEFAULT_SUBNET_POOL = "10.213.0.0/16"
SANDBOX_PREFIX = 29
# .1 is the IPAM gateway (never assigned in isolated gateway mode), .2 the proxy, .3 the sandbox.
_PROXY_OFFSET = 2
_SANDBOX_OFFSET = 3


@dataclass(frozen=True, slots=True)
class EgressSubnet:
    cidr: str
    proxy_address: str
    sandbox_address: str


class SubnetPool:
    def __init__(self, cidr: str = DEFAULT_SUBNET_POOL, *, prefix: int = SANDBOX_PREFIX) -> None:
        network = ipaddress.ip_network(cidr, strict=True)
        if not isinstance(network, ipaddress.IPv4Network) or not network.prefixlen <= prefix <= 29:
            raise ValueError(f"egress subnet pool {cidr!r} must be IPv4 and hold /{prefix} subnets of 8+ addresses")
        self._network = network
        self._prefix = prefix

    @property
    def cidr(self) -> str:
        return str(self._network)

    def subnet(self, cidr: str) -> EgressSubnet:
        net = ipaddress.ip_network(cidr, strict=True)
        return EgressSubnet(str(net), str(net[_PROXY_OFFSET]), str(net[_SANDBOX_OFFSET]))

    def contains(self, cidr: str) -> bool:
        try:
            net = ipaddress.ip_network(cidr, strict=False)
        except ValueError:
            return False
        return isinstance(net, ipaddress.IPv4Network) and net.subnet_of(self._network)

    def contains_any(self, cidr: str) -> bool:
        """Whether ``cidr`` shares any address with the pool."""
        try:
            net = ipaddress.ip_network(cidr, strict=False)
        except ValueError:
            return False
        return isinstance(net, ipaddress.IPv4Network) and net.overlaps(self._network)

    def allocate(self, taken: Collection[str]) -> EgressSubnet | None:
        """The lowest free subnet, or ``None`` when every one is taken. A taken network of another size blocks
        every subnet it overlaps (another network on the daemon may be a /24 inside the pool)."""
        used: set[ipaddress.IPv4Network] = set()
        for cidr in taken:
            net = ipaddress.ip_network(cidr, strict=False)
            if not isinstance(net, ipaddress.IPv4Network) or not net.overlaps(self._network):
                continue
            if net.prefixlen >= self._prefix:
                used.add(net.supernet(new_prefix=self._prefix))
            elif net.subnet_of(self._network):
                used.update(net.subnets(new_prefix=self._prefix))
            else:
                return None
        for net in self._network.subnets(new_prefix=self._prefix):
            if net not in used:
                return self.subnet(str(net))
        return None


__all__ = ["DEFAULT_SUBNET_POOL", "SANDBOX_PREFIX", "EgressSubnet", "SubnetPool"]
