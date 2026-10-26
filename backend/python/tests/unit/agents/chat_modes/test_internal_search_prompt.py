"""The enterprise-search mode text under ANSWER_COMPOSITION_RULES and
THOROUGH_RETRIEVAL: unchanged with both off, each switch adding only its own
paragraphs, and never naming a tool the chat is not granted."""

from __future__ import annotations

import itertools
import re

import pytest

from app.agents.chat_modes.internal_search_prompt import internal_search_instructions

# The text this mode sent before the switches existed. A failure here means
# the default prompt changed: every deployment's answers, and its cached
# prompt prefix, would change with it.
_BEFORE_THE_SWITCHES = (
    "Answer ONLY from the organization's internal knowledge base / provided context and "
    "attachments. Do not use your own training knowledge to answer factual questions. If the "
    "answer is not present in the retrieved context, say so explicitly rather than guessing.\n\n"
    "The context you are given contains the most relevant BLOCKS from each document — not "
    "the full documents. For questions that can be answered by locating the right passage "
    "(a date, a name, a number, a status), the blocks are sufficient. For questions that "
    "need whole-document content — a summary, what the risks or key points are, a review, "
    "a comparison, anything covering all of something — a handful of blocks cannot support "
    "the answer. When a candidate list appears below the context blocks showing how much of "
    "each record you hold (\"you have N of M blocks\"), call `knowledgegraph__fetch_record` "
    "with those record IDs before answering."
)

_COMPOSE = "Compose the answer so it stands on its own"
_SPEAKER = "name the person or bot who said it"
_QUALIFIERS = "\"about\", \"at most\" and AND/OR conditions stay as written"
_PREMISE = "Correct the question's premise only where that changes the answer"
_DEFINING_RECORD = "the record that defines it for the exact scope asked about"
_CONFLICT_ORDER = (
    "When sources disagree, prefer in order: a record saying it updates, corrects or replaces "
    "the other"
)
_SECOND_VALUE = "Present a second value for what was asked only when"
_REPORT_ONLY = "said, report each version with its source."
_READ_IN_FULL = "read in full the records your answer will rest on"
_BLOCKS_SUFFICE = "the blocks are sufficient"
_SEARCH_AGAIN = "search again in the terms a document would use"
_LIST_THE_SET = "one search returns a sample, not the set"
_SEEK_OTHER = "search for other sources on the same subject before answering"
_ENTITY_TOOLS = ("knowledgegraph__search_entities", "entity_ids")

_COMPOSITION_MARKERS = (
    _COMPOSE, _SPEAKER, _QUALIFIERS, _PREMISE, _DEFINING_RECORD, _CONFLICT_ORDER, _SECOND_VALUE,
)
_THOROUGH_MARKERS = (_READ_IN_FULL, _SEARCH_AGAIN, _LIST_THE_SET, _SEEK_OTHER)

# What a flat enterprise-search chat can call: the knowledge-graph toolset is
# essential there, and the fetch tool is granted once a result names a record.
_ALWAYS_GRANTED = frozenset({
    "knowledgegraph__fetch_record", "knowledgegraph__list_files", "knowledgegraph__navigate",
    "knowledgegraph__search", "start_block",
})
# `find_records_by_entity` is not here: it is granted only after
# `search_entities` has returned an entity id.
_GRANTED_WITH_ENTITY_STORE = frozenset({"knowledgegraph__search_entities", "entity_ids"})

_ALL_COMBINATIONS = list(itertools.product((False, True), repeat=3))


def _text(*, composition: bool = False, thorough: bool = False, entity_tools: bool = False) -> str:
    return internal_search_instructions(composition=composition, thorough=thorough, entity_tools=entity_tools)


class TestBothOff:
    @pytest.mark.parametrize("entity_tools", [False, True])
    def test_is_the_text_from_before_the_switches(self, entity_tools: bool) -> None:
        assert _text(entity_tools=entity_tools) == _BEFORE_THE_SWITCHES


class TestComposition:
    def test_adds_every_composition_rule(self) -> None:
        text = _text(composition=True)
        for marker in _COMPOSITION_MARKERS:
            assert text.count(marker) == 1, marker

    def test_keeps_the_fetch_paragraph_and_adds_no_turns(self) -> None:
        text = _text(composition=True)
        assert text.startswith(_BEFORE_THE_SWITCHES + "\n\n")
        for marker in _THOROUGH_MARKERS:
            assert marker not in text, marker
        assert _REPORT_ONLY in text

    def test_conflict_order_follows_the_composition_paragraph(self) -> None:
        text = _text(composition=True)
        assert text.index(_COMPOSE) < text.index(_REPORT_ONLY) < text.index(_CONFLICT_ORDER)
        assert text.index(_CONFLICT_ORDER) < text.index(_SECOND_VALUE)


class TestThorough:
    def test_replaces_the_blocks_are_enough_rule_with_reading_in_full(self) -> None:
        text = _text(thorough=True)
        assert _BLOCKS_SUFFICE not in text
        assert _READ_IN_FULL in text
        assert "`knowledgegraph__fetch_record`" in text

    def test_adds_search_again_and_seek_other_versions_without_composition(self) -> None:
        text = _text(thorough=True)
        for marker in (_SEARCH_AGAIN, _LIST_THE_SET, _SEEK_OTHER):
            assert text.count(marker) == 1, marker
        for marker in _COMPOSITION_MARKERS:
            assert marker not in text, marker

    def test_names_entity_tools_only_when_they_are_granted(self) -> None:
        without = _text(thorough=True)
        with_store = _text(thorough=True, entity_tools=True)

        for name in _ENTITY_TOOLS:
            assert name not in without
            assert name in with_store
        assert "folder (`knowledgegraph__list_files`, `knowledgegraph__navigate`) the question covers" in without
        assert "no such folder, keep searching" in without
        assert "`knowledgegraph__navigate`) or entity the question covers" in with_store
        assert "no such folder or entity, keep searching" in with_store


class TestSecondValue:
    def test_a_different_persons_value_in_one_record_is_a_version(self) -> None:
        """A question about what different people said needs each person's
        value, even when both are in one meeting or thread."""
        paragraph = _text(composition=True).split("\n\n")[-1]
        allowed, excluded = paragraph.split(" The same source's looser or partial mention", 1)
        assert "a different person in the same thread, meeting or email" in allowed
        assert "is not a rival version" in excluded
        assert "a day or time without the date" in excluded


class TestBothOn:
    def test_seeks_other_versions_instead_of_only_reporting_them(self) -> None:
        text = _text(composition=True, thorough=True)
        assert text.count(_SEEK_OTHER) == 1
        assert _REPORT_ONLY not in text.replace(_SEEK_OTHER, "")

    def test_paragraph_order(self) -> None:
        text = _text(composition=True, thorough=True, entity_tools=True)
        order = [_READ_IN_FULL, _SEARCH_AGAIN, _COMPOSE, _SEEK_OTHER, _CONFLICT_ORDER, _SECOND_VALUE]
        positions = [text.index(marker) for marker in order]
        assert positions == sorted(positions)


@pytest.mark.parametrize(("composition", "thorough", "entity_tools"), _ALL_COMBINATIONS)
class TestEveryCombination:
    def test_paragraphs_are_joined_cleanly(self, composition: bool, thorough: bool, entity_tools: bool) -> None:
        text = _text(composition=composition, thorough=thorough, entity_tools=entity_tools)
        assert text == text.strip()
        assert "\n\n\n" not in text
        assert "  " not in text
        assert not re.search(r" \n|\n ", text)
        for paragraph in text.split("\n\n"):
            assert paragraph.endswith("."), paragraph[-80:]

    def test_names_only_granted_tools(self, composition: bool, thorough: bool, entity_tools: bool) -> None:
        text = _text(composition=composition, thorough=thorough, entity_tools=entity_tools)
        allowed = _ALWAYS_GRANTED | (_GRANTED_WITH_ENTITY_STORE if entity_tools else frozenset())
        named = set(re.findall(r"`([^`]+)`", text))
        assert named <= allowed, named - allowed

    def test_claims_no_list_position(self, composition: bool, thorough: bool, entity_tools: bool) -> None:
        """Whether the context and candidate list are in relevance order is
        RELEVANCE_ORDERED_CONTEXT's choice; without it they are in record-id
        order, so "the top records" would point at arbitrary ones."""
        text = _text(composition=composition, thorough=thorough, entity_tools=entity_tools).lower()
        for phrase in ("the top ", "top one", "first records", "highest-ranked", "highest ranked", "listed first"):
            assert phrase not in text, phrase

    def test_does_not_depend_on_anything_but_its_arguments(
        self, composition: bool, thorough: bool, entity_tools: bool,
    ) -> None:
        first = _text(composition=composition, thorough=thorough, entity_tools=entity_tools)
        assert first == _text(composition=composition, thorough=thorough, entity_tools=entity_tools)
