"""``allow_with_redaction`` (OWD-9, TG-12): a call that runs without a person, whose result is redacted first.

A policy decides it for a call and names the ``Redaction`` (``RedactsResults``): why, and what it masks. The
``EffectRunner`` applies it to what the call returns before the outcome is journaled, so the model, a resumed run and
the transcript all read the redacted result and never the original. A person's approval of a call that a layer also
redacts does not lift the redaction: the runner asks the policy again whenever the call runs.

Only the content the model reads is redacted. Presentation events stay as the tool made them: they are shown to the
person the run acts for, who may read their own data.
"""

from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Protocol, runtime_checkable

from harness_core.effects.types import ALLOW_WITH_REDACTION
from harness_core.security.personal_data import redact_personal_in

if TYPE_CHECKING:
    from collections.abc import Callable

    from pydantic import JsonValue

    from harness_core.effects.types import ApprovalPolicy, ToolEffect, ToolExecution
    from harness_core.kernel.core.messages import ToolCall

    Redactor = Callable[[JsonValue, set[str]], JsonValue]

MAX_REDACTION_REASON_CHARS: Final = 200


@dataclass(frozen=True, slots=True)
class Redaction:
    """``reason``: the rule that redacts, as the model and the audit log read it (``classification:restricted``)."""

    reason: str
    redact: Redactor = redact_personal_in


POLICY_REDACTION: Final = Redaction("policy")


@runtime_checkable
class RedactsResults(Protocol):
    """An ``ApprovalPolicy`` that names the redaction it applies to a call's result; ``None``: none. Asked whenever
    the call runs (allowed, approved or retried), so a wrapper forwards it from the policy it wraps."""

    def redaction(self, call: ToolCall, effect: ToolEffect) -> Redaction | None: ...


def redaction_of(policy: ApprovalPolicy, call: ToolCall, effect: ToolEffect) -> Redaction | None:
    """The redaction ``policy`` names for the call; a policy that does not name one redacts by ``POLICY_REDACTION``
    when it decides ``allow_with_redaction``."""
    if isinstance(policy, RedactsResults):
        return policy.redaction(call, effect)
    return POLICY_REDACTION if policy(call, effect) == ALLOW_WITH_REDACTION else None


def either(a: Redaction | None, b: Redaction | None) -> Redaction | None:
    """Two layers' redactions as one: both apply, in order, and the reason names both."""
    if a is None or b is None or a == b:
        return a or b
    reason = f"{a.reason}; {b.reason}"[:MAX_REDACTION_REASON_CHARS]

    def both(value: JsonValue, found: set[str]) -> JsonValue:
        return b.redact(a.redact(value, found), found)

    return Redaction(reason, both)


def redaction_note(reason: str, kinds: set[str]) -> str:
    """What the model reads after a redacted result: what was masked and why, so it neither guesses the masked
    values nor asks for them again."""
    what = ", ".join(sorted(k.replace("_", " ") for k in kinds))
    return (
        f"[Policy redacted {what} from this result ({json.dumps(reason[:MAX_REDACTION_REASON_CHARS])}). "
        "Do not try to recover the masked values.]"
    )


def redacted(execution: ToolExecution, redaction: Redaction) -> tuple[ToolExecution, frozenset[str]]:
    """``execution`` with its content redacted, and the kinds masked (empty: nothing was)."""
    found: set[str] = set()
    content = redaction.redact(execution.content, found)
    if not found:
        return execution, frozenset()
    note = redaction_note(redaction.reason, found)
    if isinstance(content, str):
        content = f"{content}\n{note}"
    elif execution.parts and isinstance(content, list):
        content = [*content, {"type": "text", "text": note}]
    return dataclasses.replace(execution, content=content), frozenset(found)


__all__ = [
    "MAX_REDACTION_REASON_CHARS",
    "POLICY_REDACTION",
    "Redaction",
    "RedactsResults",
    "either",
    "redacted",
    "redaction_note",
    "redaction_of",
]
