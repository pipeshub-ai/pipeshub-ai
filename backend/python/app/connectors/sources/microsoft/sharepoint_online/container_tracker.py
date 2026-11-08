"""Remembers which sites and libraries the last complete listing had, to tell which ones have since gone.

Graph's delta covers the items of a library, not the library or the site it is in: a deleted library or site
never shows up in any delta. The connector compares what a complete listing returns with what the previous
one returned. A container that is missing is only a candidate: the caller removes it after Graph confirms,
by answering 404 for that very container, that it no longer exists.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.connectors.core.base.sync_point.sync_point import SyncPoint


class ContainerTracker:
    def __init__(self, sync_point: "SyncPoint", key: str) -> None:
        self.sync_point = sync_point
        self.key = key

    async def known(self) -> set[str]:
        stored = await self.sync_point.read_sync_point(self.key)
        return set((stored or {}).get("ids") or [])

    async def remember(self, ids: set[str]) -> None:
        await self.sync_point.update_sync_point(self.key, sync_point_data={"ids": sorted(ids)})
