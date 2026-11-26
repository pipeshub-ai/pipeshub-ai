"""CS-1..CS-3 (E8): Codex state is the thread's rollout in a versioned envelope."""

from __future__ import annotations

import dataclasses
import json

import pytest

from harness_core.engines.errors import EngineStateIncompatible
from harness_core.engines.types import EngineVersion
from harness_engines.codex.state import (
    FORMAT,
    SCHEMA_VERSION,
    StateHoldsSecret,
    decode_state,
    encode_state,
    rollout_path,
)

CODEX = EngineVersion("codex", "0.160.0", "sha256:" + "d" * 64)
THREAD = "01a102ba-7a18-77a1-90cd-37d11a4ca68d"
PATH = f"2026/10/03/rollout-2026-10-03T17-05-37-{THREAD}.jsonl"
ROLLOUT = b'{"type":"session_meta","payload":{"id":"x"}}\n{"type":"response_item","payload":{"text":"\xc3\xa9"}}\n'


def test_cs1_the_envelope_round_trips() -> None:
    state = encode_state(CODEX, THREAD, PATH, ROLLOUT)
    assert (state.engine, state.schema_version) == (CODEX, SCHEMA_VERSION)
    envelope = json.loads(state.payload)
    assert (envelope["format"], envelope["threadId"], envelope["path"]) == (FORMAT, THREAD, PATH)
    restored = decode_state(state, CODEX)
    assert (restored.thread_id, restored.path, restored.rollout) == (THREAD, PATH, ROLLOUT)


@pytest.mark.parametrize("change", ["version", "digest", "kind", "schema"])
def test_cs2_state_of_another_engine_build_is_refused(change: str) -> None:
    state = encode_state(CODEX, THREAD, PATH, ROLLOUT)
    if change == "version":
        state = dataclasses.replace(state, engine=dataclasses.replace(CODEX, version="0.161.0"))
    elif change == "digest":
        state = dataclasses.replace(state, engine=dataclasses.replace(CODEX, binary_digest="sha256:" + "e" * 64))
    elif change == "kind":
        state = dataclasses.replace(state, engine=dataclasses.replace(CODEX, kind="opencode"))
    else:
        state = dataclasses.replace(state, schema_version=SCHEMA_VERSION + 1)
    with pytest.raises(EngineStateIncompatible) as refused:
        decode_state(state, CODEX)
    assert refused.value.reason_code == "state_incompatible"


@pytest.mark.parametrize(
    "envelope",
    [
        b"not json",
        json.dumps({"format": "opencode-export", "threadId": THREAD, "path": PATH, "rollout": ""}).encode(),
        json.dumps({"format": FORMAT, "threadId": THREAD, "path": f"../../{PATH}", "rollout": ""}).encode(),
        json.dumps({"format": FORMAT, "threadId": THREAD, "path": "/etc/passwd", "rollout": ""}).encode(),
        json.dumps({"format": FORMAT, "threadId": THREAD, "path": PATH.replace(THREAD, "0" * 36), "rollout": ""}).encode(),
        json.dumps({"format": FORMAT, "threadId": THREAD, "path": PATH, "rollout": 7}).encode(),
    ],
    ids=["not-json", "other-format", "traversal", "absolute", "other-thread", "not-text"],
)
def test_cs2_a_corrupt_envelope_is_refused(envelope: bytes) -> None:
    state = dataclasses.replace(encode_state(CODEX, THREAD, PATH, ROLLOUT), payload=envelope)
    with pytest.raises(EngineStateIncompatible) as refused:
        decode_state(state, CODEX)
    assert refused.value.reason_code == "state_corrupt"


def test_cs2_only_this_threads_rollout_under_sessions_is_a_rollout_path() -> None:
    assert rollout_path(PATH, THREAD) == PATH
    assert rollout_path(f"sessions/{PATH}", THREAD) is None
    assert rollout_path(PATH, "0" * 36) is None
    with pytest.raises(ValueError, match="rollout"):
        encode_state(CODEX, THREAD, "2026/10/03/notes.jsonl", ROLLOUT)


def test_cs3_a_rollout_holding_a_secret_is_never_encoded() -> None:
    with pytest.raises(StateHoldsSecret):
        encode_state(CODEX, THREAD, PATH, ROLLOUT + b"token-123\n", secrets=("token-123",))
