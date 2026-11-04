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


async def lock_apps(tx: Tx, org: str, app_ids: set[str], *, org_wide: bool) -> None:
    """Transaction-scoped locks, in the order above, held to the end of the unit."""
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
