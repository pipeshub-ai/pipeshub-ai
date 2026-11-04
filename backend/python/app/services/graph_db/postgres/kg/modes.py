"""The modes of a connector in kg and the moves between them.

``legacy``: readers take today's walk. ``shadow``: kg has been copied and verified against the graph (``code_epoch`` and ``verified_at`` say when and by which code), readers still take
the walk and compare. ``enforce``: readers of a connector that is *current* (:func:`drainer.is_current`) take kg. The only way up is a backfill that ends in a verify without a difference
and without a change waiting in the log (``backfill.backfill``), then a promotion that verifies again. The way down is always allowed and writes nothing else: :func:`demote` for one
connector, :func:`demote_org` for all of an org. A demoted connector has no copy cursor, so the next backfill starts from the beginning.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from app.services.graph_db.postgres.kg.locks import try_lock_apps
from app.services.graph_db.postgres.kg.writers import KgWriter

if TYPE_CHECKING:
    from app.core.db import Tx

MODES = ("legacy", "shadow", "enforce")


async def state(tx: Tx, org: str, app_sk: int) -> tuple[str | None, str]:
    """(copy cursor, mode) of a connector, or of the org's principals (``app_sk`` 0)."""
    r = await tx.fetchrow("/* pipeshub:kg.state_read */ SELECT backfill_cursor, mode FROM kg.projection_state WHERE org_id = $1 AND app_sk = $2", org, app_sk)
    return (r["backfill_cursor"], r["mode"]) if r else (None, "legacy")


async def demote(tx: Tx, org: str, app_sk: int) -> None:
    """Back to the walk, with nothing to resume: the copy starts again from the first node."""
    await KgWriter(tx).projection_set(app_sk, epoch=0, mode="legacy", digest=None, cursor=None, verified_at=None)


async def demote_org(tx: Tx, org: str) -> int:
    """Demote every connector of the org that is not already on the walk; returns how many. The principals' pseudo-connector (``app_sk`` 0) goes too, since its copy is as stale as the rest.
    Cross-connector marks (``cross_app``) are facts about the graph and are not touched."""
    rows = await tx.fetch("""/* pipeshub:kg.demote_read */ SELECT app_sk FROM kg.projection_state WHERE org_id = $1 AND (mode <> 'legacy' OR backfill_cursor IS NOT NULL) ORDER BY app_sk""", org)
    for r in rows:
        await demote(tx, org, r["app_sk"])
    return len(rows)


async def try_lock_copies(tx: Tx, org: str) -> bool:
    """Take, without waiting, the lock of every connector that is on a copy (not ``legacy``, or with a cursor). A flip or a promotion holds its connector's lock from before its verify to its
    write, so a take-back that demotes without this lock could delete the log row of a change that the verify never saw and demote, and the flip would then overwrite the demotion with
    ``shadow`` or ``enforce``. False when one is held: the take-back leaves its rows, which then also show the flip a change is waiting."""
    ids = [r["id"] for r in await tx.fetch("""/* pipeshub:kg.copies */ SELECT k.id FROM kg.projection_state s JOIN kg.node_keys k ON k.org_id = s.org_id AND k.sk = s.app_sk AND k.coll = 1
        WHERE s.org_id = $1 AND (s.mode <> 'legacy' OR s.backfill_cursor IS NOT NULL)""", org)]
    return await try_lock_apps(tx, org, set(ids))
