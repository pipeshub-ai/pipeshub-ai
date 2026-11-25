"""TR-03: a scheduled run's trigger context is staged at ``in/trigger.json`` (/pipeshub/inputs) on its first turn."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import pytest

from harness_core.engines.briefing import TRIGGER_NOTE, BriefedFile, engine_task_prompt
from harness_core.engines.controller import EngineController
from harness_core.engines.durable.inputs import TRIGGER_INPUT_NAME, trigger_input_bytes
from harness_core.engines.types import EngineTask, FollowUp
from harness_core.journal.types import InputArtifactRef
from tests.unit.harness_core.engines.durable.test_inputs import finish, upload, world

if TYPE_CHECKING:
    from harness_core.engines.controller import EngineRunContext, EngineTurnOutcome

RUN = "run-trigger"
TRIGGER: dict[str, Any] = {
    "kind": "connector",
    "scheduleId": "sch-1",
    "firingId": "fir-1",
    "slotAt": "2026-10-30T09:00:00.000Z",
    "window": {"start": "2026-10-30T09:00:00.000Z", "end": "2026-10-30T09:05:00.000Z"},
    "event": {"connectorId": "con-1", "eventType": "newRecord", "at": "2026-10-30T09:01:02.000Z", "recordId": "rec-1"},
}
STAGED = trigger_input_bytes(TRIGGER)


@pytest.fixture
def tasks(monkeypatch: pytest.MonkeyPatch) -> list[EngineTask | FollowUp]:
    seen: list[EngineTask | FollowUp] = []
    original = EngineController.run_turn

    async def recording(self: EngineController, run: EngineRunContext, task: EngineTask | FollowUp) -> EngineTurnOutcome:
        seen.append(task)
        return await original(self, run, task)

    monkeypatch.setattr(EngineController, "run_turn", recording)
    return seen


def test_tr03_the_trigger_file_is_canonical_json() -> None:
    assert STAGED.endswith(b"}\n")
    assert json.loads(STAGED) == TRIGGER
    assert STAGED == trigger_input_bytes(dict(reversed(list(TRIGGER.items()))))
    assert STAGED.decode().splitlines()[1].startswith('  "event"')


async def test_tr03_turn_one_finds_the_trigger_under_in_pinned_and_briefed(tasks: list[EngineTask | FollowUp]) -> None:
    w, adapter = world()
    w.admit(RUN, "say triaged", trigger=TRIGGER)
    assert await finish(w, RUN) == "completed"
    assert adapter.opened == [{"in/trigger.json": STAGED}]
    (started,) = [e.data for e in w.events(RUN) if e.type == "turn.started"]
    (pin,) = started["inputs"]  # type: ignore[misc]
    assert {k: pin[k] for k in ("name", "artifactId", "version", "mimeType", "sizeBytes")} == {  # type: ignore[index]
        "name": TRIGGER_INPUT_NAME, "artifactId": f"trigger:{RUN}", "version": 1, "mimeType": "application/json",
        "sizeBytes": len(STAGED),
    }
    [task] = tasks
    assert isinstance(task, EngineTask)
    assert task.prompt == engine_task_prompt("say triaged", [BriefedFile(TRIGGER_INPUT_NAME, STAGED)])
    assert TRIGGER_NOTE in task.prompt


async def test_tr03_a_redrive_before_the_seed_seeds_the_pinned_trigger_bytes() -> None:
    w, adapter = world()
    w.admit(RUN, "say triaged", trigger=TRIGGER)
    assert await w.crash_at(RUN, "engine.turn.after_acquire")
    assert await finish(w, RUN) == "completed"
    assert adapter.opened == [{"in/trigger.json": STAGED}]
    started = [e for e in w.events(RUN) if e.type == "turn.started"]
    assert len(started) == 1


async def test_tr03_a_run_no_schedule_fired_stages_nothing() -> None:
    w, adapter = world()
    w.admit(RUN, "say plain")
    assert await finish(w, RUN) == "completed"
    assert adapter.opened == [{}]
    (started,) = [e.data for e in w.events(RUN) if e.type == "turn.started"]
    assert "inputs" not in started


async def test_tr03_the_trigger_sits_beside_the_artifacts_the_input_names() -> None:
    w, adapter = world()
    notes = await upload(w, "notes.md", b"draft")
    w.admit(RUN, "say both", inputs=[InputArtifactRef(notes)], trigger=TRIGGER)
    assert await finish(w, RUN) == "completed"
    assert adapter.opened == [{"in/notes.md": b"draft", "in/trigger.json": STAGED}]


async def test_tr03_an_artifact_named_like_the_trigger_file_conflicts_explicitly() -> None:
    w, adapter = world()
    clash = await upload(w, TRIGGER_INPUT_NAME, b"{}")
    w.admit(RUN, "say clash", inputs=[InputArtifactRef(clash)], trigger=TRIGGER)
    disposition = await w.attempt(RUN)
    assert (disposition.status, disposition.reason) == ("failed", "engine_input_conflict")
    assert adapter.opened == []
