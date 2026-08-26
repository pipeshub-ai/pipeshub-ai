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
"""

from __future__ import annotations

import logging

from app.agent_loop_lib.core.messages import (
    ImagePart,
    Message,
    Part,
    TextPart,
)

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


__all__ = ["cap_images", "count_images"]
