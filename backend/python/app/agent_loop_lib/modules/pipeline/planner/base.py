from __future__ import annotations

import logging
import re
from abc import ABC, abstractmethod

from pydantic import BaseModel, Field

from app.agent_loop_lib.core.scope import StateSlot
from app.agent_loop_lib.core.types import Confidence, Goal

logger = logging.getLogger(__name__)


class PlanStep(BaseModel):
    """One step in a structured execution plan.

    Used by ``create_plan`` (when the LLM provides a ``steps`` array)
    and consumed by ``OrchestratorLoop``'s programmatic dispatch to build
    synthetic ``spawn_agent`` calls without LLM-mediated translation.

    ``boundaries`` and ``output_format`` are optional but strongly steered
    toward in the Phase 1 planning instructions (see ``orchestrator.py``):
    the documented deep-mode failure mode is duplicate/gapped sub-tasks
    from steps whose scope was implicit rather than stated — a step that
    only carries a one-line ``description`` gives its sub-agent (which
    never sees its SIBLING steps) nothing to disambiguate an overlapping
    scope from, or to know what shape of answer the next step downstream
    expects. Both fields get folded into the synthetic spawn's goal text
    by ``_programmatic_dispatch`` (``orchestrator.py``), not just carried
    here for display.
    """

    id: str
    description: str
    domain: str
    tool_names: list[str] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)
    boundaries: list[str] = Field(default_factory=list)
    output_format: str | None = None


class Plan(BaseModel):
    """A planner's output: the goal it was produced for, and the model's
    raw text response — never parsed into structured fields. Consumers
    that need the plan (`PlanExecuteLoop`, `create_plan`/`replan`/
    `critique_plan` tools) inject/forward `text` verbatim; the model's own
    downstream `write_todos` tool call is the structured channel for
    anything that needs to become actual todo items.

    When ``steps`` is populated (structured plan from the orchestrator),
    ``text`` is a human-readable summary auto-generated from the steps.

    ``confidence`` is deliberately never asked of the model as a separate
    structured field on the free-form path — see ``extract_trailing_
    confidence()`` below for why, and ``create_plan.py``'s ``confidence``
    tool parameter for the structured-steps path (already function-call
    args, so a one-field ask there is not the same cost as forcing a
    whole-plan JSON schema).
    """

    goal: Goal
    text: str
    steps: list[PlanStep] | None = None
    confidence: Confidence | None = None


STRUCTURED_PLAN_SLOT: StateSlot[Plan | None] = StateSlot(
    key="planner.structured_plan",
    default_factory=lambda: None,
)


def parse_confidence(raw: object) -> Confidence:
    """Best-effort parse of an LLM-produced confidence value.

    LLMs sometimes ignore JSON-schema ``enum`` constraints and return a
    numeric score (``"0.90"``, ``0.85``) or a capitalised variant
    (``"High"``) instead of the required ``"high"``/``"medium"``/``"low"``.
    Crashing on those is worse than degrading gracefully.

    Still used by `PlanCritic`/`ResultCritic` (and the `require_critique`
    hook), whose OUTPUTS remain deliberately structured — `passed` drives
    verify-retry control flow, unlike a `Plan`'s free-form `text`.
    """
    if isinstance(raw, Confidence):
        return raw
    text = str(raw).strip().lower()
    try:
        return Confidence(text)
    except ValueError:
        pass
    try:
        score = float(text)
        if score >= 0.7:
            return Confidence.HIGH
        if score >= 0.4:
            return Confidence.MEDIUM
        return Confidence.LOW
    except (ValueError, TypeError):
        pass
    logger.warning("Unrecognised confidence value %r, defaulting to MEDIUM", raw)
    return Confidence.MEDIUM


# Matches a line like "Confidence: high", "**Confidence:** Medium", or
# "confidence : LOW" — deliberately lenient about markdown bold markers and
# capitalisation, since this is a convention asked of the model in a system
# prompt (`planner/default.py`'s `_SYSTEM_PROMPT`), not a schema it's forced
# to emit through function-calling.
_CONFIDENCE_LINE_RE = re.compile(r"(?im)^\s*\**confidence\**\s*:\s*(.+?)\s*$")


def extract_trailing_confidence(text: str) -> Confidence:
    """Extract a ``Confidence: low|medium|high`` convention line from
    free-form markdown plan text, instead of forcing the model to produce
    a structured JSON plan just to carry one enum value (models degrade
    when forced into strict JSON generation for open-ended content like a
    plan — see the module-level discussion in ``create_plan.py``).

    Takes the LAST matching line in the text (the model is asked to put it
    at the very end, but this stays forgiving of stray commentary after
    it). Falls back to ``Confidence.MEDIUM`` — same default
    ``parse_confidence()`` already uses for an unrecognised value — when no
    line is found at all, so a non-compliant model still gets today's
    "let it through" behavior rather than a new silent block.

    Shared by ``DefaultPlanner.plan()`` (to populate ``Plan.confidence``)
    and ``supervisor_confidence_gate()`` (to read confidence straight off
    the tool result string `create_plan` actually returns) — one
    extraction rule, used identically by the producer and the gate that
    consumes it.
    """
    matches = list(_CONFIDENCE_LINE_RE.finditer(text))
    if not matches:
        return Confidence.MEDIUM
    return parse_confidence(matches[-1].group(1))


class Planner(ABC):
    @abstractmethod
    async def plan(self, goal: Goal) -> Plan: ...
