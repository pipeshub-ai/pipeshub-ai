"""What the Entra group syncs of OneDrive and SharePoint do once their saved delta link has expired.

Only the link reports deleted groups. With it gone, the groups deleted since it was taken are found by asking for
the stored ones that the directory no longer lists: left alone, they would go on giving their members access.
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING

from msgraph.generated.models.o_data_errors.o_data_error import ODataError

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from logging import Logger

    from app.connectors.core.base.data_processor.data_source_entities_processor import (
        DataSourceEntitiesProcessor,
    )
    from app.connectors.sources.microsoft.common.msgraph_client import MSGraphClient

GROUPS_READ_AGAIN = (
    "Microsoft no longer accepts the saved position in this directory's group changes (it is kept for 30 days), so "
    "every group was read again. Groups deleted in the meantime were removed from PipesHub."
)

# An Entra group is stored under its object id. SharePoint site groups and the "everyone" groups are not.
_OBJECT_ID = re.compile(r"[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}", re.IGNORECASE)
_NOT_FOUND = 404


async def remove_groups_gone_from_directory(
    processor: DataSourceEntitiesProcessor, connector_id: str, graph: MSGraphClient,
    delete: Callable[[str], Awaitable[bool]], logger: Logger,
) -> int:
    """Remove the stored Entra groups the directory confirms it no longer has. Returns how many.

    Raises when the groups could not be read or one could not be removed, so the caller keeps the old link and
    the next run tries again. A group that could not be looked up is kept.
    """
    stored = await processor.get_all_user_groups(connector_id, raise_on_error=True)
    candidates = {
        g.source_user_group_id for g in stored
        if g.source_user_group_id and _OBJECT_ID.fullmatch(g.source_user_group_id)
    }
    if candidates:
        candidates -= {g.id for g in await graph.get_all_user_groups()}
    removed = 0
    for group_id in sorted(candidates):
        try:
            async with graph.rate_limiter:
                await graph.client.groups.by_group_id(group_id).get()
            continue
        except ODataError as e:
            if e.response_status_code != _NOT_FOUND:
                logger.warning("Entra group %s could not be looked up; it is kept", group_id)
                continue
        if not await delete(group_id):
            raise RuntimeError(f"Entra group {group_id} is gone from the directory and could not be removed")
        removed += 1
    return removed
