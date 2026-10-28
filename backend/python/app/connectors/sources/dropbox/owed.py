"""Work an earlier run owes, kept with the connector's sync points so it survives a restart.

Dropbox lists each change once. A delete whose existence check failed, or a folder whose cursor was reset part way,
must not be forgotten when the cursor moves on: the external ids are kept here and the next run tries them again.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.connectors.core.base.sync_point.sync_point import (
    generate_record_sync_point_key,
)

if TYPE_CHECKING:
    from app.connectors.core.base.sync_point.sync_point import SyncPoint

OWED_DELETES_KEY = generate_record_sync_point_key("owed", "deletes", "global")


class OwedDeletes:
    def __init__(self, sync_point: SyncPoint) -> None:
        self.sync_point = sync_point
        self.ids: list[str] = []

    async def load(self) -> list[str]:
        data = await self.sync_point.read_sync_point(OWED_DELETES_KEY) or {}
        self.ids = list(dict.fromkeys(data.get("externalIds") or []))
        return list(self.ids)

    def add(self, external_ids: list[str]) -> None:
        for external_id in external_ids:
            if external_id not in self.ids:
                self.ids.append(external_id)

    def settle(self, still_owed: list[str]) -> None:
        """After the owed ids were checked again: only those still unsure stay."""
        self.ids = list(dict.fromkeys(still_owed))

    async def save(self) -> None:
        await self.sync_point.update_sync_point(OWED_DELETES_KEY, {"externalIds": self.ids})
