"""Run a named-entity graph write again when it lost a race it can win next time."""

from __future__ import annotations

import asyncio
import random
from typing import TYPE_CHECKING, TypeVar

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

T = TypeVar("T")

WRITE_ATTEMPTS = 5


async def retry_named_entity_write(graph, step: Callable[[], Awaitable[T]]) -> T:
    """A popular entity (a company, a year) is a hot node: concurrent writers and
    sweeps deadlock or conflict on it. The failed transaction rolled back whole and
    every step is idempotent, so running it again is safe."""
    for attempt in range(1, WRITE_ATTEMPTS + 1):
        try:
            return await step()
        except Exception as exc:
            if attempt == WRITE_ATTEMPTS or not graph.is_named_entity_write_retryable(exc):
                raise
            await _backoff(attempt)
    raise AssertionError("unreachable")


async def _backoff(attempt: int) -> None:
    await asyncio.sleep(random.uniform(0.02, 0.1) * 2 ** (attempt - 1))
