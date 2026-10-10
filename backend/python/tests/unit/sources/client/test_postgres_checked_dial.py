"""connect_to_checked_address: each pool connection dials an address that passed the address policy."""

import socket
from unittest.mock import AsyncMock, MagicMock

import pytest

import app.sources.client.postgres.postgres as pg
from app.utils.url_fetcher import HostCheckError


@pytest.fixture
def fake_asyncpg(monkeypatch):
    fake = MagicMock()
    fake.connect = AsyncMock(return_value="connection")
    fake.create_pool = AsyncMock(return_value=MagicMock())
    monkeypatch.setattr(pg, "asyncpg", fake)
    return fake


@pytest.fixture(autouse=True)
def _private_addresses_allowed(monkeypatch):
    monkeypatch.delenv("PIPESHUB_BLOCK_PRIVATE_ADDRESSES", raising=False)


def _answers(*ips):
    return [
        (socket.AF_INET6 if ":" in ip else socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 0))
        for ip in ips
    ]


def _real_loop():
    loop = MagicMock()
    loop.create_connection = AsyncMock(return_value=("transport", "protocol"))
    loop.time = MagicMock(side_effect=[100.0, 103.0])
    return loop


async def test_the_name_is_looked_up_once_and_the_checked_address_dialled(fake_asyncpg, monkeypatch):
    # A second lookup of the name would answer with the cloud metadata address.
    getaddrinfo = MagicMock(side_effect=[_answers("203.0.113.7"), _answers("169.254.169.254")])
    monkeypatch.setattr(socket, "getaddrinfo", getaddrinfo)
    real_loop = _real_loop()

    assert await pg.connect_to_checked_address(host="db.example.com", port=5432, loop=real_loop) == "connection"
    connect_kwargs = fake_asyncpg.connect.await_args.kwargs
    # asyncpg keeps the name, for SNI and certificate checks.
    assert connect_kwargs["host"] == "db.example.com"
    await connect_kwargs["loop"].create_connection("factory", "db.example.com", 5432)

    real_loop.create_connection.assert_awaited_once_with("factory", "203.0.113.7", 5432)
    getaddrinfo.assert_called_once()


async def test_the_lookup_spends_the_connections_timeout(fake_asyncpg, monkeypatch):
    seen = {}

    async def vetted(host, *, lookup_timeout_s):
        seen["lookup_timeout_s"] = lookup_timeout_s
        import ipaddress

        return [ipaddress.ip_address("203.0.113.7")]

    monkeypatch.setattr(pg, "vetted_addresses", vetted)
    await pg.connect_to_checked_address(host="db.example.com", port=5432, timeout=10, loop=_real_loop())
    assert seen["lookup_timeout_s"] == 10.0
    assert fake_asyncpg.connect.await_args.kwargs["timeout"] == 7.0  # 3 s went on the lookup


@pytest.mark.parametrize(
    ("answer", "message"),
    [
        (socket.gaierror(11001, "getaddrinfo failed"), "Could not find a server"),
        (_answers("169.254.169.254"), "cloud metadata"),
    ],
)
async def test_a_refused_or_unresolvable_name_is_never_dialled(fake_asyncpg, monkeypatch, answer, message):
    monkeypatch.setattr(socket, "getaddrinfo", MagicMock(side_effect=[answer]))
    with pytest.raises(HostCheckError, match=message):
        await pg.connect_to_checked_address(host="db.example.com", port=5432, loop=_real_loop())
    fake_asyncpg.connect.assert_not_called()


async def test_a_private_address_is_refused_while_the_switch_is_on(fake_asyncpg, monkeypatch):
    monkeypatch.setenv("PIPESHUB_BLOCK_PRIVATE_ADDRESSES", "true")
    with pytest.raises(HostCheckError, match="private or internal address"):
        await pg.connect_to_checked_address(host="10.0.0.1", port=5432, loop=_real_loop())
    fake_asyncpg.connect.assert_not_called()


async def test_an_allowed_socket_path_is_left_to_asyncpg(fake_asyncpg):
    real_loop = _real_loop()
    await pg.connect_to_checked_address(host="/var/run/postgresql", port=5432, loop=real_loop)
    assert fake_asyncpg.connect.await_args.kwargs["loop"] is real_loop


async def test_the_dial_loop_tries_each_address_and_names_the_host_for_direct_tls():
    real_loop = _real_loop()
    real_loop.create_connection.side_effect = [ConnectionRefusedError(), ("transport", "protocol")]
    import ipaddress

    dial_loop = pg._CheckedDialLoop(real_loop, [ipaddress.ip_address("::1"), ipaddress.ip_address("127.0.0.1")])
    ssl_context = object()

    assert await dial_loop.create_connection("factory", "db.example.com", 5432, ssl=ssl_context) == (
        "transport",
        "protocol",
    )
    calls = real_loop.create_connection.await_args_list
    assert [call.args[1] for call in calls] == ["::1", "127.0.0.1"]
    assert all(call.kwargs == {"ssl": ssl_context, "server_hostname": "db.example.com"} for call in calls)
    assert dial_loop.start_tls is real_loop.start_tls


async def test_the_dial_loop_raises_the_last_error_when_every_address_fails():
    import ipaddress

    real_loop = _real_loop()
    real_loop.create_connection.side_effect = [ConnectionRefusedError(), TimeoutError()]
    dial_loop = pg._CheckedDialLoop(real_loop, [ipaddress.ip_address("::1"), ipaddress.ip_address("127.0.0.1")])
    with pytest.raises(TimeoutError):
        await dial_loop.create_connection("factory", "db.example.com", 5432)


async def test_the_pool_opens_every_connection_through_the_check(fake_asyncpg):
    client = pg.PostgreSQLClient(host="db.example.com", database="shop", user="reader", password="pw")
    await client.connect()
    assert fake_asyncpg.create_pool.await_args.kwargs["connect"] is pg.connect_to_checked_address
