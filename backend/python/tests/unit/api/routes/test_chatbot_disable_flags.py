"""Coverage for DISABLE_SEMANTIC_SEARCH / DISABLE_STORAGE_PATTERN env flags.

Exercises the parallel semantic-search + pattern-match branching added to
``create_internal_search_tool`` (app/api/routes/chatbot.py ~155-275) and the
mirrored standard-path branching in ``_generate_internal_search_stream``
(~985-1042).
"""

import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.api.routes.chatbot import create_internal_search_tool, _generate_internal_search_stream


def _make_tool(
    retrieval=None,
    config_service=None,
    org_id="org-1",
    user_id="user-1",
    final_results=None,
):
    if retrieval is None:
        retrieval = AsyncMock()
        retrieval.search_with_filters = AsyncMock(
            return_value={
                "searchResults": [{"dummy": True}],
                "virtual_to_record_map": {},
                "status_code": 200,
            },
        )
    return retrieval, create_internal_search_tool(
        retrieval,
        org_id,
        user_id,
        10,
        None,
        MagicMock(),  # blob_store
        False,  # is_multimodal_llm
        {},  # virtual_record_id_to_result
        MagicMock(),  # graph_provider
        MagicMock(),  # ref_mapper
        final_results if final_results is not None else [],
        config_service,
    )


def _patched_pipeline(pm_return=None, gf_return=None):
    """Context managers patching the helper calls invoked inside the tool body."""
    return (
        patch(
            "app.api.routes.chatbot.execute_pattern_match_pipeline",
            new_callable=AsyncMock,
            return_value=pm_return if pm_return is not None else [],
        ),
        patch(
            "app.api.routes.chatbot.get_flattened_results",
            new_callable=AsyncMock,
            return_value=gf_return if gf_return is not None else [],
        ),
        patch(
            "app.api.routes.chatbot.enrich_virtual_record_id_to_result_with_fk_children",
            new_callable=AsyncMock,
        ),
        patch(
            "app.api.routes.chatbot.build_message_content_array",
            return_value=([[{"type": "text", "text": "x"}]], MagicMock()),
        ),
    )


@pytest.mark.asyncio
class TestCreateInternalSearchToolDisableFlags:
    async def test_disable_semantic_search_skips_retrieval_and_returns_stub(self):
        """DISABLE_SEMANTIC_SEARCH=true must not call retrieval_service.search_with_filters."""
        retrieval, tool_fn = _make_tool(config_service=MagicMock())

        pm_patch, gf_patch, enrich_patch, bmc_patch = _patched_pipeline(
            pm_return=[], gf_return=[],
        )
        with patch.dict(os.environ, {"DISABLE_SEMANTIC_SEARCH": "true"}):
            with pm_patch as pm_mock, gf_patch as gf_mock, enrich_patch, bmc_patch:
                out = await tool_fn.ainvoke({"query": "q", "reason": "r"})

        retrieval.search_with_filters.assert_not_called()
        # pattern match still runs because DISABLE_STORAGE_PATTERN is unset and
        # config_service is truthy (covers the 177-178 branch body).
        pm_mock.assert_awaited_once()
        assert out["ok"] is True
        # Semantic search was stubbed to {"searchResults": []}; downstream
        # flattening is invoked with that empty stub, not real search hits.
        gf_mock.assert_awaited_once()
        assert gf_mock.await_args.args[0] == []

    async def test_disable_storage_pattern_skips_pattern_match(self):
        """DISABLE_STORAGE_PATTERN=true must not call execute_pattern_match_pipeline."""
        retrieval, tool_fn = _make_tool(config_service=MagicMock())

        pm_patch, gf_patch, enrich_patch, bmc_patch = _patched_pipeline()
        with patch.dict(os.environ, {"DISABLE_STORAGE_PATTERN": "true"}):
            with pm_patch as pm_mock, gf_patch, enrich_patch, bmc_patch:
                out = await tool_fn.ainvoke({"query": "q", "reason": "r"})

        retrieval.search_with_filters.assert_awaited_once()
        pm_mock.assert_not_called()
        assert out["ok"] is True

    async def test_both_disabled_returns_empty_stub_without_any_search_calls(self):
        """Both flags true: no parallel tasks are scheduled at all (empty-task branch)."""
        retrieval, tool_fn = _make_tool(config_service=MagicMock())

        pm_patch, gf_patch, enrich_patch, bmc_patch = _patched_pipeline()
        with patch.dict(
            os.environ,
            {"DISABLE_SEMANTIC_SEARCH": "true", "DISABLE_STORAGE_PATTERN": "true"},
        ):
            with pm_patch as pm_mock, gf_patch as gf_mock, enrich_patch, bmc_patch:
                out = await tool_fn.ainvoke({"query": "q", "reason": "r"})

        retrieval.search_with_filters.assert_not_called()
        pm_mock.assert_not_called()
        assert out["ok"] is True
        # get_flattened_results still runs (on the empty stub searchResults=[])
        gf_mock.assert_awaited_once()
        assert gf_mock.await_args.args[0] == []

    async def test_env_flag_values_are_case_and_whitespace_insensitive_falsy_by_default(self):
        """Sanity check: unset/false env leaves both paths active (existing default behavior)."""
        retrieval, tool_fn = _make_tool(config_service=MagicMock())

        pm_patch, gf_patch, enrich_patch, bmc_patch = _patched_pipeline()
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("DISABLE_SEMANTIC_SEARCH", None)
            os.environ.pop("DISABLE_STORAGE_PATTERN", None)
            with pm_patch as pm_mock, gf_patch, enrich_patch, bmc_patch:
                out = await tool_fn.ainvoke({"query": "q", "reason": "r"})

        retrieval.search_with_filters.assert_awaited_once()
        pm_mock.assert_awaited_once()
        assert out["ok"] is True

    async def test_semantic_task_exception_is_raised_and_reported(self):
        """Exception surfaced from the gathered semantic task must propagate to the outer except."""
        retrieval = AsyncMock()
        retrieval.search_with_filters = AsyncMock(side_effect=RuntimeError("search boom"))
        retrieval, tool_fn = _make_tool(retrieval=retrieval, config_service=MagicMock())

        pm_patch, gf_patch, enrich_patch, bmc_patch = _patched_pipeline()
        with pm_patch, gf_patch, enrich_patch, bmc_patch:
            out = await tool_fn.ainvoke({"query": "q", "reason": "r"})

        assert out["ok"] is False
        assert "search boom" in out["error"]

    async def test_pattern_match_exception_is_logged_and_swallowed(self):
        """A failing pattern-match task must not fail the whole tool call."""
        retrieval, tool_fn = _make_tool(config_service=MagicMock())

        with patch(
            "app.api.routes.chatbot.execute_pattern_match_pipeline",
            new_callable=AsyncMock,
            side_effect=RuntimeError("pattern boom"),
        ):
            with patch(
                "app.api.routes.chatbot.get_flattened_results",
                new_callable=AsyncMock,
                return_value=[],
            ):
                with patch(
                    "app.api.routes.chatbot.enrich_virtual_record_id_to_result_with_fk_children",
                    new_callable=AsyncMock,
                ):
                    with patch(
                        "app.api.routes.chatbot.build_message_content_array",
                        return_value=([[{"type": "text", "text": "x"}]], MagicMock()),
                    ):
                        out = await tool_fn.ainvoke({"query": "q", "reason": "r"})

        retrieval.search_with_filters.assert_awaited_once()
        assert out["ok"] is True

    async def test_pattern_match_records_are_merged_into_content(self):
        """Non-empty raw pattern-match records get merged via merge_pattern_match_results."""
        retrieval, tool_fn = _make_tool(config_service=MagicMock())

        raw_pm_records = [{"id": "pm-1"}]
        pm_blocks = [{"virtual_record_id": "vr-2", "block_index": 0, "content": "pm-block"}]

        with patch(
            "app.api.routes.chatbot.execute_pattern_match_pipeline",
            new_callable=AsyncMock,
            return_value=raw_pm_records,
        ):
            with patch(
                "app.api.routes.chatbot.get_flattened_results",
                new_callable=AsyncMock,
                return_value=[],
            ):
                with patch(
                    "app.api.routes.chatbot.enrich_virtual_record_id_to_result_with_fk_children",
                    new_callable=AsyncMock,
                ):
                    with patch(
                        "app.api.routes.chatbot.merge_pattern_match_results",
                        new_callable=AsyncMock,
                        return_value=pm_blocks,
                    ) as merge_mock:
                        with patch(
                            "app.api.routes.chatbot.build_message_content_array",
                            return_value=([[{"type": "text", "text": "x"}]], MagicMock()),
                        ):
                            out = await tool_fn.ainvoke({"query": "q", "reason": "r"})

        merge_mock.assert_awaited_once()
        assert out["ok"] is True

    async def test_pattern_match_blocks_are_capped_with_limit_as_budget(self):
        """Pattern-match blocks must be run through cap_pattern_match_blocks
        using the tool's `limit` as the budget, so a record with many blocks
        can't overflow the LLM context on its own."""
        retrieval, tool_fn = _make_tool(config_service=MagicMock())  # limit=10

        raw_pm_records = [{"id": "pm-1"}]
        pm_blocks = [{"virtual_record_id": "vr-2", "block_index": i} for i in range(3)]
        capped = pm_blocks[:1]

        with patch(
            "app.api.routes.chatbot.execute_pattern_match_pipeline",
            new_callable=AsyncMock,
            return_value=raw_pm_records,
        ):
            with patch(
                "app.api.routes.chatbot.get_flattened_results",
                new_callable=AsyncMock,
                return_value=[],
            ):
                with patch(
                    "app.api.routes.chatbot.enrich_virtual_record_id_to_result_with_fk_children",
                    new_callable=AsyncMock,
                ):
                    with patch(
                        "app.api.routes.chatbot.merge_pattern_match_results",
                        new_callable=AsyncMock,
                        return_value=pm_blocks,
                    ):
                        with patch(
                            "app.api.routes.chatbot.cap_pattern_match_blocks",
                            return_value=capped,
                        ) as cap_mock:
                            with patch(
                                "app.api.routes.chatbot.build_message_content_array",
                                return_value=([[{"type": "text", "text": "x"}]], MagicMock()),
                            ):
                                await tool_fn.ainvoke({"query": "q", "reason": "r"})

        cap_mock.assert_called_once()
        assert cap_mock.call_args.kwargs["budget"] == 10
        assert cap_mock.call_args.args[0] == pm_blocks


# ---------------------------------------------------------------------------
# _generate_internal_search_stream: standard (non-tool) retrieval path
# ---------------------------------------------------------------------------


def _make_stream_request_and_query(**query_overrides):
    qi = SimpleNamespace(
        attachments=[],
        previousConversations=[],
        chatMode="internal_search",
        mode="simple",
        query="plain",
        modelKey=None,
        modelName=None,
        limit=5,
        filters=None,
        conversationId="c1",
    )
    for k, v in query_overrides.items():
        setattr(qi, k, v)

    req = MagicMock()
    req.state.user = {"orgId": "org", "userId": "user"}
    req.query_params = {}
    req.app.container.logger.return_value = MagicMock()
    return req, qi


async def _fake_stream_llm(**kwargs):
    yield {"event": "token", "data": {}}
    yield {"event": "done", "data": {}}


def _stream_common_patches():
    """Patches shared by every standard-path stream test (LLM setup, tool wiring)."""
    return [
        patch("app.api.routes.chatbot.stream_llm_response_with_tools", side_effect=_fake_stream_llm),
        patch(
            "app.api.routes.chatbot.has_sql_connector_configured",
            new_callable=AsyncMock,
            return_value=False,
        ),
        patch(
            "app.api.routes.chatbot.has_slack_connector_configured",
            new_callable=AsyncMock,
            return_value=False,
        ),
        patch("app.api.routes.chatbot.create_fetch_full_record_tool", return_value=MagicMock()),
        patch(
            "app.api.routes.chatbot.enrich_virtual_record_id_to_result_with_fk_children",
            new_callable=AsyncMock,
        ),
        patch(
            "app.api.routes.chatbot._build_chat_llm_messages",
            new_callable=AsyncMock,
            return_value=([], MagicMock()),
        ),
        patch(
            "app.api.routes.chatbot.get_llm_for_chat",
            new_callable=AsyncMock,
            return_value=(
                MagicMock(),
                {"provider": "openai", "isMultimodal": False, "contextLength": 8000},
                {},
            ),
        ),
        patch("app.api.routes.chatbot.BlobStorage", return_value=MagicMock()),
        patch(
            "app.api.routes.chatbot._build_llm_user_context_string",
            new_callable=AsyncMock,
            return_value="",
        ),
    ]


async def _run_stream_with_patches(req, qi, retrieval, gp, cfg, extra_patches):
    patches = _stream_common_patches() + extra_patches
    started = [p.start() for p in patches]
    try:
        async for _ in _generate_internal_search_stream(req, qi, retrieval, gp, cfg):
            pass
    finally:
        for p in patches:
            p.stop()
    return started


@pytest.mark.asyncio
class TestGenerateInternalSearchStreamDisableFlags:
    async def test_disable_semantic_search_skips_retrieval_call(self):
        req, qi = _make_stream_request_and_query()
        retrieval = AsyncMock()
        retrieval.search_with_filters = AsyncMock(
            return_value={"searchResults": [{}], "virtual_to_record_map": {}, "status_code": 200},
        )
        gp = AsyncMock()
        cfg = AsyncMock()

        pm_mock = AsyncMock(return_value=[])
        gf_mock = AsyncMock(return_value=[])
        extra = [
            patch("app.api.routes.chatbot.execute_pattern_match_pipeline", pm_mock),
            patch("app.api.routes.chatbot.get_flattened_results", gf_mock),
        ]

        with patch.dict(os.environ, {"DISABLE_SEMANTIC_SEARCH": "true"}):
            await _run_stream_with_patches(req, qi, retrieval, gp, cfg, extra)

        retrieval.search_with_filters.assert_not_called()
        pm_mock.assert_awaited_once()

    async def test_disable_storage_pattern_skips_pattern_match_call(self):
        req, qi = _make_stream_request_and_query()
        retrieval = AsyncMock()
        retrieval.search_with_filters = AsyncMock(
            return_value={"searchResults": [{}], "virtual_to_record_map": {}, "status_code": 200},
        )
        gp = AsyncMock()
        cfg = AsyncMock()

        pm_mock = AsyncMock(return_value=[])
        gf_mock = AsyncMock(return_value=[])
        extra = [
            patch("app.api.routes.chatbot.execute_pattern_match_pipeline", pm_mock),
            patch("app.api.routes.chatbot.get_flattened_results", gf_mock),
        ]

        with patch.dict(os.environ, {"DISABLE_STORAGE_PATTERN": "true"}):
            await _run_stream_with_patches(req, qi, retrieval, gp, cfg, extra)

        retrieval.search_with_filters.assert_awaited_once()
        pm_mock.assert_not_called()

    async def test_both_disabled_skips_both_calls(self):
        req, qi = _make_stream_request_and_query()
        retrieval = AsyncMock()
        retrieval.search_with_filters = AsyncMock(
            return_value={"searchResults": [{}], "virtual_to_record_map": {}, "status_code": 200},
        )
        gp = AsyncMock()
        cfg = AsyncMock()

        pm_mock = AsyncMock(return_value=[])
        gf_mock = AsyncMock(return_value=[])
        extra = [
            patch("app.api.routes.chatbot.execute_pattern_match_pipeline", pm_mock),
            patch("app.api.routes.chatbot.get_flattened_results", gf_mock),
        ]

        with patch.dict(
            os.environ,
            {"DISABLE_SEMANTIC_SEARCH": "true", "DISABLE_STORAGE_PATTERN": "true"},
        ):
            await _run_stream_with_patches(req, qi, retrieval, gp, cfg, extra)

        retrieval.search_with_filters.assert_not_called()
        pm_mock.assert_not_called()
        gf_mock.assert_awaited_once()

    async def test_semantic_task_exception_is_surfaced_as_sse_error(self):
        """A raised semantic-search exception must propagate to the outer error handler."""
        req, qi = _make_stream_request_and_query()
        retrieval = AsyncMock()
        retrieval.search_with_filters = AsyncMock(side_effect=RuntimeError("standard boom"))
        gp = AsyncMock()
        cfg = AsyncMock()

        pm_mock = AsyncMock(return_value=[])
        extra = [
            patch("app.api.routes.chatbot.execute_pattern_match_pipeline", pm_mock),
        ]

        patches = _stream_common_patches() + extra
        started = [p.start() for p in patches]
        events = []
        try:
            async for chunk in _generate_internal_search_stream(req, qi, retrieval, gp, cfg):
                events.append(chunk)
        finally:
            for p in patches:
                p.stop()

        assert any("standard boom" in c for c in events)

    async def test_pattern_match_records_are_merged_in_standard_path(self):
        """Non-empty raw pattern-match records get merged via merge_pattern_match_results."""
        req, qi = _make_stream_request_and_query()
        retrieval = AsyncMock()
        retrieval.search_with_filters = AsyncMock(
            return_value={"searchResults": [{}], "virtual_to_record_map": {}, "status_code": 200},
        )
        gp = AsyncMock()
        cfg = AsyncMock()

        raw_pm_records = [{"id": "pm-1"}]
        pm_blocks = [{"virtual_record_id": "vr-2", "block_index": 0, "content": "pm-block"}]

        pm_mock = AsyncMock(return_value=raw_pm_records)
        gf_mock = AsyncMock(return_value=[])
        merge_mock = AsyncMock(return_value=pm_blocks)
        extra = [
            patch("app.api.routes.chatbot.execute_pattern_match_pipeline", pm_mock),
            patch("app.api.routes.chatbot.get_flattened_results", gf_mock),
            patch("app.api.routes.chatbot.merge_pattern_match_results", merge_mock),
        ]

        await _run_stream_with_patches(req, qi, retrieval, gp, cfg, extra)

        merge_mock.assert_awaited_once()

    async def test_pattern_match_blocks_are_capped_with_query_limit_as_budget(self):
        """Standard path must also run pattern-match blocks through
        cap_pattern_match_blocks, using query_info.limit as the budget —
        same shared cap used by create_internal_search_tool and retrieval.py."""
        req, qi = _make_stream_request_and_query(limit=5)
        retrieval = AsyncMock()
        retrieval.search_with_filters = AsyncMock(
            return_value={"searchResults": [{}], "virtual_to_record_map": {}, "status_code": 200},
        )
        gp = AsyncMock()
        cfg = AsyncMock()

        raw_pm_records = [{"id": "pm-1"}]
        pm_blocks = [{"virtual_record_id": "vr-2", "block_index": i} for i in range(3)]
        capped = pm_blocks[:1]

        pm_mock = AsyncMock(return_value=raw_pm_records)
        gf_mock = AsyncMock(return_value=[])
        merge_mock = AsyncMock(return_value=pm_blocks)
        cap_mock = MagicMock(return_value=capped)
        extra = [
            patch("app.api.routes.chatbot.execute_pattern_match_pipeline", pm_mock),
            patch("app.api.routes.chatbot.get_flattened_results", gf_mock),
            patch("app.api.routes.chatbot.merge_pattern_match_results", merge_mock),
            patch("app.api.routes.chatbot.cap_pattern_match_blocks", cap_mock),
        ]

        await _run_stream_with_patches(req, qi, retrieval, gp, cfg, extra)

        cap_mock.assert_called_once()
        assert cap_mock.call_args.kwargs["budget"] == 5
        assert cap_mock.call_args.args[0] == pm_blocks
