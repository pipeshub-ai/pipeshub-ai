"""Last-line enforcement of a model's image cap, at the transport boundary.

Selection already happens at the source (`app/utils/image_admission.py`), and
for a straightforward request that is enough. This guard exists for the cases
where the count the source enforced is not the count that reaches the wire:

* A sub-agent runs a different model than its parent (`domain_agents.py`
  builds its own `ModelSpec`) while sharing the parent's tool state, so images
  admitted under Anthropic's cap can arrive at a local model that takes one.
* A conversation switches models between turns, replaying history that was
  admitted for the previous one.
* Any future producer that attaches an image without going through admission.

The rule is the same one the renderers follow: drop pixels, never content. An
image that loses its place here leaves its text behind, so the model still
knows a figure existed and can ask for it again.

Every transport has to enforce it, not just one. `LangChainTransport` calls
`cap_images` itself; the direct SDK transports live in `agent_loop_lib` and
know nothing about PipesHub's image policy, so `CappedImagesTransport` wraps
them instead -- one decorator rather than the same three-method edit repeated
per provider.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from app.agent_loop_lib.core.messages import (
    ImagePart,
    Message,
    Part,
    TextPart,
)
from app.agent_loop_lib.transport.base import LLMTransport

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from app.agent_loop_lib.core.responses import ModelResponse, StructuredResponse
    from app.agent_loop_lib.core.streaming import StreamEvent
    from app.agent_loop_lib.core.tool_schema import ToolSchema

logger = logging.getLogger(__name__)

# What an image that is cut here leaves behind. Deliberately terse: this is a
# safety net, and the record's own text is already in the message.
_DROPPED_NOTE = "[image not shown: this model's per-request image limit]"


def count_images(messages: list[Message]) -> int:
    return sum(
        1
        for message in messages
        if isinstance(message.content, list)
        for part in message.content
        if isinstance(part, ImagePart)
    )


def cap_images(messages: list[Message], max_images: int) -> list[Message]:
    """Return `messages` with at most `max_images` image parts.

    Keeps the LAST `max_images` images: in an agent loop the most recent are
    the ones the current step is reasoning about, while the oldest have
    usually been summarized into text already.

    Returns the input list unchanged when it is already within the cap, so the
    common path allocates nothing.
    """
    total = count_images(messages)
    if max_images < 0 or total <= max_images:
        return messages

    # Walk backwards keeping the newest images; everything earlier is cut.
    keep_from_end = max_images
    capped: list[Message] = []
    for message in reversed(messages):
        if not isinstance(message.content, list):
            capped.append(message)
            continue
        new_content: list[Part] = []
        for part in reversed(message.content):
            if not isinstance(part, ImagePart):
                new_content.append(part)
                continue
            if keep_from_end > 0:
                keep_from_end -= 1
                new_content.append(part)
            else:
                new_content.append(TextPart(text=_DROPPED_NOTE))
        new_content.reverse()
        capped.append(message.model_copy(update={"content": new_content}))
    capped.reverse()

    logger.warning(
        "Image guard: request carried %d images but this model accepts %d; "
        "%d were replaced with a text note",
        total, max_images, total - max_images,
    )
    return capped


class CappedImagesTransport(LLMTransport):
    """Decorates any `LLMTransport`, capping the images in every request.

    Delegates unchanged apart from that: this is the transport-boundary net
    `LangChainTransport` applies inline, made available to the direct SDK
    transports without teaching each of them a policy that is not theirs to
    know. Same decorator shape as `OpikTracingTransport`.
    """

    def __init__(self, inner: LLMTransport, max_images: int) -> None:
        self._inner = inner
        self._max_images = max_images

    @property
    def provider(self) -> str:
        return self._inner.provider

    @property
    def model_name(self) -> str:
        return self._inner.model_name

    def _capped(self, messages: list[Message]) -> list[Message]:
        return cap_images(messages, self._max_images)

    # Signatures mirror `LLMTransport` rather than collecting `**kwargs`: a
    # knob added to the base class should fail here loudly, not be swallowed
    # and silently dropped before it reaches the provider.
    async def complete(
        self,
        messages: list[Message],
        tools: "list[ToolSchema] | None" = None,
        system: str | None = None,
        model: str | None = None,
        thinking_budget: int | None = None,
        effort: str | None = None,
        system_blocks: list[str] | None = None,
    ) -> "ModelResponse":
        return await self._inner.complete(
            self._capped(messages), tools, system, model,
            thinking_budget, effort, system_blocks,
        )

    async def complete_structured(
        self,
        messages: list[Message],
        output_schema: dict[str, Any],
        system: str | None = None,
        model: str | None = None,
    ) -> "StructuredResponse":
        return await self._inner.complete_structured(
            self._capped(messages), output_schema, system, model,
        )

    def stream(
        self,
        messages: list[Message],
        tools: "list[ToolSchema] | None" = None,
        system: str | None = None,
        model: str | None = None,
        thinking_budget: int | None = None,
        effort: str | None = None,
        system_blocks: list[str] | None = None,
    ) -> "AsyncIterator[StreamEvent]":
        # Not `async def`: `stream` returns its iterator rather than awaiting
        # one, so wrapping it in a coroutine would change the contract.
        return self._inner.stream(
            self._capped(messages), tools, system, model,
            thinking_budget, effort, system_blocks,
        )


def with_image_cap(transport: LLMTransport, max_images: int | None) -> LLMTransport:
    """`transport` capped at `max_images`, or unchanged when there is no cap
    to apply. Keeps the "no policy configured behaves exactly as before" rule
    every other image path follows."""
    if max_images is None:
        return transport
    return CappedImagesTransport(transport, max_images)


__all__ = ["CappedImagesTransport", "cap_images", "count_images", "with_image_cap"]
