"""CS-1..CS-3 (E9): the session transcript in a versioned envelope."""

from __future__ import annotations

import dataclasses
import json

import pytest

from harness_core.engines.errors import EngineStateIncompatible, StateHoldsSecret
from harness_core.engines.events import EngineState
from harness_core.engines.types import EngineVersion
from harness_engines.claude_code.state import (
    FORMAT,
    SCHEMA_VERSION,
    decode_state,
    encode_state,
)

CLAUDE = EngineVersion("claude_code", "2.1.289", "sha256:" + "a" * 64)
SESSION = "8c1281a9-3593-454e-a515-a6b4e0235fcc"
TRANSCRIPT = b'{"type":"user","message":{"role":"user","content":"say alpha \\u00e9"}}\n'


def test_cs1_the_envelope_round_trips() -> None:
    state = encode_state(CLAUDE, SESSION, TRANSCRIPT)
    assert (state.engine, state.schema_version, SCHEMA_VERSION) == (CLAUDE, 1, 1)
    envelope = json.loads(state.payload)
    assert set(envelope) == {"format", "sessionId", "transcript"} and envelope["format"] == FORMAT
    restored = decode_state(state, CLAUDE)
    assert (restored.session_id, restored.transcript) == (SESSION, TRANSCRIPT)


@pytest.mark.parametrize("change", ["version", "digest", "kind", "schema"])
def test_cs2_state_of_another_engine_build_is_refused(change: str) -> None:
    state = encode_state(CLAUDE, SESSION, TRANSCRIPT)
    if change == "version":
        state = dataclasses.replace(state, engine=dataclasses.replace(CLAUDE, version="2.1.290"))
    elif change == "digest":
        state = dataclasses.replace(state, engine=dataclasses.replace(CLAUDE, binary_digest="sha256:" + "b" * 64))
    elif change == "kind":
        state = dataclasses.replace(state, engine=dataclasses.replace(CLAUDE, kind="codex"))
    else:
        state = dataclasses.replace(state, schema_version=2)
    with pytest.raises(EngineStateIncompatible) as refused:
        decode_state(state, CLAUDE)
    assert refused.value.reason_code == "state_incompatible"


@pytest.mark.parametrize(
    "payload",
    [
        b"not json",
        json.dumps({"format": "codex-rollout", "sessionId": SESSION, "transcript": ""}).encode(),
        json.dumps({"format": FORMAT, "sessionId": "../../etc/passwd", "transcript": ""}).encode(),
        json.dumps({"format": FORMAT, "sessionId": SESSION.upper(), "transcript": ""}).encode(),
        json.dumps({"format": FORMAT, "sessionId": SESSION, "transcript": 3}).encode(),
        json.dumps([1]).encode(),
    ],
)
def test_cs2_a_corrupt_envelope_is_refused(payload: bytes) -> None:
    with pytest.raises(EngineStateIncompatible) as refused:
        decode_state(EngineState(engine=CLAUDE, schema_version=SCHEMA_VERSION, payload=payload), CLAUDE)
    assert refused.value.reason_code == "state_corrupt"


def test_cs3_a_transcript_holding_a_secret_is_never_saved() -> None:
    with pytest.raises(StateHoldsSecret):
        encode_state(CLAUDE, SESSION, TRANSCRIPT + b"token-123", secrets=("token-123",))
    with pytest.raises(ValueError):
        encode_state(CLAUDE, "not-a-session", TRANSCRIPT)
