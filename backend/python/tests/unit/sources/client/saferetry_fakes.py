"""SAFE-RETRY support: loopback servers that lose an acknowledgement, and a durable run around one write.

Each fake reads a request whole before it decides, so a dropped answer is a write that landed: the server has it,
the client never hears back. ``received`` is what reached the server, in order.
"""

from __future__ import annotations

import importlib
import json
import re
import socket
import socketserver
import struct
import sys
import threading
from contextlib import contextmanager
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING, Literal, override

import pytest

from harness_core.kernel.durable import DurableKernel, classify_tool_effect
from harness_core.tools.base import Tag, ToolOutput
from harness_core.tools.durable_port import RegistryToolPort
from harness_core.tools.registry import ToolRegistry
from tests.unit.harness_core.durable.support import World, reply, scripted
from tests.unit.harness_core.tools.test_executor_unknown import ScriptedTool

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterator
    from types import ModuleType

DROP = "drop"
# Answer, then close the kept-alive connection without saying so: a server's idle timeout.
CLOSE_AFTER = "close_after"
Step = int | Literal["drop", "close_after"]


@dataclass
class FakeHTTP:
    """Answers each request by the next step of ``plan`` (a status, ``DROP`` or ``CLOSE_AFTER``), then 200."""

    plan: list[Step] = field(default_factory=list[Step])
    body: dict[str, object] = field(default_factory=lambda: {"id": "1"})
    received: list[tuple[str, str]] = field(default_factory=list[tuple[str, str]])
    idle_closed: threading.Event = field(default_factory=threading.Event)
    address: str = ""

    def writes(self) -> list[tuple[str, str]]:
        return [(method, path) for method, path in self.received if method not in ("GET", "HEAD")]

    def handler(self) -> type[BaseHTTPRequestHandler]:
        fake = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def _serve(self) -> None:
                _ = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                fake.received.append((self.command, self.path.split("?", 1)[0]))
                step: Step = fake.plan.pop(0) if fake.plan else 200
                if step == DROP:
                    self.close_connection = True
                    return
                status = 200 if step == CLOSE_AFTER else step
                payload = json.dumps(fake.body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                _ = self.wfile.write(payload)
                self.wfile.flush()
                if step == CLOSE_AFTER:
                    self.close_connection = True
                    self.connection.shutdown(socket.SHUT_RDWR)
                    fake.idle_closed.set()

            do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = _serve

            @override
            def log_message(self, format: str, *args: object) -> None:
                return

        return Handler


@contextmanager
def serve(handler: type[socketserver.BaseRequestHandler], *, http: bool = True) -> Iterator[str]:
    """Runs a loopback server; yields ``host:port``."""
    server = (ThreadingHTTPServer if http else socketserver.ThreadingTCPServer)(("127.0.0.1", 0), handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


def closed_port() -> int:
    """A loopback port nothing listens on: a connect is refused before anything is sent."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


class _Wire(socketserver.BaseRequestHandler):
    def recv_exactly(self, size: int) -> bytes | None:
        data = b""
        while len(data) < size:
            chunk = self.request.recv(size - len(data))
            if not chunk:
                return None
            data += chunk
        return data


@dataclass
class FakeSQL:
    """A database that records every statement it reads and loses the answer to the first one that starts with
    ``drop_on`` (case-insensitive). ``received`` excludes the driver's own session statements."""

    drop_on: str | None = None
    received: list[str] = field(default_factory=list[str])

    def take(self, statement: str) -> bool:
        """Records the statement; whether to lose its answer."""
        text = statement.strip()
        if not text.lower().startswith(("set ", "begin")):
            self.received.append(text)
        if self.drop_on is not None and text.lower().startswith(self.drop_on.lower()):
            self.drop_on = None
            return True
        return False


def _mysql_packet(seq: int, payload: bytes) -> bytes:
    return struct.pack("<I", len(payload))[:3] + bytes([seq & 0xFF]) + payload


def mariadb_handler(fake: FakeSQL) -> type[socketserver.BaseRequestHandler]:
    """The MariaDB wire protocol, as far as Connector/Python's pool needs it: handshake, OK to every command, a
    transaction status the driver commits by."""
    capabilities = 0x1 | 0x8 | 0x200 | 0x2000 | 0x8000 | 0x10000 | 0x20000 | 0x80000 | 0x200000
    salt = b"saferetry-salt-0123x"
    greeting = (
        b"\x0a10.11.0-MariaDB\x00"
        + struct.pack("<I", 1)
        + salt[:8]
        + b"\x00"
        + struct.pack("<H", capabilities & 0xFFFF)
        + b"\x21"
        + struct.pack("<H", 2)
        + struct.pack("<H", capabilities >> 16)
        + bytes([21])
        + b"\x00" * 10
        + salt[8:]
        + b"\x00mysql_native_password\x00"
    )

    class Handler(_Wire):
        autocommit = True
        in_transaction = False

        def ok(self, seq: int) -> bytes:
            status = (0x2 if self.autocommit else 0) | (0x1 if self.in_transaction else 0)
            return _mysql_packet(seq, b"\x00\x01\x00" + struct.pack("<HH", status, 0))

        def read_packet(self) -> tuple[int, bytes] | None:
            header = self.recv_exactly(4)
            if header is None:
                return None
            body = self.recv_exactly(header[0] | header[1] << 8 | header[2] << 16)
            return None if body is None else (header[3], body)

        @override
        def handle(self) -> None:
            self.request.sendall(_mysql_packet(0, greeting))
            login = self.read_packet()
            if login is None:
                return
            self.request.sendall(self.ok(login[0] + 1))
            while (packet := self.read_packet()) is not None:
                seq, body = packet
                if body[0] == 0x01:
                    return
                if body[0] == 0x03:
                    statement = body[1:].decode()
                    if fake.take(statement):
                        return
                    lowered = statement.strip().lower()
                    if setting := re.search(r"\bautocommit\s*=\s*([01])", lowered):
                        self.autocommit = setting.group(1) == "1"
                    elif lowered in ("commit", "rollback"):
                        self.in_transaction = False
                    elif not self.autocommit:
                        self.in_transaction = True
                self.request.sendall(self.ok(seq + 1))

    return Handler


def black_hole_handler(accepted: threading.Event) -> type[socketserver.BaseRequestHandler]:
    """Accepts a connection and never answers: a server that is up at the TCP level and hung above it."""

    class Handler(_Wire):
        @override
        def handle(self) -> None:
            accepted.set()
            _ = self.request.recv(1)

    return Handler


def _pg_message(kind: bytes, payload: bytes = b"") -> bytes:
    return kind + struct.pack("!I", len(payload) + 4) + payload


def redshift_handler(fake: FakeSQL) -> type[socketserver.BaseRequestHandler]:
    """The PostgreSQL wire protocol as redshift_connector speaks it: trust auth, then the extended protocol; a
    statement takes effect at its Execute."""

    class Handler(_Wire):
        @override
        def handle(self) -> None:
            size = self.recv_exactly(4)
            if size is None or self.recv_exactly(struct.unpack("!I", size)[0] - 4) is None:
                return
            hello = _pg_message(b"R", struct.pack("!I", 0))
            for key, value in (("server_version", "8.0.2"), ("client_encoding", "UTF8"), ("integer_datetimes", "on")):
                hello += _pg_message(b"S", f"{key}\x00{value}\x00".encode())
            self.request.sendall(hello + _pg_message(b"K", struct.pack("!II", 1, 2)) + _pg_message(b"Z", b"I"))
            statements: dict[bytes, str] = {}
            portal = ""
            while (header := self.recv_exactly(5)) is not None:
                kind = header[:1]
                body = self.recv_exactly(struct.unpack("!I", header[1:])[0] - 4)
                if body is None or kind == b"X":
                    return
                if kind == b"P":
                    name, rest = body.split(b"\x00", 1)
                    statements[name] = rest.split(b"\x00", 1)[0].decode()
                    self.request.sendall(_pg_message(b"1"))
                elif kind == b"B":
                    portal = statements.get(body.split(b"\x00")[1], "")
                    self.request.sendall(_pg_message(b"2"))
                elif kind == b"D":
                    self.request.sendall(_pg_message(b"t", struct.pack("!H", 0)) + _pg_message(b"n"))
                elif kind == b"E":
                    if fake.take(portal):
                        return
                    self.request.sendall(_pg_message(b"C", b"INSERT 0 1\x00"))
                elif kind == b"S":
                    self.request.sendall(_pg_message(b"Z", b"T"))

    return Handler


def real_mariadb_driver() -> ModuleType:
    """The installed Connector/Python."""
    return importlib.import_module("mariadb")


@pytest.fixture
def real_mariadb(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    driver = real_mariadb_driver()
    monkeypatch.setitem(sys.modules, "mariadb", driver)
    monkeypatch.setattr("app.sources.client.mariadb.mariadb.mariadb", driver)
    return driver


UNKNOWN = "ix.unknown.t1.0"


async def run_durable_write(body: Callable[[], Awaitable[ToolOutput]]) -> tuple[str, int]:
    """One durable run whose model calls a non-idempotent write tool once: (the run's status, the tool's
    executions). The tool goes through the production port and executor, as every connector tool does."""
    world = World()
    world.admit()
    tool = ScriptedTool("send", body)
    registry = ToolRegistry()
    registry.register_tool(tool)
    port = RegistryToolPort(registry, {"send": classify_tool_effect("send", [Tag("effect", "write")])})
    _, model = scripted(reply("", ("send", {})), reply("done"))
    kernel = DurableKernel(
        journal=world.journal,
        content=world.content,
        model=model,
        tools=port,
        clock=world.clock,
        checkpoints=world.checkpoints,
        live=world.live,
    )
    done = await world.run(kernel)
    status = done.status if done.status != "suspended" else f"suspended:{done.reason}"
    return status, tool.calls


async def as_tool_output(call: Awaitable[object]) -> ToolOutput:
    """What a connector tool does with its client's answer: an exception becomes an error result."""
    try:
        _ = await call
    except Exception as exc:
        return ToolOutput(success=False, error=type(exc).__name__)
    return ToolOutput(success=True, data="sent")


@contextmanager
def http_fake(**body: object) -> Iterator[FakeHTTP]:
    fake = FakeHTTP(body=dict(body) if body else {"id": "1"})
    with serve(fake.handler()) as address:
        fake.address = address
        yield fake


__all__ = [
    "CLOSE_AFTER",
    "DROP",
    "UNKNOWN",
    "FakeHTTP",
    "FakeSQL",
    "as_tool_output",
    "closed_port",
    "http_fake",
    "black_hole_handler",
    "mariadb_handler",
    "real_mariadb",
    "real_mariadb_driver",
    "redshift_handler",
    "run_durable_write",
    "serve",
]
