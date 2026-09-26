"""`stream_collector` turns a chat SSE stream into the JSON reply of the
non-streaming `/chat` and `/agent/{id}/chat` routes."""

import json
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock

import pytest

from app.agents.agent_loop.protocol.stream_collector import (
    NO_RESPONSE_MESSAGE,
    SSEFrame,
    SSEFrameParser,
    StreamOutcome,
    collect_stream_outcome,
)


def _frame(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def _run_finished(result: dict, **extra) -> str:
    return _frame("RUN_FINISHED", {"type": "RUN_FINISHED", "runId": "r1", "result": result, **extra})


def _run_error(message: str, code: str | None = None, **extra) -> str:
    data = {"type": "RUN_ERROR", "runId": "r1", "message": message, **extra}
    if code is not None:
        data["code"] = code
    return _frame("RUN_ERROR", data)


class _Stream:
    """Async iterator that records whether the collector closed it."""

    def __init__(self, *chunks: str | bytes) -> None:
        self._chunks = list(chunks)
        self.closed = False

    def __aiter__(self) -> AsyncIterator[str | bytes]:
        return self

    async def __anext__(self) -> str | bytes:
        if not self._chunks:
            raise StopAsyncIteration
        return self._chunks.pop(0)

    async def aclose(self) -> None:
        self.closed = True


def _body(response) -> dict:
    return json.loads(response.body)


class TestSSEFrameParser:
    def test_frame_split_across_chunks_is_parsed_once_whole(self):
        parser = SSEFrameParser()
        whole = _run_finished({"answer": "42"})
        cut = len(whole) // 2

        assert list(parser.feed(whole[:cut])) == []
        frames = list(parser.feed(whole[cut:]))

        assert frames == [SSEFrame("RUN_FINISHED", json.loads(whole.split("data: ", 1)[1]))]

    def test_several_frames_in_one_chunk(self):
        parser = SSEFrameParser()
        chunk = _frame("STEP_STARTED", {"stepName": "a"}) + _frame("STEP_FINISHED", {"stepName": "a"})

        assert [f.event for f in parser.feed(chunk)] == ["STEP_STARTED", "STEP_FINISHED"]

    def test_crlf_line_endings_and_bytes(self):
        parser = SSEFrameParser()
        chunk = b'event: complete\r\ndata: {"answer": "x"}\r\n\r\n'

        assert list(parser.feed(chunk)) == [SSEFrame("complete", {"answer": "x"})]

    def test_multi_line_data_is_joined(self):
        parser = SSEFrameParser()
        chunk = 'event: complete\ndata: {"answer":\ndata: "multi"}\n\n'

        assert list(parser.feed(chunk)) == [SSEFrame("complete", {"answer": "multi"})]

    def test_malformed_json_and_comment_frames_are_skipped(self):
        parser = SSEFrameParser()
        chunk = ": keep-alive\n\n" + "event: complete\ndata: {not json\n\n" + _frame("complete", {"answer": "ok"})

        assert list(parser.feed(chunk)) == [SSEFrame("complete", {"answer": "ok"})]

    def test_flush_returns_a_trailing_frame_without_blank_line(self):
        parser = SSEFrameParser()
        assert list(parser.feed('event: complete\ndata: {"answer": "tail"}')) == []

        assert list(parser.flush()) == [SSEFrame("complete", {"answer": "tail"})]
        assert list(parser.flush()) == []


class TestCollectStreamOutcome:
    async def test_root_run_finished_result_is_the_completion(self):
        stream = _Stream(
            _frame("RUN_STARTED", {"runId": "r1"}),
            _frame("TEXT_MESSAGE_CONTENT", {"delta": "4"}),
            _run_finished({"answer": "42", "citations": []}),
        )

        outcome = await collect_stream_outcome(stream)

        assert outcome == StreamOutcome(completion={"answer": "42", "citations": []})
        assert stream.closed

    async def test_legacy_complete_event_is_the_completion(self):
        outcome = await collect_stream_outcome(_Stream(_frame("complete", {"answer": "legacy"})))

        assert outcome.completion == {"answer": "legacy"}

    async def test_sub_agent_frames_do_not_decide_the_outcome(self):
        stream = _Stream(
            _run_error("child failed", "server_error", parentRunId="r1"),
            _frame("RUN_FINISHED", {"runId": "c1", "parentRunId": "r1"}),
            _run_finished({"answer": "parent answered"}),
        )

        outcome = await collect_stream_outcome(stream)

        assert outcome == StreamOutcome(completion={"answer": "parent answered"})

    async def test_run_finished_without_result_does_not_erase_completion(self):
        stream = _Stream(_run_finished({"answer": "kept"}), _frame("RUN_FINISHED", {"runId": "r1"}))

        assert (await collect_stream_outcome(stream)).completion == {"answer": "kept"}

    async def test_completion_wins_over_an_earlier_error(self):
        stream = _Stream(_run_error("transient", "server_error"), _run_finished({"answer": "recovered"}))

        response = (await collect_stream_outcome(stream)).to_response()

        assert response.status_code == 200
        assert _body(response) == {"answer": "recovered"}

    async def test_stream_without_terminal_frame_is_a_500(self):
        response = (await collect_stream_outcome(_Stream(_frame("STEP_STARTED", {})))).to_response()

        assert response.status_code == 500
        assert _body(response)["message"] == NO_RESPONSE_MESSAGE
        assert _body(response)["code"] == "no_response"

    async def test_frame_split_across_chunks_still_completes(self):
        whole = _run_finished({"answer": "split"})

        outcome = await collect_stream_outcome(_Stream(whole[:7], whole[7:20], whole[20:]))

        assert outcome.completion == {"answer": "split"}

    async def test_disconnected_client_stops_the_stream(self):
        stream = _Stream(_frame("STEP_STARTED", {}), _run_finished({"answer": "never read"}))
        is_disconnected = AsyncMock(return_value=True)

        outcome = await collect_stream_outcome(stream, is_disconnected)

        assert outcome.completion is None
        assert outcome.error["code"] == "client_disconnected"
        assert stream.closed
        is_disconnected.assert_awaited_once()

    async def test_iterator_is_closed_when_the_stream_raises(self):
        class _Boom(_Stream):
            async def __anext__(self):
                raise RuntimeError("upstream broke")

        stream = _Boom()
        with pytest.raises(RuntimeError):
            await collect_stream_outcome(stream)
        assert stream.closed


class TestErrorResponse:
    @pytest.mark.parametrize(
        ("code", "status"),
        [
            ("llm_initialization_failed", 424),
            ("llm_not_configured", 424),
            ("rate_limit", 429),
            ("content_filter", 422),
            ("request_too_large", 413),
            ("invalid_request", 400),
            ("server_error", 502),
            ("timeout", 504),
            ("something_new", 500),
            (None, 500),
        ],
    )
    async def test_error_code_maps_to_status(self, code, status):
        outcome = await collect_stream_outcome(_Stream(_run_error("user-facing text", code)))

        response = outcome.to_response()

        assert response.status_code == status
        assert _body(response) == {
            "status": "error",
            "code": code or "unknown",
            "message": "user-facing text",
            "searchResults": [],
            "records": [],
        }

    async def test_explicit_status_code_wins(self):
        stream = _Stream(_frame("error", {"error": "Too many requests", "status_code": 429}))

        response = (await collect_stream_outcome(stream)).to_response()

        assert response.status_code == 429
        assert _body(response)["message"] == "Too many requests"

    async def test_out_of_range_status_code_is_ignored(self):
        stream = _Stream(_frame("error", {"message": "odd", "status_code": 200}))

        assert (await collect_stream_outcome(stream)).to_response().status_code == 500
