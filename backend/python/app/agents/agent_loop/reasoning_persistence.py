"""Reasoning/thinking persistence gate (Phase 1f of the AG-UI migration).

Chain-of-thought can be long and provider-specific, so persistence is
opt-OUT via an env var (default on, per product decision — Claude/ChatGPT/
Cursor all persist a "thinking" transcript by default): even when on,
per-turn content is truncated before it reaches `completion_data["reasoning"]`/
`completion_data["parts"]` (and, from there, Node's message document — see
the migration plan's Phase 1f/2c). Live streaming of `REASONING_MESSAGE_*`
events to the frontend is UNAFFECTED by this flag — it only gates what gets
written to durable storage.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.agents.agent_loop.protocol.transcript_collector import MessagePart

# Not a customer-facing setting (same rationale as `PIPESHUB_USE_AGENT_LOOP`/
# `PIPESHUB_USE_COMPOSED_AGENTS`) — a deployment-level opt-out, not an
# admin-UI toggle.
_MAX_REASONING_CHARS = 4000


def reasoning_persistence_enabled() -> bool:
    """Defaults to enabled — set `PIPESHUB_PERSIST_REASONING=false` to opt
    a deployment out of storing chain-of-thought in Mongo."""
    return os.getenv("PIPESHUB_PERSIST_REASONING", "true").strip().lower() != "false"


def build_reasoning_payload(reasoning_turns: list[dict[str, Any]]) -> list[dict[str, Any]] | None:
    """`None` (omit the field entirely) unless persistence is enabled AND
    the run actually produced reasoning — additive field, so omitting it
    is indistinguishable from an older client to any downstream consumer."""
    if not reasoning_turns or not reasoning_persistence_enabled():
        return None
    return [
        {**turn, "content": str(turn.get("content", ""))[:_MAX_REASONING_CHARS]}
        for turn in reasoning_turns
    ]


def filter_reasoning_parts(parts: list["MessagePart"]) -> list["MessagePart"]:
    """Applies the SAME enable/truncate policy `build_reasoning_payload`
    uses, but to the parts-transcript shape (recursing into `sub_agent`
    parts' own nested `parts`) — `reasoning` parts are dropped entirely
    when persistence is disabled, otherwise truncated to
    `_MAX_REASONING_CHARS`. Called once, from `respond.py`, right before
    `completion_data["parts"]` is set."""
    enabled = reasoning_persistence_enabled()
    result: list["MessagePart"] = []
    for part in parts:
        if part.get("type") == "reasoning":
            if not enabled:
                continue
            part = {**part, "content": str(part.get("content", ""))[:_MAX_REASONING_CHARS]}
        elif part.get("type") == "sub_agent":
            part = {**part, "parts": filter_reasoning_parts(part.get("parts", []))}
        result.append(part)
    return result


__all__ = ["reasoning_persistence_enabled", "build_reasoning_payload", "filter_reasoning_parts"]
