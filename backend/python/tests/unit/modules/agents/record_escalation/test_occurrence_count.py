"""Tests for occurrence-count helpers (issue #2996)."""
from __future__ import annotations

import pytest

from app.agents.actions.knowledge_graph.ops.fetch import _occurrence_count_note
from app.modules.agents.record_escalation.occurrence_count import (
    count_occurrences,
    format_occurrence_count_note,
    is_occurrence_count_query,
    parse_occurrence_phrase,
    record_plain_text,
)
from app.modules.agents.record_escalation.policy import needs_whole_document


class TestIsOccurrenceCountQuery:
    @pytest.mark.parametrize(
        "text",
        [
            "How many times is harry potter mentioned in the book?",
            "how many times is \"Harry Potter\" mentioned",
            "count the occurrences of GDPR in the document",
            "how many mentions of ACME",
            "how often does the warranty appear in the contract",
            "number of appearances of foo in the pdf",
            "how many times does the document mention GDPR",
            "how often does the record mention harry potter",
        ],
    )
    def test_positive(self, text: str) -> None:
        assert is_occurrence_count_query(text) is True

    @pytest.mark.parametrize(
        "text",
        [
            "how many documents do we have",
            "how many risks does this contract list",
            "When was the disaster recovery test performed?",
            "how many times did we meet last week",
            "",
        ],
    )
    def test_negative(self, text: str) -> None:
        assert is_occurrence_count_query(text) is False


class TestParseOccurrencePhrase:
    def test_unquoted_mentioned_in_book(self) -> None:
        assert (
            parse_occurrence_phrase(
                "How many times is harry potter mentioned in the book?"
            )
            == "harry potter"
        )

    def test_quoted_wins(self) -> None:
        assert (
            parse_occurrence_phrase(
                'How many times is "Harry Potter" mentioned in the book?'
            )
            == "Harry Potter"
        )

    def test_count_occurrences_of(self) -> None:
        assert (
            parse_occurrence_phrase("count the occurrences of GDPR in the document")
            == "GDPR"
        )

    def test_how_often(self) -> None:
        assert (
            parse_occurrence_phrase(
                "how often does the warranty appear in the contract"
            )
            == "warranty"
        )

    def test_not_occurrence_query(self) -> None:
        assert parse_occurrence_phrase("how many documents do we have") is None

    def test_document_mention_wording(self) -> None:
        assert (
            parse_occurrence_phrase("how many times does the document mention GDPR")
            == "GDPR"
        )
        assert (
            parse_occurrence_phrase("how often does the record mention harry potter")
            == "harry potter"
        )


class TestCountOccurrences:
    def test_case_insensitive_phrase(self) -> None:
        text = "Harry Potter sat down. Then harry potter stood up. HARRY POTTER."
        assert count_occurrences(text, "Harry Potter") == 3

    def test_does_not_count_partial_words(self) -> None:
        assert count_occurrences("potteries and Potter", "Potter") == 1

    def test_flexible_whitespace(self) -> None:
        assert count_occurrences("harry   potter", "harry potter") == 1

    def test_empty(self) -> None:
        assert count_occurrences("", "x") == 0
        assert count_occurrences("abc", "") == 0


class TestRecordPlainText:
    def test_skips_fragment_children(self) -> None:
        record = {
            "block_containers": {
                "blocks": [
                    {"index": 0, "data": "Hello Harry Potter"},
                    {"index": 1, "parent_block_index": 0, "data": "Harry Potter"},
                    {"index": 2, "data": {"text": " more Harry Potter"}},
                ]
            }
        }
        text = record_plain_text(record)
        assert text.count("Harry Potter") == 2
        assert "Hello" in text

    def test_top_level_blocks_fallback(self) -> None:
        record = {"blocks": [{"data": "only here"}]}
        assert record_plain_text(record) == "only here"


class TestFormatNote:
    def test_includes_counts(self) -> None:
        note = format_occurrence_count_note(
            phrase="Harry Potter",
            per_record=[("rec-1", "book", 47)],
        )
        assert "47" in note
        assert "Harry Potter" in note
        assert "do not recount" in note.lower()


class TestNeedsWholeDocumentOccurrence:
    def test_reported_query_needs_whole_document(self) -> None:
        assert (
            needs_whole_document(
                None,
                "How many times is harry potter mentioned in the book?",
            )
            is True
        )

    def test_corpus_how_many_documents_is_not_this_path(self) -> None:
        # Corpus enumeration is a different question (#2975 / #2988).
        assert needs_whole_document(None, "how many documents do we have") is False

    def test_occurrence_query_overrides_marker_no(self) -> None:
        assert (
            needs_whole_document(
                "no",
                "How many times is harry potter mentioned in the book?",
            )
            is True
        )


class TestOccurrenceCountNoteOnFetch:
    """#2996 step 3: tally is computed over full record text, not snippets."""

    def test_counts_full_text_and_skips_fragments(self) -> None:
        record = {
            "id": "rec-hp",
            "record_name": "harry potter 50 pages",
            "block_containers": {
                "blocks": [
                    {"index": 0, "data": "Harry Potter went to school. "},
                    {"index": 1, "data": "Then harry potter came home. HARRY POTTER."},
                    {"index": 2, "parent_block_index": 0, "data": "Harry Potter"},
                ]
            },
        }
        note = _occurrence_count_note(
            "How many times is harry potter mentioned in the book?",
            [record],
        )
        assert "Computed occurrence count" in note
        assert "**3**" in note
        assert "rec-hp" in note

    def test_blank_for_non_tally_query(self) -> None:
        record = {
            "id": "rec-1",
            "block_containers": {"blocks": [{"index": 0, "data": "Harry Potter"}]},
        }
        assert _occurrence_count_note("summarize this document", [record]) == ""
