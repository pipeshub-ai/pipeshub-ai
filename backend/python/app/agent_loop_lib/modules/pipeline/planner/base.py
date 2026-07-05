from __future__ import annotations

import logging
from abc import ABC, abstractmethod

from pydantic import BaseModel, field_validator

from app.agent_loop_lib.core.types import Confidence, Goal

logger = logging.getLogger(__name__)


class Phase(BaseModel):
    name: str
    description: str
    tools: list[str] = []

    @field_validator("tools", mode="before")
    @classmethod
    def _coerce_none(cls, v: object) -> object:
        return v if v is not None else []


class Plan(BaseModel):
    goal: Goal
    phases: list[Phase]
    confidence: Confidence


def parse_confidence(raw: object) -> Confidence:
    """Best-effort parse of an LLM-produced confidence value.

    LLMs sometimes ignore JSON-schema ``enum`` constraints and return a
    numeric score (``"0.90"``, ``0.85``) or a capitalised variant
    (``"High"``) instead of the required ``"high"``/``"medium"``/``"low"``.
    Crashing on those is worse than degrading gracefully.
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
