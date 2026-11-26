"""The modes of a connector in kg and the moves between them.

``legacy``: readers take today's walk (the knowledge hub listing reads the structure of a finished copy of this code's epoch, never its access: ``kh_listing.head``). ``shadow``: kg has been copied and verified against the graph (``code_epoch`` and ``verified_at`` say when and by which code), readers still take
the walk and compare. ``enforce``: readers of a connector that is *current* (:func:`drainer.is_current`) take kg. The only way up is a backfill that ends in a verify without a difference
and without a change waiting in the log (``backfill.backfill``), then a promotion that verifies again. The way down is always allowed and writes nothing else: :func:`demote`. A demoted connector has no copy cursor, so the next backfill starts from the beginning.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

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
