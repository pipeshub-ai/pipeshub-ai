"""Ask User Tool Improvement Plan, Testing Plan item 6: a small eval
dataset for the ask-vs-act DECISION `intent.parse_intent_and_route()`
makes — not whether the resulting question/answer is well-formed (that's
`test_intrim_tools_ask_user_question.py`), but whether the model chooses to
ask AT ALL for a given query.

Deliberately narrow, mirroring `decomposition_queries.py`'s scope: this is
NOT an end-to-end eval of question quality (option grounding, multiSelect
correctness, ...) — see `hooks/ask_user_quality.py`'s production logging
for that signal at scale. This dataset only pins the binary ask/act call,
split evenly between queries the plan's `_CLARIFY_INSTRUCTIONS` explicitly
DOES want to trigger (2+ incompatible targets, or a bare fragment) and
queries it explicitly must NOT (a vague-but-searchable topic, or a clear
single-target request) — the exact boundary weakness #1 in the plan
(before Phase 1) reported as broken.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AskActEvalQuery:
    id: str
    query: str
    expect_ask: bool
    """True when `_CLARIFY_INSTRUCTIONS` should trigger (either tier) for
    this query; False when the model should answer/act instead."""
    notes: str = ""


ASK_ACT_EVAL_QUERIES: tuple[AskActEvalQuery, ...] = (
    # -- Expected to ASK (moderate: clear topic, 2+ incompatible targets) --
    AskActEvalQuery(
        id="update_the_ticket",
        query="Update the ticket with the new deadline.",
        expect_ask=True,
        notes="Several tickets plausibly live in a real conversation with nothing narrowing which one.",
    ),
    AskActEvalQuery(
        id="send_it_to_the_team",
        query="Send the summary to the team.",
        expect_ask=True,
        notes="'The team' could resolve to 2+ channels/DLs with nothing in context to pick one.",
    ),
    AskActEvalQuery(
        id="cancel_the_meeting",
        query="Cancel the meeting.",
        expect_ask=True,
        notes="No meeting name/time given and multiple could be live — a write action on the wrong one is costly.",
    ),
    # -- Expected to ASK (fatal: no topic/action at all) --
    AskActEvalQuery(
        id="do_it",
        query="Do it.",
        expect_ask=True,
        notes="Bare fragment with no antecedent — fatal tier.",
    ),
    AskActEvalQuery(
        id="bare_yes",
        query="Yes.",
        expect_ask=True,
        notes="No prior turn to resolve 'yes' against in this dataset's isolated single-turn queries.",
    ),
    AskActEvalQuery(
        id="empty_fragment",
        query="that thing from before",
        expect_ask=True,
        notes="No conversation history in this eval to resolve the reference against.",
    ),
    # -- Expected to ACT (vague-but-searchable — must NOT ask) --
    AskActEvalQuery(
        id="tell_me_about_pricing",
        query="Tell me about the new pricing.",
        expect_ask=False,
        notes="Broad-but-answerable topic — retrieval first, partial results beat a question.",
    ),
    AskActEvalQuery(
        id="whats_our_pto_policy",
        query="What is our company's PTO policy?",
        expect_ask=False,
        notes="Single clear internal-knowledge lookup, no competing target.",
    ),
    AskActEvalQuery(
        id="summarize_onboarding_docs",
        query="Summarize our onboarding documentation.",
        expect_ask=False,
        notes="Clear single topic — a broad search surface, not an ambiguous target choice.",
    ),
    # -- Expected to ACT (clear single target) --
    AskActEvalQuery(
        id="find_q3_revenue_report",
        query="Find the Q3 revenue report.",
        expect_ask=False,
        notes="Specific, searchable document reference — no competing targets named.",
    ),
    AskActEvalQuery(
        id="list_open_prs_assigned_to_me",
        query="List all open pull requests assigned to me.",
        expect_ask=False,
        notes="Self-scoped (assigned to me) — no ambiguity about which PRs.",
    ),
    AskActEvalQuery(
        id="whats_my_calendar_tomorrow",
        query="What's on my calendar tomorrow?",
        expect_ask=False,
        notes="Single well-defined lookup with an explicit time range.",
    ),
)


def query_by_id(query_id: str) -> AskActEvalQuery:
    by_id = {q.id: q for q in ASK_ACT_EVAL_QUERIES}
    if query_id not in by_id:
        raise KeyError(f"No eval query with id {query_id!r}. Known ids: {sorted(by_id)}")
    return by_id[query_id]


__all__ = ["ASK_ACT_EVAL_QUERIES", "AskActEvalQuery", "query_by_id"]
