"""Request-read bounds for the harness's HTTP listeners (SEC-DOS-04, C6.40): slowloris on headers and bodies.

Neither aiohttp nor uvicorn bounds how long a client may take to send a request: a peer that sends one header
byte a minute holds a connection (and a handler slot) for ever. ``ReadGuard`` wraps a listener's protocol and
closes a connection whose request head is not in within ``header_timeout_s`` of its first byte, or whose whole
request is not in within ``request_timeout_s`` plus the time its bytes take at ``min_body_bytes_per_s``. Only
the request is timed: once it is in, a response may stream for as long as it likes (SSE, model streams). The
server's own keep-alive timeout bounds an idle connection between requests; ``max_connections`` caps how many
a listener holds at once. A ``CONNECT`` or ``Upgrade`` request ends the guard's watch: what follows is a tunnel.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, cast, override

from harness_core.logs import HarnessLog

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from aiohttp import web

ENV_HEADER_TIMEOUT_S: Final = "HARNESS_HTTP_HEADER_TIMEOUT_S"
ENV_REQUEST_TIMEOUT_S: Final = "HARNESS_HTTP_REQUEST_TIMEOUT_S"
ENV_MIN_BODY_BYTES_PER_S: Final = "HARNESS_HTTP_MIN_BODY_BYTES_PER_S"
ENV_MAX_CONNECTIONS: Final = "HARNESS_HTTP_MAX_CONNECTIONS"
DEFAULT_HEADER_TIMEOUT_S: Final = 15.0
DEFAULT_REQUEST_TIMEOUT_S: Final = 60.0
# A request may take longer than ``request_timeout_s`` only by sending at least this fast: a 2 GiB archive to the
# broker at this rate earns about nine hours, a one-byte drip earns nothing.
DEFAULT_MIN_BODY_BYTES_PER_S: Final = 64 * 1024
DEFAULT_MAX_CONNECTIONS: Final = 4096
# Past this the head is not one any of the listeners accepts (they refuse it themselves); the guard stops parsing
# and only its deadline is left to close the connection.
_MAX_HEAD_BYTES: Final = 256 * 1024
_MAX_CHUNK_LINE: Final = 4096
_LOG = HarnessLog.named("harness_core.http_limits")


class InvalidReadLimit(ValueError):
    """A ``HARNESS_HTTP_*`` setting is not a positive number: the process refuses to start rather than guess."""


@dataclass(frozen=True, slots=True)
class ReadLimits:
    header_timeout_s: float = DEFAULT_HEADER_TIMEOUT_S
    request_timeout_s: float = DEFAULT_REQUEST_TIMEOUT_S
    min_body_bytes_per_s: float = DEFAULT_MIN_BODY_BYTES_PER_S
    max_connections: int = DEFAULT_MAX_CONNECTIONS

    def __post_init__(self) -> None:
        if min(self.header_timeout_s, self.request_timeout_s, self.min_body_bytes_per_s, self.max_connections) <= 0:
            raise ValueError("read limits must be positive")
        if self.header_timeout_s > self.request_timeout_s:
            raise ValueError("the header timeout cannot be longer than the request timeout")

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> ReadLimits:
        values = os.environ if env is None else env
        header = _env_float(ENV_HEADER_TIMEOUT_S, DEFAULT_HEADER_TIMEOUT_S, values)
        request = _env_float(ENV_REQUEST_TIMEOUT_S, DEFAULT_REQUEST_TIMEOUT_S, values)
        if header > request:
            raise InvalidReadLimit(f"{ENV_HEADER_TIMEOUT_S} cannot be longer than {ENV_REQUEST_TIMEOUT_S}")
        return cls(
            header_timeout_s=header,
            request_timeout_s=request,
            min_body_bytes_per_s=_env_float(ENV_MIN_BODY_BYTES_PER_S, DEFAULT_MIN_BODY_BYTES_PER_S, values),
            max_connections=_env_int(ENV_MAX_CONNECTIONS, DEFAULT_MAX_CONNECTIONS, values),
        )


def _env_float(name: str, default: float, env: Mapping[str, str]) -> float:
    raw = (env.get(name) or "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        value = 0.0
    if not 0 < value < float("inf"):
        raise InvalidReadLimit(f"{name} must be a positive number, got {raw!r}")
    return value


def _env_int(name: str, default: int, env: Mapping[str, str]) -> int:
    value = _env_float(name, default, env)
    if value != int(value):
        raise InvalidReadLimit(f"{name} must be a positive whole number, got {value!r}")
    return int(value)


class RequestFraming:
    """Where an HTTP/1.1 request stream is: in a head, in a body, between requests, or past the guard's watch."""

    HEAD, BODY, CHUNK_SIZE, CHUNK_DATA, CHUNK_END, TRAILERS, IDLE, PASSIVE, BROKEN = range(9)

    def __init__(self) -> None:
        self.state = self.IDLE
        self._buffer = b""
        self._remaining = 0

    def feed(self, data: bytes) -> list[str]:
        """Advances over ``data``; answers the events it crossed (``start``, ``head``, ``done``, ``passive``)."""
        events: list[str] = []
        while data and self.state not in (self.PASSIVE, self.BROKEN):
            if self.state == self.IDLE:
                self.state = self.HEAD
                events.append("start")
            if self.state == self.HEAD:
                data = self._head(data, events)
            elif self.state in (self.BODY, self.CHUNK_DATA):
                taken = min(self._remaining, len(data))
                self._remaining -= taken
                data = data[taken:]
                if self._remaining == 0:
                    if self.state == self.BODY:
                        self._done(events)
                    else:
                        self.state, self._remaining = self.CHUNK_END, 2
            elif self.state == self.CHUNK_END:
                taken = min(self._remaining, len(data))
                self._remaining -= taken
                data = data[taken:]
                if self._remaining == 0:
                    self.state = self.CHUNK_SIZE
            else:
                data = self._line_state(data, events)
        return events

    def _done(self, events: list[str]) -> None:
        self.state, self._buffer = self.IDLE, b""
        events.append("done")

    def _head(self, data: bytes, events: list[str]) -> bytes:
        self._buffer += data
        # Clients may send blank lines between requests (RFC 9112 2.2).
        self._buffer = self._buffer.lstrip(b"\r\n")
        end = self._buffer.find(b"\r\n\r\n")
        if end < 0:
            if len(self._buffer) > _MAX_HEAD_BYTES:
                self.state = self.BROKEN
            return b""
        head, rest = self._buffer[:end], self._buffer[end + 4 :]
        self._buffer = b""
        events.append("head")
        lines = head.split(b"\r\n")
        method = lines[0].split(b" ", 1)[0].upper()
        headers: dict[bytes, bytes] = {}
        for line in lines[1:]:
            name, _, value = line.partition(b":")
            headers[name.strip().lower()] = value.strip().lower()
        if method == b"CONNECT" or b"upgrade" in headers or b"upgrade" in headers.get(b"connection", b""):
            self.state = self.PASSIVE
            events.append("passive")
            return b""
        if headers.get(b"transfer-encoding", b"").rsplit(b",", 1)[-1].strip() == b"chunked":
            self.state = self.CHUNK_SIZE
            return rest
        try:
            length = int(headers.get(b"content-length", b"0") or b"0")
        except ValueError:
            self.state = self.BROKEN
            return b""
        if length > 0:
            self.state, self._remaining = self.BODY, length
        else:
            self._done(events)
        return rest

    def _line_state(self, data: bytes, events: list[str]) -> bytes:
        self._buffer += data
        if self.state == self.TRAILERS:
            if self._buffer.startswith(b"\r\n"):
                rest = self._buffer[2:]
                self._done(events)
                return rest
            end = self._buffer.find(b"\r\n\r\n")
            if end < 0:
                if len(self._buffer) > _MAX_HEAD_BYTES:
                    self.state = self.BROKEN
                return b""
            rest = self._buffer[end + 4 :]
            self._done(events)
            return rest
        end = self._buffer.find(b"\r\n")
        if end < 0:
            if len(self._buffer) > _MAX_CHUNK_LINE:
                self.state = self.BROKEN
            return b""
        line, rest = self._buffer[:end], self._buffer[end + 2 :]
        self._buffer = b""
        try:
            size = int(line.split(b";", 1)[0].strip(), 16)
        except ValueError:
            self.state = self.BROKEN
            return b""
        if size == 0:
            self.state = self.TRAILERS
        else:
            self.state, self._remaining = self.CHUNK_DATA, size
        return rest


class Connections:
    """How many connections one listener holds, shared by every ``ReadGuard`` it makes."""

    def __init__(self, limit: int) -> None:
        self.limit = limit
        self.open = 0


class ReadGuard(asyncio.Protocol):
    """``inner`` with its requests' reads bounded by ``limits``; every protocol callback is forwarded to it."""

    def __init__(
        self,
        inner: asyncio.Protocol,
        limits: ReadLimits,
        *,
        listener: str,
        connections: Connections | None = None,
        started_at: float | None = None,
    ) -> None:
        self.inner = inner
        self._limits = limits
        self._listener = listener
        self._connections = connections
        self._framing = RequestFraming()
        self._transport: asyncio.Transport | None = None
        self._timer: asyncio.TimerHandle | None = None
        self._started_at = started_at
        self._received = 0
        self._counted = False
        self._refused = False

    @override
    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self._transport = cast("asyncio.Transport", transport)
        if self._connections is not None:
            if self._connections.open >= self._connections.limit:
                self._refused = True
                _LOG.warning("http.connection_refused", listener=self._listener, limit=self._connections.limit)
                self._transport.abort()
                return
            self._connections.open += 1
            self._counted = True
        # A connection that sends nothing is a request that has not started: its head is due all the same.
        self._begin(self._started_at)
        self.inner.connection_made(transport)

    @override
    def data_received(self, data: bytes) -> None:
        if self._refused:
            return
        for event in self._framing.feed(data):
            if event == "start" and self._timer is None:
                self._begin(None)
            elif event == "done":
                self._stop()
            elif event == "passive":
                self._stop()
                # A tunnel may be handed to a TLS protocol that this guard never hears close: it is the tunnel
                # handler's to bound, and its slot is given back now rather than leaked.
                self._release()
        if self._timer is not None:
            self._received += len(data)
        self.inner.data_received(data)

    @override
    def eof_received(self) -> bool | None:
        if self._refused:
            return None
        return self.inner.eof_received()

    @override
    def connection_lost(self, exc: Exception | None) -> None:
        self._stop()
        self._release()
        if not self._refused:
            self.inner.connection_lost(exc)

    @override
    def pause_writing(self) -> None:
        self.inner.pause_writing()

    @override
    def resume_writing(self) -> None:
        self.inner.resume_writing()

    def _release(self) -> None:
        if self._counted and self._connections is not None:
            self._connections.open -= 1
            self._counted = False

    def _begin(self, started_at: float | None) -> None:
        loop = asyncio.get_running_loop()
        self._started_at = loop.time() if started_at is None else started_at
        self._received = 0
        self._schedule(loop, self._deadline())

    def _deadline(self) -> float:
        assert self._started_at is not None
        if self._framing.state in (RequestFraming.IDLE, RequestFraming.HEAD):
            return self._started_at + self._limits.header_timeout_s
        return (
            self._started_at + self._limits.request_timeout_s + self._received / self._limits.min_body_bytes_per_s
        )

    def _schedule(self, loop: asyncio.AbstractEventLoop, when: float) -> None:
        if self._timer is not None:
            self._timer.cancel()
        self._timer = loop.call_at(when, self._expire)

    def _stop(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
        self._started_at = None

    def _expire(self) -> None:
        self._timer = None
        transport = self._transport
        if transport is None or transport.is_closing() or transport.get_protocol() is not self:
            return
        loop = asyncio.get_running_loop()
        deadline = self._deadline()
        if loop.time() < deadline:
            self._schedule(loop, deadline)
            return
        head = self._framing.state in (RequestFraming.IDLE, RequestFraming.HEAD)
        _LOG.warning("http.read_timeout", listener=self._listener, phase="head" if head else "body")
        transport.abort()


def guarded(
    factory: Callable[[], asyncio.Protocol], limits: ReadLimits, *, listener: str
) -> Callable[[], asyncio.Protocol]:
    """A protocol factory for ``loop.create_server`` whose connections ``ReadGuard`` bounds."""
    connections = Connections(limits.max_connections)

    def make() -> asyncio.Protocol:
        return ReadGuard(factory(), limits, listener=listener, connections=connections)

    return make


async def start_guarded_site(
    runner: web.BaseRunner, host: str | None, port: int, *, limits: ReadLimits, listener: str
) -> int:
    """``web.TCPSite(runner, host, port).start()`` with the requests' reads bounded; answers the bound port."""
    site = _guarded_site_class()(runner, host, port, limits=limits, listener=listener)
    await site.start()
    return site.port


_SITE_CLASS: list[Callable[..., web.TCPSite]] = []


def _guarded_site_class() -> Callable[..., web.TCPSite]:
    # aiohttp is an optional dependency of harness_core (``broker-http``): imported only by a host that serves.
    if _SITE_CLASS:
        return _SITE_CLASS[0]
    from aiohttp import web

    class GuardedTCPSite(web.TCPSite):
        __slots__ = ("_limits", "_listener")

        def __init__(
            self, runner: web.BaseRunner, host: str | None, port: int, *, limits: ReadLimits, listener: str
        ) -> None:
            super().__init__(runner, host, port)
            self._limits = limits
            self._listener = listener

        @override
        async def start(self) -> None:
            await web.BaseSite.start(self)
            server = self._runner.server
            assert server is not None
            loop = asyncio.get_running_loop()
            self._server = await loop.create_server(
                guarded(server, self._limits, listener=self._listener),
                self._host,
                self._port,
                backlog=self._backlog,
                reuse_address=self._reuse_address,
                reuse_port=self._reuse_port,
            )
            sockets = self._server.sockets
            self._bound_port = sockets[0].getsockname()[1] if sockets else self._port

    _SITE_CLASS.append(GuardedTCPSite)
    return GuardedTCPSite


def guarded_uvicorn_protocol(limits: ReadLimits, *, listener: str) -> Callable[..., asyncio.Protocol]:
    """For ``uvicorn.Config(http=...)``: uvicorn's own HTTP protocol, wrapped in ``ReadGuard``."""
    from uvicorn.config import HTTP_PROTOCOLS
    from uvicorn.importer import import_from_string

    inner_class = import_from_string(HTTP_PROTOCOLS["auto"])
    connections = Connections(limits.max_connections)

    def make(**kwargs: object) -> asyncio.Protocol:
        return ReadGuard(inner_class(**kwargs), limits, listener=listener, connections=connections)

    return make


__all__ = [
    "DEFAULT_HEADER_TIMEOUT_S",
    "DEFAULT_MAX_CONNECTIONS",
    "DEFAULT_MIN_BODY_BYTES_PER_S",
    "DEFAULT_REQUEST_TIMEOUT_S",
    "ENV_HEADER_TIMEOUT_S",
    "ENV_MAX_CONNECTIONS",
    "ENV_MIN_BODY_BYTES_PER_S",
    "ENV_REQUEST_TIMEOUT_S",
    "Connections",
    "InvalidReadLimit",
    "ReadGuard",
    "ReadLimits",
    "RequestFraming",
    "guarded",
    "guarded_uvicorn_protocol",
    "start_guarded_site",
]
