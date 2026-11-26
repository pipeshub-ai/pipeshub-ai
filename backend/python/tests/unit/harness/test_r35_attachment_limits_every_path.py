"""R34FIX-FU-1 (ADR-RUN-03 D5): a file the model never read is named in the answer on every path that reads one.

R34-FIX named a turn's unread file only on an agent's loop run. The built-in assistant's chat-mode runs and a steered
(mid-run) message read their attachments too, as the person who attached them (D-ATT-1), yet a file the access check
dropped or that came back without content was never named: the answer read complete. Every reader now records what the
model was not given through one recorder, and the one finaliser (``with_limits``) leads the answer with the line.
"""

from __future__ import annotations

import dataclasses
from contextlib import ExitStack
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import pytest

from app.harness.presentation import unread_attachments_of, with_limits
from harness_core.kernel.durable.ports import SteeringInput
from harness_core.kernel.durable.slots import PRE_RUN_SLOT_PREFIX
from harness_core.testing import ModelScript, Reply, ScriptedModel, Turn
from harness_core.testing.durable import ScriptedModelPort
from tests.unit.harness.assembly import world as w
from tests.unit.harness.assembly.test_loop_tools import _full_grant

if TYPE_CHECKING:
    from collections.abc import Iterator

    from harness_core.driver.run_driver import AssembledRuntime

QUERY = "Summarise the file."
ANSWER = "Here is the summary."
REFUSED = "I couldn't read the attached file {name}: access to it was refused."
UNREADABLE = "I couldn't read the attached file {name}: its content could not be read."


def _file(name: str, vrid: str) -> dict[str, Any]:
    return {"recordId": f"rec-{vrid}", "virtualRecordId": vrid, "recordName": name, "mimeType": "application/pdf"}


@pytest.fixture(autouse=True)
def _deployment(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("PIPESHUB_ENABLE_LAZY_TOOLS", "false")
    monkeypatch.setenv("PIPESHUB_ENABLE_FINAL_ANSWER", "false")
    with w.deployment():
        yield


class _Blobs:
    """The resolver's reads: a file with content resolves into the citation state; one without comes back empty."""

    def __init__(self, content: dict[str, str]) -> None:
        self.content = content

    async def resolve(
        self, context: Any, log: object, attachments: list[dict[str, Any]] | None = None, **_: object  # noqa: ANN401
    ) -> tuple[str, list[dict[str, Any]]]:
        files = attachments if attachments is not None else context.tool_state.get("attachments") or []
        read = {a["virtualRecordId"]: self.content[a["virtualRecordId"]] for a in files if a["virtualRecordId"] in self.content}
        context.tool_state.setdefault("virtual_record_id_to_result", {}).update({v: {"text": t} for v, t in read.items()})
        return "\n".join(f"<record>{t}</record>" for t in read.values()), []

    def patched(self) -> ExitStack:
        stack = ExitStack()
        for target in (
            "app.agents.agent_loop.hooks.resolve_attachments_for_goal",
            "app.agents.agent_loop.factory.resolve_attachments_for_goal",
        ):
            stack.enter_context(patch(target, self.resolve))
        return stack


def _chat_spec() -> Any:  # noqa: ANN401
    return w.pinned_spec(tool_ids=[], kb=[], agent=False, chat={"mode": "internal_search", "internalSearch": True}, strict_scope=False)


def _agent_spec() -> tuple[Any, dict[str, Any]]:
    agent = w.agent_doc()
    return w.pinned_spec(**_full_grant(agent)), agent


async def _run(
    spec: Any, services: w.Services, attachments: list[dict[str, Any]], *, chat_mode: str = "quick"  # noqa: ANN401
) -> AssembledRuntime:
    from app.harness.assembly.assembler import PipesHubRuntimeAssembler

    port = ScriptedModelPort(ScriptedModel(ModelScript(id="p", turns=[Turn(reply=Reply(text="ok"))])))
    services.retrieval.search_with_filters = _no_results
    runtime = await PipesHubRuntimeAssembler(services, model_factory=w.model_factory(port)).build(
        w.claim(spec, query=QUERY, chat_mode=chat_mode, attachments=attachments)
    )
    for step in runtime.pre_run:
        _ = {PRE_RUN_SLOT_PREFIX + step.key: await step.run(QUERY)}
    return runtime


async def _no_results(*_: object, **__: object) -> dict[str, Any]:
    return {"status_code": 200, "searchResults": [], "virtual_to_record_map": {}}


def _answer(runtime: AssembledRuntime, journal: list[Any] | None = None) -> str:
    """The answer as the finaliser leads it (``PipesHubPresenter.finalize``), from what the attempt recorded and the
    run journaled."""
    data: dict[str, Any] = {"answer": ANSWER, "partTexts": [ANSWER]}
    with_limits(data, [], unread_attachments_of(runtime.presenter._context, journal or []))  # type: ignore[union-attr]
    return str(data["answer"])


def _world(readable: dict[str, str]) -> w.Services:
    services = w.Services(agent={})
    services.readable_vrids.update(readable)
    return services


# --- the built-in assistant's chat-mode run --------------------------------------------------------------------------


async def test_a_chat_mode_run_names_the_attachment_the_person_may_not_access() -> None:
    blobs = _Blobs({"v1": "text one"})
    with blobs.patched():
        runtime = await _run(_chat_spec(), _world({}), [_file("secret.pdf", "v1")], chat_mode="internal_search")

    assert _answer(runtime).startswith(REFUSED.format(name="secret.pdf"))


async def test_a_chat_mode_run_names_the_attachment_that_came_back_without_content() -> None:
    blobs = _Blobs({})
    with blobs.patched():
        runtime = await _run(_chat_spec(), _world({"v1": "rec-v1"}), [_file("empty.pdf", "v1")], chat_mode="internal_search")

    assert _answer(runtime).startswith(UNREADABLE.format(name="empty.pdf"))


async def test_a_chat_mode_run_that_read_its_attachment_says_nothing_more() -> None:
    blobs = _Blobs({"v1": "text one"})
    with blobs.patched():
        runtime = await _run(_chat_spec(), _world({"v1": "rec-v1"}), [_file("read.pdf", "v1")], chat_mode="internal_search")

    assert _answer(runtime) == ANSWER


# --- a steered message's attachments ---------------------------------------------------------------------------------


async def _steer(runtime: AssembledRuntime, attachments: list[dict[str, Any]], journal: list[Any], seq: int = 1) -> None:
    """A steered message consumed: what the renderer returns is journaled with the consumption."""
    assert runtime.inputs is not None
    rendered = await runtime.inputs.render(
        SteeringInput(message_seq=seq, text="and this one", attachments=tuple(attachments))
    )
    journal.extend(rendered.events)


@pytest.mark.parametrize("chat", [True, False], ids=["chat-mode", "agent"])
async def test_a_steered_attachment_the_sender_may_not_access_is_named_in_the_final_answer(chat: bool) -> None:
    spec, agent = (_chat_spec(), {}) if chat else _agent_spec()
    services = w.Services(agent=agent)
    blobs = _Blobs({"v2": "text two"})
    with blobs.patched():
        runtime = await _run(spec, services, [], chat_mode="internal_search" if chat else "quick")
        journal: list[Any] = []
        await _steer(runtime, [_file("theirs.pdf", "v2")], journal)

    assert _answer(runtime, journal).startswith(REFUSED.format(name="theirs.pdf"))
    # A resumed attempt does not render the message again: the journal alone names it.
    fresh = await _run(spec, w.Services(agent=agent), [], chat_mode="internal_search" if chat else "quick")
    assert _answer(fresh, journal).startswith(REFUSED.format(name="theirs.pdf"))


async def test_a_steered_attachment_without_content_is_named_and_a_readable_one_is_not() -> None:
    spec, agent = _agent_spec()
    services = w.Services(agent=agent)
    services.readable_vrids.update({"v2": "rec-v2", "v3": "rec-v3"})
    blobs = _Blobs({"v3": "text three"})
    with blobs.patched():
        runtime = await _run(spec, services, [])
        journal: list[Any] = []
        await _steer(runtime, [_file("empty.pdf", "v2"), _file("fine.pdf", "v3")], journal)

    answer = _answer(runtime, journal)
    assert answer.startswith(UNREADABLE.format(name="empty.pdf"))
    assert "fine.pdf" not in answer


async def test_the_turn_s_and_the_steered_files_are_named_together_once_each() -> None:
    spec, agent = _agent_spec()
    services = w.Services(agent=agent)
    blobs = _Blobs({})
    with blobs.patched():
        runtime = await _run(spec, services, [_file("first.pdf", "v1")])
        journal: list[Any] = []
        await _steer(runtime, [_file("first.pdf", "v1"), _file("second.pdf", "v2")], journal, seq=1)
        await _steer(runtime, [_file("second.pdf", "v2")], journal, seq=2)

    files, _typed = unread_attachments_of(runtime.presenter._context, journal)  # type: ignore[union-attr]
    assert list(files) == [
        {"kind": "attachment.not_accessible", "file": "first.pdf"},
        {"kind": "attachment.not_accessible", "file": "second.pdf"},
    ]


async def test_a_steered_file_travels_untyped_to_a_node_that_cannot_read_the_kind() -> None:
    from app.harness.assembly.assembler import PipesHubRuntimeAssembler

    spec, agent = _agent_spec()
    claim = w.claim(spec, query=QUERY)
    old = dataclasses.replace(claim, accepts=frozenset())
    port = ScriptedModelPort(ScriptedModel(ModelScript(id="p", turns=[Turn(reply=Reply(text="ok"))])))
    with _Blobs({}).patched():
        runtime = await PipesHubRuntimeAssembler(w.Services(agent=agent), model_factory=w.model_factory(port)).build(old)
        for step in runtime.pre_run:
            _ = await step.run(QUERY)
        journal: list[Any] = []
        await _steer(runtime, [_file("theirs.pdf", "v2")], journal)

    data: dict[str, Any] = {"answer": ANSWER, "partTexts": [ANSWER]}
    with_limits(data, [], unread_attachments_of(runtime.presenter._context, journal))  # type: ignore[union-attr]
    assert "limits" not in data
    assert data["answer"].startswith(REFUSED.format(name="theirs.pdf"))


# --- the loop path, unchanged ----------------------------------------------------------------------------------------


async def test_the_agent_loop_run_still_names_its_unread_attachment() -> None:
    spec, agent = _agent_spec()
    services = w.Services(agent=agent)
    services.readable_vrids["v1"] = "rec-v1"
    blobs = _Blobs({"v1": "text one"})
    with blobs.patched():
        runtime = await _run(spec, services, [_file("read.pdf", "v1"), _file("refused.pdf", "v9")])

    answer = _answer(runtime)
    assert answer.startswith(REFUSED.format(name="refused.pdf"))
    assert "read.pdf:" not in answer
