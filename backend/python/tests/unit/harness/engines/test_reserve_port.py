"""The lanes' ports are held from the moment they are picked until the stack stops (``reserve_port``).

The engine lanes pick a port on the bridge address, hand it to the proxy container, and start the server on it
later. Picked by binding port 0 and closing the probe, the port was free in between, and an outgoing connection
from the same address could take it as its source port: the server then failed with ``[Errno 98] address already
in use``. Needs no Docker: loopback stands in for the bridge address.
"""

from __future__ import annotations

import errno
import socket

import pytest
from aiohttp import web

from tests.unit.harness.engines.real_stack import release_ports, reserve_port

HOST = "127.0.0.1"


def _claim(port: int) -> socket.socket:
    """Takes ``port`` as an outgoing connection's source port would, without ``SO_REUSEADDR``."""
    sock = socket.socket()
    try:
        sock.bind((HOST, port))
    except BaseException:
        sock.close()
        raise
    return sock


async def _serve(port: int) -> web.AppRunner:
    runner = web.AppRunner(web.Application())
    await runner.setup()
    await web.TCPSite(runner, HOST, port, reuse_address=True).start()
    return runner


async def test_a_probed_and_closed_port_can_be_taken_before_its_server_binds() -> None:
    """The failure the reservation removes: nothing holds a probed port, so another socket can take it first."""
    with socket.socket() as probe:
        probe.bind((HOST, 0))
        port = probe.getsockname()[1]
    taker = _claim(port)
    try:
        with pytest.raises(OSError) as refused:
            await _serve(port)
        assert refused.value.errno == errno.EADDRINUSE
    finally:
        taker.close()


async def test_a_reserved_port_refuses_other_sockets_and_serves_its_own_server_until_released() -> None:
    port = reserve_port(HOST)
    try:
        with pytest.raises(OSError) as refused:
            _claim(port)
        assert refused.value.errno == errno.EADDRINUSE

        first = await _serve(port)
        with pytest.raises(OSError):
            await _serve(port)
        await first.cleanup()
        # A lane restarts servers on their port (outages, a second worker): the reservation still lets it.
        again = await _serve(port)
        await again.cleanup()
    finally:
        release_ports(HOST)
    _claim(port).close()


def test_every_reservation_is_a_distinct_port_until_released() -> None:
    try:
        ports = {reserve_port(HOST) for _ in range(32)}
        assert len(ports) == 32
    finally:
        release_ports(HOST)
