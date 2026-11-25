"""CITESTREAM: a durable run's live text frames carry the answer's numbered
citations, so a marker is a link while the answer streams (as legacy's
``TerminalAnswerStreamer`` does), not only once the final answer lands."""

from __future__ import annotations

from typing import Any

from app.harness.live_citations import CitedLiveSink
from app.utils.chat_helpers import CitationRefMapper
from harness_core.journal.types import LiveFrame
from tests.unit.agents.adapter.conftest import make_context

URL = "https://example.com/leave-policy"


class _Bus:
    def __init__(self) -> None:
        self.frames: list[LiveFrame] = []

    async def publish(self, frame: LiveFrame) -> None:
        self.frames.append(frame)


def _context() -> Any:  # noqa: ANN401
    context = make_context()
    mapper = CitationRefMapper()
    assert mapper.get_or_create_ref(URL) == "ref1"
    context.tool_state["citation_ref_mapper"] = mapper
    context.tool_state["web_records"] = [{"url": URL, "title": "Leave policy", "content": "25 days"}]
    return context


def _frame(n: int, text: str, *, kind: str = "text_delta", attempt: int = 1) -> LiveFrame:
    return LiveFrame("run_1", 1, attempt, n, kind, {"text": text})  # type: ignore[arg-type]


class _Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


async def _sink(clock: _Clock | None = None) -> tuple[CitedLiveSink, _Bus]:
    bus = _Bus()
    return CitedLiveSink(bus, _context(), interval_s=10.0, clock=clock or _Clock()), bus


async def test_a_streamed_citation_is_numbered_with_its_source_before_the_answer_ends() -> None:
    sink, bus = await _sink()

    await sink.publish(_frame(1, "You get 25 days"))
    await sink.publish(_frame(2, " of leave [source](re"))
    await sink.publish(_frame(3, "f1)."))

    last = bus.frames[-1].data
    assert last["text"] == "f1)."
    assert last["answer"] == f"You get 25 days of leave [1]({URL})."
    assert [c["chunkIndex"] for c in last["citations"]] == [1]  # type: ignore[union-attr,index]
    assert last["citations"][0]["metadata"]["webUrl"] == URL  # type: ignore[index,call-overload]


async def test_text_without_a_new_ref_waits_out_the_window_and_citations_are_not_resent() -> None:
    clock = _Clock()
    sink, bus = await _sink(clock)

    await sink.publish(_frame(1, "See [source](ref1)."))
    await sink.publish(_frame(2, " More text"))
    clock.now += 11
    await sink.publish(_frame(3, " and more."))

    first, quiet, due = (f.data for f in bus.frames)
    assert "citations" in first
    assert "answer" not in quiet
    assert due["answer"] == f"See [1]({URL}). More text and more."
    assert "citations" not in due, "unchanged citations ride only on the frame that changed them"


async def test_a_new_attempt_starts_its_own_text_and_other_frames_pass_untouched() -> None:
    sink, bus = await _sink()

    await sink.publish(_frame(1, "cut off [source](ref1)"))
    await sink.publish(_frame(1, "thinking", kind="reasoning_delta", attempt=2))
    await sink.publish(_frame(2, "Fresh answer.", attempt=2))

    assert bus.frames[1].data == {"text": "thinking"}
    assert bus.frames[2].data["answer"] == "Fresh answer."
    assert bus.frames[2].data["citations"] == []


async def test_a_failing_numbering_still_streams_the_text(monkeypatch: Any) -> None:  # noqa: ANN401
    import app.harness.live_citations as mod

    def boom(*_a: object, **_k: object) -> None:
        raise ValueError("bad state")

    monkeypatch.setattr(mod, "resolve_streamed_answer", boom)
    sink, bus = await _sink()

    await sink.publish(_frame(1, "Hello [source](ref1)"))

    assert bus.frames[0].data == {"text": "Hello [source](ref1)"}
