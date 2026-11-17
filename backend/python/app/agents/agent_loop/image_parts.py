"""Images a tool brings back, as the parts the model is sent.

Every image goes through request-wide image admission (the per-request cap, dedupe,
downscaling), as images of fetched records do.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from app.agent_loop_lib.core.messages import TextPart

if TYPE_CHECKING:
    from app.agent_loop_lib.core.messages import Part
    from app.agents.agent_loop.context import AgentContext


async def admitted_image_parts(
    image_uris: list[str], context: AgentContext | None, *, source: str,
) -> list[Part]:
    """One part per ``data:`` URI, in order: an ``ImagePart`` when admitted, otherwise a
    text note ``[image <source> not shown: <why>]`` so the model knows one was left out."""
    if context is None or not context.is_multimodal_llm:
        return [TextPart(text=f"[image {source} not shown: this model does not accept images]") for _ in image_uris]

    from app.utils.chat_helpers import image_dict_to_part
    from app.utils.image_admission import (
        ImageCandidate,
        ImageOrigin,
        admission_from_state,
    )

    admission = admission_from_state(context.tool_state)
    await admission.warm(image_uris)
    verdict = admission.admit([
        ImageCandidate(ref=str(index), data_uri=uri, origin=ImageOrigin.FETCHED_RECORD, block_index=index)
        for index, uri in enumerate(image_uris)
    ])

    parts: dict[int, Part] = {}
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
