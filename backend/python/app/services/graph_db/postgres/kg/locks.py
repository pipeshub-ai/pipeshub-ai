"""The per-(org, app) advisory locks of the projection.

Every projection holds the lock of each connector whose rows it writes, so two projections of one connector never interleave and one that reads the graph after taking
the lock sees every write a projection that committed before it made. The locks are taken in a fixed order and held to the end of the unit: the org-wide key ('') first, then
the connectors in the order of their lock keys (the hash, then the id: two ids can share a key, and an order by id would then differ between units that need overlapping
sets). The org-wide key is taken exclusively by a projection that touches principals or nodes that are gone (their rows span connectors) and shared by one that stays inside
its connectors.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.core.db import Tx

LOCK_CLASS = 7360      # registered in kernel.lock_keys by kg_0001
MAX_ROUNDS = 6         # a unit that keeps finding connectors it has not locked this many times gives up


class NeedLocks(Exception):
    """The unit reaches rows of connectors it does not hold (or of a node that needs the whole org). Raised before anything of theirs is written: the unit rolls back to its
    savepoint, which releases its locks, and takes the larger set in one ascending pass."""

    def __init__(self, apps: set[str], *, wide: bool = False) -> None:
        super().__init__(f"the unit reaches connectors {sorted(apps)}" + (" and rows that span the organisation" if wide else ""))
        self.apps = set(apps)
        self.wide = wide


async def lock_apps(tx: Tx, org: str, app_ids: set[str], *, org_wide: bool) -> None:
    """Transaction-scoped locks, in the order above, held to the end of the unit. No connector to lock is no reason to lock less: the org key is then exclusive."""
    org_wide = org_wide or not (app_ids - {""})
    if org_wide:
        await tx.execute("/* pipeshub:kg.lock_org_exclusive */ SELECT pg_advisory_xact_lock($1::int, hashtext($2))", LOCK_CLASS, f"{org}|")
    else:
        await tx.execute("/* pipeshub:kg.lock_org_shared */ SELECT pg_advisory_xact_lock_shared($1::int, hashtext($2))", LOCK_CLASS, f"{org}|")
    apps = sorted(app_ids - {""})
    if not apps:
        return
    keys = {r["app"]: r["k"] for r in await tx.fetch("/* pipeshub:kg.lock_keys */ SELECT a AS app, hashtext($1 || '|' || a) AS k FROM unnest($2::text[]) a", org, apps)}
    for a in sorted(apps, key=lambda x: (keys[x], x)):
        await tx.execute("/* pipeshub:kg.lock_app */ SELECT pg_advisory_xact_lock($1::int, $2::int)", LOCK_CLASS, keys[a])


async def try_lock_apps(tx: Tx, org: str, app_ids: set[str], *, org_wide: bool = False) -> bool:
    """The locks of :func:`lock_apps` for a unit that must not wait (the org key, shared or with ``org_wide`` exclusive, then the connectors in the same order), or False at the first one that
    is held elsewhere. The caller rolls back to a savepoint of its own on False, which releases what was taken."""
    if org_wide:
        taken = await tx.fetchval("/* pipeshub:kg.try_lock_org_exclusive */ SELECT pg_try_advisory_xact_lock($1::int, hashtext($2))", LOCK_CLASS, f"{org}|")
    else:
        taken = await tx.fetchval("/* pipeshub:kg.try_lock_org */ SELECT pg_try_advisory_xact_lock_shared($1::int, hashtext($2))", LOCK_CLASS, f"{org}|")
    if not taken:
        return False
    apps = sorted(app_ids - {""})
    if not apps:
        return True
    keys = {r["app"]: r["k"] for r in await tx.fetch("/* pipeshub:kg.lock_keys */ SELECT a AS app, hashtext($1 || '|' || a) AS k FROM unnest($2::text[]) a", org, apps)}
    for a in sorted(apps, key=lambda x: (keys[x], x)):
        if not await tx.fetchval("/* pipeshub:kg.try_lock_app */ SELECT pg_try_advisory_xact_lock($1::int, $2::int)", LOCK_CLASS, keys[a]):
            return False
    return True
