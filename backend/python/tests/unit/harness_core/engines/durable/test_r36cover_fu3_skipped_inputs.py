"""R36COVER-FU-3: an input artifact a job refuses on its own (over the per-input cap, or gone since its parent handed
it over) no longer fails the whole job with a line that names no file. The job goes on with the inputs it could take;
its first ``turn.started`` pins the skipped ones beside the others (so a re-driven turn says the same), and the engine
is told in its first task which file it could not use and why, so its answer can say so (the answer contract).

A Node that does not keep the list (``skipped_inputs`` not accepted) sees the refusal fail the run, as before.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from harness_core.engines.briefing import BriefedFile, engine_task_prompt, skipped_note
from harness_core.engines.controller import EngineController
from harness_core.engines.durable.inputs import (
    SkippedInput,
    skip_data,
    skipped_input,
    skipped_of,
)
from harness_core.engines.errors import EngineInputTooLarge, EngineInputUnavailable
from harness_core.engines.types import EngineTask, FollowUp
from harness_core.journal.types import SKIPPED_INPUTS, InputArtifactRef
from harness_core.testing.engines.durable import ORG, SESSION, engine_run_spec
from tests.unit.harness_core.engines.durable.test_inputs import finish, upload, world

if TYPE_CHECKING:
    from harness_core.engines.controller import EngineRunContext, EngineTurnOutcome

SALES = b"region,amount\nnorth,1\n"
BIG = b"x" * 4096
TOO_LARGE = "it is too large to give to a task: 4.0 KB, and the limit is 1.0 KB"


@pytest.fixture
def tasks(monkeypatch: pytest.MonkeyPatch) -> list[EngineTask | FollowUp]:
    seen: list[EngineTask | FollowUp] = []
    original = EngineController.run_turn

    async def recording(self: EngineController, run: EngineRunContext, task: EngineTask | FollowUp) -> EngineTurnOutcome:
        seen.append(task)
        return await original(self, run, task)

    monkeypatch.setattr(EngineController, "run_turn", recording)
    return seen


async def test_r36fu3_1_a_too_large_input_is_skipped_named_and_the_job_goes_on(tasks: list[EngineTask | FollowUp]) -> None:
    w, adapter = world()
    w.input_max_bytes = 1024
    sales = await upload(w, "sales.csv", SALES)
    plan = await upload(w, "plan.csv", BIG)
    w.admit("run-s1", "say read", inputs=[InputArtifactRef(sales), InputArtifactRef(plan)])
    assert await finish(w, "run-s1") == "completed"
    # the input it could take is there; the other is not, and nothing else failed
    assert adapter.opened == [{"in/sales.csv": SALES}]
    (started,) = [e.data for e in w.events("run-s1") if e.type == "turn.started"]
    assert [p["name"] for p in started["inputs"]] == ["sales.csv"]  # type: ignore[index, union-attr]
    assert started["skippedInputs"] == [
        {"artifactId": plan, "reason": "too_large", "words": TOO_LARGE, "name": "plan.csv"},
    ]
    # the engine is told which file it could not use and why, and to say so
    [task] = tasks
    assert isinstance(task, EngineTask)
    assert task.prompt == engine_task_prompt(
        "say read", [BriefedFile("sales.csv", SALES)], skipped=[("plan.csv", TOO_LARGE)]
    )
    assert "- plan.csv: it is too large to give to a task" in task.prompt
    assert "say plainly in your answer which file you could not use and why" in task.prompt


async def test_r36fu3_2_an_input_gone_since_the_handover_is_skipped_unnamed_and_the_job_goes_on(
    tasks: list[EngineTask | FollowUp],
) -> None:
    w, _ = world()
    sales = await upload(w, "sales.csv", SALES)
    gone = await upload(w, "notes.md", b"n")
    assert w.unreadable is not None
    w.unreadable.add(gone)
    w.admit("run-s2", "say read", inputs=[InputArtifactRef(gone), InputArtifactRef(sales)])
    assert await finish(w, "run-s2") == "completed"
    (started,) = [e.data for e in w.events("run-s2") if e.type == "turn.started"]
    (skipped,) = started["skippedInputs"]  # type: ignore[misc]
    # the reference source may not say a refused artifact's name
    assert skipped == {"artifactId": gone, "reason": "unavailable", "words": skipped["words"]}  # type: ignore[index]
    assert "no longer available" in str(skipped["words"])  # type: ignore[index]
    assert "- one of the files handed to it: it is no longer available" in tasks[0].prompt


async def test_r36fu3_3_a_redriven_first_turn_is_told_the_same_skipped_file(tasks: list[EngineTask | FollowUp]) -> None:
    w, _ = world()
    w.input_max_bytes = 1024
    plan = await upload(w, "plan.csv", BIG)
    w.admit("run-s3", "say read", inputs=[InputArtifactRef(plan)])
    assert await w.crash_at("run-s3", "engine.turn.after_acquire")
    w.input_max_bytes = 1 << 20  # resolving anew would take it now: the pin decides
    assert await finish(w, "run-s3") == "completed"
    assert [t.prompt.count("- plan.csv: it is too large") for t in tasks] == [1] * len(tasks)
    assert len([e for e in w.events("run-s3") if e.type == "turn.started"]) == 1


async def test_r36fu3_4_a_node_without_the_list_fails_the_job_as_before() -> None:
    w, _ = world(journal_accepts=frozenset())
    w.input_max_bytes = 1024
    plan = await upload(w, "plan.csv", BIG)
    w.admit("run-s4", "say read", inputs=[InputArtifactRef(plan)])
    disposition = await w.attempt("run-s4")
    assert (disposition.status, disposition.reason) == ("failed", "engine_input_too_large")
    assert "turn.started" not in w.types("run-s4")
    # and a Node that names it gets the list
    w2, _ = world(journal_accepts=frozenset({SKIPPED_INPUTS}))
    w2.input_max_bytes = 1024
    plan2 = await upload(w2, "plan.csv", BIG)
    w2.admit("run-s4b", "say read", inputs=[InputArtifactRef(plan2)])
    assert await finish(w2, "run-s4b") == "completed"


def test_r36fu3_5_skipped_inputs_pin_and_read_back() -> None:
    big = skipped_of("art-1", EngineInputTooLarge("plan.csv", 140 * 1024 * 1024, 100 * 1024 * 1024))
    assert big == SkippedInput(
        "art-1", "too_large", "it is too large to give to a task: 140.0 MB, and the limit is 100.0 MB", "plan.csv"
    )
    assert skipped_input(skip_data(big)) == big
    gone = skipped_of("art-2", EngineInputUnavailable("an input artifact is not available to this run", name="deck.pptx"))
    assert (gone.reason, gone.name) == ("unavailable", "deck.pptx")
    assert skipped_input(skip_data(gone)) == gone
    assert skipped_note([]) == ""


async def test_r46fix_2_a_job_on_its_own_stream_takes_the_conversations_files_and_skips_only_the_big_one(
    tasks: list[EngineTask | FollowUp],
) -> None:
    """R46FIX-2 (MR-16): a job runs on a stream of its own, and its inputs were resolved under that stream, so every
    file of the conversation was "unavailable" and none could be named. They are the conversation's: the small one is
    given, only the one over the cap is skipped, as too large, by its name."""
    w, adapter = world()
    w.input_max_bytes = 1024
    sales = await upload(w, "sales.csv", SALES)
    plan = await upload(w, "plan.csv", BIG)
    w.journal.admit(
        "run-child", org_id=ORG, session_id=SESSION, stream_id="child-stream-of-run-child",
        spec=engine_run_spec(tools=w.tools), input_text="say read", principal_ref="p_user",
        input_artifacts=[InputArtifactRef(plan), InputArtifactRef(sales)],
    )
    assert await finish(w, "run-child") == "completed"
    assert adapter.opened == [{"in/sales.csv": SALES}]
    (started,) = [e.data for e in w.events("run-child", "child-stream-of-run-child") if e.type == "turn.started"]
    assert [p["name"] for p in started["inputs"]] == ["sales.csv"]  # type: ignore[index, union-attr]
    assert started["skippedInputs"] == [
        {"artifactId": plan, "reason": "too_large", "words": TOO_LARGE, "name": "plan.csv"},
    ]
