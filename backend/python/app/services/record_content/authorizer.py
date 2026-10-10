"""`TieredRecordAuthorizer` — cheapest-first, no bypass.

Three tiers, each a real check:

0. The record is live. A record in the trash keeps its permission edges so it
   can be restored, and tier 2 would otherwise grant access from the edge alone.
   Raises `RecordNotFoundError`: to the reader the item is gone.
1. Org scope: `record.org_id == actor.org_id`. O(1), always runs.
2. `check_record_access_with_details`, which decides through the batch access
   check: connector and collection records by the permission model, and
   artifacts and chat attachments (records outside every App) by a grant on
   the record itself.

A direct user-to-record edge is not accepted on its own: it would skip the
STRICT/RESTRICTED rule and the connector gate, and these bytes leave the
system. No bypass path exists; service-account callers
follow the same gate (see plan §Authorization — Open decision).
"""

from __future__ import annotations

import logging
from typing import Any

from app.services.graph_db.common.record_visibility import is_live_record

from .models import RecordAccessDeniedError, RecordNotFoundError

logger = logging.getLogger(__name__)

__all__ = ["TieredRecordAuthorizer"]


class TieredRecordAuthorizer:
    """The authorization gate for the record-content kernel.

    Constructed once per resolver instance; thread-safe and stateless
    beyond the injected graph_provider.
    """

    def __init__(self, graph_provider: Any) -> None:
        self._graph = graph_provider

    async def authorize(self, actor: Any, record: Any) -> None:
        """Authorize `actor` to read `record`. Raises `RecordAccessDeniedError`
        on denial; returns `None` on success.

        Raises `RecordNotFoundError` for a record in the trash. Never raises
        for reasons other than a confirmed denial — I/O errors propagate as
        their own exception types.
        """
        if not is_live_record(record):
            record_id = getattr(record, "id", None) or (
                record.get("_key") if isinstance(record, dict) else None
            )
            raise RecordNotFoundError(f"Record {record_id} was deleted")

        # Tier 1 — org scope (O(1), always runs)
        record_org = getattr(record, "org_id", None) or (
            record.get("orgId") if isinstance(record, dict) else None
        )
        if record_org and record_org != actor.org_id:
            raise RecordAccessDeniedError(
                f"Record does not belong to org {actor.org_id}"
            )

        record_id = getattr(record, "id", None) or (
            record.get("_key") if isinstance(record, dict) else None
        )

        # Tier 2 — the permission model
        tier3_result = await self._graph.check_record_access_with_details(
            actor.user_id, actor.org_id, record_id
        )
        if tier3_result is not None:
            return

        logger.warning(
            "access_denied actor=%s record=%s",
            actor.user_id,
            record_id,
        )
        raise RecordAccessDeniedError(
            f"Actor {actor.user_id} is not authorized to read record {record_id}"
        )
