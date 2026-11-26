"""``CONNECT`` on the data plane: opaque tunnels (P5.4b), intercepted ones (WP-06), and the listener that tells
them from plain HTTP.

aiohttp does not hand over the bytes after a ``CONNECT`` (3.14 reparses them as
a new request), so with tunnels or interception on, the data-plane listener reads a
connection's first bytes itself: ``CONNECT`` goes to ``TunnelHandler``; anything else
is given, bytes included, to the aiohttp handler P5.4 serves. An opaque tunnel is
never terminated here: the proxy sees the ``CONNECT`` line and headers, then copies
bytes, and dials only what ``EgressProxy.authorize_tunnel`` resolved and checked. A
``CONNECT`` to a host the sandbox holds a credential route for is intercepted instead:
the proxy ends the TLS with a certificate of its own authority and serves each
request inside as an authorized forward, so the credential goes on where the sandbox
never sees it (OWD-1).
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import ssl
import time
from typing import TYPE_CHECKING, override

from harness_core.logs import HarnessLog
from harness_core.sandbox.egress.errors import EgressRefusalReason as R
from harness_core.sandbox.egress.errors import EgressRefused
from harness_core.sandbox.egress.policy import REFUSAL_HEADER
from harness_core.sandbox.egress.sites import MAX_RECORD, client_hello_sni
from harness_core.sandbox.egress.types import ProxyRequest
from harness_core.sandbox.egress.upstream_proxy import connect_through

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from aiohttp import web

    from harness_core.sandbox.egress.proxy import EgressProxy
    from harness_core.sandbox.egress.server import InterceptedHttpServer
    from harness_core.sandbox.egress.types import Tunnel
    from harness_core.sandbox.egress.volume import VolumeMeter

    Connector = Callable[[str, int], Awaitable[tuple[asyncio.StreamReader, asyncio.StreamWriter]]]
    # The HTTP server for one intercepted connection to (host, port), and the TLS it ends with for that host.
    Interceptor = Callable[[str, int], InterceptedHttpServer]
    ServerContext = Callable[[str], ssl.SSLContext]

CONNECT_TIMEOUT_S = 10.0
HANDSHAKE_TIMEOUT_S = 15.0
HEAD_TIMEOUT_S = 30.0
# How long a host-level-only site's client has to send its ClientHello (WP-63).
HELLO_TIMEOUT_S = 15.0
# An idle tunnel (no byte either way) is closed after this, like a silent upstream on the HTTP path.
IDLE_TIMEOUT_S = 300.0
MAX_HEAD_BYTES = 16 * 1024
_CHUNK = 64 * 1024
_CONNECT = b"CONNECT "
_LOG = HarnessLog.named("harness_core.sandbox.egress.tunnel")
_REASONS = {
    400: "Bad Request",
    403: "Forbidden",
    421: "Misdirected Request",
    502: "Bad Gateway",
    503: "Service Unavailable",
}


async def open_tcp(address: str, port: int) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    return await asyncio.open_connection(address, port)


def refusal_bytes(refused: EgressRefused) -> bytes:
    body = json.dumps(
        {"error": {"code": "egress_refused", "reason": refused.reason.value, "requestId": refused.request_id}}
    ).encode()
    head = (
        f"HTTP/1.1 {refused.status} {_REASONS.get(refused.status, 'Refused')}\r\n"
        f"Content-Type: application/json\r\n{REFUSAL_HEADER}: {refused.reason.value}\r\n"
        f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n"
    )
    return head.encode("ascii") + body


def parse_head(head: bytes) -> ProxyRequest:
    lines = head.decode("latin-1").split("\r\n")
    parts = lines[0].split(" ")
    if len(parts) != 3 or not parts[2].startswith("HTTP/1."):
        raise EgressRefused(R.MALFORMED_REQUEST, "malformed request line")
    headers: list[tuple[str, str]] = []
    for line in lines[1:]:
        if not line:
            continue
        name, sep, value = line.partition(":")
        if not sep or not name or name != name.strip():
            raise EgressRefused(R.MALFORMED_REQUEST, "malformed header")
        headers.append((name, value.strip()))
    return ProxyRequest(method=parts[0], target=parts[1], headers=tuple(headers))


class TunnelHandler:
    """Serves one ``CONNECT`` connection; tracks open tunnels per sandbox so an unbind closes them."""

    def __init__(
        self,
        proxy: EgressProxy,
        *,
        connector: Connector = open_tcp,
        connect_timeout_s: float = CONNECT_TIMEOUT_S,
        idle_timeout_s: float = IDLE_TIMEOUT_S,
        interceptor: Interceptor | None = None,
        server_context: ServerContext | None = None,
    ) -> None:
        self._proxy = proxy
        self._connector = connector
        self._interceptor = interceptor
        self._server_context = server_context
        self._connect_timeout_s = connect_timeout_s
        self._idle_timeout_s = idle_timeout_s
        self._open: dict[str, set[asyncio.Task[None]]] = {}
        # The host-level (uninspected) tunnels among them: a flow hold cuts these, which no request check reaches.
        self._opaque: dict[str, set[asyncio.Task[None]]] = {}
        self._tasks: set[asyncio.Task[None]] = set()
        self._handed_off: set[asyncio.StreamWriter] = set()
        proxy.bindings.on_unregister(self.close_sandbox)
        proxy.on_flow_held(self.close_opaque)

    def start(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.get_running_loop().create_task(self.serve(reader, writer))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def close_sandbox(self, sandbox_id: str) -> None:
        _ = self._opaque.pop(sandbox_id, None)
        for task in self._open.pop(sandbox_id, set()):
            _ = task.cancel()

    def close_opaque(self, sandbox_id: str) -> None:
        for task in self._opaque.pop(sandbox_id, set()):
            _ = task.cancel()

    async def aclose(self) -> None:
        for task in list(self._tasks):
            _ = task.cancel()
        _ = await asyncio.gather(*self._tasks, return_exceptions=True)

    async def serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        local = writer.get_extra_info("sockname")
        peer = writer.get_extra_info("peername")
        local_ip, peer_ip = (str(local[0]) if local else ""), (str(peer[0]) if peer else "")
        try:
            await self._serve(reader, writer, local_ip, peer_ip)
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            writer.close()
            # After an interception the socket belongs to the TLS layer, so this writer never hears it close.
            if writer not in self._handed_off:
                with contextlib.suppress(Exception):
                    await writer.wait_closed()
            self._handed_off.discard(writer)

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, local: str, peer: str) -> None:
        request_id = self._proxy.new_request_id()
        try:
            head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), HEAD_TIMEOUT_S)
        except (asyncio.LimitOverrunError, asyncio.IncompleteReadError, TimeoutError, ValueError):
            return
        try:
            request = parse_head(head)
        except EgressRefused as refused:
            binding = self._proxy.bindings.for_local_address(local)
            self._proxy.record_refusal(refused, request_id=request_id, binding=binding, peer=peer)
            writer.write(refusal_bytes(refused))
            await writer.drain()
            return
        try:
            tunnel = await self._proxy.authorize_tunnel(request, local=local, peer=peer, request_id=request_id)
        except EgressRefused as refused:
            writer.write(refusal_bytes(refused))
            await writer.drain()
            return
        current = asyncio.current_task()
        if current is not None:
            self._open.setdefault(tunnel.sandbox_id, set()).add(current)
            if not tunnel.intercept:
                self._opaque.setdefault(tunnel.sandbox_id, set()).add(current)
        try:
            if tunnel.intercept:
                await self._intercept(tunnel, reader, writer)
            else:
                await self._tunnel(tunnel, reader, writer)
        finally:
            if current is not None:
                self._open.get(tunnel.sandbox_id, set()).discard(current)
                self._opaque.get(tunnel.sandbox_id, set()).discard(current)

    async def _intercept(self, tunnel: Tunnel, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        """End the sandbox's TLS here and serve each request inside through the proxy's own checks."""
        if self._interceptor is None or self._server_context is None:
            refused = EgressRefused(R.TUNNEL_REFUSED, "this proxy does not end TLS")
            self._proxy.record_refusal(refused, request_id=tunnel.request_id, binding=self._proxy.bindings.get(tunnel.sandbox_id))
            writer.write(refusal_bytes(refused))
            await writer.drain()
            return
        fields = {
            **{k: v for k, v in tunnel.labels.items() if k not in ("event", "exc")},
            "sandboxId": tunnel.sandbox_id,
            "proxyRequestId": tunnel.request_id,
            "host": tunnel.host,
            "port": tunnel.port,
        }
        writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
        await writer.drain()
        # A TLS client says nothing until the CONNECT is answered; bytes already here would be lost to the handshake.
        if reader.at_eof() or getattr(reader, "_buffer", b""):
            return
        server = self._interceptor(tunnel.host, tunnel.port)
        protocol = server()
        loop = asyncio.get_running_loop()
        self._handed_off.add(writer)
        try:
            secured = await loop.start_tls(
                writer.transport,
                protocol,
                self._server_context(tunnel.host),
                server_side=True,
                ssl_handshake_timeout=HANDSHAKE_TIMEOUT_S,
            )
        except (OSError, ConnectionError, TimeoutError, ssl.SSLError) as exc:
            _LOG.info("egress.intercept_handshake_failed", **fields, errorType=type(exc).__name__)
            return
        if secured is None:
            return
        protocol.connection_made(secured)
        _LOG.info("egress.intercept_opened", **fields)
        try:
            await server.closed()
        finally:
            await server.shutdown(5.0)
            secured.close()
            _LOG.info("egress.intercept_closed", **fields)

    async def _dial(self, tunnel: Tunnel) -> tuple[asyncio.StreamReader, asyncio.StreamWriter, str]:
        last: Exception | None = None
        chained = self._proxy.upstream_egress.upstream if tunnel.via_upstream_proxy else None
        for address, port in tunnel.connect_to:
            try:
                if chained is not None:
                    # G-21: the deployment's proxy is asked for the checked address, never the name.
                    upstream = await asyncio.wait_for(
                        connect_through(chained, address, port, dial=self._connector, timeout_s=self._connect_timeout_s),
                        self._connect_timeout_s * 2,
                    )
                else:
                    upstream = await asyncio.wait_for(self._connector(address, port), self._connect_timeout_s)
            except (OSError, TimeoutError) as exc:
                last = exc
                continue
            return upstream[0], upstream[1], address
        raise EgressRefused(R.UPSTREAM_UNAVAILABLE, f"upstream failed: {type(last).__name__}")

    async def _tunnel(self, tunnel: Tunnel, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            up_reader, up_writer, address = await self._dial(tunnel)
        except EgressRefused as refused:
            binding = self._proxy.bindings.get(tunnel.sandbox_id)
            self._proxy.record_refusal(refused, request_id=tunnel.request_id, binding=binding)
            writer.write(refusal_bytes(refused))
            await writer.drain()
            return
        fields = {
            **{k: v for k, v in tunnel.labels.items() if k not in ("event", "exc")},
            "sandboxId": tunnel.sandbox_id,
            "proxyRequestId": tunnel.request_id,
            "host": tunnel.host,
            "port": tunnel.port,
            "address": address,
            "viaUpstreamProxy": tunnel.via_upstream_proxy,
        }
        _LOG.info("egress.tunnel_opened", **fields)
        started = time.monotonic()
        counts = [0, 0]
        try:
            writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            await writer.drain()
            if tunnel.require_sni and not await self._hello_names_host(tunnel, reader, up_writer, counts):
                return
            await self._copy_both(reader, writer, up_reader, up_writer, counts, tunnel.meter)
        finally:
            up_writer.close()
            with contextlib.suppress(Exception):
                await up_writer.wait_closed()
            capped = tunnel.meter is not None and tunnel.meter.stopped
            _LOG.info(
                "egress.tunnel_closed",
                **fields,
                bytesUp=counts[0],
                bytesDown=counts[1],
                seconds=round(time.monotonic() - started, 3),
                reason=R.VOLUME_CAP_EXCEEDED.value if capped else None,
            )
        if capped:
            refused = EgressRefused(R.VOLUME_CAP_EXCEEDED, "the run moved more than the deployment's egress cap")
            binding = self._proxy.bindings.get(tunnel.sandbox_id)
            self._proxy.record_refusal(refused, request_id=tunnel.request_id, binding=binding)

    async def _hello_names_host(
        self,
        tunnel: Tunnel,
        reader: asyncio.StreamReader,
        up_writer: asyncio.StreamWriter,
        counts: list[int],
    ) -> bool:
        """WP-63: a host-level-only site's tunnel carries TLS to the name it was opened for: the ClientHello's
        server name must be the ``CONNECT`` host, or the tunnel is closed before a byte reaches the upstream."""
        try:
            head = await asyncio.wait_for(reader.read(5), HELLO_TIMEOUT_S)
            size = int.from_bytes(head[3:5], "big") if len(head) == 5 else 0
            body = await asyncio.wait_for(reader.readexactly(min(size, MAX_RECORD - 5)), HELLO_TIMEOUT_S) if size else b""
        except (TimeoutError, asyncio.IncompleteReadError, ConnectionError):
            head, body = b"", b""
        hello = head + body
        if client_hello_sni(hello) != tunnel.host:
            refused = EgressRefused(R.HOST_MISMATCH, "the TLS name is not the tunnel's host", host=tunnel.host)
            self._proxy.record_refusal(refused, request_id=tunnel.request_id, binding=self._proxy.bindings.get(tunnel.sandbox_id))
            return False
        if tunnel.meter is not None and not tunnel.meter.sent(len(hello)):
            return False
        counts[0] += len(hello)
        up_writer.write(hello)
        await up_writer.drain()
        return True

    async def _copy_both(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        up_reader: asyncio.StreamReader,
        up_writer: asyncio.StreamWriter,
        counts: list[int],
        meter: VolumeMeter | None = None,
    ) -> None:
        await copy_both(reader, writer, up_reader, up_writer, counts, idle_timeout_s=self._idle_timeout_s, meter=meter)


async def copy_both(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    up_reader: asyncio.StreamReader,
    up_writer: asyncio.StreamWriter,
    counts: list[int],
    *,
    idle_timeout_s: float = IDLE_TIMEOUT_S,
    meter: VolumeMeter | None = None,
) -> None:
    """Copy bytes both ways until either side ends, neither moves for ``idle_timeout_s``, or ``meter`` says the
    run is past its cap (C6.12: both ways stop, and the chunk that crossed it is not passed on); ``counts`` is
    (bytes up, bytes down)."""
    active = [time.monotonic()]

    async def pump(source: asyncio.StreamReader, sink: asyncio.StreamWriter, index: int) -> None:
        while data := await source.read(_CHUNK):
            active[0] = time.monotonic()
            counts[index] += len(data)
            if meter is not None and not meter.add(index, len(data)):
                raise _VolumeCapped
            sink.write(data)
            await sink.drain()
        with contextlib.suppress(OSError, RuntimeError):
            if sink.can_write_eof():
                sink.write_eof()

    async def idle_watch() -> None:
        while True:
            remaining = active[0] + idle_timeout_s - time.monotonic()
            if remaining <= 0:
                return
            await asyncio.sleep(min(remaining, 5.0))

    pumps = {
        asyncio.ensure_future(pump(reader, up_writer, 0)),
        asyncio.ensure_future(pump(up_reader, writer, 1)),
    }
    watch = asyncio.ensure_future(idle_watch())
    try:
        pending = set(pumps)
        while pending:
            done, pending = await asyncio.wait({*pending, watch}, return_when=asyncio.FIRST_COMPLETED)
            if watch in done:
                break
            pending.discard(watch)
            if any(task.exception() is not None for task in done if not task.cancelled()):
                break
    finally:
        for task in (*pumps, watch):
            _ = task.cancel()
        _ = await asyncio.gather(*pumps, watch, return_exceptions=True)


class _VolumeCapped(Exception):
    """A pump stopped at the run's cap; ends both directions of the copy."""


class DataPlaneFront(asyncio.Protocol):
    """The data-plane listener with tunnels on: the first bytes decide ``CONNECT`` or HTTP for the connection.

    The chosen protocol is fed through this one rather than set on the transport, so whatever wraps this
    protocol (the listener's ``ReadGuard``) keeps seeing the connection's bytes and its end."""

    def __init__(self, http: web.Server, tunnels: TunnelHandler) -> None:
        self._http = http
        self._tunnels = tunnels
        self._transport: asyncio.Transport | None = None
        self._buffer = b""
        self._inner: asyncio.Protocol | None = None

    @override
    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self._transport = transport  # pyright: ignore[reportAttributeAccessIssue]

    @override
    def data_received(self, data: bytes) -> None:
        if self._inner is not None:
            self._inner.data_received(data)
            return
        self._buffer += data
        if len(self._buffer) < len(_CONNECT) and b"\n" not in self._buffer:
            return
        if self._buffer.startswith(_CONNECT):
            self._to_tunnel()
        else:
            self._to_http()

    @override
    def eof_received(self) -> bool | None:
        return self._inner.eof_received() if self._inner is not None else None

    @override
    def connection_lost(self, exc: Exception | None) -> None:
        if self._inner is not None:
            self._inner.connection_lost(exc)

    @override
    def pause_writing(self) -> None:
        if self._inner is not None:
            self._inner.pause_writing()

    @override
    def resume_writing(self) -> None:
        if self._inner is not None:
            self._inner.resume_writing()

    def _to_http(self) -> None:
        transport, buffered = self._transport, self._buffer
        assert transport is not None
        protocol = self._http()
        self._inner, self._buffer = protocol, b""
        protocol.connection_made(transport)
        protocol.data_received(buffered)

    def _to_tunnel(self) -> None:
        transport, buffered = self._transport, self._buffer
        assert transport is not None
        loop = asyncio.get_running_loop()
        reader = asyncio.StreamReader(limit=MAX_HEAD_BYTES, loop=loop)
        protocol = asyncio.StreamReaderProtocol(reader, loop=loop)
        self._inner, self._buffer = protocol, b""
        protocol.connection_made(transport)
        reader.feed_data(buffered)
        self._tunnels.start(reader, asyncio.StreamWriter(transport, protocol, reader, loop))


__all__ = [
    "CONNECT_TIMEOUT_S",
    "IDLE_TIMEOUT_S",
    "DataPlaneFront",
    "TunnelHandler",
    "copy_both",
    "open_tcp",
    "parse_head",
    "refusal_bytes",
]
