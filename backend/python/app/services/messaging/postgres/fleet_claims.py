"""Which consumers hold a live claim in the PostgreSQL queue, for the fleet check of keeping the old copy (``keep_old_copy``).

A separate module on purpose: the queue's own module is pinned by the record-consumer hook tests, and this read changes nothing the queue
does (it only reads ``platform.queue_deliveries``).
"""
from __future__ import annotations

from app.services.pg_runtime import RuntimeDb

_UNTAGGED_CLAIMS = """/* pipeshub:queue.untaggedclaims */
SELECT DISTINCT consumer FROM platform.queue_deliveries
WHERE status = 'claimed' AND locked_until > now() AND consumer IS NOT NULL AND group_id = ANY($2::text[]) AND position($1::text IN consumer) = 0"""


async def untagged_claimants(tag: str, group_ids: list[str], runtime: RuntimeDb | None = None) -> list[str]:
    """Names of the consumers of ``group_ids`` (the indexing groups: the connector and other services' consumers never carry the tag)
    holding a live claim whose name lacks ``tag``: pods of an older protocol that are working. A pod that holds no claim leaves no trace in
    the queue tables, so this alone never proves a fleet clean."""
    async with (await (runtime or RuntimeDb()).db()).runtime_uow() as tx:
        return sorted(r["consumer"] for r in await tx.fetch(_UNTAGGED_CLAIMS, tag, list(group_ids)))
