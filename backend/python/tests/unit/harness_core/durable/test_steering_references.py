"""P1b T2b (C-21b): a steering message names stored attachments and records by reference.

Node authorizes them at admission and queues them with the text
(``input.queued{attachments, recordIds}``). At the boundary that consumes the
message the kernel asks the run's ``InputRenderer`` for the user message a new
turn with them would give the model (text, the attachments' text, images) and
journals those parts with the consumption (``input.consumed.contentRef``, fold
6): the next request, and every later attempt's, carries them from the journal,
so no attempt renders the message twice.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import pytest

from harness_core.failpoints import Arm, Nth, Raise, registry
from harness_core.journal.types import JournalEvent, NewEvent
from harness_core.kernel.core.messages import (
    ImagePart,
    ImageSource,
    TextPart,
    UserMessage,
)
from harness_core.kernel.durable import RenderedInput, SteeringInput
from harness_core.kernel.durable.transcript import TranscriptRenderer, decode_user_parts
from harness_core.kernel.fold import FOLD_VERSION, fold_session

from .support import RUN, SESSION, Crash, World, assert_paired, reply, scripted

if TYPE_CHECKING:
    from pydantic import JsonValue

STEER = "compare it with the attached plan"
FILE: dict[str, JsonValue] = {
    "recordId": "rec-1",
    "virtualRecordId": "vr-1",
    "recordName": "plan.pdf",
    "mimeType": "application/pdf",
}
IMAGE = ImagePart(source=ImageSource(type="base64", media_type="image/png", data="iVBORw0KGgo="))


class _Renderer:
    """Plays the product renderer: the text a new turn's goal gets, and an image."""

    def __init__(self, *, events: list[NewEvent] | None = None) -> None:
        self.calls: list[SteeringInput] = []
        self._events = events or []

    async def render(self, message: SteeringInput) -> RenderedInput:
        self.calls.append(message)
        return RenderedInput(
            parts=[TextPart(text=f"{message.text}\n\n<record>plan body</record>"), IMAGE],
            events=list(self._events),
        )


def _world_with_steer(**refs: object) -> World:
    world = World(extension_writers={"x.test.citations.recorded": frozenset({"worker"})})  # type: ignore[arg-type]
    world.admit()
    world.on_read = lambda w: (
        w.journal.queue_input(RUN, STEER, **refs) if not w.events("input.queued") else None  # type: ignore[arg-type]
    )
    return world


def _lines(caplog: pytest.LogCaptureFixture, event: str) -> list[logging.LogRecord]:
    return [r for r in caplog.records if getattr(r, "harness_event", None) == event]


async def test_an_attachment_reaches_the_model_as_the_renderer_built_it(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    world = _world_with_steer(attachments=[FILE], record_ids=["rec-9"])
    renderer = _Renderer()
    _, port = scripted(reply("", ("lookup", {"q": "x"})), reply("done"))
    done = await world.run(world.kernel(port, inputs=renderer))
    assert done.status == "completed"
    assert renderer.calls == [
        SteeringInput(message_seq=1, text=STEER, attachments=(FILE,), record_ids=("rec-9",))
    ]
    (consumed,) = world.events("input.consumed")
    assert set(consumed.data) == {"messageSeq", "contentRef"}
    last = port.calls[1].messages
    assert_paired(last)
    assert last[-1] == UserMessage(
        content=[TextPart(text=f"{STEER}\n\n<record>plan body</record>"), IMAGE]
    )
    (line,) = _lines(caplog, "input.consumed")
    fields = line.harness  # type: ignore[attr-defined]
    assert (fields["attachments"], fields["records"]) == (1, 1)
    assert STEER not in repr(fields) and "plan.pdf" not in repr(fields)


async def test_the_renderers_events_are_journaled_with_the_consumption() -> None:
    world = _world_with_steer(attachments=[FILE])
    delta = NewEvent("x.test.citations.recorded", {"refs": 1}, critical=False)
    _, port = scripted(reply("", ("lookup", {"q": "x"})), reply("done"))
    assert (await world.run(world.kernel(port, inputs=_Renderer(events=[delta])))).status == "completed"
    types = world.types()
    at = types.index("input.consumed")
    assert types[at + 1] == "x.test.citations.recorded"


@pytest.mark.parametrize(
    ("failpoint", "renders"),
    [("worker.input.before_consume_commit", 2), ("worker.input.after_consume_commit", 1)],
)
async def test_a_crash_renders_again_only_before_the_consumption_committed(
    failpoint: str, renders: int
) -> None:
    world = _world_with_steer(attachments=[FILE])
    renderer = _Renderer()
    _, port = scripted(reply("", ("lookup", {"q": "x"})), reply("done"), reply("done"))
    registry.arm(failpoint, Arm(Raise(Crash), Nth(1)))
    await world.crash(world.kernel(port, inputs=renderer))
    registry.reset()
    assert (await world.run(world.kernel(port, inputs=renderer))).status == "completed"
    assert len(renderer.calls) == renders
    assert len(world.events("input.consumed")) == 1
    last = port.calls[-1].messages
    assert [m for m in last if isinstance(m, UserMessage) and isinstance(m.content, list)] == [
        UserMessage(content=[TextPart(text=f"{STEER}\n\n<record>plan body</record>"), IMAGE])
    ]


async def test_text_alone_is_consumed_as_before_without_the_renderer() -> None:
    world = _world_with_steer()
    renderer = _Renderer()
    _, port = scripted(reply("", ("lookup", {"q": "x"})), reply("done"))
    assert (await world.run(world.kernel(port, inputs=renderer))).status == "completed"
    assert renderer.calls == []
    (consumed,) = world.events("input.consumed")
    assert consumed.data == {"messageSeq": 1}
    assert port.calls[1].messages[-1] == UserMessage(content=STEER)


async def test_without_a_renderer_the_model_gets_the_text_and_the_worker_says_so(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    world = _world_with_steer(attachments=[FILE])
    _, port = scripted(reply("", ("lookup", {"q": "x"})), reply("done"))
    assert (await world.run(world.kernel(port))).status == "completed"
    assert world.events("input.consumed")[0].data == {"messageSeq": 1}
    assert port.calls[1].messages[-1] == UserMessage(content=STEER)
    (line,) = _lines(caplog, "input.references_unrendered")
    assert line.levelno == logging.WARNING


# ---- fold 6 ----

REF = {"ref": "inline:aGk", "hash": "sha256:8f434346648f6b96df89dda901c5176b10a6d83961dd3c1ac88b59b2dc327aa4", "size": 2}


def _event(seq: int, type_: str, data: dict[str, JsonValue], writer: str = "worker") -> JournalEvent:
    return JournalEvent(
        org_id="org1",
        session_id=SESSION,
        stream_id=SESSION,
        seq=seq,
        run_id=RUN,
        attempt_id="a" if writer == "worker" else None,
        fence=1 if writer == "worker" else None,
        type=type_,
        critical=True,
        ts="2026-10-01T12:00:00.000Z",
        writer=writer,  # type: ignore[arg-type]
        data=data,
    )


async def test_fold_6_places_the_rendered_parts_where_the_message_was_consumed() -> None:
    assert FOLD_VERSION >= 6
    from harness_core.journal import InMemoryContentStore
    from harness_core.kernel.durable.transcript import encode_user_parts

    content = InMemoryContentStore()
    parts = [TextPart(text="hi, see the file"), IMAGE]
    content_ref = (await content.put(encode_user_parts(parts))).model_dump(mode="json", by_alias=True)
    events = [
        _event(1, "input.queued", {"messageSeq": 1, "textRef": REF, "attachments": [FILE], "recordIds": ["rec-9"]}, "node"),
        _event(2, "input.consumed", {"messageSeq": 1, "contentRef": content_ref}),
        _event(3, "input.consumed", {"messageSeq": 1, "contentRef": content_ref}),
    ]
    session = fold_session(events, stream_id=SESSION)
    (entry,) = [e for e in session.entries if e.input_seq is not None]
    assert entry.content_ref is not None and entry.text_ref is not None
    assert session.ignored_events == 1
    assert decode_user_parts(await content.get(entry.content_ref)) == parts

    class _NoModel:
        def restore(self, *args: object, **kwargs: object) -> None:
            return None

    rendered = await TranscriptRenderer(content, _NoModel()).render(session)  # type: ignore[arg-type]
    assert rendered == [UserMessage(content=parts)]


def test_fold_6_keeps_the_references_until_consumed() -> None:
    events = [
        _event(1, "input.queued", {"messageSeq": 1, "textRef": REF, "attachments": [FILE], "recordIds": ["rec-9"]}, "node"),
    ]
    session = fold_session(events, stream_id=SESSION)
    queued = session.queued_inputs[f"{RUN}:1"]
    assert queued.has_references
    assert [a.record_id for a in queued.attachments] == ["rec-1"]
    assert queued.record_ids == ["rec-9"]
