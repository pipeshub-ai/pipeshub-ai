"""Recording the threads a parse starts must not replace the executor the parsing service sized at start-up."""
from __future__ import annotations

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor

from app.services.parsing.thread_work import track


async def test_the_executor_the_service_installed_is_kept_and_its_jobs_are_recorded() -> None:
    loop = asyncio.get_running_loop()
    sized = ThreadPoolExecutor(max_workers=3)
    loop.set_default_executor(sized)
    release = threading.Event()
    try:
        with track() as work:
            job = asyncio.ensure_future(asyncio.to_thread(release.wait, 10))
            await asyncio.sleep(0.05)
            assert work.running() == 1
        assert loop._default_executor is sized               # not replaced
        assert sized._max_workers == 3                        # not resized
        idle = threading.Event()
        work.when_idle(idle.set)
        assert not idle.is_set()
        release.set()
        await job
        assert idle.wait(5)
        assert work.running() == 0
    finally:
        release.set()
        sized.shutdown(wait=True)


async def test_jobs_started_outside_a_track_block_are_not_recorded() -> None:
    loop = asyncio.get_running_loop()
    sized = ThreadPoolExecutor(max_workers=2)
    loop.set_default_executor(sized)
    try:
        with track() as work:
            pass
        await asyncio.to_thread(lambda: None)
        assert work.running() == 0
    finally:
        sized.shutdown(wait=True)
