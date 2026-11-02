"""Which Workspace accounts can no longer sign in, and which just came back.

One job: compare this run's complete user listing with the last one, kept in a sync point, and say whose access
to take away (newly suspended, archived or deleted) and whose to rebuild (suspended or archived before, active
now). A suspended, archived or deleted account cannot open anything in Google Drive, but its PipesHub account
would keep every grant the connector ever wrote for it.

The state is saved only by `save()`, which the caller calls after the access changes went through, so a run that
fails part-way repeats them next time.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.connectors.core.base.sync_point.sync_point import SyncPoint
    from app.models.entities import AppUser

USER_STATUS_SYNC_POINT_KEY = "workspace_user_status"


@dataclass
class UserStatusChange:
    inactive: set[str] = field(default_factory=set)  # every account that cannot sign in now
    withdraw: set[str] = field(default_factory=set)  # newly inactive or gone since the last listing
    restore: set[str] = field(default_factory=set)   # inactive at the last listing, active now
    known: set[str] = field(default_factory=set)     # every account listed now, active or not


def is_active_account(user: AppUser) -> bool:
    return user.is_active is not False


class UserStatusTracker:
    def __init__(self, sync_point: SyncPoint) -> None:
        self._sync_point = sync_point
        self._pending: dict | None = None

    async def compare(self, listed: list[AppUser]) -> UserStatusChange:
        """`listed` must be a complete listing of the Workspace's users."""
        stored = await self._sync_point.read_sync_point(USER_STATUS_SYNC_POINT_KEY) or {}
        was_inactive = {e for e in stored.get("inactive") or [] if isinstance(e, str)}
        was_known = {e for e in stored.get("known") or [] if isinstance(e, str)}
        known = {u.email.lower() for u in listed if u.email}
        inactive = {u.email.lower() for u in listed if u.email and not is_active_account(u)}
        gone = was_known - known
        change = UserStatusChange(
            inactive=inactive | gone,
            withdraw=(inactive - was_inactive) | gone,
            restore=(was_inactive & known) - inactive,
            known=known,
        )
        # A deleted account stays inactive until it is listed again, so undeleting it restores its grants.
        still_away = (was_inactive - known) | gone
        self._pending = {"inactive": sorted(inactive | still_away), "known": sorted(known)}
        return change

    async def save(self) -> None:
        if self._pending is not None:
            await self._sync_point.update_sync_point(USER_STATUS_SYNC_POINT_KEY, self._pending)
            self._pending = None
