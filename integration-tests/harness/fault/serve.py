"""Serving an aiohttp app for a fault-suite fake, ready only once it listens."""

from __future__ import annotations

import asyncio

from aiohttp import web


def serve(app: web.Application, port: int, ready: str) -> None:
    async def run() -> None:
        runner = web.AppRunner(app)
        await runner.setup()
        await web.TCPSite(runner, "127.0.0.1", port).start()
        print(f"{ready} {port}", flush=True)  # noqa: T201 - the rig's readiness line
        await asyncio.Event().wait()

    asyncio.run(run())
