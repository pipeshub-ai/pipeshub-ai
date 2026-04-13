from __future__ import annotations

from collections.abc import Callable
from typing import Any

from langchain_core.tools import tool
from pydantic import BaseModel, Field
from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
from app.config.constants.arangodb import CollectionNames, ProgressStatus
from app.config.constants.service import config_node_constants
from app.modules.transformers.blob_storage import BlobStorage
from app.utils.chat_helpers import get_record
from app.utils.logger import create_logger

logger = create_logger(__name__)

class FetchFullRecordArgs(BaseModel):
    """
    Required tool args for fetching full records.
    """
    record_ids: list[str] = Field(
        ...,
        description=(
            "List of Record IDs to fetch. Each Record ID is shown in the 'Record ID :' line "
            "of the record's context metadata in the conversation. "
            "Use ONLY the exact Record IDs from the context — do NOT invent, guess, or reuse example IDs. "
            "Pass ALL record IDs in a single call."
        )
    )
    reason: str = Field(
        default="Fetching full record content for comprehensive answer",
        description="Brief explanation of why the full records are needed (e.g., 'query asks for complete details')."
    )

class FetchBlockGroupArgs(BaseModel):
    """
    Required tool args for fetching a block group.
    """
    block_group_number: str = Field(
        ...,
        description="Number of the block group to fetch."
    )
    reason: str = Field(
        default="Fetching block group for additional context",
        description="Why the block group is needed (explain the gap in the provided blocks)."
    )


# async def _try_blobstore_fetch(blob_store: BlobStorage, org_id: str, record_id: str) -> Optional[Dict[str, Any]]:
#     """
#     Try common BlobStorage paths. We don't know the exact method names in your code,
#     so attempt a few sensible options and return the first successful payload.
#     """
#     try:
#         rec = await blob_store.get_record_from_storage(org_id=org_id, virtual_record_id=record_id)
#         if rec:
#             return rec
#     except Exception:
#         pass

# async def _fetch_full_record_using_vrid(vrid: str, blob_store: BlobStorage,org_id: str) -> Dict[str, Any]:
#     """
#     Fetch complete record using virtual record id.
#     """
#     record = await _try_blobstore_fetch(blob_store, org_id, vrid)
#     if record:
#         return {"ok": True, "record": record}
#     else:
#         return {"ok": False, "error": f"Record with vrid '{vrid}' not found in blob store."}


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


async def _fetch_record_by_id(
    record_id: str,
    graph_provider: IGraphDBProvider | None,
    blob_store: BlobStorage | None,
    org_id: str | None,
    virtual_record_id_to_result: dict[str, Any],
) -> dict[str, Any] | None:
    """
    Fetch a record by its graph record id.
    
    1. Fetch the Record from graph_provider to get virtual_record_id and metadata
    2. Check if already in map (by vrid)
    3. If not, fetch from blob_store via chat_helpers.get_record
    4. Add to map for future lookups
    
    Args:
        record_id: The graph record id (record.id / record._key)
        graph_provider: Service to fetch record and resolve virtual_record_id
        blob_store: Storage to fetch record content
        org_id: Organization ID for blob storage
        virtual_record_id_to_result: Map to check/update with fetched records
        
    Returns:
        The record dict if found, None otherwise
    """
    if not graph_provider or not blob_store or not org_id:
        logger.debug(
            "Cannot fetch record %s: missing graph_provider=%s, blob_store=%s, org_id=%s",
            record_id, graph_provider is not None, blob_store is not None, org_id is not None
        )
        return None
    
    try:
        graph_record = await graph_provider.get_record_by_id(record_id)
        if not graph_record:
            logger.debug("Record %s not found in graph", record_id)
            return None

        meta = (
            graph_record.model_dump()
            if hasattr(graph_record, "model_dump")
            else graph_record
        )

        vrid = meta.get("virtual_record_id")
        if not vrid:
            logger.debug("Record %s exists in graph but has no virtual_record_id", record_id)
            return None

        if vrid in virtual_record_id_to_result:
            return virtual_record_id_to_result.get(vrid)

        # Build graph metadata for get_record so blob+graph normalization stays consistent
        record_id_value = meta.get("id") or meta.get("_key") or record_id
        virtual_to_record_map: dict[str, dict[str, Any]] = {
            vrid: {
                "id": record_id_value,
                "_key": record_id_value,
                "recordName": meta.get("record_name"),
                "recordType": meta.get("record_type"),
                "version": meta.get("version"),
                "origin": meta.get("origin"),
                "connectorName": meta.get("connector_name"),
                "connectorId": meta.get("connector_id"),
                "webUrl": meta.get("weburl"),
                "previewRenderable": meta.get("preview_renderable", True),
                "hideWeburl": meta.get("hide_weburl", False),
                "mimeType": meta.get("mime_type"),
                "sourceCreatedAtTimestamp": meta.get("source_created_at"),
                "sourceLastModifiedTimestamp": meta.get("source_updated_at"),
            }
        }

        from app.utils.chat_helpers import get_record

        await get_record(
            vrid,
            virtual_record_id_to_result,
            blob_store,
            org_id,
            virtual_to_record_map=virtual_to_record_map,
            graph_provider=graph_provider,
        )

        record = virtual_record_id_to_result.get(vrid)
        if not record:
            logger.debug("Could not fetch record from blob for vrid %s", vrid)
            return None

        if not record.get("id"):
            record["id"] = record_id
        record["virtual_record_id"] = vrid
        # Add to map for future lookups
        virtual_record_id_to_result[vrid] = record
        
        logger.info(
            "Fetched record %s (vrid=%s, name=%s) from blob storage",
            record_id, vrid, record.get("record_name") or record.get("recordName") or ""
        )
        
        return record
        
    except Exception as e:
        logger.warning("Error fetching record %s: %s", record_id, str(e))
        return None


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
                            # Enrich SQL_TABLE records with FK relations
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
        """Fetch the complete content of one or more records when the provided blocks are insufficient to answer the query. Pass ALL record IDs in a SINGLE call using the record_ids parameter.

        IMPORTANT: record_ids must be taken directly from the 'Record ID :' field shown in the context metadata for each record. Do NOT use invented IDs, example IDs that are not present in the current context.

        For SQL_TABLE records, also returns fk_parent_record_ids and fk_child_record_ids
        which can be used to fetch related tables for nested FK relationships.

        Args:
            record_ids: List of Record IDs to fetch — use the exact 'Record ID :' values from the context
            reason: Brief explanation of why the full records are needed

        Returns: Complete content of the records or {"ok": false, "error": "..."}.
        """
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


def create_record_for_fetch_block_group(record: dict[str, Any],block_group: dict[str, Any],blocks: list[dict[str, Any]]) -> dict[str, Any]:
    block_container = {
        "blocks": blocks,
        "block_groups": [block_group]
    }
    record["block_containers"] = block_container
    return record