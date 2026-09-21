"""S0 closed-book: the question alone — the model's parametric knowledge and
the contamination floor every retrieval system is measured against."""

from __future__ import annotations

from benchmarks.frames.models import AskItem
from benchmarks.frames.systems.baselines.answering import BaselineAnswerer, ContextDocument


class ClosedBookAnswerer(BaselineAnswerer):
    def documents_for(self, item: AskItem) -> list[ContextDocument]:
        return []
