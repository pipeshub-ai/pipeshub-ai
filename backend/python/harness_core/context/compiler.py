"""The context compiler: a pure function from the rendered journal and the host's
bands to the request (REPORT 6.3, P4.1).

History is append-only: an item renders the same bytes every time, a reduction
changes the items its range covers once (when it is journaled), and everything
volatile goes to the tail. Pairing is an invariant: an item holds a call with its
results, a reduction covers whole items, and the output is checked.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from harness_core.context.artifacts import artifact_index, artifact_reference
from harness_core.context.quoting import CLOSE, escape, header, quote
from harness_core.context.results import batch_result_cap, cap_result
from harness_core.context.tools import tool_band_order
from harness_core.context.types import Attribution, CompiledContext, HistoryItem
from harness_core.kernel.core.messages import (
    AssistantMessage,
    ImagePart,
    Part,
    TextPart,
    ToolMessage,
    UserMessage,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from harness_core.context.types import (
        Compaction,
        CompilerInput,
        HostBands,
        PinnedItem,
        SeqSpan,
    )
    from harness_core.kernel.core.messages import Message

EXTERNAL = "external"
LIFTED_IMAGES = "Images returned by the tool calls above:"
# The tail rides in a user message only because it must follow the history to stay out of the
# cached prefix; what it holds is the host's (a shaper puts it in the system prompt), so it says so.
TAIL_HEADER = "Context for this request from the system, not a message from the user:"


class PairingBroken(Exception):
    """A request would send a tool call without its result, or a result without its call."""

    def __init__(self, index: int, reason: str) -> None:
        super().__init__(f"message {index}: {reason}")
        self.index = index
        self.reason = reason


def cleared_marker(invocation: str | None) -> str:
    named = f" (invocation {invocation})" if invocation else ""
    return (
        f"[Tool result cleared from the context to save space{named}. "
        "The full result is kept in the run's journal.]"
    )


def compile_context(inp: CompilerInput) -> CompiledContext:
    granted = {entry.tool_id: entry for entry in inp.host.tools}
    order = tool_band_order(inp.tool_order, granted)
    items = [_render(item, inp.cleared, inp.host) for item in inp.history]
    for compaction in inp.compactions:
        items = _compact(items, compaction)
    history: list[Message] = []
    input_index: int | None = None
    for item in items:
        if item.own_input:
            input_index = len(history) + _input_at(item)
        history.extend(item.messages)
    assert_paired(history)
    tail = "\n\n".join(text for text in inp.host.tail if text)
    if tail:
        tail = f"{TAIL_HEADER}\n\n{tail}"
    return CompiledContext(
        system=inp.host.system or None,
        pinned=_pinned(inp.host.pinned),
        tools=tuple(granted[tool_id].schema for tool_id in order),
        tool_order=order,
        history=tuple(history),
        tail=(UserMessage(content=tail, injected=True),) if tail else (),
        input_index=input_index,
    )


def compacted_history(
    history: Sequence[HistoryItem], compactions: Sequence[Compaction]
) -> tuple[list[Message], int | None]:
    """The history with each journaled compaction applied, as ``compile_context`` applies them, and where the own
    input sits: the request a run assembled without the compiler holds after a ``/compact`` (SLASH2 C2)."""
    items = list(history)
    for compaction in compactions:
        items = _compact(items, compaction)
    messages: list[Message] = []
    input_index: int | None = None
    for item in items:
        if item.own_input:
            input_index = len(messages) + _input_at(item)
        messages.extend(item.messages)
    return messages, input_index


def assert_paired(messages: Sequence[Message]) -> None:
    """Every call answered by exactly one result before anything else; no result without its call."""
    pending: list[str] = []
    for index, message in enumerate(messages):
        if isinstance(message, ToolMessage):
            if message.tool_call_id not in pending:
                raise PairingBroken(index, f"result {message.tool_call_id!r} has no open call")
            pending.remove(message.tool_call_id)
            continue
        if pending:
            raise PairingBroken(index, f"calls {sorted(pending)} have no result")
        if isinstance(message, AssistantMessage):
            pending = [call.id for call in message.tool_calls or []]
    if pending:
        raise PairingBroken(len(messages), f"calls {sorted(pending)} have no result")


def _pinned(items: Sequence[PinnedItem]) -> str | None:
    rendered = [quote(i.text, i.provenance) if i.provenance is not None else i.text for i in items]
    return "\n\n".join(text for text in rendered if text) or None


def _render(item: HistoryItem, cleared: Sequence[SeqSpan], host: HostBands) -> HistoryItem:
    is_cleared = any(span.covers(item) for span in cleared)
    if item.provenance is not None:
        return _replace(item, (UserMessage(content=quote(_transcript(item.messages), item.provenance)),))
    names = {c.id: c.name for m in item.messages if isinstance(m, AssistantMessage) for c in m.tool_calls or ()}
    cap = host.max_result_chars
    if cap is not None and host.window_tokens:
        cap = batch_result_cap(len(names), host.window_tokens, cap)
    rendered = tuple(
        _result(
            item,
            m,
            cleared=is_cleared and names.get(m.tool_call_id or "") not in host.keep_results,
            cap=cap,
        )
        if isinstance(m, ToolMessage)
        else m
        for m in item.messages
    )
    if host.result_footers:
        rendered = tuple(_footed(m, host.result_footers) for m in rendered)
    if item.own_input and rendered:
        rendered = (*rendered[:-1], _own_input(rendered[-1], host))
    if host.tool_images_after_results:
        images = [p for m in rendered if isinstance(m, ToolMessage) for p in _parts(m) if isinstance(p, ImagePart)]
        if images:
            lifted = UserMessage(content=[TextPart(text=LIFTED_IMAGES), *images], injected=True)
            rendered = (*rendered, lifted)
    return _replace(item, rendered)


def _footed(message: Message, footers: Mapping[str, str]) -> Message:
    if not isinstance(message, ToolMessage) or not (footer := footers.get(message.tool_call_id or "")):
        return message
    return message.model_copy(update={"step_footer": footer})


def _own_input(message: Message, host: HostBands) -> Message:
    """The run's own input as the host renders it (PipesHub: the goal, plus attachment images)."""
    if host.input_text is None and not host.input_parts:
        return message
    text = host.input_text if host.input_text is not None else _text(message)
    if not host.input_parts:
        return UserMessage(content=text)
    return UserMessage(content=[TextPart(text=text), *host.input_parts])


def _parts(message: Message) -> list[Part]:
    return message.content if isinstance(message.content, list) else []


def _result(item: HistoryItem, message: ToolMessage, *, cleared: bool, cap: int | None) -> ToolMessage:
    call_id = message.tool_call_id or ""
    if cleared:
        return ToolMessage(
            # An artifact's reference names what to read it back with (legacy L2).
            content=artifact_reference(message)
            if message.artifact_meta is not None
            else cleared_marker(item.invocations.get(call_id)),
            tool_call_id=message.tool_call_id,
            is_error=message.is_error,
            artifact_meta=message.artifact_meta,
        )
    if cap is not None:
        message = cap_result(message, cap)
    labels = item.result_labels.get(call_id)
    if labels is None or labels.integrity != EXTERNAL:
        return message
    provenance = Attribution(f"an external source (the result of tool call {call_id})", labels)
    if isinstance(message.content, str):
        return message.model_copy(update={"content": quote(message.content, provenance)})
    return message.model_copy(update={"content": _quote_parts(message.content, provenance)})


def _quote_parts(parts: list[Part], provenance: Attribution) -> list[Part]:
    # Adjacent text parts read as one text: a marker cut across two of them is escaped as one (QA27-F5).
    runs: list[Part] = []
    for part in parts:
        if isinstance(part, TextPart) and runs and isinstance(runs[-1], TextPart):
            runs[-1] = TextPart(text=runs[-1].text + part.text)
        else:
            runs.append(part)
    body: list[Part] = [TextPart(text=escape(p.text)) if isinstance(p, TextPart) else p for p in runs]
    return [TextPart(text=header(provenance)), *body, TextPart(text=CLOSE)]


def _transcript(messages: Sequence[Message]) -> str:
    lines: list[str] = []
    for message in messages:
        text = _text(message).strip()
        if text:
            lines.append(f"{message.role.value}: {text}")
    return "\n".join(lines)


def _text(message: Message) -> str:
    content = message.content
    if isinstance(content, str):
        return content
    return "".join(p.text for p in content if isinstance(p, TextPart))


def _compact(items: list[HistoryItem], compaction: Compaction) -> list[HistoryItem]:
    covered = [i for i, item in enumerate(items) if compaction.span.covers(item)]
    if not covered:
        return items
    span = compaction.span
    summary = UserMessage(
        content=quote(
            compaction.summary,
            Attribution(
                f"a summary of the earlier conversation (journal seq {span.from_seq}-{span.to_seq})",
                compaction.labels,
            ),
        ),
        injected=True,
    )
    index = artifact_index([m for i in covered for m in items[i].messages if isinstance(m, ToolMessage)])
    if index:
        summary = summary.model_copy(update={"content": f"{summary.content}\n\n{index}"})
    reinjected: list[Message] = []
    own: Message | None = None
    for i in covered:
        item = items[i]
        if item.own_input and item.messages:
            own = item.messages[_input_at(item)]
        if item.own_input or item.user_input:
            # A merged item's are the user's messages after its summary; another item's is itself.
            reinjected.extend(m for m in item.messages if isinstance(m, UserMessage) and not m.injected)
    merged = HistoryItem(
        first_seq=span.from_seq,
        last_seq=span.to_seq,
        messages=(summary, *reinjected),
        own_input=own is not None,
        user_input=bool(reinjected),
        input_at=1 + next(n for n, m in enumerate(reinjected) if m is own) if own is not None else None,
    )
    first = covered[0]
    kept = [item for i, item in enumerate(items) if i not in set(covered)]
    return [*kept[:first], merged, *kept[first:]]


def _input_at(item: HistoryItem) -> int:
    return item.input_at if item.input_at is not None else len(item.messages) - 1


def _replace(item: HistoryItem, messages: tuple[Message, ...]) -> HistoryItem:
    return HistoryItem(
        first_seq=item.first_seq,
        last_seq=item.last_seq,
        messages=messages,
        result_labels=item.result_labels,
        invocations=item.invocations,
        provenance=None,
        own_input=item.own_input,
        user_input=item.user_input,
        input_at=item.input_at,
    )


__all__ = ["TAIL_HEADER", "PairingBroken", "assert_paired", "cleared_marker", "compacted_history", "compile_context"]
