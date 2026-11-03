"""Coverage for `agent.py`'s non-streaming `POST /{agent_id}/chat`, which
runs `chat_stream()` and drains its `body_iterator` through
`stream_collector` rather than duplicating the agent-loop pipeline.
Frame-level parsing is covered in `test_stream_collector.py`."""

from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.responses import JSONResponse, StreamingResponse


def _sse_stream(*frames: str) -> StreamingResponse:
    async def _gen():
        for frame in frames:
            yield frame

    return StreamingResponse(_gen(), media_type="text/event-stream")


def _request() -> MagicMock:
    request = MagicMock()
    request.is_disconnected = AsyncMock(return_value=False)
    return request


class TestChatNonStreaming:
    async def test_returns_completion_data_from_complete_event(self):
        from app.api.routes.agent import chat

        stream = _sse_stream(
            'event: status\ndata: {"status": "planning", "message": "..."}\n\n',
            'event: complete\ndata: {"answer": "42", "citations": []}\n\n',
        )

        with patch("app.api.routes.agent.chat_stream", new=AsyncMock(return_value=stream)):
            response = await chat(_request(), "agent-1")

        assert isinstance(response, JSONResponse)
        assert response.status_code == 200
        assert response.body == JSONResponse(content={"answer": "42", "citations": []}).body

    async def test_maps_error_event_to_json_error_response(self):
        from app.api.routes.agent import chat

        stream = _sse_stream(
            'event: error\ndata: {"error": "rate_limit", "message": "Too many requests", "status_code": 429}\n\n',
        )

        with patch("app.api.routes.agent.chat_stream", new=AsyncMock(return_value=stream)):
            response = await chat(_request(), "agent-1")

        assert isinstance(response, JSONResponse)
        assert response.status_code == 429
        import json
        body = json.loads(response.body)
        assert body["status"] == "error"
        assert body["message"] == "Too many requests"
        assert body["searchResults"] == []
        assert body["records"] == []

    async def test_error_event_without_status_code_or_code_is_a_500(self):
        from app.api.routes.agent import chat

        stream = _sse_stream('event: error\ndata: {"error": "boom"}\n\n')

        with patch("app.api.routes.agent.chat_stream", new=AsyncMock(return_value=stream)):
            response = await chat(_request(), "agent-1")

        assert response.status_code == 500
        import json
        body = json.loads(response.body)
        assert body["message"] == "boom"

    async def test_no_complete_or_error_event_returns_500(self):
        from app.api.routes.agent import chat

        stream = _sse_stream('event: status\ndata: {"status": "planning", "message": "..."}\n\n')

        with patch("app.api.routes.agent.chat_stream", new=AsyncMock(return_value=stream)):
            response = await chat(_request(), "agent-1")

        assert response.status_code == 500
        import json
        body = json.loads(response.body)
        assert "did not produce a response" in body["message"]
        assert body["code"] == "no_response"

    async def test_multiple_frames_in_one_chunk_are_all_parsed(self):
        """A single `body_iterator` chunk
        containing more than one `event:`/`data:` frame is fully parsed."""
        from app.api.routes.agent import chat

        combined_chunk = (
            'event: status\ndata: {"status": "executing", "message": "..."}\n\n'
            'event: complete\ndata: {"answer": "combined"}\n\n'
        )
        stream = _sse_stream(combined_chunk)

        with patch("app.api.routes.agent.chat_stream", new=AsyncMock(return_value=stream)):
            response = await chat(_request(), "agent-1")

        import json
        assert json.loads(response.body) == {"answer": "combined"}

    async def test_bytes_chunks_are_decoded(self):
        from app.api.routes.agent import chat

        async def _gen():
            yield b'event: complete\ndata: {"answer": "from-bytes"}\n\n'

        stream = StreamingResponse(_gen(), media_type="text/event-stream")

        with patch("app.api.routes.agent.chat_stream", new=AsyncMock(return_value=stream)):
            response = await chat(_request(), "agent-1")

        import json
        assert json.loads(response.body) == {"answer": "from-bytes"}

    async def test_last_complete_event_wins_when_stream_restreams(self):
        from app.api.routes.agent import chat

        stream = _sse_stream(
            'event: complete\ndata: {"answer": "first"}\n\n',
            'event: complete\ndata: {"answer": "second"}\n\n',
        )

        with patch("app.api.routes.agent.chat_stream", new=AsyncMock(return_value=stream)):
            response = await chat(_request(), "agent-1")

        import json
        assert json.loads(response.body) == {"answer": "second"}

    async def test_non_streaming_response_from_chat_stream_is_passed_through(self):
        """Defensive branch: if `chat_stream()` ever returns something other
        than a `StreamingResponse` (it doesn't today), `chat()` must not
        try to drain a `body_iterator` that doesn't exist."""
        from app.api.routes.agent import chat

        passthrough = JSONResponse(status_code=403, content={"message": "forbidden"})

        with patch("app.api.routes.agent.chat_stream", new=AsyncMock(return_value=passthrough)):
            response = await chat(_request(), "agent-1")

        assert response is passthrough

    async def test_agui_run_finished_result_is_returned(self):
        from app.api.routes.agent import chat

        stream = _sse_stream(
            'event: RUN_STARTED\ndata: {"type": "RUN_STARTED", "runId": "r1"}\n\n',
            'event: RUN_FINISHED\ndata: {"type": "RUN_FINISHED", "runId": "r1", '
            '"result": {"answer": "agui", "citations": []}}\n\n',
        )

        with patch("app.api.routes.agent.chat_stream", new=AsyncMock(return_value=stream)):
            response = await chat(_request(), "agent-1")

        import json
        assert response.status_code == 200
        assert json.loads(response.body) == {"answer": "agui", "citations": []}

    async def test_frame_split_across_chunks_is_not_lost(self):
        from app.api.routes.agent import chat

        whole = 'event: RUN_FINISHED\ndata: {"type": "RUN_FINISHED", "result": {"answer": "split"}}\n\n'
        stream = _sse_stream(whole[:25], whole[25:])

        with patch("app.api.routes.agent.chat_stream", new=AsyncMock(return_value=stream)):
            response = await chat(_request(), "agent-1")

        import json
        assert json.loads(response.body) == {"answer": "split"}

    async def test_root_run_error_code_maps_to_status(self):
        from app.api.routes.agent import chat

        stream = _sse_stream(
            'event: RUN_ERROR\ndata: {"type": "RUN_ERROR", "runId": "r1", '
            '"message": "No LLM is configured.", "code": "llm_not_configured"}\n\n',
        )

        with patch("app.api.routes.agent.chat_stream", new=AsyncMock(return_value=stream)):
            response = await chat(_request(), "agent-1")

        import json
        assert response.status_code == 424
        assert json.loads(response.body)["message"] == "No LLM is configured."

    async def test_marks_request_as_non_streaming_for_telemetry(self):
        from app.api.routes.agent import chat

        request = _request()
        stream = _sse_stream('event: complete\ndata: {"answer": "x"}\n\n')

        with patch("app.api.routes.agent.chat_stream", new=AsyncMock(return_value=stream)):
            await chat(request, "agent-1")

        assert request.state.chat_streaming is False
