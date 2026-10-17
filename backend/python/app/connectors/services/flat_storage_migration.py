"""One-off move of stored copies still filed under the flat ``records/<vrid>``
layout (records indexed before hierarchical storage) into
``records/<connector>/<groups>/<record path>``.

Pattern match greps ``records/<connector>/`` only, so a flat copy is invisible
to it. Records that are indexed again are relocated by BlobStorage itself; this
covers the ones that never are.

Walks storage's listing of flat documents, not the graph: only flat copies cost
anything. One replica runs it (the Redis leader lock the connector sweeps use).
Progress is kept per org in the KV store, so a restart resumes from the last
page. A pass that hit failures is run again, up to MAX_PASSES; a copy with no
record left to own it stays where it is.
"""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from app.config.constants.http_status_code import HttpStatusCode
from app.config.constants.service import Routes
from app.connectors.core.base.data_processor.storage_cleanup import (
    StorageCleanupHelper,
    choose_storage_owner,
)
from app.utils.storage_move import post_move_tree
from app.utils.time_conversion import get_epoch_timestamp_in_ms

if TYPE_CHECKING:
    import logging

    from app.config.configuration_service import ConfigurationService
    from app.modules.indexing.vector_membership_backfill import LeaderLock
    from app.modules.transformers.blob_storage import BlobStorage

PROGRESS_DIRECTORY = "/services/storageMigration/flatToHierarchical/"
LEADER_KEY = "flat_storage_migration:leader"
MAX_PASSES = 3
_PAGE = 500


def _key(org_id: str) -> str:
    return f"{PROGRESS_DIRECTORY}{org_id}"


class FlatStorageMigration:
    def __init__(
        self,
        logger: logging.Logger,
        config_service: ConfigurationService,
        graph_provider: Any,  # noqa: ANN401 - IGraphDBProvider
        blob_storage: BlobStorage,
        lock: LeaderLock,
    ) -> None:
        self.logger = logger
        self.config_service = config_service
        self.graph_provider = graph_provider
        self.blob_storage = blob_storage
        self.lock = lock
        self.storage = StorageCleanupHelper(logger, graph_provider, config_service)

    async def run_all_orgs(self) -> bool:
        """True once every org is done; False when another replica leads or
        leadership was lost (the caller tries again later)."""
        if not await self.lock.try_acquire():
            return False
        try:
            for org in await self.graph_provider.get_all_orgs() or []:
                org_id = org.get("id") or org.get("_key")
                if org_id and not await self.run_org(str(org_id)):
                    return False
            return True
        finally:
            await self.storage.close()

    async def run_org(self, org_id: str) -> bool:
        """False when leadership was lost part way; the next leader resumes."""
        progress = await self.config_service.get_config(_key(org_id), use_cache=False)
        if not isinstance(progress, dict):
            progress = {}
        if progress.get("done"):
            return True
        headers, nodejs_endpoint, _ = await self.blob_storage._get_auth_and_config(org_id)
        while True:
            page = await self._flat_documents(nodejs_endpoint, headers, progress.get("after"))
            vrids = list(OrderedDict.fromkeys(
                (doc.get("documentName") or "").split("_", 1)[-1] for doc in page
            ))
            if vrids:
                counts = await self._move_page(org_id, nodejs_endpoint, headers, vrids)
                for name, value in counts.items():
                    progress[name] = int(progress.get(name) or 0) + value
                progress["after"] = page[-1]["_id"]
                progress["updatedAt"] = get_epoch_timestamp_in_ms()
                await self.config_service.set_config(_key(org_id), progress)
                if not await self.lock.refresh():
                    return False
            if len(page) < _PAGE:
                break
        return await self._finish_pass(org_id, progress)

    async def _finish_pass(self, org_id: str, progress: dict[str, Any]) -> bool:
        failed = int(progress.get("failed") or 0)
        passes = int(progress.get("passes") or 0) + 1
        if failed and passes < MAX_PASSES:
            self.logger.warning(
                "Flat storage migration for org %s: %d copies failed to move; another pass (%d/%d)",
                org_id, failed, passes + 1, MAX_PASSES,
            )
            # Copies that moved left the flat listing, so the next pass sees only the rest.
            await self.config_service.set_config(_key(org_id), {"passes": passes})
            return await self.run_org(org_id)
        done = {**progress, "after": None, "passes": passes, "done": True}
        await self.config_service.set_config(_key(org_id), done)
        log = self.logger.error if failed else self.logger.info
        log(
            "Flat storage migration for org %s finished: %d moved, %d with no record to own them, "
            "%d failed%s",
            org_id, int(done.get("moved") or 0), int(done.get("unowned") or 0), failed,
            "; those are relocated when their records are indexed again" if failed else "",
        )
        return True

    async def _flat_documents(
        self, nodejs_endpoint: str, headers: dict[str, str], after: str | None,
    ) -> list[dict[str, Any]]:
        from app.modules.transformers.blob_storage import get_shared_session

        params = {"limit": str(_PAGE)}
        if after:
            params["after"] = after
        async with get_shared_session().get(
            f"{nodejs_endpoint}{Routes.STORAGE_FLAT_RECORD_DOCUMENTS.value}",
            headers=headers, params=params,
        ) as resp:
            if resp.status != HttpStatusCode.SUCCESS.value:
                raise Exception(f"listing flat documents failed: {resp.status}")
            return list((await resp.json()).get("documents") or [])

    async def _move_page(
        self, org_id: str, nodejs_endpoint: str, headers: dict[str, str], vrids: list[str],
    ) -> dict[str, int]:
        from app.modules.transformers.blob_storage import get_shared_session

        counts = {"moved": 0, "unowned": 0, "failed": 0}
        try:
            holders = await self.graph_provider.get_virtual_record_holders(vrids, org_id)
        except Exception as e:
            self.logger.warning("Flat storage migration: could not read holders of a page: %s", e)
            counts["failed"] = len(vrids)
            return counts
        for vrid in vrids:
            try:
                owner = await self._owner(org_id, vrid, holders.get(vrid) or [])
                if owner is None:
                    counts["unowned"] += 1
                    continue
                path = await self.storage._owner_storage_path(owner)
                if not path:
                    counts["failed"] += 1
                    continue
                result = await post_move_tree(
                    get_shared_session(), nodejs_endpoint, headers, f"records/{vrid}", path,
                    virtual_record_ids=[vrid],
                )
                counts["moved" if result.get("moved") and not result.get("failed") else "failed"] += 1
            except Exception as e:
                self.logger.warning("Flat copy of vrid %s left in place for now: %s", vrid, e)
                counts["failed"] += 1
        return counts

    async def _owner(self, org_id: str, vrid: str, holders: list[dict]) -> dict | None:
        """The record the copy is filed under: the one it describes while that
        record still holds it, else the one storage hands shared content to."""
        holders = [h for h in holders if h.get("id")]
        if not holders:
            return None
        if len(holders) > 1:
            stored = await self.blob_storage.get_record_from_storage(vrid, org_id)
            named = stored.get("id") if isinstance(stored, dict) else None
            for holder in holders:
                if holder["id"] == named and not holder.get("isDeleted"):
                    return holder
        return choose_storage_owner(holders)


async def run_flat_storage_migration(
    app_container: Any,  # noqa: ANN401
    graph_provider: Any,  # noqa: ANN401
    *,
    startup_grace_seconds: float = 180,
    retry_seconds: float = 600,
) -> None:
    """Run the migration on whichever replica holds the lock, until every org is done."""
    from app.modules.indexing.vector_membership_backfill import (
        VectorMembershipBackfillLeaderLock,
    )
    from app.modules.transformers.blob_storage import BlobStorage
    from app.services.messaging.utils import MessagingUtils

    logger = app_container.logger()
    config_service = app_container.config_service()
    owner = f"flat-storage-migration:{uuid4().hex}"
    await asyncio.sleep(startup_grace_seconds)
    while True:
        lock = None
        try:
            redis_config = await MessagingUtils._get_redis_config(app_container)
            lock = VectorMembershipBackfillLeaderLock(logger, redis_config, owner, key=LEADER_KEY)
            migration = FlatStorageMigration(
                logger, config_service, graph_provider,
                BlobStorage(logger, config_service, graph_provider), lock,
            )
            if await migration.run_all_orgs():
                return
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error("Flat storage migration stopped; it resumes later: %s", e)
        finally:
            if lock is not None:
                await lock.release()
                await lock.close()
        await asyncio.sleep(retry_seconds)
