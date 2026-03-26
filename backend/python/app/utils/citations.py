import logging
import re
from dataclasses import dataclass
from typing import Any, Optional

from app.models.blocks import BlockType, GroupType
from app.utils.chat_helpers import get_enhanced_metadata

# Initialize logger
logger = logging.getLogger(__name__)


@dataclass
class ChatDocCitation:
    content: str
    metadata: dict[str, Any]
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



def _renumber_citation_links(
    text: str,
    md_matches: list,
    url_to_citation_num: dict[str, int],
) -> str:
    """
    Replace citation numbers in markdown links with their new sequential numbers.
    Processes matches in reverse order to preserve string positions.
    """
    for match in reversed(md_matches):
        url = match.group(2).strip()
        new_num = url_to_citation_num.get(url)
        if new_num is not None:
            replacement = f"[{new_num}]({match.group(2)})"
            text = text[:match.start()] + replacement + text[match.end():]
    return text


def _extract_block_index_from_url(url: str) -> Optional[int]:
    """Extract blockIndex value from a block web URL like /record/abc/preview#blockIndex=5"""
    m = re.search(r'blockIndex=(\d+)', url)
    if m:
        return int(m.group(1))
    return None

def _extract_record_id_from_url(url: str) -> Optional[str]:
    """Extract recordId from a block web URL like /record/abc123/preview#blockIndex=5"""
    m = re.search(r'/record/([^/]+)/preview', url)
    if m:
        return m.group(1)
    return None

def normalize_citations_and_chunks(answer_text: str, final_results: list[dict[str, Any]],records: list[dict[str, Any]]=None) -> tuple[str, list[dict[str, Any]]]:
    """
    Normalize citation numbers in answer text to be sequential (1,2,3...)
    and create corresponding citation chunks with correct mapping.

    Supports two citation formats:
    1. Markdown link citations: [N](url) where N is a sequential citation number and url contains /record/{id}/preview#blockIndex={n}
    2. Legacy R-label citations: [R1-2], 【R1-2】
    """

    if records is None:
        records = []

    # First try markdown link citation pattern: [citation_number](url_with_blockIndex)
    md_link_pattern = r'\[([^\]]*?)\]\(([^)]*?/record/[^)]*?preview[^)]*?blockIndex=\d+[^)]*?)\)'
    md_matches = list(re.finditer(md_link_pattern, answer_text))

    if md_matches:
        return _normalize_markdown_link_citations(answer_text, md_matches, final_results, records)

    return answer_text, []


def _normalize_markdown_link_citations(
    answer_text: str,
    md_matches: list,
    final_results: list[dict[str, Any]],
    records: list[dict[str, Any]],
) -> tuple[str, list[dict[str, Any]]]:
    """
    Normalize markdown link citations [text](url) where url contains block preview URLs.
    Maps each citation URL to the corresponding block in final_results and renumbers sequentially.
    """
    url_to_doc_index = {}
    flattened_final_results = []
    vrids = [record.get("virtual_record_id") for record in records]

    for doc in final_results:
        virtual_record_id = doc.get("virtual_record_id")
        if virtual_record_id in vrids:
            continue
        block_type = doc.get("block_type")
        if block_type == GroupType.TABLE.value:
            _, child_results = doc.get("content", ("", []))
            if child_results:
                for child in child_results:
                    flattened_final_results.append(child)
                    child_url = child.get("block_web_url") or child.get("metadata", {}).get("blockWebUrl", "")
                    if child_url:
                        url_to_doc_index[child_url] = len(flattened_final_results) - 1
            else:
                flattened_final_results.append(doc)
                doc_url = doc.get("block_web_url") or doc.get("metadata", {}).get("blockWebUrl", "")
                if doc_url:
                    url_to_doc_index[doc_url] = len(flattened_final_results) - 1
        else:
            flattened_final_results.append(doc)
            doc_url = doc.get("block_web_url") or doc.get("metadata", {}).get("blockWebUrl", "")
            if doc_url:
                url_to_doc_index[doc_url] = len(flattened_final_results) - 1

    unique_urls = []
    seen_urls = set()
    for match in md_matches:
        url = match.group(2).strip()
        if url not in seen_urls:
            unique_urls.append(url)
            seen_urls.add(url)

    url_to_citation_num = {}
    new_citations = []
    new_citation_num = 1

    for url in unique_urls:
        doc = None
        if url in url_to_doc_index:
            idx = url_to_doc_index[url]
            doc = flattened_final_results[idx]
        else:
            # Try matching by record_id + block_index extracted from URL
            record_id = _extract_record_id_from_url(url)
            block_index = _extract_block_index_from_url(url)
            if record_id is not None and block_index is not None:
                for r in records:
                    if r.get("id") == record_id:
                        block_container = r.get("block_containers", {}) or {}
                        blocks = block_container.get("blocks", []) or []
                        if 0 <= block_index < len(blocks):
                            block = blocks[block_index]
                            enhanced_metadata = get_enhanced_metadata(r, block, {})
                            block_type = block.get("type")
                            data = block.get("data")
                            if block_type == BlockType.TABLE_ROW.value:
                                data = data.get("row_natural_language_text", "")
                            elif block_type == BlockType.IMAGE.value:
                                data = data.get("uri", "")
                            new_citations.append({
                                "content": "Image" if isinstance(data, str) and data.startswith("data:image/") else data,
                                "chunkIndex": new_citation_num,
                                "metadata": enhanced_metadata,
                                "citationType": "vectordb|document",
                            })
                            url_to_citation_num[url] = new_citation_num
                            new_citation_num += 1
                        break
                continue

        if doc is not None:
            content = doc.get("content", "")
            metadata = doc.get("metadata", {}) or {}
            new_citations.append({
                "content": "Image" if isinstance(content, str) and content.startswith("data:image/") else content,
                "chunkIndex": new_citation_num,
                "metadata": metadata,
                "citationType": "vectordb|document",
            })
            url_to_citation_num[url] = new_citation_num
            new_citation_num += 1

    answer_text = _renumber_citation_links(answer_text, md_matches, url_to_citation_num)

    return answer_text, new_citations


def normalize_citations_and_chunks_for_agent(
    answer_text: str,
    final_results: list[dict[str, Any]],
    virtual_record_id_to_result: Optional[dict[str, dict[str, Any]]] = None,
    records: Optional[list[dict[str, Any]]] = None
) -> tuple[str, list[dict[str, Any]]]:
    """
    Normalize citation numbers in answer text to be sequential (1,2,3...)
    and create corresponding citation chunks with correct mapping.

    Supports two citation formats:
    1. Markdown link citations: [N](url) where N is a sequential citation number and url contains /record/{id}/preview#blockIndex={n}
    2. Legacy R-label citations: [R1-2], 【R1-2】
    """
    if records is None:
        records = []
    if virtual_record_id_to_result is None:
        virtual_record_id_to_result = {}

    # First try markdown link citation pattern: [citation_number](url_with_blockIndex)
    md_link_pattern = r'\[([^\]]*?)\]\(([^)]*?/record/[^)]*?preview[^)]*?blockIndex=\d+[^)]*?)\)'
    md_matches = list(re.finditer(md_link_pattern, answer_text))

    if md_matches:
        return _normalize_markdown_link_citations_for_agent(
            answer_text, md_matches, final_results, virtual_record_id_to_result, records
        )

    return answer_text, []

def _normalize_markdown_link_citations_for_agent(
    answer_text: str,
    md_matches: list,
    final_results: list[dict[str, Any]],
    virtual_record_id_to_result: dict[str, dict[str, Any]],
    records: list[dict[str, Any]],
) -> tuple[str, list[dict[str, Any]]]:
    """
    Normalize markdown link citations for agent workflow.
    Maps each citation URL to the corresponding block and renumbers sequentially.
    Enhances metadata from virtual_record_id_to_result.
    """
    url_to_doc_index = {}
    flattened_final_results = []
    vrids = [record.get("virtual_record_id") for record in records]

    for doc in final_results:
        virtual_record_id = doc.get("virtual_record_id")
        if virtual_record_id in vrids:
            continue
        block_type = doc.get("block_type")
        if block_type == GroupType.TABLE.value:
            _, child_results = doc.get("content", ("", []))
            if child_results:
                for child in child_results:
                    flattened_final_results.append(child)
                    child_url = child.get("block_web_url") or child.get("metadata", {}).get("blockWebUrl", "")
                    if child_url:
                        url_to_doc_index[child_url] = len(flattened_final_results) - 1
            else:
                flattened_final_results.append(doc)
                doc_url = doc.get("block_web_url") or doc.get("metadata", {}).get("blockWebUrl", "")
                if doc_url:
                    url_to_doc_index[doc_url] = len(flattened_final_results) - 1
        else:
            flattened_final_results.append(doc)
            doc_url = doc.get("block_web_url") or doc.get("metadata", {}).get("blockWebUrl", "")
            if doc_url:
                url_to_doc_index[doc_url] = len(flattened_final_results) - 1

    unique_urls = []
    seen_urls = set()
    for match in md_matches:
        url = match.group(2).strip()
        if url not in seen_urls:
            unique_urls.append(url)
            seen_urls.add(url)

    url_to_citation_num = {}
    new_citations = []
    new_citation_num = 1

    for url in unique_urls:
        doc = None
        if url in url_to_doc_index:
            idx = url_to_doc_index[url]
            doc = flattened_final_results[idx]
        else:
            record_id = _extract_record_id_from_url(url)
            block_index = _extract_block_index_from_url(url)
            if record_id is not None and block_index is not None:
                # Search in records from tool calls
                for r in records:
                    if r.get("id") == record_id:
                        block_container = r.get("block_containers", {}) or {}
                        blocks = block_container.get("blocks", []) or []
                        if 0 <= block_index < len(blocks):
                            block = blocks[block_index]
                            enhanced_metadata = get_enhanced_metadata(r, block, {})
                            enhanced_metadata["origin"] = enhanced_metadata.get("origin") or ""
                            enhanced_metadata["recordName"] = enhanced_metadata.get("recordName") or ""
                            enhanced_metadata["recordId"] = enhanced_metadata.get("recordId") or ""
                            enhanced_metadata["mimeType"] = enhanced_metadata.get("mimeType") or ""
                            enhanced_metadata["orgId"] = enhanced_metadata.get("orgId") or ""
                            bt = block.get("type")
                            data = block.get("data")
                            if bt == BlockType.TABLE_ROW.value:
                                data = data.get("row_natural_language_text", "")
                            elif bt == BlockType.IMAGE.value:
                                data = data.get("uri", "")
                            new_citations.append({
                                "content": "Image" if isinstance(data, str) and data.startswith("data:image/") else data,
                                "chunkIndex": new_citation_num,
                                "metadata": enhanced_metadata,
                                "citationType": "vectordb|document",
                            })
                            url_to_citation_num[url] = new_citation_num
                            new_citation_num += 1
                        break

                # Also search in virtual_record_id_to_result
                if url not in url_to_citation_num:
                    for rec in virtual_record_id_to_result.values():
                        if rec and rec.get("id") == record_id:
                            block_container = rec.get("block_containers", {}) or {}
                            blocks = block_container.get("blocks", []) or []
                            if 0 <= block_index < len(blocks):
                                block = blocks[block_index]
                                enhanced_metadata = get_enhanced_metadata(rec, block, {})
                                enhanced_metadata["origin"] = enhanced_metadata.get("origin") or ""
                                enhanced_metadata["recordName"] = enhanced_metadata.get("recordName") or ""
                                enhanced_metadata["recordId"] = enhanced_metadata.get("recordId") or ""
                                enhanced_metadata["mimeType"] = enhanced_metadata.get("mimeType") or ""
                                enhanced_metadata["orgId"] = enhanced_metadata.get("orgId") or ""
                                bt = block.get("type")
                                data = block.get("data")
                                if bt == BlockType.TABLE_ROW.value:
                                    data = data.get("row_natural_language_text", "")
                                elif bt == BlockType.IMAGE.value:
                                    data = data.get("uri", "")
                                new_citations.append({
                                    "content": "Image" if isinstance(data, str) and data.startswith("data:image/") else data,
                                    "chunkIndex": new_citation_num,
                                    "metadata": enhanced_metadata,
                                    "citationType": "vectordb|document",
                                })
                                url_to_citation_num[url] = new_citation_num
                                new_citation_num += 1
                            break
                continue

        if doc is not None:
            content = doc.get("content", "")
            metadata = doc.get("metadata", {}) or {}

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

            metadata["origin"] = metadata.get("origin") or ""
            metadata["recordName"] = metadata.get("recordName") or ""
            metadata["recordId"] = metadata.get("recordId") or ""
            metadata["mimeType"] = metadata.get("mimeType") or ""
            metadata["orgId"] = metadata.get("orgId") or ""

            new_citations.append({
                "content": "Image" if isinstance(content, str) and content.startswith("data:image/") else content,
                "chunkIndex": new_citation_num,
                "metadata": metadata,
                "citationType": "vectordb|document",
            })
            url_to_citation_num[url] = new_citation_num
            new_citation_num += 1

    if not new_citations and unique_urls:
        logger.error(f"FAILED to create citations for URLs: {unique_urls}")

    answer_text = _renumber_citation_links(answer_text, md_matches, url_to_citation_num)

    return answer_text, new_citations
