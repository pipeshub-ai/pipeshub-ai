import re
from dataclasses import dataclass
from typing import Any

from app.models.blocks import BlockType, GroupType
from app.utils.chat_helpers import (
    get_enhanced_metadata,
    is_base64_image,
    valid_group_labels,
)
from app.utils.logger import create_logger

# Initialize logger
logger = create_logger(__name__)

# Regex matching markdown links whose target is any of:
#   - internal tiny ref (ref1, ref2, ...)
#   - tiny web-ref URL (https://ref1.xyz)
#   - legacy full block web URL (/record/<id>/preview#blockIndex=N)
#   - any http(s):// URL used as a web citation target
# Kept permissive because web citations are arbitrary external URLs.
_MD_LINK_PATTERN = r'\[([^\]]*?)\]\((ref\d+|https?://[^)]+)\)'

# Tiny web-ref URL format exposed to the LLM when the real URL exceeds TINY_URL_THRESHOLD.
# The embedded refN resolves through the shared CitationRefMapper, same as internal-search refs.
_TINY_WEB_REF_PATTERN = re.compile(r'^https?://(ref\d+)\.xyz/?$')

# URLs with length <= this are shown to the LLM verbatim; longer URLs are aliased as https://refN.xyz.
TINY_URL_THRESHOLD = 40


def extract_tiny_ref(target: str) -> str | None:
    """Return the inner refN from a tiny web-ref URL like https://ref1.xyz, else None."""
    if not target:
        return None
    m = _TINY_WEB_REF_PATTERN.match(target)
    return m.group(1) if m else None


def build_tiny_web_ref_url(ref: str) -> str:
    """Wrap a refN token into its LLM-facing tiny URL form (https://refN.xyz)."""
    return f"https://{ref}.xyz"


def display_url_for_llm(url: str, ref_mapper: "object | None") -> str:
    """Return the citation form the LLM should see for a web URL.

    Short URLs (<= TINY_URL_THRESHOLD) are emitted verbatim so the LLM can cite them
    directly. Longer URLs are aliased through the ref_mapper as https://refN.xyz so
    small models do not mangle them.
    """
    if not url:
        return url
    if len(url) <= TINY_URL_THRESHOLD or ref_mapper is None:
        return url
    ref = ref_mapper.get_or_create_ref(url)
    return build_tiny_web_ref_url(ref)

CITATION_WORD_LIMIT = 4


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


def _resolve_ref(target: str, ref_to_url: dict[str, str] | None) -> str:
    """Resolve a citation target — if it's a tiny ref (refN) or tiny web-ref URL (https://refN.xyz),
    return the full URL from the mapping; otherwise return as-is."""
    if not ref_to_url:
        return target
    if target in ref_to_url:
        return ref_to_url[target]
    inner_ref = extract_tiny_ref(target)
    if inner_ref and inner_ref in ref_to_url:
        return ref_to_url[inner_ref]
    return target


def _renumber_citation_links(
    text: str,
    md_matches: list,
    url_to_citation_num: dict[str, int],
    ref_to_url: dict[str, str] | None = None,
) -> str:
    """
    Replace citation numbers in markdown links with their new sequential numbers.
    Resolves tiny refs to full URLs in the output so the frontend receives full URLs.
    Processes matches in reverse order to preserve string positions.
    """
    for match in reversed(md_matches):
        raw_target = match.group(2).strip()
        full_url = _resolve_ref(raw_target, ref_to_url)
        new_num = url_to_citation_num.get(full_url)
        if new_num is not None:
            replacement = f"[{new_num}]({full_url})"
        else:
            replacement = ""
        text = text[:match.start()] + replacement + text[match.end():]
    return text


def _extract_block_index_from_url(url: str) -> int | None:
    """Extract blockIndex value from a block web URL like /record/abc/preview#blockIndex=5"""
    m = re.search(r'blockIndex=(\d+)', url)
    if m:
        return int(m.group(1))
    return None

def _extract_record_id_from_url(url: str) -> str | None:
    """Extract recordId from a block web URL like /record/abc123/preview#blockIndex=5"""
    m = re.search(r'/record/([^/]+)/preview', url)
    if m:
        return m.group(1)
    return None


def _find_web_record_by_url(
    url: str,
    web_records: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Find a web record whose 'url' field matches exactly."""
    if not url:
        return None
    for rec in web_records:
        if rec.get("url") == url:
            return rec
    return None


def _safe_stringify_content(value: Any) -> str:
    """Convert citation content to string without raising."""
    try:
        return str(value)
    except Exception as exc:
        logger.warning("Failed to cast citation content to string: %s", exc)
        return ""



def _is_url_resolvable_via_records(
    url: str,
    records: list[dict[str, Any]],
    flattened_final_results: list[dict[str, Any]],
    virtual_record_id_to_result: dict[str, dict[str, Any]] | None = None,
) -> bool:
    """Check if a citation URL can be resolved via record_id+block_index lookup."""
    record_id = _extract_record_id_from_url(url)
    block_index = _extract_block_index_from_url(url)
    if record_id is None or block_index is None:
        return False

    for doc in flattened_final_results:
        metadata = doc.get("metadata", {})
        doc_record_id = metadata.get("recordId")
        doc_block_index = doc.get("block_index")
        if doc_record_id == record_id and doc_block_index == block_index:
            return True

        # Table groups nest child rows inside content tuple — check those too
        if (doc.get("block_type") == GroupType.TABLE.value or doc.get("block_type") in valid_group_labels) and doc_record_id == record_id:
            content = doc.get("content")
            if isinstance(content, tuple) and len(content) >= 2:
                child_results = content[1]
                if isinstance(child_results, list):
                    for child in child_results:
                        if child.get("block_index") == block_index:
                            return True

    for r in records:
        if r.get("id") == record_id:
            blocks = (r.get("block_containers", {}) or {}).get("blocks", []) or []
            if 0 <= block_index < len(blocks):
                return True

    if virtual_record_id_to_result:
        for rec in virtual_record_id_to_result.values():
            if rec and rec.get("id") == record_id:
                blocks = (rec.get("block_containers", {}) or {}).get("blocks", []) or []
                if 0 <= block_index < len(blocks):
                    return True

    return False

def detect_hallucinated_citation_urls(
    answer_text: str,
    records: list[dict[str, Any]] | None = None,
    flattened_final_results: list[dict[str, Any]] | None = None,
    virtual_record_id_to_result: dict[str, dict[str, Any]] | None = None,
    ref_to_url: dict[str, str] | None = None,
) -> list[str]:
    """
    Detect citation targets in answer_text that don't match any known block.

    Handles both tiny refs (ref1, ref2) and legacy full URLs.

    Returns:
            - hallucinated: targets found in answer that could not be resolved
    """
    if records is None:
        records = []
    if flattened_final_results is None:
        flattened_final_results = []

    md_matches = list(re.finditer(_MD_LINK_PATTERN, answer_text))
    if not md_matches:
        return []

    unique_targets = []
    seen = set()
    for match in md_matches:
        target = match.group(2).strip()
        if target not in seen:
            unique_targets.append(target)
            seen.add(target)

    hallucinated = []
    for target in unique_targets:
        # Tiny ref: valid iff it exists in the ref_to_url mapping
        if re.match(r'^ref\d+$', target):
            if ref_to_url and target in ref_to_url:
                continue
            hallucinated.append(target)
        else:
            continue

    return hallucinated


def normalize_citations_and_chunks(
    answer_text: str,
    final_results: list[dict[str, Any]],
    records: list[dict[str, Any]] = None,
    ref_to_url: dict[str, str] | None = None,
    virtual_record_id_to_result: dict[str, dict[str, Any]] | None = None,
    web_records: list[dict[str, Any]] | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    """
    Normalize citation numbers in answer text to be sequential (1,2,3...)
    and create corresponding citation chunks with correct mapping.

    Supports tiny refs (ref1, ref2), legacy full URL citations, and web citations (#wcite=).
    """

    if records is None:
        records = []
    if virtual_record_id_to_result is None:
        virtual_record_id_to_result = {}

    md_matches = list(re.finditer(_MD_LINK_PATTERN, answer_text))

    if md_matches:
        return _normalize_markdown_link_citations(
            answer_text, md_matches, final_results, records,
            ref_to_url=ref_to_url,
            virtual_record_id_to_result=virtual_record_id_to_result,
            web_records=web_records,
        )

    return answer_text, []


def _normalize_markdown_link_citations(
    answer_text: str,
    md_matches: list,
    final_results: list[dict[str, Any]],
    records: list[dict[str, Any]],
    ref_to_url: dict[str, str] | None = None,
    virtual_record_id_to_result: dict[str, dict[str, Any]] | None = None,
    web_records: list[dict[str, Any]] | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    """
    Normalize markdown link citations [text](target) where target is a tiny ref or full URL.
    Maps each citation to the corresponding block in final_results, records, or web_records
    and renumbers sequentially.
    """
    if web_records is None:
        web_records = []

    url_to_doc_index = {}
    flattened_final_results = []

    for doc in final_results:
        block_type = doc.get("block_type")
        if block_type == GroupType.TABLE.value or block_type in valid_group_labels:
            _, child_results = doc.get("content", ("", []))
            if child_results:
                for child in child_results:
                    child_url = child.get("block_web_url")
                    if child_url:
                        flattened_final_results.append(child)
                        url_to_doc_index[child_url] = len(flattened_final_results) - 1
        else:
            doc_url = doc.get("block_web_url")
            if doc_url:
                flattened_final_results.append(doc)
                url_to_doc_index[doc_url] = len(flattened_final_results) - 1

    # Collect unique citation targets, resolving refs to full URLs
    unique_urls = []
    seen_urls = set()
    for match in md_matches:
        raw_target = match.group(2).strip()
        full_url = _resolve_ref(raw_target, ref_to_url)
        if full_url not in seen_urls:
            unique_urls.append(full_url)
            seen_urls.add(full_url)

    url_to_citation_num = {}
    new_citations = []
    new_citation_num = 1

    def _append_citation_from_record(
        record: dict[str, Any],
        record_id: str,
        block_index: int,
        empty_data_log_prefix: str,
        url: str,
    ) -> bool:
        nonlocal new_citation_num

        block_container = record.get("block_containers", {}) or {}
        blocks = block_container.get("blocks", []) or []
        if not (0 <= block_index < len(blocks)):
            return False

        block = blocks[block_index]
        enhanced_metadata = get_enhanced_metadata(record, block, {})
        block_type = block.get("type")
        data = block.get("data")
        if block_type == BlockType.TABLE_ROW.value:
            data = data.get("row_natural_language_text", "")
        elif block_type == BlockType.IMAGE.value:
            data = data.get("uri", "")
        if not data:
            logger.warning(
                "🔎 [KB-CITE] normalize(chat): %s | record_id=%s block_index=%s block_type=%s",
                empty_data_log_prefix, record_id, block_index, block_type,
            )
            return False

        citation_content = "Image" if is_base64_image(data) else _safe_stringify_content(value=data)
        if not citation_content:
            return False

        new_citations.append({
            "content": citation_content,
            "chunkIndex": new_citation_num,
            "metadata": enhanced_metadata,
            "citationType": "vectordb|document",
        })
        url_to_citation_num[url] = new_citation_num
        new_citation_num += 1
        return True

    for url in unique_urls:
    # Match by exact URL against web records (web citations are keyed by URL)
        if web_records:
            web_rec = _find_web_record_by_url(url, web_records)
            if web_rec:
                web_content = web_rec.get("content", "")
                if not web_content:
                    continue
                new_citations.append({
                    "content": _safe_stringify_content(value=web_content),
                    "chunkIndex": new_citation_num,
                    "metadata": {
                        "recordId":url.split("#:~:text=")[0],
                        "mimeType":"text/html",
                        "recordName": url.split("#:~:text=")[0],
                        "webUrl": url,
                        "origin": "WEB_SEARCH",
                        "orgId": web_rec.get("org_id", ""),
                        "connector": "WEB",
                    },
                    "citationType": "web|url",
                })
                url_to_citation_num[url] = new_citation_num
                new_citation_num += 1
            else:
                logger.debug(f"No web record found for URL: {url}")
            continue

        if url in url_to_doc_index:
            idx = url_to_doc_index[url]
            doc = flattened_final_results[idx]
            content = doc.get("content", "")
            if isinstance(content, tuple):
                content = content[0]
            elif not isinstance(content, str):
                content = _safe_stringify_content(value=content)

            if not content:
                logger.warning("🔎 [KB-CITE] normalize(chat): empty content for matched URL | url=%s", url)
                continue

            new_citations.append({
                "content": "Image" if is_base64_image(content) else content,
                "chunkIndex": new_citation_num,
                "metadata": doc.get("metadata", {}),
                "citationType": "vectordb|document",
            })
            url_to_citation_num[url] = new_citation_num
            new_citation_num += 1
        else:
            # Try matching by record_id + block_index extracted from URL
            record_id = _extract_record_id_from_url(url)
            block_index = _extract_block_index_from_url(url)
            if record_id is not None and block_index is not None:
                _matched = False
                for r in records:
                    if r.get("id") == record_id:
                        _matched = _append_citation_from_record(
                            record=r,
                            record_id=record_id,
                            block_index=block_index,
                            empty_data_log_prefix="records fallback empty data",
                            url=url,
                        )
                        break

                if not _matched and virtual_record_id_to_result:
                    for rec in virtual_record_id_to_result.values():
                        if rec and rec.get("id") == record_id:
                            _matched = _append_citation_from_record(
                                record=rec,
                                record_id=record_id,
                                block_index=block_index,
                                empty_data_log_prefix="vrid_map fallback empty data",
                                url=url,
                            )
                            break

                if not _matched:
                    logger.warning(
                        "🔎 [KB-CITE] normalize(chat): DROPPED citation | url=%s record_id=%s block_index=%s records_len=%d vrid_map_len=%d",
                        url, record_id, block_index,
                        len(records) if records else 0,
                        len(virtual_record_id_to_result) if virtual_record_id_to_result else 0,
                    )


    answer_text = _renumber_citation_links(answer_text, md_matches, url_to_citation_num, ref_to_url=ref_to_url)

    return answer_text, new_citations


def normalize_citations_and_chunks_for_agent(
    answer_text: str,
    final_results: list[dict[str, Any]],
    virtual_record_id_to_result: dict[str, dict[str, Any]] | None = None,
    records: list[dict[str, Any]] | None = None,
    ref_to_url: dict[str, str] | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    """
    Normalize citation numbers in answer text to be sequential (1,2,3...)
    and create corresponding citation chunks with correct mapping.

    Supports both tiny refs (ref1, ref2) and legacy full URL citations.
    """
    if records is None:
        records = []
    if virtual_record_id_to_result is None:
        virtual_record_id_to_result = {}

    md_matches = list(re.finditer(_MD_LINK_PATTERN, answer_text))

    if md_matches:
        return _normalize_markdown_link_citations_for_agent(
            answer_text, md_matches, final_results, virtual_record_id_to_result, records,
            ref_to_url=ref_to_url,
        )

    return answer_text, []

def _normalize_markdown_link_citations_for_agent(
    answer_text: str,
    md_matches: list,
    final_results: list[dict[str, Any]],
    virtual_record_id_to_result: dict[str, dict[str, Any]],
    records: list[dict[str, Any]],
    ref_to_url: dict[str, str] | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    """
    Normalize markdown link citations for agent workflow.
    Maps each citation to the corresponding block and renumbers sequentially.
    Enhances metadata from virtual_record_id_to_result.
    """
    url_to_doc_index = {}
    flattened_final_results = []

    for doc in final_results:
        virtual_record_id = doc.get("virtual_record_id")
        block_type = doc.get("block_type")
        if block_type == GroupType.TABLE.value or block_type in valid_group_labels:
            _, child_results = doc.get("content", ("", []))
            if child_results:
                for child in child_results:
                    child_url = child.get("block_web_url")
                    if child_url:
                        flattened_final_results.append(child)
                        url_to_doc_index[child_url] = len(flattened_final_results) - 1
        else:
            doc_url = doc.get("block_web_url")
            if doc_url:
                flattened_final_results.append(doc)
                url_to_doc_index[doc_url] = len(flattened_final_results) - 1

    # Collect unique citation targets, resolving refs to full URLs
    unique_urls = []
    seen_urls = set()
    for match in md_matches:
        raw_target = match.group(2).strip()
        full_url = _resolve_ref(raw_target, ref_to_url)
        if full_url not in seen_urls:
            unique_urls.append(full_url)
            seen_urls.add(full_url)

    url_to_citation_num = {}
    new_citations = []
    new_citation_num = 1

    for url in unique_urls:
        if url in url_to_doc_index:
            idx = url_to_doc_index[url]
            doc = flattened_final_results[idx]
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

            # Ensure required fields
            metadata["origin"] = metadata.get("origin") or ""
            metadata["recordName"] = metadata.get("recordName") or ""
            metadata["recordId"] = metadata.get("recordId") or ""
            metadata["mimeType"] = metadata.get("mimeType") or ""
            metadata["orgId"] = metadata.get("orgId") or ""

            if isinstance(content, tuple):
                content = content[0]
            elif not isinstance(content, str):
                content = _safe_stringify_content(value=content)

            if not content:
                continue

            new_citations.append({
                "content": "Image" if is_base64_image(content) else content,
                "chunkIndex": new_citation_num,
                "metadata": metadata,
                "citationType": "vectordb|document",
            })
            url_to_citation_num[url] = new_citation_num
            new_citation_num += 1
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
                            if not data:
                                continue
                            citation_content = "Image" if is_base64_image(data) else _safe_stringify_content(value=data)
                            if not citation_content:
                                continue
                            new_citations.append({
                                "content": citation_content,
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
                                if not data:
                                    continue
                                citation_content = "Image" if is_base64_image(data) else _safe_stringify_content(value=data)
                                if not citation_content:
                                    continue
                                new_citations.append({
                                    "content": citation_content,
                                    "chunkIndex": new_citation_num,
                                    "metadata": enhanced_metadata,
                                    "citationType": "vectordb|document",
                                })
                                url_to_citation_num[url] = new_citation_num
                                new_citation_num += 1
                            break
                if url not in url_to_citation_num:
                    logger.warning(
                        "🔎 [KB-CITE] normalize(agent): DROPPED citation | url=%s record_id=%s block_index=%s records_len=%d vrid_map_len=%d",
                        url, record_id, block_index,
                        len(records) if records else 0,
                        len(virtual_record_id_to_result) if virtual_record_id_to_result else 0,
                    )
                continue



    if not new_citations and unique_urls:
        logger.error(f"FAILED to create citations for URLs: {unique_urls}")

    answer_text = _renumber_citation_links(answer_text, md_matches, url_to_citation_num, ref_to_url=ref_to_url)

    return answer_text, new_citations
