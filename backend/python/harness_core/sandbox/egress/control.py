"""Where the proxy's control plane listens: only where no sandbox has a network path (E2E4).

The proxy is attached to every sandbox's own network, so a listener on a wildcard address would accept TCP from
every sandbox, leaving the application's ``control_from_sandbox`` refusal as the only layer (and that one knows a
sandbox-facing address only once its binding is registered). ``EGRESS_CONTROL_HOST`` names the control listener's
addresses: a host name resolved at start (in Compose the service name, which Docker's resolver answers only on the
Compose networks) or literal addresses, comma-separated. A wildcard is refused, an address inside the sandbox subnet
pool (``SANDBOX_EGRESS_SUBNETS``, the broker's) is dropped, and nothing left refuses the start.
"""

from __future__ import annotations

import ipaddress
import os
import socket
from typing import TYPE_CHECKING

from harness_core.sandbox.egress.addresses import DEFAULT_SUBNET_POOL
from harness_core.sandbox.egress.errors import ControlPlaneUnconfined

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

ENV_CONTROL_HOST = "EGRESS_CONTROL_HOST"
ENV_SANDBOX_SUBNETS = "SANDBOX_EGRESS_SUBNETS"
_WILDCARDS = frozenset({"", "*", "0.0.0.0", "::", "[::]"})


def _system_resolve(host: str) -> tuple[str, ...]:
    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except (OSError, UnicodeError):
        return ()
    return tuple(dict.fromkeys(str(info[4][0]) for info in infos))


def _literal(value: str) -> str | None:
    try:
        return str(ipaddress.ip_address(value.strip("[]")))
    except ValueError:
        return None


def _is_wildcard(address: str) -> bool:
    if address in _WILDCARDS:
        return True
    literal = _literal(address)
    return literal is not None and ipaddress.ip_address(literal).is_unspecified


def refuse_wildcards(addresses: Iterable[str]) -> tuple[str, ...]:
    """The addresses as given, or ``ControlPlaneUnconfined`` for an empty list or any wildcard among them."""
    chosen = tuple(a.strip() for a in addresses)
    if not chosen or any(_is_wildcard(a) for a in chosen):
        raise ControlPlaneUnconfined("the control plane must listen on named addresses, never a wildcard")
    return chosen


def control_bind_addresses(
    configured: str | None,
    *,
    pool: str = DEFAULT_SUBNET_POOL,
    resolve: Callable[[str], tuple[str, ...]] = _system_resolve,
) -> tuple[str, ...]:
    """The addresses ``configured`` names, outside ``pool``; refused when unset, a wildcard, or nothing is left."""
    if configured is None or not configured.strip():
        raise ControlPlaneUnconfined(f"{ENV_CONTROL_HOST} is not set: the control plane would have no confined address")
    entries = refuse_wildcards(e for e in configured.split(",") if e.strip())
    sandbox_side = ipaddress.ip_network(pool, strict=False)
    found: list[str] = []
    for entry in entries:
        literal = _literal(entry)
        for address in (literal,) if literal is not None else resolve(entry):
            ip = ipaddress.ip_address(address)
            if ip.is_unspecified:
                raise ControlPlaneUnconfined(f"{ENV_CONTROL_HOST} resolves to a wildcard address")
            if ip.version == sandbox_side.version and ip in sandbox_side:
                continue
            if address not in found:
                found.append(address)
    if not found:
        raise ControlPlaneUnconfined(f"{ENV_CONTROL_HOST} names no address outside the sandbox subnet pool")
    return tuple(found)


def sandbox_subnet_pool_from_env() -> str:
    """The sandbox subnet pool the broker allocates from; the proxy reads the same setting."""
    return (os.getenv(ENV_SANDBOX_SUBNETS) or "").strip() or DEFAULT_SUBNET_POOL


def control_bind_addresses_from_env() -> tuple[str, ...]:
    return control_bind_addresses(os.getenv(ENV_CONTROL_HOST), pool=sandbox_subnet_pool_from_env())


__all__ = [
    "ENV_CONTROL_HOST",
    "ENV_SANDBOX_SUBNETS",
    "control_bind_addresses",
    "control_bind_addresses_from_env",
    "refuse_wildcards",
    "sandbox_subnet_pool_from_env",
]
