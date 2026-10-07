"""Shared `runId` validation for every request body that carries one —
`ChatQuery` (stream, both `chatbot.py` and `agent.py`) and `CancelRunRequest`
(`POST /chat/cancel`). Both must agree on what counts as valid so a value
a stream endpoint accepted is always a value the cancel endpoint accepts
back."""

from __future__ import annotations

import uuid

__all__ = ["validate_run_id"]


def validate_run_id(value: str | None) -> str | None:
    """`None` (absent) is valid on the STREAM side — the agent loop falls
    back to generating one (see `stream_bridge.py`/`bridge.py`). The cancel
    endpoint's `runId` field is not optional, so `None` never reaches this
    validator there. Any non-`None` value must be a valid UUID string."""
    if value is None:
        return value
    try:
        uuid.UUID(value)
    except (ValueError, AttributeError, TypeError) as exc:
        raise ValueError(f"Invalid runId {value!r}: must be a UUID.") from exc
    return value
