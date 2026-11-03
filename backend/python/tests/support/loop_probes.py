"""Measuring whether the running event loop can still do I/O promptly."""
from __future__ import annotations

import asyncio
import socket
import time


class IoProbe:
    """The slowest burst of local socket round trips while the probe runs.

    The outage was a convoy, not a frozen loop: every I/O call releases the GIL
    and then waits for a busy thread to give it back, so a burst of Redis calls
    took seconds while a quiet loop still looked responsive. Two hundred round
    trips take under 10 ms on an idle loop and over a second with pdfminer
    running in a thread. The burst is that long so 0.25 s sits far from both
    on fast and slow machines alike: at fifty, a fast CI runner's stalled
    burst came in under it.
    """

    BURST = 200

    def __init__(self) -> None:
        self.worst = 0.0
        self._task: asyncio.Task | None = None

    async def _run(self) -> None:
        near, far = socket.socketpair()
        near_reader, near_writer = await asyncio.open_connection(sock=near)
        far_reader, far_writer = await asyncio.open_connection(sock=far)
        try:
            while True:
                started = time.monotonic()
                for _ in range(self.BURST):
                    near_writer.write(b"PING\n")
                    await far_reader.readline()
                    far_writer.write(b"PONG\n")
                    await near_reader.readline()
                self.worst = max(self.worst, time.monotonic() - started)
                await asyncio.sleep(0.01)
        finally:
            near_writer.close()
            far_writer.close()

    def __enter__(self) -> IoProbe:
        self._task = asyncio.get_running_loop().create_task(self._run())
        return self

    def __exit__(self, *_exc: object) -> None:
        assert self._task is not None
        self._task.cancel()
