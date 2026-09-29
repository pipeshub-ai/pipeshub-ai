"""Parsing judge replies. Strict about the verdict token, tolerant of the
quoting/markdown judges wrap it in — public FRAMES harnesses have mis-scored
runs by matching the first TRUE/FALSE anywhere in the explanation."""

from __future__ import annotations

import re

_DECISION = re.compile(r"(?im)^[\W_]*decision[\W_]*:?[\W_]*(TRUE|FALSE)\b")
_SIMPLEQA_LETTER = re.compile(r"\b([ABC])\b")
_SUPPORT = re.compile(r"(?im)^[\W_]*support[\W_]*:?[\W_]*(FULL|PARTIAL|NONE)\b")
_EVIDENCE_SUPPORT = re.compile(
    # Anchored at line end, so an echo of the instruction line
    # ("SUPPORTED, PARTIAL or UNSUPPORTED") is not read as a verdict.
    r"(?im)^[\W_]*evidence[\W_]+support[\W_]*:?[\W_]*(SUPPORTED|PARTIAL|UNSUPPORTED)[\W_]*$",
)
_REASON = re.compile(r"(?im)^[\W_]*reason[\W_]*:[\W_]*(.+?)\s*$")
_REASON_CHARS = 300

SIMPLEQA_LABELS = {"A": "CORRECT", "B": "INCORRECT", "C": "NOT_ATTEMPTED"}
SUPPORT_SCORES = {"FULL": 1.0, "PARTIAL": 0.5, "NONE": 0.0}


def parse_frames_decision(text: str) -> str | None:
    """The last `Decision:` line's TRUE/FALSE, or None."""
    matches = _DECISION.findall(text or "")
    return matches[-1].upper() if matches else None


def parse_simpleqa_grade(text: str) -> str | None:
    match = _SIMPLEQA_LETTER.search((text or "").strip())
    return SIMPLEQA_LABELS[match.group(1)] if match else None


def parse_support(text: str) -> float | None:
    matches = _SUPPORT.findall(text or "")
    return SUPPORT_SCORES[matches[-1].upper()] if matches else None


def parse_evidence_support(text: str) -> str | None:
    """The last `Evidence support:` line's label, or None."""
    matches = _EVIDENCE_SUPPORT.findall(text or "")
    return matches[-1].upper() if matches else None


def parse_reason(text: str) -> str:
    matches = _REASON.findall(text or "")
    return matches[-1][:_REASON_CHARS] if matches else ""
