"""``HostResolver`` adapters: a static table, and the proxy host's own resolver."""

from __future__ import annotations

import asyncio
import ipaddress
import socket
from typing import TYPE_CHECKING

from harness_core.sandbox.egress.errors import HostUnresolvable

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence


def _literal(host: str) -> str | None:
    try:
        return str(ipaddress.ip_address(host.strip("[]")))
    except ValueError:
        return None


class StaticResolver:
    def __init__(self, table: Mapping[str, Sequence[str]] | None = None) -> None:
        self._table = {k.lower(): tuple(v) for k, v in (table or {}).items()}

    async def resolve(self, host: str) -> tuple[str, ...]:
        literal = _literal(host)
        if literal is not None:
            return (literal,)
        found = self._table.get(host.lower())
        if not found:
            raise HostUnresolvable(host)
        return found


class SystemResolver:
    async def resolve(self, host: str) -> tuple[str, ...]:
        literal = _literal(host)
        if literal is not None:
            return (literal,)
        try:
            infos = await asyncio.get_running_loop().getaddrinfo(host, None, type=socket.SOCK_STREAM)
        except (OSError, UnicodeError) as exc:
            raise HostUnresolvable(host) from exc
        addresses = tuple(dict.fromkeys(str(info[4][0]) for info in infos))
        if not addresses:
            raise HostUnresolvable(host)
        return addresses


__all__ = ["StaticResolver", "SystemResolver"]
