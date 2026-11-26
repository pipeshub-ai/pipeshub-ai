"""``harness-diff``: where did two runs of the same scenario first behave differently?

Steps are compared in order on behaviour only: which tools ran with which
arguments, what they returned, what the model said, the effects committed
and the final answer. Call ids are ignored (a live model mints new ones) and
UUIDs inside values are masked (tools mint them, e.g. question ids) — that is
the allowlist TEST-STRATEGY §4.6 lets strict replay ignore.
The model request digest is context, not behaviour: a differing digest is
reported as a note unless ``compare_context`` is set, matching the
TEST-STRATEGY §4.6 ``harness-diff`` mode ("report the context diff and
continue").
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from harness_core.testing.replay.recording import ModelStep, ToolStep

if TYPE_CHECKING:
    from pydantic import JsonValue

    from harness_core.testing.replay.recording import Recording

_PREVIEW = 160
_FIELD_ORDER = (
    "kind",
    "name",
    "args",
    "status",
    "error",
    "result",
    "tool_calls",
    "text",
)


_UUID = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}"
)


def _canon(value: JsonValue) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return _UUID.sub("<uuid>", raw)


def _preview(value: str | None) -> str:
    if value is None:
        return "(nothing)"
    return value if len(value) <= _PREVIEW else value[:_PREVIEW] + "…"


def describe(step: ModelStep | ToolStep | None) -> str:
    if step is None:
        return "(no step)"
    if isinstance(step, ToolStep):
        return f"tool {step.name}({_canon(step.args)}) -> {step.status}"
    if step.error is not None:
        return f"model error {step.error.kind}"
    assert step.response is not None
    calls = ", ".join(f"{c.name}({_canon(c.args)})" for c in step.response.tool_calls)
    if calls:
        return f"model -> {calls}"
    return f"model -> text {_preview(step.response.text)!r}"


def _behaviour(step: ModelStep | ToolStep) -> dict[str, JsonValue]:
    if isinstance(step, ToolStep):
        return {
            "kind": "tool",
            "name": step.name,
            "args": _canon(step.args),
            "status": step.status,
            "result": _canon(step.result),
            "error": _canon(step.error),
        }
    response = step.response
    return {
        "kind": "model",
        "error": step.error.kind if step.error else None,
        "text": _canon(response.text) if response else None,
        "tool_calls": [f"{c.name}:{_canon(c.args)}" for c in response.tool_calls]
        if response
        else [],
    }


@dataclass(frozen=True)
class Divergence:
    step: int  # 1-based; 0 means "after the last step" (final answer, effects)
    field: str
    expected: str
    actual: str
    expected_step: str
    actual_step: str
    previous_step: str

    def render(self) -> str:
        where = f"step {self.step}" if self.step else "outcome"
        return "\n".join(
            [
                f"First divergence at {where}: {self.field} differs",
                f"  previous (same in both): {self.previous_step}",
                f"  expected: {self.expected_step}",
                f"  actual:   {self.actual_step}",
                f"  expected {self.field}: {_preview(self.expected)}",
                f"  actual   {self.field}: {_preview(self.actual)}",
            ]
        )


@dataclass
class DiffReport:
    divergence: Divergence | None
    context_notes: list[str] = field(default_factory=list[str])

    @property
    def identical(self) -> bool:
        return self.divergence is None

    def render(self) -> str:
        lines = (
            ["Runs behave identically."]
            if self.divergence is None
            else [self.divergence.render()]
        )
        lines.extend(f"note: {n}" for n in self.context_notes)
        return "\n".join(lines)


def diff_recordings(
    expected: Recording,
    actual: Recording,
    *,
    compare_context: bool = False,
    compare_effects: bool = True,
) -> DiffReport:
    """``compare_effects=False`` for a replay whose tools were frozen: frozen
    tools commit nothing, and what they would have committed is already
    compared step by step through their recorded arguments and results."""
    notes: list[str] = []
    exp_steps, act_steps = expected.steps, actual.steps
    for i in range(max(len(exp_steps), len(act_steps))):
        e = exp_steps[i] if i < len(exp_steps) else None
        a = act_steps[i] if i < len(act_steps) else None
        previous = describe(exp_steps[i - 1]) if i else "(start of run)"
        if e is None or a is None:
            return DiffReport(
                Divergence(
                    step=i + 1,
                    field="step count",
                    expected=str(len(exp_steps)),
                    actual=str(len(act_steps)),
                    expected_step=describe(e),
                    actual_step=describe(a),
                    previous_step=previous,
                ),
                notes,
            )
        eb, ab = _behaviour(e), _behaviour(a)
        for key in _FIELD_ORDER:
            if eb.get(key) != ab.get(key):
                return DiffReport(
                    Divergence(
                        step=i + 1,
                        field=key,
                        expected=_canon(eb.get(key)),
                        actual=_canon(ab.get(key)),
                        expected_step=describe(e),
                        actual_step=describe(a),
                        previous_step=previous,
                    ),
                    notes,
                )
        if (
            isinstance(e, ModelStep)
            and isinstance(a, ModelStep)
            and e.request.digest != a.request.digest
        ):
            note = (
                f"step {i + 1}: model request context differs "
                f"(messages {e.request.message_count} -> {a.request.message_count}, "
                f"tools {len(e.request.tools)} -> {len(a.request.tools)})"
            )
            if compare_context:
                return DiffReport(
                    Divergence(
                        step=i + 1,
                        field="request context",
                        expected=e.request.digest,
                        actual=a.request.digest,
                        expected_step=describe(e),
                        actual_step=describe(a),
                        previous_step=previous,
                    ),
                    notes,
                )
            notes.append(note)
    last = describe(exp_steps[-1]) if exp_steps else "(start of run)"
    outcome = [
        ("final status", expected.final.status, actual.final.status),
        ("final answer", _canon(expected.final.answer), _canon(actual.final.answer)),
    ]
    if compare_effects:
        outcome.insert(
            0,
            (
                "effects",
                _canon([x.model_dump(mode="json") for x in expected.effects]),
                _canon([x.model_dump(mode="json") for x in actual.effects]),
            ),
        )
    for label, exp_value, act_value in outcome:
        if exp_value != act_value:
            return DiffReport(
                Divergence(
                    step=0,
                    field=label,
                    expected=exp_value,
                    actual=act_value,
                    expected_step="(end of run)",
                    actual_step="(end of run)",
                    previous_step=last,
                ),
                notes,
            )
    return DiffReport(None, notes)


__all__ = ["DiffReport", "Divergence", "describe", "diff_recordings"]
