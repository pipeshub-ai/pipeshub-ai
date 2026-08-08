"""Tests for ``app.agents.agent_loop.hooks.attachment_resolver`` —
first-turn resolution, follow-up turn rehydration, and early
``knowledgegraph__fetch_record`` registration."""

from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from app.agent_loop_lib.core.messages import TextPart, UserMessage
from app.agents.agent_loop.context import AgentContext
from app.agents.agent_loop.hooks.attachment_resolver import (
    _collect_historical_attachments,
    _merge_records_into_state,
    _register_fetch_tool,
    attachment_rehydration,
    resolve_attachments_for_goal,
    shape_image_injection,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _noop_next() -> None:
    return None


def _make_context(**overrides) -> AgentContext:
    defaults: dict[str, Any] = {
        "org_id": "org-1",
        "user_id": "user-1",
        "user_email": "u@example.com",
        "conversation_id": "conv-1",
    }
    defaults.update(overrides)
    return AgentContext(**defaults)


def _make_turn_ctx(*, turn_index: int = 0, with_scope: bool = True):
    goal = SimpleNamespace(constraints=[])
    if with_scope:
        registry = MagicMock()
        registry.register_tool_if_absent = MagicMock()
        spec = SimpleNamespace(tool_names=["run_code", "search_tools"])
        runtime = SimpleNamespace(tool_registry=registry)
        run_scope = SimpleNamespace(goal=goal, runtime=runtime, spec=spec)
        turn_scope = SimpleNamespace(run=run_scope)
    else:
        turn_scope = None
    ctx = SimpleNamespace(turn_index=turn_index, scope=turn_scope)
    return ctx, goal


def _pdf_attachment(name: str = "resume.pdf", vrid: str = "vrid-1") -> dict[str, Any]:
    return {
        "recordName": name,
        "mimeType": "application/pdf",
        "virtualRecordId": vrid,
        "extension": "pdf",
    }


def _fake_record(vrid: str = "vrid-1") -> dict[str, Any]:
    return {
        "id": f"rec-{vrid}",
        "virtual_record_id": vrid,
        "context_metadata": "Test record",
        "block_containers": {
            "blocks": [
                {"index": 0, "type": "text", "data": "Hello world"},
            ],
        },
    }


_LOG = logging.getLogger("test_attachment_resolver")


# ---------------------------------------------------------------------------
# _collect_historical_attachments
# ---------------------------------------------------------------------------

class TestCollectHistoricalAttachments:
    def test_extracts_from_user_query_turns(self):
        prev = [
            {"role": "user_query", "content": "tell me about this", "attachments": [_pdf_attachment()]},
            {"role": "bot_response", "content": "Here you go"},
        ]
        result = _collect_historical_attachments(prev)
        assert len(result) == 1
        assert result[0]["virtualRecordId"] == "vrid-1"

    def test_skips_bot_response_turns(self):
        prev = [
            {"role": "bot_response", "content": "hi", "attachments": [_pdf_attachment()]},
        ]
        assert _collect_historical_attachments(prev) == []

    def test_skips_attachments_without_vrid(self):
        prev = [
            {"role": "user_query", "content": "q", "attachments": [{"recordName": "x.pdf"}]},
        ]
        assert _collect_historical_attachments(prev) == []

    def test_empty_conversations(self):
        assert _collect_historical_attachments([]) == []

    def test_no_attachments_key(self):
        prev = [{"role": "user_query", "content": "hello"}]
        assert _collect_historical_attachments(prev) == []


# ---------------------------------------------------------------------------
# _merge_records_into_state
# ---------------------------------------------------------------------------

class TestMergeRecordsIntoState:
    def test_creates_vrmap_when_absent(self):
        state: dict[str, Any] = {}
        _merge_records_into_state(state, {"vrid-1": {"id": "rec-1"}})
        assert state["virtual_record_id_to_result"]["vrid-1"]["id"] == "rec-1"

    def test_does_not_overwrite_existing(self):
        state: dict[str, Any] = {
            "virtual_record_id_to_result": {"vrid-1": {"id": "existing"}},
        }
        _merge_records_into_state(state, {"vrid-1": {"id": "new"}})
        assert state["virtual_record_id_to_result"]["vrid-1"]["id"] == "existing"

    def test_noop_for_empty_records(self):
        state: dict[str, Any] = {}
        _merge_records_into_state(state, {})
        assert "virtual_record_id_to_result" not in state


# ---------------------------------------------------------------------------
# resolve_attachments_for_goal
# ---------------------------------------------------------------------------

class TestResolveAttachmentsForGoal:
    async def test_no_attachments_returns_empty(self):
        ctx = _make_context(tool_state={})
        text, images = await resolve_attachments_for_goal(ctx, _LOG)
        assert text == ""
        assert images == []

    async def test_no_blob_store_returns_empty(self):
        ctx = _make_context(tool_state={"attachments": [_pdf_attachment()]})
        text, images = await resolve_attachments_for_goal(ctx, _LOG)
        assert text == ""
        assert images == []

    async def test_resolves_and_returns_record_text(self):
        blob = AsyncMock()
        blob.get_record_from_storage = AsyncMock(return_value=_fake_record())

        ctx = _make_context(
            tool_state={"attachments": [_pdf_attachment()]},
            blob_store=blob,
        )

        with patch(
            "app.utils.attachment_utils.resolve_attachments",
            new_callable=AsyncMock,
        ) as mock_resolve:
            mock_resolve.return_value = [
                {"type": "text", "text": "<record>\nTest\n</record>"},
            ]

            text, images = await resolve_attachments_for_goal(ctx, _LOG)

        assert "Attached files from the user" in text
        assert "<record>" in text
        assert images == []

    async def test_resolves_image_blocks(self):
        blob = AsyncMock()
        blob.get_record_from_storage = AsyncMock(return_value=_fake_record())

        ctx = _make_context(
            tool_state={"attachments": [_pdf_attachment()]},
            blob_store=blob,
            is_multimodal_llm=True,
        )

        with patch(
            "app.utils.attachment_utils.resolve_attachments",
            new_callable=AsyncMock,
        ) as mock_resolve:
            mock_resolve.return_value = [
                {"type": "text", "text": "some text"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}},
            ]

            text, images = await resolve_attachments_for_goal(ctx, _LOG)

        assert "Attached files from the user" in text
        assert len(images) == 1
        assert images[0]["type"] == "image_url"

    async def test_populates_citation_maps(self):
        blob = AsyncMock()
        blob.get_record_from_storage = AsyncMock(return_value=_fake_record())

        ctx = _make_context(
            tool_state={"attachments": [_pdf_attachment()]},
            blob_store=blob,
        )

        async def fake_resolve(*args, **kwargs):
            out_records = kwargs.get("out_records")
            if out_records is not None:
                out_records["vrid-1"] = _fake_record()
            return [{"type": "text", "text": "block content"}]

        mock_resolve = AsyncMock(side_effect=fake_resolve)

        with patch(
            "app.utils.attachment_utils.resolve_attachments",
            mock_resolve,
        ):
            await resolve_attachments_for_goal(ctx, _LOG)

        assert "vrid-1" in ctx.tool_state.get("virtual_record_id_to_result", {})
        assert ctx.tool_state.get("citation_ref_mapper") is not None
        assert isinstance(ctx.tool_state.get("resolved_attachment_blocks"), list)

    async def test_handles_resolve_failure(self):
        blob = AsyncMock()
        ctx = _make_context(
            tool_state={"attachments": [_pdf_attachment()]},
            blob_store=blob,
        )

        with patch(
            "app.utils.attachment_utils.resolve_attachments",
            new_callable=AsyncMock,
            side_effect=RuntimeError("blob down"),
        ):
            text, images = await resolve_attachments_for_goal(ctx, _LOG)

        assert text == ""
        assert images == []
        assert ctx.tool_state["resolved_attachment_blocks"] == []

    async def test_does_not_write_to_blob(self):
        """Attachment is already in blob via upload API — no artifact registration."""
        blob = AsyncMock()
        ctx = _make_context(
            tool_state={"attachments": [_pdf_attachment()]},
            blob_store=blob,
        )

        with patch(
            "app.utils.attachment_utils.resolve_attachments",
            new_callable=AsyncMock,
            return_value=[{"type": "text", "text": "content"}],
        ):
            text, _images = await resolve_attachments_for_goal(ctx, _LOG)

        assert "Attached files from the user" in text
        blob.save_versioned_artifact_to_storage.assert_not_called()


# ---------------------------------------------------------------------------
# attachment_rehydration (PRE_TURN hook)
# ---------------------------------------------------------------------------

class TestAttachmentRehydration:
    async def test_skips_non_zero_turn(self):
        ctx_data, goal = _make_turn_ctx(turn_index=1)
        context = _make_context(previous_conversations=[
            {"role": "user_query", "content": "q", "attachments": [_pdf_attachment()]},
        ])
        await attachment_rehydration(context)(ctx_data, _noop_next)
        assert len(goal.constraints) == 0

    async def test_skips_when_no_previous_conversations(self):
        ctx_data, goal = _make_turn_ctx()
        context = _make_context(previous_conversations=[])
        await attachment_rehydration(context)(ctx_data, _noop_next)
        assert len(goal.constraints) == 0

    async def test_skips_when_no_scope(self):
        ctx_data, _ = _make_turn_ctx(with_scope=False)
        context = _make_context(previous_conversations=[
            {"role": "user_query", "content": "q", "attachments": [_pdf_attachment()]},
        ])
        await attachment_rehydration(context)(ctx_data, _noop_next)

    async def test_skips_when_no_blob_store(self):
        ctx_data, goal = _make_turn_ctx()
        context = _make_context(previous_conversations=[
            {"role": "user_query", "content": "q", "attachments": [_pdf_attachment()]},
        ])
        await attachment_rehydration(context)(ctx_data, _noop_next)
        assert len(goal.constraints) == 0

    async def test_populates_vrmap_and_appends_constraint(self):
        blob = AsyncMock()
        blob.get_record_from_storage = AsyncMock(return_value=_fake_record())

        ctx_data, goal = _make_turn_ctx()
        context = _make_context(
            previous_conversations=[
                {"role": "user_query", "content": "explain this", "attachments": [_pdf_attachment()]},
                {"role": "bot_response", "content": "Sure"},
            ],
            blob_store=blob,
            tool_state={},
        )

        with patch(
            "app.utils.chat_helpers.record_to_message_content",
            return_value=([], None),
        ):
            await attachment_rehydration(context)(ctx_data, _noop_next)

        assert "vrid-1" in context.tool_state.get("virtual_record_id_to_result", {})
        assert len(goal.constraints) == 1
        assert "resume.pdf" in goal.constraints[0]
        assert "knowledgegraph__fetch_record" in goal.constraints[0]
        assert "rec-vrid-1" in goal.constraints[0]

    async def test_skips_already_populated_vrids(self):
        blob = AsyncMock()
        blob.get_record_from_storage = AsyncMock(return_value=_fake_record())

        ctx_data, goal = _make_turn_ctx()
        context = _make_context(
            previous_conversations=[
                {"role": "user_query", "content": "q", "attachments": [_pdf_attachment()]},
            ],
            blob_store=blob,
            tool_state={"virtual_record_id_to_result": {"vrid-1": {"id": "existing"}}},
        )

        await attachment_rehydration(context)(ctx_data, _noop_next)

        blob.get_record_from_storage.assert_not_awaited()
        assert context.tool_state["virtual_record_id_to_result"]["vrid-1"]["id"] == "existing"
        assert len(goal.constraints) == 1
        assert '"existing"' in goal.constraints[0]

    async def test_handles_blob_fetch_failure(self):
        blob = AsyncMock()
        blob.get_record_from_storage = AsyncMock(side_effect=RuntimeError("offline"))

        ctx_data, goal = _make_turn_ctx()
        context = _make_context(
            previous_conversations=[
                {"role": "user_query", "content": "q", "attachments": [_pdf_attachment()]},
            ],
            blob_store=blob,
            tool_state={},
        )

        await attachment_rehydration(context)(ctx_data, _noop_next)

        assert len(goal.constraints) == 1
        assert "could not be loaded" in goal.constraints[0]

    async def test_skips_history_without_attachments(self):
        ctx_data, goal = _make_turn_ctx()
        context = _make_context(
            previous_conversations=[
                {"role": "user_query", "content": "hello"},
                {"role": "bot_response", "content": "hi"},
            ],
            blob_store=AsyncMock(),
        )
        await attachment_rehydration(context)(ctx_data, _noop_next)
        assert len(goal.constraints) == 0

    async def test_registers_fetch_full_record_tool(self):
        """Tool is registered in the live registry when records are resolved."""
        blob = AsyncMock()
        blob.get_record_from_storage = AsyncMock(return_value=_fake_record())

        ctx_data, goal = _make_turn_ctx()
        context = _make_context(
            previous_conversations=[
                {"role": "user_query", "content": "q", "attachments": [_pdf_attachment()]},
            ],
            blob_store=blob,
            tool_state={},
        )

        with patch(
            "app.utils.chat_helpers.record_to_message_content",
            return_value=([], None),
        ):
            await attachment_rehydration(context)(ctx_data, _noop_next)

        registry = ctx_data.scope.run.runtime.tool_registry
        registry.register_tool_if_absent.assert_called_once()
        spec = ctx_data.scope.run.spec
        assert "knowledgegraph__fetch_record" in spec.tool_names


# ---------------------------------------------------------------------------
# _register_fetch_tool
# ---------------------------------------------------------------------------

class TestRegisterFetchTool:
    def test_no_op_without_scope(self):
        ctx_data = SimpleNamespace(scope=None)
        context = _make_context(tool_state={})
        _register_fetch_tool(ctx_data, context)

    def test_delegates_to_citations_module(self):
        ctx_data, _ = _make_turn_ctx()
        context = _make_context(tool_state={"virtual_record_id_to_result": {}})
        context.root_agent_spec = SimpleNamespace(tool_names=["run_code"])

        _register_fetch_tool(ctx_data, context)

        registry = ctx_data.scope.run.runtime.tool_registry
        registry.register_tool_if_absent.assert_called_once()

    def test_no_op_without_registry(self):
        """Gracefully handles missing registry (e.g. in tests)."""
        run_scope = SimpleNamespace(runtime=SimpleNamespace(tool_registry=None))
        ctx_data = SimpleNamespace(scope=SimpleNamespace(run=run_scope))
        context = _make_context(tool_state={})
        _register_fetch_tool(ctx_data, context)


# ---------------------------------------------------------------------------
# shape_image_injection (PRE_MODEL hook)
# ---------------------------------------------------------------------------

class TestShapeImageInjection:
    async def test_noop_when_no_image_blocks(self):
        context = _make_context(tool_state={})
        mw = shape_image_injection(context)
        msg = UserMessage(content="hello")
        ctx = SimpleNamespace(messages=[msg])
        await mw(ctx, _noop_next)
        assert ctx.messages[0].content == "hello"

    async def test_injects_image_into_text_user_message(self):
        from app.agent_loop_lib.core.messages import ImagePart, TextPart

        context = _make_context(
            tool_state={},
            attachment_image_blocks=[
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc123"}},
            ],
        )
        mw = shape_image_injection(context)

        msg = UserMessage(content="describe this image")
        ctx = SimpleNamespace(messages=[msg])
        await mw(ctx, _noop_next)

        parts = ctx.messages[0].content
        assert isinstance(parts, list)
        assert len(parts) == 2
        assert isinstance(parts[0], TextPart)
        assert parts[0].text == "describe this image"
        assert isinstance(parts[1], ImagePart)
        assert parts[1].source.data == "data:image/png;base64,abc123"

    async def test_does_not_double_inject(self):
        from app.agent_loop_lib.core.messages import ImagePart, TextPart

        context = _make_context(
            tool_state={},
            attachment_image_blocks=[
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc123"}},
            ],
        )
        mw = shape_image_injection(context)

        msg = UserMessage(content="describe this image")
        ctx = SimpleNamespace(messages=[msg])

        await mw(ctx, _noop_next)
        await mw(ctx, _noop_next)

        parts = ctx.messages[0].content
        assert isinstance(parts, list)
        image_count = sum(1 for p in parts if isinstance(p, ImagePart))
        assert image_count == 1

    async def test_skips_non_user_messages(self):
        from app.agent_loop_lib.core.messages import AssistantMessage

        context = _make_context(
            tool_state={},
            attachment_image_blocks=[
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc123"}},
            ],
        )
        mw = shape_image_injection(context)

        assistant_msg = AssistantMessage(content="I am the assistant")
        user_msg = UserMessage(content="query")
        ctx = SimpleNamespace(messages=[assistant_msg, user_msg])
        await mw(ctx, _noop_next)

        assert assistant_msg.content == [TextPart(text="I am the assistant")]
        parts = user_msg.content
        assert isinstance(parts, list)
        assert any(
            getattr(p, "type", None) == "image" for p in parts
        )

    async def test_deferred_block_pickup(self):
        """Blocks set after registration are picked up on first dispatch."""
        from app.agent_loop_lib.core.messages import ImagePart

        context = _make_context(tool_state={})
        mw = shape_image_injection(context)

        context.attachment_image_blocks = [
            {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,xyz"}},
        ]

        msg = UserMessage(content="what is this?")
        ctx = SimpleNamespace(messages=[msg])
        await mw(ctx, _noop_next)

        parts = ctx.messages[0].content
        assert isinstance(parts, list)
        assert any(isinstance(p, ImagePart) for p in parts)
