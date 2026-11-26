"""An asyncio loop whose clock moves only when every task waits on a timer.

A bound the code enforces with ``asyncio.wait_for``, ``asyncio.timeout`` or ``loop.call_at`` is a deadline on the
loop's clock. Timed with ``time.monotonic()`` on a real loop, the reading also holds whatever stopped the process
meanwhile (a descheduled CPU under a full gate, a collection of a large heap), so the test fails on the host, not
on the code. On this loop the reading is the sum of the deadlines the code waited on, exactly, and it does not wait
for them in real time.

Ready file descriptors (the loop's self-pipe, a socket) are still served first, and a loop with no timer left blocks
on them for real. A test whose code waits on real I/O and a timer at once sees the timer fire first: use it for code
whose waits are all on the loop's clock. That includes ``asyncio.to_thread`` and ``run_in_executor``: the clock does
not wait for a thread, so a deadline raced against one fires first.
"""

from __future__ import annotations

import asyncio
import functools
import inspect
import selectors
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine


class _VirtualSelector(selectors.DefaultSelector):
    def __init__(self, advance: Callable[[float], None]) -> None:
        super().__init__()
        self._advance = advance

    def select(self, timeout: float | None = None) -> list[tuple[selectors.SelectorKey, int]]:
        ready = super().select(0)
        if ready or timeout == 0:
            return ready
        if timeout is None:
            return super().select(None)
        self._advance(timeout)
        return []


class VirtualTimeLoop(asyncio.SelectorEventLoop):
    def __init__(self, start: float = 0.0) -> None:
        self._now = start
        super().__init__(selector=_VirtualSelector(self._advance))

    def time(self) -> float:
        return self._now

    def _advance(self, seconds: float) -> None:
        self._now += max(0.0, seconds)


def run_on_virtual_time[T](main: Callable[[], Coroutine[Any, Any, T]]) -> T:
    async def run() -> T:
        try:
            return await main()
        finally:
            # The runner's own shutdown bounds the executor's join with a 300 s timer, which this clock reaches at
            # once; joined here without a timer, the loop waits for the threads for real.
            await asyncio.get_running_loop().shutdown_default_executor()

    with asyncio.Runner(loop_factory=VirtualTimeLoop) as runner:
        return runner.run(run())


def on_virtual_time[F: Callable[..., Any]](test: F) -> F:
    """An ``async def`` test run by ``run_on_virtual_time``; pytest still fills its fixtures (the wrapper keeps the
    test's signature). On a class, every ``async def test_*`` of it."""
    if isinstance(test, type):
        for name, member in list(vars(test).items()):
            if name.startswith("test") and inspect.iscoroutinefunction(member):
                setattr(test, name, on_virtual_time(member))
        return test

    @functools.wraps(test)
    def run(*args: Any, **kwargs: Any) -> Any:  # noqa: ANN401 - the test's own arguments
        return run_on_virtual_time(lambda: test(*args, **kwargs))

    return run  # type: ignore[return-value]


__all__ = ["VirtualTimeLoop", "on_virtual_time", "run_on_virtual_time"]
