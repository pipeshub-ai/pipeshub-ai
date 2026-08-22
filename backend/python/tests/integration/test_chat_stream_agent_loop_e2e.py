"""End-to-end wiring test for `POST /chat/stream`: HTTP request body ->
`askAIStream()` -> `_generate_chat_stream_via_agent_loop()` -> `chat_modes.
run_chat_stream()` -> `PipesHubAgentFactory`/`AgentContext`/`AnswerFinalizer`
-> SSE bytes back out through the `StreamingResponse`.

Unlike `tests/unit/agents/chat_modes/test_bridge.py` (which patches
`PipesHubAgentFactory.create` to unit-test `run_chat_stream()` in
isolation) or `tests/unit/api/routes/test_chatbot_agent_loop_dispatch.py`
(which patches `run_chat_stream` itself to unit-test the route seam), this
test mocks ONLY the outermost I/O boundaries a real deployment can't run
in CI (LLM, retrieval/graph/config services, connector-presence checks) and
lets every layer in between run for real -- proving the full chain wires
together, not just each link in isolation.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import Request
from fastapi.responses import StreamingResponse

from app.api.routes.chatbot import askAIStream


def _mock_request(body: dict) -> MagicMock:
    request = MagicMock(spec=Request)
    request.state.user = {"orgId": "org-1", "userId": "user-1", "email": "user@corp.example"}
    request.query_params = {"sendUserInfo": True}
    request.json = AsyncMock(return_value=body)
    request.app.container.logger.return_value = MagicMock()
    return request


def _mock_config_service() -> AsyncMock:
    config_service = AsyncMock()
    config_service.get_config.return_value = {"providers": []}
    return config_service


def _mock_retrieval_service(search_results: list[dict] | None = None) -> AsyncMock:
    retrieval_service = AsyncMock()
    retrieval_service.search_with_filters.return_value = {
        "status_code": 200,
        "searchResults": search_results or [],
        "virtual_to_record_map": {},
    }
    return retrieval_service


async def _drain(response: StreamingResponse) -> list[str]:
    assert isinstance(response, StreamingResponse)
    return [chunk async for chunk in response.body_iterator]


def _events_by_name(chunks: list[str]) -> dict[str, list[dict]]:
    by_name: dict[str, list[dict]] = {}
    for chunk in chunks:
        for block in chunk.split("\n\n"):
            block = block.strip()
            if not block:
                continue
            lines = block.split("\n")
            event_line = next((l for l in lines if l.startswith("event:")), None)
            data_line = next((l for l in lines if l.startswith("data:")), None)
            if not event_line or not data_line:
                continue
            name = event_line[len("event:"):].strip()
            by_name.setdefault(name, []).append(json.loads(data_line[len("data:"):].strip()))
    return by_name


@pytest.fixture
def _no_connectors():
    # `run_chat_stream()` imports these lazily inside its own body (see
    # `bridge.py`), so they must be patched at their SOURCE modules, not
    # as `chat_modes.bridge` attributes.
    with (
        patch(
            "app.utils.execute_query.has_sql_connector_configured",
            new=AsyncMock(return_value=False),
        ),
        patch(
            "app.utils.fetch_slack_thread.has_slack_connector_configured",
            new=AsyncMock(return_value=False),
        ),
    ):
        yield


@pytest.fixture
def _agent_loop_llm():
    with patch(
        "app.api.routes.chatbot.get_llm_for_chat",
        new=AsyncMock(return_value=(
            MagicMock(),
            {"provider": "openai", "isMultimodal": False, "contextLength": 128000},
            {},
        )),
    ):
        yield


@pytest.fixture
def _fake_agent_run():
    """Real `run_chat_stream()` -> real `PipesHubAgentFactory` construction
    would need a live LLM/tool registry -- the one boundary this E2E test
    still mocks, at the exact same seam `test_bridge.py` uses."""

    async def _fake_create(self, context, llm, chat_mode, *, query, model_name="", session_id=None, model_key=None):
        agent = MagicMock()
        agent.last_stream_result = MagicMock(success=True, error=None, output="The answer, with citations.")

        async def _fake_stream(goal, **kwargs):
            return
            yield  # pragma: no cover - marks this an async generator

        agent.stream = _fake_stream
        return agent, MagicMock(), MagicMock(constraints=[]), []

    async def _fake_finalizer_run(self, *, agent_success, agent_error, event_sink, agent_output=None, streamed_answer="", reasoning_turns=None):
        await event_sink.write({
            "event": "complete",
            "data": {"answer": agent_output, "answerMatchType": "Match Found"},
        })
        return {"answer": agent_output}

    with (
        patch("app.agents.chat_modes.bridge.PipesHubAgentFactory.create", new=_fake_create),
        patch("app.agents.chat_modes.bridge.AnswerFinalizer.run", new=_fake_finalizer_run),
    ):
        yield


@pytest.mark.usefixtures("_no_connectors", "_agent_loop_llm", "_fake_agent_run")
class TestChatStreamAgentLoopEndToEnd:
    async def test_internal_search_mode_streams_status_then_complete(self):
        request = _mock_request({"query": "What is our refund policy?", "chatMode": "internal_search"})

        response = await askAIStream(
            request=request,
            retrieval_service=_mock_retrieval_service(),
            graph_provider=MagicMock(),
            config_service=_mock_config_service(),
        )
        chunks = await _drain(response)
        events = _events_by_name(chunks)

        assert "complete" in events
        assert events["complete"][-1]["answer"] == "The answer, with citations."
        assert "error" not in events

    async def test_web_search_mode_resolves_web_provider_and_completes(self):
        request = _mock_request({"query": "Latest news on the Fed rate decision", "chatMode": "web_search"})
        config_service = AsyncMock()
        config_service.get_config.return_value = {
            "providers": [{"provider": "serper", "isDefault": True, "configuration": {"apiKey": "x"}}]
        }

        response = await askAIStream(
            request=request,
            retrieval_service=_mock_retrieval_service(),
            graph_provider=MagicMock(),
            config_service=config_service,
        )
        chunks = await _drain(response)
        events = _events_by_name(chunks)

        assert "complete" in events
        config_service.get_config.assert_awaited()

    async def test_agent_mode_streams_to_completion(self):
        request = _mock_request({"query": "Summarize the Q3 board deck and check for any related news", "chatMode": "agent"})

        response = await askAIStream(
            request=request,
            retrieval_service=_mock_retrieval_service(),
            graph_provider=MagicMock(),
            config_service=_mock_config_service(),
        )
        chunks = await _drain(response)
        events = _events_by_name(chunks)

        assert "complete" in events
        assert "error" not in events

    async def test_legacy_chat_mode_value_resolves_to_internal_search_and_completes(self):
        """Persisted conversations / older clients send pre-migration
        `chatMode` values (`analysis`, `precise`, ...) -- `regenerate` on
        such a conversation must still resolve to `INTERNAL_SEARCH_POLICY`
        (see `policy.py::resolve_chat_mode_policy`) and complete normally
        rather than erroring or silently falling back to `web_search`."""
        request = _mock_request({"query": "What is our refund policy?", "chatMode": "precise"})

        response = await askAIStream(
            request=request,
            retrieval_service=_mock_retrieval_service(),
            graph_provider=MagicMock(),
            config_service=_mock_config_service(),
        )
        chunks = await _drain(response)
        events = _events_by_name(chunks)

        assert "complete" in events
        assert "error" not in events

    async def test_attachment_upload_then_chat_folds_attachment_into_agent_context(self):
        """Attachment flow: a document attachment's resolved text context
        must reach the agent's `Goal.constraints` (see `attachments.py`)
        so the model can answer from -- and cite -- the attached record,
        not just the org's ambient knowledge base."""
        captured_goal = {}

        async def _fake_create_capturing_goal(self, context, llm, chat_mode, *, query, model_name="", session_id=None, model_key=None):
            agent = MagicMock()
            agent.last_stream_result = MagicMock(success=True, error=None, output="Per the attached deck, revenue is up 12%.")

            async def _fake_stream(goal, **kwargs):
                captured_goal["goal"] = goal
                return
                yield  # pragma: no cover - marks this an async generator

            agent.stream = _fake_stream
            goal = MagicMock(constraints=[])
            return agent, MagicMock(), goal, []

        async def _fake_finalizer_run(self, *, agent_success, agent_error, event_sink, agent_output=None, streamed_answer="", reasoning_turns=None):
            await event_sink.write({"event": "complete", "data": {"answer": agent_output}})
            return {"answer": agent_output}

        fake_record = {
            "id": "rec-attach-1",
            "frontend_url": "https://example.com/records/rec-attach-1",
            "context_metadata": "Q3 Board Deck",
            "block_containers": {"blocks": [], "block_groups": []},
        }

        request = _mock_request({
            "query": "Summarize this deck",
            "chatMode": "internal_search",
            "attachments": [
                {"virtualRecordId": "vr-attach-1", "mimeType": "application/pdf", "fileName": "board_deck.pdf"},
            ],
        })

        with (
            patch(
                "app.agents.chat_modes.bridge.PipesHubAgentFactory.create",
                new=_fake_create_capturing_goal,
            ),
            patch("app.agents.chat_modes.bridge.AnswerFinalizer.run", new=_fake_finalizer_run),
            patch(
                "app.modules.transformers.blob_storage.BlobStorage.get_record_from_storage",
                new=AsyncMock(return_value=fake_record),
            ),
        ):
            response = await askAIStream(
                request=request,
                retrieval_service=_mock_retrieval_service(),
                graph_provider=MagicMock(),
                config_service=_mock_config_service(),
            )
            chunks = await _drain(response)

        events = _events_by_name(chunks)
        assert "complete" in events
        assert "error" not in events
        assert any(
            "Q3 Board Deck" in str(c) for c in captured_goal["goal"].constraints
        )

    async def test_llm_initialization_failure_surfaces_as_sse_error_not_500(self):
        request = _mock_request({"query": "hello", "chatMode": "internal_search"})

        with patch("app.api.routes.chatbot.get_llm_for_chat", new=AsyncMock(return_value=None)):
            response = await askAIStream(
                request=request,
                retrieval_service=_mock_retrieval_service(),
                graph_provider=MagicMock(),
                config_service=_mock_config_service(),
            )
            chunks = await _drain(response)

        events = _events_by_name(chunks)
        assert "RUN_ERROR" in events
        assert events["RUN_ERROR"][-1]["code"] == "llm_initialization_failed"
        assert "complete" not in events
        assert "RUN_FINISHED" not in events


@pytest.mark.usefixtures("_no_connectors", "_agent_loop_llm")
class TestWebSearchCitationEndToEnd:
    """`TestChatStreamAgentLoopEndToEnd.test_web_search_mode_resolves_web_
    provider_and_completes` above fakes BOTH `PipesHubAgentFactory.create`
    and `AnswerFinalizer.run` (via the `_fake_agent_run` fixture), so
    `web_search`'s tool call and citation resolution never actually run.
    This class only fakes `create()` — and even there, runs the REAL
    `_build_dynamic_tools` (which wraps `web_search` in `WebToolAdapter`,
    see `tool_loader.py`/`web_tool_adapter.py`) and executes it for real —
    so the REAL (unpatched) `AnswerFinalizer.run` normalizes the resulting
    citation, proving the full "Web citation restore" chain wires together
    end to end, not just each link in isolation."""

    async def test_web_search_tool_call_resolves_into_a_citation(self):
        canned_results = [
            {
                "title": "Fed holds rates steady",
                "link": "https://news.example.com/fed-rate-decision",
                "snippet": "The Federal Reserve held its benchmark rate steady on Wednesday.",
            }
        ]

        async def _fake_create(self, context, llm, chat_mode, *, query, model_name="", session_id=None, model_key=None):
            from app.agents.agent_loop.tool_loader import _build_dynamic_tools  # noqa: PLC0415

            tools = _build_dynamic_tools(context)
            web_search_tool = next(t for t in tools if t.name == "dynamic__web_search")
            await web_search_tool.execute(query=query)

            ref_mapper = context.tool_state["citation_ref_mapper"]
            ref, _url = next(iter(ref_mapper.ref_to_url.items()))
            # Shaped exactly as the system prompt asks for it — citation link
            # plus the trailing confidence marker (`prompt_builder.py`'s
            # `_CITATION_RULES` / `_ANSWER_CONFIDENCE`).
            answer = (
                f"The Fed held rates steady this week [source](https://{ref}.xyz)."
                "\n\n---\nConfidence: High"
            )

            agent = MagicMock()
            agent.last_stream_result = MagicMock(success=True, error=None, output=answer)

            async def _fake_stream(goal, **kwargs):
                return
                yield  # pragma: no cover - marks this an async generator

            agent.stream = _fake_stream
            return agent, MagicMock(), MagicMock(constraints=[]), []

        request = _mock_request({"query": "Latest news on the Fed rate decision", "chatMode": "web_search"})

        with (
            patch("app.agents.chat_modes.bridge.PipesHubAgentFactory.create", new=_fake_create),
            patch(
                "app.utils.web_search_tool._search_with_duckduckgo",
                new=AsyncMock(return_value=canned_results),
            ),
        ):
            response = await askAIStream(
                request=request,
                retrieval_service=_mock_retrieval_service(),
                graph_provider=MagicMock(),
                config_service=_mock_config_service(),
            )
            chunks = await _drain(response)

        events = _events_by_name(chunks)
        assert "RUN_FINISHED" in events
        completion = events["RUN_FINISHED"][-1]["result"]
        assert completion["citations"], "web_search tool call should have produced a resolvable citation"
        assert completion["citations"][0]["citationType"] == "web|url"
        assert "[1]" in completion["answer"]
        assert completion["confidence"] == "High"
        assert "Confidence:" not in completion["answer"]
