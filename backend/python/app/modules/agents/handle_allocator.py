"""Claim a unique handle by writing and retrying, never by checking first.

The unique constraint (Neo4j) / unique index (Arango) is the only arbiter, so two
concurrent creators of the same name cannot both win. The directory lookup runs
only after a loss, to skip past handles that are already taken.
"""

from collections.abc import Awaitable, Callable
from typing import Protocol, TypeVar

from app.modules.agents import handles
from app.services.graph_db.errors import UniqueConstraintViolation

T = TypeVar("T")

MAX_ATTEMPTS = 5
LOOKUP_LIMIT = 100


class HandleDirectory(Protocol):
    async def search_agent_handles(self, org_id: str, prefix: str, limit: int = 20) -> list[str]: ...


class HandlesExhaustedError(Exception):
    """No free candidate was claimed within the attempt budget."""


async def taken_handles(graph: HandleDirectory, org_id: str, base: str) -> set[str]:
    return set(await graph.search_agent_handles(org_id, base, LOOKUP_LIMIT))


async def suggest(graph: HandleDirectory, org_id: str, base: str) -> str | None:
    """The first free `base-N` (N >= 2), for a handle someone else already holds."""
    return handles.first_free_candidate(base, await taken_handles(graph, org_id, base), start=2)


async def claim(
    graph: HandleDirectory,
    org_id: str,
    base: str,
    write: Callable[[str], Awaitable[T]],
    *,
    max_attempts: int = MAX_ATTEMPTS,
) -> tuple[str, T]:
    """Run `write(candidate)` for `base`, then `base-2`, ... until one is not taken.

    `write` must raise `UniqueConstraintViolation` when the candidate is taken and
    must leave nothing behind when it does.
    """
    candidate: str | None = base
    taken: set[str] = set()
    for _ in range(max_attempts):
        if candidate is None:
            break
        try:
            return candidate, await write(candidate)
        except UniqueConstraintViolation:
            taken.add(candidate)
            taken |= await taken_handles(graph, org_id, base)
            candidate = handles.first_free_candidate(base, taken)
    raise HandlesExhaustedError(base)
