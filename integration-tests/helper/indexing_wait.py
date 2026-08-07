# pyright: ignore-file

"""Shared poller for ``Record.indexing_status`` reaching COMPLETED.

Connector ITs use this to assert a record actually made it through the live
indexing pipeline — not just that it synced into the graph. The terminal-status
fail-fast matters as much as the COMPLETED case: a parser that silently yields
no content lands on ``EMPTY`` rather than hanging, so the assertion names the
real failure instead of timing out.

Requires a working indexing stack with models configured (see
``helper/ai_models_setup.py``), otherwise records never leave NOT_STARTED.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any

from app.config.constants.arangodb import ProgressStatus  # type: ignore[import-not-found]
from app.models.entities import Record  # type: ignore[import-not-found]
from helper.graph_provider import GraphProviderProtocol  # type: ignore[import-not-found]

logger = logging.getLogger("indexing-wait")

INDEXING_WAIT_SEC = int(os.getenv("INTEGRATION_INDEXING_WAIT_SEC", "180"))

# Statuses the pipeline will not advance past.
RECORD_INDEXING_TERMINAL: frozenset[str] = frozenset(
    {
        ProgressStatus.COMPLETED.value,
        ProgressStatus.FAILED.value,
        ProgressStatus.FILE_TYPE_NOT_SUPPORTED.value,
        ProgressStatus.EMPTY.value,
        ProgressStatus.AUTO_INDEX_OFF.value,
        ProgressStatus.ENABLE_MULTIMODAL_MODELS.value,
    }
)


async def wait_until_record_indexing_completed(
    graph_provider: GraphProviderProtocol,
    connector_id: str,
    external_record_id: str,
    *,
    timeout: int = INDEXING_WAIT_SEC,
    poll_interval: int = 5,
    description: str = "record indexing COMPLETED",
    pipeshub_client: Any | None = None,
) -> Record:
    """Poll the graph until the record reaches ``indexingStatus == COMPLETED``.

    If ``pipeshub_client`` is set and the record hits ``AUTO_INDEX_OFF`` once,
    triggers ``reindex_record`` and continues polling so auto-index can run again.

    Raises:
        AssertionError: If a terminal non-COMPLETED status is observed.
        TimeoutError: If COMPLETED is not reached within ``timeout`` seconds.
    """
    start = time.time()
    deadline = start + timeout
    attempt = 0
    last_status: str | None = None
    reindexed_after_auto_index_off = False

    while time.time() < deadline:
        attempt += 1
        rec = await graph_provider.get_record_by_external_id(connector_id, external_record_id)
        if rec is not None:
            last_status = rec.indexing_status
            if last_status == ProgressStatus.COMPLETED.value:
                logger.info(
                    "✅ %s COMPLETED (attempt %d, %.1fs)",
                    description, attempt, time.time() - start,
                )
                return rec
            if last_status in RECORD_INDEXING_TERMINAL:
                if (
                    last_status == ProgressStatus.AUTO_INDEX_OFF.value
                    and pipeshub_client is not None
                    and not reindexed_after_auto_index_off
                ):
                    logger.info("🔄 %s — AUTO_INDEX_OFF, triggering reindex", description)
                    pipeshub_client.reindex_record(rec.id)
                    reindexed_after_auto_index_off = True
                    await asyncio.sleep(8)
                    continue
                raise AssertionError(
                    f"{description}: record {external_record_id!r} reached terminal "
                    f"indexingStatus={last_status!r} (expected COMPLETED)"
                )
        remaining = deadline - time.time()
        if remaining <= 0:
            break
        sleep_time = min(poll_interval, remaining)
        logger.info(
            "⏳ %s — status=%s (attempt %d, %.0fs left)",
            description, last_status or "pending", attempt, remaining,
        )
        await asyncio.sleep(sleep_time)

    raise TimeoutError(
        f"Timed out waiting for {description} on externalRecordId={external_record_id!r} "
        f"after {timeout}s (last indexingStatus={last_status!r}, attempts={attempt})"
    )
