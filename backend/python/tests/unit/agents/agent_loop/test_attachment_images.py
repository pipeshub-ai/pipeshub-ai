"""The person's attached images go through the request's image admission and
survive the transport's image cap (docs/context-engineering/REPORT.md CE-10).

Before: `resolve_attachments_for_goal` built its own permissive admission, so an
attachment skipped the model's per-image limits (a 3000x2000 PNG went to Ollama
at 1567x1045, over its 1120 long edge) and did not take a slot in the request's
count. The transport cap then kept the newest images, and the attachment sits on
the current request, ahead of every image the run fetched: it was the first to go.
"""

from __future__ import annotations

import base64
import io
import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from PIL import Image

from app.agent_loop_lib.agent.hook_dispatch import dispatch_pre_model
from app.agent_loop_lib.context.base import ContextBudget
from app.agent_loop_lib.core.messages import (
    AssistantMessage,
    ImagePart,
    TextPart,
    ToolCall,
    ToolMessage,
    UserMessage,
)
from app.agents.agent_loop.context import AgentContext
from app.agents.agent_loop.factory import PipesHubAgentFactory
from app.agents.agent_loop.hooks.attachment_resolver import resolve_attachments_for_goal
from app.agents.agent_loop.image_guard import cap_images
from app.utils.chat_helpers import image_dict_to_part


def _png(width: int, height: int, color: tuple[int, int, int]) -> str:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), color).save(buf, "PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def _size(uri: str) -> tuple[int, int]:
    return Image.open(io.BytesIO(base64.b64decode(uri.split(",", 1)[1]))).size


ATTACHMENT = _png(3000, 2000, (200, 10, 10))


def _context(provider: str) -> AgentContext:
    record = {
        "id": "rec-1", "virtual_record_id": "vr1", "record_name": "shot.png",
        "block_containers": {"blocks": [{"index": 0, "type": "image", "format": "base64", "data": {"uri": ATTACHMENT}}]},
    }
    blob = MagicMock()
    blob.get_record_from_storage = AsyncMock(return_value=record)
    graph = MagicMock()
    graph.get_records_by_virtual_record_id = AsyncMock(return_value=["rec-1"])
    graph.check_record_access_with_details = AsyncMock(return_value={"id": "rec-1"})
    return AgentContext(
        org_id="org", user_id="user", user_email="user@example.com", conversation_id="c",
        llm_provider=provider, is_multimodal_llm=True, blob_store=blob, graph_provider=graph,
        tool_state={"attachments": [{"recordName": "shot.png", "mimeType": "image/png", "virtualRecordId": "vr1"}]},
    )


class TestAttachmentImagesGoThroughAdmission:
    async def test_downscaled_to_the_model_limit(self) -> None:
        context = _context("ollama")
        policy = context.tool_state["image_admission"].policy

        _, blocks = await resolve_attachments_for_goal(context, logging.getLogger("t"))

        assert len(blocks) == 1
        assert blocks[0]["user_attached"] is True
        assert max(_size(blocks[0]["image_url"]["url"])) <= policy.max_long_edge_px

    async def test_it_takes_a_slot_in_the_request(self) -> None:
        context = _context("ollama")
        admission = context.tool_state["image_admission"]
        before = admission.remaining

        await resolve_attachments_for_goal(context, logging.getLogger("t"))

        assert admission.remaining == before - 1


def _figure(i: int) -> dict[str, Any]:
    return {"type": "image_url", "image_url": {"url": _png(64 + i, 64, (10, 20 * i, 30))}}


def _retrieved_round(n_figures: int) -> list:
    call = ToolCall(id="t1", name="knowledgegraph__fetch_record", arguments={"record_ids": ["r1"]})
    parts = [TextPart(text="figures")] + [image_dict_to_part(_figure(i)) for i in range(n_figures)]
    return [
        AssistantMessage(content=[TextPart(text="Reading.")], tool_calls=[call]),
        ToolMessage(content=parts, tool_call_id="t1"),
    ]


def _user_images(messages: list) -> list[ImagePart]:
    return [
        p for m in messages if isinstance(m, UserMessage) and isinstance(m.content, list)
        for p in m.content if isinstance(p, ImagePart)
    ]


class TestTheImageCapKeepsTheAttachment:
    async def test_six_figures_and_one_attachment_under_a_cap_of_five(self) -> None:
        """The production chain puts the attachment on the current request;
        the transport cap then keeps it ahead of the fetched figures."""
        context = AgentContext(org_id="org", user_id="user", user_email="user@example.com")
        attachment = {"type": "image_url", "image_url": {"url": _png(80, 80, (250, 0, 0))}, "user_attached": True}
        context.attachment_image_blocks = [attachment]
        hooks = PipesHubAgentFactory._build_hooks(
            context, transport_registry=MagicMock(), model_name="m",
        )
        stored = [UserMessage(content="What does my screenshot show?"), *_retrieved_round(6)]
        budget = ContextBudget.for_model("m", context_window=200_000, reserved_prompt_tokens=10_000)

        sent = await dispatch_pre_model(hooks, stored, budget, turn_index=1, max_turns=30)
        capped = cap_images(sent, 5)

        kept = _user_images(capped)
        assert len(kept) == 1
        assert kept[0].source == image_dict_to_part(attachment).source
        tool_images = [p for p in capped[-1].content if isinstance(p, ImagePart)]
        assert len(tool_images) == 4

    def test_without_attachments_the_newest_images_are_kept(self) -> None:
        capped = cap_images([UserMessage(content="q"), *_retrieved_round(6)], 5)

        parts = capped[-1].content
        assert [isinstance(p, ImagePart) for p in parts[1:]] == [False, True, True, True, True, True]
