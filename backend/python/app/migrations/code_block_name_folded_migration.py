"""One-time backfill of ``nameFolded`` on code blocks projected before it existed (SC-6).

A bare-name symbol lookup matches ``nameFolded`` exactly; until a block has it,
the lookup falls back to the capped text search. New and re-indexed files get
the field from projection; this fills in the rest in batches. Idempotent: a
second replica running it at the same time finds fewer blocks and stops.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from logging import Logger

    from app.config.configuration_service import ConfigurationService
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

MIGRATION_FLAG_KEY = "/migrations/code_block_name_folded_v1"
_BATCH = 5000


async def run_code_block_name_folded_backfill(
    config_service: ConfigurationService,
    graph_provider: IGraphDBProvider,
    logger: Logger,
) -> int:
    """Backfill every block, then set the flag. Returns how many blocks were updated."""
    try:
        flag = await config_service.get_config(MIGRATION_FLAG_KEY, use_cache=False)
    except Exception:
        flag = None
    if isinstance(flag, dict) and flag.get("done") is True:
        return 0
    total = 0
    while updated := await graph_provider.backfill_block_name_folded(_BATCH):
        total += updated
    try:
        await config_service.set_config(MIGRATION_FLAG_KEY, {"done": True, "blocks": total})
    except Exception:
        logger.warning("Code block nameFolded backfill finished but its flag was not saved; "
                       "it will check again on the next start", exc_info=True)
    logger.info("Code block nameFolded backfill: %d block(s) updated", total)
    return total
