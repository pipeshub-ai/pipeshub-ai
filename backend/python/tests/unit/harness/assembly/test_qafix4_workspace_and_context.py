"""QAFIX4 B1 and B2 through the real assembler and driver over the in-memory journal (QA-FP12, task 4).

B1: a file ``run_code`` writes into the chat's workspace (``$WORKSPACE_DIR``) is saved as a workspace revision and
is there for the next turn's sandbox; the tool's description says where that folder is, and a call that writes
files anywhere else is told they will not be kept.

B2: a long run's request always carries the user's own message. The legacy PRE_MODEL shapers that cut history
(loop compaction at every fifth turn, the sliding window, deterministic compaction) did it without a journal entry
and dropped the message the run was answering; on the durable loop only the journaled ladder reduces history.
"""

from __future__ import annotations

import json
import os
from contextlib import ExitStack
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import pytest

from app.harness.assembly.assembler import PipesHubRuntimeAssembler
from app.harness.assembly.model import ResolvedModel
from app.harness.assembly.run_code_workspace import RunCodeWorkspace
from harness_core.driver import RunDriver
from harness_core.kernel.core.messages import UserMessage
from harness_core.testing import (
    ManualClock,
    ModelScript,
    Reply,
    ScriptedModel,
    ToolCallSpec,
    Turn,
)
from harness_core.testing.durable import ScriptedModelPort
from harness_core.workspace.sync import TAR
from tests.harness_replay.artifacts import ParityArtifactRegistry
from tests.support.sandbox_env import sandbox_backend, sandbox_env
from tests.unit.harness_core.workspace.sync.support import SESSION, FakeSyncHarness

from . import world as w

if TYPE_CHECKING:
    from collections.abc import Iterator

    from app.harness.assembly.context import RunContext
    from app.harness.journal_node import NodeClaim

RUN_CODE = "run_code"
NOTES = "Batch 1: login failures (12), billing (4).\n"
WRITE_NOTES = (
    "import os, pathlib\n"
    "notes = pathlib.Path(os.environ['WORKSPACE_DIR']) / 'notes' / 'themes.md'\n"
    "notes.parent.mkdir(parents=True, exist_ok=True)\n"
    f"notes.write_text({NOTES!r})\n"
    "print('saved')\n"
)
READ_NOTES = (
    "import os, pathlib\n"
    "print(pathlib.Path(os.environ['WORKSPACE_DIR'], 'notes', 'themes.md').read_text())\n"
)
WRITE_OUTSIDE = "import pathlib\npathlib.Path('notes').mkdir(exist_ok=True)\npathlib.Path('notes/themes.md').write_text('x')\n"


@pytest.fixture(autouse=True)
def _deployment() -> Iterator[None]:
    with ExitStack() as stack:
        stack.enter_context(w.deployment())
        stack.enter_context(patch("app.services.artifact_registry.ArtifactRegistryService", new=ParityArtifactRegistry(w.ORG, "ses-1")))
        stack.enter_context(patch.dict(os.environ, {**sandbox_env("host"), "SANDBOX_ALLOW_NETWORK": "false"}))
        stack.enter_context(sandbox_backend("host"))
        yield


def _port(*replies: Reply) -> ScriptedModelPort:
    return ScriptedModelPort(ScriptedModel(ModelScript(id="qafix4", turns=[Turn(reply=r) for r in replies])))


def _code(code: str, call_id: str) -> Reply:
    return Reply(tool_calls=[ToolCallSpec(id=call_id, name=RUN_CODE, args={"code": code, "language": "python"})])


def _model(port: ScriptedModelPort, context_length: int) -> Any:  # noqa: ANN401
    from tests.unit.agents.adapter.conftest import FakeChatModel

    async def build(run: RunContext, services: object) -> ResolvedModel:
        return ResolvedModel(
            raw_llm=FakeChatModel(), provider="openai", is_multimodal=False,
            context_length=context_length, is_reasoning=False, port=port,
        )

    return build


class Session:
    """One chat on the code-running agent, its runs driven one after another, with the chat's workspace."""

    def __init__(self, port: ScriptedModelPort, *, context_length: int = 128_000) -> None:
        self.h = FakeSyncHarness(TAR)
        self.port = port
        self.clock = ManualClock()
        self.journal = w.pipeshub_journal(clock=self.clock, lease_ms=30_000)

        async def workspace(claim: NodeClaim, run: RunContext) -> RunCodeWorkspace:
            return RunCodeWorkspace(store=self.h.store, objects=self.h.objects, scope=self.h.object_scope, workspace_id=SESSION)

        assembler = PipesHubRuntimeAssembler(
            w.Services(), model_factory=_model(port, context_length), run_code_workspace=workspace
        )
        self.driver = RunDriver(journal=self.journal, assembler=w.ClaimAssembler(assembler), clock=self.clock)

    async def ask(self, run_id: str, text: str) -> Any:  # noqa: ANN401
        spec = w.pinned_spec(tool_ids=[], sandbox="agent-code")
        self.journal.admit(run_id, org_id=w.ORG, session_id="ses-1", spec=spec, input_text=text)
        return await self.driver.run_claimed(await w.claim_next(self.journal, run_id))

    def outcomes(self) -> dict[str, dict[str, Any]]:
        found: dict[str, dict[str, Any]] = {}
        for event in self.journal.events("ses-1"):
            if event.type == "tool.outcome":
                found[str(event.data["invocationId"])] = event.data
        return found

    def result(self, call_id: str) -> dict[str, Any]:
        """The ``run_code`` result the model read for ``call_id``, from the next request it was sent in."""
        for call in self.port.calls:
            for message in call.messages:
                if getattr(message, "tool_call_id", None) == call_id:
                    content = message.content if isinstance(message.content, str) else "".join(
                        getattr(p, "text", "") for p in message.content
                    )
                    return json.loads(content[content.index("{") : content.rindex("}") + 1])
        raise AssertionError(f"no result for {call_id}")


# --- B1: the chat's workspace ------------------------------------------------------------------------


async def test_b1_a_file_written_to_the_workspace_is_saved_and_the_next_turn_reads_it() -> None:
    port = _port(
        _code(WRITE_NOTES, "w1"), Reply(text="Noted."),
        _code(READ_NOTES, "r1"), Reply(text="Here are the notes."),
    )
    chat = Session(port)
    first = await chat.ask("run-1", "keep notes in your workspace")
    assert first.status == "completed"
    head = await chat.h.store.head(chat.h.object_scope, SESSION)
    assert sorted(head.files) == ["notes/themes.md"], "the file the model wrote is the workspace's next revision"
    assert await chat.h.objects.get(chat.h.object_scope, head.files["notes/themes.md"].content) == NOTES.encode()

    second = await chat.ask("run-2", "what do your notes say")
    assert second.status == "completed"
    read = chat.result("r1")
    assert read["exit_code"] == 0, read
    assert NOTES.strip() in read["stdout"], "a fresh sandbox in the next turn has the workspace's files"


async def test_b1_the_tool_tells_the_model_where_the_workspace_is() -> None:
    port = _port(Reply(text="ok"))
    chat = Session(port)
    assert (await chat.ask("run-1", "hi")).status == "completed"
    (schema,) = [t for t in port.calls[0].tools or [] if t.name == RUN_CODE]
    description = schema.description
    assert "$WORKSPACE_DIR" in description
    assert "later" in description and "kept" in description, description


async def test_b1_files_written_outside_the_workspace_are_named_as_not_kept() -> None:
    port = _port(_code(WRITE_OUTSIDE, "o1"), Reply(text="done"))
    chat = Session(port)
    assert (await chat.ask("run-1", "keep notes")).status == "completed"
    result = chat.result("o1")
    assert result.get("not_kept") == ["notes/themes.md"], result
    assert "$WORKSPACE_DIR" in result["not_kept_note"]
    head = await chat.h.store.head(chat.h.object_scope, SESSION)
    assert head.files == {}


# --- B2: the user's message survives a long run --------------------------------------------------------


async def test_b2_every_request_of_a_long_run_carries_the_users_message() -> None:
    """QA-FP12 run 8cbf6950: turn 6 (the legacy loop's fifth) went out at a fifth of the size without the batch the
    user had pasted, and the model asked what to do. A small window makes the legacy shapers fire on every turn.
    Since main #4083 the system prompt and the tools count against the window too, so the window is the smallest
    that still holds them beside the pasted batch."""
    batch = "TICKET " + " ".join(f"label-{i}" for i in range(6_000))
    calls = [_code(f"print({i})", f"c{i}") for i in range(5)]
    port = _port(Reply(text="Hello."), *calls, Reply(text="Tallied."))
    chat = Session(port, context_length=32_000)
    assert (await chat.ask("run-1", "hi")).status == "completed"
    done = await chat.ask("run-2", f"Tally these labels:\n{batch}")
    assert done.status == "completed"
    run_two = port.calls[1:]
    assert len(run_two) == 6
    for turn, call in enumerate(run_two, start=1):
        texts = [str(m.content) for m in call.messages if isinstance(m, UserMessage)]
        assert any(batch in text for text in texts), f"turn {turn} lost the user's message"
