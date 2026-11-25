"""CH-12: the fault matrix's partition is silent. While partitioned, bytes are held both ways and nothing is reset,
so each side learns of it only from its own timeouts, as on a real network; healed, what was held is delivered."""

from __future__ import annotations

import asyncio
import contextlib

from tests.unit.harness.engines.durable_stack import NodeOutage


async def _echo(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    with contextlib.suppress(ConnectionError):
        while data := await reader.read(1024):
            writer.write(data)
            await writer.drain()
    writer.close()


async def test_ch12_a_partition_holds_bytes_without_a_reset_and_heals() -> None:
    server = await asyncio.start_server(_echo, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    link = NodeOutage("127.0.0.1", ("127.0.0.1", port))
    await link.start()
    reader, writer = await asyncio.open_connection("127.0.0.1", int(link.url.rsplit(":", 1)[1]))
    try:
        writer.write(b"before")
        assert await asyncio.wait_for(reader.read(1024), 5) == b"before"

        link.partition()
        writer.write(b"held")
        await writer.drain()
        try:
            got = await asyncio.wait_for(reader.read(1024), 0.5)
        except TimeoutError:
            got = None
        assert got is None, "nothing crosses a partition, and nothing is reset"

        link.heal()
        assert await asyncio.wait_for(reader.read(1024), 5) == b"held"
    finally:
        writer.close()
        await link.stop()
        server.close()
