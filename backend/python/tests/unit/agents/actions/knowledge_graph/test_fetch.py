"""Tests for ``app.agents.actions.knowledge_graph.ops.fetch``."""
from __future__ import annotations

import os
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.actions.knowledge_graph.ops.fetch import (
    FETCH_RECORD_TOOL_NAME,
    execute_fetch_record,
    resolve_block_cap,
)


class TestResolveBlockCap:
    def test_default_when_env_unset(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("PIPESHUB_FULL_RECORD_MAX_BLOCKS", None)
            assert resolve_block_cap("gpt-4", None) == 200

    def test_env_override(self) -> None:
        with patch.dict(os.environ, {"PIPESHUB_FULL_RECORD_MAX_BLOCKS": "50"}):
            assert resolve_block_cap("gpt-4", None) == 50

    def test_requested_max_lower_than_env(self) -> None:
        with patch.dict(os.environ, {"PIPESHUB_FULL_RECORD_MAX_BLOCKS": "100"}):
            assert resolve_block_cap("gpt-4", 30) == 30

    def test_requested_max_higher_than_env(self) -> None:
        with patch.dict(os.environ, {"PIPESHUB_FULL_RECORD_MAX_BLOCKS": "50"}):
            assert resolve_block_cap("gpt-4", 200) == 50

    def test_zero_env_uses_default(self) -> None:
        with patch.dict(os.environ, {"PIPESHUB_FULL_RECORD_MAX_BLOCKS": "0"}):
            assert resolve_block_cap("gpt-4", None) == 200

    def test_negative_env_uses_default(self) -> None:
        with patch.dict(os.environ, {"PIPESHUB_FULL_RECORD_MAX_BLOCKS": "-5"}):
            assert resolve_block_cap("gpt-4", None) == 200

    def test_invalid_env_uses_default(self) -> None:
        with patch.dict(os.environ, {"PIPESHUB_FULL_RECORD_MAX_BLOCKS": "abc"}):
            assert resolve_block_cap("gpt-4", None) == 200

    def test_whitespace_env_uses_default(self) -> None:
        with patch.dict(os.environ, {"PIPESHUB_FULL_RECORD_MAX_BLOCKS": "  "}):
            assert resolve_block_cap("gpt-4", None) == 200

    def test_requested_max_zero_ignored(self) -> None:
        with patch.dict(os.environ, {"PIPESHUB_FULL_RECORD_MAX_BLOCKS": "100"}):
            assert resolve_block_cap("gpt-4", 0) == 100

    def test_requested_max_negative_ignored(self) -> None:
        with patch.dict(os.environ, {"PIPESHUB_FULL_RECORD_MAX_BLOCKS": "100"}):
            assert resolve_block_cap("gpt-4", -1) == 100


def _make_context(**overrides: Any) -> SimpleNamespace:
    defaults: dict[str, Any] = {
        "model_name": "gpt-4",
        "org_id": "org-1",
        "user_id": "user-1",
        "graph_provider": AsyncMock(),
        "full_records_fetched": set(),
        "tool_state": {},
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


class TestExecuteFetchRecord:
    @pytest.mark.asyncio
    async def test_happy_path_single_record(self) -> None:
        fake_tool = MagicMock()
        fake_tool.coroutine = AsyncMock(return_value={
            "ok": True,
            "records": [{"id": "rec-1", "blocks": []}],
            "not_available_ids": [],
        })

        ref_mapper = MagicMock()
        content_item = [{"type": "text", "text": "Hello world"}]

        with patch.dict(os.environ, {}, clear=False), \
             patch(
                 "app.utils.fetch_full_record.create_fetch_full_record_tool",
                 return_value=fake_tool,
             ), \
             patch(
                 "app.utils.chat_helpers.record_to_message_content",
                 return_value=(content_item, ref_mapper),
             ), \
             patch(
                 "app.utils.chat_helpers.get_record_id_shortener_if_enabled",
                 return_value=None,
             ):
            os.environ.pop("PIPESHUB_FULL_RECORD_MAX_BLOCKS", None)
            ctx = _make_context()
            result = await execute_fetch_record(
                context=ctx,
                virtual_records={"vr-1": {"id": "rec-1"}},
                citation_ref_mapper=ref_mapper,
                record_ids="rec-1",
            )

        assert result["success"] is True
        assert "Hello world" in result["text"]
        assert "rec-1" in ctx.full_records_fetched

    @pytest.mark.asyncio
    async def test_string_record_id_converted_to_list(self) -> None:
        fake_tool = MagicMock()
        fake_tool.coroutine = AsyncMock(return_value={
            "ok": True,
            "records": [{"id": "rec-1"}],
            "not_available_ids": [],
        })
        ref_mapper = MagicMock()

        with patch(
            "app.utils.fetch_full_record.create_fetch_full_record_tool",
            return_value=fake_tool,
        ), patch(
            "app.utils.chat_helpers.record_to_message_content",
            return_value=([{"type": "text", "text": "x"}], ref_mapper),
        ), patch(
            "app.utils.chat_helpers.get_record_id_shortener_if_enabled",
            return_value=None,
        ):
            ctx = _make_context()
            result = await execute_fetch_record(
                context=ctx,
                virtual_records={},
                citation_ref_mapper=ref_mapper,
                record_ids="single-id",
            )
        assert result["success"] is True
        call_kwargs = fake_tool.coroutine.call_args[1]
        assert call_kwargs["record_ids"] == ["single-id"]

    @pytest.mark.asyncio
    async def test_exception_returns_error(self) -> None:
        fake_tool = MagicMock()
        fake_tool.coroutine = AsyncMock(side_effect=RuntimeError("db down"))

        ref_mapper = MagicMock()

        with patch(
            "app.utils.fetch_full_record.create_fetch_full_record_tool",
            return_value=fake_tool,
        ), patch(
            "app.utils.chat_helpers.get_record_id_shortener_if_enabled",
            return_value=None,
        ):
            result = await execute_fetch_record(
                context=_make_context(),
                virtual_records={},
                citation_ref_mapper=ref_mapper,
                record_ids=["rec-1"],
            )
        assert result["success"] is False
        assert "db down" in result["error"]

    @pytest.mark.asyncio
    async def test_not_available_ids_appended(self) -> None:
        fake_tool = MagicMock()
        fake_tool.coroutine = AsyncMock(return_value={
            "ok": True,
            "records": [{"id": "rec-1"}],
            "not_available_ids": ["rec-2"],
        })
        ref_mapper = MagicMock()

        with patch(
            "app.utils.fetch_full_record.create_fetch_full_record_tool",
            return_value=fake_tool,
        ), patch(
            "app.utils.chat_helpers.record_to_message_content",
            return_value=([{"type": "text", "text": "content"}], ref_mapper),
        ), patch(
            "app.utils.chat_helpers.get_record_id_shortener_if_enabled",
            return_value=None,
        ):
            result = await execute_fetch_record(
                context=_make_context(),
                virtual_records={},
                citation_ref_mapper=ref_mapper,
                record_ids=["rec-1", "rec-2"],
            )
        assert result["success"] is True
        assert "not available" in result["text"]
        assert "rec-2" in result["text"]

    @pytest.mark.asyncio
    async def test_non_ok_result_returns_tool_output(self) -> None:
        fake_tool = MagicMock()
        fake_tool.coroutine = AsyncMock(return_value={
            "ok": False,
            "error": "record not found",
        })
        ref_mapper = MagicMock()

        with patch(
            "app.utils.fetch_full_record.create_fetch_full_record_tool",
            return_value=fake_tool,
        ), patch(
            "app.utils.chat_helpers.get_record_id_shortener_if_enabled",
            return_value=None,
        ), patch(
            "app.agents.agent_loop.tool_adapter._to_tool_output",
            return_value=SimpleNamespace(success=False, data=None, error="record not found"),
        ):
            result = await execute_fetch_record(
                context=_make_context(),
                virtual_records={},
                citation_ref_mapper=ref_mapper,
                record_ids=["rec-1"],
            )
        assert result["success"] is False
        assert result["error"] == "record not found"

    @pytest.mark.asyncio
    async def test_record_id_shortener_resolves_and_shortens(self) -> None:
        fake_tool = MagicMock()
        fake_tool.coroutine = AsyncMock(return_value={
            "ok": True,
            "records": [{"id": "full-rec-id"}],
            "not_available_ids": [],
        })
        ref_mapper = MagicMock()
        shortener = MagicMock()
        shortener.resolve = MagicMock(side_effect=lambda x: "full-rec-id" if x == "R1" else x)
        shortener.shorten_record_ids_in_text = MagicMock(side_effect=lambda t: t.replace("full-rec-id", "R1"))

        with patch(
            "app.utils.fetch_full_record.create_fetch_full_record_tool",
            return_value=fake_tool,
        ), patch(
            "app.utils.chat_helpers.record_to_message_content",
            return_value=([{"type": "text", "text": "Record ID: full-rec-id"}], ref_mapper),
        ), patch(
            "app.utils.chat_helpers.get_record_id_shortener_if_enabled",
            return_value=shortener,
        ):
            ctx = _make_context()
            result = await execute_fetch_record(
                context=ctx,
                virtual_records={},
                citation_ref_mapper=ref_mapper,
                record_ids=["R1"],
            )
        shortener.resolve.assert_called_once_with("R1")
        assert result["success"] is True
        assert "R1" in result["text"]

    @pytest.mark.asyncio
    async def test_not_available_ids_shortened(self) -> None:
        fake_tool = MagicMock()
        fake_tool.coroutine = AsyncMock(return_value={
            "ok": True,
            "records": [{"id": "rec-1"}],
            "not_available_ids": ["rec-2"],
        })
        ref_mapper = MagicMock()
        shortener = MagicMock()
        shortener.resolve = MagicMock(side_effect=lambda x: x)
        shortener.shorten_record_ids_in_text = MagicMock(side_effect=lambda t: t)
        shortener.get_or_create_short_id = MagicMock(return_value="R2")

        with patch(
            "app.utils.fetch_full_record.create_fetch_full_record_tool",
            return_value=fake_tool,
        ), patch(
            "app.utils.chat_helpers.record_to_message_content",
            return_value=([{"type": "text", "text": "content"}], ref_mapper),
        ), patch(
            "app.utils.chat_helpers.get_record_id_shortener_if_enabled",
            return_value=shortener,
        ):
            result = await execute_fetch_record(
                context=_make_context(),
                virtual_records={},
                citation_ref_mapper=ref_mapper,
                record_ids=["rec-1", "rec-2"],
            )
        assert "'R2'" in result["text"]


class TestFetchRecordToolName:
    def test_constant_value(self) -> None:
        assert FETCH_RECORD_TOOL_NAME == "knowledgegraph__fetch_record"
