"""Unit tests for `app.agents.chat_modes.bridge` -- the agent-loop entry
point for ALL THREE `/chat/stream` modes. Mirrors the mocking style of
`tests/unit/agents/adapter/test_sse_bridge.py::TestRunAgentLoopStream`
(same `PipesHubAgentFactory.create`/`AnswerFinalizer.run` patch points,
same fake-agent helpers) since `run_chat_stream` is deliberately the
chat-mode counterpart of `run_agent_loop_stream`.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from app.agents.chat_modes.bridge import (
    _apply_policy_to_chat_state,
    _resolve_custom_instructions,
    _resolve_web_search_config,
    _with_mode_instructions,
    run_chat_stream,
)
from app.agents.chat_modes.policy import (
    AGENT_POLICY,
    INTERNAL_SEARCH_POLICY,
    WEB_SEARCH_POLICY,
)
from app.agents.chat_modes.prefetch import PrefetchResult
from app.utils.chat_helpers import CitationRefMapper


def _stream_agent(result: Any) -> MagicMock:
    """Same shape `test_sse_bridge.py` uses: `.stream(goal)` yields nothing,
    leaves `.last_stream_result` set to `result`."""
    agent = MagicMock()
    agent.last_stream_result = None

    async def _fake_stream(goal, **kwargs):
        agent.last_stream_result = result
        return
        yield  # pragma: no cover - marks this an async generator

    agent.stream = _fake_stream
    return agent


def _patch_connectors(has_sql: bool = False, has_slack: bool = False):
    return (
        patch(
            "app.utils.execute_query.has_sql_connector_configured",
            new=AsyncMock(return_value=has_sql),
        ),
        patch(
            "app.utils.fetch_slack_thread.has_slack_connector_configured",
            new=AsyncMock(return_value=has_slack),
        ),
    )


class TestModeInstructions:
    def test_known_mode_appends_addendum_to_base_instructions(self) -> None:
        result = _with_mode_instructions("Base prompt.", INTERNAL_SEARCH_POLICY)
        assert result.startswith("Base prompt.")
        assert "internal knowledge base" in result

    def test_known_mode_with_no_base_instructions_returns_addendum_alone(self) -> None:
        result = _with_mode_instructions(None, WEB_SEARCH_POLICY)
        assert "web search" in result.lower()
        assert not result.startswith("\n")

    def test_blank_base_instructions_treated_as_absent(self) -> None:
        result = _with_mode_instructions("   ", AGENT_POLICY)
        assert result == _with_mode_instructions(None, AGENT_POLICY)


class TestApplyPolicyToChatState:
    def test_internal_search_forces_knowledge_true_web_search_off(self) -> None:
        chat_state: dict[str, Any] = {"has_sql_connector": True, "has_slack_connector": True}
        _apply_policy_to_chat_state(chat_state, INTERNAL_SEARCH_POLICY, web_search_config=None)

        assert chat_state["has_knowledge"] is True
        assert chat_state["has_sql_knowledge"] is True
        assert chat_state["has_slack_knowledge"] is True
        assert chat_state["web_search_config"] is None
        assert chat_state["chat_mode"] == "internal_search"

    def test_web_search_disables_knowledge_even_with_connectors_present(self) -> None:
        chat_state: dict[str, Any] = {"has_sql_connector": True, "has_slack_connector": True}
        web_cfg = {"provider": "tavily", "configuration": {}}
        _apply_policy_to_chat_state(chat_state, WEB_SEARCH_POLICY, web_search_config=web_cfg)

        assert chat_state["has_knowledge"] is False
        assert chat_state["has_sql_knowledge"] is False
        assert chat_state["has_slack_knowledge"] is False
        assert chat_state["web_search_config"] == web_cfg

    def test_knowledge_true_but_no_connector_present_keeps_sql_slack_off(self) -> None:
        chat_state: dict[str, Any] = {"has_sql_connector": False, "has_slack_connector": False}
        _apply_policy_to_chat_state(chat_state, AGENT_POLICY, web_search_config=None)

        assert chat_state["has_knowledge"] is True
        assert chat_state["has_sql_knowledge"] is False
        assert chat_state["has_slack_knowledge"] is False


class TestResolveCustomInstructions:
    """`_resolve_custom_instructions` -- the workspace "Custom Instructions"
    settings page (`customSystemPrompt`/`customSystemPromptWebSearch`/
    `customSystemPromptAgent` on `AIModelsConfig`), mode-resolved so each
    of the three `/chat/stream` modes picks its own configured prompt."""

    def test_internal_search_reads_custom_system_prompt_key(self) -> None:
        ai_models_config = {"customSystemPrompt": "Always answer in French."}
        result = _resolve_custom_instructions(ai_models_config, INTERNAL_SEARCH_POLICY)
        assert result == "Always answer in French."

    def test_web_search_reads_custom_system_prompt_web_search_key(self) -> None:
        ai_models_config = {"customSystemPromptWebSearch": "Always cite sources."}
        result = _resolve_custom_instructions(ai_models_config, WEB_SEARCH_POLICY)
        assert result == "Always cite sources."

    def test_agent_reads_custom_system_prompt_agent_key(self) -> None:
        ai_models_config = {"customSystemPromptAgent": "Prefer internal knowledge first."}
        result = _resolve_custom_instructions(ai_models_config, AGENT_POLICY)
        assert result == "Prefer internal knowledge first."

    def test_modes_do_not_cross_read_each_others_key(self) -> None:
        """Setting only the web-search prompt must not leak into internal_search
        or agent mode -- each mode's admin-configured prompt is independent."""
        ai_models_config = {"customSystemPromptWebSearch": "Web-only instructions."}
        assert _resolve_custom_instructions(ai_models_config, INTERNAL_SEARCH_POLICY) is None
        assert _resolve_custom_instructions(ai_models_config, AGENT_POLICY) is None

    def test_missing_config_returns_none(self) -> None:
        assert _resolve_custom_instructions({}, INTERNAL_SEARCH_POLICY) is None

    def test_blank_or_whitespace_prompt_returns_none(self) -> None:
        """An org that saved then cleared the field must omit the prompt
        builder section entirely, not render an empty '## Custom
        Instructions' header."""
        ai_models_config = {"customSystemPrompt": "   "}
        assert _resolve_custom_instructions(ai_models_config, INTERNAL_SEARCH_POLICY) is None

    def test_result_is_stripped(self) -> None:
        ai_models_config = {"customSystemPrompt": "  Be concise.  "}
        assert _resolve_custom_instructions(ai_models_config, INTERNAL_SEARCH_POLICY) == "Be concise."


class TestResolveWebSearchConfig:
    async def test_falls_back_to_duckduckgo_when_no_default_provider(self) -> None:
        """Configured providers exist but none is marked default -- the
        Node.js layer treats DuckDuckGo as the active default in that case
        (see `cm_controller.ts::getWebSearchProviders`), so this must not
        disable web_search/fetch_url entirely."""
        config_service = AsyncMock()
        config_service.get_config.return_value = {"providers": [{"provider": "tavily", "isDefault": False}]}

        result = await _resolve_web_search_config(config_service, MagicMock())
        assert result == {"provider": "duckduckgo", "configuration": {}}

    async def test_falls_back_to_duckduckgo_when_no_providers_configured(self) -> None:
        """Brand-new org that has never touched web search settings --
        `providers` is empty/absent. Must still default to DuckDuckGo, not
        silently disable the tool."""
        config_service = AsyncMock()
        config_service.get_config.return_value = {"providers": []}

        result = await _resolve_web_search_config(config_service, MagicMock())
        assert result == {"provider": "duckduckgo", "configuration": {}}

    async def test_returns_default_provider_configuration(self) -> None:
        config_service = AsyncMock()
        config_service.get_config.return_value = {
            "providers": [
                {"provider": "tavily", "isDefault": False},
                {"provider": "serper", "isDefault": True, "configuration": {"apiKey": "x"}},
            ]
        }

        result = await _resolve_web_search_config(config_service, MagicMock())
        assert result == {"provider": "serper", "configuration": {"apiKey": "x"}}

    async def test_config_lookup_failure_returns_none(self) -> None:
        config_service = AsyncMock()
        config_service.get_config.side_effect = RuntimeError("etcd unavailable")

        result = await _resolve_web_search_config(config_service, MagicMock())
        assert result is None


class TestRunChatStream:
    @staticmethod
    def _base_kwargs(policy=None) -> dict[str, Any]:
        config_service = AsyncMock()
        # `config_service.get_config` is itself an `AsyncMock` attribute
        # (child attrs of an `AsyncMock` propagate async-ness), so its
        # return value must be a concrete dict, not the default child
        # mock -- otherwise `_resolve_web_search_config`'s `.get("providers")`
        # silently returns an unawaited coroutine instead of a list.
        config_service.get_config.return_value = {"providers": []}
        return {
            "query_info": {"query": "hello", "chatMode": "agent", "filters": {}},
            "user_info": {"userId": "user-1", "orgId": "org-1"},
            "llm": MagicMock(),
            "policy": policy or AGENT_POLICY,
            "log": MagicMock(),
            "retrieval_service": AsyncMock(),
            "graph_provider": MagicMock(),
            "reranker_service": MagicMock(),
            "config_service": config_service,
        }

    async def test_build_initial_state_failure_yields_error_event(self) -> None:
        sql_patch, slack_patch = _patch_connectors()
        with (
            patch(
                "app.modules.agents.qna.chat_state.build_initial_state",
                side_effect=RuntimeError("boom"),
            ),
            sql_patch,
            slack_patch,
        ):
            events = [chunk async for chunk in run_chat_stream(**self._base_kwargs())]

        assert len(events) == 1
        assert events[0].startswith("event: error\n")
        payload = json.loads(events[0].split("data: ", 1)[1].strip())
        assert "boom" not in payload["message"]

    async def test_successful_agent_mode_run_streams_to_completion(self) -> None:
        async def _fake_create(self, context, llm, chat_mode, *, query, model_name="", model_key=None):
            agent = _stream_agent(MagicMock(success=True, error=None, output="42"))
            return agent, MagicMock(constraints=[]), MagicMock(constraints=[]), []

        async def _fake_finalizer_run(self, *, agent_success, agent_error, event_sink, agent_output=None, streamed_answer="", reasoning_turns=None):
            await event_sink.write({"event": "complete", "data": {"answer": agent_output}})
            return {"answer": agent_output}

        sql_patch, slack_patch = _patch_connectors()
        with (
            patch(
                "app.modules.agents.qna.chat_state.build_initial_state",
                return_value={"org_id": "org-1", "user_id": "user-1", "query": "hello"},
            ),
            sql_patch,
            slack_patch,
            patch("app.agents.chat_modes.bridge.PipesHubAgentFactory.create", new=_fake_create),
            patch("app.agents.chat_modes.bridge.AnswerFinalizer.run", new=_fake_finalizer_run),
        ):
            events = [chunk async for chunk in run_chat_stream(**self._base_kwargs(policy=AGENT_POLICY))]

        assert len(events) == 1
        assert events[0].startswith("event: complete\n")
        assert json.loads(events[0].split("data: ", 1)[1].strip()) == {"answer": "42"}

    async def test_web_search_mode_resolves_provider_config_before_run(self) -> None:
        """`WEB_SEARCH_POLICY.include_web_search=True` must trigger the
        config lookup, and the resolved config must land on `ChatState`
        before `build_initial_state`'s state is handed to the factory."""
        captured_chat_state: dict[str, Any] = {}

        async def _fake_create(self, context, llm, chat_mode, *, query, model_name="", model_key=None):
            captured_chat_state.update(context.tool_state)
            agent = _stream_agent(MagicMock(success=True, error=None, output="ok"))
            return agent, MagicMock(constraints=[]), MagicMock(constraints=[]), []

        async def _fake_finalizer_run(self, *, agent_success, agent_error, event_sink, agent_output=None, streamed_answer="", reasoning_turns=None):
            await event_sink.write({"event": "complete", "data": {"answer": agent_output}})
            return {"answer": agent_output}

        kwargs = self._base_kwargs(policy=WEB_SEARCH_POLICY)
        kwargs["config_service"].get_config.return_value = {
            "providers": [{"provider": "serper", "isDefault": True, "configuration": {"apiKey": "x"}}]
        }

        sql_patch, slack_patch = _patch_connectors()
        with (
            patch(
                "app.modules.agents.qna.chat_state.build_initial_state",
                return_value={"org_id": "org-1", "user_id": "user-1", "query": "hello"},
            ),
            sql_patch,
            slack_patch,
            patch("app.agents.chat_modes.bridge.PipesHubAgentFactory.create", new=_fake_create),
            patch("app.agents.chat_modes.bridge.AnswerFinalizer.run", new=_fake_finalizer_run),
        ):
            events = [chunk async for chunk in run_chat_stream(**kwargs)]

        kwargs["config_service"].get_config.assert_awaited()
        assert events[-1].startswith("event: complete\n")

    async def test_custom_instructions_land_on_tool_state_before_factory_create(self) -> None:
        """The org-level "Custom Instructions" prompt for the active mode
        must be resolved and set on `ChatState`/`AgentContext.tool_state`
        before `PipesHubAgentFactory.create()` builds the prompt builder,
        so `PipesHubPromptBuilder` can render it in the same turn."""
        captured_context: Any = None

        async def _fake_create(self, context, llm, chat_mode, *, query, model_name="", model_key=None):
            nonlocal captured_context
            captured_context = context
            agent = _stream_agent(MagicMock(success=True, error=None, output="ok"))
            return agent, MagicMock(constraints=[]), MagicMock(constraints=[]), []

        async def _fake_finalizer_run(self, *, agent_success, agent_error, event_sink, agent_output=None, streamed_answer="", reasoning_turns=None):
            await event_sink.write({"event": "complete", "data": {"answer": agent_output}})
            return {"answer": agent_output}

        kwargs = self._base_kwargs(policy=AGENT_POLICY)
        kwargs["ai_models_config"] = {"customSystemPromptAgent": "Prefer internal knowledge first."}

        sql_patch, slack_patch = _patch_connectors()
        with (
            patch(
                "app.modules.agents.qna.chat_state.build_initial_state",
                return_value={"org_id": "org-1", "user_id": "user-1", "query": "hello"},
            ),
            sql_patch,
            slack_patch,
            patch("app.agents.chat_modes.bridge.PipesHubAgentFactory.create", new=_fake_create),
            patch("app.agents.chat_modes.bridge.AnswerFinalizer.run", new=_fake_finalizer_run),
        ):
            events = [chunk async for chunk in run_chat_stream(**kwargs)]

        assert events[-1].startswith("event: complete\n")
        assert captured_context.custom_instructions == "Prefer internal knowledge first."
        assert captured_context.tool_state["custom_instructions"] == "Prefer internal knowledge first."

    async def test_no_custom_instructions_configured_leaves_field_none(self) -> None:
        """A brand-new org that never touched the "Custom Instructions" page
        must not render an empty prompt section (see `PipesHubPromptBuilder`'s
        `custom_instructions` handling)."""
        captured_context: Any = None

        async def _fake_create(self, context, llm, chat_mode, *, query, model_name="", model_key=None):
            nonlocal captured_context
            captured_context = context
            agent = _stream_agent(MagicMock(success=True, error=None, output="ok"))
            return agent, MagicMock(constraints=[]), MagicMock(constraints=[]), []

        async def _fake_finalizer_run(self, *, agent_success, agent_error, event_sink, agent_output=None, streamed_answer="", reasoning_turns=None):
            await event_sink.write({"event": "complete", "data": {"answer": agent_output}})
            return {"answer": agent_output}

        sql_patch, slack_patch = _patch_connectors()
        with (
            patch(
                "app.modules.agents.qna.chat_state.build_initial_state",
                return_value={"org_id": "org-1", "user_id": "user-1", "query": "hello"},
            ),
            sql_patch,
            slack_patch,
            patch("app.agents.chat_modes.bridge.PipesHubAgentFactory.create", new=_fake_create),
            patch("app.agents.chat_modes.bridge.AnswerFinalizer.run", new=_fake_finalizer_run),
        ):
            events = [chunk async for chunk in run_chat_stream(**self._base_kwargs(policy=AGENT_POLICY))]

        assert events[-1].startswith("event: complete\n")
        assert captured_context.custom_instructions is None

    async def test_internal_search_prefetch_merges_into_goal_and_tool_state(self) -> None:
        """`INTERNAL_SEARCH_POLICY.prefetch_retrieval=True` -- a non-empty
        `PrefetchResult` must be folded into `context.tool_state` and
        `goal.constraints`."""
        prefetch_result = PrefetchResult(
            formatted_context="Refunds are processed within 5 business days.",
            final_results=[{"virtual_record_id": "vr1"}],
            virtual_record_id_to_result={"vr1": {"recordId": "r1"}},
            tool_records=[{"recordId": "r1"}],
            citation_ref_mapper=CitationRefMapper(),
            is_empty=False,
        )
        captured: dict[str, Any] = {}

        async def _fake_create(self, context, llm, chat_mode, *, query, model_name="", model_key=None):
            goal = MagicMock(constraints=[])
            captured["context"] = context
            captured["goal"] = goal
            agent = _stream_agent(MagicMock(success=True, error=None, output="ok"))
            return agent, MagicMock(), goal, []

        async def _fake_finalizer_run(self, *, agent_success, agent_error, event_sink, agent_output=None, streamed_answer="", reasoning_turns=None):
            await event_sink.write({"event": "complete", "data": {"answer": agent_output}})
            return {"answer": agent_output}

        sql_patch, slack_patch = _patch_connectors()
        with (
            patch(
                "app.modules.agents.qna.chat_state.build_initial_state",
                return_value={"org_id": "org-1", "user_id": "user-1", "query": "hello"},
            ),
            sql_patch,
            slack_patch,
            patch("app.agents.chat_modes.bridge.PipesHubAgentFactory.create", new=_fake_create),
            patch("app.agents.chat_modes.bridge.AnswerFinalizer.run", new=_fake_finalizer_run),
            patch(
                "app.agents.chat_modes.bridge.prefetch_retrieval",
                new=AsyncMock(return_value=prefetch_result),
            ),
        ):
            events = [
                chunk
                async for chunk in run_chat_stream(**self._base_kwargs(policy=INTERNAL_SEARCH_POLICY))
            ]

        assert events[-1].startswith("event: complete\n")
        assert captured["context"].tool_state["final_results"] == prefetch_result.final_results
        assert any(
            "Refunds are processed" in str(c) for c in captured["goal"].constraints
        )

    async def test_prefetch_appends_candidate_list_when_records_are_incomplete(self) -> None:
        """In prefetch mode the retrieval tool never runs, so candidate-list
        computation must happen in bridge.py.  Without it the model is handed
        document blocks but has no coverage count ("you have 4 of 87 blocks")
        and therefore no basis to judge whether a full fetch would add anything.

        Regression test: "Summarize the document" in quick/internal-search mode
        must yield a goal constraint that includes the candidate list."""
        # Record with 10 blocks total; prefetch retrieved 2.
        record_obj = {
            "id": "rec-1",
            "record_name": "AcmeMSA.pdf",
            "block_containers": {
                "blocks": [{"type": "TEXT"} for _ in range(10)]
            },
        }
        final_results = [
            {"virtual_record_id": "vr1", "block_index": 2},
            {"virtual_record_id": "vr1", "block_index": 5},
        ]
        vr_map = {"vr1": record_obj}

        prefetch_result = PrefetchResult(
            formatted_context="[block 2 content]\n[block 5 content]",
            final_results=final_results,
            virtual_record_id_to_result=vr_map,
            tool_records=[record_obj],
            citation_ref_mapper=CitationRefMapper(),
            is_empty=False,
        )
        captured: dict[str, Any] = {}

        async def _fake_create(self, context, llm, chat_mode, *, query, model_name="", model_key=None):
            goal_obj = MagicMock(constraints=[])
            captured["context"] = context
            captured["goal"] = goal_obj
            # Simulate what router.py would set for "summarize this document"
            context.needs_whole_document = True
            context.tool_state["needs_whole_document"] = True
            agent = _stream_agent(MagicMock(success=True, error=None, output="done"))
            return agent, MagicMock(), goal_obj, []

        async def _fake_finalizer_run(self, *, agent_success, agent_error, event_sink, agent_output=None, streamed_answer="", reasoning_turns=None):
            await event_sink.write({"event": "complete", "data": {"answer": agent_output}})

        sql_patch, slack_patch = _patch_connectors()
        with (
            patch(
                "app.modules.agents.qna.chat_state.build_initial_state",
                return_value={"org_id": "org-1", "user_id": "user-1", "query": "Summarize this document"},
            ),
            sql_patch,
            slack_patch,
            patch("app.agents.chat_modes.bridge.PipesHubAgentFactory.create", new=_fake_create),
            patch("app.agents.chat_modes.bridge.AnswerFinalizer.run", new=_fake_finalizer_run),
            patch(
                "app.agents.chat_modes.bridge.prefetch_retrieval",
                new=AsyncMock(return_value=prefetch_result),
            ),
        ):
            events = [
                chunk
                async for chunk in run_chat_stream(**self._base_kwargs(policy=INTERNAL_SEARCH_POLICY))
            ]

        assert events[-1].startswith("event: complete\n")
        constraint_text = " ".join(str(c) for c in captured["goal"].constraints)
        # The candidate list must be present: the model cannot decide whether to
        # fetch without knowing it holds 2 of 10 blocks.
        assert "rec-1" in constraint_text, "candidate record_id must appear"
        assert "2 of 10 blocks" in constraint_text, "coverage counts are the load-bearing part"
        # Header must reflect the whole-document framing.
        assert "whole-document content" in constraint_text

    async def test_prefetch_candidate_list_appears_even_without_whole_document_bit(self) -> None:
        """The coverage list must be visible regardless of needs_whole_document,
        because a model blind to its own coverage cannot judge a fetch either way.
        The bit only changes the header framing, not whether the list appears."""
        record_obj = {
            "id": "rec-2",
            "record_name": "Spec.pdf",
            "block_containers": {"blocks": [{"type": "TEXT"} for _ in range(20)]},
        }
        final_results = [{"virtual_record_id": "vr2", "block_index": 1}]
        vr_map = {"vr2": record_obj}

        prefetch_result = PrefetchResult(
            formatted_context="[block 1 content]",
            final_results=final_results,
            virtual_record_id_to_result=vr_map,
            tool_records=[record_obj],
            citation_ref_mapper=CitationRefMapper(),
            is_empty=False,
        )
        captured: dict[str, Any] = {}

        async def _fake_create(self, context, llm, chat_mode, *, query, model_name="", model_key=None):
            goal_obj = MagicMock(constraints=[])
            captured["context"] = context
            captured["goal"] = goal_obj
            # Bit is False — pinpoint query, but the list must still appear.
            context.needs_whole_document = False
            context.tool_state["needs_whole_document"] = False
            agent = _stream_agent(MagicMock(success=True, error=None, output="done"))
            return agent, MagicMock(), goal_obj, []

        async def _fake_finalizer_run(self, *, agent_success, agent_error, event_sink, agent_output=None, streamed_answer="", reasoning_turns=None):
            await event_sink.write({"event": "complete", "data": {"answer": "done"}})

        sql_patch, slack_patch = _patch_connectors()
        with (
            patch(
                "app.modules.agents.qna.chat_state.build_initial_state",
                return_value={"org_id": "org-1", "user_id": "user-1", "query": "who is the CEO"},
            ),
            sql_patch,
            slack_patch,
            patch("app.agents.chat_modes.bridge.PipesHubAgentFactory.create", new=_fake_create),
            patch("app.agents.chat_modes.bridge.AnswerFinalizer.run", new=_fake_finalizer_run),
            patch(
                "app.agents.chat_modes.bridge.prefetch_retrieval",
                new=AsyncMock(return_value=prefetch_result),
            ),
        ):
            events = [
                chunk
                async for chunk in run_chat_stream(**self._base_kwargs(policy=INTERNAL_SEARCH_POLICY))
            ]

        constraint_text = " ".join(str(c) for c in captured["goal"].constraints)
        assert "rec-2" in constraint_text, "list must appear even without whole_document bit"
        assert "1 of 20 blocks" in constraint_text
        # Coverage is 5% (< low-coverage threshold), so the low-coverage header
        # applies regardless of the whole-document bit being False.
        assert "Coverage is incomplete (as low as 5%)" in constraint_text

    async def test_prefetch_collected_images_extend_attachment_image_blocks(self) -> None:
        """Prefetch has no tool result to carry a multipart `ToolMessage`, so
        its `collected_images` must be merged into `context.
        attachment_image_blocks` for the existing `shape_image_injection`
        PRE_MODEL hook to deliver -- and the shared per-request `ImageBudget`
        (from `context.tool_state`) must be the one handed to
        `prefetch_retrieval`, not a fresh one, so the 50-image cap holds
        across prefetch + search + fetch + attachments together."""
        collected_images = [{
            "ref": "ref1", "block_index": 0,
            "image_url": {"url": "data:image/png;base64,xx"},
            "virtual_record_id": "vr1",
        }]
        prefetch_result = PrefetchResult(
            formatted_context="[image block]",
            final_results=[{"virtual_record_id": "vr1"}],
            virtual_record_id_to_result={"vr1": {"recordId": "r1"}},
            tool_records=[{"recordId": "r1"}],
            citation_ref_mapper=CitationRefMapper(),
            is_empty=False,
            collected_images=collected_images,
        )
        captured: dict[str, Any] = {}
        captured_prefetch_kwargs: dict[str, Any] = {}

        async def _fake_prefetch_retrieval(**kwargs):
            captured_prefetch_kwargs.update(kwargs)
            return prefetch_result

        async def _fake_create(self, context, llm, chat_mode, *, query, model_name="", model_key=None):
            goal = MagicMock(constraints=[])
            captured["context"] = context
            agent = _stream_agent(MagicMock(success=True, error=None, output="ok"))
            return agent, MagicMock(), goal, []

        async def _fake_finalizer_run(self, *, agent_success, agent_error, event_sink, agent_output=None, streamed_answer="", reasoning_turns=None):
            await event_sink.write({"event": "complete", "data": {"answer": agent_output}})
            return {"answer": agent_output}

        sql_patch, slack_patch = _patch_connectors()
        with (
            patch(
                "app.modules.agents.qna.chat_state.build_initial_state",
                return_value={"org_id": "org-1", "user_id": "user-1", "query": "hello"},
            ),
            sql_patch,
            slack_patch,
            patch("app.agents.chat_modes.bridge.PipesHubAgentFactory.create", new=_fake_create),
            patch("app.agents.chat_modes.bridge.AnswerFinalizer.run", new=_fake_finalizer_run),
            patch(
                "app.agents.chat_modes.bridge.prefetch_retrieval",
                new=_fake_prefetch_retrieval,
            ),
        ):
            events = [
                chunk
                async for chunk in run_chat_stream(**self._base_kwargs(policy=INTERNAL_SEARCH_POLICY))
            ]

        assert events[-1].startswith("event: complete\n")
        assert captured["context"].attachment_image_blocks == collected_images
        assert captured_prefetch_kwargs["image_budget"] is captured["context"].tool_state["image_budget"]

    async def test_agent_run_failure_emits_error_event(self) -> None:
        async def _fake_create(self, context, llm, chat_mode, *, query, model_name="", model_key=None):
            raise RuntimeError("transport exploded")

        sql_patch, slack_patch = _patch_connectors()
        with (
            patch(
                "app.modules.agents.qna.chat_state.build_initial_state",
                return_value={"org_id": "org-1", "user_id": "user-1", "query": "hello"},
            ),
            sql_patch,
            slack_patch,
            patch("app.agents.chat_modes.bridge.PipesHubAgentFactory.create", new=_fake_create),
        ):
            events = [chunk async for chunk in run_chat_stream(**self._base_kwargs())]

        assert len(events) == 1
        assert events[0].startswith("event: error\n")
        payload = json.loads(events[0].split("data: ", 1)[1].strip())
        assert "transport exploded" not in payload["message"]

    async def test_sandbox_manager_destroyed_on_completion(self) -> None:
        sandbox_manager = MagicMock()
        sandbox_manager.destroy_all = AsyncMock()

        async def _fake_create(self, context, llm, chat_mode, *, query, model_name="", model_key=None):
            context.sandbox_manager = sandbox_manager
            agent = _stream_agent(MagicMock(success=True, error=None, output="ok"))
            return agent, MagicMock(), MagicMock(constraints=[]), []

        async def _fake_finalizer_run(self, *, agent_success, agent_error, event_sink, agent_output=None, streamed_answer="", reasoning_turns=None):
            await event_sink.write({"event": "complete", "data": {"answer": "ok"}})
            return {"answer": "ok"}

        sql_patch, slack_patch = _patch_connectors()
        with (
            patch(
                "app.modules.agents.qna.chat_state.build_initial_state",
                return_value={"org_id": "org-1", "user_id": "user-1", "query": "hello"},
            ),
            sql_patch,
            slack_patch,
            patch("app.agents.chat_modes.bridge.PipesHubAgentFactory.create", new=_fake_create),
            patch("app.agents.chat_modes.bridge.AnswerFinalizer.run", new=_fake_finalizer_run),
        ):
            events = [chunk async for chunk in run_chat_stream(**self._base_kwargs())]

        assert events[-1].startswith("event: complete\n")
        sandbox_manager.destroy_all.assert_awaited_once()


    async def test_client_disconnect_mid_stream_cancels_producer_and_running_agent(self) -> None:
        """Mirrors `run_agent_loop_stream()`'s disconnect-cancellation
        contract (see `test_sse_bridge.py`'s "detached_and_orphaned_spawn_
        tasks" regression tests): when the SSE consumer stops pulling
        (client disconnect -> FastAPI cancels the task awaiting `body_
        iterator.__anext__()`), `run_chat_stream()`'s `finally` block must
        cancel the still-running `_produce()` task rather than let the
        agent keep executing against an abandoned request."""
        agent_stream_started = asyncio.Event()
        agent_stream_cancelled = asyncio.Event()

        async def _hanging_stream(goal, **kwargs):
            agent_stream_started.set()
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                agent_stream_cancelled.set()
                raise
            return
            yield  # pragma: no cover - marks this an async generator

        agent = MagicMock()
        agent.last_stream_result = None
        agent.stream = _hanging_stream

        async def _fake_create(self, context, llm, chat_mode, *, query, model_name="", model_key=None):
            return agent, MagicMock(constraints=[]), MagicMock(constraints=[]), []

        sql_patch, slack_patch = _patch_connectors()
        with (
            patch(
                "app.modules.agents.qna.chat_state.build_initial_state",
                return_value={"org_id": "org-1", "user_id": "user-1", "query": "hello"},
            ),
            sql_patch,
            slack_patch,
            patch("app.agents.chat_modes.bridge.PipesHubAgentFactory.create", new=_fake_create),
        ):
            agen = run_chat_stream(**self._base_kwargs())
            pull_task = asyncio.ensure_future(agen.__anext__())
            await asyncio.wait_for(agent_stream_started.wait(), timeout=2)

            # Simulate the client going away: cancel the task that was
            # awaiting the next SSE frame, exactly as Starlette does when
            # the connection drops mid-response.
            pull_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await pull_task

            await asyncio.wait_for(agent_stream_cancelled.wait(), timeout=2)
            await agen.aclose()


class TestRunChatStreamNoToolsDegradation:
    """`supports_tool_calls=False` (Ollama) path."""

    @staticmethod
    def _base_kwargs(policy) -> dict[str, Any]:
        return {
            "query_info": {"query": "hello", "chatMode": policy.name, "filters": {}},
            "user_info": {"userId": "user-1", "orgId": "org-1"},
            "llm": MagicMock(),
            "policy": policy,
            "log": MagicMock(),
            "retrieval_service": AsyncMock(),
            "graph_provider": MagicMock(),
            "reranker_service": MagicMock(),
            "config_service": AsyncMock(),
            "supports_tool_calls": False,
        }

    async def test_web_search_mode_fails_fast_with_unsupported_model_error(self) -> None:
        events = [chunk async for chunk in run_chat_stream(**self._base_kwargs(WEB_SEARCH_POLICY))]

        assert len(events) == 1
        assert events[0].startswith("event: error\n")
        payload = json.loads(events[0].split("data: ", 1)[1].strip())
        assert payload["type"] == "unsupported_model_mode"

    async def test_agent_mode_fails_fast_with_unsupported_model_error(self) -> None:
        events = [chunk async for chunk in run_chat_stream(**self._base_kwargs(AGENT_POLICY))]

        assert len(events) == 1
        payload = json.loads(events[0].split("data: ", 1)[1].strip())
        assert payload["type"] == "unsupported_model_mode"

    async def test_internal_search_mode_degrades_to_forced_prefetch_and_streams(self) -> None:
        prefetch_result = PrefetchResult(
            formatted_context="Context for no-tools answer.",
            final_results=[{"virtual_record_id": "vr1"}],
            virtual_record_id_to_result={},
            tool_records=[],
            citation_ref_mapper=CitationRefMapper(),
            is_empty=False,
        )

        async def _fake_handle_simple_mode(**kwargs):
            yield {"event": "answer_chunk", "data": {"chunk": "Answer", "accumulated": "Answer"}}
            yield {"event": "complete", "data": {"answer": "Answer"}}

        with (
            patch(
                "app.agents.chat_modes.bridge.prefetch_retrieval",
                new=AsyncMock(return_value=prefetch_result),
            ) as mock_prefetch,
            patch(
                "app.agents.chat_modes.bridge.handle_simple_mode",
                new=_fake_handle_simple_mode,
            ),
        ):
            events = [
                chunk
                async for chunk in run_chat_stream(**self._base_kwargs(INTERNAL_SEARCH_POLICY))
            ]

        # `force=True` regardless of follow-up detection -- no tool exists to fall back on.
        assert mock_prefetch.call_args.kwargs["force"] is True
        event_names = [chunk.split("\n", 1)[0] for chunk in events]
        assert "event: status" in event_names
        assert event_names[-1] == "event: complete"
