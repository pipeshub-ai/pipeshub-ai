"""HTTP to ``opencode serve`` inside a sandbox, tunnelled through ``SandboxHandle.connect``.

The server listens on loopback in the sandbox; the only way in is the broker's
tunnel, so the HTTP connection pool's network backend opens every connection
with ``connect(port)``. opencode's server auth is HTTP Basic with a
per-sandbox password (F4 §2.1).
"""

from __future__ import annotations

import asyncio
import base64
import json
from typing import TYPE_CHECKING, override

import httpcore

from harness_core.engines.jsonview import as_object
from harness_core.sandbox.errors import SandboxConnectError

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterable, Mapping

    from harness_core.engines.types import JsonValue
    from harness_core.sandbox import ByteStream, SandboxHandle

USERNAME = "opencode"
REQUEST_TIMEOUT_S = 30.0


class OpencodeHttpError(Exception):
    def __init__(self, status: int, path: str) -> None:
        super().__init__(f"opencode answered {status} for {path}")
        self.status = status
        self.path = path


class _TunnelStream(httpcore.AsyncNetworkStream):
    def __init__(self, stream: ByteStream) -> None:
        self._stream = stream

    @override
    async def read(self, max_bytes: int, timeout: float | None = None) -> bytes:
        try:
            return await asyncio.wait_for(self._stream.read(max_bytes), timeout)
        except TimeoutError as exc:
            raise httpcore.ReadTimeout(str(exc)) from exc

    @override
    async def write(self, buffer: bytes, timeout: float | None = None) -> None:
        try:
            await asyncio.wait_for(self._stream.write(buffer), timeout)
        except TimeoutError as exc:
            raise httpcore.WriteTimeout(str(exc)) from exc

    @override
    async def aclose(self) -> None:
        await self._stream.aclose()


class SandboxNetworkBackend(httpcore.AsyncNetworkBackend):
    """Every TCP connection is a tunnel to ``port`` inside the sandbox; the host is ignored."""

    def __init__(self, sandbox: SandboxHandle, port: int) -> None:
        self._sandbox = sandbox
        self._port = port

    @override
    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[httpcore.SOCKET_OPTION] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        try:
            stream = await asyncio.wait_for(self._sandbox.connect(self._port), timeout)
        # SandboxConnectError is the port's "nothing listens yet"; the start-up probe retries it.
        except (SandboxConnectError, ConnectionError, TimeoutError) as exc:
            raise httpcore.ConnectError(str(exc)) from exc
        return _TunnelStream(stream)

    @override
    async def sleep(self, seconds: float) -> None:
        await asyncio.sleep(seconds)


class SseParser:
    """Incremental ``text/event-stream`` parser: bytes in, complete ``data`` payloads out."""

    def __init__(self) -> None:
        self._buffer = b""
        self._data: list[str] = []

    def feed(self, chunk: bytes) -> list[str]:
        self._buffer += chunk
        out: list[str] = []
        while True:
            newline = self._buffer.find(b"\n")
            if newline < 0:
                return out
            raw, self._buffer = self._buffer[:newline], self._buffer[newline + 1 :]
            line = raw.rstrip(b"\r").decode("utf-8")
            if not line:
                if self._data:
                    out.append("\n".join(self._data))
                    self._data = []
                continue
            if line.startswith(":"):
                continue
            name, _, value = line.partition(":")
            if name == "data":
                self._data.append(value[1:] if value.startswith(" ") else value)


class OpencodeHttp:
    def __init__(self, sandbox: SandboxHandle, port: int, password: str) -> None:
        self._pool = httpcore.AsyncConnectionPool(
            network_backend=SandboxNetworkBackend(sandbox, port), max_connections=8
        )
        token = base64.b64encode(f"{USERNAME}:{password}".encode()).decode()
        self._headers: list[tuple[bytes, bytes]] = [
            (b"authorization", f"Basic {token}".encode()),
            (b"host", f"127.0.0.1:{port}".encode()),
        ]
        self._base = f"http://127.0.0.1:{port}"

    def _timeouts(self, read: float | None) -> dict[str, float | None]:
        return {"connect": REQUEST_TIMEOUT_S, "read": read, "write": REQUEST_TIMEOUT_S, "pool": REQUEST_TIMEOUT_S}

    async def request(
        self, method: str, path: str, body: JsonValue | None = None, *, timeout_s: float = REQUEST_TIMEOUT_S
    ) -> JsonValue:
        headers = list(self._headers)
        content = b""
        if body is not None:
            content = json.dumps(body).encode()
            headers.append((b"content-type", b"application/json"))
        response = await self._pool.request(
            method,
            self._base + path,
            headers=headers,
            content=content,
            extensions={"timeout": {"connect": timeout_s, "read": timeout_s, "write": timeout_s, "pool": timeout_s}},
        )
        if response.status >= 400:
            raise OpencodeHttpError(response.status, path)
        if not response.content:
            return None
        value: JsonValue = json.loads(response.content)
        return value

    async def events(self) -> AsyncIterator[Mapping[str, JsonValue]]:
        """The instance's ``/event`` stream. It has no resume cursor (F4 §2.3)."""
        parser = SseParser()
        async with self._pool.stream(
            "GET",
            self._base + "/event",
            headers=[*self._headers, (b"accept", b"text/event-stream")],
            extensions={"timeout": self._timeouts(None)},
        ) as response:
            if response.status >= 400:
                raise OpencodeHttpError(response.status, "/event")
            async for chunk in response.aiter_stream():
                for data in parser.feed(chunk):
                    try:
                        value: JsonValue = json.loads(data)
                    except ValueError:
                        continue
                    if isinstance(value, dict):
                        yield as_object(value)

    async def aclose(self) -> None:
        await self._pool.aclose()


__all__ = ["OpencodeHttp", "OpencodeHttpError", "SandboxNetworkBackend", "SseParser"]
