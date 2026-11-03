"""Each connector's share of the indexing slots, for the fair dispatcher.

The DRR scheduler decides whose record is *dispatched* next, and it counts
records: every dispatch costs the key one credit. That is fair when records
cost about the same, and it is not when they do not. The resource every
record competes for is an index slot, and a record holds its slot from
download to vector upsert -- seconds for a chat message, minutes for a large
pull request. With equal dispatch counts, the connector whose records hold
their slots forty times longer ends up holding nearly every slot, and the
other connector's records wait behind it.

This module bounds that. While another connector has records waiting for
the same slot pool, a connector may have at most its share of that pool's
slots in flight (dispatched and not yet finished): ``ceil(limit * share)``,
never less than one. The share is split the same way the scheduler's key is,
level by level -- between orgs first, then between one org's connectors --
weighted by the scheduler's own ``WeightProvider`` when one is configured.

The cap only applies while someone else is waiting. A connector indexing on
its own still gets every slot, so an idle pool never stays idle for the sake
of fairness. It does not take slots back either: a connector that already
holds more than its share keeps them until those records finish.

Bookkeeping is in-process and per node, like the slot pools it divides. A
restart loses nothing that matters: the records that were in flight are
gone with the process, so their counts would be zero anyway.
"""
from __future__ import annotations

import math
import threading
from collections import defaultdict
from typing import TYPE_CHECKING

from app.services.messaging.scheduling.interface import FairnessKey, WeightProvider

if TYPE_CHECKING:
    from collections.abc import Callable, Hashable, Iterable, Mapping

__all__ = ["SlotShare", "SlotSharePlan"]

# (entity key, slot pool name)
_EntityPool = tuple[FairnessKey, str]


class SlotSharePlan:
    """Each connector's cap for one dispatch decision.

    Built from the set of connectors with records waiting, per slot pool, so
    that every check made while choosing the next record sees the same
    picture. Cheap to rebuild: the scheduler hands out one record per
    decision, and the backlog is small (one entry per connector and tier).
    """

    __slots__ = ("_caps", "_in_flight", "_pool_of", "_entity_depth")

    def __init__(
        self,
        caps: "Mapping[_EntityPool, int]",
        in_flight: "Callable[[FairnessKey, str], int]",
        pool_of: "Callable[[FairnessKey], str]",
        entity_depth: int,
    ) -> None:
        self._caps = caps
        self._in_flight = in_flight
        self._pool_of = pool_of
        self._entity_depth = entity_depth

    def cap_for(self, key: FairnessKey) -> int | None:
        """The in-flight cap for ``key``'s connector in its pool, or ``None``
        when nobody else is waiting for that pool (no cap)."""
        entity = key[: self._entity_depth]
        return self._caps.get((entity, self._pool_of(key)))

    def allows(self, key: FairnessKey) -> bool:
        """Whether the record at the head of ``key``'s queue may be dispatched.

        ``key`` is the scheduler's full leaf key; its first ``entity_depth``
        levels name the connector.
        """
        cap = self.cap_for(key)
        if cap is None:
            return True
        entity = key[: self._entity_depth]
        return self._in_flight(entity, self._pool_of(key)) < cap


class SlotShare:
    """Counts each connector's records in flight per slot pool, and turns the
    current backlog into per-connector caps (see the module docstring).

    ``started`` is called on the consumer's main loop when a record is
    dispatched; ``finished`` may be called from the worker thread when its
    task ends, so the counts are guarded by a lock. ``finished`` is
    idempotent and ignores tokens it never saw, so callers can call it on
    every exit path without tracking whether the record was counted.
    """

    def __init__(
        self,
        entity_depth: int,
        weights: WeightProvider | None = None,
    ) -> None:
        self._entity_depth = max(1, entity_depth)
        self._weights = weights
        self._lock = threading.Lock()
        self._by_token: dict[Hashable, _EntityPool] = {}
        self._counts: defaultdict[_EntityPool, int] = defaultdict(int)

    # -- bookkeeping -----------------------------------------------------

    def started(self, token: Hashable, key: FairnessKey, pool: str) -> None:
        entity_pool = (key[: self._entity_depth], pool)
        with self._lock:
            if token in self._by_token:
                return
            self._by_token[token] = entity_pool
            self._counts[entity_pool] += 1

    def finished(self, token: Hashable) -> None:
        with self._lock:
            entity_pool = self._by_token.pop(token, None)
            if entity_pool is None:
                return
            remaining = self._counts[entity_pool] - 1
            if remaining > 0:
                self._counts[entity_pool] = remaining
            else:
                del self._counts[entity_pool]

    def in_flight(self, entity: FairnessKey, pool: str) -> int:
        with self._lock:
            return self._counts.get((tuple(entity), pool), 0)

    # -- policy ----------------------------------------------------------

    def _weight(self, prefix: FairnessKey) -> int:
        if self._weights is not None:
            weight = self._weights.quantum_for(prefix)
            if weight > 0:
                return weight
        return 1

    def plan(
        self,
        backlogged: "Iterable[FairnessKey]",
        pool_of: "Callable[[FairnessKey], str]",
        limit_of: "Callable[[str], int]",
    ) -> SlotSharePlan:
        """Caps for every connector with records waiting.

        ``backlogged`` is every leaf key with buffered records
        (``DRRScheduler.backlogged_keys``); ``pool_of`` maps a leaf key to the
        slot pool its records draw from; ``limit_of`` gives a pool's current
        slot limit on this node. A pool with a single waiting connector gets
        no caps at all.
        """
        waiting: defaultdict[str, set[FairnessKey]] = defaultdict(set)
        for key in backlogged:
            waiting[pool_of(key)].add(tuple(key[: self._entity_depth]))

        caps: dict[_EntityPool, int] = {}
        for pool, entities in waiting.items():
            if len(entities) < 2:
                continue
            limit = max(1, limit_of(pool))
            for entity, share in self._shares(entities).items():
                caps[(entity, pool)] = max(1, math.ceil(limit * share))
        return SlotSharePlan(caps, self.in_flight, pool_of, self._entity_depth)

    def _shares(self, entities: "set[FairnessKey]") -> dict[FairnessKey, float]:
        """Split 1.0 between ``entities`` level by level, by weight.

        Mirrors the scheduler's hierarchy: an org with ten waiting connectors
        gets the same share as an org with one, and its ten split it.
        """
        shares: dict[FairnessKey, float] = {}

        def split(prefix: FairnessKey, members: list[FairnessKey], share: float) -> None:
            depth = len(prefix)
            if depth == self._entity_depth:
                shares[prefix] = share
                return
            children: dict[FairnessKey, list[FairnessKey]] = {}
            for entity in members:
                children.setdefault(entity[: depth + 1], []).append(entity)
            weights = {child: self._weight(child) for child in children}
            total = sum(weights.values())
            for child, child_members in children.items():
                split(child, child_members, share * weights[child] / total)

        split((), sorted(entities), 1.0)
        return shares
