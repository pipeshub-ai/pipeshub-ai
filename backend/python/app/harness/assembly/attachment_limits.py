"""The files a turn named that never reached its model, said in the answer (R33JGAPS-FU-6, ADR-RUN-03 D5).

A file the person attached that the run could not read was dropped without a word: the answer read complete, and a
model may answer as if it had read it. The run now names each one in the answer's "could not do" line: refused when
the access check left it out, unreadable when it was kept but no content came back. An image a model without vision
is shown as "[Image attached]" is not one of them: the model is told it is there.

Every reader of a turn's files records them here (R34FIX-FU-1): the run's own attachments in the attempt's tool state
(each attempt reads them again), a steered message's in the journal with its consumption (it is read once, and a
resumed attempt does not read it again). The finaliser names both, each file once.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final

from app.harness.engines.capability_limits import (
    ATTACHMENT_NOT_ACCESSIBLE,
    ATTACHMENT_UNREADABLE,
    attachment_item,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from pydantic import JsonValue

    from app.harness.assembly.context import Attachment
    from harness_core.journal.types import JournalEvent, NewEvent

# Where the attempt keeps them for its presenter: rebuilt on every attempt, as the attachments are resolved again.
UNREAD_ATTACHMENTS_KEY: Final = "harness_unread_attachments"
# A steered message's, one slot per message so the fold keeps every message's list.
STEERED_UNREAD_SLOT: Final = "pipeshub.unread_attachments.input."


def unread_attachments(
    named: Sequence[Attachment],
    kept: Sequence[Mapping[str, Any]],
    read: Mapping[str, Any],
    *,
    multimodal: bool,
    counted: bool = True,
) -> list[dict[str, JsonValue]]:
    """Each file of ``named`` (the run's input) the model was not given: not in ``kept`` (what the access check let
    through) or kept but absent from ``read`` (the records resolved, by virtual record id). Files it may not name are
    one item per kind, with how many when the claim's Node reads ``count`` (``counted``, R47POLISH-FU-1)."""
    kept_ids = {str(a.get("virtualRecordId")) for a in kept if a.get("virtualRecordId")}
    named_items: list[dict[str, JsonValue]] = []
    unnamed: dict[str, int] = {}
    for attachment in named:
        vrid = attachment.virtual_record_id
        if (attachment.mime_type or "").lower().startswith("image/") and not multimodal:
            continue
        if vrid and vrid in read:
            continue
        kind = ATTACHMENT_NOT_ACCESSIBLE if vrid and vrid not in kept_ids else ATTACHMENT_UNREADABLE
        if not attachment.record_name:
            unnamed[kind] = unnamed.get(kind, 0) + 1
            continue
        item = attachment_item(kind, attachment.record_name)
        if item not in named_items:
            named_items.append(item)
    return named_items + _unnamed_items(unnamed, counted=counted)


def _unnamed_items(unnamed: Mapping[str, int], *, counted: bool) -> list[dict[str, JsonValue]]:
    return [attachment_item(kind, None, count=n if counted else 1) for kind, n in unnamed.items()]


def record_unread(
    state: dict[str, Any], items: Sequence[Mapping[str, JsonValue]], *, typed: bool, counted: bool = True
) -> None:
    """The attempt's list, with ``typed``: whether the claim's Node reads the kinds typed (``attachment_limits``), and
    ``counted``: whether it reads an unnamed item's ``count`` (``attachment_count``). Recorded even when empty, so a
    steered file named later travels the way this Node reads it."""
    found = state.get(UNREAD_ATTACHMENTS_KEY)
    have = list(found[0]) if isinstance(found, tuple) else []
    state[UNREAD_ATTACHMENTS_KEY] = (_once([*have, *items]), typed, counted)


def steered_unread(message_seq: int, items: Sequence[Mapping[str, JsonValue]]) -> NewEvent | None:
    """A steered message's unread files, journaled with its consumption."""
    from harness_core.journal.types import NewEvent

    if not items:
        return None
    return NewEvent("loop.state_saved", {"slots": {f"{STEERED_UNREAD_SLOT}{message_seq}": [dict(i) for i in items]}})


def unread_of(state: object, events: Sequence[JournalEvent] = ()) -> tuple[list[dict[str, Any]], bool]:
    """Every file of the run the model was never given: the attempt's, then each steered message's from the journal;
    and whether they travel typed (as before when nothing was recorded)."""
    found = state.get(UNREAD_ATTACHMENTS_KEY) if isinstance(state, dict) else None
    own, typed, counted = (*found, True)[:3] if isinstance(found, tuple) and len(found) >= 2 else ((), True, True)
    # One list per steered message, the last one journaled: a replayed fold must not count a message twice.
    by_message: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        slots = event.data.get("slots") if event.type == "loop.state_saved" and isinstance(event.data, dict) else None
        if not isinstance(slots, dict):
            continue
        for key, value in slots.items():
            if str(key).startswith(STEERED_UNREAD_SLOT) and isinstance(value, list):
                by_message[str(key)] = [i for i in value if isinstance(i, dict)]
    lists = [list(own), *by_message.values()]
    named = _once([i for items in lists for i in items if i.get("file")])
    # Each list counts its own unnamed files, so files of different messages add up (R47POLISH-FU-1).
    unnamed: dict[str, int] = {}
    for items in lists:
        for item in _once([i for i in items if not i.get("file")]):
            count = item.get("count")
            kind = str(item.get("kind"))
            unnamed[kind] = unnamed.get(kind, 0) + (count if isinstance(count, int) and count > 0 else 1)
    return [*named, *_unnamed_items(unnamed, counted=bool(counted))], bool(typed)


def _once(items: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    seen: list[dict[str, Any]] = []
    for item in items:
        if dict(item) not in seen:
            seen.append(dict(item))
    return seen


__all__ = [
    "STEERED_UNREAD_SLOT",
    "UNREAD_ATTACHMENTS_KEY",
    "record_unread",
    "steered_unread",
    "unread_attachments",
    "unread_of",
]
