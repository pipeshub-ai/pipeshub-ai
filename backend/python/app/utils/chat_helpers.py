import asyncio
import re
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple, Union
from urllib.parse import quote
from uuid import uuid4

from jinja2 import Template

from app.config.constants.service import config_node_constants
from app.models.blocks import BlockType, GroupType, SemanticMetadata
from app.models.entities import (
    Connectors,
    FileRecord,
    LinkPublicStatus,
    LinkRecord,
    MailRecord,
    OriginTypes,
    ProjectRecord,
    Record,
    RecordType,
    TicketRecord,
)
from app.modules.qna.prompt_templates import (
    block_group_prompt,
    qna_prompt_context,
    qna_prompt_instructions_1,
    qna_prompt_instructions_2,
    qna_prompt_simple,
    table_prompt,
)
from app.modules.transformers.blob_storage import BlobStorage
from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
from app.services.vector_db.const.const import VECTOR_DB_COLLECTION_NAME
from app.utils.logger import create_logger
from app.utils.mimetype_to_extension import get_extension_from_mimetype

group_types = [GroupType.LIST.value,GroupType.ORDERED_LIST.value,GroupType.FORM_AREA.value,GroupType.INLINE.value,GroupType.KEY_VALUE_AREA.value,GroupType.TEXT_SECTION.value]

# Create a logger for this module
logger = create_logger("chat_helpers")

collection_map = {
                    RecordType.TICKET.value: "tickets",
                    RecordType.PROJECT.value: "projects",
                    RecordType.FILE.value: "files",
                    RecordType.MAIL.value: "mails",
                    RecordType.LINK.value: "links",
                }

def create_record_instance_from_dict(record_dict: Dict[str, Any], graph_doc: Optional[Dict[str, Any]] = None) -> Optional[Record]:
    """
    Creates a Record subclass instance from a dictionary.

    Args:
        record_dict: Dictionary with record data from blob storage
        graph_doc: Optional dictionary with type-specific data from ArangoDB collection

    Returns:
        Record subclass instance or None
    """
    if not record_dict:
        return None

    if not graph_doc:
        return Record(
                id=record_dict.get("id", ""),
                record_name=record_dict.get("record_name", ""),
                record_type=RecordType(record_dict.get("record_type")),
                connector_name=Connectors(record_dict.get("connector_name")) if record_dict.get("connector_name") else Connectors.KNOWLEDGE_BASE,
                mime_type=record_dict.get("mime_type", ""),
                external_record_id=record_dict.get("external_record_id", ""),
                weburl=record_dict.get("weburl", ""),
                version=record_dict.get("version", 1),
                origin=OriginTypes(record_dict.get("origin")) if record_dict.get("origin") else OriginTypes.UPLOAD,
                connector_id=record_dict.get("connector_id", ""),
                source_created_at=record_dict.get("source_created_at", ""),
                source_updated_at=record_dict.get("source_updated_at", ""),
                semantic_metadata=SemanticMetadata(**record_dict.get("semantic_metadata", {})),
            )

    record_type = record_dict.get("record_type")

    base_args = {
        "id": record_dict.get("id", ""),
        "org_id": record_dict.get("org_id", ""),
        "record_name": record_dict.get("record_name", ""),
        "external_record_id": record_dict.get("external_record_id", ""),
        "version": record_dict.get("version", 1),
        "origin": OriginTypes(record_dict.get("origin")) if record_dict.get("origin") else OriginTypes.UPLOAD,
        "connector_name": Connectors(record_dict.get("connector_name")) if record_dict.get("connector_name") else Connectors.KNOWLEDGE_BASE,
        "connector_id": record_dict.get("connector_id", ""),
        "mime_type": record_dict.get("mime_type", ""),
        "source_created_at": record_dict.get("source_created_at", ""),
        "source_updated_at": record_dict.get("source_updated_at", ""),
        "weburl": record_dict.get("weburl", ""),
        "semantic_metadata": SemanticMetadata(**record_dict.get("semantic_metadata", {})),
    }

    try:
        if record_type == RecordType.TICKET.value and graph_doc:
            specific_args = {
                "record_type": RecordType.TICKET,
                "status": graph_doc.get("status"),
                "priority": graph_doc.get("priority"),
                "type": graph_doc.get("type"),
                "delivery_status": graph_doc.get("deliveryStatus"),
                "assignee": graph_doc.get("assignee"),
                "assignee_email": graph_doc.get("assigneeEmail"),
                "reporter_name": graph_doc.get("reporterName"),
                "reporter_email": graph_doc.get("reporterEmail"),
                "creator_name": graph_doc.get("creatorName"),
                "creator_email": graph_doc.get("creatorEmail"),
            }
            return TicketRecord(**base_args, **specific_args)

        elif record_type == RecordType.PROJECT.value and graph_doc:
            specific_args = {
                "record_type": RecordType.PROJECT,
                "status": graph_doc.get("status"),
                "priority": graph_doc.get("priority"),
                "lead_name": graph_doc.get("leadName"),
                "lead_email": graph_doc.get("leadEmail"),
            }
            return ProjectRecord(**base_args, **specific_args)

        elif record_type == RecordType.FILE.value and graph_doc:
            specific_args = {
                "record_type": RecordType.FILE,
                "is_file": graph_doc.get("isFile", True),
                "extension": graph_doc.get("extension"),
            }
            return FileRecord(**base_args, **specific_args)

        elif record_type == RecordType.MAIL.value and graph_doc:
            specific_args = {
                "record_type": RecordType.MAIL,
                "subject": graph_doc.get("subject"),
                "from_email": graph_doc.get("from"),
                "to_emails": graph_doc.get("to"),
                "cc_emails": graph_doc.get("cc"),
                "bcc_emails": graph_doc.get("bcc"),
            }
            return MailRecord(**base_args, **specific_args)

        elif record_type == RecordType.LINK.value and graph_doc:
            specific_args = {
                "record_type": RecordType.LINK,
                "url": graph_doc.get("url", ""),
                "title": graph_doc.get("title"),
                "is_public": LinkPublicStatus(graph_doc.get("isPublic", "unknown")),
                "linked_record_id": graph_doc.get("linkedRecordId"),
            }
            return LinkRecord(**base_args, **specific_args)

        else:
            return None
    except Exception as e:
        logger.error(f"Error creating record instance: {str(e)}")
        return None

async def get_flattened_results(result_set: List[Dict[str, Any]], blob_store: BlobStorage, org_id: str, is_multimodal_llm: bool, virtual_record_id_to_result: Dict[str, Dict[str, Any]],virtual_to_record_map: Dict[str, Dict[str, Any]]=None,from_tool: bool = False,from_retrieval_service: bool = False,graph_provider: Optional[IGraphDBProvider] = None) -> List[Dict[str, Any]]:
    flattened_results = []
    image_index = 0
    seen_chunks = set()
    adjacent_chunks = {}
    new_type_results = []
    old_type_results = []
    if from_retrieval_service:
        new_type_results = result_set
    else:
        for result in result_set:
            meta = result.get("metadata")
            is_block_group = meta.get("isBlockGroup")
            if is_block_group is not None:
                new_type_results.append(result)
            else:
                old_type_results.append(result)

    sorted_new_type_results = sorted(new_type_results, key=lambda x: not x.get("metadata", {}).get("isBlockGroup", False))
    rows_to_be_included = defaultdict(list)

    records_to_fetch = set()
    for result in sorted_new_type_results:
        virtual_record_id = result["metadata"].get("virtualRecordId")

        if virtual_record_id and virtual_record_id not in virtual_record_id_to_result:
            records_to_fetch.add(virtual_record_id)

    # Fetch frontend URL once for all records
    frontend_url = None
    try:
        endpoints_config = await blob_store.config_service.get_config(
            config_node_constants.ENDPOINTS.value,
            default={}
        )
        if isinstance(endpoints_config, dict):
            frontend_url = endpoints_config.get("frontend", {}).get("publicEndpoint")
    except Exception as e:
        logger.warning(f"Failed to fetch frontend URL from config service: {str(e)}")

    await asyncio.gather(*[get_record(virtual_record_id,virtual_record_id_to_result,blob_store,org_id,virtual_to_record_map,graph_provider,frontend_url) for virtual_record_id in records_to_fetch])

    for result in sorted_new_type_results:
        virtual_record_id = result["metadata"].get("virtualRecordId")
        if not virtual_record_id:
            continue
        meta = result.get("metadata")

        if virtual_record_id not in adjacent_chunks:
            adjacent_chunks[virtual_record_id] = []

        index = meta.get("blockIndex")
        is_block_group = meta.get("isBlockGroup")
        if is_block_group:
            chunk_id = f"{virtual_record_id}-{index}-block_group"
        else:
            chunk_id = f"{virtual_record_id}-{index}"

        if chunk_id in seen_chunks:
            continue
        seen_chunks.add(chunk_id)

        record = virtual_record_id_to_result[virtual_record_id]
        if record is None:
            continue
        block_container = record.get("block_containers",{})
        blocks = block_container.get("blocks",[])
        block_groups = block_container.get("block_groups",[])

        if is_block_group:
            block = block_groups[index]
        else:
            block = blocks[index]

        block_type = block.get("type")
        result["block_type"] = block_type
        if block_type == BlockType.TEXT.value and block.get("parent_index") is None:
            result["content"] = block.get("data","")
            adjacent_chunks[virtual_record_id].append(index-1)
            adjacent_chunks[virtual_record_id].append(index+1)
        elif block_type == BlockType.IMAGE.value:
            data = block.get("data")
            if data:
                if from_retrieval_service:
                    result["content"] = f"image_{image_index}"
                    image_index += 1
                else:
                    if is_multimodal_llm:
                        image_uri = data.get("uri")
                        if image_uri:
                            result["content"] = image_uri
                        else:
                            continue
                    else:
                        if result.get("content") and result.get("content").startswith("data:image/"):
                            continue

                    adjacent_chunks[virtual_record_id].append(index-1)
                    adjacent_chunks[virtual_record_id].append(index+1)
            else:
                continue
        elif block_type == BlockType.TABLE_ROW.value:
            block_group_index = block.get("parent_index")
            rows_to_be_included[f"{virtual_record_id}_{block_group_index}"].append((index,float(result.get("score",0.0))))
            continue
        elif block_type == GroupType.TABLE.value:
            table_data = block.get("data",{})
            table_metadata = block.get("table_metadata", {})
            children = block.get("children")

            # Handle both old and new children formats
            if children:
                if isinstance(children, dict) and 'block_ranges' in children:
                    # New range-based format
                    block_ranges = children.get('block_ranges', [])
                    first_block_index = block_ranges[0].get('start') if block_ranges else None
                    last_block_index = block_ranges[-1].get('end') if block_ranges else None
                    # Get all block indices from ranges
                    all_block_indices = []
                    for range_obj in block_ranges:
                        start = range_obj.get('start')
                        end = range_obj.get('end')
                        if start is not None and end is not None:
                            all_block_indices.extend(range(start, end + 1))
                else:
                    # Old format (list of BlockContainerIndex)
                    first_block_index = children[0].get("block_index") if len(children) > 0 else None
                    last_block_index = children[-1].get("block_index") if len(children) > 0 else None
                    all_block_indices = [child.get("block_index") for child in children if child.get("block_index") is not None]
            else:
                first_block_index = None
                last_block_index = None
                all_block_indices = []

            result["block_index"] = first_block_index
            if first_block_index is not None:
                adjacent_chunks[virtual_record_id].append(first_block_index-1)
                adjacent_chunks[virtual_record_id].append(last_block_index+1)

                num_of_cells = table_metadata.get("num_of_cells", None) if isinstance(table_metadata, dict) else None
                if num_of_cells is None:
                    is_large_table = True
                else:
                    is_large_table = num_of_cells > MAX_CELLS_IN_TABLE_THRESHOLD
                table_summary = table_data.get("table_summary","")

                if not is_large_table:
                    child_results=[]
                    for child_block_index in all_block_indices:
                        child_id = f"{virtual_record_id}-{child_block_index}"
                        if child_id in seen_chunks:
                            continue
                        seen_chunks.add(child_id)
                        if child_block_index < len(blocks):
                            child_block = blocks[child_block_index]
                            row_text = child_block.get("data", {}).get("row_natural_language_text", "")

                            # Create a result for the table row
                            child_result = {
                                "content": row_text,
                                "block_type": BlockType.TABLE_ROW.value,
                                "virtual_record_id": virtual_record_id,
                                "block_index": child_block_index,
                                "metadata": get_enhanced_metadata(record, child_block, meta),
                                "score": float(result.get("score",0.0)),
                                "citationType": "vectordb|document",
                            }
                            child_results.append(child_result)

                    table_result = {
                        "content":(table_summary,child_results),
                        "block_type": GroupType.TABLE.value,
                        "virtual_record_id": virtual_record_id,
                        "block_index": first_block_index,
                        "block_group_index": index,
                        "metadata": get_enhanced_metadata(record,block,meta),
                    }
                    flattened_results.append(table_result)
                    continue
                else:
                    rows_to_be_included[f"{virtual_record_id}_{index}"]=[]
                    continue
            else:
                continue
        elif block.get("parent_index") is not None:
            parent_index = block.get("parent_index")
            group_text_result = build_group_text(block_groups, blocks, parent_index, virtual_record_id, seen_chunks)
            if group_text_result is None:
                continue
            label, first_child_block_index, content = group_text_result
            result["content"] = content
            result["block_type"] = label
            result["virtual_record_id"] = virtual_record_id
            result["block_index"] = first_child_block_index
            result["block_group_index"] = parent_index
            result["metadata"] = get_enhanced_metadata(record, blocks[first_child_block_index], meta)
            flattened_results.append(result)
            continue


        result["virtual_record_id"] = virtual_record_id
        if "block_index" not in result:
            result["block_index"] = index
        enhanced_metadata = get_enhanced_metadata(record,block,meta)
        result["metadata"] = enhanced_metadata
        flattened_results.append(result)

    for key,rows_tuple in rows_to_be_included.items():
        sorted_rows_tuple = sorted(rows_tuple)
        virtual_record_id,block_group_index = key.split("_")
        block_group_index = int(block_group_index)
        record = virtual_record_id_to_result[virtual_record_id]
        if record is None:
            continue
        block_container = record.get("block_containers",{})
        blocks = block_container.get("blocks",[])
        block_groups = block_container.get("block_groups",[])
        block_group = block_groups[block_group_index]
        table_summary = block_group.get("data",{}).get("table_summary","")
        child_results = []
        for row_index,row_score in sorted_rows_tuple:
            block = blocks[row_index]
            block_type = block.get("type")
            if block_type == BlockType.TABLE_ROW.value:
                block_text = block.get("data",{}).get("row_natural_language_text","")
                enhanced_metadata = get_enhanced_metadata(record,block,{})
                child_results.append({
                    "content": block_text,
                    "block_type": block_type,
                    "metadata": enhanced_metadata,
                    "virtual_record_id": virtual_record_id,
                    "block_index": row_index,
                    "citationType": "vectordb|document",
                    "score": row_score,
                })
        if sorted_rows_tuple:
            first_child_block_index = sorted_rows_tuple[0][0]
            adjacent_chunks[virtual_record_id].append(first_child_block_index-1)
            if len(sorted_rows_tuple) > 1:
                last_child_block_index = sorted_rows_tuple[-1][0]
                adjacent_chunks[virtual_record_id].append(last_child_block_index+1)

        # Skip creating table_result if no rows were found
        if not sorted_rows_tuple:
            continue

        table_result = {
            "content":(table_summary,child_results),
            "block_type": GroupType.TABLE.value,
            "virtual_record_id": virtual_record_id,
            "block_index": first_child_block_index,
            "block_group_index": block_group_index,
            "metadata": get_enhanced_metadata(record,block_group,{}),
        }
        flattened_results.append(table_result)



    if not from_tool and not from_retrieval_service:
        for virtual_record_id,adjacent_chunks_list in adjacent_chunks.items():
            for index in adjacent_chunks_list:
                chunk_id = f"{virtual_record_id}-{index}"
                if chunk_id in seen_chunks:
                    continue
                seen_chunks.add(chunk_id)
                record = virtual_record_id_to_result[virtual_record_id]
                if record is None:
                    continue
                blocks  = record.get("block_containers",{}).get("blocks",[])
                if index < len(blocks) and index >= 0:
                    block = blocks[index]
                    block_type = block.get("type")
                    if block_type == BlockType.TEXT.value:
                        block_text = block.get("data","")
                        enhanced_metadata = get_enhanced_metadata(record,block,{})
                        flattened_results.append({
                            "content": block_text,
                            "block_type": block_type,
                            "metadata": enhanced_metadata,
                            "virtual_record_id": virtual_record_id,
                            "block_index": index,
                            "citationType": "vectordb|document",
                        })

    # Store point_id_to_blockIndex mappings separately for old type results
    # This mapping is used to convert point_id from search results to block index
    point_id_to_blockIndex_mappings = {}

    for result in old_type_results:
        virtual_record_id = result.get("metadata",{}).get("virtualRecordId")
        meta = result.get("metadata",{})

        if virtual_record_id not in virtual_record_id_to_result:
            record,point_id_to_blockIndex = await create_record_from_vector_metadata(meta,org_id,virtual_record_id,blob_store)
            virtual_record_id_to_result[virtual_record_id] = record
            point_id_to_blockIndex_mappings[virtual_record_id] = point_id_to_blockIndex

        point_id = meta.get("point_id")
        point_id_to_blockIndex = point_id_to_blockIndex_mappings.get(virtual_record_id, {})
        if point_id not in point_id_to_blockIndex:
            logger.warning("Missing point_id mapping: virtual_record_id=%s point_id=%s", virtual_record_id, str(point_id))
            continue
        index = point_id_to_blockIndex[point_id]
        chunk_id = f"{virtual_record_id}-{index}"
        if chunk_id in seen_chunks:
            continue
        seen_chunks.add(chunk_id)

        record = virtual_record_id_to_result[virtual_record_id]
        if record is None:
            continue
        block_container = record.get("block_containers",{})
        blocks = block_container.get("blocks",[])
        block_groups = block_container.get("block_groups",[])

        block = blocks[index]
        block_type = block.get("type")
        result["block_type"] = block_type
        result["virtual_record_id"] = virtual_record_id
        result["block_index"] = index
        enhanced_metadata = get_enhanced_metadata(record,block,meta)
        result["metadata"] = enhanced_metadata
        flattened_results.append(result)

    return flattened_results

def get_enhanced_metadata(record:Dict[str, Any],block:Dict[str, Any],meta:Dict[str, Any]) -> Dict[str, Any]:
        try:
            virtual_record_id = record.get("virtual_record_id", "")
            block_type = block.get("type")
            citation_metadata = block.get("citation_metadata")
            if citation_metadata:
                page_num =  citation_metadata.get("page_number",None)
            else:
                page_num = None
            data = block.get("data")
            if data:
                if block_type == GroupType.TABLE.value:
                    # Handle both dict and string data types
                    if isinstance(data, dict):
                        # Use table_summary instead of table_markdown, with fallback for backward compatibility
                        block_text = data.get("table_summary", "") or data.get("table_markdown", "")
                    else:
                        block_text = str(data)
                elif block_type == BlockType.TABLE_ROW.value:
                    # Handle both dict and string data types
                    if isinstance(data, dict):
                        block_text = data.get("row_natural_language_text","")
                    else:
                        block_text = str(data)
                elif block_type == BlockType.TEXT.value:
                    block_text = data
                elif block_type == BlockType.IMAGE.value:
                    block_text = "image"
                else:
                    block_text = meta.get("blockText","")
            else:
                block_text = ""

            mime_type = record.get("mime_type")
            if not mime_type:
                mime_type = meta.get("mimeType")

            extension = meta.get("extension")
            if extension is None:
                extension = get_extension_from_mimetype(mime_type)

            block_num = meta.get("blockNum")
            if block_num is None:
                if extension == "xlsx" or extension == "tsv":
                    # Guard against non-dict data
                    if isinstance(data, dict):
                        block_num = [data.get("row_number", 1)]
                    else:
                        block_num = [1]
                elif extension == "csv":
                    if isinstance(data, dict):
                        block_num = [data.get("row_number", 1)-1]
                    else:
                        block_num = [0]
                else:
                    block_num = [block.get("index", 0) + 1]

            preview_renderable = meta.get("previewRenderable")
            if preview_renderable is None:
                preview_renderable = record.get("preview_renderable", True)

            hide_weburl = meta.get("hideWeburl")
            if hide_weburl is None:
                hide_weburl = record.get("hide_weburl", False)



            web_url = meta.get("webUrl") or record.get("weburl", "")
            origin = meta.get("origin") or record.get("origin", "")
            recordId = meta.get("recordId") or record.get("id", "")
            if hide_weburl and recordId:
                web_url = f"/record/{recordId}"
            elif web_url and origin != "UPLOAD":
                web_url = generate_text_fragment_url(web_url, block_text)

            enhanced_metadata = {
                        "orgId": meta.get("orgId") or record.get("org_id", ""),
                        "recordId": recordId,
                        "virtualRecordId": virtual_record_id,
                        "recordName": meta.get("recordName") or record.get("record_name", ""),
                        "recordType": record.get("record_type", ""),
                        "recordVersion": record.get("version", ""),
                        "origin": origin,
                        "connector": meta.get("connector") or record.get("connector_name", ""),
                        "blockText": block_text,
                        "blockType": str(block_type),
                        "bounding_box": extract_bounding_boxes(block.get("citation_metadata")),
                        "pageNum":[page_num],
                        "extension": extension,
                        "mimeType": mime_type,
                        "blockNum":block_num,
                        "webUrl": web_url,
                        "previewRenderable": preview_renderable,
                        "hideWeburl": hide_weburl,
                    }
            if extension == "xlsx" or meta.get("sheetName"):
                if isinstance(data, dict):
                    enhanced_metadata["sheetName"] = data.get("sheet_name", "")
                else:
                    enhanced_metadata["sheetName"] = meta.get("sheetName", "")
            if extension == "xlsx" or meta.get("sheetNum"):
                if isinstance(data, dict):
                    enhanced_metadata["sheetNum"] = data.get("sheet_number", 1)
                else:
                    enhanced_metadata["sheetNum"] = meta.get("sheetNum", 1)
            return enhanced_metadata
        except Exception as e:
            raise e

def extract_bounding_boxes(citation_metadata) -> List[Dict[str, float]]:
        """Safely extract bounding box data from citation metadata"""
        if not citation_metadata or not citation_metadata.get("bounding_boxes"):
            return None

        bounding_boxes = citation_metadata.get("bounding_boxes")
        if not isinstance(bounding_boxes, list):
            return None

        try:
            result = []
            for point in bounding_boxes:
                if "x" in point and "y" in point:
                    result.append({"x": point.get("x"), "y": point.get("y")})
                else:
                    return None
            return result
        except Exception as e:
            raise e

async def get_record(virtual_record_id: str,virtual_record_id_to_result: Dict[str, Dict[str, Any]],blob_store: BlobStorage,org_id: str,virtual_to_record_map: Dict[str, Dict[str, Any]]=None,graph_provider: Optional[IGraphDBProvider] = None,frontend_url: Optional[str] = None) -> None:
    try:
        record = await blob_store.get_record_from_storage(virtual_record_id=virtual_record_id, org_id=org_id)
        if record:
            graphDb_record = (virtual_to_record_map or {}).get(virtual_record_id)
            if graphDb_record:
                record_type = graphDb_record.get("recordType")
                record_key = graphDb_record.get("_key")

                record["id"] = record_key
                record["org_id"] = org_id
                record["record_name"] = graphDb_record.get("recordName")
                record["record_type"] = record_type
                record["version"] = graphDb_record.get("version")
                record["origin"] = graphDb_record.get("origin")
                record["connector_name"] = graphDb_record.get("connectorName")
                record["weburl"] = graphDb_record.get("webUrl")
                record["preview_renderable"] = graphDb_record.get("previewRenderable", True)
                record["hide_weburl"] = graphDb_record.get("hideWeburl", False)
                record["mime_type"] = graphDb_record.get("mimeType")
                record["source_created_at"] = graphDb_record.get("sourceCreatedAtTimestamp")
                record["source_updated_at"] = graphDb_record.get("sourceLastModifiedTimestamp")

                # Fetch type-specific metadata and generate formatted string
                graph_doc = None
                if graph_provider and record_key:
                    try:
                        # Determine collection name based on record type

                        collection = collection_map.get(record_type)

                        if collection:
                            graph_doc = await graph_provider.get_document(
                                document_key=record_key,
                                collection=collection
                            )
                    except Exception as e:
                        # Log but don't fail - graceful degradation
                        logger.error(f"Error fetching type-specific metadata for record {record_key}: {str(e)}")

                record_instance = create_record_instance_from_dict(record, graph_doc)
                if record_instance:
                    record["context_metadata"] = record_instance.to_llm_context(frontend_url=frontend_url)
                else:
                    record["context_metadata"] = ""

            virtual_record_id_to_result[virtual_record_id] = record
        else:
            virtual_record_id_to_result[virtual_record_id] = None

    except Exception as e:
        raise e

async def create_record_from_vector_metadata(metadata: Dict[str, Any], org_id: str, virtual_record_id: str,blob_store: BlobStorage) -> Tuple[Dict[str, Any], Dict[str, int]]:
    try:
        # Lazy import to avoid circular dependency: chat_helpers -> ContainerUtils -> RetrievalService -> chat_helpers
        from app.containers.utils.utils import ContainerUtils
        summary = metadata.get("summary", "")
        categories = [metadata.get("categories", "")]
        topics = metadata.get("topics", "")
        sub_category_level_1 = metadata.get("subcategoryLevel1","")
        sub_category_level_2 = metadata.get("subcategoryLevel2","")
        sub_category_level_3 = metadata.get("subcategoryLevel3","")
        languages = metadata.get("languages", "")
        departments = metadata.get("departments", "")
        semantic_metadata = {
            "summary": summary,
            "categories": categories,
            "topics": topics,
            "sub_category_level_1": sub_category_level_1,
            "sub_category_level_2": sub_category_level_2,
            "sub_category_level_3": sub_category_level_3,
            "languages": languages,
            "departments": departments,
        }

        extension = get_extension_from_mimetype(metadata.get("mimeType",""))

        record = {
            "id": metadata.get("recordId", ""),
            "org_id": org_id,
            "record_name": metadata.get("recordName", ""),
            "record_type": metadata.get("recordType", ""),
            "external_record_id": metadata.get("externalRecordId", virtual_record_id),
            "external_revision_id": metadata.get("externalRevisionId", virtual_record_id),
            "version": metadata.get("version",""),
            "origin": metadata.get("origin",""),
            "connector_name": metadata.get("connector") or metadata.get("connectorName",""),
            "virtual_record_id": virtual_record_id,
            "mime_type": metadata.get("mimeType",""),
            "created_at": metadata.get("createdAtTimestamp", ""),
            "updated_at": metadata.get("updatedAtTimestamp", ""),
            "source_created_at": metadata.get("sourceCreatedAtTimestamp", ""),
            "source_updated_at": metadata.get("sourceLastModifiedTimestamp", ""),
            "weburl": metadata.get("webUrl", ""),
            "semantic_metadata": semantic_metadata,
            "extension": extension,
        }
        blocks = []
        container_utils = ContainerUtils()

        vector_db_service = await container_utils.get_vector_db_service(blob_store.config_service)

# Create filter
        payload_filter = await vector_db_service.filter_collection(must={
            "virtualRecordId": virtual_record_id,
        })

# Scroll through all points with the filter
        points = []

        result = await vector_db_service.scroll(
                collection_name=VECTOR_DB_COLLECTION_NAME,
                scroll_filter=payload_filter,
                limit=100000,
            )


        points.extend(result[0])

        point_id_to_blockIndex = {}
        new_payloads = []

        for i,point in enumerate(points):
            payload = point.payload
            if payload:
                meta = payload.get("metadata")
                page_content = payload.get("page_content")
                block = create_block_from_metadata(meta,page_content)
                point_id_to_blockIndex[point.id] = i
                blocks.append(block)
                new_payloads.append({"metadata":{
                    "virtualRecordId": virtual_record_id,
                    "blockIndex": block.get("index"),
                    "orgId": org_id,
                    "isBlockGroup": False,
                    "isBlock": False,
                },
                "page_content": payload.get("page_content")
                })

        sorted_blocks = sorted(blocks, key=lambda x: x.get("index", 0))
        for i,block in enumerate(sorted_blocks):
            block["index"] = i

        record["block_containers"] = {
            "blocks": sorted_blocks,
            "block_groups": []
        }

        return record,point_id_to_blockIndex
    except Exception as e:
        raise e


def create_block_from_metadata(metadata: Dict[str, Any],page_content: str) -> Dict[str, Any]:
    try:
        page_num = metadata.get("pageNum")
        if isinstance(page_num, (list,tuple)):
            page_num = page_num[0] if page_num else None

        citation_metadata = {
            "page_number": page_num,
            "bounding_boxes": metadata.get("bounding_box")
        }

        extension = metadata.get("extension")
        if extension == "docx":
            data = page_content
        else:
            data = metadata.get("blockText",page_content)

        block_type = metadata.get("blockType","text")
        # Create the Block structure
        block = {
            "id": str(uuid4()),  # Generate unique ID
            "index": metadata.get("blockNum")[0] if metadata.get("blockNum") and len(metadata.get("blockNum")) > 0 else 0, # TODO: blockNum indexing might be different for different file types
            "type": block_type,
            "format": "txt",
            "comments": [],
            "source_creation_date": metadata.get("sourceCreatedAtTimestamp"),
            "source_update_date": metadata.get("sourceLastModifiedTimestamp"),
            "data": data,
            "weburl": metadata.get("webUrl"),
            "citation_metadata": citation_metadata,
        }
        return block
    except Exception as e:
        raise e

MAX_CELLS_IN_TABLE_THRESHOLD = 250  # Equivalent to ~700 words assuming ~2-3 words per cell


def _find_first_block_index_recursive(block_groups: List[Dict[str, Any]], children: Union[Dict[str, Any], List[Dict[str, Any]]]) -> int | None:
    """Recursively search through the first child to find the first block_index.

    Args:
        block_groups: List of block groups
        children: BlockGroupChildren object or List of child container indices (old format)

    Returns:
        First block_index found in the first child, or None if not found
    """
    if not children:
        return None

    # Handle new range-based format
    if isinstance(children, dict) and 'block_ranges' in children:
        block_ranges = children.get('block_ranges', [])
        if block_ranges:
            # Return the first index from the first range
            return block_ranges[0].get('start')

        # If no block ranges, check block group ranges
        block_group_ranges = children.get('block_group_ranges', [])
        if block_group_ranges:
            first_bg_index = block_group_ranges[0].get('start')
            if first_bg_index is not None and 0 <= first_bg_index < len(block_groups):
                nested_group = block_groups[first_bg_index]
                nested_children = nested_group.get("children")
                if nested_children:
                    return _find_first_block_index_recursive(block_groups, nested_children)
        return None

    # Handle old format (list of BlockContainerIndex)
    if isinstance(children, list) and len(children) > 0:
        first_child = children[0]
        block_index = first_child.get("block_index")
        if block_index is not None:
            return block_index

        block_group_index = first_child.get("block_group_index")
        if block_group_index is not None and 0 <= block_group_index < len(block_groups):
            nested_group = block_groups[block_group_index]
            nested_children = nested_group.get("children", [])
            if nested_children:
                return _find_first_block_index_recursive(block_groups, nested_children)

    return None


def _extract_text_content_recursive(
    block_groups: List[Dict[str, Any]],
    blocks: List[Dict[str, Any]],
    children: Union[Dict[str, Any], List[Dict[str, Any]]],
    virtual_record_id: str = None,
    seen_chunks: set = None,
    depth: int = 0,
) -> str:
    """Recursively extract text content from children and nested children.

    Args:
        block_groups: List of block groups
        blocks: List of blocks
        children: BlockGroupChildren object or List of child container indices (old format)
        virtual_record_id: Optional virtual record ID for tracking seen chunks
        seen_chunks: Optional set to track seen chunks

    Returns:
        Concatenated text content from all children and nested children
    """
    content = ""
    indent = "  " * depth

    # Handle new range-based format
    if isinstance(children, dict) and ('block_ranges' in children or 'block_group_ranges' in children):
        # Process block ranges
        block_ranges = children.get('block_ranges', [])
        for range_obj in block_ranges:
            start = range_obj.get('start')
            end = range_obj.get('end')
            if start is not None and end is not None:
                for block_index in range(start, end + 1):
                    # Track seen chunks
                    if virtual_record_id is not None and seen_chunks is not None:
                        child_id = f"{virtual_record_id}-{block_index}"
                        seen_chunks.add(child_id)

                    # Extract text from block
                    if 0 <= block_index < len(blocks):
                        child_block = blocks[block_index]
                        if child_block.get("type") == BlockType.TEXT.value:
                            content += f"{indent}{child_block.get('data', '')}\n"

        # Process block group ranges
        block_group_ranges = children.get('block_group_ranges', [])
        for range_obj in block_group_ranges:
            start = range_obj.get('start')
            end = range_obj.get('end')
            if start is not None and end is not None:
                for block_group_index in range(start, end + 1):
                    # Track seen chunks
                    if virtual_record_id is not None and seen_chunks is not None:
                        child_id = f"{virtual_record_id}-{block_group_index}-block_group"
                        seen_chunks.add(child_id)

                    # Recursively process nested children
                    if 0 <= block_group_index < len(block_groups):
                        nested_group = block_groups[block_group_index]
                        nested_children = nested_group.get("children")
                        if nested_children:
                            content += _extract_text_content_recursive(
                                block_groups, blocks, nested_children, virtual_record_id, seen_chunks, depth + 1
                            )
        return content

    # Handle old format (list of BlockContainerIndex)
    if not isinstance(children, list):
        return content

    for child in children:
        block_index = child.get("block_index")
        block_group_index = child.get("block_group_index")

        # Track seen chunks if virtual_record_id is provided
        if virtual_record_id is not None and seen_chunks is not None:
            if block_index is not None:
                child_id = f"{virtual_record_id}-{block_index}"
                seen_chunks.add(child_id)
            elif block_group_index is not None:
                child_id = f"{virtual_record_id}-{block_group_index}-block_group"
                seen_chunks.add(child_id)

        # If child has a direct block_index, extract text from that block
        if block_index is not None and 0 <= block_index < len(blocks):
            child_block = blocks[block_index]
            if child_block.get("type") == BlockType.TEXT.value:
                content += f"{indent}{child_block.get('data', '')}\n"

        # If child has a block_group_index, recursively process nested children
        elif block_group_index is not None and 0 <= block_group_index < len(block_groups):
            nested_group = block_groups[block_group_index]
            nested_children = nested_group.get("children", [])
            if nested_children:
                content += _extract_text_content_recursive(
                    block_groups, blocks, nested_children, virtual_record_id, seen_chunks, depth + 1
                )

    return content


def build_group_text(block_groups: List[Dict[str, Any]], blocks: List[Dict[str, Any]], parent_index: int, virtual_record_id: str = None, seen_chunks: set = None) -> Tuple[str, int, str] | None:
    """Extract grouped text content and first child index for supported group types.

    Returns (label, first_child_block_index, content) or None if invalid or unsupported.
    """
    if parent_index is None or parent_index < 0 or parent_index >= len(block_groups):
        return None

    parent_block = block_groups[parent_index]
    label = parent_block.get("type")
    valid_group_labels = [
        GroupType.LIST.value,
        GroupType.ORDERED_LIST.value,
        GroupType.FORM_AREA.value,
        GroupType.INLINE.value,
        GroupType.KEY_VALUE_AREA.value,
        GroupType.TEXT_SECTION.value,
    ]

    if label not in valid_group_labels:
        return None

    children = parent_block.get("children", [])
    if not children:
        return None

    first_child_block_index = _find_first_block_index_recursive(block_groups, children)
    if first_child_block_index is None:
        logger.warning(
            "⚠️ build_group_text: first_child_block_index is None for parent_index=%s",
            parent_index
        )
        return None

    content = _extract_text_content_recursive(
        block_groups, blocks, children, virtual_record_id, seen_chunks, 0
    )
    return label, first_child_block_index, content


def build_group_blocks(block_groups: List[Dict[str, Any]], blocks: List[Dict[str, Any]], parent_index: int) -> List[Dict[str, Any]]:
    if parent_index < 0 or parent_index >= len(block_groups):
        return None
    parent_block = block_groups[parent_index]

    children = parent_block.get("children")
    if not children:
        return []

    result_blocks = []

    # Handle new range-based format
    if isinstance(children, dict) and 'block_ranges' in children:
        block_ranges = children.get('block_ranges', [])
        for range_obj in block_ranges:
            start = range_obj.get('start')
            end = range_obj.get('end')
            if start is not None and end is not None:
                for block_index in range(start, end + 1):
                    if 0 <= block_index < len(blocks):
                        result_blocks.append(blocks[block_index])
        return result_blocks

    # Handle old format (list of BlockContainerIndex)
    if isinstance(children, list):
        for child in children:
            block_index = child.get("block_index")
            if block_index is not None and 0 <= block_index < len(blocks):
                result_blocks.append(blocks[block_index])

    return result_blocks


def record_to_message_content(record: Dict[str, Any], final_results: List[Dict[str, Any]] = None) -> str|None:
    """
    Convert a record JSON object to message content format matching get_message_content.

    Args:
        record: The record JSON object containing block_containers and other metadata
        final_results: Optional list of final results for context

    Returns:
        String of message content in the same format as get_message_content
    """

    try:
        record_string = ""
        context_metadata = record.get("context_metadata", "")
        record_string += f"""<record>\n{context_metadata}
Record blocks (sorted):\n\n"""
        # Process blocks
        block_containers = record.get("block_containers", {})
        blocks = block_containers.get("blocks", [])
        block_groups = block_containers.get("block_groups", [])

        seen_block_groups = set()
        record_number = 1
        # Determine record_number consistent with previously sent context if possible
        try:
            if final_results:
                # Build ordered list of unique virtual_record_ids as used in get_message_content
                ordered_unique_vrids = []
                seen_vrids = set()
                for res in final_results:
                    vrid = res.get("virtual_record_id")
                    if vrid is not None and vrid not in seen_vrids:
                        seen_vrids.add(vrid)
                        ordered_unique_vrids.append(vrid)

                # Map current record's virtual_record_id to its position (1-based)
                current_vrid = record.get("virtual_record_id")
                if current_vrid in ordered_unique_vrids:
                    record_number = ordered_unique_vrids.index(current_vrid) + 1
        except Exception:
            return []

        # Group blocks with parent_index (like table rows) for processing as block groups

        # Process individual blocks
        for block in blocks:
            block_index = block.get("index", 0)
            block_type = block.get("type")

            block_number = f"R{record_number}-{block_index}"
            data = block.get("data", "")

            if block_type == BlockType.IMAGE.value:
                continue
            elif block_type == BlockType.TEXT.value and block.get("parent_index") is None:
                record_string += f"* Block Number: {block_number}\n* Block Type: {block_type}\n* Block Content: {data}\n\n"
            elif block_type == BlockType.TABLE_ROW.value:
                # Group table rows by their parent_index for block group processing
                block_group_index = block.get("parent_index")
                block_group_id = f"{record.get('virtual_record_id', '')}-{block_group_index}"
                if block_group_id in seen_block_groups:
                    continue
                seen_block_groups.add(block_group_id)
                if block_group_index is not None:
                    corresponding_block_group = block_groups[block_group_index]

                    # Process the block group with its child rows
                    block_type = corresponding_block_group.get("type")
                    data = corresponding_block_group.get("data", {})

                    if block_type == GroupType.TABLE.value:
                        table_summary = data.get("table_summary", "") if isinstance(data, dict) else str(data)

                        # Get block indices from children (handle both old and new formats)
                        children = corresponding_block_group.get("children")
                        rows_to_be_included_list = []
                        if children:
                            if isinstance(children, dict) and 'block_ranges' in children:
                                # New range-based format
                                for range_obj in children.get('block_ranges', []):
                                    start = range_obj.get('start')
                                    end = range_obj.get('end')
                                    if start is not None and end is not None:
                                        rows_to_be_included_list.extend(range(start, end + 1))
                            elif isinstance(children, list):
                                # Old format
                                rows_to_be_included_list = [child.get("block_index") for child in children if child.get("block_index") is not None]

                        # Process table rows
                        child_results = []
                        for row_index in rows_to_be_included_list:
                            if row_index < len(blocks):
                                block = blocks[row_index]
                                block_data = block.get("data", {})
                                if isinstance(block_data, dict):
                                    row_text = block_data.get("row_natural_language_text", "")
                                else:
                                    row_text = str(block_data)

                                child_results.append({
                                    "content": row_text,
                                    "block_index": row_index,
                                })

                        if child_results:
                            template = Template(table_prompt)
                            rendered_form = template.render(
                                block_group_index=block_group_index,
                                table_summary=table_summary,
                                table_rows=child_results,
                                record_number=record_number,
                            )
                            record_string += f"{rendered_form}\n\n"


            elif(block.get("parent_index") is not None):
                parent_index = block.get("parent_index")
                block_group_id = f"{record.get('virtual_record_id', '')}-{parent_index}"
                if block_group_id in seen_block_groups:
                    continue
                template = Template(block_group_prompt)
                if parent_index >= len(block_groups):
                    continue
                block_group = block_groups[parent_index]
                group_blocks = build_group_blocks(block_groups, blocks, parent_index)


                if not group_blocks:
                    continue
                seen_block_groups.add(block_group_id)
                rendered_form = template.render(
                    block_group_index=parent_index,
                    label=block_group.get("type"),
                    blocks=group_blocks,
                    record_number=record_number,
                )
                record_string += f"{rendered_form}\n\n"
            else:
                record_string += f"* Block Number: {block_number}\n* Block Type: {block_type}\n* Block Content: {data}\n\n"

        return record_string
    except Exception as e:
        raise Exception(f"Error in record_to_message_content: {e}") from e


def get_message_content(flattened_results: List[Dict[str, Any]], virtual_record_id_to_result: Dict[str, Any], user_data: str, query: str, logger, mode: str = "json") -> str:
    content = []

    # Use simple prompt for quick mode
    if mode == "simple":
        # Build simple context - just blocks with numbers
        chunks = []
        seen_blocks = set()
        for result in flattened_results:
            virtual_record_id = result.get("virtual_record_id")
            block_index = result.get("block_index")
            result_id = f"{virtual_record_id}_{block_index}"

            if result_id not in seen_blocks:
                seen_blocks.add(result_id)
                block_type = result.get("block_type")

                # Skip images for simplicity
                if block_type == BlockType.IMAGE.value:
                    continue

                # Get content text
                if block_type == GroupType.TABLE.value:
                    table_summary, child_results = result.get("content")
                    content_text = f"Table: {table_summary}"
                else:
                    content_text = result.get("content", "")

                chunks.append({
                    "metadata": {
                        "blockText": content_text,
                        "recordName": result.get("record_name")
                    }
                })

        # Render simple prompt
        template = Template(qna_prompt_simple)
        rendered_form = template.render(
            query=query,
            chunks=chunks
        )

        content.append({
            "type": "text",
            "text": rendered_form
        })

        return content

    else:
        # Standard/JSON mode - use detailed prompt
        template = Template(qna_prompt_instructions_1)
        rendered_form = template.render(
                    user_data=user_data,
                    query=query,
                    rephrased_queries=[],
                    mode=mode,
                    )

        content.append({
                    "type": "text",
                    "text": rendered_form
                })

        seen_virtual_record_ids = set()
        seen_blocks = set()
        record_number = 1
        for i,result in enumerate(flattened_results):
            virtual_record_id = result.get("virtual_record_id")
            if virtual_record_id not in seen_virtual_record_ids:
                if i > 0:
                    content.append({
                        "type": "text",
                        "text": "</record>"
                    })
                    record_number = record_number + 1
                seen_virtual_record_ids.add(virtual_record_id)
                record = virtual_record_id_to_result[virtual_record_id]
                if record is None:
                    continue

                template = Template(qna_prompt_context)
                rendered_form = template.render(
                    context_metadata=record.get("context_metadata", ""),
                )
                content.append({
                    "type": "text",
                    "text": rendered_form
                })

            result_id = f"{virtual_record_id}_{result.get('block_index')}"
            if result_id not in seen_blocks:
                seen_blocks.add(result_id)
                block_type = result.get("block_type")
                block_index = result.get("block_index")
                block_number = f"R{record_number}-{block_index}"
                if block_type == BlockType.IMAGE.value:
                    if result.get("content").startswith("data:image/"):
                        content.append({
                            "type": "text",
                            "text": f"* Block Number: {block_number}\n* Block Type: {block_type}\n* Block Content:"
                        })
                        content.append({
                            "type": "image_url",
                            "image_url": {"url": result.get("content")}
                        })
                    else:
                        content.append({
                            "type": "text",
                            "text": f"* Block Number: {block_number}\n* Block Type: image description\n* Block Content: {result.get('content')}\n\n"
                        })
                elif block_type == GroupType.TABLE.value:
                    table_summary,child_results = result.get("content")
                    if child_results:
                        template = Template(table_prompt)
                        rendered_form = template.render(
                            block_group_index=result.get("block_group_index"),
                            table_summary=table_summary,
                            table_rows=child_results,
                            record_number=record_number,
                        )
                        content.append({
                            "type": "text",
                            "text": f"{rendered_form}\n\n"
                        })
                    else:
                        content.append({
                            "type": "text",
                            "text": f"* Block Group Number: R{record_number}-{result.get('block_group_index')}\n* Block Type: table summary \n* Block Content: {table_summary}\n\n"
                        })
                elif block_type == BlockType.TEXT.value:
                    content.append({
                        "type": "text",
                        "text": f"* Block Number: {block_number}\n* Block Type: {block_type}\n* Block Content: {result.get('content')}\n\n"
                    })
                elif block_type == BlockType.TABLE_ROW.value:
                    content.append({
                        "type": "text",
                        "text": f"* Block Number: {block_number}\n* Block Type: table row\n* Block Content: {result.get('content')}\n\n"
                    })
                elif block_type in group_types:
                    content.append({
                        "type": "text",
                        "text": f"* Block Number: {block_number}\n* Block Type: {block_type}\n* Block Content: {result.get('content')}\n\n"
                    })
                else:
                    content.append({
                        "type": "text",
                        "text": f"* Block Number: {block_number}\n* Block Type: {block_type}\n* Block Content: {result.get('content')}\n\n"
                    })
            else:
                continue

        # Render instructions_2 with mode parameter
        template_instructions_2 = Template(qna_prompt_instructions_2)
        rendered_instructions_2 = template_instructions_2.render(mode=mode)

        content.append({
            "type": "text",
            "text": f"</record>\n</context>\n\n{rendered_instructions_2}"
        })
        return content



def get_message_content_for_tool(flattened_results: List[Dict[str, Any]], virtual_record_id_to_result: Dict[str, Any], final_results: List[Dict[str,    Any]]) -> List[str]:
    virtual_record_id_to_record_number = {}
    seen_virtual_record_ids = set()
    record_number = 1

    for result in final_results:
        virtual_record_id = result.get("virtual_record_id")
        if virtual_record_id not in seen_virtual_record_ids:
            seen_virtual_record_ids.add(virtual_record_id)
            virtual_record_id_to_record_number[virtual_record_id] = record_number
            record_number = record_number + 1
    all_record_strings = []
    seen_blocks = set()
    seen_virtual_record_ids.clear()
    record_ids =[]
    record_string = ""
    for i,result in enumerate(flattened_results):
        virtual_record_id = result.get("virtual_record_id")
        if virtual_record_id not in seen_virtual_record_ids:
            if i > 0:
                all_record_strings.append(record_string)
                record_string = ""
            seen_virtual_record_ids.add(virtual_record_id)
            record = virtual_record_id_to_result[virtual_record_id]
            if record is None:
                continue

            record_string += f"""<record>\n{record.get("context_metadata", "")}
Record blocks (sorted):\n\n"""
            record_ids.append(record.get("id"))

        result_id = f"{virtual_record_id}_{result.get('block_index')}"
        if result_id not in seen_blocks:
            seen_blocks.add(result_id)
            block_type = result.get("block_type")
            block_index = result.get("block_index")
            record_number = virtual_record_id_to_record_number[virtual_record_id] if virtual_record_id in virtual_record_id_to_record_number else None
            if record_number is None:
                continue
            block_number = f"R{record_number}-{block_index}"
            if block_type == GroupType.TABLE.value:
                table_summary,child_results = result.get("content")
                if child_results:
                    template = Template(table_prompt)
                    rendered_form = template.render(
                        block_group_index=result.get("block_group_index"),
                        table_summary=table_summary,
                        table_rows=child_results,
                        record_number=record_number,
                    )
                    record_string += f"{rendered_form}\n\n"
                else:
                    record_string += f"* Block Group Number: R{record_number}-{result.get('block_group_index')}\n* Block Type: table summary \n* Block Content: {table_summary}\n\n"
            elif block_type == BlockType.TEXT.value:
                record_string += f"* Block Number: {block_number}\n* Block Type: {block_type}\n* Block Content: {result.get('content')}\n\n"
            elif block_type != BlockType.IMAGE.value:
                record_string += f"* Block Number: {block_number}\n* Block Type: {block_type}\n* Block Content: {result.get('content')}\n\n"
        else:
            continue

    all_record_strings.append(record_string)

    return all_record_strings

def block_group_to_message_content(tool_result: Dict[str, Any], final_results: List[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    content = []
    block_group = tool_result.get("block_group", {})
    block_group_index = block_group.get("index", 0)
    record_number = tool_result.get("record_number", 1)
    record_id = tool_result.get("record_id", "")
    record_name = tool_result.get("record_name", "")
    content.append({
            "type": "text",
            "text": f"""<record>
            * Record Id: {record_id}
            * Record Name: {record_name}
            * Block Group:
            """
        })

    child_results = []
    blocks = block_group.get("blocks",[])
    table_summary = block_group.get("data",{}).get("table_summary","")
    for block in blocks:
        block_data = block.get("data", {})
        if isinstance(block_data, dict):
            row_text = block_data.get("row_natural_language_text", "")
        else:
            row_text = str(block_data)

        child_results.append({
            "content": row_text,
            "block_index": block.get("index", 0),
        })

    if child_results:
        template = Template(table_prompt)
        rendered_form = template.render(
            block_group_index=block_group_index,
            table_summary=table_summary,
            table_rows=child_results,
            record_number=record_number,
        )
        content.append({
            "type": "text",
            "text": rendered_form
        })
    else:
        content.append({
            "type": "text",
            "text": f"* Block Group Number: R{record_number}-{block_group_index}\n* Block Type: table summary\n* Block Content: {table_summary}"
        })
    content.append({
        "type": "text",
        "text": """</record>
        Now produce the final answer STRICTLY following the previously provided Output format.\n
        CRITICAL REQUIREMENTS:\n
        - Always include block citations (e.g., [R1-2]) wherever the answer is derived from blocks.\n
        - Use only one citation per bracket pair and ensure the numbers correspond to the block numbers shown above.\n
        - Return a single JSON object exactly as specified (answer, reason, confidence, answerMatchType, blockNumbers)."""
    })
    return content


def count_tokens_in_messages(messages: List[Any],enc) -> int:
    """
    Count the total number of tokens in a messages array.
    Supports both dict messages and LangChain message objects.

    Args:
        messages: List of message dictionaries or LangChain message objects

    Returns:
        Total number of tokens across all messages
    """
    logger.debug(
        "count_tokens_in_messages: starting token count for %d messages",
        len(messages) if messages else 0,
    )

    total_tokens = 0

    for message in messages:
        # Handle LangChain message objects (AIMessage, HumanMessage, ToolMessage, etc.)
        if hasattr(message, "content"):
            content = getattr(message, "content", "")
        # Handle dict messages
        elif isinstance(message, dict):
            content = message.get("content", "")
        else:
            # Skip unknown types
            logger.debug("count_tokens_in_messages: skipping unknown message type")
            continue

        # Handle different content types
        if isinstance(content, str):
            total_tokens += count_tokens_text(content,enc)
        elif isinstance(content, list):
            # Handle content as list of content objects (like in get_message_content)
            for content_item in content:
                if isinstance(content_item, dict):
                    if content_item.get("type") == "text":
                        text_content = content_item.get("text", "")
                        total_tokens += count_tokens_text(text_content,enc)
                    # Skip image_url and other non-text content for token counting
                elif isinstance(content_item, str):
                    total_tokens += count_tokens_text(content_item,enc)
        else:
            # Convert other types to string
            total_tokens += count_tokens_text(str(content),enc)

    return total_tokens


def count_tokens_text(text: str,enc) -> int:
    """Count tokens in text using tiktoken or fallback heuristic"""
    if not text:
        return 0
    if enc is not None:
        try:
            return len(enc.encode(text))
        except Exception:
            logger.warning("tiktoken encoding failed, falling back to heuristic.")
            pass
    else:
        try:
            import tiktoken  # type: ignore
            try:
                enc = tiktoken.get_encoding("cl100k_base")
                return len(enc.encode(text))
            except Exception:
                logger.warning("tiktoken encoding failed, falling back to heuristic.")
                pass
        except Exception:
            logger.warning("tiktoken encoding failed, falling back to heuristic.")
            pass

    return max(1, len(text) // 4)

def count_tokens(messages: List[Any], message_contents: List[str]) -> Tuple[int, int]:
    # Lazy import tiktoken; fall back to a rough heuristic if unavailable
    enc = None
    try:
        import tiktoken  # type: ignore
        try:
            enc = tiktoken.get_encoding("cl100k_base")
        except Exception:
            logger.warning("tiktoken encoding failed, falling back to heuristic.")
            enc = None
    except Exception:
        logger.warning("tiktoken import failed, falling back to heuristic.")
        enc = None


    current_message_tokens = count_tokens_in_messages(messages,enc)
    new_tokens = 0

    for message_content in message_contents:
        new_tokens += count_tokens_text(message_content,enc)


    return current_message_tokens, new_tokens



FRAGMENT_WORD_COUNT = 4


def extract_start_end_text(snippet: str) -> Tuple[str, str]:
    if not snippet:
        return "", ""

    PATTERN = re.compile(r'[a-zA-Z0-9 ]+')

    # --- Find start_text: first matching segment, first 4 words ---
    first_match = PATTERN.search(snippet)
    if not first_match:
        return "", ""

    first_text = first_match.group().strip()
    if not first_text:
        return "", ""

    words = first_text.split()
    start_text = " ".join(words[:FRAGMENT_WORD_COUNT])
    start_text_end = first_match.start() + len(first_text.split()[0])  # not needed yet

    # Compute exact end position of start_text in snippet
    # It starts at first_match.start() + leading whitespace offset
    leading_spaces = len(first_match.group()) - len(first_match.group().lstrip())
    start_text_begin = first_match.start() + leading_spaces
    start_text_end = start_text_begin + len(start_text)

    # --- Find end_text: last matching segment after start_text_end, last 4 words ---
    # Search backwards by scanning from start_text_end onward for the *last* match
    remaining = snippet[start_text_end:]

    # Find last match in remaining using finditer (but we only keep last)
    # Alternatively, search from the end using a reverse approach
    last_text = None
    for m in PATTERN.finditer(remaining):
        stripped = m.group().strip()
        if stripped:
            last_text = stripped

    if last_text:
        words = last_text.split()
        end_text = " ".join(words[-FRAGMENT_WORD_COUNT:])
    elif len(first_text.split()) > FRAGMENT_WORD_COUNT:
        word_count = len(first_text.split())
        diff = word_count - FRAGMENT_WORD_COUNT
        diff = min(FRAGMENT_WORD_COUNT, diff)
        # Fall back to last 4 words of the first segment
        end_text = " ".join(first_text.split()[-diff:])
    else:
        end_text = ""

    return start_text, end_text

def generate_text_fragment_url(base_url: str, text_snippet: str) -> str:
    """
    Generate a URL with text fragment for direct navigation to specific text.

    Format: url#:~:text=start_text,end_text

    Args:
        base_url: The base URL of the page
        text_snippet: The text to highlight/navigate to

    Returns:
        URL with text fragment, or base_url if encoding fails
    """
    if not base_url or not text_snippet:
        return base_url

    try:
        snippet = text_snippet.strip()
        if not snippet:
            return base_url

        start_text, end_text = extract_start_end_text(snippet)

        if not start_text:
            return base_url

        encoded_start = quote(start_text, safe='')
        encoded_end = None
        if end_text:
            encoded_end = quote(end_text, safe='')

        if '#' in base_url:
            base_url = base_url.split('#')[0]

        return f"{base_url}#:~:text={encoded_start}{(',' + encoded_end) if encoded_end else ''}"

    except Exception:
        return base_url



