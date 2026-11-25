"""Claude Code state codec: the session's transcript in a versioned envelope.

The transcript (``$CLAUDE_CONFIG_DIR/projects/<cwd slug>/<session id>.jsonl``)
is what ``--resume <session id>`` restores a session from: copied alone into an
empty config directory, the history comes back under the same session id
(verified on 2.1.289). Nothing else under the config directory is state.
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
FORMAT = "claude-code-transcript"
_SESSION = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


@dataclass(frozen=True, slots=True)
class ClaudeState:
    session_id: str
    transcript: bytes


def valid_session_id(value: str) -> bool:
    """A session id is a UUID: it names the transcript file, so nothing else may reach a path."""
    return _SESSION.match(value) is not None


def encode_state(
    engine: EngineVersion, session_id: str, transcript: bytes, *, secrets: Iterable[str] = ()
) -> EngineState:
    text = transcript.decode("utf-8")
    for secret in secrets:
        if secret and secret in text:
            raise StateHoldsSecret("engine state holds a credential")
    if not valid_session_id(session_id):
        raise ValueError("not a Claude Code session id")
    envelope: dict[str, JsonValue] = {"format": FORMAT, "sessionId": session_id, "transcript": text}
    payload = json.dumps(envelope, sort_keys=True, separators=(",", ":")).encode()
    return EngineState(engine=engine, schema_version=SCHEMA_VERSION, payload=payload)


def decode_state(state: EngineState, expected: EngineVersion) -> ClaudeState:
    if state.engine != expected:
        raise EngineStateIncompatible("state was saved by another engine version")
    if state.schema_version != SCHEMA_VERSION:
        raise EngineStateIncompatible(f"unknown Claude Code state schema {state.schema_version}")
    try:
        envelope = as_object(json.loads(state.payload))
    except ValueError as exc:
        raise EngineStateIncompatible("Claude Code state is not JSON", reason="state_corrupt") from exc
    session_id = as_str(envelope.get("sessionId"))
    transcript = envelope.get("transcript")
    if as_str(envelope.get("format")) != FORMAT or not valid_session_id(session_id) or not isinstance(transcript, str):
        raise EngineStateIncompatible("not a Claude Code transcript envelope", reason="state_corrupt")
    return ClaudeState(session_id=session_id, transcript=transcript.encode("utf-8"))


__all__ = ["FORMAT", "SCHEMA_VERSION", "ClaudeState", "decode_state", "encode_state", "valid_session_id"]
