"""Take out the copy of a file that was removed from a record, when the file itself stays.

PipesHub keeps one FILE record per link of a file to a record (``<document id>-<record
id>``). Removing a file from a record deletes the link only: the file's versions do not
change, so an incremental read of files never sees it, and getDeleted does not list links.
So each incremental sync lists the links of the files PipesHub holds and removes the copies
whose link is gone. A file attached to an email is filed under the email's task, so links
to emails are read through EmailMessage.ActivityId.

Only a complete listing removes anything: a query that fails raises, and nothing is removed.
"""

from __future__ import annotations

import re
from logging import Logger
from typing import Any, AsyncIterator, Callable

from app.config.constants.arangodb import CollectionNames
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.connectors.core.base.data_store.data_store import DataStoreProvider
from app.models.entities import RecordType

RowPages = Callable[[str], AsyncIterator[list[dict[str, Any]]]]

_SF_ID = re.compile(r"^[a-zA-Z0-9]{15}([a-zA-Z0-9]{3})?$")
# Ids per IN list: 300 quoted ids keep a GET query well under Salesforce's 16 KB URI limit.
_IN_BATCH = 300


class SalesforceFileLinks:
    def __init__(
        self,
        rows: RowPages,
        processor: DataSourceEntitiesProcessor,
        data_store_provider: DataStoreProvider,
        connector_id: str,
        logger: Logger,
    ) -> None:
        self._rows = rows
        self._processor = processor
        self._store = data_store_provider
        self._connector_id = connector_id
        self._logger = logger

    async def run(self) -> None:
        stored = await self._stored_copies()
        if not stored:
            return
        docs = sorted({doc for doc, _ in stored.values()})
        links = await self._current_links(docs)
        removed = 0
        for record_id, (doc, entity) in stored.items():
            if (doc, entity) not in links:
                await self._processor.on_record_deleted(record_id=record_id)
                removed += 1
        if removed:
            self._logger.info("Removed %d file copies whose record no longer has the file", removed)

    async def _stored_copies(self) -> dict[str, tuple[str, str]]:
        """record id -> (document id, record id it is filed under), both 15 characters."""
        async with self._store.transaction() as tx_store:
            nodes = await tx_store.get_nodes_by_field_in(
                collection=CollectionNames.RECORDS.value, field="recordType", values=[RecordType.FILE.value],
            )
        out: dict[str, tuple[str, str]] = {}
        for node in nodes or []:
            if node.get("connectorId") != self._connector_id or node.get("isDeleted"):
                continue
            doc, _, entity = str(node.get("externalRecordId") or "").partition("-")
            record_id = node.get("id") or node.get("_key")
            if record_id and _SF_ID.match(doc) and _SF_ID.match(entity):
                out[record_id] = (doc[:15], entity[:15])
        return out

    async def _current_links(self, docs: list[str]) -> set[tuple[str, str]]:
        links: set[tuple[str, str]] = set()
        via_email: dict[str, set[str]] = {}
        for start in range(0, len(docs), _IN_BATCH):
            batch = "','".join(docs[start:start + _IN_BATCH])
            soql = (
                "SELECT ContentDocumentId, LinkedEntityId, LinkedEntity.Type FROM ContentDocumentLink "
                f"WHERE ContentDocumentId IN ('{batch}')"
            )
            async for page in self._rows(soql):
                for row in page:
                    doc = str(row.get("ContentDocumentId") or "")[:15]
                    entity = str(row.get("LinkedEntityId") or "")
                    if not doc or not entity:
                        continue
                    if ((row.get("LinkedEntity") or {}).get("Type")) == "EmailMessage":
                        via_email.setdefault(entity, set()).add(doc)
                    links.add((doc, entity[:15]))
        emails = sorted(e for e in via_email if _SF_ID.match(e))
        for start in range(0, len(emails), _IN_BATCH):
            batch = "','".join(emails[start:start + _IN_BATCH])
            async for page in self._rows(f"SELECT Id, ActivityId FROM EmailMessage WHERE Id IN ('{batch}')"):
                for row in page:
                    task = str(row.get("ActivityId") or "")[:15]
                    for doc in via_email.get(str(row.get("Id") or ""), set()):
                        if task:
                            links.add((doc, task))
        return links
