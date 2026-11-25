"""ACP session state in a versioned envelope: what a fresh sandbox resumes the session from.

Two ways, per profile (``resume.mode``):

- ``load``: the agent keeps its sessions on disk and advertises ``loadSession``. The state is the files under the
  profile's ``resume.paths`` and the session id; a fresh sandbox gets the files back (the sandbox user's) and the
  driver calls ``session/load``.
- ``recap``: the agent cannot load a session. The state is the conversation the driver saw (each turn's prompt and
  the agent's final answer); a fresh session's first prompt carries it, and the workspace carries the files.

A ``load`` agent that turns out not to advertise ``loadSession`` resumes by recap too, so both are always captured.
"""

from __future__ import annotations

import base64
import json
import posixpath
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from harness_core.engines.errors import EngineStateIncompatible, StateHoldsSecret
from harness_core.engines.events import EngineState
from harness_core.engines.jsonview import as_list, as_object, as_str

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from harness_core.engines.types import EngineVersion, JsonValue

SCHEMA_VERSION = 1
FORMAT = "acp-session"
# A session's saved state, all files together; beyond it the run resumes from the workspace and the recap.
MAX_STATE_BYTES = 32 * 1024 * 1024
MAX_RECAP_CHARS = 64 * 1024
Role = Literal["user", "assistant"]


@dataclass(frozen=True, slots=True)
class AcpState:
    agent: str
    session_id: str
    files: Mapping[str, bytes]
    recap: tuple[tuple[Role, str], ...]


def _under(path: str, roots: Iterable[str]) -> bool:
    normal = posixpath.normpath(path)
    return normal == path and ".." not in path.split("/") and any(path.startswith(r.rstrip("/") + "/") for r in roots)


def encode_state(
    engine: EngineVersion,
    state: AcpState,
    *,
    roots: Iterable[str],
    secrets: Iterable[str] = (),
) -> EngineState:
    roots = tuple(roots)
    total = sum(len(content) for content in state.files.values())
    files: Mapping[str, bytes] = state.files if total <= MAX_STATE_BYTES else {}
    for path in files:
        if not _under(path, roots):
            raise ValueError("a state file outside the profile's state folders")
    secrets = [s for s in secrets if s]
    texts = [*(content.decode("utf-8", "replace") for content in files.values()), *(text for _, text in state.recap)]
    for secret in secrets:
        if any(secret in text for text in texts):
            raise StateHoldsSecret("engine state holds a credential")
    envelope: dict[str, JsonValue] = {
        "format": FORMAT,
        "agent": state.agent,
        "sessionId": state.session_id,
        "files": {path: base64.b64encode(content).decode() for path, content in sorted(files.items())},
        "recap": [{"role": role, "text": text} for role, text in state.recap],
    }
    payload = json.dumps(envelope, sort_keys=True, separators=(",", ":")).encode()
    return EngineState(engine=engine, schema_version=SCHEMA_VERSION, payload=payload)


def decode_state(state: EngineState, expected: EngineVersion, *, agent: str, roots: Iterable[str]) -> AcpState:
    if state.engine != expected:
        raise EngineStateIncompatible("state was saved by another engine version")
    if state.schema_version != SCHEMA_VERSION:
        raise EngineStateIncompatible(f"unknown ACP state schema {state.schema_version}")
    try:
        envelope = as_object(json.loads(state.payload))
    except ValueError as exc:
        raise EngineStateIncompatible("ACP state is not JSON", reason="state_corrupt") from exc
    if as_str(envelope.get("format")) != FORMAT or as_str(envelope.get("agent")) != agent:
        raise EngineStateIncompatible("not this agent's ACP session envelope", reason="state_corrupt")
    roots = tuple(roots)
    files: dict[str, bytes] = {}
    for path, content in as_object(envelope.get("files")).items():
        if not _under(path, roots) or not isinstance(content, str):
            raise EngineStateIncompatible("an ACP state file outside the profile's folders", reason="state_corrupt")
        try:
            files[path] = base64.b64decode(content, validate=True)
        except ValueError as exc:
            raise EngineStateIncompatible("an ACP state file is not base64", reason="state_corrupt") from exc
    recap: list[tuple[Role, str]] = []
    for entry in as_list(envelope.get("recap")):
        item = as_object(entry)
        role = as_str(item.get("role"))
        if role not in ("user", "assistant"):
            raise EngineStateIncompatible("an ACP recap entry has no role", reason="state_corrupt")
        recap.append((role, as_str(item.get("text"))))  # pyright: ignore[reportArgumentType]
    return AcpState(agent=agent, session_id=as_str(envelope.get("sessionId")), files=files, recap=tuple(recap))


def recap_text(recap: Iterable[tuple[Role, str]]) -> str:
    """The earlier conversation as the first prompt of a fresh session carries it, newest kept when it is long."""
    lines = [f"{'User' if role == 'user' else 'You'}: {text}" for role, text in recap]
    body = "\n\n".join(lines)
    if len(body) > MAX_RECAP_CHARS:
        body = body[-MAX_RECAP_CHARS:]
    return (
        "This session continues an earlier one in the same workspace. The earlier conversation was:\n\n"
        f"{body}\n\nContinue from there."
    )


__all__ = [
    "FORMAT",
    "MAX_STATE_BYTES",
    "SCHEMA_VERSION",
    "AcpState",
    "StateHoldsSecret",
    "decode_state",
    "encode_state",
    "recap_text",
]
