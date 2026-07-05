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

from enum import Enum
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, field_validator

__all__ = [
    "MessageRole",
    "ToolCall",
    "ImageSource",
    "TextPart",
    "ThinkingPart",
    "ImagePart",
    "Part",
    "SystemMessage",
    "UserMessage",
    "AssistantMessage",
    "ToolMessage",
    "Message",
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


class ImageSource(BaseModel):
    type: str                      # "base64" | "url"
    media_type: str | None = None  # "image/jpeg" | "image/png" | "image/gif" | "image/webp"
    data: str = ""                 # base64 string (type="base64") or URL (type="url")


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


class ToolMessage(BaseModel):
    role: Literal[MessageRole.TOOL] = MessageRole.TOOL
    content: str = ""
    tool_call_id: str | None = None
    is_error: bool = False


Message = Annotated[
    SystemMessage | UserMessage | AssistantMessage | ToolMessage,
    Field(discriminator="role"),
]
