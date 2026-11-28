"""The non-streaming JSON reply carries the answer's label wherever the
streamed frame did: on the answer, and on an error answer."""

import json

from app.agents.agent_loop.protocol.stream_collector import collect_stream_outcome

LABEL = {"v": 5, "complete": True, "direct": {"records": ["rec-1"]}}


def _frame(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


async def _chunks(*frames: str):  # noqa: ANN202
    for frame in frames:
        yield frame


async def test_the_json_answer_keeps_the_label() -> None:
    outcome = await collect_stream_outcome(_chunks(_frame("RUN_FINISHED", {
        "type": "RUN_FINISHED", "runId": "r1", "result": {"answer": "hi", "provenance": LABEL},
    })))

    assert json.loads(outcome.to_response().body)["provenance"] == LABEL


async def test_the_json_error_answer_keeps_the_label() -> None:
    outcome = await collect_stream_outcome(_chunks(_frame("RUN_ERROR", {
        "type": "RUN_ERROR", "runId": "r1", "message": "It failed.", "code": "server_error", "provenance": LABEL,
    })))

    body = json.loads(outcome.to_response().body)
    assert body["status"] == "error" and body["provenance"] == LABEL


async def test_an_error_with_no_label_sends_none() -> None:
    outcome = await collect_stream_outcome(_chunks(_frame("RUN_ERROR", {
        "type": "RUN_ERROR", "runId": "r1", "message": "It failed.", "code": "server_error",
    })))

    assert "provenance" not in json.loads(outcome.to_response().body)
