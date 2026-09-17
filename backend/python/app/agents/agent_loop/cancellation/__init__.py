"""Cooperative cancellation for agent-loop runs (Stop Generation, Phase
3a) — see `registry.py`'s module docstring for the full design.
"""

from __future__ import annotations

from app.agents.agent_loop.cancellation.factory import build_run_cancellation_registry
from app.agents.agent_loop.cancellation.in_process import (
    InProcessRunCancellationRegistry,
)
from app.agents.agent_loop.cancellation.kv_backed import KVBackedRunCancellationRegistry
from app.agents.agent_loop.cancellation.registry import (
    CancelOutcome,
    RunCancellationRegistry,
    RunOwner,
)

__all__ = [
    "CancelOutcome",
    "InProcessRunCancellationRegistry",
    "KVBackedRunCancellationRegistry",
    "RunCancellationRegistry",
    "RunOwner",
    "build_run_cancellation_registry",
]
