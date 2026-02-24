from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from app.modules.transformers.blob_storage import BlobStorage
from app.utils.logger import create_logger
from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
logger = create_logger("fetch_full_record")


class FetchFullRecordArgs(BaseModel):
    """
    Required tool args for fetching full records.
    """
    record_ids: List[str] = Field(
        ...,
        description="List of IDs (or virtualRecordIds) of the records to fetch. Pass all record IDs that need to be fetched in a single call. Prefer the IDs found in chunk metadata."
    )
    reason: str = Field(
        default="Fetching full record content for comprehensive answer",
        description="Why the full records are needed (explain the gap in the provided blocks)."
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
    record: Dict[str, Any],
    graph_provider: IGraphDBProvider,
) -> Dict[str, Any]:
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
    graph_provider: Optional["IGraphDBProvider"],
    blob_store: Optional[BlobStorage],
    org_id: Optional[str],
    virtual_record_id_to_result: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """
    Fetch a record by its graph record id.
    
    1. Resolve record_id -> virtual_record_id via graph_provider
    2. Check if already in map (by vrid)
    3. If not, fetch from blob_store
    4. Add to map for future lookups
    
    Args:
        record_id: The graph record id
        graph_provider: Service to resolve record_id to virtual_record_id
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
        # Resolve record_id to virtual_record_id
        record_id_to_vrid = await graph_provider.get_virtual_record_ids_for_record_ids([record_id])
        vrid = record_id_to_vrid.get(record_id)
        
        if not vrid:
            logger.debug("Could not resolve record_id %s to virtual_record_id", record_id)
            return None
        
        # Check if already in map by vrid
        if vrid in virtual_record_id_to_result:
            existing_record = virtual_record_id_to_result[vrid]
            if existing_record:
                logger.debug("Record %s found in map by vrid %s", record_id, vrid)
                return existing_record
        
        # Fetch from blob storage
        record = await blob_store.get_record_from_storage(virtual_record_id=vrid, org_id=org_id)
        
        if not record:
            logger.debug("Could not fetch record from blob for vrid %s", vrid)
            virtual_record_id_to_result[vrid] = None
            return None
        
        # Enrich with graph metadata (similar to get_record in chat_helpers)
        # get_record_by_id returns a Record Pydantic model or dict (generic 'id' field)
        try:
            graph_record = await graph_provider.get_record_by_id(record_id)
            if graph_record:
                # Normalize to dict: Pydantic models use model_dump(), dicts use as-is
                meta = (
                    graph_record.model_dump()
                    if hasattr(graph_record, "model_dump")
                    else graph_record
                )
                if isinstance(meta, dict):
                    record_id_value = meta.get("id") or meta.get("_key") or record_id
                    record["id"] = record_id_value
                    record["org_id"] = org_id
                    record["record_name"] = meta.get("record_name") or meta.get("recordName")
                    record["record_type"] = meta.get("record_type") or meta.get("recordType")
                    record["version"] = meta.get("version")
                    record["origin"] = meta.get("origin")
                    record["connector_name"] = meta.get("connector_name") or meta.get("connectorName")
                    record["weburl"] = meta.get("weburl") or meta.get("webUrl")
                else:
                    record["id"] = record_id
            else:
                record["id"] = record_id
        except Exception as e:
            logger.warning("Could not fetch graph metadata for record %s: %s", record_id, str(e))
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
    record_ids: List[str],
    virtual_record_id_to_result: Dict[str, Any],
    graph_provider: Optional[IGraphDBProvider] = None,
    blob_store: Optional[BlobStorage] = None,
    org_id: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Fetch multiple complete records at once.
    For SQL_TABLE records, also enriches with FK parent/child record IDs.
    
    If a record_id is not found in the map, attempts to:
    1. Resolve record_id -> virtual_record_id via graph_provider
    2. Fetch the record from blob_store
    3. Enrich with FK relations if SQL_TABLE
    
    Returns:
    {
      "ok": true,
      "records": [...],
      "not_found": [...]  # IDs that weren't found even after blob fetch
    }
    """
    records = list(virtual_record_id_to_result.values())

    found_records = []
    not_found_ids = []

    for record_id in record_ids:
        record = next((record for record in records if record is not None and record.get("id") == record_id), None)
        if record:
            # Enrich SQL_TABLE records with FK relations
            record_type = record.get("record_type") or record.get("recordType")
            if record_type == "SQL_TABLE" and graph_provider:
                record = await _enrich_sql_table_with_fk_relations(record, graph_provider)
            found_records.append(record)
        else:
            # Record not in map - try to fetch from blob storage
            fetched_record = await _fetch_record_by_id(
                record_id, graph_provider, blob_store, org_id, virtual_record_id_to_result
            )
            if fetched_record:
                # Enrich SQL_TABLE records with FK relations
                record_type = fetched_record.get("record_type") or fetched_record.get("recordType")
                if record_type == "SQL_TABLE" and graph_provider:
                    fetched_record = await _enrich_sql_table_with_fk_relations(fetched_record, graph_provider)
                found_records.append(fetched_record)
            else:
                not_found_ids.append(record_id)

    if found_records:
        logger.info(f"Found {len(found_records)} records")
        logger.debug(f"Found records data: {found_records}")
        result = {
            "ok": True,
            "records": found_records,
            "record_count": len(found_records)
        }
        if not_found_ids:
            result["not_found"] = not_found_ids
        return result

    # Nothing found
    return {"ok": False, "error": f"None of the requested records were found: {', '.join(record_ids)}"}


# Option 1: Create the tool without the decorator and handle runtime kwargs manually
def create_fetch_full_record_tool(
    virtual_record_id_to_result: Dict[str, Any],
    graph_provider: Optional[IGraphDBProvider] = None,
    blob_store: Optional[BlobStorage] = None,
    org_id: Optional[str] = None,
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
    async def fetch_full_record_tool(record_ids: List[str], reason: str = "Fetching full record content for comprehensive answer") -> Dict[str, Any]:
        """
        Retrieve the complete content of multiple records (all blocks/groups) for better answering.
        Pass all record IDs at once instead of making multiple separate calls.
        
        For SQL_TABLE records, also returns fk_parent_record_ids and fk_child_record_ids
        which can be used to fetch related tables for nested FK relationships.

        Args:
            record_ids: List of virtual record IDs or record IDs to fetch
            reason: Clear explanation of why the full records are needed

        Returns:
        {"ok": true, "records": [...], "record_count": N, "not_found": [...]}
        or {"ok": false, "error": "..."}.
        
        For SQL_TABLE records, each record will include:
        - fk_parent_record_ids: List of record IDs for parent tables (tables this table references)
        - fk_child_record_ids: List of record IDs for child tables (tables that reference this table)
        """
        try:
            result = await _fetch_multiple_records_impl(
                record_ids, virtual_record_id_to_result, graph_provider, blob_store, org_id
            )
            return result
        except Exception as e:
            # Return error as dict
            return {"ok": False, "error": f"Failed to fetch records: {str(e)}"}

    return fetch_full_record_tool


def create_record_for_fetch_block_group(record: Dict[str, Any],block_group: Dict[str, Any],blocks: List[Dict[str, Any]]) -> Dict[str, Any]:
    block_container = {
        "blocks": blocks,
        "block_groups": [block_group]
    }
    record["block_containers"] = block_container
    return record
