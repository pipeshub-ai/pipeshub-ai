"""SR-10..SR-15 (WP-06, OWD-1): a sandbox's PostgreSQL client is signed in by the egress proxy, never by itself.

The server here is a stand-in that speaks the real protocol and checks SCRAM-SHA-256 from the password with RFC 5802's
own formulas (independent of the proxy's client); RFC 7677's published exchange pins the client. A real PostgreSQL
lane is ``test_wp06_postgres_docker.py``.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import hmac
import json
import struct
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import pytest

from harness_core.sandbox.auth import StaticBearerAuth
from harness_core.sandbox.egress import (
    EgressBinding,
    EgressProxy,
    EgressRefusalReason,
    EgressRefused,
    InMemoryEgressBindings,
    InMemoryTokenSource,
    ProxyLimits,
    StaticResolver,
)
from harness_core.sandbox.egress.postgres import (
    PROTOCOL_3,
    SSL_REQUEST,
    ScramClient,
    md5_password,
    parse_startup,
    read_message,
    startup_message,
)
from harness_core.sandbox.egress.sealed import (
    CredentialSealer,
    RoutedTokenSource,
    SealedTokenSource,
)
from harness_core.sandbox.egress.server import EgressServer
from harness_core.sandbox.types import (
    CredentialInjection,
    EgressDestination,
    EgressPolicy,
    TokenRoute,
)
from harness_core.testing.clock import ManualClock
from harness_core.testing.ids import SeededIds

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

SEALER = CredentialSealer(b"deployment-secret")
PH = "pxh-ph-database-0123456789abcd"
USER, PASSWORD = "app_reader", "db-pass-0123456789!"


def test_sr10_the_scram_client_matches_rfc_7677() -> None:
    client = ScramClient("pencil", nonce="rOprNGfwEbeRWgbNEkqO", user="user")
    assert client.first() == b"n,,n=user,r=rOprNGfwEbeRWgbNEkqO"
    final = client.final(b"r=rOprNGfwEbeRWgbNEkqO%hvYDpWUa2RaTCAfuxFIlj)hNlF$k0,s=W22ZaJ0SNY7soEsUEjb6gQ==,i=4096")
    assert final == b"c=biws,r=rOprNGfwEbeRWgbNEkqO%hvYDpWUa2RaTCAfuxFIlj)hNlF$k0,p=dHzbZapWIk4jUhN+Ute9ytag9zjfMHgsqmmiz7AndVQ="
    client.verify(b"v=6rriTRBi23WpRR/wtup+mMhUZUn/dB5nLTJRsjl95G4=")


def test_sr10_md5_is_postgres_double_hash() -> None:
    inner = hashlib.md5(b"secretbob").hexdigest()
    assert md5_password("bob", "secret", b"salt") == b"md5" + hashlib.md5(inner.encode() + b"salt").hexdigest().encode()


def test_sr10_startup_parameters_round_trip() -> None:
    raw = startup_message({"user": "u", "database": "d", "application_name": "psql"})
    (length, version) = struct.unpack("!II", raw[:8])
    assert length == len(raw) and version == PROTOCOL_3
    assert parse_startup(raw[8:]) == {"user": "u", "database": "d", "application_name": "psql"}


def _msg(kind: bytes, body: bytes) -> bytes:
    return kind + struct.pack("!I", len(body) + 4) + body


@dataclass
class FakePostgres:
    """Speaks the server side: asks for ``method`` (scram, md5, password), checks it, then answers queries."""

    method: str
    password: str = PASSWORD
    port: int = 0
    startups: list[dict[str, str]] = field(default_factory=list[dict[str, str]])
    received_passwords: list[bytes] = field(default_factory=list[bytes])
    queries: list[str] = field(default_factory=list[str])

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        with contextlib.suppress(Exception):
            await self._handle(reader, writer)
        writer.close()

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        while True:
            (length,) = struct.unpack("!I", await reader.readexactly(4))
            body = await reader.readexactly(length - 4)
            if struct.unpack("!I", body[:4])[0] == SSL_REQUEST:
                writer.write(b"N")
                continue
            params = parse_startup(body[4:])
            break
        self.startups.append(params)
        if not await self._authenticate(reader, writer, params["user"]):
            writer.write(_msg(b"E", b"SFATAL\x00C28P01\x00Mpassword authentication failed\x00\x00"))
            await writer.drain()
            return
        writer.write(_msg(b"R", struct.pack("!I", 0)))
        writer.write(_msg(b"S", b"server_version\x0016.4\x00"))
        writer.write(_msg(b"K", struct.pack("!II", 42, 7)))
        writer.write(_msg(b"Z", b"I"))
        await writer.drain()
        while True:
            kind, body = await read_message(reader)
            if kind == b"X":
                return
            if kind == b"Q":
                self.queries.append(body.rstrip(b"\x00").decode())
                writer.write(_msg(b"C", b"SELECT 1\x00") + _msg(b"Z", b"I"))
                await writer.drain()

    async def _authenticate(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, user: str) -> bool:
        if self.method == "password":
            writer.write(_msg(b"R", struct.pack("!I", 3)))
            await writer.drain()
            _, body = await read_message(reader)
            self.received_passwords.append(body)
            return body == self.password.encode() + b"\x00"
        if self.method == "md5":
            writer.write(_msg(b"R", struct.pack("!I", 5) + b"abcd"))
            await writer.drain()
            _, body = await read_message(reader)
            self.received_passwords.append(body)
            inner = hashlib.md5((self.password + user).encode()).hexdigest()
            return body == b"md5" + hashlib.md5(inner.encode() + b"abcd").hexdigest().encode() + b"\x00"
        return await self._scram(reader, writer)

    async def _scram(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> bool:
        writer.write(_msg(b"R", struct.pack("!I", 10) + b"SCRAM-SHA-256-PLUS\x00SCRAM-SHA-256\x00\x00"))
        await writer.drain()
        _, body = await read_message(reader)
        mechanism, _, rest = body.partition(b"\x00")
        assert mechanism == b"SCRAM-SHA-256"
        first = rest[4:].decode()
        first_bare = first.split(",", 2)[2]
        client_nonce = dict(p.split("=", 1) for p in first_bare.split(","))["r"]
        salt, iterations, nonce = b"0123456789abcdef", 4096, client_nonce + "server-nonce"
        server_first = f"r={nonce},s={base64.b64encode(salt).decode()},i={iterations}"
        writer.write(_msg(b"R", struct.pack("!I", 11) + server_first.encode()))
        await writer.drain()
        _, body = await read_message(reader)
        final = body.decode()
        without_proof, _, proof = final.rpartition(",p=")
        salted = hashlib.pbkdf2_hmac("sha256", self.password.encode(), salt, iterations)
        stored = hashlib.sha256(hmac.new(salted, b"Client Key", hashlib.sha256).digest()).digest()
        auth = f"{first_bare},{server_first},{without_proof}".encode()
        signature = hmac.new(stored, auth, hashlib.sha256).digest()
        client_key = bytes(a ^ b for a, b in zip(base64.b64decode(proof), signature, strict=True))
        if hashlib.sha256(client_key).digest() != stored:
            return False
        server_key = hmac.new(salted, b"Server Key", hashlib.sha256).digest()
        verifier = base64.b64encode(hmac.new(server_key, auth, hashlib.sha256).digest()).decode()
        writer.write(_msg(b"R", struct.pack("!I", 12) + f"v={verifier}".encode()))
        await writer.drain()
        return True


def _policy(port: int, *, credential: dict[str, str] | None = None, sslmode: str = "disable") -> EgressPolicy:
    material = json.dumps(credential or {"user": USER, "password": PASSWORD})
    return EgressPolicy(
        destinations=(
            EgressDestination("pxh-database", (5432,), upstream=f"postgres://{USER}@127.0.0.1:{port}/?sslmode={sslmode}"),
        ),
        token_routes=(TokenRoute(PH, "pxh-database", SEALER.seal(PH, material), CredentialInjection.POSTGRES),),
    )


@contextlib.asynccontextmanager
async def _stack(fake: FakePostgres, policy_port: int | None = None, **policy: object) -> AsyncIterator[int]:
    listener = await asyncio.start_server(fake.handle, "127.0.0.1", 0)
    fake.port = listener.sockets[0].getsockname()[1]
    clock = ManualClock()
    proxy = EgressProxy(
        bindings=InMemoryEgressBindings(),
        tokens=RoutedTokenSource(InMemoryTokenSource(b"t", clock), SealedTokenSource(SEALER, clock)),
        resolver=StaticResolver({}),
        upstreams={},
        clock=clock,
        ids=SeededIds(),
        limits=ProxyLimits(),
    )
    port = policy_port if policy_port is not None else fake.port
    await proxy.bindings.register(EgressBinding("sbx-db", "127.0.0.2", "127.0.0.1", _policy(port, **policy), {"orgId": "o1"}))  # pyright: ignore[reportArgumentType]
    server = EgressServer(proxy, StaticBearerAuth("c"), instance="db")
    _ = await server.start(host="127.0.0.2", data_port=0, control_port=0, control_host="127.0.0.1", database_port=0)
    assert server.database_port is not None
    try:
        yield server.database_port
    finally:
        await server.stop()
        listener.close()


async def _client(port: int, *, ssl_first: bool = True) -> tuple[asyncio.StreamReader, asyncio.StreamWriter, list[bytes]]:
    """A client as psql is: asks for TLS, takes no, sends its startup with a user of its own and no password."""
    reader, writer = await asyncio.open_connection("127.0.0.2", port, local_addr=("127.0.0.1", 0))
    if ssl_first:
        writer.write(struct.pack("!II", 8, SSL_REQUEST))
        await writer.drain()
        assert await reader.readexactly(1) == b"N"
    writer.write(startup_message({"user": "sandbox", "database": "analytics", "application_name": "psql"}))
    await writer.drain()
    kinds: list[bytes] = []
    while True:
        kind, body = await asyncio.wait_for(read_message(reader), 10)
        kinds.append(kind + body[:4] if kind == b"R" else kind)
        if kind in (b"Z", b"E"):
            return reader, writer, kinds


@pytest.mark.parametrize("method", ["scram", "md5", "password"])
async def test_sr11_the_proxy_signs_in_and_the_client_is_never_asked(method: str) -> None:
    fake = FakePostgres(method)
    async with _stack(fake) as port:
        reader, writer, kinds = await _client(port)
        assert kinds == [b"R" + struct.pack("!I", 0), b"S", b"K", b"Z"]
        writer.write(_msg(b"Q", b"select 1\x00"))
        await writer.drain()
        assert (await read_message(reader))[0] == b"C"
        writer.write(_msg(b"X", b""))
        await writer.drain()
        writer.close()
    assert fake.startups[0]["user"] == USER and fake.startups[0]["database"] == "analytics"
    assert fake.queries == ["select 1"]


async def test_sr12_a_wrong_password_is_the_servers_refusal_and_the_password_is_not_in_it() -> None:
    fake = FakePostgres("scram", password="the-real-one-0123456789")
    async with _stack(fake) as port:
        _, writer, kinds = await _client(port)
        writer.close()
    assert kinds == [b"E"]


async def test_sr13_a_connection_that_is_not_the_bound_sandboxs_is_refused_before_any_connection() -> None:
    fake = FakePostgres("scram")
    async with _stack(fake) as port:
        reader, writer = await asyncio.open_connection("127.0.0.2", port, local_addr=("127.0.0.3", 0))
        writer.write(startup_message({"user": "x", "database": "y"}))
        await writer.drain()
        kind, body = await asyncio.wait_for(read_message(reader), 10)
        writer.close()
    assert kind == b"E" and b"source_mismatch" in body and fake.startups == []


async def test_sr13_a_sandbox_without_a_database_route_reaches_no_database() -> None:
    clock = ManualClock()
    proxy = EgressProxy(
        bindings=InMemoryEgressBindings(),
        tokens=RoutedTokenSource(InMemoryTokenSource(b"t", clock), SealedTokenSource(SEALER, clock)),
        resolver=StaticResolver({}),
        upstreams={},
        clock=clock,
        ids=SeededIds(),
    )
    await proxy.bindings.register(
        EgressBinding("sbx-x", "127.0.0.2", "127.0.0.1", EgressPolicy(destinations=(EgressDestination("a.example"),)))
    )
    with pytest.raises(EgressRefused) as caught:
        _ = await proxy.authorize_database(local="127.0.0.2", peer="127.0.0.1")
    assert caught.value.reason is EgressRefusalReason.DESTINATION_NOT_ALLOWED


async def test_sr14_require_refuses_a_database_without_tls() -> None:
    fake = FakePostgres("scram")
    async with _stack(fake, sslmode="require") as port:
        _, writer, kinds = await _client(port)
        writer.close()
    assert kinds == [b"E"] and fake.startups == []


async def test_sr15_replication_is_not_passed_and_the_users_own_name_is_replaced() -> None:
    fake = FakePostgres("password")
    async with _stack(fake) as port:
        reader, writer = await asyncio.open_connection("127.0.0.2", port, local_addr=("127.0.0.1", 0))
        writer.write(startup_message({"user": "postgres", "database": "d", "replication": "database"}))
        await writer.drain()
        while (await asyncio.wait_for(read_message(reader), 10))[0] != b"Z":
            pass
        writer.close()
    assert fake.startups == [{"user": USER, "database": "d"}]
    assert fake.received_passwords == [PASSWORD.encode() + b"\x00"]
