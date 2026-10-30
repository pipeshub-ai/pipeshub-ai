"""Images in tool results, for providers that reject them there.

OpenAI-family models on Chat Completions and Ollama accept an image only in a
user message. Every transport that formats for them strips the images out of
the tool results and sends them once, in one user message after the results,
built here so the LangChain and direct transports deliver the same request.
The message is loop-added (``injected``): the person did not send it.
"""

from __future__ import annotations

from collections.abc import Sequence

from app.agent_loop_lib.core.messages import ImagePart, TextPart, UserMessage
from app.agent_loop_lib.core.types import Message, MessageRole

#: Opens the relocated message, so the model can tell these images from what
#: the person attached.
RELOCATED_IMAGES_NOTE = "Images returned by the tool results above:"


def tool_result_images(messages: Sequence[Message]) -> list[ImagePart]:
    """Every image inside a tool result, in order."""
    return [
        part
        for m in messages
        if m.role == MessageRole.TOOL and isinstance(m.content, list)
        for part in m.content
        if isinstance(part, ImagePart)
    ]


def has_tool_result_images(messages: Sequence[Message]) -> bool:
    return bool(tool_result_images(messages))


def relocated_tool_images(messages: Sequence[Message]) -> UserMessage | None:
    """The tool results' images as one loop-added user message, or None when
    no tool result carries an image."""
    images = tool_result_images(messages)
    if not images:
        return None
    return UserMessage(content=[TextPart(text=RELOCATED_IMAGES_NOTE), *images], injected=True)


__all__ = [
    "RELOCATED_IMAGES_NOTE",
    "has_tool_result_images",
    "relocated_tool_images",
    "tool_result_images",
]
