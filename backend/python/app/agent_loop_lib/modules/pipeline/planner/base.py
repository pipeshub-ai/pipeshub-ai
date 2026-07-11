from __future__ import annotations

import logging
from abc import ABC, abstractmethod

from pydantic import BaseModel

from app.agent_loop_lib.core.types import Confidence, Goal

logger = logging.getLogger(__name__)


class Plan(BaseModel):
    """A planner's output: the goal it was produced for, and the model's
    raw text response — never parsed into structured fields. Consumers
    that need the plan (`PlanExecuteLoop`, `create_plan`/`replan`/
    `critique_plan` tools) inject/forward `text` verbatim; the model's own
    downstream `write_todos` tool call is the structured channel for
    anything that needs to become actual todo items."""

    goal: Goal
    text: str


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


class Planner(ABC):
    @abstractmethod
    async def plan(self, goal: Goal) -> Plan: ...
