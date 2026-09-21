"""Failure signatures for wrong answers of systems that expose a retrieval
trace. The first matching rule wins and the order is part of the contract
(unit-tested): it moves from "the run was broken" to "retrieval never found
it", so each bucket points at one kind of fix.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

# Fewer tool waves than this, with no failed searches, means the loop quit
# while it still had budget.
STOPPED_EARLY_MAX_WAVES = 10


class Failure(StrEnum):
    SYSTEM_ERROR = "system_error"
    POLICY_VIOLATION = "policy_violation"
    ABSTAINED = "abstained"
    REASONING_MISS = "F6_reasoning_miss"
    CONTEXT_OVERFLOW = "F3_context_overflow"
    TURN_BUDGET = "F2_turn_budget"
    LINKS_UNUSED = "F5_links_unused"
    STOPPED_EARLY = "F1_stopped_early"
    NEVER_SURFACED = "F4_entity_never_surfaced"
    OTHER = "other_retrieval_miss"


@dataclass(frozen=True)
class FailureEvidence:
    correct: bool
    has_error: bool
    policy_violation: bool
    strict_label: str | None
    context_recall: float | None
    tool_waves: int
    # Reported by the run itself (`run_usage`), never mirrored from the
    # system under test's source: a copied constant silently goes stale the
    # moment the cap moves, and tool waves are not turns — one turn can
    # carry several parallel calls.
    hit_turn_cap: bool
    failed_tool_calls: int
    # Gold articles missing from context, and what is known about them:
    missing_gold: frozenset[str]
    surfaced: frozenset[str]
    unrendered_fetches: frozenset[str]
    linked_from_context: frozenset[str]


def classify(e: FailureEvidence) -> Failure | None:
    if e.has_error:
        return Failure.SYSTEM_ERROR
    if e.policy_violation:
        return Failure.POLICY_VIOLATION
    if e.correct:
        return None
    if e.strict_label == "NOT_ATTEMPTED":
        return Failure.ABSTAINED
    if e.context_recall is None or e.context_recall >= 1.0:
        return Failure.REASONING_MISS
    if e.missing_gold & e.unrendered_fetches:
        return Failure.CONTEXT_OVERFLOW
    if e.hit_turn_cap:
        return Failure.TURN_BUDGET
    if e.missing_gold & e.linked_from_context:
        return Failure.LINKS_UNUSED
    if e.tool_waves <= STOPPED_EARLY_MAX_WAVES and e.failed_tool_calls == 0:
        return Failure.STOPPED_EARLY
    if not e.missing_gold & e.surfaced:
        return Failure.NEVER_SURFACED
    return Failure.OTHER
