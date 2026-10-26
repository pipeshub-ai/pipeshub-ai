"""One org's share of a pool while another org wants it too (S-4).

The gates bound how much work a service runs, not whose: one org's burst --
a monorepo onboarding, a 4 MB bundle per commit -- took every slot and the
next org's requests waited out the gate timeout. An org may hold more than
its share of a pool only while no other org has a request for that pool
waiting or running, so a single-org install keeps the whole pool.
"""
from __future__ import annotations

from collections import Counter

from app.utils.env_config import env_float

__all__ = ["OrgShare", "max_org_share"]


def max_org_share() -> float:
    return env_float("PARSE_MAX_ORG_SHARE", 0.5, low=0.05, high=1.0)


class OrgShare:
    def __init__(self, share: float) -> None:
        self._share = share
        # (pool, org) -> requests waiting or running; and cost held once admitted
        self._demand: Counter[tuple[str, str]] = Counter()
        self._held: Counter[tuple[str, str]] = Counter()

    def arrive(self, pool: str, org: str) -> None:
        self._demand[(pool, org)] += 1

    def leave(self, pool: str, org: str, held_cost: int = 0) -> None:
        self._demand[(pool, org)] -= 1
        self._held[(pool, org)] -= held_cost
        if self._demand[(pool, org)] <= 0:
            del self._demand[(pool, org)]
            self._held.pop((pool, org), None)

    def over_share(self, pool: str, org: str, cost: int, limit: int) -> bool:
        """Whether admitting ``cost`` more would put ``org`` past its share of
        ``pool`` while another org is waiting on, or using, the same pool."""
        held = self._held[(pool, org)]
        # An org with nothing running is always let in, so a request costing
        # more than the share still runs.
        if not held or not any(p == pool and o != org for p, o in self._demand):
            return False
        return held + cost > max(1, int(limit * self._share))

    def hold(self, pool: str, org: str, cost: int) -> None:
        self._held[(pool, org)] += cost
