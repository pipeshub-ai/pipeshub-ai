"""Removing the selected project once GitLab says it is gone.

GitLab answers 404 for a project that was deleted and for one the sync account can no
longer read. Either way nobody may keep reading it through PipesHub. Any other failure
(401, 5xx, a timeout) says nothing about the project and removes nothing.

A renamed or transferred project also answers 404 by its old path when the redirect
GitLab sends cannot be followed (gitlab.com breaks it at the first dot of the path), so
the project is looked up by its id before anything is removed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.config.constants.arangodb import CollectionNames, Connectors
from app.connectors.core.base.sync_point.sync_point import (
    generate_record_sync_point_key,
)

if TYPE_CHECKING:
    from app.connectors.sources.gitlab.connector import GitLabConnector
    from gitlab.v4.objects import Project

_PROJECT_GROUP_SUFFIXES = ("-work-items", "-merge-requests", "-code-repository")


def _ref_key(project_path: str) -> str:
    return generate_record_sync_point_key(Connectors.GITLAB.value, f"project-ref:{project_path}", "")


class RemovedProjects:
    """Remembers which project id a configured path resolved to, and removes that project
    with everything under it when the path answers 404."""

    def __init__(self, connector: "GitLabConnector") -> None:
        self.c = connector
        self.logger = connector.logger

    async def remember(self, project_path: str, project_id: int) -> None:
        await self.c.record_sync_point.update_sync_point(_ref_key(project_path), {"project_id": project_id})

    async def _stored_project_id(self, project_path: str) -> str | None:
        """A project synced before its path was remembered: its group is named after the path."""
        c = self.c
        async with c.data_store_provider.transaction() as tx_store:
            nodes = await tx_store.get_nodes_by_filters(
                collection=CollectionNames.RECORD_GROUPS.value,
                filters={"connectorId": c.connector_id, "name": project_path},
            )
        ids = [str(n.get("externalGroupId")) for n in nodes if str(n.get("externalGroupId") or "").isdigit()]
        return ids[0] if len(ids) == 1 else None

    async def moved_or_remove(self, project_path: str) -> Project | None:
        """The configured path answered 404. Returns the project if it still exists under
        its id (renamed or transferred); removes it if the id answers 404 too."""
        c = self.c
        state = await c.record_sync_point.read_sync_point(_ref_key(project_path)) or {}
        project_id = state.get("project_id") or await self._stored_project_id(project_path)
        if project_id is None:
            return None
        res = await c.runtime.ds_call(c.data_source.get_project, int(project_id))
        if res.success and res.data:
            self.logger.warning(
                "GitLab project %s moved to %s: syncing it by id. Select the new path in the connector's filter.",
                project_path, getattr(res.data, "path_with_namespace", project_id),
            )
            return res.data
        if res.status_code != 404:
            c.note_failed_read(f"project {project_path} (id {project_id})", res)
            return None
        await self._remove(project_path, project_id)
        return None

    async def _remove(self, project_path: str, project_id: int | str) -> None:
        c = self.c
        self.logger.warning(
            "GitLab answers 404 for project %s (id %s): removing it from PipesHub", project_path, project_id,
        )
        # The project group holds the work-item, merge-request and code groups below it.
        stored = await c.data_entities_processor.get_record_group_by_external_id(c.connector_id, str(project_id))
        if stored is not None and not await c.data_entities_processor.on_record_group_deleted(
            str(project_id), c.connector_id
        ):
            c.note_sync_problem(f"could not remove project {project_path} after GitLab answered 404")
            return
        for suffix in (*_PROJECT_GROUP_SUFFIXES, "-work-items-deletion-check", "-merge-requests-deletion-check"):
            await c.record_sync_point.delete_sync_point(
                generate_record_sync_point_key(Connectors.GITLAB.value, f"{project_id}{suffix}", ""))
        await c.record_sync_point.delete_sync_point(_ref_key(project_path))
