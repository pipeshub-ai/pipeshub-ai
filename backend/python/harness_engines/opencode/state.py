"""opencode state codec: ``opencode export`` JSON in a versioned envelope.

The export (``{info, messages[{info, parts}]}``) is opencode's portable
checkpoint: independent of its SQLite schema, importable into an empty store,
and verified to carry full history into the next turn (F4 §7). Copying the
SQLite files also works but ties restore to the DB schema of one release.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

from harness_core.engines.errors import EngineStateIncompatible, StateHoldsSecret
from harness_core.engines.events import EngineState
from harness_core.engines.jsonview import as_object, as_str

if TYPE_CHECKING:
    from collections.abc import Iterable

    from harness_core.engines.types import EngineVersion, JsonValue

SCHEMA_VERSION = 1
FORMAT = "opencode-export"


@dataclass(frozen=True, slots=True)
class OpencodeState:
    session_id: str
    export: bytes


def encode_state(
    engine: EngineVersion, session_id: str, export: bytes, *, secrets: Iterable[str] = ()
) -> EngineState:
    text = export.decode("utf-8")
    for secret in secrets:
        if secret and secret in text:
            raise StateHoldsSecret("engine state holds a credential")
    value: JsonValue = json.loads(text)
    exported_id = as_str(as_object(as_object(value).get("info")).get("id"))
    if exported_id != session_id:
        raise ValueError("export is not of this session")
    envelope: dict[str, JsonValue] = {"format": FORMAT, "sessionId": session_id, "export": value}
    payload = json.dumps(envelope, sort_keys=True, separators=(",", ":")).encode()
    return EngineState(engine=engine, schema_version=SCHEMA_VERSION, payload=payload)


def decode_state(state: EngineState, expected: EngineVersion) -> OpencodeState:
    if state.engine != expected:
        raise EngineStateIncompatible("state was saved by another engine version")
    if state.schema_version != SCHEMA_VERSION:
        raise EngineStateIncompatible(f"unknown opencode state schema {state.schema_version}")
    try:
        envelope = as_object(json.loads(state.payload))
    except ValueError as exc:
        raise EngineStateIncompatible("opencode state is not JSON", reason="state_corrupt") from exc
    session_id = as_str(envelope.get("sessionId"))
    if as_str(envelope.get("format")) != FORMAT or not session_id or "export" not in envelope:
        raise EngineStateIncompatible("not an opencode export envelope", reason="state_corrupt")
    export = json.dumps(envelope["export"], separators=(",", ":")).encode()
    return OpencodeState(session_id=session_id, export=export)


__all__ = ["FORMAT", "SCHEMA_VERSION", "OpencodeState", "StateHoldsSecret", "decode_state", "encode_state"]
