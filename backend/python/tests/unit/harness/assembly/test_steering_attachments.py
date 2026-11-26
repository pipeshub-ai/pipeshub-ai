"""P1b T2b (C-21b): a steering message's attachments reach the model as a new turn's do.

The oracle is the durable new turn itself, built in the same world: the goal
step's description (every run's attachment text and citation hint), the
built-in assistant's constraints (its chat attachment text) and the images the
first user message carries. A steered message with the same attachments gives
the model the same text and the same images, in its own user message.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, patch

import pytest

from app.agents.chat_modes.attachments import ResolvedAttachments
from app.utils.chat_helpers import image_dict_to_part
from harness_core.kernel.core.messages import TextPart
from harness_core.kernel.durable.ports import SteeringInput
from harness_core.kernel.durable.slots import PRE_RUN_SLOT_PREFIX
from harness_core.testing import ModelScript, Reply, ScriptedModel, Turn
from harness_core.testing.durable import ScriptedModelPort

from . import world as w
from .test_loop_tools import _full_grant

if TYPE_CHECKING:
    from collections.abc import Iterator

    from harness_core.driver.run_driver import AssembledRuntime

QUERY = "How much leave?"
ATTACHMENTS: list[dict[str, Any]] = [
    {"recordId": "rec-1", "virtualRecordId": "vr-1", "recordName": "Leave.pdf", "mimeType": "application/pdf"}
]
IMAGE = {"type": "image_url", "image_url": {"url": "data:image/png;base64,iVBORw0KGgo="}}
GOAL_TEXT = "\n\nAttached files from the user:\n<record>Leave policy: 25 days</record>"


@pytest.fixture(autouse=True)
def _deployment(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("PIPESHUB_ENABLE_LAZY_TOOLS", "false")
    monkeypatch.setenv("PIPESHUB_ENABLE_FINAL_ANSWER", "false")
    with w.deployment():
        yield


def _resolvers() -> tuple[AsyncMock, AsyncMock]:
    goal = AsyncMock(return_value=(GOAL_TEXT, [IMAGE]))
    # Main moved chat-mode attachment text into the goal resolver; the chat resolver only scopes retrieval.
    chat = AsyncMock(return_value=ResolvedAttachments(virtual_record_ids=["vr-1"]))
    return goal, chat


async def _build(services: w.Services, spec: Any, **claim: Any) -> AssembledRuntime:  # noqa: ANN401
    from app.harness.assembly.assembler import PipesHubRuntimeAssembler

    port = ScriptedModelPort(ScriptedModel(ModelScript(id="p", turns=[Turn(reply=Reply(text="ok"))])))
    # The actor may access the attached file (main #4255 checks it by its content id).
    services.readable_vrids["vr-1"] = "rec-1"
    return await PipesHubRuntimeAssembler(services, model_factory=w.model_factory(port)).build(
        w.claim(spec, **claim)
    )


async def _goal(runtime: AssembledRuntime) -> dict[str, Any]:
    slots: dict[str, Any] = {}
    for step in runtime.pre_run:
        slots[PRE_RUN_SLOT_PREFIX + step.key] = await step.run(QUERY)
    return slots["pre_run.goal"]


def _patched(goal: AsyncMock, chat: AsyncMock) -> Any:  # noqa: ANN401
    from contextlib import ExitStack

    stack = ExitStack()
    for target in (
        "app.agents.agent_loop.hooks.resolve_attachments_for_goal",
        "app.agents.agent_loop.factory.resolve_attachments_for_goal",
    ):
        stack.enter_context(patch(target, goal))
    for target in ("app.agents.chat_modes.setup.resolve_attachments", "app.agents.chat_modes.attachments.resolve_attachments"):
        stack.enter_context(patch(target, chat))
    return stack


async def test_an_agent_run_gives_the_steered_attachment_the_new_turns_text_and_images(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO)
    agent = w.agent_doc()
    spec = w.pinned_spec(**_full_grant(agent))
    goal, chat = _resolvers()
    with _patched(goal, chat):
        new_turn = await _build(w.Services(agent=agent), spec, query=QUERY, attachments=ATTACHMENTS)
        oracle = await _goal(new_turn)
        images = new_turn.presenter._context.attachment_image_blocks  # type: ignore[union-attr]
        steered_run = await _build(w.Services(agent=agent), spec, query="an earlier question")
        _ = await _goal(steered_run)
        assert steered_run.inputs is not None
        rendered = await steered_run.inputs.render(
            SteeringInput(message_seq=1, text=QUERY, attachments=tuple(ATTACHMENTS))
        )
    assert rendered.parts[0] == TextPart(text=oracle["description"])
    assert rendered.parts[1:] == [image_dict_to_part(i) for i in images]
    # The resolver was asked for this message's attachments, not the run's own.
    assert goal.await_args_list[-1].kwargs["attachments"] == ATTACHMENTS
    (line,) = [r for r in caplog.records if getattr(r, "harness_event", None) == "input.rendered"]
    fields = line.harness  # type: ignore[attr-defined]
    assert (fields["attachments"], fields["images"], fields["attachmentText"]) == (1, 1, True)
    assert "Leave" not in repr(fields) and QUERY not in repr(fields)


async def test_text_and_records_alone_render_the_text_as_a_new_turn_does() -> None:
    agent = w.agent_doc()
    spec = w.pinned_spec(**_full_grant(agent))
    goal, chat = _resolvers()
    with _patched(goal, chat):
        runtime = await _build(w.Services(agent=agent), spec, query="an earlier question")
        assert runtime.inputs is not None
        rendered = await runtime.inputs.render(SteeringInput(message_seq=1, text=QUERY, record_ids=("rec-9",)))
    assert rendered.parts == [TextPart(text=QUERY)]
    assert goal.await_count == 0


async def test_a_steered_attachment_the_sender_cannot_read_never_reaches_the_model() -> None:
    """BUGS-B (SYNC4 §10, steering by collaborators): only the run's own actor steers a run (Node's
    ``assertSteeredByActor``; anyone else's message waits as a next run that runs as its sender), so the sender is the
    identity the check runs as. An attachment they cannot read is dropped whatever ``recordId`` the client sent."""
    agent = w.agent_doc()
    spec = w.pinned_spec(**_full_grant(agent))
    goal, chat = _resolvers()
    services = w.Services(agent=agent)
    with _patched(goal, chat):
        runtime = await _build(services, spec, query="an earlier question")
        services.readable_vrids.clear()  # the sender lost access to vr-1 (or never had it)
        assert runtime.inputs is not None
        rendered = await runtime.inputs.render(
            SteeringInput(message_seq=1, text=QUERY, attachments=tuple(ATTACHMENTS))
        )
    assert rendered.parts == [TextPart(text=QUERY)]
    assert goal.await_count == 0
