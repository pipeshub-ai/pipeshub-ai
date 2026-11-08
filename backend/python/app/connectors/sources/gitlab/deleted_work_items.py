"""Removing issues and merge requests that GitLab no longer has.

The listings only return what exists, so a deleted issue or merge request is simply
never mentioned again. Each sync asks GitLab for one count per project. If it is lower
than the last count plus what was created since, something went away: then the ids
are swept, and each stored item missing from the sweep is looked up on its own. Only a
404 for that item removes it; any other answer keeps it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from app.config.constants.arangodb import CollectionNames, Connectors
from app.connectors.core.base.sync_point.sync_point import (
    generate_record_sync_point_key,
)
from app.models.entities import RecordType
from app.utils.time_conversion import get_epoch_timestamp_in_ms, parse_timestamp

from .common.utils import parse_item_id_from_url

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime

    from app.connectors.sources.gitlab.connector import GitLabConnector
    from app.sources.client.gitlab.gitlab import GitLabResponse

_NOT_FOUND = 404


@dataclass(frozen=True)
class WorkItemKind:
    label: str
    record_type: str
    list_method: str
    get_method: str
    iid_arg: str
    group_suffixes: tuple[str, ...]


ISSUES = WorkItemKind("issue", RecordType.TICKET.value, "list_issues", "get_issue", "issue_iid",
                      ("-work-items", "-confidential-work-items"))
MERGE_REQUESTS = WorkItemKind("merge request", RecordType.PULL_REQUEST.value, "list_merge_requests",
                              "get_merge_request", "mr_iid", ("-merge-requests",))


class DeletedWorkItems:
    """Finds the issues (or merge requests) of a project deleted on GitLab and removes them."""

    def __init__(self, connector: "GitLabConnector", kind: WorkItemKind) -> None:
        self.c = connector
        self.kind = kind
        self.logger = connector.logger

    def _key(self, project_id: int) -> str:
        return generate_record_sync_point_key(
            Connectors.GITLAB.value, f"{project_id}{self.kind.group_suffixes[0]}-deletion-check", ""
        )

    async def reconcile(
        self,
        project_id: int,
        *,
        listed: list[Any],
        listed_everything: bool,
        created_after: datetime | None,
        created_before: datetime | None,
    ) -> None:
        """Call after this sync's listing of the project was read and stored in full.
        ``listed_everything``: that listing had no update-time bound, so it holds every item."""
        c, kind = self.c, self.kind
        method = getattr(c.data_source, kind.list_method)
        checked_at = get_epoch_timestamp_in_ms()
        count = await c.runtime.ds_call(method, project_id=project_id, created_after=created_after,
                                        created_before=created_before, per_page=1, iterator=True)
        if not count.success or count.data is None:
            c.note_failed_read(f"the {kind.label} count of project {project_id}", count)
            return
        total = getattr(count.data, "total", None)
        if total is None:
            # GitLab leaves the count out above 10,000 items.
            self.logger.warning(
                "GitLab gave no %s count for project %s; deleted %ss are not looked for this sync",
                kind.label, project_id, kind.label,
            )
            return
        state = await c.record_sync_point.read_sync_point(self._key(project_id)) or {}
        # A sweep that failed must run again: by the next sync the listing has moved past
        # the items created meanwhile, so the count alone can no longer show a deletion.
        if state.get("total") is not None and not state.get("sweep_pending"):
            since = int(state.get("checked_at") or 0)
            created = sum(1 for item in listed if parse_timestamp(item.created_at) > since)
            if total >= int(state["total"]) + created:
                await c.record_sync_point.update_sync_point(
                    self._key(project_id), {"total": total, "checked_at": checked_at})
                return
        present = {int(item.iid) for item in listed} if listed_everything else None
        if await self._sweep(project_id, method, created_after, created_before, present):
            await c.record_sync_point.update_sync_point(
                self._key(project_id), {"total": total, "checked_at": checked_at, "sweep_pending": False})
        else:
            await c.record_sync_point.update_sync_point(self._key(project_id), {**state, "sweep_pending": True})

    async def _sweep(self, project_id: int, method: Callable[..., GitLabResponse], created_after: datetime | None,
                     created_before: datetime | None, present: set[int] | None) -> bool:
        c, kind = self.c, self.kind
        stored = await self._stored(project_id)
        if not stored:
            return True
        if present is None:
            listing = await c.runtime.ds_call(method, project_id=project_id, created_after=created_after,
                                              created_before=created_before, per_page=100, get_all=True)
            if not listing.success:
                c.note_failed_read(f"the {kind.label} ids of project {project_id}", listing)
                return False
            present = {int(item.iid) for item in listing.data or []}
        complete = True
        for iid, record in stored.items():
            if iid in present:
                continue
            res = await c.runtime.ds_call(getattr(c.data_source, kind.get_method),
                                          project_id=project_id, **{kind.iid_arg: iid})
            if res.success:
                continue
            if res.status_code != _NOT_FOUND:
                complete = False
                c.note_failed_read(f"{kind.label} {iid} of project {project_id}", res)
                continue
            await self._remove(record)
        return complete

    async def _stored(self, project_id: int) -> dict[int, dict[str, Any]]:
        """Live stored items of this kind for the project, by iid."""
        c = self.c
        out: dict[int, dict[str, Any]] = {}
        async with c.data_store_provider.transaction() as tx_store:
            for suffix in self.kind.group_suffixes:
                nodes = await tx_store.get_nodes_by_filters(
                    collection=CollectionNames.RECORDS.value,
                    filters={"connectorId": c.connector_id, "externalGroupId": f"{project_id}{suffix}",
                             "recordType": self.kind.record_type},
                    return_fields=["_key", "externalRecordId", "webUrl", "isDeleted"],
                )
                for node in nodes:
                    if node.get("isDeleted"):
                        continue
                    try:
                        iid = parse_item_id_from_url(node.get("webUrl") or "")
                    except ValueError:
                        continue
                    out[iid] = node
        return out

    async def _remove(self, node: dict[str, Any]) -> None:
        """The item and the attachment records filed under it."""
        c = self.c
        record_id = node.get("id") or node.get("_key")
        external_id = node.get("externalRecordId")
        self.logger.info("Removing %s %s: deleted on GitLab", self.kind.label, external_id)
        if external_id:
            for child in await c.data_entities_processor.get_records_by_parent(c.connector_id, external_id):
                if child.record_type == RecordType.FILE.value or child.record_type == RecordType.FILE:
                    await c.data_entities_processor.on_record_deleted(child.id)
        await c.data_entities_processor.on_record_deleted(record_id)
