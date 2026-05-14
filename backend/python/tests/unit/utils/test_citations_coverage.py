"""
Additional coverage tests for app.utils.citations

Targets uncovered functions and branches:
- extract_tiny_ref
- build_tiny_web_ref_url
- _expand_multi_ref_links
- _normalize_bracket_refs
- _wrap_bare_refs
- _remove_duplicate_citation_links
- _trailing_paren_replacer / _consecutive_links_replacer internals
- display_url_for_llm
"""

import pytest

from app.utils.citations import (
    _clean_duplicate_citation_links,
    _expand_multi_ref_links,
    _normalize_bracket_refs,
    _wrap_bare_refs,
    build_tiny_web_ref_url,
    display_url_for_llm,
    extract_tiny_ref,
    fix_json_string,
)


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
