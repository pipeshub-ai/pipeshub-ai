"""LoopLocal: one resource per event loop, each closed on the loop that owns it."""

import asyncio
import threading
from collections.abc import Coroutine
from typing import Any, TypeVar

from app.utils.loop_local import LoopLocal, running_loop

T = TypeVar("T")


def _run(loop: asyncio.AbstractEventLoop, coro: Coroutine[Any, Any, T]) -> T:
    return loop.run_until_complete(coro)


async def _get(local: LoopLocal[T]) -> T:
    return local.get()


class _Resource:
    def __init__(self) -> None:
        self.closed_on: asyncio.AbstractEventLoop | None = None

    async def close(self) -> None:
        self.closed_on = asyncio.get_running_loop()


def test_a_loop_reuses_its_own_resource() -> None:
    local = LoopLocal(object)
    loop = asyncio.new_event_loop()
    try:
        assert _run(loop, _get(local)) is _run(loop, _get(local))
    finally:
        loop.close()


def test_each_loop_gets_its_own_resource() -> None:
    local = LoopLocal(object)
    a, b = asyncio.new_event_loop(), asyncio.new_event_loop()
    try:
        assert _run(a, _get(local)) is not _run(b, _get(local))
        assert len(local) == 2
    finally:
        a.close()
        b.close()


def test_a_closed_loops_resource_is_dropped_on_the_next_get() -> None:
    local = LoopLocal(object)
    gone = asyncio.new_event_loop()
    _run(gone, _get(local))
    gone.close()
    live = asyncio.new_event_loop()
    try:
        _run(live, _get(local))
        assert len(local) == 1
    finally:
        live.close()


def test_replacing_one_loops_resource_leaves_other_loops_alone() -> None:
    local = LoopLocal(object)
    a, b = asyncio.new_event_loop(), asyncio.new_event_loop()

    async def replace() -> object:
        return local.replace_current()

    try:
        first_a, first_b = _run(a, _get(local)), _run(b, _get(local))
        fresh_a = _run(a, replace())
        assert fresh_a is not first_a
        assert _run(a, _get(local)) is fresh_a
        assert _run(b, _get(local)) is first_b
    finally:
        a.close()
        b.close()


def test_closing_runs_each_close_on_the_loop_that_owns_the_resource() -> None:
    local = LoopLocal(_Resource)
    owner = asyncio.new_event_loop()
    thread = threading.Thread(target=owner.run_forever, daemon=True)
    thread.start()
    here = asyncio.new_event_loop()
    try:
        remote = asyncio.run_coroutine_threadsafe(_get(local), owner).result(timeout=5)
        mine = _run(here, _get(local))
        assert _run(here, local.aclose_all(lambda r: r.close())) == []
        assert (remote.closed_on, mine.closed_on) == (owner, here)
        assert len(local) == 0
    finally:
        owner.call_soon_threadsafe(owner.stop)
        thread.join(timeout=5)
        owner.close()
        here.close()


def test_close_errors_are_returned_and_a_stopped_loops_resource_is_only_dropped() -> None:
    class _Failing(_Resource):
        async def close(self) -> None:
            raise RuntimeError("boom")

    local = LoopLocal(_Failing)
    stopped = asyncio.new_event_loop()
    _run(stopped, _get(local))
    here = asyncio.new_event_loop()
    try:
        _run(here, _get(local))
        errors = _run(here, local.aclose_all(lambda r: r.close()))
        # Only this loop's close could run; the stopped loop's resource is dropped unclosed.
        assert [str(e) for e in errors] == ["boom"]
        assert len(local) == 0
    finally:
        stopped.close()
        here.close()


def test_there_is_no_running_loop_outside_one() -> None:
    assert running_loop() is None
