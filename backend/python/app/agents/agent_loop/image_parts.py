"""Images a tool brings back, as the parts the model is sent.

Every image goes through request-wide image admission (the per-request cap, dedupe,
downscaling), as images of fetched records do.
"""
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from app.agent_loop_lib.core.messages import TextPart

if TYPE_CHECKING:
    from app.agent_loop_lib.core.messages import Part
    from app.agents.agent_loop.context import AgentContext


async def admitted_image_parts(
    image_uris: list[str], context: AgentContext | None, *, source: str, verified: bool = False,
) -> list[Part]:
    """One part per ``data:`` URI, in order: an ``ImagePart`` when admitted, otherwise a
    text note ``[image <source> not shown: <why>]`` so the model knows one was left out.

    An image that does not decode in full, or is animated, is left out: one such image makes a
    provider refuse the whole request. A large but valid image goes on to admission, which
    downscales it. ``verified``: the caller already checked every image (web page images are
    checked as they are read), so they are not decoded twice."""
    if context is None or not context.is_multimodal_llm:
        return [TextPart(text=f"[image {source} not shown: this model does not accept images]") for _ in image_uris]

    from app.utils.chat_helpers import image_dict_to_part
    from app.utils.image_admission import (
        ImageCandidate,
        ImageOrigin,
        admission_from_state,
    )

    parts: dict[int, Part] = {}
    readable = list(range(len(image_uris)))
    if not verified:
        from app.utils.image_utils import ImageCheck, check_data_uri

        checks = await asyncio.to_thread(lambda: [check_data_uri(uri, max_pixels=None) for uri in image_uris])
        for index, check in enumerate(checks):
            if check is not ImageCheck.OK:
                parts[index] = TextPart(text=f"[image {source} not shown: {check.value}]")
        readable = [index for index, check in enumerate(checks) if check is ImageCheck.OK]

    admission = admission_from_state(context.tool_state)
    await admission.warm([image_uris[index] for index in readable])
    verdict = admission.admit([
        ImageCandidate(ref=str(index), data_uri=image_uris[index], origin=ImageOrigin.FETCHED_RECORD, block_index=index)
        for index in readable
    ])

    for candidate in verdict.admitted:
        part = image_dict_to_part({"image_url": {"url": candidate.data_uri}})
        parts[int(candidate.ref)] = part if part is not None else TextPart(
            text=f"[image {source} not shown: unreadable image data]",
        )
    for degraded in verdict.degraded:
        parts[int(degraded.candidate.ref)] = TextPart(
            text=f"[image {source} not shown: {degraded.reason.value.replace('_', ' ')}]",
        )
    return [parts[index] for index in sorted(parts)]
