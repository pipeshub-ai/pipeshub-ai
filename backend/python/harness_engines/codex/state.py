"""Codex state codec: the thread's rollout file in a versioned envelope.

The rollout (``$CODEX_HOME/sessions/YYYY/MM/DD/rollout-<ts>-<threadId>.jsonl``)
is what Codex resumes a thread from: copied alone into an empty ``CODEX_HOME``
under its own relative path, ``thread/resume {threadId}`` restores the history,
also for 0.160's paginated threads (E8 FB-1). The SQLite indexes beside it are
rebuilt and are not part of the state.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from harness_core.engines.errors import EngineStateIncompatible, StateHoldsSecret
from harness_core.engines.events import EngineState
from harness_core.engines.jsonview import as_object, as_str

if TYPE_CHECKING:
    from collections.abc import Iterable

    from harness_core.engines.types import EngineVersion, JsonValue

SCHEMA_VERSION = 1
FORMAT = "codex-rollout"
_ROLLOUT = re.compile(r"^\d{4}/\d{2}/\d{2}/rollout-[0-9A-Za-z:T.-]+-(?P<thread>[0-9a-f-]{36})\.jsonl$")


@dataclass(frozen=True, slots=True)
class CodexState:
    thread_id: str
    # Relative to ``$CODEX_HOME/sessions``.
    path: str
    rollout: bytes


def rollout_path(path: str, thread_id: str) -> str | None:
    """``path`` if it names ``thread_id``'s rollout under ``sessions/`` (no traversal), else ``None``."""
    found = _ROLLOUT.match(path)
    return path if found is not None and found.group("thread") == thread_id else None


def encode_state(
    engine: EngineVersion, thread_id: str, path: str, rollout: bytes, *, secrets: Iterable[str] = ()
) -> EngineState:
    text = rollout.decode("utf-8")
    for secret in secrets:
        if secret and secret in text:
            raise StateHoldsSecret("engine state holds a credential")
    if rollout_path(path, thread_id) is None:
        raise ValueError("not this thread's rollout")
    envelope: dict[str, JsonValue] = {"format": FORMAT, "threadId": thread_id, "path": path, "rollout": text}
    payload = json.dumps(envelope, sort_keys=True, separators=(",", ":")).encode()
    return EngineState(engine=engine, schema_version=SCHEMA_VERSION, payload=payload)


def decode_state(state: EngineState, expected: EngineVersion) -> CodexState:
    if state.engine != expected:
        raise EngineStateIncompatible("state was saved by another engine version")
    if state.schema_version != SCHEMA_VERSION:
        raise EngineStateIncompatible(f"unknown Codex state schema {state.schema_version}")
    try:
        envelope = as_object(json.loads(state.payload))
    except ValueError as exc:
        raise EngineStateIncompatible("Codex state is not JSON", reason="state_corrupt") from exc
    thread_id = as_str(envelope.get("threadId"))
    path = rollout_path(as_str(envelope.get("path")), thread_id)
    rollout = envelope.get("rollout")
    if as_str(envelope.get("format")) != FORMAT or not thread_id or path is None or not isinstance(rollout, str):
        raise EngineStateIncompatible("not a Codex rollout envelope", reason="state_corrupt")
    return CodexState(thread_id=thread_id, path=path, rollout=rollout.encode("utf-8"))


__all__ = [
    "FORMAT",
    "SCHEMA_VERSION",
    "CodexState",
    "StateHoldsSecret",
    "decode_state",
    "encode_state",
    "rollout_path",
]
