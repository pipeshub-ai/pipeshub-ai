"""Bounded containers for per-request accumulator state.

`AgentContext.tool_state` accumulates several lists/dicts across the life of
an agent run — tool call results, retrieval hits, web search records — with
no upper bound today (see `hooks/result_accumulation.py`,
`actions/retrieval/retrieval.py`). A long multi-turn run (`_MAX_TURNS = 15`,
each turn potentially calling 3-5 tools that each return 10-50 results) can
accumulate hundreds of entries, each carrying text snippets and metadata,
none of which get freed until the whole request completes.

This module provides the small set of primitives used to bound that growth:

- `BoundedList`: a `list` subclass that evicts from the FRONT (oldest first)
  once it exceeds `maxlen` — a drop-in replacement anywhere code reads the
  accumulator via indexing/iteration/`len()`, for rolling-window semantics.
- `bounded_append`: mutates a plain list stored under `container[key]` in
  place, evicting from the front past `maxlen` — used by hook call sites
  that don't control how the list was originally seeded (e.g. `ChatState`
  dicts built elsewhere) and so can't rely on the value being a
  `BoundedList` instance.
- `cap_list`: truncates an already-assembled list to at most `max_size`,
  keeping the first N entries. Used where results arrive pre-ranked by
  relevance (retrieval, web search) and the cap should keep the best
  matches rather than the most recent ones.
"""

from __future__ import annotations

from typing import Any, Iterable, TypeVar

T = TypeVar("T")

__all__ = ["BoundedDict", "BoundedList", "bounded_append", "cap_dict", "cap_list"]


class BoundedList(list):
    """A `list` that silently evicts its oldest entries once `append`/
    `extend` would push it past `maxlen`.

    Only `append`/`extend` enforce the bound — slicing, indexing, and
    iteration behave exactly like a normal list, so this is safe to hand to
    code that treats the accumulator as a plain `list[dict]` (JSON
    serialization, `len()`, `for entry in ...`).
    """

    def __init__(self, iterable: Iterable[T] = (), *, maxlen: int) -> None:
        if maxlen < 1:
            raise ValueError("maxlen must be >= 1")
        super().__init__(iterable)
        self.maxlen = maxlen
        self._evict()

    def append(self, item: T) -> None:  # type: ignore[override]
        super().append(item)
        self._evict()

    def extend(self, iterable: Iterable[T]) -> None:  # type: ignore[override]
        super().extend(iterable)
        self._evict()

    def _evict(self) -> None:
        overflow = len(self) - self.maxlen
        if overflow > 0:
            del self[:overflow]


class BoundedDict(dict):
    """A `dict` that evicts its oldest-inserted key once `__setitem__`/
    `update` would push it past `maxsize`.

    Not true LRU (no touch-on-read) — this is a FIFO-by-insertion bound,
    which is enough for write-heavy accumulators (e.g.
    `virtual_record_id_to_result`) that are read in bulk rather than
    repeatedly re-touched per key. Relies on dict preserving insertion
    order (guaranteed since Python 3.7).
    """

    def __init__(self, *args: Any, maxsize: int, **kwargs: Any) -> None:
        if maxsize < 1:
            raise ValueError("maxsize must be >= 1")
        super().__init__(*args, **kwargs)
        self.maxsize = maxsize
        self._evict()

    def __setitem__(self, key: Any, value: Any) -> None:
        super().__setitem__(key, value)
        self._evict()

    def update(self, *args: Any, **kwargs: Any) -> None:  # type: ignore[override]
        super().update(*args, **kwargs)
        self._evict()

    def _evict(self) -> None:
        while len(self) > self.maxsize:
            oldest_key = next(iter(self))
            del self[oldest_key]


def cap_dict(items: dict[Any, T], max_size: int) -> dict[Any, T]:
    """Return `items` truncated to at most `max_size` entries, keeping the
    MOST RECENTLY inserted keys (the tail of insertion order) — the newest
    document references are the ones most likely still relevant to the
    ongoing conversation."""
    if len(items) <= max_size:
        return items
    return dict(list(items.items())[-max_size:])


def bounded_append(container: dict[str, Any], key: str, item: Any, *, maxlen: int) -> None:
    """`container.setdefault(key, []).append(item)`, then trim the front of
    the list so it never holds more than `maxlen` entries.

    Works regardless of whether `container[key]` started life as a plain
    `list` (e.g. seeded by `chat_state.build_initial_state()`) or a
    `BoundedList` — this is the safe choice for hook call sites that don't
    own the accumulator's initial construction.
    """
    lst = container.setdefault(key, [])
    lst.append(item)
    overflow = len(lst) - maxlen
    if overflow > 0:
        del lst[:overflow]


def cap_list(items: list[T], max_size: int) -> list[T]:
    """Return `items` truncated to at most `max_size` entries, keeping the
    first N. Retrieval/web-search results arrive already ranked by
    relevance (never re-sorted downstream — see `retrieval.py`'s "Do NOT
    sort here" note), so keeping the head preserves the best matches rather
    than the most recently appended ones."""
    if len(items) <= max_size:
        return items
    return items[:max_size]
