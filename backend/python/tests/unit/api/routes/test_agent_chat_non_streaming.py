"""Coverage for `agent.py`'s non-streaming `POST /{agent_id}/chat`, which
runs `chat_stream()` and drains its `body_iterator` (see `chat()`'s own
docstring for why this is deliberately not a second copy of that setup
logic) rather than duplicating the agent-loop pipeline."""

from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.responses import JSONResponse, StreamingResponse


def _sse_stream(*frames: str) -> StreamingResponse:
    async def _gen():
        for frame in frames:
            yield frame

    return StreamingResponse(_gen(), media_type="text/event-stream")


class TestChatNonStreaming:
    async def test_returns_completion_data_from_complete_event(self):
        from app.api.routes.agent import chat

        stream = _sse_stream(
            'event: status\ndata: {"status": "planning", "message": "..."}\n\n',
            'event: complete\ndata: {"answer": "42", "citations": []}\n\n',
        )

        with patch("app.api.routes.agent.chat_stream", new=AsyncMock(return_value=stream)):
            response = await chat(MagicMock(), "agent-1")

        assert isinstance(response, JSONResponse)
        assert response.status_code == 200
        assert response.body == JSONResponse(content={"answer": "42", "citations": []}).body

    async def test_maps_error_event_to_json_error_response(self):
        from app.api.routes.agent import chat

        stream = _sse_stream(
            'event: error\ndata: {"error": "rate_limit", "message": "Too many requests", "status_code": 429}\n\n',
        )

        with patch("app.api.routes.agent.chat_stream", new=AsyncMock(return_value=stream)):
            response = await chat(MagicMock(), "agent-1")

        assert isinstance(response, JSONResponse)
        assert response.status_code == 429
        import json
        body = json.loads(response.body)
        assert body["status"] == "error"
        assert body["message"] == "Too many requests"
        assert body["searchResults"] == []
        assert body["records"] == []

    async def test_error_event_without_status_code_defaults_to_400(self):
        from app.api.routes.agent import chat

        stream = _sse_stream('event: error\ndata: {"error": "boom"}\n\n')

        with patch("app.api.routes.agent.chat_stream", new=AsyncMock(return_value=stream)):
            response = await chat(MagicMock(), "agent-1")

        assert response.status_code == 400
        import json
        body = json.loads(response.body)
        assert body["message"] == "boom"

    async def test_no_complete_or_error_event_returns_500(self):
        from app.api.routes.agent import chat

        stream = _sse_stream('event: status\ndata: {"status": "planning", "message": "..."}\n\n')

        with patch("app.api.routes.agent.chat_stream", new=AsyncMock(return_value=stream)):
            response = await chat(MagicMock(), "agent-1")

        assert response.status_code == 500
        import json
        body = json.loads(response.body)
        assert "did not produce a response" in body["message"]

    async def test_multiple_frames_in_one_chunk_are_all_parsed(self):
        """`_parse_sse_events` must split a single `body_iterator` chunk
        containing more than one `event:`/`data:` frame."""
        from app.api.routes.agent import chat

        combined_chunk = (
            'event: status\ndata: {"status": "executing", "message": "..."}\n\n'
            'event: complete\ndata: {"answer": "combined"}\n\n'
        )
        stream = _sse_stream(combined_chunk)

        with patch("app.api.routes.agent.chat_stream", new=AsyncMock(return_value=stream)):
            response = await chat(MagicMock(), "agent-1")

        import json
        assert json.loads(response.body) == {"answer": "combined"}

    async def test_bytes_chunks_are_decoded(self):
        from app.api.routes.agent import chat

        async def _gen():
            yield b'event: complete\ndata: {"answer": "from-bytes"}\n\n'

        stream = StreamingResponse(_gen(), media_type="text/event-stream")

        with patch("app.api.routes.agent.chat_stream", new=AsyncMock(return_value=stream)):
            response = await chat(MagicMock(), "agent-1")

        import json
        assert json.loads(response.body) == {"answer": "from-bytes"}

    async def test_last_complete_event_wins_when_stream_restreams(self):
        from app.api.routes.agent import chat

        stream = _sse_stream(
            'event: complete\ndata: {"answer": "first"}\n\n',
            'event: complete\ndata: {"answer": "second"}\n\n',
        )

        with patch("app.api.routes.agent.chat_stream", new=AsyncMock(return_value=stream)):
            response = await chat(MagicMock(), "agent-1")

        import json
        assert json.loads(response.body) == {"answer": "second"}

    async def test_non_streaming_response_from_chat_stream_is_passed_through(self):
        """Defensive branch: if `chat_stream()` ever returns something other
        than a `StreamingResponse` (it doesn't today), `chat()` must not
        try to drain a `body_iterator` that doesn't exist."""
        from app.api.routes.agent import chat

        passthrough = JSONResponse(status_code=403, content={"message": "forbidden"})

        with patch("app.api.routes.agent.chat_stream", new=AsyncMock(return_value=passthrough)):
            response = await chat(MagicMock(), "agent-1")

        assert response is passthrough
