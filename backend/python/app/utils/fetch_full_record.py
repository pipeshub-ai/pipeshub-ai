from __future__ import annotations

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
            "List of Record IDs to fetch (max 5). These come from "
            "the 'Record ID :' line in retrieval context metadata. "
            "Use ONLY exact IDs from the context — do NOT invent or guess IDs."
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


async def _fetch_multiple_records_impl(
    record_ids: list[str],
    virtual_record_id_to_result: dict[str, Any],
    graph_provider: IGraphDBProvider | None = None,
    blob_store: BlobStorage | None = None,
    org_id: str | None = None,
) -> dict[str, Any]:
    """
    Fetch multiple complete records at once.
    For SQL_TABLE records, also enriches with FK parent/child record IDs.

    If a record_id is not found in the map, attempts to:
    1. Fetch the Record from graph_provider to get virtual_record_id
    2. Fetch the record content from blob_store
    3. Enrich with FK relations if SQL_TABLE

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

    # Get frontend_url from the first non-None record already in the map
    frontend_url = next(
        (r["frontend_url"] for r in virtual_record_id_to_result.values()
         if r is not None and r.get("frontend_url")),
        None,
    )

    config_service = graph_provider.config_service if graph_provider else None

    for record_id in record_ids:
        virtual_record_id = None
        found_record = None

        for vrid, record in virtual_record_id_to_result.items():
            if record is not None and record.get("id") == record_id:
                virtual_record_id = vrid
                found_record = record
                break

        if found_record:
            found_record["virtual_record_id"] = virtual_record_id
            await _apply_live_ticket_context_metadata(
                found_record,
                config_service=config_service,
                graph_provider=graph_provider,
                frontend_url=frontend_url,
            )
            # Enrich SQL_TABLE records with FK relations
            record_type = found_record.get("record_type") or found_record.get("recordType")
            if record_type == "SQL_TABLE" and graph_provider:
                found_record = await _enrich_sql_table_with_fk_relations(found_record, graph_provider)
            found_records.append(found_record)
            continue

        if org_id and graph_provider:
            try:
                graphDb_record = await graph_provider.get_document(
                                document_key=record_id,
                                collection=CollectionNames.RECORDS.value
                            )

                if graphDb_record:
                    indexing_status = graphDb_record.get("indexingStatus")
                    if indexing_status == ProgressStatus.COMPLETED.value:
                        vrid = graphDb_record.get("virtualRecordId")
                        blob_store = BlobStorage(logger=logger, config_service=graph_provider.config_service, graph_provider=graph_provider)
                        frontend_url = None
                        try:
                            endpoints_config = await blob_store.config_service.get_config(
                                config_node_constants.ENDPOINTS.value,
                                default={}
                            )
                            if isinstance(endpoints_config, dict):
                                frontend_url = endpoints_config.get("frontend", {}).get("publicEndpoint")
                        except Exception:
                            pass
                        virtual_to_record_map = {vrid: graphDb_record}
                        await get_record(vrid, virtual_record_id_to_result, blob_store, org_id, virtual_to_record_map, graph_provider, frontend_url)
                        blob_record = virtual_record_id_to_result.get(vrid)
                        if blob_record:
                            blob_record["virtual_record_id"] = vrid
                            await _apply_live_ticket_context_metadata(
                                blob_record,
                                config_service=config_service,
                                graph_provider=graph_provider,
                                frontend_url=frontend_url,
                            )
                            # Enrich SQL_TABLE records with FK relations
                            record_type = blob_record.get("record_type") or blob_record.get("recordType")
                            if record_type == "SQL_TABLE" and graph_provider:
                                blob_record = await _enrich_sql_table_with_fk_relations(blob_record, graph_provider)
                            found_records.append(blob_record)
                            continue
                else:
                    # record_id might be a virtual_record_id (UUID from find_records).
                    # Try resolving: virtualRecordId → record _key → fetch.
                    if hasattr(graph_provider, "get_records_by_virtual_record_id"):
                        record_keys = await graph_provider.get_records_by_virtual_record_id(record_id)
                        if record_keys:
                            actual_record_id = record_keys[0]
                            graphDb_record = await graph_provider.get_document(
                                document_key=actual_record_id,
                                collection=CollectionNames.RECORDS.value,
                            )
                            if graphDb_record:
                                indexing_status = graphDb_record.get("indexingStatus")
                                if indexing_status == ProgressStatus.COMPLETED.value:
                                    vrid = graphDb_record.get("virtualRecordId") or record_id
                                    blob_store = BlobStorage(logger=logger, config_service=graph_provider.config_service, graph_provider=graph_provider)
                                    frontend_url = None
                                    try:
                                        endpoints_config = await blob_store.config_service.get_config(
                                            config_node_constants.ENDPOINTS.value,
                                            default={}
                                        )
                                        if isinstance(endpoints_config, dict):
                                            frontend_url = endpoints_config.get("frontend", {}).get("publicEndpoint")
                                    except Exception:
                                        pass
                                    virtual_to_record_map = {vrid: graphDb_record}
                                    await get_record(vrid, virtual_record_id_to_result, blob_store, org_id, virtual_to_record_map, graph_provider, frontend_url)
                                    blob_record = virtual_record_id_to_result.get(vrid)
                                    if blob_record:
                                        blob_record["virtual_record_id"] = vrid
                                        await _apply_live_ticket_context_metadata(
                                            blob_record,
                                            config_service=config_service,
                                            graph_provider=graph_provider,
                                            frontend_url=frontend_url,
                                        )
                                        record_type = blob_record.get("record_type") or blob_record.get("recordType")
                                        if record_type == "SQL_TABLE" and graph_provider:
                                            blob_record = await _enrich_sql_table_with_fk_relations(blob_record, graph_provider)
                                        found_records.append(blob_record)
                                        continue
            except Exception:
                pass

        not_available_ids.append(record_id)

    result: dict[str, Any] = {}
    result["ok"] = False

    if found_records:
        result["ok"] = True
        result["records"] = found_records
        result["record_count"] = len(found_records)
    else:
        return {"ok": False, "error": "None of the requested records were found."}


    result["not_available_ids"] = not_available_ids

    return result


def create_fetch_full_record_tool(
    virtual_record_id_to_result: dict[str, Any],
    org_id: str | None = None,
    graph_provider: IGraphDBProvider | None = None,
    blob_store: BlobStorage | None = None,
) -> Callable:
    """
    Factory function to create the tool with runtime dependencies injected.
    
    Args:
        virtual_record_id_to_result: Mapping of virtual record IDs to record data
        graph_provider: Optional GraphDB service for enriching SQL_TABLE records
                        with FK parent/child relations and resolving record IDs
        blob_store: Optional blob storage for fetching records not in the map
        org_id: Optional organization ID for blob storage lookups
    """
    @tool("fetch_full_record", args_schema=FetchFullRecordArgs)
    async def fetch_full_record_tool(record_ids: list[str], reason: str = "Fetching full record content for comprehensive answer") -> dict[str, Any]:
        """Fetch the complete content of one or more records. Pass relevant record IDs in a SINGLE call.

        Accepts record IDs from retrieval context 'Record ID :' field.
        Maximum 5 record IDs per call.

        For SQL_TABLE records, also returns fk_parent_record_ids and fk_child_record_ids
        which can be used to fetch related tables for nested FK relationships.

        Args:
            record_ids: List of Record IDs to fetch (max 5).
                        Source: 'Record ID :' in retrieval context.
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
            )
        except Exception as e:
            # Return error as dict
            return {"ok": False, "error": f"Failed to fetch records: {str(e)}"}

    return fetch_full_record_tool


