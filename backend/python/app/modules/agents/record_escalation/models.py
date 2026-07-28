"""
Data models for record-escalation: candidate selection, fetch planning, and
judge verdicts. All frozen dataclasses — no I/O, no LLM, no thresholds.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class FetchCandidate:
    """A single record the model may want to fetch in full."""

    record_id: str
    record_name: str
    topics: tuple[str, ...]
    summary: str
    blocks_held: int
    blocks_total: int


@dataclass(frozen=True)
class FetchPlan:
    """Result of build_candidates: what to offer and why exclusions happened."""

    candidates: tuple[FetchCandidate, ...]
    # (record_id, reason) pairs — for logging / auditing only, never shown to the model.
    excluded: tuple[tuple[str, str], ...]

    @property
    def has_candidates(self) -> bool:
        return len(self.candidates) > 0


@dataclass(frozen=True)
class FetchVerdict:
    """
    Judge output. `needed` may be empty (that is the veto).
    `judged=False` means the judge could not run; callers must treat it as
    silence — failing open toward *not* fetching.
    """

    # (record_id, reason) pairs — MAY be empty.
    needed: tuple[tuple[str, str], ...]
    judged: bool
