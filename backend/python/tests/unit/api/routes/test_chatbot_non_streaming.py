"""Coverage for `chatbot.py`'s non-streaming `POST /chat`, which runs
`askAIStream()` and drains its `body_iterator` rather than keeping a second
copy of the chat pipeline (see `askAI()`'s own docstring). Node's
`createConversation` and `addMessage` are its callers."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.responses import JSONResponse, StreamingResponse


def _sse_stream(*frames: str) -> StreamingResponse:
    async def _gen():
        for frame in frames:
            yield frame

    return StreamingResponse(_gen(), media_type="text/event-stream")


async def _ask(stream) -> JSONResponse:
    from app.api.routes.chatbot import askAI

    with patch("app.api.routes.chatbot.askAIStream", new=AsyncMock(return_value=stream)):
        return await askAI(
            MagicMock(),
            retrieval_service=MagicMock(),
            graph_provider=MagicMock(),
            config_service=MagicMock(),
            cancellation_registry=MagicMock(),
        )


class TestAskAINonStreaming:
    async def test_returns_the_answer_from_the_complete_event(self):
        response = await _ask(
            _sse_stream(
                'event: status\ndata: {"status": "searching"}\n\n',
                'event: answer_chunk\ndata: {"accumulated": "4"}\n\n',
                'event: complete\ndata: {"answer": "42", "citations": []}\n\n',
            )
        )

        assert isinstance(response, JSONResponse)
        assert response.status_code == 200
        assert json.loads(response.body) == {"answer": "42", "citations": []}

    async def test_returns_the_answer_from_an_ag_ui_run_finished_event(self):
        response = await _ask(
            _sse_stream(
                'event: RUN_FINISHED\ndata: {"result": {"answer": "42", "citations": []}}\n\n',
            )
        )

        assert response.status_code == 200
        assert json.loads(response.body) == {"answer": "42", "citations": []}

    async def test_a_sub_agent_frame_does_not_decide_the_outcome(self):
        # A nested run failing is handed back to the parent as a tool result;
        # the parent still answers, so only root-run frames count.
        response = await _ask(
            _sse_stream(
                'event: error\ndata: {"parentRunId": "root", "message": "sub-agent gave up"}\n\n',
                'event: complete\ndata: {"answer": "answered anyway", "citations": []}\n\n',
            )
        )

        assert response.status_code == 200
        assert json.loads(response.body)["answer"] == "answered anyway"

    async def test_an_error_event_becomes_a_json_error_with_its_status(self):
        response = await _ask(
            _sse_stream(
                'event: error\ndata: {"message": "No language model is configured", "status_code": 400}\n\n',
            )
        )

        assert response.status_code == 400
        body = json.loads(response.body)
        assert body["status"] == "error"
        assert body["message"] == "No language model is configured"
        assert body["searchResults"] == []
        assert body["records"] == []

    async def test_an_error_event_without_a_status_defaults_to_400(self):
        response = await _ask(_sse_stream('event: error\ndata: {"error": "boom"}\n\n'))

        assert response.status_code == 400
        assert json.loads(response.body)["message"] == "boom"

    async def test_a_stream_with_no_answer_and_no_error_is_a_failure(self):
        # Reported as a failure rather than an empty answer: the caller cannot
        # tell those apart, and only one of them is true.
        response = await _ask(_sse_stream('event: status\ndata: {"status": "searching"}\n\n'))

        assert response.status_code == 500
        assert "No answer" in json.loads(response.body)["message"]

    async def test_several_frames_arriving_in_one_chunk_are_all_read(self):
        response = await _ask(
            _sse_stream(
                'event: status\ndata: {"status": "searching"}\n\n'
                'event: complete\ndata: {"answer": "42", "citations": []}\n\n',
            )
        )

        assert response.status_code == 200
        assert json.loads(response.body)["answer"] == "42"

    async def test_chunks_arriving_as_bytes_are_decoded(self):
        async def _gen():
            yield b'event: complete\ndata: {"answer": "42", "citations": []}\n\n'

        response = await _ask(StreamingResponse(_gen(), media_type="text/event-stream"))

        assert response.status_code == 200
        assert json.loads(response.body)["answer"] == "42"

    async def test_the_last_answer_wins(self):
        response = await _ask(
            _sse_stream(
                'event: complete\ndata: {"answer": "first", "citations": []}\n\n',
                'event: complete\ndata: {"answer": "second", "citations": []}\n\n',
            )
        )

        assert json.loads(response.body)["answer"] == "second"

    async def test_a_non_streaming_response_is_passed_straight_through(self):
        passthrough = JSONResponse(status_code=409, content={"detail": "already running"})

        response = await _ask(passthrough)

        assert response is passthrough
