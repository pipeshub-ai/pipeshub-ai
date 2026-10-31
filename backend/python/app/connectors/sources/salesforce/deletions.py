"""Take out of PipesHub what was deleted in Salesforce since the last sync.

Salesforce's getDeleted resource lists, per object, the ids deleted in a window (recycle
bin and hard deletes alike) for up to 15 days, or less once the org's delete log is purged
(``earliestDateAvailable``). When the window since the last sync is no longer covered,
the object's live ids are listed in full instead and whatever PipesHub holds that is not
among them is removed.

Only a definite answer removes anything: a getDeleted response that lists the id, or a
listing that ran to the end. A failed or partial read raises, so the sync fails, nothing
is removed and the checkpoint stays where it was.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from logging import Logger
from typing import Any, AsyncIterator, Callable, Iterable, Optional

from app.config.constants.arangodb import CollectionNames
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.connectors.core.base.data_store.data_store import DataStoreProvider
from app.connectors.core.base.sync_point.sync_point import SyncPoint
from app.connectors.sources.salesforce.sync_window import checkpoint_for
from app.models.entities import RecordGroupType, RecordType
from app.sources.external.salesforce.salesforce_data_source import SalesforceDataSource
from app.utils.time_conversion import parse_timestamp

# Objects whose records PipesHub keeps, with the record type each becomes.
TRACKED: dict[str, Optional[RecordType]] = {
    "Opportunity": RecordType.DEAL,
    "Case": RecordType.CASE,
    "Task": RecordType.TASK,
    "Product2": RecordType.PRODUCT,
    "ContentDocument": None,   # its FILE records are "<document id>-<linked record id>"
    "Account": None,           # a record group, and the files linked to it
}

# getDeleted needs start more than a minute before end (seconds are ignored).
_MIN_WINDOW_MS = 2 * 60 * 1000


class DeletionReadError(RuntimeError):
    """Salesforce did not give a definite answer; nothing was removed."""


@dataclass(frozen=True)
class DeletedWindow:
    ids: list[str]
    covered_ms: Optional[int]   # latestDateCovered
    window_lost: bool           # the delete log no longer reaches back to our start


# (SOQL, through queryAll) -> pages of rows; a page that fails raises.
IdPages = Callable[[str, bool], AsyncIterator[list[dict[str, Any]]]]

_SF_ID = re.compile(r"^[a-zA-Z0-9]{15}([a-zA-Z0-9]{3})?$")
_IN_BATCH = 300


class SalesforceDeletions:
    """Removes deleted Salesforce records from PipesHub, one object at a time."""

    def __init__(
        self,
        data_source: SalesforceDataSource,
        processor: DataSourceEntitiesProcessor,
        data_store_provider: DataStoreProvider,
        sync_point: SyncPoint,
        connector_id: str,
        logger: Logger,
        now_ms: Callable[[], int],
        list_ids: IdPages,
    ) -> None:
        self._source = data_source
        self._processor = processor
        self._store = data_store_provider
        self._sync_point = sync_point
        self._connector_id = connector_id
        self._logger = logger
        self._now_ms = now_ms
        self._list_ids = list_ids

    async def run(self, api_version: str, run_started_ms: int, *, full_run: bool = False) -> None:
        """``full_run``: this sync read everything that exists, so only deletions from its
        start on are owed. An incremental run with no checkpoint (an install upgraded from a
        version that did not track deletions) lists every object once instead."""
        for sobject in TRACKED:
            await self._run_one(api_version, sobject, run_started_ms, full_run=full_run)

    # ------------------------------------------------------------------ one object

    async def _run_one(self, api_version: str, sobject: str, run_started_ms: int, *, full_run: bool = False) -> None:
        key = f"deleted_{sobject}"
        since = (await self._sync_point.read_sync_point(key) or {}).get("lastSyncTimestamp")
        if not since:
            if not full_run:
                # Upgraded install: whatever went before now was never removed.
                await self._reconcile(sobject)
            await self._sync_point.update_sync_point(key, {"lastSyncTimestamp": checkpoint_for(run_started_ms)})
            return
        end_ms = self._now_ms()
        if end_ms - since < _MIN_WINDOW_MS:
            return
        window = await self._read_deleted(api_version, sobject, since, end_ms)
        if window.window_lost:
            self._logger.warning(
                "Salesforce's delete log for %s no longer reaches back to the last sync; "
                "listing every %s to find what was deleted", sobject, sobject,
            )
            await self._reconcile(sobject)
            covered = end_ms
        else:
            await self._remove(sobject, window.ids)
            covered = window.covered_ms or end_ms
        await self._sync_point.update_sync_point(key, {"lastSyncTimestamp": checkpoint_for(covered)})

    async def _read_deleted(self, api_version: str, sobject: str, start_ms: int, end_ms: int) -> DeletedWindow:
        response = await self._source.s_object_get_deleted(
            sobject_api_name=sobject,
            version=api_version,
            start=_iso(start_ms),
            end=_iso(end_ms),
        )
        if not response.success:
            detail = f"{response.message or ''} {response.error or ''}"
            if "INVALID_REPLICATION_DATE" in detail:
                return DeletedWindow([], None, True)
            if response.status_code in (400, 404) and "REQUEST_LIMIT_EXCEEDED" not in detail:
                # An object Salesforce does not replicate has no delete log: list it instead.
                self._logger.warning("getDeleted is not available for %s (%s); listing it instead",
                                     sobject, response.status_code)
                return DeletedWindow([], None, True)
            raise DeletionReadError(
                f"getDeleted for {sobject} failed ({response.status_code}): {response.error}"
            )
        data = response.data if isinstance(response.data, dict) else {}
        rows = data.get("deletedRecords")
        if not isinstance(rows, list):
            raise DeletionReadError(f"getDeleted for {sobject} returned no deletedRecords list")
        earliest = _ms(data.get("earliestDateAvailable"))
        if earliest is not None and earliest > start_ms:
            return DeletedWindow([], None, True)
        ids = [str(r["id"]) for r in rows if isinstance(r, dict) and r.get("id")]
        return DeletedWindow(ids, _ms(data.get("latestDateCovered")), False)

    # ------------------------------------------------------------------ removal

    async def _remove(self, sobject: str, ids: list[str]) -> None:
        if not ids:
            return
        # A record restored from the recycle bin since it was deleted is live again; the
        # log still lists the delete. Only ids a normal query no longer returns go.
        ids = await self._still_deleted(sobject, ids)
        if not ids:
            return
        record_type = TRACKED[sobject]
        removed = 0
        if record_type is not None:
            for node in await self._records_by(field="externalRecordId", values=ids):
                if node.get("recordType") == record_type.value:
                    removed += await self._delete(node)
            # Files filed under a deleted record: Salesforce drops the record's links.
            for node in await self._records_by(field="externalParentId", values=ids):
                if node.get("recordType") == RecordType.FILE.value:
                    removed += await self._delete(node)
        elif sobject == "ContentDocument":
            doc_ids = {i[:15] for i in ids}
            for node in await self._file_records():
                if str(node.get("externalRecordId", "")).split("-")[0][:15] in doc_ids:
                    removed += await self._delete(node)
        elif sobject == "Account":
            removed += await self._remove_accounts(ids)
        if removed:
            self._logger.info("Removed %d record(s) for %d deleted %s", removed, len(ids), sobject)

    async def _remove_accounts(self, account_ids: Iterable[str]) -> int:
        removed = 0
        wanted = set(account_ids)
        for node in await self._file_records():
            # Only files attached to the account itself: a file under one of its deals sits in
            # the same group and goes (or moves) with that deal.
            if node.get("externalGroupId") in wanted and not node.get("externalParentId"):
                removed += await self._delete(node)
        for account_id in wanted:
            # The account's deals, cases and tasks are deleted (or moved) on their own; the
            # group goes once nothing is left in it.
            if not await self._group_has_records(account_id):
                await self._processor.on_record_group_deleted(account_id, self._connector_id)
        return removed

    async def _reconcile(self, sobject: str) -> None:
        live: set[str] = set()
        async for page in self._list_ids(*_live_ids_query(sobject)):
            live.update(str(r["Id"])[:15] for r in page if r.get("Id"))
        record_type = TRACKED[sobject]
        gone: list[str] = []
        if record_type is not None:
            for node in await self._records_of_type(record_type):
                ext = str(node.get("externalRecordId") or "")
                if ext and ext[:15] not in live:
                    gone.append(ext)
        elif sobject == "ContentDocument":
            for node in await self._file_records():
                doc = str(node.get("externalRecordId") or "").split("-")[0]
                if doc and doc[:15] not in live:
                    gone.append(doc)
        elif sobject == "Account":
            async with self._store.transaction() as tx_store:
                groups = await tx_store.get_nodes_by_field_in(
                    collection=CollectionNames.RECORD_GROUPS.value, field="connectorId",
                    values=[self._connector_id],
                )
            gone = [g["externalGroupId"] for g in groups or []
                    if g.get("groupType") == RecordGroupType.SALESFORCE_ORG.value
                    and g.get("externalGroupId") and str(g["externalGroupId"])[:15] not in live]
        await self._remove(sobject, sorted(set(gone)))

    async def _still_deleted(self, sobject: str, ids: list[str]) -> list[str]:
        live: set[str] = set()
        valid = [i for i in dict.fromkeys(ids) if _SF_ID.match(i)]
        for start in range(0, len(valid), _IN_BATCH):
            batch = valid[start:start + _IN_BATCH]
            soql, query_all = _live_ids_query(sobject, "Id IN ('" + "','".join(batch) + "')")
            async for page in self._list_ids(soql, query_all):
                live.update(str(r["Id"])[:15] for r in page if r.get("Id"))
        return [i for i in valid if i[:15] not in live]

    # ------------------------------------------------------------------ graph reads

    async def _records_by(self, *, field: str, values: list[str]) -> list[dict[str, Any]]:
        async with self._store.transaction() as tx_store:
            nodes = await tx_store.get_nodes_by_field_in(
                collection=CollectionNames.RECORDS.value, field=field, values=values,
            )
        return [n for n in nodes or [] if n.get("connectorId") == self._connector_id]

    async def _records_of_type(self, record_type: RecordType) -> list[dict[str, Any]]:
        async with self._store.transaction() as tx_store:
            nodes = await tx_store.get_nodes_by_field_in(
                collection=CollectionNames.RECORDS.value, field="recordType", values=[record_type.value],
            )
        return [n for n in nodes or [] if n.get("connectorId") == self._connector_id]

    async def _file_records(self) -> list[dict[str, Any]]:
        return await self._records_of_type(RecordType.FILE)

    async def _group_has_records(self, external_group_id: str) -> bool:
        return any(n.get("externalGroupId") == external_group_id
                   for n in await self._records_by(field="externalGroupId", values=[external_group_id]))

    async def _delete(self, node: dict[str, Any]) -> int:
        record_id = node.get("id") or node.get("_key")
        if not record_id or node.get("isDeleted"):
            return 0
        await self._processor.on_record_deleted(record_id=record_id)
        return 1


def _live_ids_query(sobject: str, condition: str = "") -> tuple[str, bool]:
    """(SOQL listing ids of live records, whether to send it to queryAll). Tasks go through
    queryAll: a normal query leaves out archived activities, which are live."""
    if sobject == "Task":
        where = " AND ".join(c for c in ("IsDeleted = false", condition) if c)
        return f"SELECT Id FROM Task WHERE {where}", True
    return f"SELECT Id FROM {sobject}" + (f" WHERE {condition}" if condition else ""), False


def _iso(epoch_ms: int) -> str:
    from datetime import datetime, timezone

    return datetime.fromtimestamp(int(epoch_ms) // 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")


def _ms(value: Any) -> Optional[int]:
    if not isinstance(value, str) or not value:
        return None
    try:
        return parse_timestamp(value.replace("+0000", "+00:00"))
    except Exception:
        return None


__all__ = ["SalesforceDeletions", "DeletionReadError", "TRACKED"]
