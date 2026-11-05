"""The record reranker contract.

A strategy ranks candidate records for a question and returns their ids,
best first. It may return fewer ids than it was given, or ids it was not
given; the pipeline keeps only the first ``top`` known ids, fills the rest in
the fused order and never adds a record. A strategy raises on failure; the
pipeline turns any exception (and the timeout) into the fused order.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

    from app.config.configuration_service import ConfigurationService


class RecordRerankError(Exception):
    """A strategy could not produce a ranking (no model, unusable output)."""


@dataclass(frozen=True)
class RecordCandidate:
    record_id: str
    """The virtual record id the search matched."""
    title: str
    text: str
    """Summary or best passage, already capped."""


@dataclass(frozen=True)
class RerankRequest:
    question: str
    candidates: Sequence[RecordCandidate]
    """In the fused order, best first."""
    top: int
    org_id: str


@dataclass(frozen=True)
class RerankerDeps:
    """What a strategy may need from the service that runs it."""

    config_service: ConfigurationService | None = None


class RecordReranker(ABC):
    name: str = ""

    @abstractmethod
    async def rank(self, request: RerankRequest) -> list[str]:
        """Candidate record ids, best first."""
