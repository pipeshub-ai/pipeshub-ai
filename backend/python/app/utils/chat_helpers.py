import re
from typing import Any, Dict, List, Tuple
from uuid import uuid4

from jinja2 import Template

from app.containers.utils.utils import ContainerUtils
from app.models.blocks import BlockType, GroupType
from app.modules.qna.prompt_templates import (
    qna_prompt_context,
    qna_prompt_instructions_1,
    qna_prompt_instructions_2,
)
from app.modules.transformers.blob_storage import BlobStorage
from app.services.vector_db.const.const import VECTOR_DB_COLLECTION_NAME
from app.utils.mimetype_to_extension import get_extension_from_mimetype


async def get_flattened_results(result_set: List[Dict[str, Any]], blob_store: BlobStorage, org_id: str, is_multimodal_llm: bool, virtual_record_id_to_result: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:

    flattened_results = []
    seen_chunks = set()
    chunk_index = 1
    adjacent_chunks = {}
    new_type_results = []
    old_type_results = []
    for result in result_set:
        meta = result.get("metadata")
        is_block_group = meta.get("isBlockGroup")
        if is_block_group is not None:
            new_type_results.append(result)
        else:
            old_type_results.append(result)

    sorted_new_type_results = sorted(new_type_results, key=lambda x: not x.get("metadata", {}).get("isBlockGroup", False))

    for result in sorted_new_type_results:
        virtual_record_id = result["metadata"].get("virtualRecordId")
        meta = result.get("metadata")

        if virtual_record_id not in virtual_record_id_to_result:
            adjacent_chunks[virtual_record_id] = []
            await get_blocks(meta,virtual_record_id,virtual_record_id_to_result,blob_store,org_id)

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
            block_group = block_groups[block_group_index]
            table_data = block_group.get("data",{})
            table_markdown = table_data.get("table_markdown","")
            row_text = block.get("data",{}).get("row_natural_language_text","")
            table_summary = table_data.get("table_summary","")
            result["content"] = f"table_summary: {table_summary}\n\nrow_text: {row_text}"

            children = block_group.get("children")
            first_block_index = children[0].get("block_index") if children and len(children) > 0 else None
            if first_block_index is not None:
                adjacent_chunks[virtual_record_id].append(first_block_index-1)
                last_block_index = children[-1].get("block_index") if children and len(children) > 0 else None
                if last_block_index is not None:
                    adjacent_chunks[virtual_record_id].append(last_block_index+1)
        elif block_type == GroupType.TABLE.value:
            table_data = block.get("data",{})
            table_markdown = table_data.get("table_markdown","")
            children = block.get("children")
            first_block_index = children[0].get("block_index") if children and len(children) > 0 else None
            result["block_index"] = first_block_index
            if first_block_index is not None:
                adjacent_chunks[virtual_record_id].append(first_block_index-1)
                last_block_index = children[-1].get("block_index") if children and len(children) > 0 else None
                if last_block_index is not None:
                    adjacent_chunks[virtual_record_id].append(last_block_index+1)
            is_large_table = checkForLargeTable(table_markdown)
            if is_large_table:
                table_summary = table_data.get("table_summary","")
                result["content"] = f"table_summary: {table_summary}"
            else:
                result["content"] = table_markdown
                for child in children:
                    child_block_index = child.get("block_index")
                    seen_chunks.add(f"{virtual_record_id}-{child_block_index}")

        result["chunk_index"] = chunk_index
        chunk_index += 1
        result["virtual_record_id"] = virtual_record_id
        if "block_index" not in result:
            result["block_index"] = index
        enhanced_metadata = get_enhanced_metadata(record,block,meta)
        result["metadata"] = enhanced_metadata

        flattened_results.append(result)

    for virtual_record_id,adjacent_chunks_list in adjacent_chunks.items():
        for index in adjacent_chunks_list:
            chunk_id = f"{virtual_record_id}-{index}"
            if chunk_id in seen_chunks:
                continue
            seen_chunks.add(chunk_id)
            record = virtual_record_id_to_result[virtual_record_id]
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
                        "chunk_index": chunk_index,
                        "virtual_record_id": virtual_record_id,
                        "block_index": index,
                        "citationType": "vectordb|document",
                    })
                    chunk_index += 1

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
        block_container = record.get("block_containers",{})
        blocks = block_container.get("blocks",[])
        block_groups = block_container.get("block_groups",[])

        block = blocks[index]
        block_type = block.get("type")
        result["block_type"] = block_type
        result["chunk_index"] = chunk_index
        chunk_index += 1
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

            extension = meta.get("extension")
            if extension is None:
                extension = get_extension_from_mimetype(record.get("mime_type"))

            block_num = meta.get("blockNum")
            if block_num is None:
                block_num = [block.get("index",0)+1]

            enhanced_metadata = {
                        "orgId": record.get("orgId", ""),
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
                        "mimeType": record.get("mime_type",""),
                        "blockNum":block_num
                    }
            if meta.get("sheetName"):
                enhanced_metadata["sheetName"] = meta.get("sheetName")
            if meta.get("sheetNum"):
                enhanced_metadata["sheetNum"] = meta.get("sheetNum")
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

async def get_blocks(meta: Dict[str, Any],virtual_record_id: str,virtual_record_id_to_result: Dict[str, Dict[str, Any]],blob_store: BlobStorage,org_id: str) -> None:
    try:
        record = await blob_store.get_record_from_storage(virtual_record_id=virtual_record_id, org_id=org_id)
        if record:
            virtual_record_id_to_result[virtual_record_id] = record
        else:
            raise Exception("Record not found in blob storage")

    except Exception as e:
        raise e

async def create_record_from_vector_metadata(metadata: Dict[str, Any], org_id: str, virtual_record_id: str,blob_store: BlobStorage) -> Tuple[Dict[str, Any], Dict[str, int]]:
    try:
        summary = metadata.get("summary", "")
        categories = [metadata.get("category", "")]
        topics = metadata.get("topics", "")
        sub_category_level_1 = metadata.get("subcategories", {}).get("level1", "")
        sub_category_level_2 = metadata.get("subcategories", {}).get("level2", "")
        sub_category_level_3 = metadata.get("subcategories", {}).get("level3", "")
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

        record["block_containers"] = {
            "blocks": blocks,
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


def get_message_content(flattened_results: List[Dict[str, Any]], virtual_record_id_to_result: Dict[str, Any], user_data: str, query: str) -> str:
    content = []
    task_instructions = qna_prompt_instructions_1
    content.append({
                "type": "text",
                "text": task_instructions
            })

    sorted_flattened_results = sorted(flattened_results, key=lambda x: (x['virtual_record_id'], x['block_index']))
    seen_virtual_record_ids = set()
    seen_blocks = set()
    for result in sorted_flattened_results:
        virtual_record_id = result.get("virtual_record_id")
        if virtual_record_id not in seen_virtual_record_ids:
            seen_virtual_record_ids.add(virtual_record_id)
            record = virtual_record_id_to_result[virtual_record_id]  # Unpack the tuple
            semantic_metadata = record.get("semantic_metadata")
            template = Template(qna_prompt_context)
            rendered_form = template.render(
                user_data=user_data,
                query=query,
                rephrased_queries=[],
                semantic_metadata=semantic_metadata,
                )
            content.append({
                "type": "text",
                "text": rendered_form
            })

        result_id = f"{virtual_record_id}_{result.get('block_index')}"
        if result_id not in seen_blocks:
            seen_blocks.add(result_id)
            chunk_index = result.get("chunk_index")
            block_type = result.get("block_type")
            if block_type == BlockType.IMAGE.value:
                if result.get("content").startswith("data:image/"):
                    content.append({
                        "type": "text",
                        "text": f"* Chunk Index: {chunk_index}\n* Chunk Type: {block_type}\n* Chunk Content:"
                    })
                    content.append({
                        "type": "image_url",
                        "image_url": {"url": result.get("content")}
                    })
                else:
                    content.append({
                        "type": "text",
                        "text": f"* Chunk Index: {chunk_index}\n* Chunk Type: image_description\n* Chunk Content: {result.get('content')}"
                    })
            elif block_type == BlockType.TABLE_ROW.value or block_type == GroupType.TABLE.value:
                content.append({
                    "type": "text",
                    "text": f"* Chunk Index: {chunk_index}\n* Chunk Type: table\n* Chunk Content: {result.get('content')}"
                })
            elif block_type == BlockType.TEXT.value:
                content.append({
                    "type": "text",
                    "text": f"* Chunk Index: {chunk_index}\n* Chunk Type: {block_type}\n* Chunk Content: {result.get('content')}"
                })
            else:
                content.append({
                    "type": "text",
                    "text": f"* Chunk Index: {chunk_index}\n* Chunk Type: {block_type}\n* Chunk Content: {result.get('content')}"
                })
        else:
            continue

    content.append({
        "type": "text",
        "text": f"</context>\n\n{qna_prompt_instructions_2}"
    })

    return content
