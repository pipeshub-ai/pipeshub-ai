import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from app.models.blocks import BlockType, GroupType
from app.utils.chat_helpers import get_enhanced_metadata

# Initialize logger
logger = logging.getLogger(__name__)


@dataclass
class ChatDocCitation:
    content: str
    metadata: Dict[str, Any]
    chunkindex: int



def fix_json_string(json_str) -> str:
    """Fix control characters in JSON string values without parsing."""
    result = ""
    in_string = False
    escaped = False
    ascii_start = 32
    ascii_end = 127
    extended_ascii_end = 159
    for c in json_str:
        if escaped:
            # Previous character was a backslash, this character is escaped
            result += c
            escaped = False
            continue

        if c == "\\":
            # This is a backslash, next character will be escaped
            result += c
            escaped = True
            continue

        if c == '"':
            # This is a quote, toggle whether we're in a string
            in_string = not in_string
            result += c
            continue

        if in_string:
            # We're inside a string, escape control characters properly
            if c == "\n":
                result += "\\n"
            elif c == "\r":
                result += "\\r"
            elif c == "\t":
                result += "\\t"
            elif ord(c) < ascii_start or (ord(c) >= ascii_end and ord(c) <= extended_ascii_end):
                # Other control characters as unicode escapes
                result += f"\\u{ord(c):04x}"
            else:
                result += c
        else:
            # Not in a string, keep as is
            result += c

    return result



def normalize_citations_and_chunks(answer_text: str, final_results: List[Dict[str, Any]],records: List[Dict[str, Any]]=None) -> Tuple[str, List[Dict[str, Any]]]:
    """
    Normalize citation numbers in answer text to be sequential (1,2,3...)
    and create corresponding citation chunks with correct mapping.
    Handles both single citations [R1-2] and multiple citations [R1-2, R1-3, R2-1].
    """
    if records is None:
        records = []
    # Extract all citation numbers from the answer text
    # Match both regular square brackets [R1-2] and Chinese brackets 【R1-2】
    # Also match multiple citations within a single pair of brackets [R1-2, R1-3]
    citation_pattern = r'\[\s*((?:R\d+-\d+(?:\s*,\s*)?)+)\s*\]|【\s*((?:R\d+-\d+(?:\s*,\s*)?)+)\s*】'
    matches = re.finditer(citation_pattern, answer_text)

    unique_citations = []
    seen = set()

    for match in matches:
        # Check which group matched (group 1 for [...], group 2 for 【...】)
        citations_str = match.group(1) or match.group(2)

        # Split by comma to handle multiple citations in single brackets
        citation_keys = [c.strip() for c in citations_str.split(',') if c.strip()]

        for citation_key in citation_keys:
            if citation_key not in seen:
                unique_citations.append(citation_key)
                seen.add(citation_key)

    if not unique_citations:
        return answer_text, []

    citation_mapping = {}
    new_citations = []
    record_number = 0
    block_number_to_index = {}
    flattened_final_results = []
    seen = set()
    vrids = [record.get("virtual_record_id") for record in records]
    record_number_to_vrid = {}
    for i,doc in enumerate(final_results):
        virtual_record_id = doc.get("virtual_record_id")

        if virtual_record_id not in seen:
            record_number += 1
            record_number_to_vrid[record_number] = virtual_record_id
            seen.add(virtual_record_id)


        if virtual_record_id not in vrids:
            block_index = doc.get("block_index")
            block_type = doc.get("block_type")
            if block_type == GroupType.TABLE.value:
                _,child_results = doc.get("content")
                if child_results:
                    for child in child_results:
                        child_block_index = child.get("block_index")
                        flattened_final_results.append(child)
                        block_number_to_index[f"R{record_number}-{child_block_index}"] = len(flattened_final_results) - 1
                else:
                    flattened_final_results.append(doc)
                    block_number_to_index[f"R{record_number}-{block_index}"] = len(flattened_final_results) - 1
            else:
                flattened_final_results.append(doc)
                block_number_to_index[f"R{record_number}-{block_index}"] = len(flattened_final_results) - 1

    new_citation_num = 1
    for i, old_citation_key in enumerate(unique_citations):

        # Get the corresponding chunk from final_results
        if old_citation_key in block_number_to_index:
            chunk_index = block_number_to_index[old_citation_key]

            if 0 <= chunk_index < len(flattened_final_results):
                doc = flattened_final_results[chunk_index]
                content = doc.get("content", "")
                new_citations.append({
                    "content": "Image" if content.startswith("data:image/") else content,
                    "chunkIndex": new_citation_num,  # Use new sequential number
                    "metadata": doc.get("metadata", {}),
                    "citationType": "vectordb|document",
                })
                citation_mapping[old_citation_key] = new_citation_num
                new_citation_num += 1
        else:
            # Safely parse citation key like "R<record>-<block>"
            key_match = re.match(r"R(\d+)-(\d+)", old_citation_key)
            if not key_match:
                continue
            try:
                number = int(key_match.group(1))
                block_index = int(key_match.group(2))
            except (TypeError, ValueError):
                continue

            # Ensure record number maps to a known VRID
            if number not in record_number_to_vrid:
                continue
            vrid = record_number_to_vrid[number]

            # Find the record by VRID
            record = next((r for r in records if r.get("virtual_record_id") == vrid), None)
            if record is None:
                continue

            # Extract blocks safely
            block_container = record.get("block_containers", {}) or {}
            blocks = block_container.get("blocks", []) or []
            if not isinstance(blocks, list):
                continue
            if block_index < 0 or block_index >= len(blocks):
                continue

            block = blocks[block_index]
            if not isinstance(block, dict):
                continue
            block_type = block.get("type")
            data = block.get("data")
            if block_type == BlockType.TABLE_ROW.value:
                data = data.get("row_natural_language_text","")
            elif block_type == BlockType.IMAGE.value:
                data = data.get("uri","")
            enhanced_metadata = get_enhanced_metadata(record,block,{})
            new_citations.append({
                "content": "Image" if data.startswith("data:image/") else data,
                "chunkIndex": new_citation_num,  # Use new sequential number
                "metadata": enhanced_metadata,
                "citationType": "vectordb|document",
            })
            citation_mapping[old_citation_key] = new_citation_num
            new_citation_num += 1

    # Replace citation numbers in answer text - always use regular brackets for output
    def replace_citation(match) -> str:
        # Check which group matched to get the citation keys
        citations_str = match.group(1) or match.group(2)

        # Split by comma to handle multiple citations (filter out empty strings)
        citation_keys = [c.strip() for c in citations_str.split(',') if c.strip()]

        new_nums = [str(citation_mapping[old_key]) for old_key in citation_keys if old_key in citation_mapping]

        if new_nums:
            # Always output regular brackets for consistency
            return ''.join(f"[{num}]" for num in new_nums)
        return ""

    normalized_answer = re.sub(citation_pattern, replace_citation, answer_text)
    return normalized_answer, new_citations


def process_citations(
    llm_response,
    documents: List[Dict[str, Any]],
    records: List[Dict[str, Any]] = None,
    from_agent: bool = False,
    virtual_record_id_to_result: Optional[Dict[str, Dict[str, Any]]] = None
) -> Dict[str, Any]:
    """
    Process the LLM response and extract citations from relevant documents with normalization.

    Args:
        llm_response: The LLM response (string, dict, or AIMessage)
        documents: List of retrieved documents/chunks
        records: Full record data from tool calls (optional)
        from_agent: Whether this is from agent (vs chatbot) workflow
        virtual_record_id_to_result: Mapping of virtual record IDs to full records (for agents)
    """
    if records is None:
        records = []
    if virtual_record_id_to_result is None:
        virtual_record_id_to_result = {}
    try:
        # Handle the case where llm_response might be an object with a content field
        if hasattr(llm_response, "content"):
            response_content = llm_response.content
        elif isinstance(llm_response, dict) and "content" in llm_response:
            response_content = llm_response["content"]
        else:
            response_content = llm_response

        # Parse the LLM response if it's a string
        if isinstance(response_content, str):
            try:
                # Clean the JSON string before parsing
                cleaned_content = response_content.strip()
                # Handle nested JSON (sometimes response is JSON within JSON)
                if cleaned_content.startswith('"') and cleaned_content.endswith('"'):
                    cleaned_content = cleaned_content[1:-1].replace('\\"', '"')

                # Handle escaped newlines and other special characters
                cleaned_content = cleaned_content.replace("\\n", "\n").replace("\\t", "\t")

                # Apply our fix for control characters in JSON string values
                cleaned_content = fix_json_string(cleaned_content)

                # Try to parse the cleaned content
                response_data = json.loads(cleaned_content)
            except json.JSONDecodeError as e:
                # If regular parsing fails, try a more lenient approach
                try:
                    start_idx = cleaned_content.find("{")
                    end_idx = cleaned_content.rfind("}")

                    if start_idx >= 0 and end_idx > start_idx:
                        potential_json = cleaned_content[start_idx : end_idx + 1]
                        potential_json = fix_json_string(potential_json)
                        response_data = json.loads(potential_json)
                    else:
                        return {
                            "error": f"Failed to parse LLM response: {str(e)}",
                            "raw_response": response_content,
                        }
                except Exception as nested_e:
                    return {
                        "error": f"Failed to parse LLM response: {str(e)}, Nested error: {str(nested_e)}",
                        "raw_response": response_content,
                    }
        else:
            response_data = response_content

        # Create a result object (either use existing or create new)
        if isinstance(response_data, dict):
            result = response_data.copy()
        else:
            result = {"answer": str(response_data)}

        # Normalize citations in the answer if it exists
        if "answer" in result:
            if from_agent:
                # CRITICAL: Pass virtual_record_id_to_result and records for proper metadata
                normalized_answer, citations = normalize_citations_and_chunks_for_agent(
                    result["answer"],
                    documents,
                    virtual_record_id_to_result=virtual_record_id_to_result,
                    records=records
                )
            else:
                normalized_answer, citations = normalize_citations_and_chunks(result["answer"], documents,records)
            result["answer"] = normalized_answer
            result["citations"] = citations
        else:
            # Fallback for cases where answer is not in a structured format
            if from_agent:
                # CRITICAL: Pass virtual_record_id_to_result and records for proper metadata
                normalized_answer, citations = normalize_citations_and_chunks_for_agent(
                    str(response_data),
                    documents,
                    virtual_record_id_to_result=virtual_record_id_to_result,
                    records=records
                )
            else:
                normalized_answer, citations = normalize_citations_and_chunks(str(response_data), documents, records)
            result = {
                "answer": normalized_answer,
                "citations": citations
            }

        return result

    except Exception as e:
        import traceback
        return {
            "error": f"Citation processing failed: {str(e)}",
            "traceback": traceback.format_exc(),
            "raw_response": llm_response,
        }


def normalize_citations_and_chunks_for_agent(
    answer_text: str,
    final_results: List[Dict[str, Any]],
    virtual_record_id_to_result: Optional[Dict[str, Dict[str, Any]]] = None,
    records: Optional[List[Dict[str, Any]]] = None
) -> tuple[str, List[Dict[str, Any]]]:
    """
    Normalize citation numbers in answer text to be sequential (1,2,3...)
    and create corresponding citation chunks with correct mapping.

    This function now matches the working logic from normalize_citations_and_chunks
    used in chatbot.py, with support for:
    - Multiple citations in one bracket: [R1-2, R1-3, R2-1]
    - Single citations: [R1-2]
    - Chinese brackets: 【R1-2】
    - Record lookup from tools

    Args:
        answer_text: The answer text with citations
        final_results: List of result documents with metadata
        virtual_record_id_to_result: Optional mapping of virtual_record_id to full record data
        records: Optional list of full record data from tool calls
    """
    if records is None:
        records = []

    # Match both regular square brackets [R1-2] and Chinese brackets 【R1-2】
    # Also match multiple citations within a single pair of brackets [R1-2, R1-3]
    citation_pattern = r'\[\s*((?:R\d+-\d+(?:\s*,\s*)?)+)\s*\]|【\s*((?:R\d+-\d+(?:\s*,\s*)?)+)\s*】'
    matches = re.finditer(citation_pattern, answer_text)

    unique_citations = []
    seen = set()

    for match in matches:
        # Check which group matched (group 1 for [...], group 2 for 【...】)
        citations_str = match.group(1) or match.group(2)

        # Split by comma to handle multiple citations in single brackets
        citation_keys = [c.strip() for c in citations_str.split(',') if c.strip()]

        for citation_key in citation_keys:
            if citation_key not in seen:
                unique_citations.append(citation_key)
                seen.add(citation_key)

    if not unique_citations:
        # No citation markers found, but if final_results exist, create citations from all results
        if final_results:
            all_citations = []
            for idx, doc in enumerate(final_results):
                content = doc.get("content", "")
                # Handle table blocks
                if isinstance(content, tuple):
                    content = content[0] if content else ""

                # Get metadata and ensure all required fields are present
                metadata = doc.get("metadata", {}) or {}

                # If metadata is missing required fields, try to get them from virtual_record_id_to_result
                if virtual_record_id_to_result:
                    virtual_record_id = doc.get("virtual_record_id") or metadata.get("virtualRecordId")
                    if virtual_record_id and virtual_record_id in virtual_record_id_to_result:
                        record = virtual_record_id_to_result[virtual_record_id]
                        # Fill in missing required fields from record
                        if not metadata.get("origin"):
                            metadata["origin"] = record.get("origin", "")
                        if not metadata.get("recordName"):
                            metadata["recordName"] = record.get("record_name", "")
                        if not metadata.get("recordId"):
                            metadata["recordId"] = record.get("id", "")
                        if not metadata.get("mimeType"):
                            metadata["mimeType"] = record.get("mime_type", "")

                # Ensure required fields have at least empty string defaults (validation requirement)
                metadata["origin"] = metadata.get("origin") or ""
                metadata["recordName"] = metadata.get("recordName") or ""
                metadata["recordId"] = metadata.get("recordId") or ""
                metadata["mimeType"] = metadata.get("mimeType") or ""
                metadata["orgId"] = metadata.get("orgId") or ""  # Add orgId

                # Ensure content is not None
                citation_content = content or ""
                if isinstance(citation_content, str) and citation_content.startswith("data:image/"):
                    citation_content = "Image"

                all_citations.append({
                    "content": citation_content,
                    "chunkIndex": idx + 1,
                    "metadata": metadata,
                    "citationType": "vectordb|document",
                })
            return answer_text, all_citations
        return answer_text, []

    # Main citation processing logic (matches normalize_citations_and_chunks)
    citation_mapping = {}
    new_citations = []
    record_number = 0
    block_number_to_index = {}
    flattened_final_results = []
    seen_vrids = set()
    vrids = [record.get("virtual_record_id") for record in records]
    record_number_to_vrid = {}

    # First pass: flatten final_results and build mappings
    for i, doc in enumerate(final_results):
        virtual_record_id = doc.get("virtual_record_id")

        if virtual_record_id not in seen_vrids:
            record_number += 1
            record_number_to_vrid[record_number] = virtual_record_id
            seen_vrids.add(virtual_record_id)

        if virtual_record_id not in vrids:
            block_index = doc.get("block_index")
            block_type = doc.get("block_type")
            if block_type == GroupType.TABLE.value:
                _, child_results = doc.get("content")
                if child_results:
                    for child in child_results:
                        child_block_index = child.get("block_index")
                        flattened_final_results.append(child)
                        block_number_to_index[f"R{record_number}-{child_block_index}"] = len(flattened_final_results) - 1
                else:
                    flattened_final_results.append(doc)
                    block_number_to_index[f"R{record_number}-{block_index}"] = len(flattened_final_results) - 1
            else:
                flattened_final_results.append(doc)
                block_number_to_index[f"R{record_number}-{block_index}"] = len(flattened_final_results) - 1


    new_citation_num = 1
    for i, old_citation_key in enumerate(unique_citations):
        # Get the corresponding chunk from final_results
        logger.debug(f"🔍 Processing citation {i+1}/{len(unique_citations)}: {old_citation_key}")
        if old_citation_key in block_number_to_index:
            chunk_index = block_number_to_index[old_citation_key]

            if 0 <= chunk_index < len(flattened_final_results):
                doc = flattened_final_results[chunk_index]
                content = doc.get("content", "")

                # Get metadata and ensure all required fields
                metadata = doc.get("metadata", {}) or {}

                # Try to enhance metadata from virtual_record_id_to_result
                if virtual_record_id_to_result:
                    virtual_record_id = doc.get("virtual_record_id") or metadata.get("virtualRecordId")
                    if virtual_record_id and virtual_record_id in virtual_record_id_to_result:
                        record = virtual_record_id_to_result[virtual_record_id]
                        if not metadata.get("origin"):
                            metadata["origin"] = record.get("origin", "")
                        if not metadata.get("recordName"):
                            metadata["recordName"] = record.get("record_name", "")
                        if not metadata.get("recordId"):
                            metadata["recordId"] = record.get("id", "")
                        if not metadata.get("mimeType"):
                            metadata["mimeType"] = record.get("mime_type", "")

                # Ensure required fields
                metadata["origin"] = metadata.get("origin") or ""
                metadata["recordName"] = metadata.get("recordName") or ""
                metadata["recordId"] = metadata.get("recordId") or ""
                metadata["mimeType"] = metadata.get("mimeType") or ""
                metadata["orgId"] = metadata.get("orgId") or ""

                new_citations.append({
                    "content": "Image" if content.startswith("data:image/") else content,
                    "chunkIndex": new_citation_num,
                    "metadata": metadata,
                    "citationType": "vectordb|document",
                })
                citation_mapping[old_citation_key] = new_citation_num
                new_citation_num += 1
        else:
            # Lookup in records (from tool calls) - matches normalize_citations_and_chunks logic
            key_match = re.match(r"R(\d+)-(\d+)", old_citation_key)
            if not key_match:
                continue
            try:
                number = int(key_match.group(1))
                block_index = int(key_match.group(2))
            except (TypeError, ValueError):
                continue

            # Ensure record number maps to a known VRID
            if number not in record_number_to_vrid:
                continue
            vrid = record_number_to_vrid[number]

            # Find the record by VRID
            record = next((r for r in records if r.get("virtual_record_id") == vrid), None)
            if record is None:
                continue

            # Extract blocks safely
            block_container = record.get("block_containers", {}) or {}
            blocks = block_container.get("blocks", []) or []
            if not isinstance(blocks, list):
                continue
            if block_index < 0 or block_index >= len(blocks):
                continue

            block = blocks[block_index]
            if not isinstance(block, dict):
                continue
            block_type = block.get("type")
            data = block.get("data")
            if block_type == BlockType.TABLE_ROW.value:
                data = data.get("row_natural_language_text", "")
            elif block_type == BlockType.IMAGE.value:
                data = data.get("uri", "")

            enhanced_metadata = get_enhanced_metadata(record, block, {})

            # Ensure required fields
            enhanced_metadata["origin"] = enhanced_metadata.get("origin") or ""
            enhanced_metadata["recordName"] = enhanced_metadata.get("recordName") or ""
            enhanced_metadata["recordId"] = enhanced_metadata.get("recordId") or ""
            enhanced_metadata["mimeType"] = enhanced_metadata.get("mimeType") or ""
            enhanced_metadata["orgId"] = enhanced_metadata.get("orgId") or ""

            new_citations.append({
                "content": "Image" if data.startswith("data:image/") else data,
                "chunkIndex": new_citation_num,
                "metadata": enhanced_metadata,
                "citationType": "vectordb|document",
            })
            citation_mapping[old_citation_key] = new_citation_num
            new_citation_num += 1

    # Replace citation numbers in answer text - always use regular brackets for output
    def replace_citation(match) -> str:
        # Check which group matched to get the citation keys
        citations_str = match.group(1) or match.group(2)

        # Split by comma to handle multiple citations (filter out empty strings)
        citation_keys = [c.strip() for c in citations_str.split(',') if c.strip()]

        new_nums = [str(citation_mapping[old_key]) for old_key in citation_keys if old_key in citation_mapping]

        if new_nums:
            # Always output regular brackets for consistency
            return ''.join(f"[{num}]" for num in new_nums)
        return ""

    normalized_answer = re.sub(citation_pattern, replace_citation, answer_text)

    if not new_citations and unique_citations:
        logger.error(f"❌ FAILED to create citations for markers: {unique_citations}")

    return normalized_answer, new_citations
