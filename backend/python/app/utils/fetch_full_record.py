from __future__ import annotations

import asyncio

from collections.abc import Callable
from typing import Any

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import CollectionNames, ProgressStatus
from app.config.constants.service import config_node_constants
from app.models.entities import RecordType, TicketRecord
from app.modules.transformers.blob_storage import BlobStorage
from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
from app.utils.chat_helpers import collection_map, create_record_instance_from_dict, get_record
from app.utils.logger import create_logger

logger = create_logger(__name__)


class FetchFullRecordArgs(BaseModel):
    """
    Required tool args for fetching full records.
    """
    record_ids: list[str] = Field(
        ...,
        description=(
            "List of Record IDs to fetch. Each Record ID is shown in the 'Record ID:' line "
            "of the record's context metadata in the conversation. "
            "Use ONLY the exact Record IDs from the context — do NOT invent, guess, or reuse example IDs. "
            "Pass ALL record IDs in a single call."
        )
    )
    reason: str = Field(
        default="Fetching full record content for comprehensive answer",
        description="Brief explanation of why the full records are needed (e.g., 'query asks for complete details')."
    )


async def _apply_live_ticket_context_metadata(
    record: dict[str, Any],
    *,
    config_service: ConfigurationService | None,
    graph_provider: IGraphDBProvider | None,
    frontend_url: str | None,
) -> None:
    """Upgrade ticket context_metadata with live Jira fields (fetch_full_record only)."""
    if not config_service:
        return

    record_type = record.get("record_type") or record.get("recordType")
    if record_type != RecordType.TICKET.value:
        return

    record_key = record.get("id") or record.get("_key")
    if not record_key:
        return

    graph_doc = None
    if graph_provider:
        collection = collection_map.get(record_type)
        if collection:
            try:
                graph_doc = await graph_provider.get_document(
                    document_key=record_key,
                    collection=collection,
                )
            except Exception as e:
                logger.warning(
                    "Could not fetch ticket graph doc for live context on %s: %s",
                    record_key,
                    str(e),
                )

    record_instance = create_record_instance_from_dict(record, graph_doc)
    if not isinstance(record_instance, TicketRecord):
        return

    record["context_metadata"] = await record_instance.to_llm_context_with_live_fields(
        frontend_url=frontend_url,
        config_service=config_service,
    )


async def _enrich_sql_table_with_fk_relations(
    record: dict[str, Any],
    graph_provider: IGraphDBProvider,
) -> dict[str, Any]:
    """
    Enrich a SQL_TABLE record with FK parent and child record IDs.
    Args:
        record: The SQL_TABLE record to enrich
        graph_provider: Service to query FK relations from GraphDB
    Returns:
        The record with fk_parent_record_ids and fk_child_record_ids added
    """
    from app.config.constants.arangodb import RecordRelations
    
    record_id = record.get("id") or record.get("record_id")
    if not record_id:
        logger.debug("FK enrichment skipped: no record_id found in record")
        return record
    
    record_name = record.get("record_name") or record.get("recordName") or ""
    fk_child_ids = []
    fk_parent_ids = []
    
    try:
        # Get child records (tables that reference this table via FK)
        fk_child_ids = await graph_provider.get_child_record_ids_by_relation_type(
            record_id, RecordRelations.FOREIGN_KEY.value
        )
        fk_child_ids = fk_child_ids if isinstance(fk_child_ids, list) else list(fk_child_ids)
        logger.debug(
            "FK enrichment for %s (id=%s): found %d child tables: %s",
            record_name, record_id, len(fk_child_ids), fk_child_ids
        )
    except Exception as e:
        logger.warning("Could not fetch child record IDs for %s: %s", record_id, str(e))
    
    try:
        # Get parent records (tables this table references via FK)
        fk_parent_ids = await graph_provider.get_parent_record_ids_by_relation_type(
            record_id, RecordRelations.FOREIGN_KEY.value
        )
        fk_parent_ids = fk_parent_ids if isinstance(fk_parent_ids, list) else list(fk_parent_ids)
        logger.debug(
            "FK enrichment for %s (id=%s): found %d parent tables: %s",
            record_name, record_id, len(fk_parent_ids), fk_parent_ids
        )
    except Exception as e:
        logger.warning("Could not fetch parent record IDs for %s: %s", record_id, str(e))
    
    # Add FK relations to the record (non-destructive - creates a copy)
    enriched_record = dict(record)
    enriched_record["fk_parent_record_ids"] = fk_parent_ids
    enriched_record["fk_child_record_ids"] = fk_child_ids
    
    if fk_parent_ids or fk_child_ids:
        logger.info(
            "FK enrichment: enriched SQL_TABLE %s with %d parent and %d child FK relations",
            record_name or record_id, len(fk_parent_ids), len(fk_child_ids)
        )
    
    return enriched_record


# Concurrency for resolving record ids. Each miss is an ACL check, a graph
# read and a blob download; a handful in flight cuts the wall clock of a
# multi-record fetch without hammering the stores.
_RESOLVE_CONCURRENCY = 5

# Why an id produced no content. Deliberately coarse where it has to be:
# "no access" and "does not exist" share one reason, because distinguishing
# them turns this tool into an existence oracle for records the caller may
# not read. The other two leak nothing and are worth telling the model.
UNAVAILABLE = "not_available"
NOT_INDEXED_YET = "not_indexed_yet"
STORAGE_ERROR = "storage_error"


class _RecordResolver:
    """Turns one record id into a record, or into the reason there isn't one.

    Holds the per-call collaborators — the blob client and the frontend URL
    that used to be rebuilt inside the loop for every record.
    """

    def __init__(
        self,
        *,
        virtual_record_id_to_result: dict[str, Any],
        graph_provider: IGraphDBProvider | None,
        blob_store: BlobStorage | None,
        config_service: ConfigurationService | None,
        org_id: str | None,
        user_id: str | None,
        frontend_url: str | None,
    ) -> None:
        self._map = virtual_record_id_to_result
        self._graph_provider = graph_provider
        self._blob_store = blob_store
        self._config_service = config_service
        self._org_id = org_id
        self._user_id = user_id
        self._frontend_url = frontend_url
        self._endpoints_read = False

    async def resolve(self, record_id: str) -> tuple[str, dict[str, Any] | None, str | None]:
        cached = self._from_map(record_id)
        if cached is not None:
            return record_id, await self._enrich(cached), None

        # An id that is not already in the (ACL-filtered) map is unverified.
        # Without a user to check against, it is never served.
        if not (self._org_id and self._graph_provider and self._user_id):
            return record_id, None, UNAVAILABLE

        try:
            if not await self._graph_provider.check_record_access_with_details(
                self._user_id, self._org_id, record_id,
            ):
                return record_id, None, UNAVAILABLE
        except Exception:
            logger.warning("Access check failed for %s", record_id, exc_info=True)
            return record_id, None, STORAGE_ERROR

        try:
            graph_record = await self._graph_provider.get_document(
                document_key=record_id, collection=CollectionNames.RECORDS.value,
            )
        except Exception:
            logger.warning("Graph read failed for %s", record_id, exc_info=True)
            return record_id, None, STORAGE_ERROR

        if not graph_record:
            return record_id, None, UNAVAILABLE
        if graph_record.get("indexingStatus") != ProgressStatus.COMPLETED.value:
            # Actionable: "try again shortly" is a different instruction from
            # "this record does not exist".
            return record_id, None, NOT_INDEXED_YET

        try:
            record = await self._download(graph_record)
        except Exception:
            logger.warning("Blob read failed for %s", record_id, exc_info=True)
            return record_id, None, STORAGE_ERROR

        if record is None:
            return record_id, None, UNAVAILABLE
        return record_id, await self._enrich(record), None

    def _from_map(self, record_id: str) -> dict[str, Any] | None:
        for vrid, record in self._map.items():
            if record is not None and record.get("id") == record_id:
                record["virtual_record_id"] = vrid
                return record
        return None

    async def _download(self, graph_record: dict[str, Any]) -> dict[str, Any] | None:
        vrid = graph_record.get("virtualRecordId")
        if not vrid:
            return None
        blob_store = await self._ensure_blob_store()
        await self._ensure_frontend_url(blob_store)
        await get_record(
            vrid, self._map, blob_store, self._org_id, {vrid: graph_record},
            self._graph_provider, self._frontend_url,
        )
        record = self._map.get(vrid)
        if record:
            record["virtual_record_id"] = vrid
        return record

    async def _ensure_blob_store(self) -> BlobStorage:
        if self._blob_store is None:
            self._blob_store = BlobStorage(
                logger=logger,
                config_service=self._graph_provider.config_service,
                graph_provider=self._graph_provider,
            )
        return self._blob_store

    async def _ensure_frontend_url(self, blob_store: BlobStorage) -> None:
        if self._frontend_url is not None or self._endpoints_read:
            return
        self._endpoints_read = True
        try:
            endpoints = await blob_store.config_service.get_config(
                config_node_constants.ENDPOINTS.value, default={},
            )
            if isinstance(endpoints, dict):
                self._frontend_url = endpoints.get("frontend", {}).get("publicEndpoint")
        except Exception:
            logger.debug("Could not read the frontend endpoint", exc_info=True)

    async def _enrich(self, record: dict[str, Any]) -> dict[str, Any]:
        await _apply_live_ticket_context_metadata(
            record,
            config_service=self._config_service,
            graph_provider=self._graph_provider,
            frontend_url=self._frontend_url,
        )
        record_type = record.get("record_type") or record.get("recordType")
        if record_type == "SQL_TABLE" and self._graph_provider:
            return await _enrich_sql_table_with_fk_relations(record, self._graph_provider)
        return record


async def _fetch_multiple_records_impl(
    record_ids: list[str],
    virtual_record_id_to_result: dict[str, Any],
    graph_provider: IGraphDBProvider | None = None,
    blob_store: BlobStorage | None = None,
    org_id: str | None = None,
    user_id: str | None = None,
) -> dict[str, Any]:
    """
    Fetch multiple complete records at once.
    For SQL_TABLE records, also enriches with FK parent/child record IDs.

    If a record_id is not found in the map, attempts to:
    0. Verify the user may read it (the map itself is already ACL-filtered,
       an arbitrary id is not), skipping the record when they may not
    1. Fetch the Record from graph_provider to get virtual_record_id
    2. Fetch the record content from blob_store
    3. Enrich with FK relations if SQL_TABLE

    Without `user_id` the check cannot run, so the id-resolution path is
    skipped entirely rather than served unchecked.

    Returns:
    {
      "ok": true,
      "records": [...],
      "record_count": N,
      "not_available": {"id": "This record is not available"},   # fetched or map-keyed but missing
      "invalid_record_ids": {"id": "Invalid record ID"}           # malformed / non-UUID IDs
    }
    """
    found_records = []
    not_available_ids = []
    unavailable_reasons: dict[str, str] = {}

    # Get frontend_url from the first non-None record already in the map
    frontend_url = next(
        (r["frontend_url"] for r in virtual_record_id_to_result.values()
         if r is not None and r.get("frontend_url")),
        None,
    )

    config_service = graph_provider.config_service if graph_provider else None

    # Built once, not once per record: this used to be constructed inside the
    # loop, along with a fresh ENDPOINTS read.
    resolver = _RecordResolver(
        virtual_record_id_to_result=virtual_record_id_to_result,
        graph_provider=graph_provider,
        blob_store=blob_store,
        config_service=config_service,
        org_id=org_id,
        user_id=user_id,
        frontend_url=frontend_url,
    )

    # Records resolve concurrently -- each miss costs an ACL check, a graph
    # read and a blob download, and ten ids were thirty sequential round
    # trips. The map is shared with retrieval, so nothing writes to it from a
    # worker: results come back in order and are applied on this task.
    semaphore = asyncio.Semaphore(_RESOLVE_CONCURRENCY)

    async def _resolve(record_id: str) -> tuple[str, dict[str, Any] | None, str | None]:
        async with semaphore:
            return await resolver.resolve(record_id)

    resolutions = await asyncio.gather(*(_resolve(rid) for rid in record_ids))

    for record_id, record, reason in resolutions:
        if record is not None:
            found_records.append(record)
        else:
            not_available_ids.append(record_id)
            unavailable_reasons[record_id] = reason or UNAVAILABLE

    result: dict[str, Any] = {}
    result["ok"] = False

    if found_records:
        result["ok"] = True
        result["records"] = found_records
        result["record_count"] = len(found_records)
    else:
        # Keep the per-id detail: "none were found" alone leaves the model
        # unable to tell a typo from a record that is still indexing.
        return {
            "ok": False,
            "error": (
                "No record IDs were provided." if not record_ids
                else "None of the requested records were available."
            ),
            "not_available_ids": not_available_ids,
            "unavailable_reasons": unavailable_reasons,
        }

    result["not_available_ids"] = not_available_ids
    result["unavailable_reasons"] = unavailable_reasons

    return result


def create_fetch_full_record_tool(
    virtual_record_id_to_result: dict[str, Any],
    org_id: str | None = None,
    graph_provider: IGraphDBProvider | None = None,
    blob_store: BlobStorage | None = None,
    user_id: str | None = None,
) -> Callable:
    """
    Factory function to create the tool with runtime dependencies injected.
    
    Args:
        virtual_record_id_to_result: Mapping of virtual record IDs to record data
        graph_provider: Optional GraphDB service for enriching SQL_TABLE records
                        with FK parent/child relations and resolving record IDs
        blob_store: Optional blob storage for fetching records not in the map
        org_id: Optional organization ID for blob storage lookups
        user_id: Requesting user, required to resolve a record ID that is not
                 already in the (ACL-filtered) map — see
                 `_fetch_multiple_records_impl`
    """
    @tool("fetch_full_record", args_schema=FetchFullRecordArgs)
    async def fetch_full_record_tool(record_ids: list[str], reason: str = "Fetching full record content for comprehensive answer") -> dict[str, Any]:
        """Read one or more records end to end. Search gives you a few matching blocks per record; lookup_record/navigate/list_files give you an ID and metadata and no content at all; this gives you everything.

        Call this BEFORE answering whenever what you currently hold is
        incomplete for what the question needs:

        - The answer is a property of the whole document — a summary or
          overview, its risks/gaps/obligations/key points, a review or
          assessment, a comparison of documents, whether it mentions something
          anywhere, anything asking for all of something. A handful of blocks
          CANNOT support that answer, however relevant they look, because the
          parts you were not given are exactly what you would be implying are
          unimportant.
        - You hold no passage at all — the record came from lookup, navigation
          or listing, so you have its ID and metadata and nothing it says.
          Never infer content from a title.

        (Skip only when the exact fact needed — a date, a name, a number, a
        status, one clause — is already visible in a block you hold, or
        metadata alone settles the question outright, e.g. a ticket's status
        or assignee.)

        Those are illustrations, not a checklist — apply the same reasoning to
        whatever was actually asked.

        Pass every record_id you need in ONE call, taken from a candidate list, a
        'Record ID' field, a record_id= or node_id= shown by navigation — never invent IDs.

        For SQL_TABLE records, also returns fk_parent_record_ids and fk_child_record_ids
        which can be used to fetch related tables for nested FK relationships.

        Args:
            record_ids: List of Record IDs to fetch — use the exact Record ID values from the context
            reason: Brief explanation of why the full records are needed

        Returns: Complete content of the records or {"ok": false, "error": "..."}.
        """
        logger.info(
            "fetch_full_record called: record_ids=%s, reason=%r",
            record_ids,
            reason,
        )
        try:
            return await _fetch_multiple_records_impl(
                record_ids,
                virtual_record_id_to_result,
                org_id=org_id,
                graph_provider=graph_provider,
                blob_store=blob_store,
                user_id=user_id,
            )
        except Exception as e:
            # Return error as dict
            return {"ok": False, "error": f"Failed to fetch records: {str(e)}"}

    return fetch_full_record_tool


