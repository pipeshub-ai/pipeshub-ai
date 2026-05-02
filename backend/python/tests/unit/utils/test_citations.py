"""Unit tests for app.utils.citations (new markdown-link citation format)."""

import re
from unittest.mock import patch

from app.models.blocks import BlockType, GroupType
from app.utils.citations import (
    _extract_block_index_from_url,
    _extract_record_id_from_url,
    _renumber_citation_links,
    detect_hallucinated_citation_urls,
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
    _resolve_ref,
    _safe_stringify_content,
    _wrap_bare_refs,
    normalize_malformed_citations,
)


class TestResolveRef:
    def test_ref_resolved_via_mapping(self):
        """Cover line 72 — tiny ref present in mapping returns the full URL."""
        mapping = {"ref1": "http://full/url#blockIndex=0"}
        assert _resolve_ref("ref1", mapping) == "http://full/url#blockIndex=0"

    def test_ref_not_in_mapping_returns_target(self):
        assert _resolve_ref("ref99", {"ref1": "x"}) == "ref99"

    def test_none_mapping_returns_target(self):
        assert _resolve_ref("ref1", None) == "ref1"


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
        assert "[ref5](ref5)" in result

    def test_bare_tiny_url_wrapped(self):
        result = _wrap_bare_refs("See https://ref5.xyz for details.")
        assert "[ref5](https://ref5.xyz)" in result

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
        assert "[ref1](ref1)" in result
        assert "[ref2](ref2)" in result

    def test_bare_ref_at_end_of_sentence(self):
        result = _wrap_bare_refs("Supported by ref7.")
        assert "[ref7](ref7)" in result

    def test_bare_ref_in_parentheses(self):
        result = _wrap_bare_refs("(ref8)")
        assert "[ref8](ref8)" in result

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
        assert "[ref3](ref3)" in result

    def test_bare_tiny_url_wrapped(self):
        result = normalize_malformed_citations("See https://ref3.xyz.")
        assert "[ref3](https://ref3.xyz)" in result

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
        assert "[ref4](ref4)" in result        # bare ref wrapped

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
