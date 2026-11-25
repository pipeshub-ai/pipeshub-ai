"""FU2-3: the human-test smoke's partial-output check (``deployment/e2e/smoke_human.py`` ``partial_retraction``).

It reads a turn the way the web app shows it: a message the stream retracted (#4442) is gone, the final answer
snapshot stays. It passes only when the scripted model failed a call mid-stream, the partial text is neither on
screen nor saved, the turn says it failed and each run took the path it asked for. The stack is a fake here.
"""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

REPO = Path(__file__).resolve().parents[5]
sys.path.insert(0, str(REPO / "deployment" / "e2e"))

smoke = importlib.import_module("smoke_human")
from lane import Frame, read_sse  # noqa: E402

GOLDEN = REPO / "backend/nodejs/apps/tests/contracts/golden/chat_llm_error.sse"


def frame(kind: str, **data: Any) -> Frame:  # noqa: ANN401 - a frame's JSON
    return Frame(None, kind, {"type": kind, **data})


def retracted(marker: str, *, discard: bool = True) -> list[Frame]:
    frames = [
        frame("RUN_STARTED", runId="r1"),
        frame("TEXT_MESSAGE_START", messageId="m1"),
        frame("TEXT_MESSAGE_CONTENT", messageId="m1", delta=f"{marker} is the start"),
        frame("TEXT_MESSAGE_END", messageId="m1", **({"discarded": True} if discard else {})),
    ]
    if discard:
        frames.append(frame("CUSTOM", name="message_discarded", value={"messageId": "m1"}))
    return [*frames, frame("RUN_ERROR", code="llm_unavailable", message="The model failed")]


def test_main_s_legacy_error_stream_shows_only_the_error_answer_and_reads_as_failed() -> None:
    with GOLDEN.open("rb") as stream:
        frames = list(read_sse(stream))
    assert smoke.shown_answer(frames).startswith("The AI model's provider is limiting requests")
    assert smoke.surfaced_error(frames)


def test_a_retracted_message_is_not_shown_and_one_left_on_screen_is() -> None:
    assert "partial-1" not in smoke.shown_answer(retracted("partial-1"))
    assert "partial-1" in smoke.shown_answer(retracted("partial-1", discard=False))
    assert smoke.surfaced_error(retracted("partial-1")) == "RUN_ERROR llm_unavailable"
    assert smoke.surfaced_error([frame("RUN_FINISHED", result={})]) == ""


class FakeStack(SimpleNamespace):
    def __init__(self, *, saved: str, fault: bool = True) -> None:
        self.requests: list[dict[str, Any]] = []
        self.saved, self.fault, self.marker = saved, fault, ""
        super().__init__(seed={"agentLoop": "a1"}, model=SimpleNamespace(ok=self._model), api=SimpleNamespace(ok=self._api))

    def _model(self, method: str, path: str, body: object | None = None) -> dict[str, Any]:
        return {"requests": list(self.requests)}

    def _api(self, method: str, path: str, body: object | None = None) -> dict[str, Any]:
        return {"conversation": {"status": "Failed", "messages": [
            {"messageType": "user_query", "content": "fail marker"},
            {"messageType": "bot_response", "content": self.saved.replace("MARKER", self.marker)},
        ]}}


@pytest.fixture
def stack(monkeypatch: pytest.MonkeyPatch) -> FakeStack:
    monkeypatch.setattr(smoke, "results", [])
    fake = FakeStack(saved="Something went wrong.")
    asked: list[str | None] = []

    def turn(stack: FakeStack, agent: str, text: str, *, harness: str | None = None, **_: object) -> tuple[list[Frame], str, str]:
        asked.append(harness)
        if stack.fault:
            stack.requests.append({"kind": "loop", "fault": "mid_stream"})
        stack.marker = text.split()[1]
        return retracted(stack.marker), f"s{len(asked)}", f"r{len(asked)}"

    monkeypatch.setattr(smoke, "turn", turn)
    monkeypatch.setattr(smoke, "admitted", lambda stack, session, run: session == "s2")
    fake.asked = asked
    return fake


def outcomes() -> list[str]:
    return [outcome for _, outcome, _ in smoke.results]


def test_both_paths_pass_when_nothing_partial_stays(stack: FakeStack) -> None:
    smoke.partial_retraction(stack)
    assert outcomes() == [smoke.OK, smoke.OK]
    assert stack.asked == ["legacy", None]


def test_a_partial_answer_saved_fails_the_check(stack: FakeStack) -> None:
    stack.saved = "MARKER is the start"
    smoke.partial_retraction(stack)
    assert outcomes() == [smoke.FAIL, smoke.FAIL]
    assert "saved=True" in json.dumps(smoke.results)


def test_no_failed_model_call_fails_the_check(stack: FakeStack) -> None:
    stack.fault = False
    smoke.partial_retraction(stack)
    assert outcomes() == [smoke.FAIL, smoke.FAIL]
