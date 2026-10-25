"""Finds what PipesHub still holds that Linear no longer has in the sync's scope.

Linear offers no feed of deletions. An issue removed for good, an issue moved to a team
outside the sync, a deleted attachment or document, a deleted team: none of them show up
in "changed since" listings. The reconciler lists ids on both sides and removes what
Linear no longer returns.

What the sync filters leave out (a deselected team, an issue outside a date filter) is
not removed here. Changing a filter runs a full sync, after which the platform unlinks
what that sync did not write: the records stay, and nobody can read them.

Safety rules:
  * Ids are compared only against a listing that was read to its end; a failed or
    partial listing raises and nothing is removed.
  * An issue missing from the listing is looked up by id before it is removed.
  * A team is removed only on a definite answer: not found, or archived.
"""

from collections.abc import Callable, Iterable
from logging import Logger
from typing import Any

from app.config.constants.arangodb import CollectionNames
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.connectors.core.base.sync_point.sync_point import SyncPoint
from app.connectors.sources.linear.api_gateway import LinearGateway, LinearNotFound
from app.connectors.sources.linear.record_removal import RecordRemover
from app.models.entities import Record, RecordType

ID_PAGE = 250
PROJECT_PAGE = 25  # the project listing also carries each project's link and milestone ids
CONFIRM_BATCH = 50
# A pass that needed more than this many API calls is repeated once a day, not every sync.
CHEAP_PASS_CALLS = 200
EXPENSIVE_PASS_INTERVAL_MS = 24 * 3600 * 1000
SYNC_POINT_KEY = "reconciliation"


class SourceReconciler:
    def __init__(
        self,
        gateway: LinearGateway,
        processor: DataSourceEntitiesProcessor,
        remover: RecordRemover,
        sync_point: SyncPoint,
        connector_id: str,
        logger: Logger,
        now: Callable[[], int],
    ) -> None:
        self._gateway = gateway
        self._processor = processor
        self._remover = remover
        self._sync_point = sync_point
        self._connector_id = connector_id
        self._logger = logger
        self._now = now
        self._calls = 0

    # ------------------------------------------------------------------ teams

    async def remove_stale_teams(self, synced_team_ids: set[str]) -> list[str]:
        """Remove stored teams Linear deleted, archived or hides from the sync account.
        Returns the removed team ids.

        A team is never judged gone merely because a listing did not return it, or
        because the team filter leaves it out: it is looked up by id.
        """
        stored = await self._processor.get_nodes_by_filters(
            CollectionNames.RECORD_GROUPS.value, {"connectorId": self._connector_id},
            ["externalGroupId", "isDeletedAtSource"])
        removed: list[str] = []
        for doc in stored or []:
            team_id = doc.get("externalGroupId")
            if not team_id or team_id in synced_team_ids:
                continue
            if doc.get("isDeletedAtSource"):
                continue  # already removed; kept only until its records leave the trash
            if not await self._team_is_gone(team_id):
                continue
            await self._remover.remove_group(team_id)
            removed.append(team_id)
        return removed

    async def _team_is_gone(self, team_id: str) -> bool:
        """Deleted, archived, or no longer visible to the sync account. A team Linear
        still returns by id is kept, whatever the listing said."""
        try:
            data = await self._gateway.call("team", {"id": team_id})
        except LinearNotFound:
            return True
        team = data.get("team") or {}
        return not team or bool(team.get("archivedAt"))

    # ------------------------------------------------------------------ cadence

    async def due(self, *, forced: bool) -> bool:
        if forced:
            return True
        state = await self._sync_point.read_sync_point(SYNC_POINT_KEY) or {}
        last_run, last_calls = state.get("last_run"), state.get("calls") or 0
        if not last_run or last_calls <= CHEAP_PASS_CALLS:
            return True
        return self._now() - last_run >= EXPENSIVE_PASS_INTERVAL_MS

    async def _ids(
        self, operation: str, root: str, variables: dict[str, Any], page_size: int = ID_PAGE
    ) -> list[dict[str, Any]]:
        nodes: list[dict[str, Any]] = []
        async for page, _, _ in self._gateway.pages(operation, [root], variables, page_size=page_size):
            self._calls += 1
            nodes.extend(page)
        return nodes

    # ------------------------------------------------------------------ the pass

    async def _stored(self, record_type: RecordType, scope: list[str]) -> list[Record]:
        """This connector's live records of one type that are filed under a synced team."""
        records = await self._processor.get_records_by_record_type(self._connector_id, record_type)
        return [
            r for r in records or []
            if r.external_record_group_id in scope
            and getattr(r, "is_placeholder", False) is not True
            and getattr(r, "is_deleted", False) is not True
        ]

    async def run_projects(
        self, team_ids: Iterable[str]
    ) -> tuple[int, dict[str, dict[str, Any]], dict[str, Record]]:
        """Projects are few; they are compared on every sync.

        Returns how many were removed, what Linear listed (by id) and what is stored (by
        id), so the caller can also spot projects whose links or milestones changed.
        """
        scope = sorted(set(team_ids))
        if not scope:
            return 0, {}, {}
        stored = await self._stored(RecordType.PROJECT, scope)
        listed = {
            n["id"]: n for n in await self._ids(
                "projectIds", "projects",
                {"filter": {"accessibleTeams": {"some": {"id": {"in": scope}}}},
                 "includeArchived": True}, page_size=PROJECT_PAGE)
            if n.get("id") and not n.get("trashed")
        }
        removed = 0
        kept: dict[str, Record] = {}
        for record in stored:
            if record.external_record_id in listed:
                kept[record.external_record_id] = record
            else:
                await self._remover.remove_record(record)
                removed += 1
        return removed, listed, kept

    async def run(self, team_ids: Iterable[str]) -> dict[str, int]:
        """One pass over issues, attachments and documents of the synced teams.
        Returns counts of removals."""
        scope = sorted(set(team_ids))
        self._calls = 0
        counts = {"issues": 0, "attachments": 0, "documents": 0}
        if not scope:
            return counts

        counts["issues"] = await self._reconcile_issues(scope, await self._stored(RecordType.TICKET, scope))
        counts["attachments"] = await self._reconcile_attachments(await self._stored(RecordType.LINK, scope))
        counts["documents"] = await self._reconcile_documents(await self._stored(RecordType.WEBPAGE, scope))

        await self._sync_point.update_sync_point(
            SYNC_POINT_KEY, {"last_run": self._now(), "calls": self._calls})
        if any(counts.values()):
            self._logger.info("Linear reconciliation removed %s", counts)
        return counts

    async def _reconcile_issues(self, scope: list[str], stored: list[Record]) -> int:
        if not stored:
            return 0
        live = {
            n["id"] for n in await self._ids(
                "issueIds", "issues",
                {"filter": {"team": {"id": {"in": scope}}}, "includeArchived": True})
            if n.get("id") and not n.get("trashed")
        }
        suspects = [r for r in stored if r.external_record_id not in live]
        removed = 0
        for start in range(0, len(suspects), CONFIRM_BATCH):
            batch = suspects[start:start + CONFIRM_BATCH]
            found = {
                n["id"]: n for n in await self._ids(
                    "issueIds", "issues",
                    {"filter": {"id": {"in": [r.external_record_id for r in batch]}},
                     "includeArchived": True})
            }
            for record in batch:
                node = found.get(record.external_record_id)
                team_id = ((node or {}).get("team") or {}).get("id")
                if node is not None and not node.get("trashed") and team_id in scope:
                    continue  # still there and still in a synced team: the listing was behind
                await self._remover.remove_record(record)
                removed += 1
        return removed

    async def _reconcile_attachments(self, stored: list[Record]) -> int:
        """Issue attachments only: a project's external links are compared when the
        project is read, and are skipped here by looking at the parent's type."""
        if not stored:
            return 0
        # An archived issue's attachments are archived with it, and are left out unless asked for
        live = {n["id"] for n in await self._ids("attachmentIds", "attachments", {"includeArchived": True}) if n.get("id")}
        removed = 0
        parent_types: dict[str, RecordType | None] = {}
        for record in stored:
            if record.external_record_id in live:
                continue
            parent_id = record.parent_external_record_id or ""
            if parent_id not in parent_types:
                parent = await self._processor.get_record_by_external_id(self._connector_id, parent_id) if parent_id else None
                parent_types[parent_id] = parent.record_type if parent else None
            if parent_types[parent_id] == RecordType.PROJECT:
                continue
            await self._remover.remove_record(record)
            removed += 1
        return removed

    async def _reconcile_documents(self, stored: list[Record]) -> int:
        if not stored:
            return 0
        # A deleted document is trashed (archived); an archived issue's documents are archived too
        live = {
            n["id"] for n in await self._ids("documentIds", "documents", {"includeArchived": True})
            if n.get("id") and not n.get("trashed")
        }
        removed = 0
        for record in stored:
            if record.external_record_id not in live:
                await self._remover.remove_record(record)
                removed += 1
        return removed


__all__ = ["SourceReconciler"]
