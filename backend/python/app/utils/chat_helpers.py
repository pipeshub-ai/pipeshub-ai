import re
from collections import defaultdict
from typing import Any, Dict, List, Tuple
from uuid import uuid4

from jinja2 import Template

from app.models.blocks import BlockType, GroupType
from app.modules.qna.prompt_templates import (
    qna_prompt_context,
    qna_prompt_context_for_tool,
    qna_prompt_instructions_1,
    qna_prompt_instructions_2,
    table_prompt,
)
from app.modules.transformers.blob_storage import BlobStorage
from app.services.vector_db.const.const import VECTOR_DB_COLLECTION_NAME
from app.utils.mimetype_to_extension import get_extension_from_mimetype


async def get_flattened_results(result_set: List[Dict[str, Any]], blob_store: BlobStorage, org_id: str, is_multimodal_llm: bool, virtual_record_id_to_result: Dict[str, Dict[str, Any]],from_tool: bool = False,from_retrieval_service: bool = False) -> List[Dict[str, Any]]:
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
    for result in sorted_new_type_results:
        virtual_record_id = result["metadata"].get("virtualRecordId")
        meta = result.get("metadata")

        if virtual_record_id not in virtual_record_id_to_result:
            await get_record(meta,virtual_record_id,virtual_record_id_to_result,blob_store,org_id)



        if virtual_record_id not in adjacent_chunks:
            adjacent_chunks[virtual_record_id] = []

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
        if block_type == BlockType.TEXT.value:
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
            table_markdown = table_data.get("table_markdown","")
            children = block.get("children")
            first_block_index = children[0].get("block_index") if children and len(children) > 0 else None
            result["block_index"] = first_block_index
            if first_block_index is not None:
                adjacent_chunks[virtual_record_id].append(first_block_index-1)
                last_block_index = children[-1].get("block_index")
                adjacent_chunks[virtual_record_id].append(last_block_index+1)

                is_large_table = checkForLargeTable(table_markdown)
                table_summary = table_data.get("table_summary","")

                if is_large_table:
                    rows_to_be_included[f"{virtual_record_id}_{index}"]=[]
                    continue
                else:
                    child_results=[]
                    for child in children:
                        child_block_index = child.get("block_index")
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
        point_id_to_blockIndex = point_id_to_blockIndex_mappings[virtual_record_id]
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
                        block_text = data.get("table_markdown","")
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
                if extension == "xlsx":
                    # Guard against non-dict data
                    if isinstance(data, dict):
                        block_num = [data.get("row_number", 1)]
                    else:
                        block_num = [1]
                else:
                    block_num = [block.get("index", 0) + 1]

            enhanced_metadata = {
                        "orgId": meta.get("orgId", "") if meta.get("orgId") else record.get("org_id", ""),
                        "recordId": record.get("id", ""),
                        "virtualRecordId": virtual_record_id,
                        "recordName": record.get("record_name",""),
                        "recordType": record.get("record_type",""),
                        "recordVersion": record.get("version",""),
                        "origin": record.get("origin",""),
                        "connector": record.get("connector_name",""),
                        "blockText": block_text,
                        "blockType": str(block_type),
                        "bounding_box": extract_bounding_boxes(block.get("citation_metadata")),
                        "pageNum":[page_num],
                        "extension": extension,
                        "mimeType": mime_type,
                        "blockNum":block_num
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

async def get_record(meta: Dict[str, Any],virtual_record_id: str,virtual_record_id_to_result: Dict[str, Dict[str, Any]],blob_store: BlobStorage,org_id: str) -> None:
    try:
        record = await blob_store.get_record_from_storage(virtual_record_id=virtual_record_id, org_id=org_id)
        if record:
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

        record = {
            "id": metadata.get("recordId", ""),
            "org_id": org_id,
            "record_name": metadata.get("recordName", ""),
            "record_type": metadata.get("recordType", ""),
            "external_record_id": metadata.get("externalRecordId", virtual_record_id),
            "external_revision_id": metadata.get("externalRevisionId", virtual_record_id),
            "version": metadata.get("version",""),
            "origin": metadata.get("origin",""),
            "connector_name": metadata.get("connectorName",""),
            "virtual_record_id": virtual_record_id,
            "mime_type": metadata.get("mimeType",""),
            "created_at": metadata.get("createdAtTimestamp", ""),
            "updated_at": metadata.get("updatedAtTimestamp", ""),
            "source_created_at": metadata.get("sourceCreatedAtTimestamp", ""),
            "source_updated_at": metadata.get("sourceLastModifiedTimestamp", ""),
            "weburl": metadata.get("webUrl", ""),
            "semantic_metadata": semantic_metadata,
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
        if page_num:
            page_num = page_num[0]
        else:
            page_num = None
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

MAX_WORDS_IN_TABLE_THRESHOLD = 700

def checkForLargeTable(markdown: str) -> bool:
    cleaned = re.sub(r'(\|)|(-{3,})|(:?-+:?)', ' ', markdown)
    cleaned = re.sub(r'\s+', ' ', cleaned).strip()
    words = cleaned.split(' ')
    words = [word for word in words if word]
    return len(words) > MAX_WORDS_IN_TABLE_THRESHOLD


def record_to_message_content(record: Dict[str, Any], final_results: List[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    """
    Convert a record JSON object to message content format matching get_message_content.

    Args:
        record: The record JSON object containing block_containers and other metadata
        user_data: Optional user data for context
        query: Optional query for context

    Returns:
        List of message content dictionaries in the same format as get_message_content
    """

    try:
        content = []
        record_id = record.get("id", "")
        record_name = record.get("record_name", "")
        content.append({
            "type": "text",
            "text": f"""<record>
            * Record Id: {record_id}
            * Record Name: {record_name}
            * Record content:
            """
        })
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
            elif block_type == BlockType.TEXT.value:
                content.append({
                    "type": "text",
                    "text": f"* Block Number: {block_number}\n* Block Type: {block_type}\n* Block Content: {data}\n\n"
                })
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
                        rows_to_be_included_list = [ child.get("block_index") for child in corresponding_block_group.get("children", [])]
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
                            content.append({
                                "type": "text",
                                "text": f"{rendered_form}\n\n"
                            })
                        else:
                            content.append({
                                "type": "text",
                                "text": f"* Block Group Number: R{record_number}-{block_group_index}\n* Block Type: table summary\n* Block Content: {table_summary}\n\n"
                            })
            else:
                content.append({
                    "type": "text",
                    "text": f"* Block Number: {block_number}\n* Block Type: {block_type}\n* Block Content: {data}\n\n"
                })

        # Add closing tags
        content.append({
            "type": "text",
            "text": "</record>"
        })

        return content
    except Exception as e:
        raise Exception(f"Error in record_to_message_content: {e}") from e

def get_message_content(flattened_results: List[Dict[str, Any]], virtual_record_id_to_result: Dict[str, Any], user_data: str, query: str, logger) -> str:
    content = []

    template = Template(qna_prompt_instructions_1)
    rendered_form = template.render(
                user_data=user_data,
                query=query,
                rephrased_queries=[],
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
            semantic_metadata = record.get("semantic_metadata")

            template = Template(qna_prompt_context)
            rendered_form = template.render(
                record_id=record.get("id","Not available"),
                record_name=record.get("record_name","Not available"),
                semantic_metadata=semantic_metadata,
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
            else:
                content.append({
                    "type": "text",
                    "text": f"* Block Number: {block_number}\n* Block Type: {block_type}\n* Block Content: {result.get('content')}\n\n"
                })
        else:
            continue

    content.append({
        "type": "text",
        "text": f"</record>\n</context>\n\n{qna_prompt_instructions_2}"
    })

    logger.debug(f"content: {content}")
    return content

def get_message_content_for_tool(flattened_results: List[Dict[str, Any]], virtual_record_id_to_result: Dict[str, Any], final_results: List[Dict[str,    Any]]) -> str:
    virtual_record_id_to_record_number = {}
    seen_virtual_record_ids = set()
    record_number = 1

    for result in final_results:
        virtual_record_id = result.get("virtual_record_id")
        if virtual_record_id not in seen_virtual_record_ids:
            seen_virtual_record_ids.add(virtual_record_id)
            virtual_record_id_to_record_number[virtual_record_id] = record_number
            record_number = record_number + 1
    all_contents = []
    content = []
    seen_blocks = set()
    seen_virtual_record_ids.clear()
    record_ids =[]

    for i,result in enumerate(flattened_results):
        virtual_record_id = result.get("virtual_record_id")
        if virtual_record_id not in seen_virtual_record_ids:
            if i > 0:
                content.append({
                    "type": "text",
                    "text": "</record>"
                })
                all_contents.append(content)
                content = []
            seen_virtual_record_ids.add(virtual_record_id)
            record = virtual_record_id_to_result[virtual_record_id]
            if record is None:
                continue

            template = Template(qna_prompt_context_for_tool)
            rendered_form = template.render(
                record_id=record.get("id","Not available"),
                record_name=record.get("record_name","Not available"),
            )
            content.append({
                "type": "text",
                "text": rendered_form
            })
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
            # if block_type == BlockType.IMAGE.value:
            #     if result.get("content").startswith("data:image/"):
            #         content.append({
            #             "type": "text",
            #             "text": f"* Block Number: {block_number}\n* Block Type: {block_type}\n* Block Content:"
            #         })
            #         content.append({
            #             "type": "image_url",
            #             "image_url": {"url": result.get("content")}
            #         })
            #     else:
            #         content.append({
            #             "type": "text",
            #             "text": f"* Block Number: {block_number}\n* Block Type: image description\n* Block Content: {result.get('content')}\n\n"
            #         })
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
            elif block_type != BlockType.IMAGE.value:
                content.append({
                    "type": "text",
                    "text": f"* Block Number: {block_number}\n* Block Type: {block_type}\n* Block Content: {result.get('content')}\n\n"
                })
        else:
            continue

    content.append({
        "type": "text",
        "text": "</record>"
    })
    all_contents.append(content)

    return all_contents,record_ids

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


def count_tokens_in_records(records: List[Dict[str, Any]]) -> int:
    """
    Count the total number of tokens in a list of records, excluding image type blocks.

    Args:
        records: List of record dictionaries containing block_containers with blocks

    Returns:
        Total number of tokens across all non-image blocks in all records
    """
    # Lazy import tiktoken; fall back to a rough heuristic if unavailable
    enc = None
    try:
        import tiktoken  # type: ignore
        try:
            enc = tiktoken.get_encoding("cl100k_base")
        except Exception:
            enc = None
    except Exception:
        enc = None

    def count_tokens(text: str) -> int:
        """Count tokens in text using tiktoken or fallback heuristic"""
        if not text:
            return 0
        if enc is not None:
            try:
                return len(enc.encode(text))
            except Exception:
                pass
        # Fallback heuristic: ~4 chars per token
        return max(1, len(text) // 4)

    total_tokens = 0

    for record in records:
        if not record:
            continue

        # Get block containers
        block_containers = record.get("block_containers", {})
        blocks = block_containers.get("blocks", [])
        block_containers.get("block_groups", [])

        # Process individual blocks
        for block in blocks:
            block_type = block.get("type")

            # Skip image type blocks
            if block_type == BlockType.IMAGE.value:
                continue

            # Extract text content based on block type
            data = block.get("data", "")
            text_content = ""

            if block_type == BlockType.TEXT.value:
                text_content = str(data) if data else ""
            elif block_type == BlockType.TABLE_ROW.value:
                # For table rows, get the natural language text
                if isinstance(data, dict):
                    text_content = data.get("row_natural_language_text", "")
                else:
                    text_content = str(data)
            else:
                # For any other non-image block type, include the data as text
                text_content = str(data) if data else ""

            # Count tokens for this block's text content
            if text_content:
                total_tokens += count_tokens(text_content)

    return total_tokens
