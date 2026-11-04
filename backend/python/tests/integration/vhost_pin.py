"""Sends S3 virtual-hosted host names to a local MinIO, for the storage integration tests.

The S3 leg of those tests runs virtual-hosted: the S3 adapter reads a stored record
only through <bucket>.s3.us-east-1.amazonaws.com URLs. With
STORAGE_IT_S3_VHOST_ADDR=host:port every lookup of an ``*.amazonaws.com`` host
resolves to the MinIO at that address instead (the Node side does the same in
backend/nodejs/apps/tests/support/vhost-pin.js), so those URLs reach MinIO and
nothing leaves the machine. TLS is still verified, against the test CA in
SSL_CERT_FILE. Installed by conftest.py.
"""

from __future__ import annotations

import socket
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable


def is_pinned_host(host: object) -> bool:
    """Host names compare case-insensitively and may end in the root dot."""
    return isinstance(host, str) and host.lower().rstrip(".").endswith(".amazonaws.com")


def pin_getaddrinfo(addr: str, original: Callable[..., object]) -> Callable[..., object]:
    pin_host, _, pin_port = addr.rpartition(":")

    def pinned(host: object, port: object, *args: object, **kwargs: object) -> object:
        if is_pinned_host(host):
            return original(pin_host, int(pin_port), *args, **kwargs)
        return original(host, port, *args, **kwargs)

    return pinned


def install(addr: str) -> None:
    socket.getaddrinfo = pin_getaddrinfo(addr, socket.getaddrinfo)
