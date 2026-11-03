"""The message model: a discriminated union of role-specific message types,
replacing the old monolithic `Message` (one class for all four roles, with
role-specific fields all declared optional regardless of which role actually
uses them — an SRP/LSP violation: a `UserMessage` had a meaningless
`tool_call_id` field, a `ToolMessage` had a meaningless `tool_calls` field).

Modeled after Pydantic AI's `ModelMessage`/Agno's message parts: each
concrete class only declares the fields that make sense for its role, and
`Message` is the `Annotated[Union[...], Field(discriminator="role")]` alias
used everywhere a heterogeneous, role-agnostic list is needed (conversation
history, `AgentTurn.messages`, transport payloads, ...). The discriminator
also fixes a latent deserialization bug the old design had no protection
against: without it, `list[ContentPart]`-shaped fields silently drop
subclass-specific data on round-trip (Pydantic falls back to the first
union member that structurally matches).

`MessageRole` is kept as a plain `str` `Enum` purely as an ergonomic set of
comparison constants — `msg.role == MessageRole.TOOL` still reads naturally
and still evaluates correctly (each concrete class's `role` is a `Literal`
matching the enum's own string value, and `str, Enum` members compare equal
to their plain string value in both directions). It is NOT what makes the
four message types polymorphic; the discriminated union is.
"""

from __future__ import annotations

from collections.abc import Sequence
from enum import Enum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, field_validator

__all__ = [
    "MessageRole",
    "ToolCall",
    "MALFORMED_TOOL_CALL_ARGS_KEY",
    "MALFORMED_TOOL_CALL_ERROR_KEY",
    "ImageSource",
    "TextPart",
    "ThinkingPart",
    "ImagePart",
    "Part",
    "SystemMessage",
    "UserMessage",
    "AssistantMessage",
    "ToolMessageMeta",
    "ToolMessage",
    "Message",
    "CONTEXT_NOTE_RULE",
    "add_loop_note",
    "as_sent",
    "render_context_note",
]


class MessageRole(str, Enum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class ToolCall(BaseModel):
    id: str
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict)


# Sentinel keys a transport's message converter can set on `arguments` when
# a provider handed back a tool call whose argument JSON could not be
# parsed (or repaired) at all — rather than dropping the call (which would
# make the turn look like a plain no-tool-call response and let a weak
# model "finish" without ever invoking the tool it clearly meant to call),
# the converter still emits a `ToolCall` carrying these keys so
# `agent/tool_loop.py::execute_tool_call` can turn it into a corrective
# error `ToolMessage` and keep the loop going. See
# `app/agents/agent_loop/converters.py::_recover_invalid_tool_call`.
MALFORMED_TOOL_CALL_ARGS_KEY = "__malformed_json__"
MALFORMED_TOOL_CALL_ERROR_KEY = "__parse_error__"


class ImageSource(BaseModel):
    type: str                      # "base64" | "url"
    media_type: str | None = None  # "image/jpeg" | "image/png" | "image/gif" | "image/webp"
    data: str = ""                 # base64 string (type="base64") or URL (type="url")


def image_data_url(source: "ImageSource") -> str:
    """An `ImageSource` as a URL the OpenAI-family APIs accept.

    They take one string for both cases: a plain URL, or a `data:` URI carrying
    base64. Kept here next to the model so each transport does not re-derive it.
    """
    if source.type == "base64":
        media_type = source.media_type or "image/jpeg"
        return f"data:{media_type};base64,{source.data}"
    return source.data


class TextPart(BaseModel):
    type: Literal["text"] = "text"
    text: str


class ThinkingPart(BaseModel):
    """Extended-thinking / reasoning content (Claude, o-series, ...) —
    carried through so observability/eval tooling can inspect it, but never
    fed back to a provider as ordinary text (see transport formatting)."""

    type: Literal["thinking"] = "thinking"
    thinking: str


class ImagePart(BaseModel):
    type: Literal["image"] = "image"
    source: ImageSource
    # Pixel dimensions when the producer measured them, so `count_tokens` can
    # size the image without decoding it on every shaper pass. `None` means
    # unmeasured, never zero-cost -- see `tokens.py`.
    width: int | None = None
    height: int | None = None
    # The person attached this image to their request. An image cap keeps these
    # before images the run fetched, whatever their position in the request.
    user_attached: bool = False


# Open/Closed: adding a new content shape (e.g. DocumentPart) means adding a
# class here and to this Union — no existing message class changes.
Part = Annotated[
    TextPart | ThinkingPart | ImagePart,
    Field(discriminator="type"),
]


def _coerce_text_parts(value: Any) -> Any:
    """Ergonomic constructor sugar: `AssistantMessage(content="hi")` and
    `AssistantMessage(content=None)` both work without callers having to
    wrap a bare string in `[TextPart(text=...)]` themselves — the ONLY
    shape that gets deserialized/serialized on the wire is still the
    discriminated `list[Part]`, this just widens the constructor."""
    if value is None:
        return []
    if isinstance(value, str):
        return [TextPart(text=value)] if value else []
    return value


class SystemMessage(BaseModel):
    role: Literal[MessageRole.SYSTEM] = MessageRole.SYSTEM
    content: str


class UserMessage(BaseModel):
    role: Literal[MessageRole.USER] = MessageRole.USER
    # Plain str covers the overwhelming common case (text-only user turns);
    # list[Part] is for multimodal input (images alongside text).
    content: str | list[Part] = ""
    # True when the message was injected programmatically (recovery nudge,
    # phase-transition note, plan-injection, ...) rather than typed by a
    # human user.  `collect_parent_tool_results` uses this to avoid
    # treating a system-injected nudge as a conversation boundary that
    # hides all earlier tool results from a child agent.
    injected: bool = False
    # True for a small subset of `injected` messages whose loss mid-run
    # would break the loop's own control flow rather than just cost some
    # context quality — e.g. `PlanExecuteLoop`'s upfront plan injection,
    # the one thing the whole rest of the run is supposed to execute
    # against. `shape_sliding_window` never evicts a `pinned` message
    # regardless of position, unlike ordinary `injected` nudges (phase
    # transitions, retry prompts, ...) which stay evictable on purpose —
    # losing one of those degrades guidance; losing the plan itself
    # strands the executor with no plan to execute.
    pinned: bool = False
    # The context layer that wrote this message (a compaction summary), on
    # the per-call copy only. Layers read it instead of matching their own
    # header text, which a person's question could contain too.
    compacted_by: str | None = None


class AssistantMessage(BaseModel):
    role: Literal[MessageRole.ASSISTANT] = MessageRole.ASSISTANT
    content: list[Part] = Field(default_factory=list)
    # Tool calls are a distinct, structured concept from free-form content
    # (they're always fully-parsed by the transport, never partial text),
    # so they stay a dedicated field rather than another Part variant —
    # avoids the old design's dual representation (a ToolCall AND a
    # TOOL_USE ContentBlock wrapping the same data).
    tool_calls: list[ToolCall] | None = None
    # True when the provider cut generation at the output-token cap (e.g.
    # Anthropic stop_reason == "max_tokens") — see ModelResponse.stop_reason,
    # which is the authoritative source; this field mirrors it onto the
    # message itself since `Agent.step()`'s truncation-recovery branch reads
    # the message, not the response, at the point it needs the flag.
    truncated: bool = False
    # The context layer that shortened this message, on the per-call copy
    # only (see `UserMessage.compacted_by`).
    compacted_by: str | None = None

    @field_validator("content", mode="before")
    @classmethod
    def _coerce_content(cls, value: Any) -> Any:
        return _coerce_text_parts(value)

    @property
    def text(self) -> str:
        """Concatenated text of every `TextPart` in `content` — the common
        case callers actually want (`extract_text()`-equivalent) without
        having to filter the parts list themselves."""
        return "".join(part.text for part in self.content if isinstance(part, TextPart))


class ToolMessageMeta(BaseModel):
    """Structured metadata attached when a tool result is registered as an
    artifact (see ``shape_artifact_registration`` POST_TOOL_USE middleware).
    Enables downstream context shapers to compact or expand the message
    without parsing free-form text."""

    artifact_id: str
    summary: str = ""
    tool_name: str = ""
    tool_args: dict[str, Any] | None = None
    result_schema: dict[str, Any] | None = None
    original_token_count: int = 0
    turn_index: int = 0


class ToolMessage(BaseModel):
    role: Literal[MessageRole.TOOL] = MessageRole.TOOL
    # Plain str covers the overwhelming common case (text-only tool
    # results); list[Part] is for tools that return images alongside text
    # (e.g. internal-knowledge search/fetch surfacing IMAGE blocks to a
    # multimodal LLM) — OpenAI and Anthropic both accept image content in
    # tool results natively; Ollama's transport falls back to `.text` only.
    content: str | list[Part] = ""
    tool_call_id: str | None = None
    is_error: bool = False
    artifact_meta: ToolMessageMeta | None = None
    # Loop-control metadata appended to the serialized message for the LLM
    # but NOT included in .content, so consumers like parent_results.py
    # that read raw content see clean tool output.
    step_footer: str = ""
    # The context layer that last shortened this result (capped, cleared,
    # replaced by a reference), on the per-call copy only. Layers read it
    # instead of matching marker text a tool's own output could contain.
    compacted_by: str | None = None

    @property
    def text(self) -> str:
        """Concatenated text of every `TextPart` in `content` when
        multipart, or `content` itself when plain `str` — the accessor
        every consumer that only cares about text (token counting,
        `parent_results.py`, context shapers) should use instead of
        reading `.content` directly."""
        if isinstance(self.content, str):
            return self.content
        return "".join(part.text for part in self.content if isinstance(part, TextPart))


Message = Annotated[
    SystemMessage | UserMessage | AssistantMessage | ToolMessage,
    Field(discriminator="role"),
]


_CONTEXT_NOTE_OPEN = '<context_note source="agent">'
_CONTEXT_NOTE_CLOSE = "</context_note>"

# Said once in every system prompt, so the model reads the wrapper below as the runtime's.
CONTEXT_NOTE_RULE = (
    f"text inside {_CONTEXT_NOTE_OPEN}...{_CONTEXT_NOTE_CLOSE} is added by the agent runtime, "
    "not written by the user."
)


def _escape_note_tags(text: str) -> str:
    # Injected text can quote tool output or a summary of it; a literal tag there would end the
    # note early and pass the rest off as the person's words.
    return text.replace("<context_note", "&lt;context_note").replace("</context_note", "&lt;/context_note")


def _is_context_note(texts: list[str]) -> bool:
    """`texts`, joined, are exactly one complete note (already rendered, so not wrapped twice)."""
    joined = "".join(texts)
    return (
        joined.startswith(_CONTEXT_NOTE_OPEN)
        and joined.endswith(_CONTEXT_NOTE_CLOSE)
        and joined.count("<context_note") == 1
        and joined.count("</context_note") == 1
    )


def render_context_note(text: str) -> str:
    """`text` wrapped as a note from the agent runtime."""
    if _is_context_note([text]):
        return text
    return f"{_CONTEXT_NOTE_OPEN}\n{_escape_note_tags(text)}\n{_CONTEXT_NOTE_CLOSE}"


def as_sent(message: Message) -> Message:
    """`message` as every transport puts it on the wire.

    Providers have no role for text the loop adds (summaries, nudges, plans, relocated images),
    so it goes in the user role; wrapped as a context note, the model can tell it from the person
    even where a provider merges it into the person's turn. Returns a copy, never changes
    `message`.
    """
    if not isinstance(message, UserMessage) or not message.injected:
        return message
    content = message.content
    if isinstance(content, str):
        return message.model_copy(update={"content": render_context_note(content)})
    if _is_context_note([part.text for part in content if isinstance(part, TextPart)]):
        return message
    parts = [TextPart(text=_escape_note_tags(part.text)) if isinstance(part, TextPart) else part for part in content]
    if parts and isinstance(parts[0], TextPart):
        parts[0] = TextPart(text=f"{_CONTEXT_NOTE_OPEN}\n{parts[0].text}")
    else:
        parts.insert(0, TextPart(text=_CONTEXT_NOTE_OPEN))
    if isinstance(parts[-1], TextPart):
        parts[-1] = TextPart(text=f"{parts[-1].text}\n{_CONTEXT_NOTE_CLOSE}")
    else:
        parts.append(TextPart(text=_CONTEXT_NOTE_CLOSE))
    return message.model_copy(update={"content": parts})


def dedupe_tool_call_ids(messages: Sequence[Message], seen: set[str]) -> int:
    """Rename, in place, each tool call whose id is empty or already in `seen`,
    and the results that answer it; `seen` gains every id kept or given.

    A provider rejects two calls sharing an id, or binds a result to the wrong
    one. Ids repeat across turns: Node saves one fixed id for every
    `ask_user_question`, and transports that number calls per response
    (`call_0`, or LangChain's empty id) reissue ids history already holds.
    Returns how many calls were renamed.
    """
    assigned: dict[str, list[str]] = {}
    used: dict[str, int] = {}
    renamed = 0
    for msg in messages:
        if isinstance(msg, AssistantMessage):
            for call in msg.tool_calls or []:
                original = call.id
                if not original or original in seen:
                    base = original or "call"
                    n = 2
                    while f"{base}_{n}" in seen:
                        n += 1
                    call.id = f"{base}_{n}"
                    renamed += 1
                seen.add(call.id)
                assigned.setdefault(original, []).append(call.id)
        elif isinstance(msg, ToolMessage) and msg.tool_call_id in assigned:
            ids = assigned[msg.tool_call_id]
            nth = used.get(msg.tool_call_id, 0)
            used[msg.tool_call_id] = nth + 1
            msg.tool_call_id = ids[min(nth, len(ids) - 1)]
    return renamed


def add_loop_note(messages: list[Message], note: str) -> str:
    """Put `note` on the latest tool result's loop footer, replacing that
    entry of `messages` with a copy so stored history keeps the plain footer.
    An instruction posing as the user right after tool output reads as a
    prompt attack to provider content filters (Azure OpenAI rejects the whole
    request), so a user message is the fallback only when no tool result
    ends the list. Returns the channel used."""
    if messages and isinstance(messages[-1], ToolMessage):
        last = messages[-1]
        messages[-1] = last.model_copy(update={"step_footer": last.step_footer + "\n" + note})
        return "step_footer"
    messages.append(UserMessage(content=note, injected=True))
    return "user_message"
