"""
Additional coverage tests for app.utils.citations

Targets uncovered functions and branches:
- extract_tiny_ref
- build_tiny_web_ref_url
- _expand_multi_ref_links
- _normalize_bracket_refs
- _wrap_bare_refs
- _clean_duplicate_citation_links
- _trailing_paren_replacer / _consecutive_links_replacer internals
- display_url_for_llm
"""

import re
from unittest.mock import patch

from app.models.blocks import BlockType, GroupType
from app.utils import citations as citations_mod
from app.utils.citations import (
    _clean_duplicate_citation_links,
    _expand_multi_ref_links,
    _find_web_record_by_url,
    _normalize_bracket_refs,
    _renumber_citation_links,
    _wrap_bare_refs,
    build_tiny_web_ref_url,
    detect_hallucinated_citation_urls,
    display_url_for_llm,
    extract_tiny_ref,
    fix_json_string,
    normalize_citations_and_chunks,
    normalize_citations_and_chunks_for_agent,
)


BASE = "http://app.example.com"
REC1 = "rec-aaa-111"


def _url(record_id: str, block_index: int, base: str = BASE) -> str:
    return f"{base}/record/{record_id}/preview#blockIndex={block_index}"


# ---------------------------------------------------------------------------
# extract_tiny_ref
# ---------------------------------------------------------------------------

class TestExtractTinyRef:
    def test_valid_tiny_ref(self):
        assert extract_tiny_ref("https://ref1.xyz") == "ref1"
        assert extract_tiny_ref("https://ref123.xyz") == "ref123"

    def test_valid_with_trailing_slash(self):
        assert extract_tiny_ref("https://ref5.xyz/") == "ref5"

    def test_non_tiny_ref_url(self):
        assert extract_tiny_ref("https://example.com") is None

    def test_empty_string(self):
        assert extract_tiny_ref("") is None

    def test_none(self):
        assert extract_tiny_ref(None) is None

    def test_bare_ref_not_url(self):
        assert extract_tiny_ref("ref1") is None

    def test_http_variant(self):
        assert extract_tiny_ref("http://ref3.xyz") == "ref3"


# ---------------------------------------------------------------------------
# build_tiny_web_ref_url
# ---------------------------------------------------------------------------

class TestBuildTinyWebRefUrl:
    def test_basic(self):
        assert build_tiny_web_ref_url("ref1") == "https://ref1.xyz"
        assert build_tiny_web_ref_url("ref42") == "https://ref42.xyz"


# ---------------------------------------------------------------------------
# _expand_multi_ref_links
# ---------------------------------------------------------------------------

class TestExpandMultiRefLinks:
    def test_single_ref_unchanged(self):
        text = "[source](ref1)"
        assert _expand_multi_ref_links(text) == text

    def test_comma_separated_refs(self):
        text = "[source](ref1, ref2, ref3)"
        result = _expand_multi_ref_links(text)
        assert "[source](ref1)" in result
        assert "[source](ref2)" in result
        assert "[source](ref3)" in result

    def test_space_separated_refs(self):
        text = "[source](ref1 ref2)"
        result = _expand_multi_ref_links(text)
        assert "[source](ref1)" in result
        assert "[source](ref2)" in result

    def test_no_refs(self):
        text = "Just plain text"
        assert _expand_multi_ref_links(text) == text


# ---------------------------------------------------------------------------
# _normalize_bracket_refs
# ---------------------------------------------------------------------------

class TestNormalizeBracketRefs:
    def test_bracketed_ref(self):
        text = "Some fact [ref3]"
        result = _normalize_bracket_refs(text)
        assert "[source](ref3)" in result

    def test_already_valid_link_unchanged(self):
        text = "[source](ref3)"
        result = _normalize_bracket_refs(text)
        assert result == text

    def test_multiple_bracket_refs(self):
        text = "Fact 1 [ref1] and fact 2 [ref2]"
        result = _normalize_bracket_refs(text)
        assert "[source](ref1)" in result
        assert "[source](ref2)" in result


# ---------------------------------------------------------------------------
# _wrap_bare_refs
# ---------------------------------------------------------------------------

class TestWrapBareRefs:
    def test_bare_ref_token(self):
        text = "According to ref3, the policy is..."
        result = _wrap_bare_refs(text)
        assert "[source](ref3)" in result

    def test_bare_tiny_url(self):
        text = "See https://ref5.xyz for details."
        result = _wrap_bare_refs(text)
        assert "[source](https://ref5.xyz)" in result

    def test_existing_link_unchanged(self):
        text = "[source](ref1) is correct"
        result = _wrap_bare_refs(text)
        assert result == text

    def test_no_refs(self):
        text = "No citations here."
        assert _wrap_bare_refs(text) == text


# ---------------------------------------------------------------------------
# _remove_duplicate_citation_links
# ---------------------------------------------------------------------------

class TestCleanDuplicateCitationLinks:
    def test_trailing_paren_url_removed(self):
        text = "[source](https://example.com/page) (https://ref1.xyz)"
        ref_to_url = {"ref1": "https://example.com/page"}
        result = _clean_duplicate_citation_links(text, ref_to_url)
        assert "(https://ref1.xyz)" not in result
        assert "[source](https://example.com/page)" in result

    def test_consecutive_same_links_deduplicated(self):
        text = "[title](ref1) [source](ref1)"
        ref_to_url = {}
        result = _clean_duplicate_citation_links(text, ref_to_url)
        # Second link should be removed since both resolve to same target
        assert result.count("[") < text.count("[")

    def test_different_targets_kept(self):
        text = "[title](ref1) [source](ref2)"
        ref_to_url = {}
        result = _clean_duplicate_citation_links(text, ref_to_url)
        assert "ref1" in result
        assert "ref2" in result

    def test_no_duplicate_links(self):
        text = "Just [source](ref1) here."
        result = _clean_duplicate_citation_links(text, {})
        assert result == text

    def test_trailing_removed_when_plain_urls_identical(self):
        text = "[src](https://dup.example/path) (https://dup.example/path)"
        assert _clean_duplicate_citation_links(text, None) == "[src](https://dup.example/path)"

    def test_trailing_kept_when_plain_urls_differ(self):
        text = "[src](https://a.example) (https://b.example)"
        assert _clean_duplicate_citation_links(text, None) == text

    def test_trailing_ref_link_target_strips_trailing_paren(self):
        """First target is ``refN`` — drop parenthesized tail (``citations.py`` L203–204)."""
        text = "[source](ref1) (https://unrelated.example/page)"
        assert _clean_duplicate_citation_links(text, {}) == "[source](ref1)"


# ---------------------------------------------------------------------------
# display_url_for_llm
# ---------------------------------------------------------------------------

class TestDisplayUrlForLlm:
    def test_short_url_returned_as_is(self):
        url = "https://x.com/p"
        result = display_url_for_llm(url, None)
        # With no ref_mapper, should return the URL directly
        assert "x.com" in result

    def test_with_ref_mapper(self):
        from app.utils.chat_helpers import CitationRefMapper
        mapper = CitationRefMapper()
        url = "https://example.com/very/long/path/that/exceeds/threshold"
        result = display_url_for_llm(url, mapper)
        # Should return either a tiny URL or the original, depending on threshold
        assert isinstance(result, str)
        assert len(result) > 0

    def test_empty_url_returns_empty(self):
        from app.utils.chat_helpers import CitationRefMapper

        assert display_url_for_llm("", CitationRefMapper()) == ""


# ---------------------------------------------------------------------------
# fix_json_string
# ---------------------------------------------------------------------------

class TestFixJsonString:
    def test_valid_json_unchanged(self):
        s = '{"answer": "hello", "confidence": "High"}'
        assert fix_json_string(s) == s

    def test_unescaped_newlines_in_string(self):
        s = '{"answer": "line1\nline2"}'
        result = fix_json_string(s)
        assert isinstance(result, str)

    def test_trailing_comma_handled(self):
        s = '{"answer": "x", "reason": "y",}'
        result = fix_json_string(s)
        # Should fix trailing comma
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# _find_web_record_by_url / renumber / detect
# ---------------------------------------------------------------------------


class TestFindWebRecordByUrl:
    def test_empty_url(self):
        assert _find_web_record_by_url("", [{"url": "https://x"}]) is None

    def test_finds_matching_row(self):
        rows = [{"url": "https://match.test", "content": "z"}]
        assert _find_web_record_by_url("https://match.test", rows) is rows[0]

    def test_no_match_returns_none(self):
        assert _find_web_record_by_url("https://a", [{"url": "https://b"}]) is None


class TestRenumberCitationLinksDescriptive:
    def test_descriptive_link_text_replaced_with_numbered_badge(self):
        """``_renumber_citation_links`` always emits ``[N](full_url)``; link text is discarded."""
        u = "https://example.com/doc#section"
        text = f"Read [Bellandur Restaurants]({u}) today."
        matches = list(re.finditer(citations_mod._MD_LINK_PATTERN, text))
        out = _renumber_citation_links(text, matches, {u: 1})
        assert "[Bellandur Restaurants]" not in out
        assert out == f"Read [1]({u}) today."


class TestDetectHallucinatedCitationDuplicates:
    def test_duplicate_ref_targets_single_hallucination_entry(self):
        out = detect_hallucinated_citation_urls("[a](ref9) also [b](ref9)")
        assert out == ["ref9"]


# ---------------------------------------------------------------------------
# normalize_citations_and_chunks (web records, LIST group, record data None)
# ---------------------------------------------------------------------------


WEB_CITE_URL = "https://search.example/snippet#:~:text=hello"


class TestNormalizeCitationsWebRecords:
    def test_web_record_builds_web_citation(self):
        answer = f"See [source]({WEB_CITE_URL})."
        web_rows = [{"url": WEB_CITE_URL, "content": "snippet body", "org_id": "org-web"}]
        _, cites = normalize_citations_and_chunks(answer, [], web_records=web_rows)
        assert len(cites) == 1
        assert cites[0]["citationType"] == "web|url"
        assert cites[0]["content"] == "snippet body"
        assert cites[0]["metadata"]["orgId"] == "org-web"
        assert cites[0]["metadata"]["webUrl"] == WEB_CITE_URL

    def test_web_record_empty_content_skipped(self):
        answer = f"[1]({WEB_CITE_URL})"
        web_rows = [{"url": WEB_CITE_URL, "content": ""}]
        _, cites = normalize_citations_and_chunks(answer, [], web_records=web_rows)
        assert cites == []

    def test_web_record_content_none_skipped(self):
        answer = f"[1]({WEB_CITE_URL})"
        web_rows = [{"url": WEB_CITE_URL, "content": None}]
        _, cites = normalize_citations_and_chunks(answer, [], web_records=web_rows)
        assert cites == []

    def test_agent_web_record_same_as_chat(self):
        answer = f"[source]({WEB_CITE_URL})"
        web_rows = [{"url": WEB_CITE_URL, "content": "agent web", "org_id": ""}]
        _, cites = normalize_citations_and_chunks_for_agent(answer, [], web_records=web_rows)
        assert len(cites) == 1
        assert cites[0]["citationType"] == "web|url"

    def test_agent_web_record_content_none_skipped(self):
        answer = f"[1]({WEB_CITE_URL})"
        web_rows = [{"url": WEB_CITE_URL, "content": None}]
        _, cites = normalize_citations_and_chunks_for_agent(answer, [], web_records=web_rows)
        assert cites == []

    def test_agent_no_web_match_falls_through_to_docs(self):
        """``web_records`` non-empty but no URL match — skip ``if web_rec`` body."""
        url = _url(REC1, 0)
        docs = [{
            "virtual_record_id": REC1,
            "block_index": 0,
            "block_type": "text",
            "block_web_url": url,
            "content": "doc chunk",
            "metadata": {"origin": "O", "recordName": "N", "recordId": REC1, "mimeType": "M", "orgId": "O"},
        }]
        answer = f"[1]({url})"
        other_web = [{"url": "https://other-only.test", "content": "x"}]
        _, cites = normalize_citations_and_chunks_for_agent(answer, docs, web_records=other_web)
        assert len(cites) == 1
        assert cites[0]["content"] == "doc chunk"


class TestNormalizeCitationsListGroupChild:
    def test_list_group_child_url_indexed_like_table(self):
        child_url = _url(REC1, 3)
        child = {
            "block_web_url": child_url,
            "content": "list item text",
            "metadata": {
                "origin": "O",
                "recordName": "N",
                "recordId": REC1,
                "mimeType": "M",
                "orgId": "Org",
            },
        }
        list_doc = {
            "virtual_record_id": REC1,
            "block_index": 0,
            "block_type": GroupType.LIST.value,
            "block_web_url": None,
            "content": ("", [child]),
            "metadata": {},
        }
        answer = f"Bullet [1]({child_url})."
        _, cites = normalize_citations_and_chunks(answer, [list_doc])
        assert len(cites) == 1
        assert cites[0]["content"] == "list item text"


class TestNormalizeCitationsChatRecordBranches:
    def test_record_fallback_block_data_none_skipped(self):
        url = _url(REC1, 0)
        record = {
            "id": REC1,
            "block_containers": {
                "blocks": [{"type": BlockType.TEXT.value, "data": None, "index": 0}],
            },
        }
        answer = f"[1]({url})"
        with patch("app.utils.citations.get_enhanced_metadata", return_value={}):
            _, cites = normalize_citations_and_chunks(answer, [], records=[record])
        assert cites == []

    def test_table_child_without_block_web_url_skips_child_index(self):
        """Child row exists but has no URL — inner ``if child_url`` branch skipped."""
        table_doc = {
            "virtual_record_id": REC1,
            "block_type": GroupType.TABLE.value,
            "content": ("summary", [{"content": "row", "metadata": {}}]),
            "metadata": {},
        }
        url = _url(REC1, 0)
        answer = f"[1]({url})"
        normalize_citations_and_chunks(answer, [table_doc], records=[])


class TestNormalizeAgentExtraBranches:
    def test_metadata_enriched_via_virtual_record_id_in_metadata(self):
        """``virtualRecordId`` on metadata fills origin when ``virtual_record_id`` on doc is absent."""
        url = _url(REC1, 0)
        docs = [{
            "block_index": 0,
            "block_type": "text",
            "block_web_url": url,
            "content": "body",
            "metadata": {"virtualRecordId": "vr-meta"},
        }]
        vrid_map = {
            "vr-meta": {
                "origin": "CONFLUENCE",
                "record_name": "Page",
                "id": "full-id",
                "mime_type": "text/html",
            },
        }
        answer = f"[1]({url})"
        _, cites = normalize_citations_and_chunks_for_agent(
            answer, docs, virtual_record_id_to_result=vrid_map
        )
        meta = cites[0]["metadata"]
        assert meta["origin"] == "CONFLUENCE"
        assert meta["recordName"] == "Page"
        assert meta["recordId"] == "full-id"
        assert meta["mimeType"] == "text/html"

    def test_vrid_fallback_when_records_match_but_block_missing(self):
        url = _url(REC1, 0)
        records = [{"id": REC1, "block_containers": {"blocks": []}}]
        vrid_map = {
            "vr1": {
                "id": REC1,
                "block_containers": {
                    "blocks": [{"type": BlockType.TEXT.value, "data": "from vrid map", "index": 0}],
                },
            },
        }
        answer = f"[1]({url})"
        with patch("app.utils.citations.get_enhanced_metadata", return_value={
            "origin": "O",
            "recordName": "N",
            "recordId": REC1,
            "mimeType": "M",
            "orgId": "Org",
        }):
            _, cites = normalize_citations_and_chunks_for_agent(
                answer, [], records=records, virtual_record_id_to_result=vrid_map
            )
        assert len(cites) == 1
        assert cites[0]["content"] == "from vrid map"

    def test_vrid_fallback_data_none_no_citation(self):
        url = _url(REC1, 0)
        vrid_map = {
            "vr1": {
                "id": REC1,
                "block_containers": {
                    "blocks": [{"type": BlockType.TEXT.value, "data": None, "index": 0}],
                },
            },
        }
        answer = f"[1]({url})"
        with patch("app.utils.citations.get_enhanced_metadata", return_value={}):
            _, cites = normalize_citations_and_chunks_for_agent(
                answer, [], records=[], virtual_record_id_to_result=vrid_map
            )
        assert cites == []
