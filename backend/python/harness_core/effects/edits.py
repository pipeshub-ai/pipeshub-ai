"""Edit-then-approve (AP-06): a person changes a held call's arguments and approves the changed call.

The worker offers it (``interaction.explained`` ``editable``, only to a journal that accepts ``approval_edits``);
Node journals the edited arguments with the approval, as ``interaction.resolved`` ``answerRef``: canonical JSON,
inline, with its SHA-256. The call keeps its identity (its invocation, its effect key and the ``argsDigest`` the
model's call journaled), so a recovery or an engine's re-call finds the same approval; what runs is the edit. The
audit then holds both versions, each with its hash: the model's in ``model.response``, the person's in the
approval. An edit that cannot be read is never replaced by the original: the call is refused.
"""

from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Protocol, cast, runtime_checkable

from harness_core.contracts import canonical_json
from harness_core.effects.flow import is_edition_reason
from harness_core.journal.inline import content_digest, inline_bytes

if TYPE_CHECKING:
    from collections.abc import Mapping

    from pydantic import JsonValue

    from harness_core.contracts.generated.common import ContentRef
    from harness_core.effects.types import EffectCall, ToolEffect, ToolExecution
    from harness_core.kernel.core.messages import ToolCall

# The largest arguments a person may edit, as canonical JSON (Node bounds the edit it accepts the same way).
MAX_EDITABLE_ARGS_BYTES: Final = 64 * 1024
# How much of the edited arguments the model reads back with the result.
MAX_EDIT_NOTE_CHARS: Final = 4000
EDITABLE: Final = "editable"
# The tool's own argument order for the edit card (WP-56): the schema's property names, which only the worker knows.
FIELD_ORDER: Final = "fieldOrder"
MAX_FIELD_ORDER: Final = 64
_MAX_FIELD_NAME: Final = 128
EDIT_UNREADABLE: Final = (
    "approval_edit_unreadable",
    "approval_edit_unreadable: the person approved this call with changes that could not be read, so it was not "
    "made. Do not call it again; tell the user it was not done and ask them to approve it again.",
)


@dataclass(frozen=True, slots=True)
class ApprovedEdit:
    """The arguments a person approved in place of the model's; ``arguments`` is ``None`` when the journaled edit
    cannot be read (a ref that is not inline, a hash that does not match, not a JSON object)."""

    arguments: dict[str, JsonValue] | None
    digest: str


def approved_edit(ref: ContentRef | None) -> ApprovedEdit | None:
    """The edit an approval's ``answerRef`` carries; ``None``: the call was approved as asked."""
    if ref is None:
        return None
    raw = inline_bytes(ref)
    if raw is None or content_digest(raw) != ref.hash:
        return ApprovedEdit(None, ref.hash)
    try:
        value = json.loads(raw)
    except ValueError:
        return ApprovedEdit(None, ref.hash)
    if not isinstance(value, dict):
        return ApprovedEdit(None, ref.hash)
    return ApprovedEdit(dict(value), ref.hash)  # pyright: ignore[reportUnknownArgumentType]


@runtime_checkable
class EditsApproved(Protocol):
    """An optional facet of an interaction view, bridge or hold: the edit a person approved a call with."""

    def edit(self, interaction_id: str) -> ApprovedEdit | None: ...


def offers_edit(call: ToolCall, effect: ToolEffect, reason: Mapping[str, JsonValue]) -> bool:
    """Whether the person may edit the call before approving it: a write whose arguments are a non-empty object a
    person can read. Never a classification ask, which a level's admin decides, and never code to execute."""
    # An edition's rule decides the call as asked; the person never edits it first.
    if effect.effect != "write" or not call.arguments or is_edition_reason(reason):
        return False
    return len(canonical_json(call.arguments)) <= MAX_EDITABLE_ARGS_BYTES


def field_order(schema: object) -> list[str]:
    """The property names an input schema lists, in its order, as many as the contract takes; empty without any."""
    properties = schema.get("properties") if isinstance(schema, dict) else None
    if not isinstance(properties, dict):
        return []
    names = [name for name in properties if isinstance(name, str) and 0 < len(name) <= _MAX_FIELD_NAME]
    return names[:MAX_FIELD_ORDER]


def offering_edit(
    call: ToolCall, effect: ToolEffect, reason: Mapping[str, JsonValue], schema: object = None
) -> Mapping[str, JsonValue]:
    """``reason`` with the edit offer when the call may be edited, and the tool's argument order when ``schema`` (its
    input schema) lists one, so the card shows the values as the tool declares them (WP-56)."""
    if not offers_edit(call, effect, reason):
        return reason
    order = field_order(schema)
    return {**reason, EDITABLE: True, **({FIELD_ORDER: cast("JsonValue", order)} if order else {})}


def edited_call(call: EffectCall, arguments: Mapping[str, JsonValue]) -> EffectCall:
    """``call`` running the person's arguments under its own identity (invocation, effect key)."""
    return dataclasses.replace(call, call=call.call.model_copy(update={"arguments": dict(arguments)}))


def edit_note(arguments: Mapping[str, JsonValue]) -> str:
    shown = json.dumps(dict(arguments), ensure_ascii=False, sort_keys=True)
    if len(shown) > MAX_EDIT_NOTE_CHARS:
        shown = shown[: MAX_EDIT_NOTE_CHARS - 1] + "…"
    return (
        "The person changed this call's arguments before approving it, so it ran with these instead of yours: "
        f"{shown}. Describe what was done with these values."
    )


def noted(execution: ToolExecution, arguments: Mapping[str, JsonValue]) -> ToolExecution:
    """The outcome the model reads: what ran, ahead of the result, so it never reports its own arguments as done."""
    note = edit_note(arguments)
    content = execution.content
    if execution.parts and isinstance(content, list):
        return dataclasses.replace(execution, content=[{"type": "text", "text": note}, *content])
    if isinstance(content, str):
        return dataclasses.replace(execution, content=f"{note}\n\n{content}")
    return dataclasses.replace(execution, content=f"{note}\n\n{json.dumps(content, ensure_ascii=False)}")


__all__ = [
    "EDITABLE",
    "FIELD_ORDER",
    "MAX_FIELD_ORDER",
    "EDIT_UNREADABLE",
    "MAX_EDITABLE_ARGS_BYTES",
    "MAX_EDIT_NOTE_CHARS",
    "ApprovedEdit",
    "EditsApproved",
    "approved_edit",
    "edit_note",
    "edited_call",
    "noted",
    "field_order",
    "offering_edit",
    "offers_edit",
]
