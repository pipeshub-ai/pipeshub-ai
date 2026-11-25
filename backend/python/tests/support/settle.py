"""Awaiting what a test expects to finish, with a hang backstop instead of a wall-clock bound (WALLCLOCK-1).

``asyncio.wait_for(task, 1)`` reads as "it finishes", but the 1 s is a bound on the host: a gate host that keeps the
process off the CPU, or a collection of a large xdist worker's heap, spends it before the task's next step runs. When
the failure the test guards against is a hang, the bound only has to end that hang with an error that names the
await, inside pytest-timeout's 30 s. When the failure is "it finished, but later than it should", a duration cannot
tell the two apart on a loaded host: assert the order instead (what was still pending when this finished).
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, TypeVar

if TYPE_CHECKING:
    from collections.abc import Awaitable

T = TypeVar("T")

SETTLE_S = 20.0


async def settle(awaitable: Awaitable[T]) -> T:
    return await asyncio.wait_for(awaitable, SETTLE_S)


__all__ = ["SETTLE_S", "settle"]
