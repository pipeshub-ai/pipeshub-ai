"""Turn a recording back into doubles: a model script and frozen tool outcomes.

``strict`` replay drives the harness under test with :func:`recording_to_script`
as its model and :class:`FrozenTools` at its tool boundary, records the new
run, and compares the two recordings with :func:`harness_core.testing.replay.diff`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal

from harness_core.testing.model_script import (
    ErrorSpec,
    ModelScript,
    Reply,
    ToolCallSpec,
    Turn,
)
from harness_core.testing.replay.recording import ToolStep

if TYPE_CHECKING:
    from pydantic import JsonValue

    from harness_core.testing.model_types import ErrorKind
    from harness_core.testing.replay.recording import ModelStep, Recording

_ERROR_KINDS: frozenset[str] = frozenset(
    {"server_error", "rate_limited", "content_filter", "timeout", "malformed"}
)


def _turn(step: ModelStep) -> Turn:
    if step.response is None:
        kind: ErrorKind = "server_error"
        if step.error is not None and step.error.kind in _ERROR_KINDS:
            kind = step.error.kind  # type: ignore[assignment]  # narrowed by the set above
        message = step.error.message if step.error else ""
        return Turn(reply=Reply(error=ErrorSpec(kind=kind, message=message)))
    r = step.response
    return Turn(
        reply=Reply(
            text=r.text,
            reasoning=r.reasoning,
            tool_calls=[
                ToolCallSpec(id=c.id, name=c.name, args=dict(c.args))
                for c in r.tool_calls
            ],
            usage=r.usage,
            stop_reason=r.stop_reason,
        )
    )


def recording_to_script(recording: Recording) -> ModelScript:
    """The recorded model responses, served back in call order."""
    return ModelScript(
        id=f"replay:{recording.manifest.scenario}",
        turns=[_turn(step) for step in recording.model_steps()],
    )


@dataclass(frozen=True)
class FrozenOutcome:
    status: Literal["succeeded", "failed", "denied"]
    result: JsonValue
    error: str | None
    sources: tuple[JsonValue, ...] = ()
    diverged: str | None = None


def _canonical(args: dict[str, JsonValue]) -> str:
    return json.dumps(args, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


@dataclass
class FrozenTools:
    """Serves each recorded tool outcome to the matching call.

    A call is matched by its call id first (scripted ids are deterministic),
    then by tool name in order of occurrence (a live model mints new ids).
    Arguments that differ from the recording are noted as a divergence and the
    recorded outcome is still served, so the run continues and the diff shows
    the first place behaviour changed rather than a crash.
    """

    recording: Recording
    divergences: list[str] = field(default_factory=list[str])
    _used: set[int] = field(default_factory=set[int])

    def _steps(self) -> list[ToolStep]:
        return [s for s in self.recording.steps if isinstance(s, ToolStep)]

    def lookup(
        self, call_id: str, name: str, args: dict[str, JsonValue]
    ) -> FrozenOutcome:
        steps = self._steps()
        index = next(
            (
                i
                for i, s in enumerate(steps)
                if i not in self._used and s.call_id == call_id
            ),
            None,
        )
        if index is None:
            index = next(
                (
                    i
                    for i, s in enumerate(steps)
                    if i not in self._used and s.name == name
                ),
                None,
            )
        if index is None:
            note = (
                f"no recorded outcome for {name}({_canonical(args)}) [call {call_id}]"
            )
            self.divergences.append(note)
            return FrozenOutcome("failed", None, f"replay: {note}", diverged=note)
        self._used.add(index)
        step = steps[index]
        note = None
        if step.name != name:
            note = f"call {call_id}: recorded tool {step.name}, harness called {name}"
        elif _canonical(step.args) != _canonical(args):
            note = (
                f"call {call_id} {name}: recorded args {_canonical(step.args)}, "
                f"harness sent {_canonical(args)}"
            )
        if note:
            self.divergences.append(note)
        return FrozenOutcome(
            step.status, step.result, step.error, tuple(step.sources), diverged=note
        )

    def unused(self) -> list[ToolStep]:
        return [s for i, s in enumerate(self._steps()) if i not in self._used]


__all__ = ["FrozenOutcome", "FrozenTools", "recording_to_script"]
