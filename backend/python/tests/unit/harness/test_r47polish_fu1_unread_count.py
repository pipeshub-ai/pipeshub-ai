"""R47POLISH-FU-1 (ADR-RUN-03 D5): several files of a chat turn the model never read and may not name say how many.

``unread_attachments`` deduped the turn's unnamed unread files (``item not in items``), so two unnamed files the
model never read were one item without a count and the answer said "one of the attached files". They are now one item
per kind with ``count`` behind ``attachment_count`` (as R46FIX-FU-2 did for a job's skipped inputs), and the turn's and
each steered message's add up.
"""

from __future__ import annotations

import dataclasses
from types import SimpleNamespace
from typing import Any

import pytest

from app.harness.assembly.attachment_limits import (
    STEERED_UNREAD_SLOT,
    UNREAD_ATTACHMENTS_KEY,
    record_unread,
    unread_attachments,
    unread_of,
)
from app.harness.assembly.context import Attachment
from app.harness.presentation import unread_attachments_of, with_limits
from harness_core.journal.types import ATTACHMENT_LIMITS
from tests.unit.harness import test_r35_attachment_limits_every_path as r35
from tests.unit.harness.assembly import world as w

REFUSED = "attachment.not_accessible"
UNREADABLE = "attachment.unreadable"


@pytest.fixture(autouse=True)
def _deployment(monkeypatch: pytest.MonkeyPatch):  # noqa: ANN202
    monkeypatch.setenv("PIPESHUB_ENABLE_LAZY_TOOLS", "false")
    monkeypatch.setenv("PIPESHUB_ENABLE_FINAL_ANSWER", "false")
    with w.deployment():
        yield


def _unnamed(vrid: str) -> dict[str, Any]:
    return {"recordId": f"rec-{vrid}", "virtualRecordId": vrid, "mimeType": "application/pdf"}


def _named(items: list[dict[str, Any]]) -> list[Attachment]:
    return [a for i in items if (a := Attachment.of(i)) is not None]


def _event(seq: int, items: list[dict[str, Any]]) -> Any:  # noqa: ANN401
    return SimpleNamespace(type="loop.state_saved", data={"slots": {f"{STEERED_UNREAD_SLOT}{seq}": items}})


def test_two_unnamed_unread_files_are_one_counted_item() -> None:
    files = _named([_unnamed("v1"), _unnamed("v2"), _unnamed("v3")])
    items = unread_attachments(files, [{"virtualRecordId": "v3"}], {}, multimodal=True)
    assert items == [{"kind": REFUSED, "count": 2}, {"kind": UNREADABLE}]


def test_a_node_without_attachment_count_gets_one_uncounted_item_as_before() -> None:
    files = _named([_unnamed("v1"), _unnamed("v2")])
    assert unread_attachments(files, [], {}, multimodal=True, counted=False) == [{"kind": REFUSED}]


async def test_a_chat_turn_with_two_unnamed_refused_files_says_how_many() -> None:
    with r35._Blobs({}).patched():
        runtime = await r35._run(
            r35._chat_spec(), r35._world({}), [_unnamed("v1"), _unnamed("v2")], chat_mode="internal_search"
        )
    assert r35._answer(runtime).startswith("I couldn't read 2 of the attached files: access to them was refused.")


async def test_the_turn_s_and_a_steered_message_s_unnamed_files_add_up() -> None:
    spec, agent = r35._agent_spec()
    with r35._Blobs({}).patched():
        runtime = await r35._run(spec, w.Services(agent=agent), [_unnamed("v1")])
        journal: list[Any] = []
        await r35._steer(runtime, [_unnamed("v2"), _unnamed("v3")], journal, seq=1)
    files, _typed = unread_attachments_of(runtime.presenter._context, journal)  # type: ignore[union-attr]
    assert list(files) == [{"kind": REFUSED, "count": 3}]


async def test_a_node_that_reads_the_kind_but_not_the_count_gets_one_item() -> None:
    from app.harness.assembly.assembler import PipesHubRuntimeAssembler
    from harness_core.testing import ModelScript, Reply, ScriptedModel, Turn
    from harness_core.testing.durable import ScriptedModelPort

    spec, agent = r35._agent_spec()
    claim = dataclasses.replace(
        w.claim(spec, query=r35.QUERY, attachments=[_unnamed("v1"), _unnamed("v2")]),
        accepts=frozenset({ATTACHMENT_LIMITS}),
    )
    port = ScriptedModelPort(ScriptedModel(ModelScript(id="p", turns=[Turn(reply=Reply(text="ok"))])))
    with r35._Blobs({}).patched():
        runtime = await PipesHubRuntimeAssembler(w.Services(agent=agent), model_factory=w.model_factory(port)).build(
            claim
        )
        for step in runtime.pre_run:
            _ = await step.run(r35.QUERY)
        journal: list[Any] = []
        await r35._steer(runtime, [_unnamed("v3")], journal)
    data: dict[str, Any] = {"answer": r35.ANSWER, "partTexts": [r35.ANSWER]}
    with_limits(data, [], unread_attachments_of(runtime.presenter._context, journal))  # type: ignore[union-attr]
    assert data["limits"]["items"] == [{"kind": REFUSED}]


def test_a_steered_message_journaled_twice_counts_once() -> None:
    state: dict[str, Any] = {}
    record_unread(state, [], typed=True, counted=True)
    two = [{"kind": REFUSED, "count": 2}]
    files, typed = unread_of(state, [_event(1, two), _event(1, two)])
    assert (files, typed) == ([{"kind": REFUSED, "count": 2}], True)


def test_state_recorded_before_the_count_still_reads() -> None:
    state = {UNREAD_ATTACHMENTS_KEY: ([{"kind": UNREADABLE}], False)}
    assert unread_of(state) == ([{"kind": UNREADABLE}], False)
