"""Unit tests for `_FetchFullRecordTool.execute` (app.agents.agent_loop.hooks.citations),
covering the "full fetch of an image record sends no image" fix: the tool must
deliver IMAGE blocks via a multipart `ToolOutput` when the LLM is multimodal, and
must never leak an `ImagePart` to a non-multimodal LLM or once the shared
`ImageBudget` is exhausted.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agent_loop_lib.core.messages import ImagePart, TextPart
from app.agents.agent_loop.context import AgentContext
from app.agents.agent_loop.hooks.citations import CitationCollector, _FetchFullRecordTool
from app.models.blocks import BlockType
from app.utils.chat_helpers import CitationRefMapper, ImageBudget

_MIN_PNG_DATA_URI = (
    "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
    "+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def _image_record(record_id: str = "rec-1") -> dict:
    return {
        "virtual_record_id": "vr-1",
        "frontend_url": "https://a.com",
        "id": record_id,
        "context_metadata": "ctx",
        "block_containers": {
            "blocks": [
                {
                    "index": 0,
                    "type": BlockType.IMAGE.value,
                    "parent_index": None,
                    "data": {"uri": _MIN_PNG_DATA_URI},
                },
            ],
            "block_groups": [],
        },
    }


def _make_context(
    *,
    is_multimodal_llm: bool,
    image_budget: ImageBudget | None = None,
    supports_multipart_tool_result: bool = True,
) -> AgentContext:
    context = AgentContext(
        org_id="org-1", user_id="user-1", user_email="a@b.com",
        is_multimodal_llm=is_multimodal_llm,
    )
    if image_budget is not None:
        context.tool_state["image_budget"] = image_budget
    context.tool_state["virtual_record_id_to_result"] = {"vr-1": _image_record()}
    context.tool_state["citation_ref_mapper"] = CitationRefMapper()
    context.tool_state["supports_multipart_tool_result"] = supports_multipart_tool_result
    return context


def _fake_structured_tool(records: list[dict]) -> MagicMock:
    tool = MagicMock()
    tool.coroutine = AsyncMock(return_value={"ok": True, "records": records, "not_available_ids": []})
    return tool


class TestFetchFullRecordToolImageDelivery:
    @pytest.mark.asyncio
    async def test_multimodal_llm_image_record_returns_multipart_output(self):
        """The core bug fix: fetching an all-image record with a multimodal
        LLM must return a multipart `ToolOutput.data` containing an
        `ImagePart` — not just a text placeholder the image silently
        vanished from. With native multipart tool-result support (the
        default), no fallback copy is stashed into `pending_tool_images`
        since `shape_retrieved_image_injection` is never registered to
        consume it — retaining it would just leak memory across turns."""
        context = _make_context(is_multimodal_llm=True)
        collector = CitationCollector(context)
        tool = _FetchFullRecordTool(collector, context)

        with patch(
            "app.utils.fetch_full_record.create_fetch_full_record_tool",
            return_value=_fake_structured_tool([_image_record()]),
        ):
            output = await tool.execute(record_ids=["rec-1"])

        assert output.success is True
        assert isinstance(output.data, list)
        assert any(isinstance(p, TextPart) for p in output.data)
        image_parts = [p for p in output.data if isinstance(p, ImagePart)]
        assert len(image_parts) == 1
        from app.agent_loop_lib.core.messages import image_data_url

        assert image_parts[0].source.type == "base64"
        from app.agent_loop_lib.core.messages import image_data_url

        assert image_data_url(image_parts[0].source) == _MIN_PNG_DATA_URI
        assert "pending_tool_images" not in context.tool_state

    @pytest.mark.asyncio
    async def test_no_native_multipart_support_also_stashes_fallback_images(self):
        """When the resolved chat model lacks native multipart tool-result
        support (Ollama), the fetch must ALSO stash a fallback copy into
        `pending_tool_images` so `shape_retrieved_image_injection` can
        re-inject it via a `UserMessage` on the next model call."""
        context = _make_context(is_multimodal_llm=True, supports_multipart_tool_result=False)
        collector = CitationCollector(context)
        tool = _FetchFullRecordTool(collector, context)

        with patch(
            "app.utils.fetch_full_record.create_fetch_full_record_tool",
            return_value=_fake_structured_tool([_image_record()]),
        ):
            output = await tool.execute(record_ids=["rec-1"])

        assert output.success is True
        assert isinstance(output.data, list)
        assert len(context.tool_state["pending_tool_images"]) == 1

    @pytest.mark.asyncio
    async def test_non_multimodal_llm_returns_plain_text(self):
        """A text-only LLM gets a plain string — no `ImagePart` anywhere."""
        context = _make_context(is_multimodal_llm=False)
        collector = CitationCollector(context)
        tool = _FetchFullRecordTool(collector, context)

        with patch(
            "app.utils.fetch_full_record.create_fetch_full_record_tool",
            return_value=_fake_structured_tool([_image_record()]),
        ):
            output = await tool.execute(record_ids=["rec-1"])

        assert output.success is True
        assert isinstance(output.data, str)
        assert "pending_tool_images" not in context.tool_state

    @pytest.mark.asyncio
    async def test_exhausted_image_budget_falls_back_to_text(self):
        """Once the shared 50-image conversation budget is exhausted (by
        prior search/attachment images this turn), a fetched IMAGE block
        must degrade to text instead of producing another `ImagePart`."""
        exhausted_budget = ImageBudget(max_images=1)
        exhausted_budget.try_consume(1)
        context = _make_context(is_multimodal_llm=True, image_budget=exhausted_budget)
        collector = CitationCollector(context)
        tool = _FetchFullRecordTool(collector, context)

        with patch(
            "app.utils.fetch_full_record.create_fetch_full_record_tool",
            return_value=_fake_structured_tool([_image_record()]),
        ):
            output = await tool.execute(record_ids=["rec-1"])

        assert output.success is True
        assert isinstance(output.data, str)
        assert "pending_tool_images" not in context.tool_state

    @pytest.mark.asyncio
    async def test_no_records_returns_tool_output_unchanged(self):
        """A not-found response (`ok: False`) goes through `_to_tool_output`
        untouched — this tool must not alter existing non-record behavior."""
        context = _make_context(is_multimodal_llm=True)
        collector = CitationCollector(context)
        tool = _FetchFullRecordTool(collector, context)

        not_found_tool = MagicMock()
        not_found_tool.coroutine = AsyncMock(return_value={"ok": False, "message": "not found"})
        with patch(
            "app.utils.fetch_full_record.create_fetch_full_record_tool",
            return_value=not_found_tool,
        ):
            output = await tool.execute(record_ids=["missing"])

        assert "pending_tool_images" not in context.tool_state
        assert not isinstance(output.data, list)
