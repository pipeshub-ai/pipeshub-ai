"""Docker exec over a hijacked HTTP connection, on asyncio.

docker-py's ``exec_start(socket=True)`` hands back a socket whose HTTP
response was read through a buffered reader, so output that arrived with
the upgrade response can be lost, and it offers no half-close. Here the
upgrade is read by an ``asyncio.StreamReader`` (which keeps every byte past
the headers) and stdin is closed with ``write_eof``.
"""

from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from harness_core.sandbox.errors import BrokerUnavailable

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

_DEFAULT_HOST = "unix:///var/run/docker.sock"
_HEADER_LIMIT = 64 * 1024


@dataclass(frozen=True, slots=True)
class DockerEndpoint:
    """Where the daemon's API is: a unix socket path, or a plain-TCP host and port."""

    unix_path: str | None
    host: str | None
    port: int | None
    api_version: str
    # The Docker API policy proxy's credential the client sends (R38IDENT-FU-1): an exec stream carries it too.
    authorization: str | None = None

    @classmethod
    def resolve(
        cls, api_version: str, docker_host: str | None = None, *, authorization: str | None = None
    ) -> DockerEndpoint:
        """The endpoint docker-py's ``from_env`` uses (``DOCKER_HOST``, else the default socket)."""
        raw = docker_host if docker_host is not None else os.environ.get("DOCKER_HOST") or _DEFAULT_HOST
        parts = urlsplit(raw)
        if parts.scheme == "unix":
            return cls(parts.path, None, None, api_version)
        if parts.scheme in ("tcp", "http") and parts.hostname and not os.environ.get("DOCKER_TLS_VERIFY"):
            return cls(None, parts.hostname, parts.port or 2375, api_version, authorization)
        raise BrokerUnavailable(f"exec needs a unix socket or plain TCP daemon endpoint, not {parts.scheme!r}")

    async def open(self) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        if self.unix_path is not None:
            return await asyncio.open_unix_connection(self.unix_path, limit=_HEADER_LIMIT)
        return await asyncio.open_connection(self.host, self.port, limit=_HEADER_LIMIT)


async def start_exec(endpoint: DockerEndpoint, exec_id: str) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    """Start a created exec attached; returns the raw multiplexed stream."""
    reader, writer = await endpoint.open()
    body = json.dumps({"Detach": False, "Tty": False}).encode()
    credential = f"Authorization: {endpoint.authorization}\r\n" if endpoint.authorization else ""
    head = (
        f"POST /v{endpoint.api_version}/exec/{exec_id}/start HTTP/1.1\r\n"
        "Host: docker\r\n"
        f"{credential}"
        "Content-Type: application/json\r\n"
        f"Content-Length: {len(body)}\r\n"
        "Connection: Upgrade\r\n"
        "Upgrade: tcp\r\n\r\n"
    )
    writer.write(head.encode("ascii") + body)
    await writer.drain()
    status_line = await reader.readline()
    parts = status_line.split(b" ", 2)
    status = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
    while (await reader.readline()) not in (b"\r\n", b"\n", b""):
        pass
    if status not in (101, 200):
        writer.close()
        raise BrokerUnavailable(f"docker refused to start exec: HTTP {status}")
    return reader, writer


async def demux(reader: asyncio.StreamReader) -> AsyncIterator[tuple[int, bytes]]:
    """``(stream, payload)`` frames of Docker's stdout/stderr multiplexing until EOF."""
    while True:
        try:
            header = await reader.readexactly(8)
        except asyncio.IncompleteReadError:
            return
        size = int.from_bytes(header[4:8], "big")
        try:
            payload = await reader.readexactly(size)
        except asyncio.IncompleteReadError as exc:
            payload = exc.partial
        yield header[0], payload
        if len(payload) < size:
            return


__all__ = ["DockerEndpoint", "demux", "start_exec"]
