"""Coverage for `chatbot.py`'s non-streaming `POST /chat` (`askAI`), the
route Node's `createConversation` / `addMessage` call. It must run the same
agent-loop stream `/chat/stream` runs and return only its final result."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException


def _request(body: dict | None = None, *, json_error: bool = False) -> MagicMock:
    request = MagicMock()
    if json_error:
        request.json = AsyncMock(side_effect=ValueError("not json"))
    else:
        request.json = AsyncMock(return_value=body if body is not None else {"query": "hello"})
    request.state.user = {"orgId": "org-1", "userId": "user-1", "email": "u@corp.com"}
    request.is_disconnected = AsyncMock(return_value=False)
    return request


def _registry(active: bool = False) -> MagicMock:
    registry = MagicMock()
    registry.is_active = AsyncMock(return_value=active)
    return registry


async def _gen(*frames: str):
    for frame in frames:
        yield frame


async def _call(request, registry=None, frames=()):
    from app.api.routes.chatbot import askAI

    stream_factory = MagicMock(return_value=_gen(*frames))
    with (
        patch("app.api.routes.chatbot._generate_chat_stream_via_agent_loop", new=stream_factory),
        patch("app.api.routes.chatbot.record_event") as record_event,
    ):
        response = await askAI(
            request,
            retrieval_service=MagicMock(),
            graph_provider=MagicMock(),
            config_service=MagicMock(),
            cancellation_registry=registry or _registry(),
        )
    return response, stream_factory, record_event


class TestAskAINonStreaming:
    async def test_returns_run_finished_result_as_json(self):
        completion = {"answer": "42", "citations": [], "confidence": "High"}
        frames = (
            'event: RUN_STARTED\ndata: {"type": "RUN_STARTED", "runId": "r1"}\n\n',
            'event: TEXT_MESSAGE_CONTENT\ndata: {"type": "TEXT_MESSAGE_CONTENT", "delta": "42"}\n\n',
            f'event: RUN_FINISHED\ndata: {json.dumps({"type": "RUN_FINISHED", "result": completion})}\n\n',
        )

        response, _, _ = await _call(_request(), frames=frames)

        assert response.status_code == 200
        assert json.loads(response.body) == completion

    async def test_runs_the_same_pipeline_as_the_stream_route(self):
        body = {"query": "q", "chatMode": "web_search", "conversationId": "c1", "modelKey": "m1"}
        registry = _registry()
        request = _request(body)

        _, stream_factory, _ = await _call(
            request, registry, frames=('event: complete\ndata: {"answer": "a"}\n\n',),
        )

        kwargs = stream_factory.call_args.kwargs
        assert kwargs["request"] is request
        assert kwargs["cancellation_registry"] is registry
        assert kwargs["query_info"].query == "q"
        assert kwargs["query_info"].chatMode == "web_search"
        assert kwargs["query_info"].conversationId == "c1"
        assert kwargs["query_info"].modelKey == "m1"

    async def test_llm_initialization_failure_is_a_424_with_its_message(self):
        frames = (
            'event: RUN_ERROR\ndata: {"type": "RUN_ERROR", "message": "Set up a model first.", '
            '"code": "llm_initialization_failed"}\n\n',
        )

        response, _, _ = await _call(_request(), frames=frames)

        assert response.status_code == 424
        assert json.loads(response.body)["message"] == "Set up a model first."

    async def test_no_terminal_frame_is_a_500(self):
        response, _, _ = await _call(_request(), frames=())

        assert response.status_code == 500
        assert json.loads(response.body)["code"] == "no_response"

    async def test_records_a_non_streaming_session(self):
        _, _, record_event = await _call(
            _request(), frames=('event: complete\ndata: {"answer": "a"}\n\n',),
        )

        name, payload = record_event.call_args.args
        assert name == "chat_session_started"
        assert payload["streaming"] is False

    async def test_invalid_json_is_a_400(self):
        with pytest.raises(HTTPException) as exc:
            await _call(_request(json_error=True))
        assert exc.value.status_code == 400

    async def test_missing_query_is_a_400(self):
        with pytest.raises(HTTPException) as exc:
            await _call(_request({"chatMode": "quick"}))
        assert exc.value.status_code == 400

    async def test_active_run_id_is_a_409_before_any_work(self):
        run_id = "0b8f2a52-8d0e-4a4e-9c64-0d9b1f0e5a11"
        with pytest.raises(HTTPException) as exc:
            await _call(_request({"query": "q", "runId": run_id}), _registry(active=True))
        assert exc.value.status_code == 409


class TestAgentLoopStreamClosing:
    async def test_closing_the_route_stream_closes_the_agent_loop_immediately(self):
        """Closing `_generate_chat_stream_via_agent_loop` (the collector stopping on
        disconnect, or Starlette on a dropped SSE client) must close `run_chat_stream`
        at once, so its producer task is cancelled instead of left to the GC."""
        from app.api.routes.chatbot import ChatQuery, _generate_chat_stream_via_agent_loop

        closed: list[bool] = []

        async def fake_run_chat_stream(*_a, **_k):
            try:
                for i in range(1000):
                    yield f'event: TEXT_MESSAGE_CONTENT\ndata: {{"delta": "{i}"}}\n\n'
            finally:
                closed.append(True)

        request = _request()
        request.query_params = {}
        request.headers = {}
        request.app.container.logger.return_value = MagicMock()
        llm_bundle = (MagicMock(), {"provider": "openai", "isMultimodal": False, "contextLength": 8000}, {})
        with (
            patch("app.api.routes.chatbot.get_llm_for_chat", new=AsyncMock(return_value=llm_bundle)),
            patch("app.api.routes.chatbot.load_system_prompts", new=AsyncMock(return_value={})),
            patch("app.api.routes.chatbot.is_user_context_enabled", new=AsyncMock(return_value=True)),
            patch("app.api.routes.chatbot.run_chat_stream", new=fake_run_chat_stream),
        ):
            stream = _generate_chat_stream_via_agent_loop(
                request, ChatQuery(query="hi"), MagicMock(), MagicMock(), MagicMock(),
            )
            await stream.__anext__()
            await stream.aclose()

            assert closed == [True]
