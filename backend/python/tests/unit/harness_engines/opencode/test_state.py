"""OC-9: the export-based state codec."""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

import pytest

from harness_core.engines.errors import EngineStateIncompatible
from harness_engines.opencode.state import (
    SCHEMA_VERSION,
    StateHoldsSecret,
    decode_state,
    encode_state,
)
from tests.unit.harness_engines.opencode.conftest import PINNED

EXPORT = (Path(__file__).parent / "fixtures" / "docker-export-session.json").read_bytes()


def test_oc9_round_trip_of_a_captured_export() -> None:
    state = encode_state(PINNED, "ses_001", EXPORT)
    assert (state.engine, state.schema_version) == (PINNED, SCHEMA_VERSION)
    envelope = json.loads(state.payload)
    assert (envelope["format"], envelope["sessionId"]) == ("opencode-export", "ses_001")
    decoded = decode_state(state, PINNED)
    assert decoded.session_id == "ses_001"
    assert json.loads(decoded.export) == json.loads(EXPORT)
    assert len(json.loads(decoded.export)["messages"]) == 7


@pytest.mark.parametrize(
    "state_change",
    [
        {"engine": dataclasses.replace(PINNED, version="1.18.35")},
        {"engine": dataclasses.replace(PINNED, binary_digest="sha256:" + "0" * 64)},
        {"schema_version": SCHEMA_VERSION + 1},
        {"payload": b"not json"},
        {"payload": b'{"format": "other", "sessionId": "s", "export": {}}'},
    ],
)
def test_oc9_refuses_other_versions_and_corrupt_envelopes(state_change: dict[str, object]) -> None:
    state = dataclasses.replace(encode_state(PINNED, "ses_001", EXPORT), **state_change)  # type: ignore[arg-type]
    with pytest.raises(EngineStateIncompatible):
        decode_state(state, PINNED)


def test_oc9_refuses_to_encode_a_secret() -> None:
    with pytest.raises(StateHoldsSecret):
        encode_state(PINNED, "ses_001", EXPORT, secrets=["jolly-eagle"])


def test_oc9_refuses_an_export_of_another_session() -> None:
    with pytest.raises(ValueError, match="not of this session"):
        encode_state(PINNED, "ses_999", EXPORT)
