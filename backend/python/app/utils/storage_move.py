"""One call to the storage service's move-tree endpoint, shared by the writer's
relocation (BlobStorage) and connector-side moves (StorageCleanupHelper)."""

from typing import Any

import aiohttp

from app.config.constants.http_status_code import HttpStatusCode
from app.config.constants.service import Routes


async def post_move_tree(
    session: aiohttp.ClientSession,
    nodejs_endpoint: str,
    headers: dict[str, str],
    old_path: str,
    new_path: str,
    *,
    virtual_record_id: str | None = None,
    virtual_record_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Move what is stored under *old_path* to *new_path*; raises on a non-200.

    *virtual_record_ids* (the folder form) names everything the mover owns
    beneath old_path; the endpoint leaves anything else in place.
    """
    body: dict[str, Any] = {"oldPath": old_path, "newPath": new_path}
    if virtual_record_id:
        body["virtualRecordId"] = virtual_record_id
    elif virtual_record_ids is not None:
        body["virtualRecordIds"] = virtual_record_ids
    async with session.post(
        f"{nodejs_endpoint}{Routes.STORAGE_MOVE_TREE.value}", json=body, headers=headers,
    ) as resp:
        if resp.status != HttpStatusCode.SUCCESS.value:
            raise Exception(f"move-tree failed: {resp.status} {(await resp.text())[:200]}")
        result = await resp.json()
    return result if isinstance(result, dict) else {"moved": 0}
