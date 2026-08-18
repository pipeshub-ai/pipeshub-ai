"""Tests for ``app.modules.agents.enumeration.policy``.

The must-not-match table is the important half. Firing on a question the census
cannot answer produces a fully cited answer about the wrong set of records,
which is a worse failure than the uncited answer this path exists to fix.
"""
from __future__ import annotations

import pytest

from app.modules.agents.enumeration.policy import (
    excluded_reason,
    is_enumeration_query,
)

# A census over everything the caller can see.
SHOULD_MATCH = [
    "How many documents are in the knowledge base?",
    "How many distinct documents are there and what is each one about?",
    "How many docs do we have?",
    "What documents do we have?",
    "List all the files",
    "List every document",
    "What files are in the knowledge base?",
    "Give me an inventory of all records",
    "Count the documents",
    "What is the total number of documents?",
]

# Each of these carries a constraint a whole-corpus census cannot honour.
MUST_NOT_MATCH = [
    # Topical: needs retrieval, not a census.
    ("What documents do we have about onboarding?", "topical"),
    ("How many contracts mention indemnity?", "topical"),
    ("Which files mention the merger?", "topical"),
    ("List all documents regarding the acquisition", "topical"),
    ("What records discuss pricing?", "topical"),
    # Container: a unit inside a document, or one folder, not the corpus.
    ("How many pages are in this PDF?", "container"),
    ("List all the files in this folder.", "container"),
    ("How many sections does this document have?", "container"),
    ("List every chapter in this file", "container"),
    # Temporal: the census has no time window.
    ("What records were updated yesterday?", "temporal"),
    ("How many documents were added last month?", "temporal"),
    ("List all files created since January", "temporal"),
    # Anaphora: refers to an earlier result the classifier cannot see.
    ("How many of those?", "anaphora"),
    ("List all of them", "anaphora"),
]

# Ordinary questions with no enumeration intent at all.
UNRELATED = [
    "What is the onboarding checklist about?",
    "Summarise the Tetra document",
    "List the risks in this contract",
    "What is the boiling point of water?",
    "Compare the onboarding checklist and Tetra",
    "Who wrote the security policy?",
    "",
]


class TestIsEnumerationQuery:
    @pytest.mark.parametrize("query", SHOULD_MATCH)
    def test_census_questions_match(self, query: str) -> None:
        assert is_enumeration_query(query) is True

    @pytest.mark.parametrize("query,_reason", MUST_NOT_MATCH)
    def test_constrained_questions_do_not_match(self, query: str, _reason: str) -> None:
        assert is_enumeration_query(query) is False

    @pytest.mark.parametrize("query", UNRELATED)
    def test_unrelated_questions_do_not_match(self, query: str) -> None:
        assert is_enumeration_query(query) is False

    def test_blank_input(self) -> None:
        assert is_enumeration_query("", "   ") is False


class TestExcludedReason:
    @pytest.mark.parametrize("query,reason", MUST_NOT_MATCH)
    def test_reason_is_named(self, query: str, reason: str) -> None:
        """The reason is reported so a fall-through can be explained rather than
        looking like the classifier simply failed to recognise the question."""
        assert excluded_reason(query) == reason

    @pytest.mark.parametrize("query", SHOULD_MATCH)
    def test_census_questions_have_no_exclusion(self, query: str) -> None:
        assert excluded_reason(query) is None
