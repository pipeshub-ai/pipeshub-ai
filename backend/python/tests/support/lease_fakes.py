"""A lease manager with one owner per pool, for tests of what holds a lease and what a lapsed holder may still do."""
from __future__ import annotations

from typing import TYPE_CHECKING

from app.services.distributed.interface import IDistributedLeaseManager

if TYPE_CHECKING:
    from collections.abc import Sequence

class FakeLeases(IDistributedLeaseManager):
    """One owner per pool; renewal can be made to fail."""

    def __init__(self) -> None:
        self.owner: dict[str, str] = {}
        self.renews = True
        self.errors = 0  # the next this many renewals raise (a lease store that is briefly unreachable)

    async def initialize(self) -> None: ...
    async def cleanup(self) -> None: ...

    async def try_acquire(self, pool: str, owner: str, limit: int, lease_seconds: float) -> bool:
        if pool in self.owner:
            return False
        self.owner[pool] = owner
        return True

    async def renew(self, pool: str, owner: str, lease_seconds: float) -> bool:
        if self.errors > 0:
            self.errors -= 1
            raise ConnectionError("lease store unreachable")
        return self.renews and self.owner.get(pool) == owner

    async def renew_many(self, leases: Sequence[tuple[str, str]], lease_seconds: float) -> dict:
        return dict.fromkeys(leases, self.renews)

    async def release(self, pool: str, owner: str) -> None:
        if self.owner.get(pool) == owner:
            del self.owner[pool]

    def take_away(self) -> None:
        """The holder's term ran out and a newer run took the lease (its renewer has not looked yet)."""
        for pool in list(self.owner):
            self.owner[pool] = "a newer run"
