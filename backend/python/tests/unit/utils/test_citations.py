"""Unit tests for app.utils.citations (new markdown-link citation format)."""

import re
from unittest.mock import MagicMock, patch

from app.models.blocks import BlockType, GroupType
import app.utils.citations as citations_mod

from app.utils.citations import (
    ChatDocCitation,
    _clean_duplicate_citation_links,
    _extract_block_index_from_url,
    _extract_record_id_from_url,
    _find_web_record_by_url,
    _renumber_citation_links,
    build_tiny_web_ref_url,
    detect_hallucinated_citation_urls,
    display_url_for_llm,
    extract_tiny_ref,
    fix_json_string,
    normalize_citations_and_chunks,
    normalize_citations_and_chunks_for_agent,
)

# ---------------------------------------------------------------------------
# URL constants used across tests
# ---------------------------------------------------------------------------
BASE = "http://app.example.com"
REC1 = "rec-aaa-111"
REC2 = "rec-bbb-222"

# Decodes to a real PNG; is_base64_image() rejects placeholder payloads like abc123.
_VALID_MINIMAL_PNG_DATA_URI = (
    "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
    "+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def _url(record_id: str, block_index: int, base: str = BASE) -> str:
    """Build a canonical block preview URL."""
    return f"{base}/record/{record_id}/preview#blockIndex={block_index}"


def _grp_url(record_id: str, group_index: int, base: str = BASE) -> str:
    """Build a block-group preview URL (blockGroupIndex)."""
    return f"{base}/record/{record_id}/preview#blockGroupIndex={group_index}"


# ---------------------------------------------------------------------------
# Helpers for building mock documents
# ---------------------------------------------------------------------------
def _make_doc(virtual_record_id, block_index, content, block_type="text", metadata=None, block_web_url=None):
    """Build a minimal document dict matching what the citation code expects."""
    if block_web_url is None:
        block_web_url = _url(virtual_record_id, block_index)
    return {
        "virtual_record_id": virtual_record_id,
        "block_index": block_index,
        "block_type": block_type,
        "content": content,
        "block_web_url": block_web_url,
        "metadata": metadata or {
            "origin": "GOOGLE_WORKSPACE",
            "recordName": "Test Doc",
            "recordId": virtual_record_id,
            "mimeType": "application/pdf",
            "orgId": "org-1",
        },
    }


# ---------------------------------------------------------------------------
# fix_json_string
# ---------------------------------------------------------------------------
class TestFixJsonString:
    """Tests for fix_json_string()."""

    def test_newline_inside_string_is_escaped(self):
        raw = '{"key": "line1\nline2"}'
        result = fix_json_string(raw)
        assert result == '{"key": "line1\\nline2"}'

    def test_tab_inside_string_is_escaped(self):
        raw = '{"key": "col1\tcol2"}'
        result = fix_json_string(raw)
        assert result == '{"key": "col1\\tcol2"}'

    def test_carriage_return_inside_string_is_escaped(self):
        raw = '{"key": "line1\rline2"}'
        result = fix_json_string(raw)
        assert result == '{"key": "line1\\rline2"}'

    def test_control_chars_outside_string_are_kept(self):
        raw = '{\n  "key": "value"\n}'
        result = fix_json_string(raw)
        assert result == '{\n  "key": "value"\n}'

    def test_escaped_quote_inside_string(self):
        raw = '{"key": "say \\"hello\\""}'
        result = fix_json_string(raw)
        assert result == '{"key": "say \\"hello\\""}'

    def test_backslash_followed_by_normal_char(self):
        raw = '{"key": "already\\nescaped"}'
        result = fix_json_string(raw)
        assert result == '{"key": "already\\nescaped"}'

    def test_empty_string(self):
        assert fix_json_string("") == ""

    def test_no_strings_at_all(self):
        raw = "{123: 456}"
        assert fix_json_string(raw) == "{123: 456}"

    def test_control_char_below_space_inside_string(self):
        raw = '{"k": "val\x01ue"}'
        result = fix_json_string(raw)
        assert "\\u0001" in result

    def test_extended_ascii_range_inside_string(self):
        raw = '{"k": "val\x7fue"}'
        result = fix_json_string(raw)
        assert "\\u007f" in result

    def test_extended_ascii_at_boundary_159(self):
        raw = '{"k": "val\x9fue"}'
        result = fix_json_string(raw)
        assert "\\u009f" in result

    def test_char_above_159_inside_string_not_escaped(self):
        raw = '{"k": "val\xa0ue"}'
        result = fix_json_string(raw)
        assert "\\u00a0" not in result
        assert "\xa0" in result

    def test_multiple_strings(self):
        raw = '{"a": "line\none", "b": "col\tcol"}'
        result = fix_json_string(raw)
        assert '\\n' in result
        assert '\\t' in result

    def test_mixed_control_chars_inside_string(self):
        raw = '{"k": "a\n\r\tb"}'
        result = fix_json_string(raw)
        assert result == '{"k": "a\\n\\r\\tb"}'


# ---------------------------------------------------------------------------
# _extract_block_index_from_url
# ---------------------------------------------------------------------------
class TestExtractBlockIndexFromUrl:

    def test_simple_block_index(self):
        url = _url(REC1, 5)
        assert _extract_block_index_from_url(url) == 5

    def test_zero_block_index(self):
        url = _url(REC1, 0)
        assert _extract_block_index_from_url(url) == 0

    def test_large_block_index(self):
        url = _url(REC1, 9999)
        assert _extract_block_index_from_url(url) == 9999

    def test_url_without_block_index_returns_none(self):
        url = f"{BASE}/record/{REC1}/preview"
        assert _extract_block_index_from_url(url) is None

    def test_url_with_block_group_index_returns_none(self):
        # blockGroupIndex does NOT match blockIndex pattern
        url = _grp_url(REC1, 3)
        assert _extract_block_index_from_url(url) is None

    def test_empty_string_returns_none(self):
        assert _extract_block_index_from_url("") is None

    def test_arbitrary_string_returns_none(self):
        assert _extract_block_index_from_url("not a url") is None

    def test_block_index_fragment_only(self):
        assert _extract_block_index_from_url("#blockIndex=7") == 7


# ---------------------------------------------------------------------------
# _extract_record_id_from_url
# ---------------------------------------------------------------------------
class TestExtractRecordIdFromUrl:

    def test_extracts_simple_id(self):
        url = _url("abc-123", 0)
        assert _extract_record_id_from_url(url) == "abc-123"

    def test_extracts_uuid_style_id(self):
        uid = "550e8400-e29b-41d4-a716-446655440000"
        url = _url(uid, 1)
        assert _extract_record_id_from_url(url) == uid

    def test_url_without_record_returns_none(self):
        url = f"{BASE}/something/else"
        assert _extract_record_id_from_url(url) is None

    def test_empty_string_returns_none(self):
        assert _extract_record_id_from_url("") is None

    def test_plain_string_returns_none(self):
        assert _extract_record_id_from_url("no record here") is None

    def test_id_with_special_chars(self):
        url = f"{BASE}/record/rec_1.2/preview#blockIndex=0"
        assert _extract_record_id_from_url(url) == "rec_1.2"

    def test_extracts_record_landing_url_without_preview(self):
        url = f"{BASE}/record/{REC1}"
        assert _extract_record_id_from_url(url) == REC1


# ---------------------------------------------------------------------------
# _renumber_citation_links
# ---------------------------------------------------------------------------
class TestRenumberCitationLinks:

    def _make_matches(self, text):
        """Return regex matches for the same pattern used in citations.py."""
        pattern = r'\[([^\]]*?)\]\(([^)]*?/record/[^)]*?preview[^)]*?block(?:Group)?Index=\d+[^)]*?)\)'
        return list(re.finditer(pattern, text))

    def test_single_citation_renumbered(self):
        url = _url(REC1, 0)
        text = f"See [1]({url}) here."
        matches = self._make_matches(text)
        result = _renumber_citation_links(text, matches, {url: 5})
        assert "[5]" in result
        assert url in result

    def test_url_not_in_mapping_left_unchanged(self):
        url = _url(REC1, 0)
        text = f"See [1]({url}) here."
        matches = self._make_matches(text)
        result = _renumber_citation_links(text, matches, {})
        # Links whose URL is not in the mapping are left as-is
        assert result == text

    def test_multiple_citations_renumbered_in_order(self):
        url1 = _url(REC1, 0)
        url2 = _url(REC2, 3)
        text = f"A [1]({url1}) and B [2]({url2})."
        matches = self._make_matches(text)
        result = _renumber_citation_links(text, matches, {url1: 10, url2: 20})
        assert "[10]" in result
        assert "[20]" in result

    def test_reverse_order_preserves_positions(self):
        """Processing in reverse ensures string offsets remain valid."""
        url1 = _url(REC1, 0)
        url2 = _url(REC2, 1)
        text = f"[1]({url1}) then [2]({url2})"
        matches = self._make_matches(text)
        result = _renumber_citation_links(text, matches, {url1: 3, url2: 4})
        # Both replaced correctly
        assert "[3]" in result
        assert "[4]" in result
        assert result.index("[3]") < result.index("[4]")

    def test_empty_mapping_unchanged(self):
        url = _url(REC1, 0)
        text = f"[1]({url})"
        matches = self._make_matches(text)
        result = _renumber_citation_links(text, matches, {})
        # Links whose URL is not in the mapping are left as-is
        assert result == text

    def test_no_matches(self):
        text = "No citations here."
        result = _renumber_citation_links(text, [], {})
        assert result == text


# ---------------------------------------------------------------------------
# detect_hallucinated_citation_urls
# ---------------------------------------------------------------------------
class TestDetectHallucinatedCitationUrls:
    r"""detect_hallucinated_citation_urls only flags tiny refs (ref\d+).
    Full block URLs are outside its current scope and are silently skipped."""

    def test_no_citation_urls_returns_empty(self):
        result = detect_hallucinated_citation_urls("No citations here.")
        assert result == []

    def test_full_block_url_not_flagged(self):
        """Full block URLs are not examined — the function only handles tiny refs."""
        url = _url(REC1, 99)
        text = f"See [1]({url})."
        result = detect_hallucinated_citation_urls(text, records=[], flattened_final_results=[])
        assert url not in result

    def test_full_block_url_resolvable_not_flagged(self):
        """Resolvable full URLs are also not flagged (skipped entirely)."""
        url = _url(REC1, 0)
        text = f"See [1]({url})."
        doc = {"metadata": {"recordId": REC1}, "block_index": 0}
        result = detect_hallucinated_citation_urls(text, flattened_final_results=[doc])
        assert url not in result

    def test_none_defaults_do_not_raise(self):
        """None records and flattened_final_results should not raise."""
        url = _url(REC1, 99)
        text = f"[1]({url})"
        result = detect_hallucinated_citation_urls(text, records=None, flattened_final_results=None)
        # Full URL is skipped; result should be empty
        assert result == []


# ---------------------------------------------------------------------------
# normalize_citations_and_chunks — new markdown-link format
# ---------------------------------------------------------------------------
class TestNormalizeCitationsAndChunks:
    """Tests for normalize_citations_and_chunks() with markdown link citations."""

    def test_no_markdown_links_returns_unchanged(self):
        """Without markdown link citations, returns text unchanged and empty list."""
        docs = [_make_doc(REC1, 0, "chunk")]
        answer = "No citations here."
        result_text, citations = normalize_citations_and_chunks(answer, docs)
        assert result_text == "No citations here."
        assert citations == []

    def test_single_citation_renumbered(self):
        url = _url(REC1, 0)
        docs = [_make_doc(REC1, 0, "chunk zero", block_web_url=url)]
        answer = f"See [1]({url}) for details."
        result_text, citations = normalize_citations_and_chunks(answer, docs)
        assert "[1]" in result_text
        assert url in result_text
        assert len(citations) == 1
        assert citations[0]["chunkIndex"] == 1
        assert citations[0]["content"] == "chunk zero"

    def test_multiple_citations_sequential(self):
        url1 = _url(REC1, 0)
        url2 = _url(REC2, 3)
        docs = [
            _make_doc(REC1, 0, "first chunk", block_web_url=url1),
            _make_doc(REC2, 3, "second chunk", block_web_url=url2),
        ]
        answer = f"Point A [1]({url1}) and point B [2]({url2})."
        result_text, citations = normalize_citations_and_chunks(answer, docs)
        assert "[1]" in result_text
        assert "[2]" in result_text
        assert len(citations) == 2
        assert citations[0]["chunkIndex"] == 1
        assert citations[1]["chunkIndex"] == 2

    def test_duplicate_url_counted_once(self):
        url = _url(REC1, 0)
        docs = [_make_doc(REC1, 0, "only chunk", block_web_url=url)]
        answer = f"See [1]({url}) and again [1]({url})."
        result_text, citations = normalize_citations_and_chunks(answer, docs)
        # Both references should map to citation 1
        assert result_text.count("[1]") == 2
        assert len(citations) == 1

    def test_image_content_replaced_with_label(self):
        url = _url(REC1, 0)
        docs = [_make_doc(REC1, 0, _VALID_MINIMAL_PNG_DATA_URI, block_web_url=url)]
        answer = f"See [1]({url})."
        _, citations = normalize_citations_and_chunks(answer, docs)
        assert citations[0]["content"] == "Image"

    def test_citation_type_is_vectordb_document(self):
        url = _url(REC1, 0)
        docs = [_make_doc(REC1, 0, "some text", block_web_url=url)]
        answer = f"[1]({url})"
        _, citations = normalize_citations_and_chunks(answer, docs)
        assert citations[0]["citationType"] == "vectordb|document"

    def test_table_block_children_flattened(self):
        child_url1 = _url(REC1, 5)
        child_url2 = _url(REC1, 6)
        child1 = {
            "block_index": 5,
            "block_web_url": child_url1,
            "content": "row1 data",
            "metadata": {
                "origin": "O", "recordName": "N", "recordId": "R", "mimeType": "M", "orgId": "Org"
            },
        }
        child2 = {
            "block_index": 6,
            "block_web_url": child_url2,
            "content": "row2 data",
            "metadata": {
                "origin": "O", "recordName": "N", "recordId": "R", "mimeType": "M", "orgId": "Org"
            },
        }
        table_doc = {
            "virtual_record_id": REC1,
            "block_index": 4,
            "block_type": GroupType.TABLE.value,
            "block_web_url": None,
            "content": ("table summary", [child1, child2]),
            "metadata": {},
        }
        answer = f"Data [1]({child_url1}) and [2]({child_url2})."
        result_text, citations = normalize_citations_and_chunks(answer, [table_doc])
        assert "[1]" in result_text
        assert "[2]" in result_text
        assert citations[0]["content"] == "row1 data"
        assert citations[1]["content"] == "row2 data"

    def test_table_block_empty_children_uses_parent(self):
        """TABLE block with no children: parent URL is not indexed, so no citation is built.
        The unresolved markdown link remains in the text unchanged."""
        parent_url = _url(REC1, 4)
        table_doc = {
            "virtual_record_id": REC1,
            "block_index": 4,
            "block_type": GroupType.TABLE.value,
            "block_web_url": parent_url,
            "content": ("table summary text", []),
            "metadata": {
                "origin": "O", "recordName": "N", "recordId": "R", "mimeType": "M", "orgId": "Org"
            },
        }
        answer = f"See [1]({parent_url})."
        result_text, citations = normalize_citations_and_chunks(answer, [table_doc])
        # No citation is produced; the unresolved link is left in the text as-is.
        assert citations == []
        assert f"[1]({parent_url})" in result_text

    def test_url_not_in_docs_falls_back_to_records(self):
        """URL not in flattened results but matches a record in records list."""
        url = _url(REC1, 0)
        record = {
            "id": REC1,
            "virtual_record_id": "vr1",
            "origin": "GOOGLE_WORKSPACE",
            "record_name": "Test",
            "mime_type": "text/plain",
            "block_containers": {
                "blocks": [
                    {"type": BlockType.TEXT.value, "data": "block text", "citation_metadata": None, "index": 0},
                ]
            },
        }
        # Doc has a different block_web_url so won't match
        docs = [_make_doc("vr1", 0, "chunk", block_web_url="http://other/no-match")]
        answer = f"See [1]({url})."
        with patch("app.utils.citations.get_enhanced_metadata", return_value={
            "origin": "GOOGLE_WORKSPACE",
            "recordName": "Test",
            "recordId": REC1,
            "mimeType": "text/plain",
            "orgId": "org-1",
        }):
            result_text, citations = normalize_citations_and_chunks(answer, docs, records=[record])
        assert "[1]" in result_text
        assert len(citations) == 1
        assert citations[0]["content"] == "block text"

    def test_record_fallback_table_row_block(self):
        url = _url(REC1, 0)
        record = {
            "id": REC1,
            "block_containers": {
                "blocks": [{
                    "type": BlockType.TABLE_ROW.value,
                    "data": {"row_natural_language_text": "Row content here"},
                    "index": 0,
                }]
            },
        }
        docs = [_make_doc("vr1", 0, "chunk", block_web_url="http://no-match")]
        answer = f"See [1]({url})."
        with patch("app.utils.citations.get_enhanced_metadata", return_value={
            "origin": "O", "recordName": "N", "recordId": REC1, "mimeType": "M", "orgId": "Org",
        }):
            _, citations = normalize_citations_and_chunks(answer, docs, records=[record])
        assert citations[0]["content"] == "Row content here"

    def test_record_fallback_table_row_missing_nl_text_falls_back_to_cells(self):
        """When row_natural_language_text is empty, cells should be joined as fallback."""
        url = _url(REC1, 0)
        record = {
            "id": REC1,
            "block_containers": {
                "blocks": [{
                    "type": BlockType.TABLE_ROW.value,
                    "data": {"row_natural_language_text": "", "cells": ["Alice", "30"]},
                    "index": 0,
                }]
            },
        }
        docs = [_make_doc("vr1", 0, "chunk", block_web_url="http://no-match")]
        answer = f"See [1]({url})."
        with patch("app.utils.citations.get_enhanced_metadata", return_value={
            "origin": "O", "recordName": "N", "recordId": REC1, "mimeType": "M", "orgId": "Org",
        }):
            _, citations = normalize_citations_and_chunks(answer, docs, records=[record])
        assert citations[0]["content"] == "Alice, 30"

    def test_record_fallback_table_row_missing_nl_text_falls_back_to_row_json(self):
        """When row_natural_language_text and cells are both absent, raw row JSON is used."""
        url = _url(REC1, 0)
        record = {
            "id": REC1,
            "block_containers": {
                "blocks": [{
                    "type": BlockType.TABLE_ROW.value,
                    "data": {"row_natural_language_text": "", "row": '{"id": 42, "name": "test"}'},
                    "index": 0,
                }]
            },
        }
        docs = [_make_doc("vr1", 0, "chunk", block_web_url="http://no-match")]
        answer = f"See [1]({url})."
        with patch("app.utils.citations.get_enhanced_metadata", return_value={
            "origin": "O", "recordName": "N", "recordId": REC1, "mimeType": "M", "orgId": "Org",
        }):
            _, citations = normalize_citations_and_chunks(answer, docs, records=[record])
        assert "42" in citations[0]["content"]

    def test_record_fallback_image_block(self):
        url = _url(REC1, 0)
        record = {
            "id": REC1,
            "block_containers": {
                "blocks": [{
                    "type": BlockType.IMAGE.value,
                    "data": {"uri": _VALID_MINIMAL_PNG_DATA_URI},
                    "index": 0,
                }]
            },
        }
        docs = [_make_doc("vr1", 0, "chunk", block_web_url="http://no-match")]
        answer = f"See [1]({url})."
        with patch("app.utils.citations.get_enhanced_metadata", return_value={
            "origin": "O", "recordName": "N", "recordId": REC1, "mimeType": "M", "orgId": "Org",
        }):
            _, citations = normalize_citations_and_chunks(answer, docs, records=[record])
        assert citations[0]["content"] == "Image"

    def test_record_fallback_block_index_out_of_range(self):
        url = _url(REC1, 99)
        record = {
            "id": REC1,
            "block_containers": {"blocks": [{"type": "text", "data": "x", "index": 0}]},
        }
        docs = []
        answer = f"See [1]({url})."
        _, citations = normalize_citations_and_chunks(answer, docs, records=[record])
        assert len(citations) == 0

    def test_url_not_matching_any_record_skipped(self):
        url = _url(REC1, 0)
        # No docs, no records with matching id
        answer = f"See [1]({url})."
        _, citations = normalize_citations_and_chunks(answer, [], records=[])
        assert len(citations) == 0

    def test_record_fallback_via_virtual_record_id_to_result(self):
        """Chat normalize should fallback via virtual_record_id_to_result when records/docs miss."""
        url = _url(REC1, 0)
        vrid_map = {
            "vr1": {
                "id": REC1,
                "block_containers": {
                    "blocks": [{"type": BlockType.TEXT.value, "data": "vrid fallback text", "index": 0}]
                },
            }
        }
        answer = f"See [1]({url})."
        with patch("app.utils.citations.get_enhanced_metadata", return_value={
            "origin": "O", "recordName": "N", "recordId": REC1, "mimeType": "M", "orgId": "Org",
        }):
            result_text, citations = normalize_citations_and_chunks(
                answer,
                final_results=[],
                records=[],
                virtual_record_id_to_result=vrid_map,
            )

        assert "[1]" in result_text
        assert len(citations) == 1
        assert citations[0]["content"] == "vrid fallback text"

    def test_record_fallback_via_virtual_record_id_to_result_image_block(self):
        """Chat normalize uses image label for IMAGE blocks from virtual_record_id_to_result fallback."""
        url = _url(REC1, 0)
        vrid_map = {
            "vr1": {
                "id": REC1,
                "block_containers": {
                    "blocks": [{
                        "type": BlockType.IMAGE.value,
                        "data": {"uri": _VALID_MINIMAL_PNG_DATA_URI},
                        "index": 0,
                    }]
                },
            }
        }
        answer = f"See [1]({url})."
        with patch("app.utils.citations.get_enhanced_metadata", return_value={
            "origin": "O", "recordName": "N", "recordId": REC1, "mimeType": "M", "orgId": "Org",
        }):
            _, citations = normalize_citations_and_chunks(
                answer,
                final_results=[],
                records=[],
                virtual_record_id_to_result=vrid_map,
            )

        assert len(citations) == 1
        assert citations[0]["content"] == "Image"

    def test_none_virtual_record_id_to_result_defaults_to_empty(self):
        """Passing None for virtual_record_id_to_result should not break chat normalize."""
        url = _url(REC1, 0)
        docs = [_make_doc(REC1, 0, "content", block_web_url=url)]
        answer = f"See [1]({url})."
        _, citations = normalize_citations_and_chunks(
            answer,
            docs,
            virtual_record_id_to_result=None,
        )
        assert len(citations) == 1

    def test_tuple_content_in_doc_unpacked(self):
        """When a doc has tuple content (e.g. table summary), the first item is used."""
        url = _url(REC1, 0)
        docs = [{
            "virtual_record_id": REC1,
            "block_index": 0,
            "block_type": "text",
            "block_web_url": url,
            "content": ("extracted text", []),
            "metadata": {"origin": "O", "recordName": "N", "recordId": REC1, "mimeType": "M", "orgId": "Org"},
        }]
        answer = f"See [1]({url})."
        _, citations = normalize_citations_and_chunks(answer, docs)
        assert citations[0]["content"] == "extracted text"

    def test_non_string_content_converted(self):
        url = _url(REC1, 0)
        docs = [{
            "virtual_record_id": REC1,
            "block_index": 0,
            "block_type": "text",
            "block_web_url": url,
            "content": 12345,
            "metadata": {"origin": "O", "recordName": "N", "recordId": REC1, "mimeType": "M", "orgId": "Org"},
        }]
        answer = f"See [1]({url})."
        _, citations = normalize_citations_and_chunks(answer, docs)
        assert citations[0]["content"] == "12345"

    def test_block_group_index_url_pattern_matched(self):
        """blockGroupIndex URLs also match the pattern."""
        url = _grp_url(REC1, 2)
        docs = [{
            "virtual_record_id": REC1,
            "block_index": 2,
            "block_type": "text",
            "block_web_url": url,
            "content": "group content",
            "metadata": {"origin": "O", "recordName": "N", "recordId": REC1, "mimeType": "M", "orgId": "Org"},
        }]
        answer = f"See [1]({url})."
        _, citations = normalize_citations_and_chunks(answer, docs)
        assert len(citations) == 1
        assert citations[0]["content"] == "group content"


# ---------------------------------------------------------------------------
# normalize_citations_and_chunks_for_agent — new markdown-link format
# ---------------------------------------------------------------------------
class TestNormalizeCitationsAndChunksForAgent:
    """Tests for normalize_citations_and_chunks_for_agent() with markdown link citations."""

    def test_no_markdown_links_returns_unchanged(self):
        docs = [_make_doc(REC1, 0, "chunk")]
        answer = "No citations here."
        result_text, citations = normalize_citations_and_chunks_for_agent(answer, docs)
        assert result_text == "No citations here."
        assert citations == []

    def test_single_citation_renumbered(self):
        url = _url(REC1, 0)
        docs = [_make_doc(REC1, 0, "agent chunk", block_web_url=url)]
        answer = f"See [1]({url})."
        result_text, citations = normalize_citations_and_chunks_for_agent(answer, docs)
        assert "[1]" in result_text
        assert len(citations) == 1
        assert citations[0]["content"] == "agent chunk"

    def test_metadata_enriched_from_virtual_record_id_to_result(self):
        url = _url(REC1, 0)
        docs = [{
            "virtual_record_id": "vr1",
            "block_index": 0,
            "block_type": "text",
            "block_web_url": url,
            "content": "some data",
            "metadata": {},  # empty
        }]
        vrid_map = {
            "vr1": {
                "origin": "SLACK",
                "record_name": "Channel Message",
                "id": "rec-99",
                "mime_type": "text/plain",
            }
        }
        answer = f"See [1]({url})."
        _, citations = normalize_citations_and_chunks_for_agent(
            answer, docs, virtual_record_id_to_result=vrid_map
        )
        meta = citations[0]["metadata"]
        assert meta["origin"] == "SLACK"
        assert meta["recordName"] == "Channel Message"
        assert meta["recordId"] == "rec-99"
        assert meta["mimeType"] == "text/plain"

    def test_metadata_enrichment_does_not_overwrite_existing_fields(self):
        """Existing metadata fields are preserved; only missing ones are filled."""
        url = _url(REC1, 0)
        docs = [{
            "virtual_record_id": "vr1",
            "block_index": 0,
            "block_type": "text",
            "block_web_url": url,
            "content": "data",
            "metadata": {"origin": "EXISTING_ORIGIN", "recordName": "Existing Name", "recordId": "R", "mimeType": "M", "orgId": "Org"},
        }]
        vrid_map = {
            "vr1": {
                "origin": "SHOULD_NOT_OVERWRITE",
                "record_name": "Also Should Not",
                "id": "R",
                "mime_type": "M",
            }
        }
        answer = f"See [1]({url})."
        _, citations = normalize_citations_and_chunks_for_agent(
            answer, docs, virtual_record_id_to_result=vrid_map
        )
        assert citations[0]["metadata"]["origin"] == "EXISTING_ORIGIN"
        assert citations[0]["metadata"]["recordName"] == "Existing Name"

    def test_missing_metadata_fields_default_to_empty(self):
        url = _url(REC1, 0)
        docs = [{
            "virtual_record_id": REC1,
            "block_index": 0,
            "block_type": "text",
            "block_web_url": url,
            "content": "data",
            "metadata": {},
        }]
        answer = f"See [1]({url})."
        _, citations = normalize_citations_and_chunks_for_agent(answer, docs)
        meta = citations[0]["metadata"]
        assert meta["origin"] == ""
        assert meta["recordName"] == ""
        assert meta["recordId"] == ""
        assert meta["mimeType"] == ""
        assert meta["orgId"] == ""

    def test_image_content_replaced_with_label(self):
        url = _url(REC1, 0)
        docs = [_make_doc(REC1, 0, _VALID_MINIMAL_PNG_DATA_URI, block_web_url=url)]
        answer = f"[1]({url})"
        _, citations = normalize_citations_and_chunks_for_agent(answer, docs)
        assert citations[0]["content"] == "Image"

    def test_table_block_children_flattened(self):
        child_url = _url(REC1, 10)
        child = {
            "virtual_record_id": REC1,
            "block_index": 10,
            "block_web_url": child_url,
            "content": "child row",
            "metadata": {"origin": "O", "recordName": "N", "recordId": REC1, "mimeType": "M", "orgId": "Org"},
        }
        table_doc = {
            "virtual_record_id": REC1,
            "block_index": 9,
            "block_type": GroupType.TABLE.value,
            "block_web_url": None,
            "content": ("summary", [child]),
            "metadata": {},
        }
        answer = f"See [1]({child_url})."
        result_text, citations = normalize_citations_and_chunks_for_agent(answer, [table_doc])
        assert "[1]" in result_text
        assert citations[0]["content"] == "child row"

    def test_record_fallback_via_records_list(self):
        url = _url(REC1, 0)
        record = {
            "id": REC1,
            "block_containers": {
                "blocks": [{"type": BlockType.TEXT.value, "data": "ticket text", "index": 0}]
            },
        }
        docs = [_make_doc("vr1", 0, "chunk", block_web_url="http://no-match")]
        answer = f"See [1]({url})."
        with patch("app.utils.citations.get_enhanced_metadata", return_value={
            "origin": "JIRA", "recordName": "TICKET-1", "recordId": REC1,
            "mimeType": "text/html", "orgId": "org-7",
        }):
            _, citations = normalize_citations_and_chunks_for_agent(
                answer, docs, records=[record]
            )
        assert len(citations) == 1
        assert citations[0]["content"] == "ticket text"

    def test_record_fallback_via_virtual_record_id_to_result(self):
        """URL not in docs or records, but found via virtual_record_id_to_result."""
        url = _url(REC1, 0)
        vrid_map = {
            "vr1": {
                "id": REC1,
                "block_containers": {
                    "blocks": [{"type": BlockType.TEXT.value, "data": "vrid text", "index": 0}]
                },
            }
        }
        docs = []
        answer = f"See [1]({url})."
        with patch("app.utils.citations.get_enhanced_metadata", return_value={
            "origin": "O", "recordName": "N", "recordId": REC1, "mimeType": "M", "orgId": "Org",
        }):
            _, citations = normalize_citations_and_chunks_for_agent(
                answer, docs, virtual_record_id_to_result=vrid_map
            )
        assert len(citations) == 1
        assert citations[0]["content"] == "vrid text"

    def test_record_fallback_table_row_block(self):
        url = _url(REC1, 0)
        record = {
            "id": REC1,
            "block_containers": {
                "blocks": [{
                    "type": BlockType.TABLE_ROW.value,
                    "data": {"row_natural_language_text": "Agent row data"},
                    "index": 0,
                }]
            },
        }
        docs = [_make_doc("vr1", 0, "chunk", block_web_url="http://no-match")]
        answer = f"See [1]({url})."
        with patch("app.utils.citations.get_enhanced_metadata", return_value={
            "origin": "O", "recordName": "N", "recordId": REC1, "mimeType": "M", "orgId": "Org",
        }):
            _, citations = normalize_citations_and_chunks_for_agent(
                answer, docs, records=[record]
            )
        assert citations[0]["content"] == "Agent row data"

    def test_record_fallback_image_block(self):
        url = _url(REC1, 0)
        record = {
            "id": REC1,
            "block_containers": {
                "blocks": [{
                    "type": BlockType.IMAGE.value,
                    "data": {"uri": _VALID_MINIMAL_PNG_DATA_URI},
                    "index": 0,
                }]
            },
        }
        docs = [_make_doc("vr1", 0, "chunk", block_web_url="http://no-match")]
        answer = f"See [1]({url})."
        with patch("app.utils.citations.get_enhanced_metadata", return_value={
            "origin": "O", "recordName": "N", "recordId": REC1, "mimeType": "M", "orgId": "Org",
        }):
            _, citations = normalize_citations_and_chunks_for_agent(
                answer, docs, records=[record]
            )
        assert citations[0]["content"] == "Image"

    def test_record_fallback_block_index_out_of_range(self):
        url = _url(REC1, 99)
        record = {
            "id": REC1,
            "block_containers": {"blocks": [{"type": "text", "data": "only block", "index": 0}]},
        }
        docs = []
        answer = f"See [1]({url})."
        _, citations = normalize_citations_and_chunks_for_agent(
            answer, docs, records=[record]
        )
        assert len(citations) == 0

    def test_url_not_matched_anywhere_skipped(self):
        url = _url(REC1, 0)
        docs = []
        answer = f"See [1]({url})."
        _, citations = normalize_citations_and_chunks_for_agent(answer, docs)
        assert len(citations) == 0

    def test_tuple_content_unpacked(self):
        url = _url(REC1, 0)
        docs = [{
            "virtual_record_id": REC1,
            "block_index": 0,
            "block_type": "text",
            "block_web_url": url,
            "content": ("text from tuple", []),
            "metadata": {"origin": "O", "recordName": "N", "recordId": REC1, "mimeType": "M", "orgId": "Org"},
        }]
        answer = f"See [1]({url})."
        _, citations = normalize_citations_and_chunks_for_agent(answer, docs)
        assert citations[0]["content"] == "text from tuple"

    def test_none_virtual_record_id_to_result_defaults_to_empty(self):
        url = _url(REC1, 0)
        docs = [_make_doc(REC1, 0, "content", block_web_url=url)]
        answer = f"See [1]({url})."
        _, citations = normalize_citations_and_chunks_for_agent(
            answer, docs, virtual_record_id_to_result=None
        )
        assert len(citations) == 1

    def test_none_records_defaults_to_empty(self):
        url = _url(REC1, 0)
        docs = [_make_doc(REC1, 0, "content", block_web_url=url)]
        answer = f"See [1]({url})."
        _, citations = normalize_citations_and_chunks_for_agent(
            answer, docs, records=None
        )
        assert len(citations) == 1


# ---------------------------------------------------------------------------
# Added tests to raise coverage
# ---------------------------------------------------------------------------

from app.utils.citations import (  # noqa: E402  # pylint: disable=wrong-import-position
    _expand_multi_ref_links,
    _find_record_by_id,
    _normalize_bracket_refs,
    _resolve_ref,
    _safe_stringify_content,
    _wrap_bare_refs,
    normalize_malformed_citations,
)


# ---------------------------------------------------------------------------
# _normalize_bracket_refs
# ---------------------------------------------------------------------------


class TestNormalizeBracketRefs:
    """Unit tests for _normalize_bracket_refs (bracketed bare refN tokens)."""

    def test_bracket_ref_becomes_markdown_link(self):
        assert _normalize_bracket_refs("See [ref3] for detail.") == "See [source](ref3) for detail."

    def test_multiple_bracket_refs(self):
        out = _normalize_bracket_refs("[ref1] and [ref2]")
        assert out == "[source](ref1) and [source](ref2)"

    def test_existing_markdown_link_untouched(self):
        text = "Use [source](ref5) here."
        assert _normalize_bracket_refs(text) == text

    def test_https_link_inside_md_link_untouched(self):
        text = "Link [label](https://example.com/a) end."
        assert _normalize_bracket_refs(text) == text

    def test_normalize_malformed_avoids_double_brackets(self):
        """[refN] must become [source](refN) without _wrap_bare_refs adding an extra leading [."""
        out = normalize_malformed_citations("Answer per [ref7].")
        assert "[source](ref7)" in out
        assert "[[source]" not in out


class TestResolveRef:
    def test_ref_resolved_via_mapping(self):
        """Tiny ref present in mapping returns the full URL."""
        mapping = {"ref1": "http://full/url#blockIndex=0"}
        assert _resolve_ref("ref1", mapping) == "http://full/url#blockIndex=0"

    def test_tiny_web_url_maps_through_inner_ref(self):
        assert _resolve_ref("https://ref1.xyz/", {"ref1": "http://canonical/page"}) == (
            "http://canonical/page"
        )

    def test_ref_not_in_mapping_returns_target(self):
        assert _resolve_ref("ref99", {"ref1": "x"}) == "ref99"

    def test_none_mapping_returns_target(self):
        assert _resolve_ref("ref1", None) == "ref1"

    def test_empty_dict_mapping_returns_target_unresolved(self):
        """Empty ``ref_to_url`` is falsy — same early return as ``None``."""
        assert _resolve_ref("ref1", {}) == "ref1"


class TestTinyRefHelpers:
    def test_extract_tiny_ref_empty(self):
        assert extract_tiny_ref("") is None

    def test_extract_tiny_ref_non_match(self):
        assert extract_tiny_ref("http://regular.example") is None

    def test_extract_tiny_ref_strips_https_form(self):
        assert extract_tiny_ref("http://ref9.xyz") == "ref9"

    def test_build_tiny_web_ref_url(self):
        assert build_tiny_web_ref_url("ref2") == "https://ref2.xyz"


class TestStripUnresolvedRefLinks:
    """Unresolved refN links must never reach the frontend or the stored answer:
    the browser renders them as broken relative links (<base_url>/chat/refN) and
    stored raw refs get replayed as history and copied by the LLM forever."""

    def test_generic_label_removed_entirely(self):
        from app.utils.citations import strip_unresolved_ref_links
        t = "The bug bash found 5 issues [source](ref162)."
        assert strip_unresolved_ref_links(t) == "The bug bash found 5 issues."

    def test_descriptive_label_kept_as_plain_text(self):
        from app.utils.citations import strip_unresolved_ref_links
        t = "See [Daily Bug Bash Notes](ref158) for details."
        assert strip_unresolved_ref_links(t) == "See Daily Bug Bash Notes for details."

    def test_numeric_label_removed(self):
        from app.utils.citations import strip_unresolved_ref_links
        assert strip_unresolved_ref_links("Fact [3](ref9).") == "Fact."

    def test_tiny_web_ref_url_target_stripped(self):
        from app.utils.citations import strip_unresolved_ref_links
        t = "Result [source](https://ref12.xyz) end."
        assert strip_unresolved_ref_links(t) == "Result end."

    def test_resolved_full_url_links_untouched(self):
        from app.utils.citations import strip_unresolved_ref_links
        t = "Fact [3](http://localhost:3000/record/abc/preview#blockIndex=5)."
        assert strip_unresolved_ref_links(t) == t

    def test_external_web_links_untouched(self):
        from app.utils.citations import strip_unresolved_ref_links
        t = "See [docs](https://example.com/guide) here."
        assert strip_unresolved_ref_links(t) == t

    def test_multiple_adjacent_unresolved_refs(self):
        from app.utils.citations import strip_unresolved_ref_links
        t = "5 issues total [source](ref158)[source](ref159)."
        assert strip_unresolved_ref_links(t) == "5 issues total."

    def test_empty_text_passthrough(self):
        from app.utils.citations import strip_unresolved_ref_links
        assert strip_unresolved_ref_links("") == ""

    def test_normalize_pipeline_strips_unmatched_refs(self):
        """End-to-end: a ref missing from ref_to_url is stripped by
        normalize_citations_and_chunks instead of surviving as a broken link."""
        text = "Known fact [source](ref1). Stale fact [source](ref162)."
        ref_to_url = {"ref1": "http://localhost:3000/record/rec-1/preview#blockIndex=0"}
        records = [{
            "id": "rec-1",
            "block_containers": {"blocks": [{"index": 0, "type": "text", "data": "hello"}]},
        }]
        normalized, citations = normalize_citations_and_chunks(
            text, [], records, ref_to_url=ref_to_url,
        )
        assert "ref162" not in normalized
        assert "/chat/ref" not in normalized
        assert len(citations) == 1


class TestDisplayUrlForLlm:
    def test_empty_url(self):
        mapper = MagicMock()
        assert display_url_for_llm("", mapper) == ""

    def test_none_mapper_returns_verbatim(self):
        u = "https://corp.example/long/path?q=1"
        assert display_url_for_llm(u, None) is u

    def test_mapper_replaces_with_tiny_web_ref_url(self):
        mapper = MagicMock()
        mapper.get_or_create_ref.return_value = "ref55"
        u = "https://docs.example/guide#section"
        assert display_url_for_llm(u, mapper) == "https://ref55.xyz"
        mapper.get_or_create_ref.assert_called_once_with(u)


class TestCleanDuplicateCitationLinks:
    def test_trailing_paren_same_as_link_target_removed(self):
        t = "[source](https://example.com/article) (https://example.com/article)"
        assert _clean_duplicate_citation_links(t) == "[source](https://example.com/article)"

    def test_trailing_https_ref_xyz_always_stripped_keep_first_link_only(self):
        t = "[s](https://example.com/long) (https://ref901.xyz)"
        assert _clean_duplicate_citation_links(t) == "[s](https://example.com/long)"

    def test_kept_when_paren_target_differs_from_resolved_link(self):
        t = "[s](https://example.com/a) (https://other.com/b)"
        assert _clean_duplicate_citation_links(t) == t

    def test_ref_mapped_same_as_paren_url(self):
        full = "https://resolved-target.example/path"
        t = "[q](ref1) (https://resolved-target.example/path)"
        assert _clean_duplicate_citation_links(t, {"ref1": full}) == "[q](ref1)"

    def test_consecutive_md_links_same_url_collapsed(self):
        u = "https://ref707.xyz"
        combo = f"[Site title]({u}) [more]({u})"
        assert _clean_duplicate_citation_links(combo) == f"[Site title]({u})"

    def test_consecutive_without_space_between(self):
        u = "https://dup.example/page"
        combo = f"[A]({u})[cite]({u})"
        assert _clean_duplicate_citation_links(combo) == f"[A]({u})"

    def test_consecutive_different_urls_kept(self):
        t = "[A](https://one.example/a) [B](https://two.example/b)"
        assert _clean_duplicate_citation_links(t) == t


class TestRenumberCitationLinksMarkdownPattern:
    def test_blank_link_label_still_renumbered(self):
        """Empty link text still matches ``_MD_LINK_PATTERN`` and renumbers like any citation."""
        page = "https://edge.example/about"
        text = f"Odd []({page})."
        matches = list(re.finditer(citations_mod._MD_LINK_PATTERN, text))
        out = _renumber_citation_links(text, matches, {page: 9})
        assert "[9]" in out

    def test_descriptive_link_text_replaced_with_numeric_badge(self):
        """``_renumber_citation_links`` always emits ``[N](full_url)`` (discards anchor text)."""
        page = "https://readable.example/guide"
        text = f"Details in [Installation guide]({page}) please."
        matches = list(re.finditer(citations_mod._MD_LINK_PATTERN, text))
        out = _renumber_citation_links(text, matches, {page: 4})
        assert out == f"Details in [4]({page}) please."
        assert "[Installation guide]" not in out


class TestChatDocCitation:
    def test_dataclass_roundtrip_fields(self):
        c = ChatDocCitation(content="body", metadata={"m": True}, chunkindex=2)
        assert c.content == "body" and c.metadata == {"m": True} and c.chunkindex == 2


class TestFindWebRecordByUrl:
    def test_empty_url_returns_none_without_iterating(self):
        assert _find_web_record_by_url("", [{"url": "x"}]) is None


class TestDetectHallucinatedExtras:
    def test_duplicate_tiny_refs_count_once(self):
        r = detect_hallucinated_citation_urls("[1](ref5) duplicate [z](ref5)")
        assert r == ["ref5"]


class TestWebRecordsCitationsPath:
    """Cover web search citation assembly in normalize_citations_*"""

    WEB = "https://search.example/snippet?q=1#page"

    def test_normalize_chat_builds_web_citation(self):
        answer = f"Found [article]({self.WEB})."
        rows = [{"url": self.WEB, "content": "HTML snippet ok", "org_id": "o-9"}]
        _, cites = normalize_citations_and_chunks(
            answer,
            [],
            records=[],
            web_records=rows,
        )
        assert len(cites) == 1
        assert cites[0]["citationType"] == "web|url"
        assert cites[0]["content"] == "HTML snippet ok"
        assert cites[0]["metadata"]["connector"] == "WEB"

    def test_empty_web_record_content_skips(self):
        answer = f"See [site]({self.WEB})."
        rows = [{"url": self.WEB, "content": "", "org_id": ""}]
        _, cites = normalize_citations_and_chunks(
            answer, [], records=[], web_records=rows
        )
        assert cites == []

    def test_normalize_agent_web_records(self):
        answer = f"Web [cite]({self.WEB})."
        rows = [{"url": self.WEB, "content": "agent web text", "org_id": ""}]
        _, cites = normalize_citations_and_chunks_for_agent(
            answer, [], records=[], web_records=rows
        )
        assert len(cites) == 1 and cites[0]["content"] == "agent web text"

    def test_normalize_agent_skips_when_web_record_content_empty(self):
        answer = f"Web [cite]({self.WEB})."
        rows = [{"url": self.WEB, "content": "", "org_id": ""}]
        _, cites = normalize_citations_and_chunks_for_agent(
            answer, [], records=[], web_records=rows,
        )
        assert cites == []

    def test_web_records_list_but_no_hit_falls_through_to_vectordb(self):
        url = _url(REC1, 0)
        docs = [_make_doc(REC1, 0, "from vector", block_web_url=url)]
        answer = f"Use [doc]({url})."
        decoy = [{"url": "http://different", "content": "noise", "org_id": ""}]
        _, cites = normalize_citations_and_chunks(
            answer, docs, web_records=decoy
        )
        assert len(cites) == 1 and cites[0]["content"] == "from vector"

    def test_normalize_agent_web_records_list_no_hit_falls_through(self):
        """Non-matching ``web_records`` rows must not skip vector hits (agent pipeline)."""
        url = _url(REC1, 0)
        docs = [_make_doc(REC1, 0, "from vector agent", block_web_url=url)]
        answer = f"Use [doc]({url})."
        decoy = [{"url": "http://different", "content": "noise", "org_id": ""}]
        _, cites = normalize_citations_and_chunks_for_agent(
            answer, docs, web_records=decoy
        )
        assert len(cites) == 1 and cites[0]["content"] == "from vector agent"


class TestFlattenListGroupDocs:
    def test_list_child_url_indexed_like_table(self):
        child_url = _url(REC1, 41)
        list_doc = {
            "virtual_record_id": REC1,
            "block_index": 40,
            "block_type": GroupType.LIST.value,
            "block_web_url": None,
            "content": (
                "",
                [
                    {
                        "block_web_url": child_url,
                        "content": "item one",
                        "metadata": {"origin": "O", "recordName": "N", "recordId": REC1, "mimeType": "M", "orgId": "Org"},
                    }
                ],
            ),
            "metadata": {},
        }
        answer = f"Bullet [1]({child_url})."
        _, cites = normalize_citations_and_chunks(answer, [list_doc])
        assert len(cites) == 1 and cites[0]["content"] == "item one"


class TestFindRecordByIdDirect:
    """Direct coverage for ``_find_record_by_id`` virtual-map vs records ordering."""

    def test_virtual_map_without_match_falls_back_to_records(self):
        rec = {"id": REC1, "record_name": "FromList"}
        assert _find_record_by_id(
            REC1,
            [rec],
            {"vr": {"id": "other-record"}},
        ) is rec

    def test_empty_virtual_map_uses_records(self):
        rec = {"id": REC1}
        assert _find_record_by_id(REC1, [rec], {}) is rec


class TestFlattenDocEdgeCases:
    """Table / plain docs that omit ``block_web_url`` do not populate the URL index."""

    def test_table_child_without_block_web_url_skips_indexing(self):
        table_doc = {
            "virtual_record_id": REC1,
            "block_index": 4,
            "block_type": GroupType.TABLE.value,
            "block_web_url": None,
            "content": (
                "summary",
                [{
                    "block_index": 5,
                    "block_web_url": None,
                    "content": "not in index",
                    "metadata": {},
                }],
            ),
            "metadata": {},
        }
        answer = f"See [1]({_url(REC1, 0)})."
        _, cites = normalize_citations_and_chunks(answer, [table_doc], records=[])
        assert cites == []

    def test_plain_doc_without_block_web_url_not_indexed(self):
        doc = {
            "virtual_record_id": REC1,
            "block_index": 0,
            "block_type": "text",
            "block_web_url": None,
            "content": "body",
            "metadata": {},
        }
        answer = f"See [1]({_url(REC1, 0)})."
        _, cites = normalize_citations_and_chunks(answer, [doc], records=[])
        assert cites == []

    def test_agent_table_child_without_block_web_url(self):
        table_doc = {
            "virtual_record_id": REC1,
            "block_index": 4,
            "block_type": GroupType.TABLE.value,
            "block_web_url": None,
            "content": (
                "summary",
                [{
                    "block_index": 5,
                    "block_web_url": None,
                    "content": "x",
                    "metadata": {},
                }],
            ),
            "metadata": {},
        }
        answer = f"See [1]({_url(REC1, 0)})."
        _, cites = normalize_citations_and_chunks_for_agent(answer, [table_doc], records=[])
        assert cites == []

    def test_agent_plain_doc_without_block_web_url(self):
        doc = {
            "virtual_record_id": REC1,
            "block_index": 0,
            "block_type": "text",
            "block_web_url": None,
            "content": "body",
            "metadata": {},
        }
        answer = f"See [1]({_url(REC1, 0)})."
        _, cites = normalize_citations_and_chunks_for_agent(answer, [doc], records=[])
        assert cites == []


class TestAgentVirtualMetadataWhenUnlinked:
    """``virtual_record_id_to_result`` present but doc not linked — skip per-field merge."""

    def test_enrichment_skips_when_doc_has_no_virtual_link_to_map(self):
        url = _url(REC1, 0)
        docs = [{
            "virtual_record_id": None,
            "block_index": 0,
            "block_type": "text",
            "block_web_url": url,
            "content": "chunk text",
            "metadata": {},
        }]
        vmap = {
            "vr-only": {
                "origin": "SHOULD_NOT_APPLY",
                "record_name": "Nope",
                "id": "x",
                "mime_type": "text/plain",
            }
        }
        _, cites = normalize_citations_and_chunks_for_agent(
            f"[1]({url})", docs, virtual_record_id_to_result=vmap
        )
        assert cites[0]["metadata"]["origin"] == ""
        assert cites[0]["metadata"]["recordName"] == ""


class TestBlockFallbackVirtualWhenRecordOob:
    """Record matches id but ``block_index`` out of range; virtual map still resolves."""

    def test_chat_uses_virtual_when_record_block_index_oob(self):
        url = _url(REC1, 1)
        record_shallow = {
            "id": REC1,
            "block_containers": {
                "blocks": [
                    {"type": BlockType.TEXT.value, "data": "only idx0", "index": 0},
                ]
            },
        }
        vrid_map = {
            "vr1": {
                "id": REC1,
                "block_containers": {
                    "blocks": [
                        {"type": BlockType.TEXT.value, "data": "a", "index": 0},
                        {"type": BlockType.TEXT.value, "data": "from vrid", "index": 1},
                    ]
                },
            },
        }
        answer = f"See [1]({url})."
        with patch("app.utils.citations.get_enhanced_metadata", return_value={
            "origin": "O", "recordName": "N", "recordId": REC1, "mimeType": "M", "orgId": "Org",
        }):
            _, cites = normalize_citations_and_chunks(
                answer, [], records=[record_shallow],
                virtual_record_id_to_result=vrid_map,
            )
        assert len(cites) == 1
        assert cites[0]["content"] == "from vrid"

    def test_agent_uses_virtual_when_record_block_index_oob(self):
        url = _url(REC1, 1)
        record_shallow = {
            "id": REC1,
            "block_containers": {
                "blocks": [
                    {"type": BlockType.TEXT.value, "data": "block zero only", "index": 0},
                ]
            },
        }
        vrid_map = {
            "vr1": {
                "id": REC1,
                "block_containers": {
                    "blocks": [
                        {"type": BlockType.TEXT.value, "data": "wrong", "index": 0},
                        {"type": BlockType.TEXT.value, "data": "from virtual map", "index": 1},
                    ]
                },
            },
        }
        answer = f"See [1]({url})."
        with patch("app.utils.citations.get_enhanced_metadata", return_value={
            "origin": "O", "recordName": "N", "recordId": REC1, "mimeType": "M", "orgId": "Org",
        }):
            _, cites = normalize_citations_and_chunks_for_agent(
                answer,
                [],
                records=[record_shallow],
                virtual_record_id_to_result=vrid_map,
            )
        assert len(cites) == 1
        assert cites[0]["content"] == "from virtual map"


class TestAppendCitationEmptyDataBranches:
    def test_chat_record_block_without_data_logged(self):
        url = _url(REC1, 0)
        record = {
            "id": REC1,
            "block_containers": {"blocks": [
                {"type": BlockType.TEXT.value, "data": None, "index": 0},
            ]},
        }
        answer = f"See [src]({url})."
        with patch("app.utils.citations.logger") as log:
            _, cites = normalize_citations_and_chunks(answer, [], records=[record])
        assert cites == []
        log.warning.assert_called()

    def test_agent_virtual_block_data_none_logs(self):
        url = _url(REC1, 0)
        vrid = {
            "vr1": {
                "id": REC1,
                "block_containers": {"blocks": [
                    {"type": BlockType.TEXT.value, "data": None, "index": 0},
                ]},
            }
        }
        answer = f"See [agent]({url})."
        with patch("app.utils.citations.logger") as log:
            _, cites = normalize_citations_and_chunks_for_agent(
                answer, [], records=[], virtual_record_id_to_result=vrid,
            )
        assert cites == []
        log.warning.assert_called()


class TestAgentMetadataMergeFromVirtualMap:
    def test_virtual_record_id_only_in_metadata_payload(self):
        url = _url(REC1, 0)
        docs = [{
            "virtual_record_id": None,
            "block_index": 0,
            "block_type": "text",
            "block_web_url": url,
            "content": "chunk",
            "metadata": {"virtualRecordId": "vr-meta"},
        }]
        vmap = {"vr-meta": {"origin": "CONFL", "record_name": "Rn", "id": "rid-1", "mime_type": "text/plain"}}
        _, cites = normalize_citations_and_chunks_for_agent(
            f"[1]({url})", docs, virtual_record_id_to_result=vmap,
        )
        meta = cites[0]["metadata"]
        assert meta["origin"] == "CONFL" and meta["recordName"] == "Rn"


class TestExpandMultiRefLinksEdge:
    def test_replacer_returns_original_when_split_yields_single(self):
        class _DummyMatch:
            def group(self, n: int) -> str:
                if n == 0:
                    return "[lbl](solo_only)"
                if n == 1:
                    return "lbl"
                if n == 2:
                    return "solo_only"
                raise AssertionError(n)

        mock_pat = MagicMock()

        def fake_sub(fn, _text):
            return fn(_DummyMatch())

        mock_pat.sub = fake_sub
        with patch.object(citations_mod, "_MULTI_REF_IN_LINK_RE", mock_pat):
            assert citations_mod._expand_multi_ref_links("ignore") == "[lbl](solo_only)"

    def test_wrap_bare_refs_unknown_match_returns_group_zero(self):
        class _BareMatch:
            def group(self, n: int) -> object:
                if n == 0:
                    return "keepme"
                return None

        mock_pat = MagicMock()

        def fake_sub(fn, _text):
            return fn(_BareMatch())

        mock_pat.sub = fake_sub
        with patch.object(citations_mod, "_BARE_CITATION_NORMALIZE_RE", mock_pat):
            assert citations_mod._wrap_bare_refs("x") == "keepme"


class TestFindRecordByVirtualMapWithNoneEntries:
    def test_skips_none_values_in_virtual_map(self):
        """``_find_record_by_id`` must ignore ``None`` map entries."""

        landing = f"{BASE}/record/{REC1}"
        real_rec = {
            "id": REC1,
            "record_name": "SkipNoneWorks",
            "block_containers": {"blocks": []},
        }
        vmap = {"nulled_entry": None, "real_record": real_rec}
        _, cites = normalize_citations_and_chunks(
            f"See [h]({landing}).",
            [],
            records=[],
            virtual_record_id_to_result=vmap,
        )
        assert len(cites) == 1
        assert "SkipNoneWorks" in cites[0]["content"]


class TestSafeStringifyContent:
    def test_int_value(self):
        assert _safe_stringify_content(123) == "123"

    def test_none_value(self):
        assert _safe_stringify_content(None) == "None"

    def test_exception_returns_empty_string(self):
        """Cover lines 118-120 — objects whose __str__ raises are handled gracefully."""

        class BadStr:
            def __str__(self):
                raise RuntimeError("boom")

        assert _safe_stringify_content(BadStr()) == ""


class TestDetectHallucinatedTinyRefs:
    """Cover lines 204-206 — tiny ref detection with ref_to_url map."""

    def test_tiny_ref_resolved_via_mapping(self):
        url = _url(REC1, 0)
        text = "See [1](ref1)."
        doc = {"metadata": {"recordId": REC1}, "block_index": 0}
        result = detect_hallucinated_citation_urls(
            text,
            flattened_final_results=[doc],
            ref_to_url={"ref1": url},
        )
        # ref1 is in ref_to_url, so it is NOT hallucinated.
        assert result == []

    def test_tiny_ref_missing_from_mapping_is_hallucinated(self):
        text = "See [1](ref42)."
        result = detect_hallucinated_citation_urls(
            text, ref_to_url={"ref1": "something"}
        )
        assert "ref42" in result

    def test_tiny_ref_without_mapping_is_hallucinated(self):
        text = "See [1](ref1)."
        result = detect_hallucinated_citation_urls(text)
        assert "ref1" in result


class TestNormalizeCitationsExtra:
    """Additional tests raising branch coverage in normalize_citations_and_chunks."""

    def test_url_mapped_to_doc_with_empty_content_skipped(self):
        """When stringified content is falsy the citation is skipped, but the
        unresolved markdown link is left in the text unchanged."""
        url = _url(REC1, 0)
        docs = [{
            "virtual_record_id": REC1,
            "block_index": 0,
            "block_type": "text",
            "block_web_url": url,
            "content": "",
            "metadata": {},
        }]
        answer = f"See [1]({url})."
        result_text, citations = normalize_citations_and_chunks(answer, docs)
        assert citations == []
        # Unresolved link stays as-is in the text
        assert f"[1]({url})" in result_text

    def test_record_fallback_table_row_with_empty_text_skipped(self):
        """Cover line 327 — TABLE_ROW block whose text is empty is skipped."""
        url = _url(REC1, 0)
        record = {
            "id": REC1,
            "block_containers": {
                "blocks": [{
                    "type": BlockType.TABLE_ROW.value,
                    "data": {"row_natural_language_text": ""},
                    "index": 0,
                }]
            },
        }
        answer = f"See [1]({url})."
        with patch("app.utils.citations.get_enhanced_metadata", return_value={}):
            _, citations = normalize_citations_and_chunks(answer, [], records=[record])
        assert citations == []

    def test_record_fallback_image_empty_uri_skipped(self):
        """Cover line 327 — IMAGE block without uri is skipped."""
        url = _url(REC1, 0)
        record = {
            "id": REC1,
            "block_containers": {
                "blocks": [{
                    "type": BlockType.IMAGE.value,
                    "data": {"uri": ""},
                    "index": 0,
                }]
            },
        }
        answer = f"See [1]({url})."
        with patch("app.utils.citations.get_enhanced_metadata", return_value={}):
            _, citations = normalize_citations_and_chunks(answer, [], records=[record])
        assert citations == []

    def test_tiny_ref_resolved_via_mapping(self):
        """Tiny ref resolves to a full URL that maps to a doc."""
        url = _url(REC1, 0)
        docs = [_make_doc(REC1, 0, "hello", block_web_url=url)]
        answer = "Fact [1](ref1)."
        result_text, citations = normalize_citations_and_chunks(
            answer, docs, ref_to_url={"ref1": url}
        )
        # After renumbering, the rewritten markdown link should contain the full URL.
        assert url in result_text
        assert len(citations) == 1
        assert citations[0]["content"] == "hello"


class TestNormalizeAgentCitationsExtra:
    """Additional tests raising branch coverage in the agent variant."""

    def test_tuple_content_with_non_string_still_handled(self):
        """Cover line 452 — non-string, non-tuple content goes through _safe_stringify_content."""
        url = _url(REC1, 0)
        docs = [{
            "virtual_record_id": REC1,
            "block_index": 0,
            "block_type": "text",
            "block_web_url": url,
            "content": 98765,  # int → stringified
            "metadata": {"origin": "O", "recordName": "N", "recordId": REC1, "mimeType": "M", "orgId": "Org"},
        }]
        answer = f"See [1]({url})."
        _, citations = normalize_citations_and_chunks_for_agent(answer, docs)
        assert citations[0]["content"] == "98765"

    def test_empty_content_in_matched_doc_skipped(self):
        """Cover line 455 — doc with empty content is skipped."""
        url = _url(REC1, 0)
        docs = [{
            "virtual_record_id": REC1,
            "block_index": 0,
            "block_type": "text",
            "block_web_url": url,
            "content": "",
            "metadata": {},
        }]
        answer = f"See [1]({url})."
        _, citations = normalize_citations_and_chunks_for_agent(answer, docs)
        assert citations == []

    def test_records_fallback_table_row_empty_text_skipped(self):
        """Cover line 489 — TABLE_ROW record fallback with empty text is skipped."""
        url = _url(REC1, 0)
        record = {
            "id": REC1,
            "block_containers": {
                "blocks": [{
                    "type": BlockType.TABLE_ROW.value,
                    "data": {"row_natural_language_text": ""},
                    "index": 0,
                }]
            },
        }
        answer = f"See [1]({url})."
        with patch("app.utils.citations.get_enhanced_metadata", return_value={}):
            _, citations = normalize_citations_and_chunks_for_agent(
                answer, [], records=[record]
            )
        assert citations == []

    def test_records_fallback_image_empty_uri_skipped(self):
        """Cover line 489 — IMAGE record fallback with empty uri is skipped."""
        url = _url(REC1, 0)
        record = {
            "id": REC1,
            "block_containers": {
                "blocks": [{
                    "type": BlockType.IMAGE.value,
                    "data": {"uri": ""},
                    "index": 0,
                }]
            },
        }
        answer = f"See [1]({url})."
        with patch("app.utils.citations.get_enhanced_metadata", return_value={}):
            _, citations = normalize_citations_and_chunks_for_agent(
                answer, [], records=[record]
            )
        assert citations == []

    def test_virtual_record_id_fallback_table_row_empty_text_skipped(self):
        """Cover lines 520/522 — virtual_record_id fallback for TABLE_ROW with empty text."""
        url = _url(REC1, 0)
        vrid_map = {
            "vr1": {
                "id": REC1,
                "block_containers": {
                    "blocks": [{
                        "type": BlockType.TABLE_ROW.value,
                        "data": {"row_natural_language_text": ""},
                        "index": 0,
                    }]
                },
            }
        }
        answer = f"See [1]({url})."
        with patch("app.utils.citations.get_enhanced_metadata", return_value={}):
            _, citations = normalize_citations_and_chunks_for_agent(
                answer, [], virtual_record_id_to_result=vrid_map
            )
        assert citations == []

    def test_virtual_record_id_fallback_image_empty_uri_skipped(self):
        """Cover line 524 — virtual_record_id fallback for IMAGE with empty uri."""
        url = _url(REC1, 0)
        vrid_map = {
            "vr1": {
                "id": REC1,
                "block_containers": {
                    "blocks": [{
                        "type": BlockType.IMAGE.value,
                        "data": {"uri": ""},
                        "index": 0,
                    }]
                },
            }
        }
        answer = f"See [1]({url})."
        with patch("app.utils.citations.get_enhanced_metadata", return_value={}):
            _, citations = normalize_citations_and_chunks_for_agent(
                answer, [], virtual_record_id_to_result=vrid_map
            )
        assert citations == []

    def test_virtual_record_id_fallback_non_image_type_resolves(self):
        """Cover line 527 — virtual_record_id fallback succeeds for plain text blocks."""
        url = _url(REC1, 0)
        vrid_map = {
            "vr1": {
                "id": REC1,
                "block_containers": {
                    "blocks": [{
                        "type": BlockType.TEXT.value,
                        "data": "real text content",
                        "index": 0,
                    }]
                },
            }
        }
        answer = f"See [1]({url})."
        with patch("app.utils.citations.get_enhanced_metadata", return_value={}):
            _, citations = normalize_citations_and_chunks_for_agent(
                answer, [], virtual_record_id_to_result=vrid_map
            )
        assert len(citations) == 1
        assert citations[0]["content"] == "real text content"

    def test_tiny_ref_path_with_ref_to_url(self):
        """The agent variant should also resolve tiny refs."""
        url = _url(REC1, 0)
        docs = [_make_doc(REC1, 0, "agent content", block_web_url=url)]
        answer = "Fact [1](ref7)."
        result_text, citations = normalize_citations_and_chunks_for_agent(
            answer, docs, ref_to_url={"ref7": url}
        )
        assert url in result_text
        assert len(citations) == 1
        assert citations[0]["content"] == "agent content"


# ---------------------------------------------------------------------------
# normalize_malformed_citations — pre-processing step
# ---------------------------------------------------------------------------

class TestExpandMultiRefLinks:
    """Unit tests for _expand_multi_ref_links()."""

    def test_comma_separated_refs_split(self):
        text = "[source](ref632, ref633)"
        result = _expand_multi_ref_links(text)
        assert result == "[source](ref632) [source](ref633)"

    def test_three_comma_separated_refs(self):
        text = "[source](ref1, ref2, ref3)"
        result = _expand_multi_ref_links(text)
        assert result == "[source](ref1) [source](ref2) [source](ref3)"

    def test_space_separated_refs_split(self):
        text = "[source](ref5 ref6)"
        result = _expand_multi_ref_links(text)
        assert result == "[source](ref5) [source](ref6)"

    def test_mixed_ref_and_url_split(self):
        text = "[source](ref1, https://example.com/page)"
        result = _expand_multi_ref_links(text)
        assert result == "[source](ref1) [source](https://example.com/page)"

    def test_two_tiny_urls_split(self):
        text = "[source](https://ref1.xyz, https://ref2.xyz)"
        result = _expand_multi_ref_links(text)
        assert result == "[source](https://ref1.xyz) [source](https://ref2.xyz)"

    def test_single_ref_unchanged(self):
        text = "[source](ref1)"
        assert _expand_multi_ref_links(text) == text

    def test_no_refs_unchanged(self):
        text = "Plain text with no links."
        assert _expand_multi_ref_links(text) == text

    def test_multiple_multi_ref_links_in_text(self):
        text = "See [a](ref1, ref2) and [b](ref3, ref4)."
        result = _expand_multi_ref_links(text)
        assert "[a](ref1)" in result
        assert "[a](ref2)" in result
        assert "[b](ref3)" in result
        assert "[b](ref4)" in result

    def test_preserves_link_text(self):
        text = "[My Link Text](ref10, ref11)"
        result = _expand_multi_ref_links(text)
        assert result == "[My Link Text](ref10) [My Link Text](ref11)"


class TestWrapBareRefs:
    """Unit tests for _wrap_bare_refs()."""

    def test_bare_ref_wrapped(self):
        result = _wrap_bare_refs("See ref5 for details.")
        assert "[source](ref5)" in result

    def test_bare_tiny_url_wrapped(self):
        result = _wrap_bare_refs("See https://ref5.xyz for details.")
        assert "[source](https://ref5.xyz)" in result

    def test_existing_markdown_link_untouched(self):
        text = "See [source](ref5) here."
        result = _wrap_bare_refs(text)
        assert result == text

    def test_existing_tiny_url_link_untouched(self):
        text = "[source](https://ref5.xyz)"
        result = _wrap_bare_refs(text)
        assert result == text

    def test_ref_in_word_not_matched(self):
        # "reference5" should not be matched because 'e' follows 'ref' not a digit
        result = _wrap_bare_refs("See reference5 here.")
        assert "[ref" not in result

    def test_multiple_bare_refs(self):
        result = _wrap_bare_refs("Data from ref1 and ref2.")
        assert "[source](ref1)" in result
        assert "[source](ref2)" in result

    def test_bare_ref_at_end_of_sentence(self):
        result = _wrap_bare_refs("Supported by ref7.")
        assert "[source](ref7)" in result

    def test_bare_ref_in_parentheses(self):
        result = _wrap_bare_refs("(ref8)")
        assert "[source](ref8)" in result

    def test_no_refs_unchanged(self):
        text = "No citations here."
        assert _wrap_bare_refs(text) == text


class TestNormalizeMalformedCitations:
    """Integration tests for normalize_malformed_citations()."""

    def test_multi_ref_link_expanded_and_not_double_wrapped(self):
        text = "[source](ref1, ref2)"
        result = normalize_malformed_citations(text)
        assert result == "[source](ref1) [source](ref2)"

    def test_bare_ref_wrapped(self):
        result = normalize_malformed_citations("Fact from ref3.")
        assert "[source](ref3)" in result

    def test_bare_tiny_url_wrapped(self):
        result = normalize_malformed_citations("See https://ref3.xyz.")
        assert "[source](https://ref3.xyz)" in result

    def test_existing_valid_link_untouched(self):
        text = "See [1](ref5) here."
        result = normalize_malformed_citations(text)
        assert result == text

    def test_combined_malformed_and_valid(self):
        text = "Good [1](ref1). Also [source](ref2, ref3) and bare ref4."
        result = normalize_malformed_citations(text)
        assert "[1](ref1)" in result           # existing link untouched
        assert "[source](ref2)" in result      # split multi-ref
        assert "[source](ref3)" in result
        assert "[source](ref4)" in result       # bare ref wrapped

    def test_bracket_ref_normalized_then_wrapping_pipeline(self):
        """Bracket refs participate in the full normalize_malformed_citations chain."""
        text = "[ref2] plus ref3"
        result = normalize_malformed_citations(text)
        assert "[source](ref2)" in result
        assert "[source](ref3)" in result

    def test_end_to_end_resolution_multi_ref(self):
        """[source](ref1, ref2) resolves both citations through the full pipeline."""
        url1 = _url(REC1, 0)
        url2 = _url(REC2, 3)
        docs = [
            _make_doc(REC1, 0, "first chunk", block_web_url=url1),
            _make_doc(REC2, 3, "second chunk", block_web_url=url2),
        ]
        answer = "[source](ref1, ref2)"
        result_text, citations = normalize_citations_and_chunks(
            answer, docs, ref_to_url={"ref1": url1, "ref2": url2}
        )
        assert len(citations) == 2
        assert citations[0]["content"] == "first chunk"
        assert citations[1]["content"] == "second chunk"

    def test_end_to_end_resolution_bare_ref(self):
        """A bare refN in text is resolved into a proper citation."""
        url = _url(REC1, 0)
        docs = [_make_doc(REC1, 0, "chunk content", block_web_url=url)]
        answer = "Supported by ref1."
        result_text, citations = normalize_citations_and_chunks(
            answer, docs, ref_to_url={"ref1": url}
        )
        assert len(citations) == 1
        assert citations[0]["content"] == "chunk content"
        assert url in result_text

    def test_end_to_end_resolution_bare_tiny_url(self):
        """A bare https://refN.xyz in text is resolved into a proper citation."""
        url = _url(REC1, 0)
        docs = [_make_doc(REC1, 0, "chunk content", block_web_url=url)]
        answer = "Supported by https://ref1.xyz."
        result_text, citations = normalize_citations_and_chunks(
            answer, docs, ref_to_url={"ref1": url}
        )
        assert len(citations) == 1
        assert citations[0]["content"] == "chunk content"
        assert url in result_text

    def test_end_to_end_agent_multi_ref(self):
        """Multi-ref link resolves through the agent pipeline."""
        url1 = _url(REC1, 0)
        url2 = _url(REC2, 3)
        docs = [
            _make_doc(REC1, 0, "agent chunk 1", block_web_url=url1),
            _make_doc(REC2, 3, "agent chunk 2", block_web_url=url2),
        ]
        answer = "Evidence [source](ref1, ref2)."
        result_text, citations = normalize_citations_and_chunks_for_agent(
            answer, docs, ref_to_url={"ref1": url1, "ref2": url2}
        )
        assert len(citations) == 2
        assert citations[0]["content"] == "agent chunk 1"
        assert citations[1]["content"] == "agent chunk 2"


class TestRecordLandingPageCitations:
    """Record /header URLs (/record/{id}) map to record-level citation chunks."""

    def test_chat_finds_record_via_records_list(self):
        landing = f"{BASE}/record/{REC1}"
        record = {
            "id": REC1,
            "record_name": "Named Record",
            "semantic_metadata": {"summary": "Summary line"},
            "block_containers": {"blocks": [], "block_groups": []},
        }
        answer = f"Overview [doc]({landing})."
        _, citations = normalize_citations_and_chunks(answer, [], records=[record])
        assert len(citations) == 1
        assert citations[0]["content"] == "Summary line"

    def test_chat_record_page_non_string_summary_is_stringified(self):
        landing = f"{BASE}/record/{REC1}"
        record = {
            "id": REC1,
            "record_name": "Named Record",
            "semantic_metadata": {"summary": 42},
            "block_containers": {"blocks": [], "block_groups": []},
        }
        answer = f"Overview [doc]({landing})."
        _, citations = normalize_citations_and_chunks(answer, [], records=[record])
        assert len(citations) == 1
        assert citations[0]["content"] == "42"

    def test_chat_finds_record_via_virtual_record_id_map(self):
        landing = f"{BASE}/record/{REC1}"
        rec = {
            "id": REC1,
            "record_name": "VR Map Record",
            "block_containers": {"blocks": [], "block_groups": []},
        }
        answer = f"See [r]({landing})."
        _, citations = normalize_citations_and_chunks(
            answer, [], records=[], virtual_record_id_to_result={"vr-x": rec},
        )
        assert len(citations) == 1
        assert "VR Map Record" in citations[0]["content"]

    def test_chat_warns_when_record_missing(self):
        landing = f"{BASE}/record/{REC1}"
        answer = f"x [d]({landing})."
        with patch("app.utils.citations.logger") as log:
            _, citations = normalize_citations_and_chunks(answer, [], records=[])
        assert citations == []
        log.warning.assert_called()

    def test_agent_record_landing_page(self):
        landing = f"{BASE}/record/{REC1}"
        record = {
            "id": REC1,
            "record_name": "Agent Landing",
            "block_containers": {"blocks": [], "block_groups": []},
        }
        answer = f"y [t]({landing})."
        _, citations = normalize_citations_and_chunks_for_agent(
            answer, [], records=[record],
        )
        assert len(citations) == 1
        assert "Agent Landing" in citations[0]["content"]

    def test_agent_warns_when_record_missing(self):
        landing = f"{BASE}/record/{REC1}"
        answer = f"z [t]({landing})."
        with patch("app.utils.citations.logger") as log:
            _, citations = normalize_citations_and_chunks_for_agent(answer, [], records=[])
        assert citations == []
        log.warning.assert_called()


class _TruthyButEmptyStr:
    """Test double whose bool is True but str() returns empty."""

    def __bool__(self):
        return True

    def __str__(self):
        return ""


class TestEmptyStringifiedContentSkip:
    """Cover lines 330, 492, 527 — citation_content empty after stringification."""

    def test_records_fallback_skipped_when_stringification_is_empty(self):
        url = _url(REC1, 0)
        record = {
            "id": REC1,
            "block_containers": {
                "blocks": [{
                    "type": "text",
                    "data": _TruthyButEmptyStr(),
                    "index": 0,
                }]
            },
        }
        answer = f"See [1]({url})."
        with patch("app.utils.citations.get_enhanced_metadata", return_value={}):
            _, citations = normalize_citations_and_chunks(answer, [], records=[record])
        assert citations == []

    def test_agent_records_fallback_skipped_when_stringification_is_empty(self):
        url = _url(REC1, 0)
        record = {
            "id": REC1,
            "block_containers": {
                "blocks": [{
                    "type": "text",
                    "data": _TruthyButEmptyStr(),
                    "index": 0,
                }]
            },
        }
        answer = f"See [1]({url})."
        with patch("app.utils.citations.get_enhanced_metadata", return_value={}):
            _, citations = normalize_citations_and_chunks_for_agent(
                answer, [], records=[record]
            )
        assert citations == []

    def test_agent_virtual_fallback_skipped_when_stringification_is_empty(self):
        url = _url(REC1, 0)
        vrid_map = {
            "vr1": {
                "id": REC1,
                "block_containers": {
                    "blocks": [{
                        "type": "text",
                        "data": _TruthyButEmptyStr(),
                        "index": 0,
                    }]
                },
            }
        }
        answer = f"See [1]({url})."
        with patch("app.utils.citations.get_enhanced_metadata", return_value={}):
            _, citations = normalize_citations_and_chunks_for_agent(
                answer, [], virtual_record_id_to_result=vrid_map
            )
        assert citations == []
