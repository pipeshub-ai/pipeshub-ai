"""
Removing repositories that have left the sync's scope.

A repository leaves the scope when the admin selects another one, when it is deleted
on GitHub, or when the token can no longer read it. Its records, record groups and
checkpoints must go with it: left behind, they keep the permissions of the last sync
for good (or, after a full sync, sit unreachable in the index).

Only a complete scope resolution may remove anything. The caller passes the repository
ids it resolved and says whether the resolution was complete: every filter entry
answered 200 or a definite 404, and every org listing succeeded.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.config.constants.arangodb import CollectionNames, Connectors
from app.connectors.core.base.sync_point.sync_point import (
    generate_record_sync_point_key,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from logging import Logger

    from app.connectors.core.base.data_processor.data_source_entities_processor import (
        DataSourceEntitiesProcessor,
    )
    from app.connectors.core.base.sync_point.sync_point import SyncPoint

    from .record_removal import RecordRemover

REPO_CHILD_GROUP_SUFFIXES = ("-work-items", "-pull-requests", "-code-repository")


def org_group_id(org_numeric_id: int) -> str:
    return f"org-{org_numeric_id}"


class RepoScopeReconciler:
    """Removes stored repositories and org groups the current scope no longer holds."""

    def __init__(
        self,
        processor: DataSourceEntitiesProcessor,
        remover: RecordRemover,
        sync_point: SyncPoint,
        connector_id: str,
        logger: Logger,
    ) -> None:
        self._processor = processor
        self._remover = remover
        self._sync_point = sync_point
        self._connector_id = connector_id
        self._logger = logger

    async def remove_out_of_scope(
        self,
        repo_ids: set[int],
        still_in_scope: Callable[[int], Awaitable[bool | None]] | None = None,
    ) -> bool:
        """Remove the repositories the scope no longer holds; return False when one was
        kept on doubt.

        ``still_in_scope`` re-asks GitHub about each repository about to be removed:
        a listing paged by offset can drop a live repository when another one is
        deleted while it is read. True or None (no clear answer) keeps it.
        """
        stored = await self._stored_group_ids()
        kept_on_doubt = False
        for external_id in sorted(stored):
            if external_id.isdigit() and int(external_id) not in repo_ids:
                if still_in_scope is not None and (await still_in_scope(int(external_id))) is not False:
                    self._logger.warning(
                        "GitHub repository id=%s is missing from the scope listing but GitHub "
                        "still answers for it; keeping it this sync.", external_id,
                    )
                    kept_on_doubt = True
                    continue
                await self._remove_repo(external_id)
        return not kept_on_doubt

    async def remove_empty_orgs(self, org_ids: set[int]) -> None:
        """Remove org groups no repository in scope belongs to.

        Called only after every repository synced: deleting an org group takes the
        groups still under it, and a repository transferred to another org is moved
        under its new org only when it syncs.
        """
        for external_id in sorted(await self._stored_group_ids()):
            if external_id.startswith("org-") and external_id[4:].isdigit() and int(external_id[4:]) not in org_ids:
                await self._processor.on_record_group_deleted(external_id, self._connector_id)

    async def _stored_group_ids(self) -> set[str]:
        groups = await self._processor.get_nodes_by_filters(
            CollectionNames.RECORD_GROUPS.value, {"connectorId": self._connector_id}
        )
        # A group kept for the trash is already marked gone; the purge removes it.
        return {str(g.get("externalGroupId") or "") for g in groups if not g.get("isDeletedAtSource")}

    async def _remove_repo(self, repo_id: str) -> None:
        removed = 0
        for suffix in REPO_CHILD_GROUP_SUFFIXES:
            removed += await self._remover.remove_group(f"{repo_id}{suffix}")
            # A repository that comes back must be read from the start, not from the
            # watermark it had when it left.
            await self._sync_point.delete_sync_point(
                generate_record_sync_point_key(Connectors.GITHUB_TEAMS.value, f"{repo_id}{suffix}", "")
            )
        removed += await self._remover.remove_group(repo_id)
        self._logger.info(
            "GitHub repository id=%s left the sync's scope; removed it and %s record(s)", repo_id, removed
        )
