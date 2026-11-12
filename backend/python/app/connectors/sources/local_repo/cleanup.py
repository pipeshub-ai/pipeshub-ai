"""Remove a Local Repository connector's mirror when the connector is deleted (through the connectors API or ``DELETE /api/v1/local-repos``)."""
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from app.connectors.sources.local_folder.connector import _cfg
from app.connectors.sources.local_repo import store

if TYPE_CHECKING:
    from logging import Logger


async def remove_mirror(config_service: Any, logger: Logger, org_id: str, owner_id: str | None, connector_id: str) -> bool:  # noqa: ANN401
    """Delete the pushed files of ``connector_id``. True when none are left; a failure is logged (the files stay) and returns False.

    The mirror is addressed as the connector's own sync does: from the org, the owner who created the connector and the repository key in its
    configuration. When the owner or the key is not on record (an old connector), it is found by the connector id that each repository records,
    inside the org. A failure to READ the configuration is not handled here: it propagates, so the delete fails instead of leaving the files."""
    config = await config_service.get_config(f"/services/connectors/{connector_id}/config")
    key = str(_cfg(((config or {}).get("sync") or {}), store.REPO_KEY, "")).strip()
    try:
        repos = [store.Repo(org_id, owner_id, key)] if key and owner_id else await asyncio.to_thread(store.find_by_connector, org_id, connector_id)
        for repo in repos:
            await asyncio.to_thread(repo.destroy, connector_id)
        return True
    except (store.StoreError, OSError, ValueError):
        logger.error("local repo: the mirror of connector %s could not be removed; its files stay on the server", connector_id, exc_info=True)
        return False
