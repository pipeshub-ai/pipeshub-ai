"""Word matching the fake connectors search with. Standard library only: a worker process imports it on the
clock of its lease (EV2's ticket connector on E1's real lane), so it must not pull in the product."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence


def words(text: str) -> set[str]:
    return {w for w in "".join(c.lower() if c.isalnum() else " " for c in text).split() if len(w) > 2}


def matching[T](items: Sequence[T], query: str, text: Callable[[T], str]) -> list[T]:
    """Items sharing a word with ``query``; every item when none does (a search that always answers)."""
    wanted = words(query)
    return [item for item in items if wanted & words(text(item))] or list(items)


__all__ = ["matching", "words"]
