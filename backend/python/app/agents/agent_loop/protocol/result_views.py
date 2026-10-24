"""How much of one reply's tool-result views (`Tool.result_view`) is sent and kept.

A view is stored on its tool-call part, inside the conversation's document (16 MB for the whole
conversation), so a reply that calls many tools keeps views only up to a small budget.
`AGUIEventEmitter` and `TranscriptCollector` each hold one and see the same events in the same
order, so what is shown live is what is shown after a reload.
"""
from __future__ import annotations

from typing import Any, Optional

from app.agents.actions.util.result_view import MAX_VIEW_BYTES, view_bytes

MAX_VIEW_BYTES_PER_REPLY = 16_000


class ResultViewBudget:
    def __init__(self) -> None:
        self._used = 0

    def admit(self, view: Any) -> Optional[dict[str, Any]]:  # noqa: ANN401
        """`view` when it fits what is left, else None."""
        if not isinstance(view, dict):
            return None
        try:
            size = view_bytes(view)
        except (TypeError, ValueError):
            return None
        if size > MAX_VIEW_BYTES or self._used + size > MAX_VIEW_BYTES_PER_REPLY:
            return None
        self._used += size
        return view
