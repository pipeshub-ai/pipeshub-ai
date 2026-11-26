"""G2C2-B3/B4: a durable engine run's first turn carries the workspace note stage 1's task carries (G2C F1)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from harness_core.contracts.generated.common import ContentRef
from harness_core.engines.briefing import (
    BriefedFile,
    WorkspaceEntry,
    engine_task_prompt,
)
from harness_core.engines.controller import EngineController
from harness_core.engines.types import EngineTask, FollowUp
from harness_core.journal.types import InputArtifactRef
from tests.unit.harness_core.engines.durable.test_inputs import finish, upload, world

if TYPE_CHECKING:
    from harness_core.engines.controller import EngineRunContext, EngineTurnOutcome

SALES = b"region,amount\nnorth,1\nsouth,2\n"


@pytest.fixture
def tasks(monkeypatch: pytest.MonkeyPatch) -> list[EngineTask | FollowUp]:
    seen: list[EngineTask | FollowUp] = []
    original = EngineController.run_turn

    async def recording(self: EngineController, run: EngineRunContext, task: EngineTask | FollowUp) -> EngineTurnOutcome:
        seen.append(task)
        return await original(self, run, task)

    monkeypatch.setattr(EngineController, "run_turn", recording)
    return seen


async def test_g2c2_b3_turn_one_is_briefed_over_its_pinned_inputs(tasks: list[EngineTask | FollowUp]) -> None:
    w, _ = world()
    sales = await upload(w, "sales.csv", SALES)
    w.admit("run-b3", "say read", inputs=[InputArtifactRef(sales)])
    assert await finish(w, "run-b3") == "completed"
    [task] = tasks
    assert isinstance(task, EngineTask)
    assert task.prompt == engine_task_prompt("say read", [BriefedFile("sales.csv", SALES)])
    # The journal keeps what the user wrote; the note is the engine's, not the conversation's.
    [message] = [e for e in w.events("run-b3") if e.type == "message.completed" and e.data.get("role") == "user"]
    assert (await w.content.get(ContentRef.model_validate(message.data["textRef"]))).decode() == "say read"


async def test_g2c2_b3_a_later_run_is_told_what_earlier_work_left(
    tasks: list[EngineTask | FollowUp],
) -> None:
    w, _ = world()
    w.admit("run-1", "write out/report.md first draft\nsay written")
    assert await finish(w, "run-1") == "completed"
    w.admit("run-2", "say again")
    assert await finish(w, "run-2") == "completed"
    first, second = tasks
    assert isinstance(first, EngineTask) and isinstance(second, EngineTask)
    assert "This task has no input files: start the work without listing the folder." in first.prompt
    assert second.prompt == engine_task_prompt("say again", [], [WorkspaceEntry("out/report.md", len(b"first draft"))])


async def test_g2c2_b4_a_redriven_first_turn_is_briefed_again_from_the_pins(tasks: list[EngineTask | FollowUp]) -> None:
    w, _ = world()
    sales = await upload(w, "sales.csv", SALES)
    w.admit("run-b4", "say read", inputs=[InputArtifactRef(sales)])
    assert await w.crash_at("run-b4", "engine.turn.after_materialize")
    assert await finish(w, "run-b4") == "completed"
    briefed = engine_task_prompt("say read", [BriefedFile("sales.csv", SALES)])
    first, again = tasks
    assert isinstance(first, EngineTask) and isinstance(again, EngineTask)
    assert first.prompt == briefed
    assert again.prompt.endswith("\n\n" + briefed) and again.prompt != briefed
