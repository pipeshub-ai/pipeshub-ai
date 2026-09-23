"""PRE_MODEL step: show each knowledge block once.

A block reaches the model many times over a turn: in the prefetched context,
in each search that finds it again, and in a fetch of its whole record. Only
the citation list was deduplicated; the text went again each time.

This step removes a block from a search result when another copy the model
can see right now already shows it. Which copy is kept, per block:

1. a fetch that shows it (the latest one): the fuller copy;
2. prefetch: the system prompt is rebuilt every call and never shaped;
3. the earliest search that shows it, so older messages stay unchanged.

It runs on the per-call view after every other shaper, so it sees what the
model will actually get: a copy that was cleared, truncated or compacted no
longer matches its manifest and does not count. Stored history is never
changed, so a removed copy comes back by itself if the kept one goes away.
Citations are unaffected: they resolve from state, not from message text.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Any

from app.agent_loop_lib.core.messages import TextPart
from app.agent_loop_lib.core.types import MessageRole
from app.modules.retrieval.context.manifest import ManifestSource, manifest_registry

if TYPE_CHECKING:
    from collections.abc import Callable

    from app.agent_loop_lib.core.messages import Message
    from app.agent_loop_lib.hooks.middleware.context import ModelCallContext
    from app.agent_loop_lib.hooks.middleware.pipeline import Middleware, Next
    from app.agents.agent_loop.context import AgentContext
    from app.modules.retrieval.context.manifest import (
        BlockKey,
        ContentManifest,
        ManifestRegistry,
        RecordSpan,
        Segment,
    )

logger = logging.getLogger(__name__)

_RECORD_CLOSE = "</record>"


class _Kind(Enum):
    FETCH = "a fetch_record result"
    PREFETCH = "the context given at the start"
    SEARCH = "another search result"


@dataclass(frozen=True)
class _Owner:
    kind: _Kind
    position: int
    """Index of the message holding the kept copy; -1 for prefetch."""


def shape_duplicate_block_elision(context: AgentContext) -> Middleware[Any]:
    """Register after every other context shaper and before tool-pairing repair."""

    async def _middleware(ctx: ModelCallContext, next_fn: Next) -> None:
        registry = manifest_registry(context.tool_state)
        try:
            ctx.messages = elide_duplicates(
                ctx.messages, registry, describe_record=_record_describer(context.tool_state),
            )
        except Exception:  # the view without elision is always correct, only larger
            logger.warning("Duplicate block elision failed; sending the view unchanged", exc_info=True)
        await next_fn()

    return _middleware


def elide_duplicates(
    messages: list[Message],
    registry: ManifestRegistry,
    *,
    describe_record: Callable[[RecordSpan], str],
) -> list[Message]:
    """``messages`` with each search result's duplicated blocks removed."""
    copies = [
        (position, manifest)
        for position, message in enumerate(messages)
        if message.role == MessageRole.TOOL
        and (manifest := registry.lookup(message.text)) is not None
    ]
    owners = _owners(copies, registry.prefetch)
    if not owners:
        return messages

    shaped = list(messages)
    for position, manifest in copies:
        if manifest.source is not ManifestSource.SEARCH:
            continue
        text = _elided_text(shaped[position].text, position, manifest, owners, describe_record)
        if text is not None:
            shaped[position] = _with_text(shaped[position], text)
    return shaped


def _owners(
    copies: list[tuple[int, ContentManifest]], prefetch: list[ContentManifest],
) -> dict[BlockKey, _Owner]:
    owners: dict[BlockKey, _Owner] = {}
    for position, manifest in copies:
        if manifest.source is ManifestSource.FETCH:
            owners.update(dict.fromkeys(manifest.blocks, _Owner(_Kind.FETCH, position)))
    for manifest in prefetch:
        for key in manifest.blocks:
            owners.setdefault(key, _Owner(_Kind.PREFETCH, -1))
    for position, manifest in copies:
        if manifest.source is ManifestSource.SEARCH:
            for key in manifest.blocks:
                owners.setdefault(key, _Owner(_Kind.SEARCH, position))
    return owners


def _elided_text(
    text: str,
    position: int,
    manifest: ContentManifest,
    owners: dict[BlockKey, _Owner],
    describe_record: Callable[[RecordSpan], str],
) -> str | None:
    edits: list[tuple[int, int, str]] = []
    for record in manifest.records:
        segments = [s for s in manifest.segments if record.start <= s.start < record.end]
        dropped = [s for s in segments if _shown_elsewhere(s, position, owners)]
        if not dropped:
            continue
        where = _where(position, {owners[key] for s in dropped for key in s.blocks})
        count = len(dropped)
        blocks = f"block{'s' if count != 1 else ''}"
        if len(dropped) == len(segments):
            stub = f"[{describe_record(record)}: its {count} matching {blocks} {_are(count)} shown in {where}.]"
            edits.append((record.start, record.end, stub))
            continue
        edits.extend((s.start, s.end, "") for s in dropped)
        close = record.end - len(_RECORD_CLOSE)
        if text[close:record.end] == _RECORD_CLOSE:
            edits.append((close, close, f"[{count} more {blocks} of this record {_are(count)} shown in {where}.]\n"))
    if not edits:
        return None
    for start, end, replacement in sorted(edits, key=lambda edit: (edit[0], edit[1]), reverse=True):
        text = text[:start] + replacement + text[end:]
    return text


def _shown_elsewhere(segment: Segment, position: int, owners: dict[BlockKey, _Owner]) -> bool:
    return (
        segment.elidable
        and bool(segment.blocks)
        and all(owners[key].position != position for key in segment.blocks)
    )


def _where(position: int, owners: set[_Owner]) -> str:
    places = {
        (owner.kind, "" if owner.kind is _Kind.PREFETCH else ("above" if owner.position < position else "below"))
        for owner in owners
    }
    if len(places) != 1:
        return "other results in this conversation"
    kind, direction = places.pop()
    return f"{kind.value} {direction}".strip()


def _are(count: int) -> str:
    return "are" if count != 1 else "is"


def _with_text(message: Message, text: str) -> Message:
    if isinstance(message.content, str):
        return message.model_copy(update={"content": text})
    parts = list(message.content)
    text_positions = [i for i, part in enumerate(parts) if isinstance(part, TextPart)]
    if len(text_positions) != 1:
        return message
    parts[text_positions[0]] = parts[text_positions[0]].model_copy(update={"text": text})
    return message.model_copy(update={"content": parts})


def _record_describer(tool_state: dict[str, Any]) -> Callable[[RecordSpan], str]:
    """How a stub names a record: its Record ID as the model saw it, and its name."""
    from app.utils.chat_helpers import RecordIdShortener

    def describe(record: RecordSpan) -> str:
        shortener = tool_state.get("record_id_shortener")
        record_id = (
            shortener.shorten_if_known(record.record_id)
            if isinstance(shortener, RecordIdShortener) else record.record_id
        )
        records = tool_state.get("virtual_record_id_to_result") or {}
        name = (records.get(record.virtual_record_id) or {}).get("record_name")
        return f"Record ID: {record_id} ({name})" if name else f"Record ID: {record_id}"

    return describe
