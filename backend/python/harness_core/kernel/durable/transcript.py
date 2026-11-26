"""Blob encodings for journal refs and the transcript the kernel sends to the model.

The transcript is rendered from the folded journal, never from worker memory:
assistant turns come from ``rawRef`` (provider-native) when the current model
can use it, else from the normalized ``messageRef``; tool results are placed
in ``model.response.toolCalls`` order, whatever order they were journaled in.
"""

from __future__ import annotations

import json
from dataclasses import replace
from typing import TYPE_CHECKING, Any, cast

from pydantic import JsonValue, TypeAdapter

from harness_core.context.types import Attribution, HistoryItem
from harness_core.contracts import canonical_json, validate_contract
from harness_core.contracts.generated.labels import Labels
from harness_core.effects.results import decode_result, encode_result
from harness_core.kernel.core.messages import (
    AssistantMessage,
    Message,
    Part,
    TextPart,
    ToolCall,
    ToolMessage,
    ToolMessageMeta,
    UserMessage,
)
from harness_core.kernel.core.tool_content import tool_message_content
from harness_core.kernel.durable.audience import (
    FOREIGN_TURN_STUB,
    HistoryAudience,
    project_turns,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

    from harness_core.contracts.generated.common import ContentRef
    from harness_core.journal.ports import ContentStorePort
    from harness_core.kernel.durable.ports import DurableModelPort, ImportedHistory
    from harness_core.kernel.fold import (
        ResponseRecord,
        ResultRecord,
        SessionEntry,
        SessionState,
    )


def encode_assistant(message: AssistantMessage) -> bytes:
    """Provider-neutral form of a response: text and tool calls only."""
    calls: list[JsonValue] = [
        {"id": c.id, "name": c.name, "args": cast("JsonValue", c.arguments)}
        for c in message.tool_calls or []
    ]
    doc: dict[str, JsonValue] = {"text": message.text, "toolCalls": calls}
    if message.truncated:
        doc["truncated"] = True
    if message.cut is not None:
        doc["cut"] = message.cut
    return canonical_json(doc)


def decode_assistant(data: bytes) -> AssistantMessage:
    doc: dict[str, Any] = json.loads(data)
    calls = [
        ToolCall(id=c["id"], name=c["name"], arguments=c["args"])
        for c in doc["toolCalls"]
    ]
    return AssistantMessage(
        content=doc["text"],
        tool_calls=calls or None,
        truncated=bool(doc.get("truncated")),
        cut=doc.get("cut"),
    )


_PARTS: TypeAdapter[list[Part]] = TypeAdapter(list[Part])


def decode_result_parts(data: bytes) -> list[Part] | None:
    """A multipart result's parts (images included), or ``None`` for any other result."""
    doc = json.loads(data)
    if not doc.get("parts"):
        return None
    return _PARTS.validate_python(doc["content"])


def encode_user_parts(parts: Sequence[Part]) -> bytes:
    """A user message's parts (text and images), e.g. a steering message with attachments."""
    return canonical_json({"parts": cast("JsonValue", _PARTS.dump_python(list(parts), mode="json"))})


def decode_user_parts(data: bytes) -> list[Part]:
    return _PARTS.validate_python(json.loads(data)["parts"])


def decode_result_meta(data: bytes) -> dict[str, JsonValue] | None:
    meta = json.loads(data).get("meta")
    return cast("dict[str, JsonValue]", meta) if isinstance(meta, dict) else None


def result_text(content: JsonValue) -> str:
    return (
        content if isinstance(content, str) else canonical_json(content).decode("utf-8")
    )


class PlainImportedHistory:
    """``ImportedHistory`` without a product's rendering: each entry's text as a
    user or assistant message (``role`` ``user_query`` / ``bot_response``)."""

    async def messages(self, transcript: bytes) -> list[Message]:
        messages: list[Message] = []
        for turn in json.loads(transcript):
            content = str(turn.get("content") or "").strip()
            if not content:
                continue
            if turn.get("role") == "user_query":
                messages.append(UserMessage(content=content))
            elif turn.get("role") == "bot_response":
                messages.append(AssistantMessage(content=[TextPart(text=content)]))
        return messages


class TranscriptRenderer:
    def __init__(
        self,
        content: ContentStorePort,
        model: DurableModelPort,
        history: ImportedHistory | None = None,
        audience: HistoryAudience | None = None,
        run_id: str | None = None,
    ) -> None:
        self._content = content
        self._model = model
        self._history: ImportedHistory = history or PlainImportedHistory()
        # Set in a shared session (SS2): other participants' turns render as a stub.
        self._audience = audience
        self._run_id = run_id
        self._cache: dict[str, bytes] = {}
        # One rendering per transcript and attempt, as legacy seeds a request once.
        self._imported: dict[str, list[Message]] = {}

    async def blob(self, ref: ContentRef) -> bytes:
        cached = self._cache.get(ref.ref)
        if cached is None:
            cached = await self._content.get(ref)
            self._cache[ref.ref] = cached
        return cached

    async def assistant(
        self,
        message_ref: ContentRef,
        raw_ref: ContentRef | None,
        *,
        provider: str,
        transport: str,
    ) -> AssistantMessage:
        if raw_ref is not None:
            restored = self._model.restore(
                await self.blob(raw_ref), provider=provider, transport=transport
            )
            if restored is not None:
                return restored
        return decode_assistant(await self.blob(message_ref))

    async def imported(self, ref: ContentRef) -> list[Message]:
        rendered = self._imported.get(ref.ref)
        if rendered is None:
            transcript = await self.blob(ref)
            if self._audience is not None:
                transcript = canonical_json(
                    cast(
                        "JsonValue",
                        project_turns(
                            (t for t in json.loads(transcript) if isinstance(t, dict)),
                            self._audience,
                        ),
                    )
                )
            rendered = await self._history.messages(transcript)
            self._imported[ref.ref] = rendered
        return [m.model_copy(deep=True) for m in rendered]

    async def prime(self, session: SessionState) -> None:
        """Renders the session's imported histories now, for the side effects a
        product's rendering has (the citation refs of their attachments)."""
        for entry in session.entries:
            if entry.role == "imported" and entry.text_ref is not None:
                _ = await self.imported(entry.text_ref)

    async def render(self, session: SessionState) -> list[Message]:
        messages, _ = await self.render_for(session, None)
        return messages

    async def render_for(
        self, session: SessionState, run_id: str | None
    ) -> tuple[list[Message], int | None]:
        """The session's transcript, and where ``run_id``'s own input message is in it."""
        messages: list[Message] = []
        input_index: int | None = None
        for item in await self.render_items(session, run_id):
            if item.own_input:
                input_index = len(messages)
            messages.extend(item.messages)
        return messages, input_index

    async def render_items(self, session: SessionState, run_id: str | None) -> list[HistoryItem]:
        """The transcript as history items (P4): an entry with its tool results, an imported
        history, or one item per foreign run (SS2's stub, or a passed turn carrying the
        provenance the context compiler quotes it with)."""
        items: list[HistoryItem] = []
        stubbed_run: str | None = None
        passed = _PassedTurn()
        own = run_id or self._run_id
        for entry in session.entries:
            if passed.run_id is not None and entry.run_id != passed.run_id:
                items.append(await self._passed(passed))
                passed = _PassedTurn()
            # A person's /run and its output reach a model only when the person attaches them (SLASH3 SD9).
            if entry.run_id is not None and entry.run_id != own and entry.run_id in session.command_runs:
                continue
            if entry.role == "imported":
                if entry.text_ref is not None:
                    items.append(_item(entry, tuple(await self.imported(entry.text_ref))))
                stubbed_run = None
                continue
            if self._audience is not None and not self._audience.owns_run(
                entry.run_id, run_id or self._run_id
            ):
                if self._audience.passed_run(entry.run_id):
                    passed.take(entry)
                    stubbed_run = None
                    continue
                # Nothing of another participant's turn is read: one stub for the run.
                if entry.run_id != stubbed_run:
                    items.append(_item(entry, tuple(_stub_messages())))
                    stubbed_run = entry.run_id
                else:
                    items[-1] = replace(items[-1], last_seq=_last_seq(entry))
                continue
            stubbed_run = None
            if entry.role == "user":
                user = await self._user(entry, run_id)
                if user is not None:
                    items.append(user)
                continue
            if entry.response is not None:
                items.append(await self._response(entry, entry.response))
        if passed.run_id is not None:
            items.append(await self._passed(passed))
        return items

    async def _user(self, entry: SessionEntry, run_id: str | None) -> HistoryItem | None:
        if entry.content_ref is not None:
            parts = decode_user_parts(await self.blob(entry.content_ref))
            return _item(entry, (UserMessage(content=parts),))
        if entry.text_ref is None:
            return None
        mine = run_id is not None and entry.run_id == run_id and not entry.injected
        own = mine and entry.input_seq is None
        message = UserMessage(content=(await self.blob(entry.text_ref)).decode("utf-8"), injected=entry.injected)
        # A steering message (``input_seq``) is the user's too: a compaction keeps it verbatim.
        return _item(entry, (message,), own_input=own, user_input=mine)

    async def _response(self, entry: SessionEntry, response: ResponseRecord) -> HistoryItem:
        messages: list[Message] = [
            await self.assistant(
                response.message_ref,
                response.raw_ref,
                provider=response.provider,
                transport=response.transport,
            )
        ]
        labels: dict[str, Labels] = {}
        invocations: dict[str, str] = {}
        for call in response.tool_calls:
            result = entry.results.get(call.call_id)
            if result is None:
                # Only a run stopped outside a worker (Node cancelling a
                # suspended run) leaves one; a provider rejects an unpaired call.
                messages.append(ToolMessage(content="cancelled", tool_call_id=call.call_id, is_error=True))
                continue
            invocations[call.call_id] = result.invocation_id
            if result.labels is not None:
                labels[call.call_id] = validate_contract(Labels, result.labels)
            messages.append(await self._result(call.call_id, result))
        return _item(entry, tuple(messages), result_labels=labels, invocations=invocations)

    async def _result(self, call_id: str, result: ResultRecord) -> ToolMessage:
        meta: dict[str, JsonValue] | None = None
        parts: list[Part] | None = None
        content: JsonValue = result.status
        is_error = result.status != "ok"
        if result.result_ref is not None:
            raw = await self.blob(result.result_ref)
            content, is_error = decode_result(raw)
            meta = decode_result_meta(raw)
            parts = decode_result_parts(raw)
        return ToolMessage(
            content=parts if parts is not None else tool_message_content(content),
            tool_call_id=call_id,
            is_error=is_error or result.status != "ok",
            artifact_meta=ToolMessageMeta.model_validate(meta) if meta else None,
        )

    async def _passed(self, turn: _PassedTurn) -> HistoryItem:
        """A turn whose label passed: its question and its final answer's text, nothing it read or called."""
        question = (await self.blob(turn.question)).decode("utf-8") if turn.question is not None else ""
        answer = ""
        if turn.answer is not None:
            answer = text_of(decode_assistant(await self.blob(turn.answer.message_ref))).strip()
        return HistoryItem(
            first_seq=turn.first_seq,
            last_seq=turn.last_seq,
            messages=(
                UserMessage(content=question or FOREIGN_TURN_STUB),
                AssistantMessage(content=[TextPart(text=answer or FOREIGN_TURN_STUB)]),
            ),
            provenance=Attribution(f"another participant's turn in this shared conversation (run {turn.run_id})"),
        )


class _PassedTurn:
    """The parts of another participant's passed run the model is given: its question and last response."""

    def __init__(self) -> None:
        self.run_id: str | None = None
        self.question: ContentRef | None = None
        self.answer: ResponseRecord | None = None
        self.first_seq = 0
        self.last_seq = 0

    def take(self, entry: SessionEntry) -> None:
        if self.run_id is None:
            self.first_seq = entry.seq
        self.last_seq = _last_seq(entry)
        self.run_id = entry.run_id
        if entry.role == "user":
            if self.question is None and not entry.injected and entry.input_seq is None:
                self.question = entry.text_ref
        elif entry.response is not None:
            self.answer = entry.response


def _last_seq(entry: SessionEntry) -> int:
    """The entry's last event: its response's tool outcomes come after it."""
    return max([entry.seq, *(r.seq for r in entry.results.values() if r.seq is not None)])


def _item(
    entry: SessionEntry,
    messages: tuple[Message, ...],
    *,
    own_input: bool = False,
    user_input: bool = False,
    result_labels: dict[str, Labels] | None = None,
    invocations: dict[str, str] | None = None,
) -> HistoryItem:
    return HistoryItem(
        first_seq=entry.seq,
        last_seq=_last_seq(entry),
        messages=messages,
        result_labels=result_labels or {},
        invocations=invocations or {},
        own_input=own_input,
        user_input=user_input,
    )


def _stub_messages() -> list[Message]:
    return [
        UserMessage(content=FOREIGN_TURN_STUB),
        AssistantMessage(content=[TextPart(text=FOREIGN_TURN_STUB)]),
    ]


def text_of(message: Message) -> str:
    content = message.content
    if isinstance(content, str):
        return content
    return "".join(p.text for p in content if isinstance(p, TextPart))


__all__ = [
    "PlainImportedHistory",
    "TranscriptRenderer",
    "decode_assistant",
    "decode_result",
    "decode_result_meta",
    "decode_result_parts",
    "decode_user_parts",
    "encode_assistant",
    "encode_result",
    "encode_user_parts",
    "result_text",
    "text_of",
]
