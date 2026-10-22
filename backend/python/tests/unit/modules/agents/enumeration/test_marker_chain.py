"""End-to-end check of the CORPUS_CENSUS marker, from model text to routing.

No surface on a default deployment produces this marker: every chat mode runs
the loop in `quick`, which sets `skip_intent=True` and therefore never makes the
intent call. The marker appears on every intent call: auto, react, deep and
planExecute.

That makes it exactly the kind of code that rots unnoticed, so the chain is
pinned here: what the model writes, what the parser returns, what survives into
the goal text, and what the classifier does with it.
"""
from __future__ import annotations

import pytest

from app.agents.agent_loop.intent import (
    _build_intent_prompt,
    _decision_from_text,
    _extract_corpus_census_and_clean,
)
from app.modules.agents.enumeration.policy import is_enumeration_query


class TestEveryIntentCallAsksForTheMarker:
    """An explicit react/deep/planExecute turn makes the intent call without
    routing. A model "no" can only decline a pattern match if that call asks
    for the marker too, and the answer is read on that path."""

    @pytest.mark.parametrize("include_routing", [True, False])
    def test_the_prompt_asks_for_it(self, include_routing: bool) -> None:
        assert "CORPUS_CENSUS" in _build_intent_prompt(include_routing, "", "", 0)

    def test_an_unrouted_decision_reads_and_strips_it(self) -> None:
        decision = _decision_from_text(
            "Find every document.\nCORPUS_CENSUS: no", include_routing=False,
        )
        assert decision.corpus_census == "no"
        assert decision.route is None
        assert "CORPUS_CENSUS" not in decision.rewritten_query
        assert decision.rewritten_query == "Find every document."

    def test_an_unrouted_decision_without_it_leaves_the_marker_unset(self) -> None:
        decision = _decision_from_text("Find every document.", include_routing=False)
        assert decision.corpus_census is None


class TestMarkerParsing:
    def test_a_yes_is_read_and_removed(self) -> None:
        raw = "The user wants a list of everything.\nCORPUS_CENSUS: yes\n"
        value, cleaned = _extract_corpus_census_and_clean(raw)
        assert value == "yes"
        # The marker must not survive into the goal: an internal token left in
        # the goal text reads as a user instruction to every model downstream.
        assert "CORPUS_CENSUS" not in cleaned
        assert "The user wants a list of everything." in cleaned

    def test_a_no_is_read_and_removed(self) -> None:
        value, cleaned = _extract_corpus_census_and_clean("brief\nCORPUS_CENSUS: no")
        assert value == "no"
        assert "CORPUS_CENSUS" not in cleaned

    def test_absent_marker_returns_none_and_leaves_text_alone(self) -> None:
        value, cleaned = _extract_corpus_census_and_clean("just a briefing")
        assert value is None
        assert cleaned == "just a briefing"

    def test_case_and_spacing_from_a_real_model(self) -> None:
        for raw in ("corpus_census: YES", "CORPUS_CENSUS:yes", "  CORPUS_CENSUS : yes  "):
            assert _extract_corpus_census_and_clean(raw)[0] == "yes", raw

    def test_the_last_marker_wins(self) -> None:
        """A model that restates itself should not leave the earlier answer in
        force."""
        raw = "CORPUS_CENSUS: yes\nactually, on reflection\nCORPUS_CENSUS: no"
        assert _extract_corpus_census_and_clean(raw)[0] == "no"


class TestMarkerReachesTheDecision:
    """The parsed value has to change routing, or the plumbing is decoration."""

    def test_yes_routes_a_phrasing_the_patterns_would_decline(self) -> None:
        query = "give me the lay of the land in here"
        assert is_enumeration_query(None, query) is False
        assert is_enumeration_query("yes", query) is True

    def test_no_declines_a_phrasing_the_patterns_would_accept(self) -> None:
        query = "How many documents do we have?"
        assert is_enumeration_query(None, query) is True
        assert is_enumeration_query("no", query) is False

    def test_an_exclusion_still_wins_over_yes(self) -> None:
        """The one case where the model does not get the last word: a condition
        the census cannot apply means a census answer would be confidently
        wrong."""
        assert is_enumeration_query("yes", "how many documents about onboarding?") is False

    def test_the_whole_chain(self) -> None:
        """Model text in, routing decision out."""
        model_output = "The user is asking what exists.\nCORPUS_CENSUS: yes\n"
        marker, goal_text = _extract_corpus_census_and_clean(model_output)
        assert is_enumeration_query(marker, goal_text) is True
        assert "CORPUS_CENSUS" not in goal_text
