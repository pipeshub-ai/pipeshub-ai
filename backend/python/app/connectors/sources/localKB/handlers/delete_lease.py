"""A hint that a knowledge-base delete is running.

The owner's delete and the storage-release finisher both run the same idempotent
cascade, so correctness never depends on this key. The finisher reads it only to
avoid starting a second copy of work that is already under way; a lost or stale
lease costs at most one extra pass or one TTL of delay.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from typing import TYPE_CHECKING

from app.core.org_context import spawn_in_request

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from logging import Logger

    from app.config.configuration_service import ConfigurationService

LEASE_TTL_SECONDS = 120
RENEW_SECONDS = 30.0


def lease_key(kb_id: str) -> str:
    return f"/services/connectors/{kb_id}/deleteLease"


async def lease_is_held(config_service: ConfigurationService, kb_id: str) -> bool:
    """Whether a delete of this KB renewed its lease within the TTL. A failed read
    answers True: the finisher then waits a tick instead of racing a live delete."""
    try:
        return await config_service.get_config(lease_key(kb_id), use_cache=False, raise_on_error=True) is not None
    except Exception:
        return True


@contextlib.asynccontextmanager
async def hold_delete_lease(
    config_service: ConfigurationService | None,
    kb_id: str,
    owner: str,
    logger: Logger,
    *,
    renew_seconds: float = RENEW_SECONDS,
) -> AsyncIterator[None]:
    """Take (or overwrite) the lease and renew it while the body runs. Never raises."""
    value = {"owner": owner, "startedAt": int(time.time() * 1000)}
    task: asyncio.Task | None = None
    if config_service is not None:
        try:
            if not await config_service.create_config_if_absent(lease_key(kb_id), value, ttl_seconds=LEASE_TTL_SECONDS):
                await config_service.set_config(lease_key(kb_id), value, ttl_seconds=LEASE_TTL_SECONDS)
        except Exception:
            logger.warning("Could not take the delete lease of KB %s", kb_id, exc_info=True)

        async def renew() -> None:
            while True:
                await asyncio.sleep(renew_seconds)
                try:
                    await config_service.set_config(lease_key(kb_id), value, ttl_seconds=LEASE_TTL_SECONDS)
                except Exception:
                    logger.warning("Could not renew the delete lease of KB %s", kb_id, exc_info=True)

        task = spawn_in_request(renew(), name=f"kb-delete-lease-{kb_id}")
    try:
        yield
    finally:
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        if config_service is not None:
            with contextlib.suppress(Exception):
                await config_service.delete_config(lease_key(kb_id))
