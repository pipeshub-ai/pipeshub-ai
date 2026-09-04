"""Deficit Round Robin scheduler for fair per-key message dispatch.

Pure and broker-agnostic: this module knows nothing about Kafka, Redis
Streams, or ``StreamMessage`` -- callers extract the fairness key and hand
over an opaque item. That keeps the algorithm testable on its own and reusable
by both indexing consumers (and any future one) without a shared base class.

**Keys are hierarchical.** A key is a tuple of levels, e.g.
``("org-7", "connector-42")``. The scheduler round-robins across level 0, and
*within* each level-0 key round-robins across level 1, and so on. That
nesting is not a second algorithm: :class:`_Node` implements one round of
deficit round robin over its children, and its children are either more
``_Node``s (an interior level) or the FIFO of buffered entries (the leaf). A
single-level key gives exactly flat DRR.

Why hierarchy is the default rather than a flat composite key: flattening
``(org, connector)`` into one key would give an org with 50 connectors fifty
times the share of an org with one. Sharing fairly between orgs *and* between
each org's connectors is two levels of the same arithmetic, not one level
over a concatenated string.

Single-threaded contract: every method here is called only from the
consumer's main event loop (both the Kafka and Redis Streams consume loops
already run there), so no internal locking is needed.
"""
from __future__ import annotations

import time
from collections import deque
from typing import TYPE_CHECKING, Generic, TypeVar

from app.services.messaging.scheduling.interface import (
    EnqueueResult,
    FairnessKey,
    FairSchedulerConfig,
    WeightProvider,
)

if TYPE_CHECKING:
    from collections.abc import Callable

T = TypeVar("T")

__all__ = ["DRRScheduler"]


class _Entry(Generic[T]):
    """One buffered item plus the time (``time.time()``-based) before which
    it must not be dispatched. Mirrors the ``_retry_not_before`` convention
    already used for re-queued messages elsewhere in this package."""

    __slots__ = ("item", "not_before")

    def __init__(self, item: T, not_before: float | None) -> None:
        self.item = item
        self.not_before = not_before


class _Node(Generic[T]):
    """One level of the DRR tree.

    An interior node round-robins over child nodes; the leaf node holds the
    FIFO of entries for one fully-qualified key. Both cases share
    :meth:`try_pop`'s round arithmetic, which is what makes hierarchical
    fairness the same algorithm as flat fairness rather than a second one.

    ``count`` is the number of items in this whole subtree, so an interior
    node can tell an empty child from a busy one without walking it.
    """

    __slots__ = ("prefix", "depth", "leaf_depth", "children", "order", "deficit", "entries", "count")

    def __init__(self, prefix: FairnessKey, depth: int, leaf_depth: int) -> None:
        self.prefix = prefix
        self.depth = depth
        self.leaf_depth = leaf_depth
        self.children: dict[str, _Node[T]] = {}
        # Round-robin order of children with a non-empty subtree. Invariant:
        # a child appears at most once, and only while it holds items --
        # maintained by push (append on empty->non-empty) and
        # try_pop/purge (drop once it drains).
        self.order: deque[str] = deque()
        self.deficit: dict[str, int] = {}
        self.entries: deque[_Entry[T]] = deque()
        self.count = 0

    @property
    def is_leaf(self) -> bool:
        return self.depth == self.leaf_depth

    def child(self, name: str) -> _Node[T] | None:
        return self.children.get(name)

    def push(self, key: FairnessKey, entry: _Entry[T]) -> None:
        self.count += 1
        if self.is_leaf:
            self.entries.append(entry)
            return
        name = key[self.depth]
        child = self.children.get(name)
        if child is None:
            child = _Node((*self.prefix, name), self.depth + 1, self.leaf_depth)
            self.children[name] = child
        was_empty = child.count == 0
        child.push(key, entry)
        if was_empty:
            self.order.append(name)
            self.deficit.setdefault(name, 0)

    def try_pop(
        self,
        now: float,
        can_dispatch: Callable[[T], bool] | None,
        quantum_for: Callable[[FairnessKey], int],
    ) -> tuple[FairnessKey, _Entry[T]] | None:
        """Pop the next eligible entry in DRR order, or ``None`` if nothing
        under this node is eligible right now.

        A child whose head is not eligible -- not due yet, or rejected by
        ``can_dispatch`` -- is skipped *without spending any of its deficit*
        and keeps its place in the round. Charging it would let a blocked
        key lose turns it never got to use.
        """
        if self.is_leaf:
            if not self.entries:
                return None
            head = self.entries[0]
            if head.not_before is not None and head.not_before > now:
                return None
            if can_dispatch is not None and not can_dispatch(head.item):
                return None
            self.entries.popleft()
            self.count -= 1
            return self.prefix, head

        remaining = len(self.order)
        while remaining > 0:
            name = self.order[0]
            child = self.children.get(name)
            if child is None or child.count == 0:
                # Invariant violation guard (should not happen): drop the
                # stale entry rather than spin on it.
                self.order.popleft()
                self.children.pop(name, None)
                self.deficit.pop(name, None)
                remaining -= 1
                continue

            popped = child.try_pop(now, can_dispatch, quantum_for)
            if popped is None:
                self.order.rotate(-1)
                remaining -= 1
                continue

            self.count -= 1
            if self.deficit.get(name, 0) < 1:
                self.deficit[name] = self.deficit.get(name, 0) + quantum_for(
                    child.prefix
                )
            self.deficit[name] -= 1
            self.order.popleft()

            if child.count > 0:
                if self.deficit[name] >= 1:
                    # Still credit left this turn: keep serving this child.
                    self.order.appendleft(name)
                else:
                    self.order.append(name)
            else:
                self.deficit.pop(name, None)
                del self.children[name]

            return popped

        return None

    def purge(self, predicate: Callable[[T], bool], removed: list[T]) -> None:
        if self.is_leaf:
            kept: deque[_Entry[T]] = deque()
            for entry in self.entries:
                if predicate(entry.item):
                    removed.append(entry.item)
                    self.count -= 1
                else:
                    kept.append(entry)
            self.entries = kept
            return

        for name in list(self.children.keys()):
            child = self.children[name]
            before = child.count
            child.purge(predicate, removed)
            self.count -= before - child.count
            if child.count == 0:
                try:
                    self.order.remove(name)
                except ValueError:
                    pass
                self.deficit.pop(name, None)
                del self.children[name]

    def drain(self, out: list[tuple[FairnessKey, T]]) -> None:
        if self.is_leaf:
            for entry in self.entries:
                out.append((self.prefix, entry.item))
            self.entries.clear()
        else:
            for child in self.children.values():
                child.drain(out)
            self.children.clear()
            self.order.clear()
            self.deficit.clear()
        self.count = 0

    def leaf_count(self) -> int:
        """Number of distinct fully-qualified keys with buffered items."""
        if self.is_leaf:
            return 1 if self.entries else 0
        return sum(child.leaf_count() for child in self.children.values())


class DRRScheduler(Generic[T]):
    """Hierarchical Deficit Round Robin over per-key virtual queues.

    Each key is a tuple of levels (``("org", "connector")`` by default). The
    scheduler round-robins across level 0, granting a key ``quantum`` credits
    the first time it is considered on a turn and spending one per dispatched
    item -- so a key with quantum 3 gets up to three consecutive items before
    the scheduler moves on, while a key with an empty queue costs nothing and
    is skipped entirely. Within a level-0 key the same arithmetic runs over
    level 1, and so on. A one-level key degenerates to flat DRR; a single
    active key degenerates to plain FIFO.

    ``max_per_entity_messages`` caps the *leaf* queue: the cap is per
    connector, not per org, so one org's many connectors each get their own
    allowance rather than competing for a shared one.
    """

    def __init__(
        self,
        config: FairSchedulerConfig,
        weights: WeightProvider | None = None,
    ) -> None:
        self._config = config
        self._weights = weights
        self._depth = max(1, len(config.key_fields))
        self._root: _Node[T] = _Node((), 0, self._depth)

    def _quantum_for(self, key: FairnessKey) -> int:
        if self._weights is not None:
            weight = self._weights.quantum_for(key)
            if weight > 0:
                return weight
        return max(1, self._config.default_quantum)

    def _normalize(self, key: FairnessKey) -> FairnessKey:
        """Pad or trim a key to the configured depth.

        A caller whose extractor disagrees with ``key_fields`` (a custom
        extractor, or a config change mid-flight) must not corrupt the tree
        shape -- every key has to address exactly ``depth`` levels.
        """
        if len(key) == self._depth:
            return key
        if len(key) > self._depth:
            return key[: self._depth]
        return key + ("__default__",) * (self._depth - len(key))

    def enqueue(
        self,
        key: FairnessKey,
        item: T,
        not_before: float | None = None,
    ) -> EnqueueResult:
        """Add ``item`` to ``key``'s virtual queue.

        Checked in this order: total buffer capacity first (a caller cannot
        distinguish "this key is busy" from "everyone is busy" otherwise),
        then the per-leaf cap. Neither check creates tree nodes, so a
        rejected enqueue leaves no empty branches behind.
        """
        if self._root.count >= self._config.max_buffered_messages:
            return EnqueueResult.BUFFER_FULL

        key = self._normalize(key)
        if self.pending_count_for(key) >= self._config.max_per_entity_messages:
            return EnqueueResult.ENTITY_FULL

        self._root.push(key, _Entry(item=item, not_before=not_before))
        return EnqueueResult.ACCEPTED

    def dequeue(
        self,
        can_dispatch: Callable[[T], bool] | None = None,
    ) -> tuple[FairnessKey, T] | None:
        """Return the next ``(key, item)`` in fair order, or ``None`` if
        nothing is currently eligible."""
        popped = self._root.try_pop(time.time(), can_dispatch, self._quantum_for)
        if popped is None:
            return None
        key, entry = popped
        return key, entry.item

    def purge(self, predicate: Callable[[T], bool]) -> list[T]:
        """Remove and return every buffered item matching ``predicate``.

        Used on Kafka partition revocation: items for revoked partitions must
        be dropped (they will be redelivered to whichever replica the
        partition lands on next) rather than dispatched against a partition
        this consumer no longer owns.
        """
        removed: list[T] = []
        self._root.purge(predicate, removed)
        return removed

    @property
    def pending_count(self) -> int:
        return self._root.count

    @property
    def active_entity_count(self) -> int:
        """Distinct fully-qualified keys with buffered items."""
        return self._root.leaf_count()

    def active_count_at(self, level: int) -> int:
        """Distinct keys with buffered items at ``level`` (0 = outermost).

        The org-level count and the connector-level count answer different
        operational questions -- "how many customers are we serving right
        now" versus "how many syncs" -- so both are exposed.
        """
        node = self._root
        if level == 0:
            return len(node.order) if not node.is_leaf else (1 if node.entries else 0)
        frontier = [node]
        for _ in range(level):
            nxt: list[_Node[T]] = []
            for current in frontier:
                if not current.is_leaf:
                    nxt.extend(current.children.values())
            frontier = nxt
        return sum(
            len(n.order) if not n.is_leaf else (1 if n.entries else 0)
            for n in frontier
        )

    def pending_count_for(self, key: FairnessKey) -> int:
        """Items buffered under ``key``, which may be a prefix: passing
        ``("org-7",)`` counts everything for that org across its connectors.
        """
        node: _Node[T] | None = self._root
        for name in key:
            if node is None or node.is_leaf:
                return 0
            node = node.child(name)
        return node.count if node is not None else 0

    @property
    def is_empty(self) -> bool:
        return self._root.count == 0

    def drain_all(self) -> list[tuple[FairnessKey, T]]:
        """Remove and return every buffered ``(key, item)``, for shutdown.

        Callers must not commit/ACK drained items -- they are left for
        redelivery, exactly like any other in-flight message at shutdown.
        """
        result: list[tuple[FairnessKey, T]] = []
        self._root.drain(result)
        return result
