"""Groups streams by Redis hash slot so a single ``XREADGROUP`` call never
spans more than one slot (R1).

On Redis Cluster / MemoryDB, ``XREADGROUP STREAMS a b`` raises CROSSSLOT the
moment ``a`` and ``b`` hash to different slots -- which lane streams
(``record-events.0`` .. ``.7``) and topic streams reliably do. On standalone,
:meth:`IRedisConnectionProvider.key_slot` always returns 0, so every stream
lands in one group and callers see exactly the single ``XREADGROUP`` call
they issued before this existed.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.services.redis.connection_provider import IRedisConnectionProvider


class StreamReadPlanner:
    """Partitions stream names into hash-slot-homogeneous groups."""

    def __init__(self, provider: "IRedisConnectionProvider") -> None:
        self._provider = provider

    def group(self, streams: list[str]) -> list[list[str]]:
        """Return groups of stream names that each share one hash slot.

        Group order is stable (first-seen slot first) so round-robin
        polling across groups is deterministic call to call; a stream added
        to ``streams`` after the planner was created is picked up the next
        time :meth:`group` runs, since nothing here is cached.
        """
        if not streams:
            return []
        by_slot: dict[int, list[str]] = {}
        for stream in streams:
            slot = self._provider.key_slot(stream)
            by_slot.setdefault(slot, []).append(stream)
        return list(by_slot.values())
