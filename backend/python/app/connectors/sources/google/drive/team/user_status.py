"""Which Workspace accounts can no longer sign in, and which just came back.

One job: compare this run's complete user listing with the last one, kept in a sync point, and say whose access
to take away (newly suspended, archived or deleted) and whose to rebuild (suspended or archived before, active
now). A suspended, archived or deleted account cannot open anything in Google Drive, but its PipesHub account
would keep every grant the connector ever wrote for it.

The state is saved only by `save()`, which the caller calls after the access changes went through, so a run that
fails part-way repeats them next time.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.connectors.core.base.sync_point.sync_point import SyncPoint
    from app.models.entities import AppUser

USER_STATUS_SYNC_POINT_KEY = "workspace_user_status"
USER_LISTING_FINGERPRINT_KEY = "workspace_user_listing_fingerprint"


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
        self._pending = {
            "inactive": sorted(inactive | still_away),
            "known": sorted(known),
            # The domains of the last complete listing: a person missing from a later cut one still has one of them.
            "domains": sorted({e.rpartition("@")[2] for e in known}),
        }
        return change

    async def save(self) -> None:
        if self._pending is not None:
            await self._sync_point.update_sync_point(USER_STATUS_SYNC_POINT_KEY, self._pending)
            self._pending = None

    async def inactive_on_partial_listing(self, listed: list[AppUser]) -> set[str]:
        """The accounts to treat as unable to sign in when `listed` may be missing users: the last complete
        listing's, plus any listed now. An account that is merely not listed is unknown, not inactive: it keeps its
        groups and grants. Nobody is restored. An account this listing shows unable to sign in is saved as inactive
        at once (the caller has already removed its access): narrowing is safe, a later cut listing must not forget
        it, and a complete listing that shows it active again restores it."""
        stored = await self._sync_point.read_sync_point(USER_STATUS_SYNC_POINT_KEY) or {}
        was_inactive = {e for e in stored.get("inactive") or [] if isinstance(e, str)}
        self._pending = None
        listed_inactive = {u.email.lower() for u in listed if u.email and not is_active_account(u)}
        if listed_inactive - was_inactive:
            await self._sync_point.update_sync_point(
                USER_STATUS_SYNC_POINT_KEY, {**stored, "inactive": sorted(was_inactive | listed_inactive)}
            )
        return was_inactive | listed_inactive

    async def known_domains(self) -> set[str]:
        """The domains of the last complete listing's accounts."""
        stored = await self._sync_point.read_sync_point(USER_STATUS_SYNC_POINT_KEY) or {}
        return {d for d in stored.get("domains") or [] if isinstance(d, str)}

    @staticmethod
    def fingerprint(listed: list[AppUser]) -> str:
        """How many accounts a listing has and which: the same directory read the same way gives the same one."""
        ids = sorted(u.source_user_id or u.email or "" for u in listed)
        return f"{len(ids)}:{hashlib.sha256(chr(10).join(ids).encode()).hexdigest()[:16]}"

    async def fingerprint_is_remembered(self, fingerprint: str) -> bool:
        stored = await self._sync_point.read_sync_point(USER_LISTING_FINGERPRINT_KEY) or {}
        return bool(fingerprint) and stored.get("fingerprint") == fingerprint

    async def remember_fingerprint(self, fingerprint: str) -> None:
        await self._sync_point.update_sync_point(USER_LISTING_FINGERPRINT_KEY, {"fingerprint": fingerprint})

    async def forget_fingerprint(self) -> None:
        """A listing that did not end as the ambiguous candidate: an older fingerprint must not vouch for a later one."""
        stored = await self._sync_point.read_sync_point(USER_LISTING_FINGERPRINT_KEY) or {}
        if stored.get("fingerprint"):
            await self._sync_point.update_sync_point(USER_LISTING_FINGERPRINT_KEY, {"fingerprint": ""})
