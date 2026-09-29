"""Building `Evidence` records: what a system showed its answering model.

The support judge checks correct answers against this text, so it must be
what the model actually read — captured after truncation, not before. Systems
that see their own prompt capture it at ask time; the rest (PipesHub, whose
stream names blocks not text, and runs recorded before capture existed) are
rebuilt afterwards from the vector store by an `EvidenceBuilder`.
"""

from __future__ import annotations

import hashlib
import logging
from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from benchmarks.harness.models import Evidence, EvidencePassage, Prediction, render_passages

logger = logging.getLogger(__name__)

EVIDENCE_FILE = "evidence.jsonl.gz"
# Above any answering context budget (~120k tokens), so the cap only bites
# on pathological captures; it keeps one record from dominating the sidecar.
MAX_EVIDENCE_CHARS = 500_000


def captured(passages: Sequence[EvidencePassage], source: str, *, reconstructed: bool = False) -> Evidence:
    """Evidence from passages, capped at `MAX_EVIDENCE_CHARS`. `chars` and
    `sha256` describe the uncapped text."""
    kept = [p for p in passages if p.text]
    full = render_passages(kept)
    if not full:
        return empty(source)
    status = "reconstructed" if reconstructed else "captured"
    digest = hashlib.sha256(full.encode()).hexdigest()
    if len(full) <= MAX_EVIDENCE_CHARS:
        return Evidence(status=status, source=source, passages=kept, chars=len(full), sha256=digest)
    bounded: list[EvidencePassage] = []
    room = MAX_EVIDENCE_CHARS
    for passage in kept:
        cost = len(passage.header) + len(passage.text) + 2
        if cost > room:
            text_room = room - len(passage.header) - 2
            if text_room > 0:
                bounded.append(passage.model_copy(update={"text": passage.text[:text_room]}))
            break
        bounded.append(passage)
        room -= cost
    return Evidence(
        status=status, source=source, passages=bounded, chars=len(full), sha256=digest, truncated=True,
    )


def empty(source: str) -> Evidence:
    return Evidence(status="empty", source=source, sha256=hashlib.sha256(b"").hexdigest())


def unavailable(source: str, reason: str, *, retryable: bool = False) -> Evidence:
    return Evidence(status="unavailable", source=source, reason=reason, retryable=retryable)


class EvidenceSourceError(Exception):
    """The store evidence is rebuilt from could not be read; retried later."""


class EvidenceBuilder(Protocol):
    def evidence_for(self, prediction: Prediction) -> Evidence: ...


@dataclass(frozen=True)
class UnavailableBuilder:
    """For a system whose evidence cannot be rebuilt in this run at all."""

    source: str
    reason: str

    def evidence_for(self, _prediction: Prediction) -> Evidence:
        return unavailable(self.source, self.reason)


class StoreBackedBuilder(ABC):
    """Rebuilds evidence from a store that may be down. After the first
    failure every later answer is marked unavailable at once instead of
    timing out again; the evidence stage retries them on the next resume."""

    source: str

    def __init__(self) -> None:
        self._down: str | None = None

    @abstractmethod
    def _build(self, prediction: Prediction) -> Evidence: ...

    def evidence_for(self, prediction: Prediction) -> Evidence:
        if self._down is not None:
            return unavailable(self.source, self._down, retryable=True)
        try:
            return self._build(prediction)
        except EvidenceSourceError as exc:
            self._down = f"vector store unreachable: {exc}"
            logger.warning("evidence: %s: %s; will be retried on the next resume", self.source, self._down)
            return unavailable(self.source, self._down, retryable=True)
