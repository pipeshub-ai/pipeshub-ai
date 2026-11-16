"""Block and group ids that follow a block's content, not the parse that produced it.

A parser mints a random id for every block it builds, so two parses of the same bytes (a retry, a
replica that raced the first run, a re-update) name the same block differently, and each run's
vector points sit beside the other's. `BlockIdentity.assign` replaces those ids, once per parse
and for every parser, with a pure function of the organisation, the virtual record, the block's
reconciliation hash and its occurrence number among blocks of the same hash. Nothing else feeds
it: no worker, attempt, clock, range size or random number.

Unchanged content therefore keeps its ids (and so its vector point ids, which derive from the
block id), and changed content gets different ids.
"""
from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from app.modules.reconciliation.service import ReconciliationService

if TYPE_CHECKING:
    from app.models.blocks import BlocksContainer

# Frozen: changing it re-keys every record on its next parse.
NS_BLOCK_V1 = uuid.uuid5(uuid.NAMESPACE_URL, "pipeshub:block-id:v1")

# Recorded in the reconciliation metadata of a record whose ids were assigned here.
ID_SCHEME = "content-v1"


def _derive(org_id: str, virtual_record_id: str, kind: str, content_hash: str, occurrence: int) -> str:
    return str(uuid.uuid5(NS_BLOCK_V1, f"{org_id}|{virtual_record_id}|{kind}|{content_hash}|{occurrence}"))


class BlockIdentity:
    @staticmethod
    def assign(container: BlocksContainer, org_id: str, virtual_record_id: str) -> bool:
        """Give every block and group of *container* its content-derived id. Idempotent.

        Returns False, leaving the ids as they are, when the organisation or the virtual record is
        unknown: an id must never be derived without the org that owns it.
        """
        if not org_id or not virtual_record_id:
            return False
        ReconciliationService.fill_content_hashes(container)
        for kind, items in (("g", container.block_groups), ("b", container.blocks)):
            seen: dict[str, int] = {}
            for item in items:
                content_hash = item.content_hash or f"pos:{item.index}"
                occurrence = seen.get(content_hash, 0)
                seen[content_hash] = occurrence + 1
                item.id = _derive(org_id, virtual_record_id, kind, content_hash, occurrence)
        return True
