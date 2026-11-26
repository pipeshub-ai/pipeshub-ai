"""A tool call's deadline, and dependency reads bounded by it (ERR-UX, QA17-O5).

The executor opens a call's scope with its wall bound. A client on the run path (graph, vector, embedding, MCP,
sandbox broker) reads through ``bounded_read``: inside a scope it waits at most its dependency's read bound or the
call's remaining time, whichever is less, and a dependency that accepts the connection and never answers ends in
``DependencyTimeout`` (code ``timeout``) instead of holding the call to the client's own minutes-long bound. Outside a
scope (indexing, connectors) a read is not changed.

Times are the running loop's clock, the one ``asyncio.timeout`` reads.
"""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
from types import MappingProxyType
from typing import TYPE_CHECKING, Final, Literal

from harness_core.effects.failures import ToolFailure

if TYPE_CHECKING:
    from collections.abc import Awaitable, Iterator, Mapping

Dependency = Literal["graph", "vector", "embedding", "mcp", "sandbox_broker"]

# The longest one read of each dependency may take on the run path; the call's remaining time bounds it further.
DEPENDENCY_READ_S: Final[Mapping[Dependency, float]] = MappingProxyType({
    "graph": 60.0,
    "vector": 60.0,
    "embedding": 60.0,
    "mcp": 60.0,
    "sandbox_broker": 120.0,
})
# How long a client on the run path may take to connect: a refused or unroutable store fails in seconds.
CONNECT_TIMEOUT_S: Final = 10.0

_deadline: ContextVar[float | None] = ContextVar("harness_call_deadline", default=None)


class DependencyTimeout(ToolFailure):
    """A dependency did not answer within its bound; the message names the dependency and the bound only."""

    def __init__(self, dependency: Dependency, bound_s: float) -> None:
        super().__init__("timeout", f"{dependency} did not answer within {bound_s:g} s")
        self.dependency: Dependency = dependency
        self.bound_s = bound_s


@contextmanager
def call_deadline(seconds: float | None) -> Iterator[None]:
    """The scope of one call bounded by ``seconds`` (``None``: unbounded); a nested scope never extends its parent."""
    if seconds is None:
        yield
        return
    at = asyncio.get_running_loop().time() + seconds
    parent = _deadline.get()
    token = _deadline.set(at if parent is None else min(parent, at))
    try:
        yield
    finally:
        _deadline.reset(token)


def remaining_s() -> float | None:
    """The current call's time left; ``None`` outside a call's scope."""
    at = _deadline.get()
    return None if at is None else max(0.0, at - asyncio.get_running_loop().time())


async def bounded_read[T](dependency: Dependency, read: Awaitable[T]) -> T:
    left = remaining_s()
    if left is None:
        return await read
    bound = min(DEPENDENCY_READ_S[dependency], left)
    try:
        async with asyncio.timeout(bound):
            return await read
    except TimeoutError as exc:
        raise DependencyTimeout(dependency, bound) from exc


__all__ = [
    "CONNECT_TIMEOUT_S",
    "DEPENDENCY_READ_S",
    "Dependency",
    "DependencyTimeout",
    "bounded_read",
    "call_deadline",
    "remaining_s",
]
