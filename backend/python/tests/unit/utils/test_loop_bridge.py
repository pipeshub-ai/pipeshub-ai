"""run_on_loop: a reply that arrived while the caller's loop was frozen."""
import asyncio
import threading
import time
from collections.abc import Iterator

import pytest

from app.utils.loop_bridge import run_on_loop


@pytest.fixture
def owner_loop() -> Iterator[asyncio.AbstractEventLoop]:
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    yield loop

    def _cancel_and_stop() -> None:
        for task in asyncio.all_tasks(loop):
            task.cancel()
        loop.call_soon(loop.stop)

    loop.call_soon_threadsafe(_cancel_and_stop)
    thread.join(timeout=5)
    loop.close()


async def _reply() -> str:
    return "ok"


async def _never() -> None:
    await asyncio.sleep(3600)


@pytest.mark.asyncio
async def test_reply_that_arrived_during_a_freeze_is_returned(owner_loop) -> None:
    task = asyncio.ensure_future(run_on_loop(owner_loop, _reply(), timeout=0.2))
    await asyncio.sleep(0)
    time.sleep(0.5)  # blocks this loop past the timeout; the reply lands meanwhile
    assert await task == "ok"


@pytest.mark.asyncio
async def test_a_reply_that_never_comes_names_the_operation(owner_loop) -> None:
    with pytest.raises(TimeoutError, match="_never"):
        await run_on_loop(owner_loop, _never(), timeout=0.1)
