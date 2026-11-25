"""An engine's native stream as it came, kept beside the canonical events (C2.39, C2.40; UP-07, EV-13).

Two things leave a turn besides its ``EngineEvent``s:

- **The native transcript.** A mapper's every input, in order: each native message the engine sent, as its
  JSON value, and the few calls its session made on it (Codex's turn id, an interrupt). A ``NativeTape``
  takes them as JSON lines and hands them out in parts (``PART_BYTES``) the caller seals into the run's
  content store, so memory holds at most one part; past ``MAX_TRANSCRIPT_BYTES`` a turn's transcript is cut
  and its last part says so. ``reparse`` feeds a transcript to a fresh mapper, the same one or an improved
  one, and returns the canonical events it yields: the same events, for the same mapper (mappers are
  deterministic over their inputs).
- **Unknown native events.** A message whose kind the adapter does not know (a newer engine's) is never a
  crash and never dropped unseen: the mapper yields ``NativeUnknown`` with the message's bytes, cut at
  ``MAX_UNKNOWN_BYTES``. The controller counts every one; the journal keeps at most ``MAX_UNKNOWN_EVENTS``
  of a turn's.

Neither is ever shown in the chat's activity: both are journaled for audit and re-parsing only.
"""

from __future__ import annotations

import functools
import json
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Concatenate, Protocol, cast, runtime_checkable

from harness_core.engines.events import NativeUnknown

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    from harness_core.engines.events import EngineEvent
    from harness_core.engines.stream import TurnSeq
    from harness_core.engines.types import JsonValue

PART_BYTES = 256 * 1024
MAX_TRANSCRIPT_BYTES = 16 * 1024 * 1024
MAX_UNKNOWN_BYTES = 16 * 1024
MAX_UNKNOWN_EVENTS = 20
# What a native kind is reduced to before it is journaled or logged: a protocol name, never free text.
_KIND = re.compile(r"[^A-Za-z0-9_./:-]")
MAX_KIND_CHARS = 64
# The op a session uses to set a mapper's attribute; any other op is the mapper method of that name.
SET_OP = "set"


def native_bytes(message: JsonValue) -> bytes:
    return json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode()


def native_kind(*parts: str) -> str:
    kind = "/".join(p for p in parts if p) or "unnamed"
    return _KIND.sub("_", kind)[:MAX_KIND_CHARS]


def unknown_event(seq: TurnSeq, kind: str, message: JsonValue) -> NativeUnknown:
    """A message of a kind the adapter does not know, as the event that keeps it (its bytes cut at a bound)."""
    raw = native_bytes(message)
    return NativeUnknown(seq.next(), native_kind(kind), raw[:MAX_UNKNOWN_BYTES], len(raw))


@dataclass(frozen=True, slots=True)
class TranscriptPart:
    """A run of a turn's transcript lines; ``final`` on the turn's last part, ``truncated`` when lines were cut."""

    turn_id: str
    index: int
    data: bytes
    lines: int
    final: bool = False
    truncated: bool = False


class NativeTape:
    """One session's native transcript, a turn at a time. ``op`` never raises and never grows past its cap."""

    def __init__(self, *, part_bytes: int = PART_BYTES, max_bytes: int = MAX_TRANSCRIPT_BYTES) -> None:
        self._part_bytes = part_bytes
        self._max_bytes = max_bytes
        self._turn_id: str | None = None
        self._buffer: list[bytes] = []
        self._buffered = 0
        self._lines = 0
        self._total = 0
        self._index = 0
        self._full: list[TranscriptPart] = []
        self.truncated = False

    def begin(self, turn_id: str) -> None:
        self._turn_id = turn_id
        self._buffer, self._buffered, self._lines, self._total, self._index = [], 0, 0, 0, 0
        self._full = []
        self.truncated = False

    def op(self, name: str, args: tuple[JsonValue, ...]) -> None:
        if self._turn_id is None or self.truncated:
            return
        line = json.dumps([name, *args], ensure_ascii=False, separators=(",", ":")).encode() + b"\n"
        if self._total + len(line) > self._max_bytes:
            self.truncated = True
            return
        self._buffer.append(line)
        self._buffered += len(line)
        self._lines += 1
        self._total += len(line)
        if self._buffered >= self._part_bytes:
            self._full.append(self._cut(final=False))

    def take(self) -> list[TranscriptPart]:
        """The parts filled since the last call."""
        full, self._full = self._full, []
        return full

    def finish(self) -> list[TranscriptPart]:
        """Every part not yet taken, the last one marked final; nothing for a turn that recorded nothing."""
        if self._turn_id is None:
            return []
        parts = self.take()
        if self._buffer or self.truncated or parts:
            parts.append(self._cut(final=True))
        self._turn_id = None
        return parts

    def _cut(self, *, final: bool) -> TranscriptPart:
        assert self._turn_id is not None
        part = TranscriptPart(
            self._turn_id, self._index, b"".join(self._buffer), self._lines, final=final, truncated=final and self.truncated
        )
        self._index += 1
        self._buffer, self._buffered, self._lines = [], 0, 0
        return part


class Taped(Protocol):
    tape: NativeTape | None


def recorded[M: Taped, **P, R](method: Callable[Concatenate[M, P], R]) -> Callable[Concatenate[M, P], R]:
    """A mapper entry point whose call goes on the mapper's tape, if it has one, before it runs."""

    @functools.wraps(method)
    def wrapper(self: M, *args: P.args, **kwargs: P.kwargs) -> R:
        tape = self.tape
        if tape is not None:
            tape.op(method.__name__, tuple(args))  # type: ignore[arg-type]
        return method(self, *args, **kwargs)

    return wrapper


def set_recorded(mapper: Taped, name: str, value: JsonValue) -> None:
    """Set a mapper attribute a session drives the mapping with, on the tape too when it changes."""
    if mapper.tape is not None and getattr(mapper, name) != value:
        mapper.tape.op(SET_OP, (name, value))
    setattr(mapper, name, value)


@runtime_checkable
class RecordsNative(Protocol):
    """A session that puts its mappers' inputs on a tape the controller gives it."""

    def tap_native(self, tape: NativeTape) -> None: ...


def transcript_lines(data: bytes) -> Iterable[list[JsonValue]]:
    for line in data.splitlines():
        if line.strip():
            yield json.loads(line)


def reparse(transcript: bytes | Iterable[bytes], mapper: object) -> list[EngineEvent]:
    """The canonical events ``mapper`` yields from a stored transcript (UP-07): every op called in order."""
    data = transcript if isinstance(transcript, bytes) else b"".join(transcript)
    events: list[EngineEvent] = []
    for entry in transcript_lines(data):
        op, args = str(entry[0]), entry[1:]
        if op == SET_OP:
            setattr(mapper, str(args[0]), args[1])
            continue
        produced = getattr(mapper, op)(*args)
        if isinstance(produced, list):
            events.extend(cast("list[EngineEvent]", produced))
        elif produced is not None:
            events.append(cast("EngineEvent", produced))
    return events


__all__ = [
    "MAX_TRANSCRIPT_BYTES",
    "MAX_UNKNOWN_BYTES",
    "MAX_UNKNOWN_EVENTS",
    "PART_BYTES",
    "NativeTape",
    "NativeUnknown",
    "RecordsNative",
    "TranscriptPart",
        "native_bytes",
    "native_kind",
    "unknown_event",
    "recorded",
    "reparse",
    "set_recorded",
]
