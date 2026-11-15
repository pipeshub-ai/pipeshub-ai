"""Unit tests run without the network (installed by tests/unit/conftest.py).

A download a test started in a worker thread, stalled past pytest-timeout, kept the pytest worker from exiting and hung
a whole CI shard. Every connection or name lookup beyond this machine is refused at once and recorded, so the test that
made it fails with the address instead of waiting on it.
"""

from __future__ import annotations

import ipaddress
import socket

blocked: list[str] = []


class NetworkBlockedError(ConnectionRefusedError):
    """Refused, as an offline machine would: code with an offline path takes it at once."""


class LookupBlockedError(socket.gaierror):
    """A name that would need DNS; a numeric address resolves without it and is let through."""


def _local(host: object) -> bool:
    if host is None or host in ("", "localhost", "0.0.0.0"):
        return True
    if isinstance(host, bytes):
        host = host.decode(errors="replace")
    if not isinstance(host, str):
        return False
    if host.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(host.split("%", 1)[0]).is_loopback
    except ValueError:
        return False


def _numeric(host: object) -> bool:
    """An address the resolver reads without DNS, the legacy IPv4 forms (2130706433, 127.1, 0x7f.1) included."""
    if not isinstance(host, str):
        return False
    try:
        ipaddress.ip_address(host.split("%", 1)[0])
    except ValueError:
        try:
            socket.inet_aton(host)
        except OSError:
            return False
    return True


def _refuse(target: str, error: type[OSError] = NetworkBlockedError) -> OSError:
    blocked.append(target)
    return error(f"unit tests do not use the network ({target}): patch the call or serve it locally")


_real_connect = socket.socket.connect
_real_connect_ex = socket.socket.connect_ex
_real_getaddrinfo = socket.getaddrinfo


def _connect(self: socket.socket, address: object) -> None:
    if self.family in (socket.AF_INET, socket.AF_INET6) and not _local(address[0]):  # type: ignore[index]
        raise _refuse(f"connect {address[0]}:{address[1]}")  # type: ignore[index]
    return _real_connect(self, address)


def _connect_ex(self: socket.socket, address: object) -> int:
    if self.family in (socket.AF_INET, socket.AF_INET6) and not _local(address[0]):  # type: ignore[index]
        raise _refuse(f"connect {address[0]}:{address[1]}")  # type: ignore[index]
    return _real_connect_ex(self, address)


# The documentation domains (RFC 2606) stand for a public site in many tests, which check the address before a fake
# answers the request: they resolve to example.com's public address, and a connection to it is still refused.
_DOCUMENTATION_DOMAINS = ("example.com", "example.org", "example.net")
_DOCUMENTATION_ADDRESS = "93.184.216.34"


def _documentation(host: object) -> bool:
    return isinstance(host, str) and any(host == d or host.endswith("." + d) for d in _DOCUMENTATION_DOMAINS)


def _getaddrinfo(host: object, port: object = None, *args: object, **kwargs: object) -> list:
    if _documentation(host):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (_DOCUMENTATION_ADDRESS, port if isinstance(port, int) else 0))]
    if not _local(host) and not _numeric(host):
        raise _refuse(f"resolve {host}", LookupBlockedError)
    return _real_getaddrinfo(host, port, *args, **kwargs)  # type: ignore[arg-type]


def install() -> None:
    socket.socket.connect = _connect  # type: ignore[method-assign]
    socket.socket.connect_ex = _connect_ex  # type: ignore[method-assign]
    socket.getaddrinfo = _getaddrinfo  # type: ignore[assignment]


def answering(names: dict[str, str]):  # noqa: ANN201
    """A ``socket.getaddrinfo`` stand-in for a test whose code looks a name up (an address check before a client is
    built): ``names`` answer from the dict, everything else goes through the guard."""

    def getaddrinfo(host: object, port: object = None, *args: object, **kwargs: object) -> list:
        if isinstance(host, str) and host in names:
            address = names[host]
            family = socket.AF_INET6 if ":" in address else socket.AF_INET
            return [(family, socket.SOCK_STREAM, 6, "", (address, port if isinstance(port, int) else 0))]
        return _getaddrinfo(host, port, *args, **kwargs)

    return getaddrinfo
