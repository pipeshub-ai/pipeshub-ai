"""Single home for the agent's user-facing confidence vocabulary.

Consolidates the three previously divergent representations:
1. The user-facing ``Very High | High | Medium | Low`` string emitted by
   the LLM's trailing confidence marker (``prompt_builder._ANSWER_CONFIDENCE``
   / ``streaming.parse_confidence_from_answer``).
2. The ``agent_loop_lib`` internal ``Confidence`` enum (``low/medium/high``)
   that ``task_complete`` writes onto ``AgentResult.confidence``.
3. The Node.js ``CONFIDENCE_LEVELS`` constants and the frontend
   ``ConfidenceLevel`` union type.

Rules enforced here:
- ``confidence_enabled()`` gates the feature behind
  ``PIPESHUB_ENABLE_CONFIDENCE`` (default **True**).  When False:
  - The ``_ANSWER_CONFIDENCE`` section is NOT injected into the system prompt.
  - ``FinalAnswerTool`` omits the ``confidence`` parameter.
  - ``AnswerFinalizer`` emits ``None`` for confidence in ``completion_data``.
- ``rubric_text()`` returns a deterministic first-row-matches rubric that is
  **task-goal-oriented**, not retrieval-specific.  The agent rates how
  confidently it accomplished the user's actual request — whether that is
  a search, a write action, code execution, or a conversational answer.
- ``reconcile()`` applies lightweight sanity caps (e.g. can't be "Very High"
  when a source was unavailable or nothing was cited in a retrieval task).
"""

from __future__ import annotations

import os
from collections.abc import Sequence


CONFIDENCE_LEVELS: tuple[str, ...] = ("Very High", "High", "Medium", "Low")

# Canonical mapping from the agent_loop_lib internal enum (lowercase) to
# the user-facing label.  task_complete populates AgentResult.confidence
# with the lowercase value; AnswerFinalizer maps it to the display label.
_INTERNAL_TO_DISPLAY: dict[str, str] = {
    "very_high": "Very High",
    "high":      "High",
    "medium":    "Medium",
    "low":       "Low",
    # task_complete uses three-level enum; treat high as "High" not "Very High"
    # (conservative — only explicit four-level sources can emit "Very High").
}


def confidence_enabled() -> bool:
    """Return True iff ``PIPESHUB_ENABLE_CONFIDENCE`` is ``"true"``.

    Defaults to **False** — confidence is opt-in.  Set the variable to
    ``"true"`` to enable the confidence trailer / FinalAnswerTool confidence
    parameter / frontend badge.
    """
    return os.getenv("PIPESHUB_ENABLE_CONFIDENCE", "true").strip().lower() == "true"


def rubric_text() -> str:
    """A deterministic first-row-matches rubric.

    The model picks the FIRST row whose condition is true.  The rubric is
    task-goal-oriented: it applies whether the user asked for a search,
    a write action, code execution, or a conversational answer.
    """
    return (
        "CONFIDENCE — pick the FIRST row that matches:\n"
        "  Low        the task could not be completed, OR the answer relies on\n"
        "             your own knowledge rather than tool results or retrieved content\n"
        "  Medium     the task was only partially completed, OR a needed source/tool\n"
        "             was unavailable, OR the results were conflicting or ambiguous\n"
        "  High       the task was fully completed using tool results or retrieved\n"
        "             content; minor gaps possible but the core request is addressed\n"
        "  Very High  the task was fully completed, every claim is grounded in tool\n"
        "             results, all relevant sources agreed, nothing was unavailable"
    )


def normalize(level: str | None) -> str | None:
    """Canonical-case the level string.

    Accepts ``"very high"``, ``"Very High"``, ``"VERY HIGH"``,
    ``"high"`` etc.  Returns ``None`` for unrecognised values.
    """
    if level is None:
        return None
    stripped = level.strip()
    lower = stripped.lower()
    for label in CONFIDENCE_LEVELS:
        if lower == label.lower():
            return label
    mapped = _INTERNAL_TO_DISPLAY.get(lower.replace(" ", "_"))
    if mapped:
        return mapped
    return None


def reconcile(
    level: str | None,
    *,
    unavailable_sources: Sequence[str],
    citation_count: int,
    is_retrieval_task: bool = True,
) -> str | None:
    """Return the final confidence label, capping optimistic values.

    Rules (applied only when they are relevant to the task type):
    - If ``level`` is ``None`` or unrecognised, return ``None``.
    - If any source was unavailable → cap at ``"Medium"`` (can't claim
      full success when something was missing).
    - For retrieval tasks: if the answer has zero citations AND level is
      ``"Very High"`` or ``"High"`` → cap at ``"Medium"`` (a retrieval
      answer with no citations can't be high confidence).
    - Otherwise return the normalised level unchanged.
    """
    normalised = normalize(level)
    if normalised is None:
        return None

    if unavailable_sources:
        if normalised in ("Very High", "High"):
            return "Medium"

    if is_retrieval_task and citation_count == 0 and normalised in ("Very High", "High"):
        return "Medium"

    return normalised


__all__ = [
    "CONFIDENCE_LEVELS",
    "confidence_enabled",
    "rubric_text",
    "normalize",
    "reconcile",
]
