"""A tool result that carries images, put on every provider wire the same way (FIX-P3-B1).

``ToolMessage.content`` is a string or a list of parts; an engine that reads an image (Claude Code's Read, Codex's
image viewer) sends the list. Wires that take an image inside a tool result (Anthropic Messages, Bedrock Converse,
the Responses API's ``function_call_output``) keep it there, in order. Wires that do not (Chat Completions refuses an
image on a ``tool`` message; Ollama's ``tool`` role and Gemini's function response take text) carry the text in the
result and relocate the images to a user message right after the run of tool results they came from. Placed there
rather than at the end of the request, history stays append-only, so the prompt-cache prefix does not move from one
turn to the next, and each image sits where it was returned.
"""

from __future__ import annotations

from dataclasses import dataclass

from harness_core.kernel.core.messages import (
    ImagePart,
    Message,
    MessageRole,
    TextPart,
    ToolMessage,
    UserMessage,
)

RELOCATED_IMAGES_PREFIX = "Images returned by the tool results above:"


def tool_result_parts(msg: ToolMessage) -> list[TextPart | ImagePart]:
    """The result's text and images in order, then its step footer. Thinking never goes back as text."""
    content = msg.content
    parts: list[TextPart | ImagePart] = (
        [p for p in content if isinstance(p, (TextPart, ImagePart))]
        if isinstance(content, list)
        else [TextPart(text=content)] if content else []
    )
    if msg.step_footer:
        parts.append(TextPart(text=msg.step_footer))
    return parts


def tool_result_text(msg: ToolMessage) -> str:
    return msg.text + msg.step_footer


def tool_result_images(msg: ToolMessage) -> list[ImagePart]:
    return [p for p in msg.content if isinstance(p, ImagePart)] if isinstance(msg.content, list) else []


@dataclass(frozen=True, slots=True)
class RelocatedImages:
    """The images of one run of consecutive tool results, for a user message after the run."""

    call_ids: tuple[str, ...]
    images: tuple[ImagePart, ...]

    @property
    def note(self) -> str:
        return f"{RELOCATED_IMAGES_PREFIX[:-1]} ({', '.join(self.call_ids)}):"


def _key(image: ImagePart) -> tuple[str, str]:
    return image.source.type, image.source.data


def relocated_images(messages: list[Message]) -> dict[int, RelocatedImages]:
    """For a wire that cannot carry an image in a tool result: keyed by the index of the last tool message of each
    run that returned images, the images to send after it.

    An image already in a user message of the request is not sent again: the native loop's fallback hook
    (``shape_retrieved_image_injection``) may have delivered it there.
    """
    delivered = {
        _key(p)
        for m in messages
        if m.role == MessageRole.USER and isinstance(m.content, list)
        for p in m.content
        if isinstance(p, ImagePart)
    }
    out: dict[int, RelocatedImages] = {}
    ids: list[str] = []
    images: list[ImagePart] = []
    for index, msg in enumerate(messages):
        if not isinstance(msg, ToolMessage):
            continue
        new = [p for p in tool_result_images(msg) if _key(p) not in delivered]
        if new:
            delivered.update(_key(p) for p in new)
            ids.append(msg.tool_call_id or "")
            images.extend(new)
        last_of_run = index + 1 == len(messages) or not isinstance(messages[index + 1], ToolMessage)
        if last_of_run and images:
            out[index] = RelocatedImages(tuple(ids), tuple(images))
            ids, images = [], []
    return out


def relocated_message(moved: RelocatedImages) -> UserMessage:
    """The user message a run's relocated images go out in. The loop adds it (``injected``), so every wire marks it as
    the runtime's context note (``as_sent``, main's TP-01), never the person's words; the LangChain arm sends the same
    message the direct transports do."""
    return UserMessage(content=[TextPart(text=moved.note), *moved.images], injected=True)


__all__ = [
    "RELOCATED_IMAGES_PREFIX",
    "RelocatedImages",
    "relocated_images",
    "relocated_message",
    "tool_result_images",
    "tool_result_parts",
    "tool_result_text",
]
