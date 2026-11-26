"""LONGTASK-F6: the run's own loop sees its chat's workspace.

Live (S3 resume-handover, gpt-6-luna): on day two, asked to "continue where we left off", a react agent looked for
the files it had written the day before through knowledge search, found nothing, and asked the person. Only
``run_code`` (inside ``coding_agent`` in react) could reach ``$WORKSPACE_DIR``. Every run now starts with the
workspace's index (its files, and the wiki's index page when there is one), journaled once so every attempt sends
the same text, and the loop can list and read workspace files itself.
"""

from __future__ import annotations

import json

import pytest

from app.harness.assembly.run_code_workspace import RunCodeWorkspace
from app.harness.assembly.workspace_view import (
    WORKSPACE_SLOT,
    WorkspaceContextHost,
    WorkspaceContextShaper,
    WorkspaceViewStep,
)
from harness_core.kernel.fold import RunState
from harness_core.logs import HarnessLog
from harness_core.testing.contracts.durable_ports import PreRunStepContract
from harness_core.testing.contracts.durable_turns import (
    ContextHostContract,
    TurnShaperContract,
)
from harness_core.testing.durable_ports import DefaultContextHost, PassThroughTurnShaper
from harness_core.workspace.sync import TAR
from tests.support.longtask_rig.brain import LongTaskModel, compile_steps
from tests.support.longtask_rig.rig import LongTaskChat
from tests.support.longtask_rig.scenario import SYSTEM_MARKER, code
from tests.unit.harness_core.durable.test_durable_port_contracts import Fresh
from tests.unit.harness_core.workspace.sync.support import SESSION, FakeSyncHarness

INDEX = "# Study\\n\\n- [Costs](findings/costs.md)\\n"
WRITE = (
    "import os, pathlib\n"
    "w = pathlib.Path(os.environ['WORKSPACE_DIR'])\n"
    "(w / 'wiki/findings').mkdir(parents=True, exist_ok=True)\n"
    f"(w / 'wiki/index.md').write_text('{INDEX}')\n"
    "(w / 'wiki/findings/costs.md').write_text('Total 412 USD\\n')\n"
    "print('saved')\n"
)


def _chat(mode: str) -> tuple[LongTaskChat, LongTaskModel]:
    scripts = {
        "A": ("[A] start the notes", compile_steps("A", [code("Saved.", WRITE, delegated=mode == "react"), "say saved"])),
        "B": (
            "[B] continue where we left off",
            compile_steps(
                "B",
                [
                    "tool list_workspace_files {}",
                    "tool read_workspace_file " + json.dumps({"path": "wiki/findings/costs.md"}),
                    "say Costs: {result:B-2}",
                ],
            ),
        ),
    }
    model = LongTaskModel(scripts=scripts, system_marker=SYSTEM_MARKER, route=None if mode == "react" else "quick")
    return LongTaskChat(model=model, context_length=128_000, chat_mode=mode), model


async def _run(mode: str) -> tuple[LongTaskChat, LongTaskModel]:
    chat, model = _chat(mode)
    with chat.deployment(), chat.watching():
        assert (await chat.ask("A", "[A] start the notes")).status == "completed"
        assert (await chat.ask("B", "[B] continue where we left off")).status == "completed"
    return chat, model


async def test_every_request_of_a_later_run_starts_from_the_workspace_index() -> None:
    _, model = await _run("react")
    later = [r for r in model.requests if r.task == "B" and r.kind == "turn"]
    assert later
    for request in later:
        shown = "\n".join(request.user_texts)
        assert "wiki/findings/costs.md" in shown and "- [Costs](findings/costs.md)" in shown, shown[:2000]


async def test_the_loop_lists_and_reads_its_workspace_files_itself() -> None:
    chat, model = await _run("react")
    offered = next(r.tools for r in model.requests if r.task == "B" and r.kind == "turn")
    assert {"list_workspace_files", "read_workspace_file"} <= set(offered), offered
    answers = [e.data["answer"] for e in chat.events() if e.type == "x.pipeshub.answer.finalized"]
    assert "Total 412 USD" in answers[-1], answers[-1]
    assert not [r for r in chat.runs if r.approvals], "reading the chat's own workspace never asks"


# --- the port contracts each adapter keeps ---------------------------------------------------------------------

WORKSPACE_STATE = RunState(
    run_id="run-1", slots={WORKSPACE_SLOT: {"text": "This chat's workspace holds 1 file(s):\n- a.md (3 bytes)"}}
)


def _workspace() -> RunCodeWorkspace:
    harness = FakeSyncHarness(TAR)
    return RunCodeWorkspace(store=harness.store, objects=harness.objects, scope=harness.object_scope, workspace_id=SESSION)


class TestWorkspaceViewStep(PreRunStepContract):
    @pytest.fixture
    def pre_run_step(self) -> WorkspaceViewStep:
        return WorkspaceViewStep(_workspace(), HarnessLog.named("tests.workspace_view"))


class TestWorkspaceContextShaper(TurnShaperContract):
    @pytest.fixture
    def shapers(self) -> Fresh[WorkspaceContextShaper]:  # type: ignore[override]
        return Fresh(lambda: WorkspaceContextShaper(PassThroughTurnShaper()), WORKSPACE_STATE)


class TestWorkspaceContextHost(ContextHostContract):
    @pytest.fixture
    def hosts(self) -> Fresh[WorkspaceContextHost]:  # type: ignore[override]
        return Fresh(lambda: WorkspaceContextHost(DefaultContextHost()), WORKSPACE_STATE)
