"""PostgreSQL through the egress proxy: the proxy signs in, the sandbox's client never holds the password (OWD-1).

A sandbox with a database route resolves the route's name to the proxy and connects to ``POSTGRES_PORT``. The proxy
reads the client's startup message, opens its own connection to the operator's database (TLS as the route's
``sslmode`` says, with libpq's meaning of each mode), signs in with the sealed credential (SCRAM-SHA-256, MD5 or
cleartext, whichever the server asks for) as the credential's user, and only then tells the client it is signed in.
From there it copies bytes both ways. The client is never asked for a password, so none is ever in the sandbox.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import hmac
import secrets
import ssl
import struct
import time
from typing import TYPE_CHECKING, Final

from harness_core.logs import HarnessLog
from harness_core.sandbox.egress.errors import EgressRefusalReason as R
from harness_core.sandbox.egress.errors import EgressRefused
from harness_core.sandbox.egress.tunnel import (
    CONNECT_TIMEOUT_S,
    IDLE_TIMEOUT_S,
    copy_both,
    open_tcp,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from harness_core.sandbox.egress.proxy import EgressProxy
    from harness_core.sandbox.egress.tunnel import Connector
    from harness_core.sandbox.egress.types import DatabaseForward

PROTOCOL_3: Final = 196608
SSL_REQUEST: Final = 80877103
GSSENC_REQUEST: Final = 80877104
CANCEL_REQUEST: Final = 80877102
MAX_STARTUP_BYTES: Final = 10_000
STARTUP_TIMEOUT_S: Final = 30.0
AUTH_TIMEOUT_S: Final = 30.0
SCRAM: Final = "SCRAM-SHA-256"
# Startup parameters the proxy sets itself, or that would change what the connection is.
_DROPPED: Final = frozenset({"user", "replication"})
_LOG = HarnessLog.named("harness_core.sandbox.egress.postgres")


class DatabaseSignInFailed(Exception):
    """The proxy could not sign in upstream; ``message`` is what the client is told (no credential in it)."""

    def __init__(self, message: str, *, sqlstate: str = "08001", server_error: bytes | None = None) -> None:
        super().__init__(message)
        self.sqlstate = sqlstate
        self.server_error = server_error


# --- wire -------------------------------------------------------------------------------------------------------


def message(kind: bytes, body: bytes) -> bytes:
    return kind + struct.pack("!I", len(body) + 4) + body


def error_response(text: str, *, sqlstate: str = "08001") -> bytes:
    fields = b"SFATAL\x00VFATAL\x00C" + sqlstate.encode() + b"\x00M" + text.encode("utf-8") + b"\x00\x00"
    return message(b"E", fields)


AUTH_OK: Final = message(b"R", struct.pack("!I", 0))


def startup_message(params: Mapping[str, str]) -> bytes:
    body = struct.pack("!I", PROTOCOL_3)
    for key, value in params.items():
        body += key.encode("utf-8") + b"\x00" + value.encode("utf-8") + b"\x00"
    body += b"\x00"
    return struct.pack("!I", len(body) + 4) + body


def parse_startup(body: bytes) -> dict[str, str]:
    """A protocol 3.0 startup message's parameters (``body`` after its length and version)."""
    parts = body.split(b"\x00")
    if len(parts) < 2 or parts[-1] != b"" or parts[-2] != b"":
        raise ValueError("unterminated startup parameters")
    values = parts[:-2]
    if len(values) % 2:
        raise ValueError("a startup parameter has no value")
    return {values[i].decode("utf-8"): values[i + 1].decode("utf-8") for i in range(0, len(values), 2)}


async def read_message(reader: asyncio.StreamReader) -> tuple[bytes, bytes]:
    head = await reader.readexactly(5)
    (length,) = struct.unpack("!I", head[1:])
    if length < 4 or length > 1 << 20:
        raise ValueError("implausible message length")
    return head[:1], await reader.readexactly(length - 4)


# --- authentication ---------------------------------------------------------------------------------------------


def md5_password(user: str, password: str, salt: bytes) -> bytes:
    inner = hashlib.md5((password + user).encode("utf-8")).hexdigest()
    return b"md5" + hashlib.md5(inner.encode("ascii") + salt).hexdigest().encode("ascii")


class ScramClient:
    """SCRAM-SHA-256 without channel binding (RFC 5802, RFC 7677), as libpq speaks it: an empty user name."""

    def __init__(self, password: str, *, nonce: str | None = None, user: str = "") -> None:
        self._password = password.encode("utf-8")
        self._nonce = nonce or base64.b64encode(secrets.token_bytes(18)).decode("ascii")
        self._first_bare = f"n={user},r={self._nonce}"
        self._server_signature: bytes | None = None

    def first(self) -> bytes:
        return ("n,," + self._first_bare).encode("ascii")

    def final(self, server_first: bytes) -> bytes:
        text = server_first.decode("utf-8")
        attrs = dict(part.split("=", 1) for part in text.split(","))
        nonce, salt, iterations = attrs["r"], base64.b64decode(attrs["s"]), int(attrs["i"])
        if not nonce.startswith(self._nonce) or iterations < 1:
            raise DatabaseSignInFailed("the database's sign-in challenge is not valid")
        salted = hashlib.pbkdf2_hmac("sha256", self._password, salt, iterations)
        client_key = hmac.new(salted, b"Client Key", hashlib.sha256).digest()
        stored_key = hashlib.sha256(client_key).digest()
        without_proof = f"c=biws,r={nonce}"
        auth_message = f"{self._first_bare},{text},{without_proof}".encode()
        signature = hmac.new(stored_key, auth_message, hashlib.sha256).digest()
        proof = bytes(a ^ b for a, b in zip(client_key, signature, strict=True))
        server_key = hmac.new(salted, b"Server Key", hashlib.sha256).digest()
        self._server_signature = hmac.new(server_key, auth_message, hashlib.sha256).digest()
        return f"{without_proof},p={base64.b64encode(proof).decode('ascii')}".encode("ascii")

    def verify(self, server_final: bytes) -> None:
        attrs = dict(part.split("=", 1) for part in server_final.decode("utf-8").split(",") if "=" in part)
        expected = self._server_signature
        if expected is None or not hmac.compare_digest(base64.b64decode(attrs.get("v", "")), expected):
            raise DatabaseSignInFailed("the database did not prove it knows the password")


async def sign_in(
    reader: asyncio.StreamReader, writer: asyncio.StreamWriter, user: str, password: str
) -> None:
    """Answer the server's authentication requests until it says ``AuthenticationOk``."""
    scram: ScramClient | None = None
    while True:
        kind, body = await read_message(reader)
        if kind == b"E":
            raise DatabaseSignInFailed("the database refused the sign-in", sqlstate="28000", server_error=body)
        if kind == b"N":
            continue
        if kind != b"R" or len(body) < 4:
            raise DatabaseSignInFailed("the database answered the sign-in with something else")
        (code,) = struct.unpack("!I", body[:4])
        payload = body[4:]
        if code == 0:
            return
        if code == 3:
            writer.write(message(b"p", password.encode("utf-8") + b"\x00"))
        elif code == 5:
            writer.write(message(b"p", md5_password(user, password, payload[:4]) + b"\x00"))
        elif code == 10:
            mechanisms = [m.decode("ascii") for m in payload.split(b"\x00") if m]
            if SCRAM not in mechanisms:
                raise DatabaseSignInFailed("the database offers no sign-in this proxy speaks")
            scram = ScramClient(password)
            first = scram.first()
            writer.write(message(b"p", SCRAM.encode("ascii") + b"\x00" + struct.pack("!I", len(first)) + first))
        elif code == 11 and scram is not None:
            writer.write(message(b"p", scram.final(payload)))
        elif code == 12 and scram is not None:
            scram.verify(payload)
        else:
            raise DatabaseSignInFailed("the database asks for a sign-in this proxy does not speak")
        await writer.drain()


# --- TLS to the database ----------------------------------------------------------------------------------------


def upstream_tls(sslmode: str) -> ssl.SSLContext | None:
    """libpq's meaning of ``sslmode``: ``require`` encrypts without checking the certificate, ``verify-ca`` checks
    it, ``verify-full`` also checks the name; ``disable`` never encrypts. ``None``: plain."""
    if sslmode == "disable":
        return None
    if sslmode in ("verify-ca", "verify-full"):
        context = ssl.create_default_context()
        context.check_hostname = sslmode == "verify-full"
        return context
    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    return context


async def open_database(
    forward: DatabaseForward, connector: Connector, *, timeout_s: float = CONNECT_TIMEOUT_S
) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    reader, writer = await asyncio.wait_for(connector(forward.host, forward.port), timeout_s)
    context = upstream_tls(forward.sslmode)
    if context is None:
        return reader, writer
    writer.write(struct.pack("!II", 8, SSL_REQUEST))
    await writer.drain()
    answer = await reader.readexactly(1)
    if answer == b"S":
        await writer.start_tls(context, server_hostname=forward.host)
        return reader, writer
    if forward.sslmode in ("require", "verify-ca", "verify-full"):
        writer.close()
        raise DatabaseSignInFailed("the database does not offer the encryption its route requires")
    return reader, writer


# --- the listener -----------------------------------------------------------------------------------------------


class PostgresFront:
    """Serves the proxy's PostgreSQL port; an unbind closes that sandbox's connections."""

    def __init__(
        self, proxy: EgressProxy, *, connector: Connector = open_tcp, idle_timeout_s: float = IDLE_TIMEOUT_S
    ) -> None:
        self._proxy = proxy
        self._connector = connector
        self._idle_timeout_s = idle_timeout_s
        self._open: dict[str, set[asyncio.Task[None]]] = {}
        self._tasks: set[asyncio.Task[None]] = set()
        proxy.bindings.on_unregister(self.close_sandbox)

    def start(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        task = asyncio.get_running_loop().create_task(self.serve(reader, writer))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def close_sandbox(self, sandbox_id: str) -> None:
        for task in self._open.pop(sandbox_id, set()):
            _ = task.cancel()

    async def aclose(self) -> None:
        for task in list(self._tasks):
            _ = task.cancel()
        _ = await asyncio.gather(*self._tasks, return_exceptions=True)

    async def serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        local = writer.get_extra_info("sockname")
        peer = writer.get_extra_info("peername")
        try:
            await self._serve(reader, writer, str(local[0]) if local else "", str(peer[0]) if peer else "")
        except (ConnectionError, asyncio.IncompleteReadError, ValueError, TimeoutError):
            pass
        finally:
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()

    async def _startup(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> dict[str, str] | None:
        while True:
            (length,) = struct.unpack("!I", await reader.readexactly(4))
            if length < 8 or length > MAX_STARTUP_BYTES:
                raise ValueError("implausible startup length")
            body = await reader.readexactly(length - 4)
            (code,) = struct.unpack("!I", body[:4])
            if code in (SSL_REQUEST, GSSENC_REQUEST):
                # The sandbox's network to the proxy is its own; only the proxy's side leaves the host.
                writer.write(b"N")
                await writer.drain()
                continue
            if code == CANCEL_REQUEST:
                return None
            if code != PROTOCOL_3:
                writer.write(error_response("unsupported frontend protocol", sqlstate="0A000"))
                await writer.drain()
                return None
            return parse_startup(body[4:])

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, local: str, peer: str) -> None:
        params = await asyncio.wait_for(self._startup(reader, writer), STARTUP_TIMEOUT_S)
        if params is None:
            return
        try:
            forward = await self._proxy.authorize_database(local=local, peer=peer)
        except EgressRefused as refused:
            writer.write(error_response(f"egress refused: {refused.reason.value}", sqlstate="28000"))
            await writer.drain()
            return
        current = asyncio.current_task()
        if current is not None:
            self._open.setdefault(forward.sandbox_id, set()).add(current)
        try:
            await self._relay(forward, params, reader, writer)
        finally:
            if current is not None:
                self._open.get(forward.sandbox_id, set()).discard(current)

    async def _relay(
        self,
        forward: DatabaseForward,
        params: Mapping[str, str],
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        fields = {
            **{k: v for k, v in forward.labels.items() if k not in ("event", "exc")},
            "sandboxId": forward.sandbox_id,
            "proxyRequestId": forward.request_id,
            "destination": forward.destination,
        }
        # The route's user signs in; without one (the operator's URL named none) the client's own name is used.
        user = forward.user or params.get("user", "")
        sent = {"user": user, **{k: v for k, v in params.items() if k not in _DROPPED}}
        try:
            up_reader, up_writer = await open_database(forward, self._connector)
        except (OSError, TimeoutError, ssl.SSLError, asyncio.IncompleteReadError, DatabaseSignInFailed) as exc:
            _LOG.warning("egress.database_unavailable", **fields, errorType=type(exc).__name__)
            writer.write(error_response("the database is unreachable through the egress proxy"))
            await writer.drain()
            return
        try:
            up_writer.write(startup_message(sent))
            await up_writer.drain()
            await asyncio.wait_for(sign_in(up_reader, up_writer, user, forward.password), AUTH_TIMEOUT_S)
        except DatabaseSignInFailed as failed:
            _LOG.warning("egress.database_signin_failed", **fields, sqlstate=failed.sqlstate)
            writer.write(
                message(b"E", failed.server_error)
                if failed.server_error is not None
                else error_response(str(failed), sqlstate=failed.sqlstate)
            )
            await writer.drain()
            up_writer.close()
            return
        except (OSError, TimeoutError, ValueError, asyncio.IncompleteReadError) as exc:
            _LOG.warning("egress.database_signin_failed", **fields, errorType=type(exc).__name__)
            writer.write(error_response("the database sign-in did not complete"))
            await writer.drain()
            up_writer.close()
            return
        writer.write(AUTH_OK)
        await writer.drain()
        _LOG.info("egress.database_opened", **fields)
        started, counts = time.monotonic(), [0, 0]
        try:
            await copy_both(
                reader, writer, up_reader, up_writer, counts, idle_timeout_s=self._idle_timeout_s, meter=forward.meter
            )
        finally:
            up_writer.close()
            with contextlib.suppress(Exception):
                await up_writer.wait_closed()
            capped = forward.meter is not None and forward.meter.stopped
            _LOG.info(
                "egress.database_closed",
                **fields,
                bytesUp=counts[0],
                bytesDown=counts[1],
                seconds=round(time.monotonic() - started, 3),
                reason=R.VOLUME_CAP_EXCEEDED.value if capped else None,
            )
        if capped:
            refused = EgressRefused(R.VOLUME_CAP_EXCEEDED, "the run moved more than the deployment's egress cap")
            binding = self._proxy.bindings.get(forward.sandbox_id)
            self._proxy.record_refusal(refused, request_id=forward.request_id, binding=binding)


__all__ = [
    "AUTH_OK",
    "DatabaseSignInFailed",
    "PostgresFront",
    "ScramClient",
    "error_response",
    "md5_password",
    "open_database",
    "parse_startup",
    "read_message",
    "sign_in",
    "startup_message",
    "upstream_tls",
]
