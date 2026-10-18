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
_CONFLICT_ORDER = "When sources disagree, prefer in order:"
_SUPERSESSION = "a record saying it updates, corrects or replaces the other"
_SECOND_VALUE = "Present a second value for what was asked only when"
_PARAPHRASE = "A paraphrase is not a false premise"
_SUBSTANCE_FIRST = "answer with that substance even if it is still requested, pending or in progress"
_SHIPPED = "an implemented change (a merged pull request, a released config or changelog)"
_SETTLED = "Once this order settles it, give the preferred value as the answer"
_CONDITIONAL = "A rule that applies only under a condition"
_GENERATED_SUMMARY = "is written by a model when the record is indexed"
_REPORT_ONLY = "said, report each version with its source."
_READ_IN_FULL = "read in full the records your answer will rest on"
_BLOCKS_SUFFICE = "the blocks are sufficient"
_SEARCH_AGAIN = "search again in the terms a document would use"
_LIST_THE_SET = "one search returns a sample, not the set"
_SEEK_OTHER = "search for other sources on the same subject before answering"
_ENTITY_TOOLS = ("knowledgegraph__search_entities", "entity_ids")

_COMPOSITION_MARKERS = (
    _COMPOSE, _SPEAKER, _QUALIFIERS, _PREMISE, _DEFINING_RECORD, _CONFLICT_ORDER, _SECOND_VALUE,
    _PARAPHRASE, _SUBSTANCE_FIRST, _SHIPPED, _SETTLED, _CONDITIONAL, _GENERATED_SUMMARY,
)
_EACH_PART = "the records you read must cover each part"
_PART_ONLY = "A record read for one part supplies that part only"
_CONDITION_CHECK = "check that it meets every condition the question sets for the part it answers"
_OTHER_WORDS = "a record that states a condition in other words meets it"
_STATED_AS_GIVEN = "Do not add that a record words a met condition differently, or hedge it"
_CONTRADICTED = "a number, status or scope a record you read contradicts is still corrected"
_UNMET_AFTER = (
    "Mention an unmet condition only when you answer from a record other than the one the "
    "question describes, in one sentence after the answer."
)
_THOROUGH_MARKERS = (
    _READ_IN_FULL, _SEARCH_AGAIN, _LIST_THE_SET, _SEEK_OTHER,
    _EACH_PART, _PART_ONLY, _CONDITION_CHECK, _OTHER_WORDS, _STATED_AS_GIVEN, _UNMET_AFTER,
)

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
        for marker in _THOROUGH_MARKERS:
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
        order = [
            _READ_IN_FULL, _SEARCH_AGAIN, _COMPOSE, _SEEK_OTHER, _CONFLICT_ORDER, _GENERATED_SUMMARY,
            _SECOND_VALUE,
        ]
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


class TestStatusFraming:
    """Answers opened with what the assistant could not do, or with a status,
    though the records described what was asked in full."""

    def test_status_follows_the_substance(self) -> None:
        text = _text(composition=True)
        assert text.count(_SUBSTANCE_FIRST) == 1
        assert "never as the opening or as a reason the question cannot be answered" in text
        assert "do not open with what you yourself cannot do, produce or send" in text

    def test_recorded_outcomes_are_not_reopened(self) -> None:
        assert "Otherwise give the status the records state" in _text(composition=True)

    def test_a_later_rollback_goes_through_the_conflict_order(self) -> None:
        """An early "done" must not outrank a later rollback or reopening."""
        text = _text(composition=True)
        assert "when sources state different statuses, such as a later rollback or reopening, the order below decides" in text
        assert "done is done" not in text
        assert text.index("the order below decides") < text.index(_CONFLICT_ORDER)

    def test_merged_or_approved_is_not_delivery(self) -> None:
        """A fix merged or approved but awaiting deployment is not done."""
        text = _text(composition=True)
        assert "(done, merged or approved, each as stated, and delivered only where a source says so)" in text
        for claim in ("approved is done", "merged is done", "shipped change"):
            assert claim not in text, claim

    def test_a_paraphrase_is_not_a_false_premise(self) -> None:
        text = _text(composition=True)
        assert text.index(_PREMISE) < text.index(_PARAPHRASE)
        assert "adding that the record does not use the question's term" in text

    @pytest.mark.parametrize("thorough", [False, True])
    def test_only_with_composition(self, thorough: bool) -> None:
        text = _text(thorough=thorough)
        assert _PARAPHRASE not in text
        assert _SUBSTANCE_FIRST not in text


class TestConflictOrder:
    def test_implemented_beats_proposed_before_supersession(self) -> None:
        """A draft that says it replaces a deployed config is still a proposal:
        for what a system does, it must not win on supersession."""
        text = _text(composition=True)
        order = [_CONFLICT_ORDER, _SHIPPED, "even one that says it replaces it", _SUPERSESSION,
                 "a narrower record applying a broader rule"]
        positions = [text.index(marker) for marker in order]
        assert positions == sorted(positions)

    def test_a_settled_conflict_is_answered_not_left_open(self) -> None:
        assert _SETTLED in _text(composition=True)

    def test_a_conditional_rule_stays_conditional(self) -> None:
        """A settled conflict must not turn one tier's rule into the answer for a
        case the sources do not place in that tier."""
        text = _text(composition=True)
        assert text.index(_SETTLED) < text.index(_CONDITIONAL)
        assert "stays conditional: state it with its condition" in text

    def test_the_order_still_compares_a_standard_with_its_narrower_exception(self) -> None:
        """A same-scope limit on the whole order shut out the narrower-record step."""
        text = _text(composition=True)
        assert "a narrower record applying a broader rule to the exact scope asked" in text
        assert "Apply the order only to sources" not in text

    @pytest.mark.parametrize("thorough", [False, True])
    def test_only_with_composition(self, thorough: bool) -> None:
        text = _text(thorough=thorough)
        for marker in (_SHIPPED, _SETTLED, _CONDITIONAL):
            assert marker not in text, marker


class TestSourceTrust:
    """The indexer's summary of a record is a model's reading of it, and it can
    resolve an ambiguity the record leaves open the wrong way."""

    def test_an_ambiguous_reply_is_not_settled_by_a_rule(self) -> None:
        """"Tried that" after "try A, or switch to B" says neither which nor both."""
        text = _text(composition=True)
        assert "does not say which of the proposals before it it answers, do not assert which" in text
        assert "covers everything the message it answers proposed" not in text

    def test_the_generated_summary_never_settles_a_fact(self) -> None:
        text = _text(composition=True)
        assert text.count(_GENERATED_SUMMARY) == 1
        assert "never to settle a fact" in text
        assert "the text decides" in text

    @pytest.mark.parametrize("thorough", [False, True])
    def test_only_with_composition(self, thorough: bool) -> None:
        assert _GENERATED_SUMMARY not in _text(thorough=thorough)

    def test_is_its_own_paragraph_before_the_second_value_one(self) -> None:
        paragraphs = _text(composition=True).split("\n\n")
        assert paragraphs[-2].startswith("A record's \"Summary:\" line")
        assert paragraphs[-1].startswith(_SECOND_VALUE)


class TestReadCoverage:
    """Multi-part answers left a part's own record unread, or settled on a
    record that failed a condition the question set."""

    def test_each_part_and_condition_needs_a_record(self) -> None:
        text = _text(thorough=True)
        assert text.index(_READ_IN_FULL) < text.index(_EACH_PART) < text.index(_CONDITION_CHECK)
        assert "optional, recommended or condition-specific items into requirements" in text

    def test_a_failed_condition_searches_again_and_leads_with_the_record_that_meets_it(self) -> None:
        text = _text(thorough=True)
        check = text.index(_CONDITION_CHECK)
        assert check < text.index("search again in different words") < text.index("lead with it")

    @pytest.mark.parametrize("composition", [False, True])
    def test_only_with_thorough(self, composition: bool) -> None:
        text = _text(composition=composition)
        for marker in (
            _EACH_PART, _PART_ONLY, _CONDITION_CHECK, _OTHER_WORDS, _STATED_AS_GIVEN, _CONTRADICTED, _UNMET_AFTER,
        ):
            assert marker not in text, marker


class TestNoConditionCaveats:
    """An exit that told the model to say which condition a record does not
    meet made answers dispute or hedge facts the question states in other
    words, and append caveats to correct answers."""

    def test_a_condition_stated_in_other_words_is_met(self) -> None:
        text = _text(thorough=True)
        assert text.index(_CONDITION_CHECK) < text.index(_OTHER_WORDS) < text.index("If one does not hold")

    @pytest.mark.parametrize("composition", [False, True])
    def test_only_a_met_condition_is_taken_as_given(self, composition: bool) -> None:
        """An absolute "do not dispute a fact the question states" fought the
        premise rule and kept a wrong duration or amount the question stated."""
        text = _text(composition=composition, thorough=True)
        assert text.index(_OTHER_WORDS) < text.index(_STATED_AS_GIVEN)
        for absolute in ("Take what the question states as given", "do not dispute or hedge a fact it states"):
            assert absolute not in text, absolute

    def test_a_contradicted_premise_that_changes_the_answer_is_still_corrected(self) -> None:
        text = _text(thorough=True)
        assert text.index(_STATED_AS_GIVEN) < text.index(_CONTRADICTED) < text.index(_UNMET_AFTER)

    def test_a_part_record_is_checked_against_its_own_part(self) -> None:
        """A 2025 EU rollout and a 2024 US rollout asked together: each record
        meets the conditions of its part, not every condition of the question."""
        text = _text(thorough=True)
        assert _CONDITION_CHECK in text
        assert "meets every condition of its part over one that does not" in text
        assert "meets every condition the question sets (" not in text

    def test_an_unmet_condition_is_named_only_for_another_record_and_after_the_answer(self) -> None:
        assert _text(thorough=True).count(_UNMET_AFTER) == 1

    @pytest.mark.parametrize(("composition", "entity_tools"), list(itertools.product((False, True), repeat=2)))
    def test_no_exit_asks_for_the_unmet_condition(self, composition: bool, entity_tools: bool) -> None:
        text = _text(composition=composition, thorough=True, entity_tools=entity_tools)
        for exit_clause in (
            "say which condition it does not meet",
            "say so if nothing closer exists",
            "answer from the nearest record",
        ):
            assert exit_clause not in text, exit_clause
