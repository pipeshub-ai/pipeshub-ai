from __future__ import annotations

import datetime
import uuid
from abc import ABC, abstractmethod

from pydantic import BaseModel, Field

"""Human feedback ingestion (Phase 5): a minimal store for human ratings
on completed runs, feeding the same learning loop as rubric grading
(eval/rubric.py) — either can gate skill promotion, or both together
(e.g. require a passing LLM grade AND no negative human feedback).
Follows the store-interface pattern already used throughout the harness
(TimelineStore, ApprovalStore, ...): an ABC plus an in-memory
implementation for tests/dev; a durable implementation can be added the
same way as any other store here.
"""


class HumanFeedback(BaseModel):
    feedback_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    run_id: str
    rating: int  # e.g. 1 (bad) - 5 (great)
    comment: str = ""
    submitted_at: str = Field(
        default_factory=lambda: datetime.datetime.now(datetime.timezone.utc).isoformat()
    )


class FeedbackStore(ABC):
    @abstractmethod
    async def add(self, feedback: HumanFeedback) -> None: ...

    @abstractmethod
    async def get_by_run(self, run_id: str) -> list[HumanFeedback]: ...


class InMemoryFeedbackStore(FeedbackStore):
    def __init__(self) -> None:
        self._feedback: list[HumanFeedback] = []

    async def add(self, feedback: HumanFeedback) -> None:
        self._feedback.append(feedback)

    async def get_by_run(self, run_id: str) -> list[HumanFeedback]:
        return [f for f in self._feedback if f.run_id == run_id]
