import logging
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote

from app.models.blocks import BlockType, GroupType
from app.utils.chat_helpers import get_enhanced_metadata
from urllib.parse import quote

# Initialize logger
logger = logging.getLogger(__name__)

CITATION_WORD_LIMIT = 4


@dataclass
class ChatDocCitation:
    content: str
    metadata: Dict[str, Any]
    chunkindex: int

def extract_start_end_text(snippet: Optional[str]) -> Tuple[str, str]:
    if not snippet:
        return "", ""
    
    PATTERN = re.compile(r"(?<!\S)[A-Za-z0-9.',;:]+(?:[ ][A-Za-z0-9.',;:]+)+(?!\S)")

    # --- Find start_text: first matching segment ---
    first_match = PATTERN.search(snippet)
    if not first_match:
        return "", ""

    first_text = first_match.group().strip()
    if not first_text:
        return "", ""

    words = first_text.split()
    start_text = " ".join(words[:CITATION_WORD_LIMIT])
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
        end_text = " ".join(words[-CITATION_WORD_LIMIT:])
    elif len(first_text.split()) > CITATION_WORD_LIMIT:
        word_count = len(first_text.split())
        diff = word_count - CITATION_WORD_LIMIT
        diff = min(CITATION_WORD_LIMIT, diff)
        # Fall back to last 4 words of the first segment
        end_text = " ".join(first_text.split()[-diff:])
    else:
        end_text = ""

    while end_text and end_text[-1] == '.':
        end_text = end_text[:-1]

    return start_text, end_text.strip()

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

        while snippet and not snippet[-1].isalnum():
            snippet = snippet[:-1]
        if not snippet:
            return base_url

        start_text, end_text = extract_start_end_text(snippet)

        if not start_text:
            return base_url

        encoded_start = quote(start_text, safe="';:[]")

        if end_text:
            encoded_end = quote(end_text, safe="';:[]")

        if '#' in base_url:
            base_url = base_url.split('#')[0]

        return f"{base_url}#:~:text={encoded_start}{(',' + encoded_end) if encoded_end else ''}"

    except Exception:
        return base_url


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

def normalize_citations_and_chunks(answer_text: str, final_results: List[Dict[str, Any]],records: List[Dict[str, Any]]=None, web_records: List[Dict[str, Any]]=None) -> Tuple[str, List[Dict[str, Any]]]:
    """
    Normalize citation numbers in answer text to be sequential (1,2,3...)
    and create corresponding citation chunks with correct mapping.
    Handles both single citations [R1-2] and multiple citations [R1-2, R1-3, R2-1].
    Also handles web citations [W1-0], [W1-2] for fetched URL content.
    """
    if records is None:
        records = []
    if web_records is None:
        web_records = []

    # Extract all citation numbers from the answer text
    # Match both regular square brackets [R1-2] and Chinese brackets 【R1-2】
    # Also match multiple citations within a single pair of brackets [R1-2, R1-3]
    # Also match web citations [W1-0], [W1-2]
    citation_pattern = r'\[\s*((?:[RW]\d+-\d+(?:\s*,\s*)?)+)\s*\]|【\s*((?:[RW]\d+-\d+(?:\s*,\s*)?)+)\s*】'
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
        elif old_citation_key.startswith("W"):
            # Handle web citations [W1-0], [W1-2]
            logger.debug(f"[WEB_CITATIONS] Processing web citation: {old_citation_key}")
            web_match = re.match(r"W(\d+)-(\d+)", old_citation_key)
            if not web_match:
                logger.debug(f"[WEB_CITATIONS] FILTERED: {old_citation_key} - regex pattern did not match")
                continue
            try:
                url_number = int(web_match.group(1))
                block_index = int(web_match.group(2))
                logger.debug(f"[WEB_CITATIONS] Parsed {old_citation_key} as url_number={url_number}, block_index={block_index}")
            except (TypeError, ValueError) as e:
                logger.debug(f"[WEB_CITATIONS] FILTERED: {old_citation_key} - failed to parse numbers: {e}")
                continue

            # Find the web record by url_number and block_index
            web_record = next(
                (r for r in web_records
                if r.get("url_number") == url_number and r.get("block_index") == block_index),
                None
            )

            if web_record is None:
                continue

            content = web_record.get("content", "")
            url = web_record.get("url", "")


            # Generate text fragment URL for direct navigation
            citation_url = generate_text_fragment_url(url, content)

            new_citations.append({
                "content": content,
                "chunkIndex": new_citation_num,
                "metadata": {
                    "recordId":url,
                    "mimeType":"text/html",
                    "recordName": url,
                    "webUrl": citation_url,
                    "origin": "WEB_SEARCH",
                    "orgId": web_record.get("org_id", ""),
                    "connector": "WEB",
                },
                "citationType": "web|url",
            })
            citation_mapping[old_citation_key] = new_citation_num
            logger.debug(f"[WEB_CITATIONS] SUCCESS: Added {old_citation_key} -> citation #{new_citation_num}")
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




def normalize_citations_and_chunks_for_agent(
    answer_text: str,
    final_results: List[Dict[str, Any]],
    virtual_record_id_to_result: Optional[Dict[str, Dict[str, Any]]] = None,
    records: Optional[List[Dict[str, Any]]] = None,
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
