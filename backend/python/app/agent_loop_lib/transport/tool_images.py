"""Images in tool results, for providers that reject them there.

OpenAI-family models on Chat Completions and Ollama accept an image only in a
user message. Every transport that formats for them strips the images out of
the tool results and sends them in one user message after the run of results
they came from (``harness_core/models/transports/tool_results.py``), so the
LangChain and direct transports deliver the same request. That message is
loop-added (``injected``): the person did not send it.
"""

from __future__ import annotations

from collections.abc import Sequence

from app.agent_loop_lib.core.messages import ImagePart
from app.agent_loop_lib.core.types import Message, MessageRole
from harness_core.models.transports.tool_results import RELOCATED_IMAGES_PREFIX

#: Opens the relocated message, so the model can tell these images from what
#: the person attached (the message names the calls they came from).
RELOCATED_IMAGES_NOTE = RELOCATED_IMAGES_PREFIX


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


__all__ = [
    "RELOCATED_IMAGES_NOTE",
    "has_tool_result_images",
    "tool_result_images",
]
