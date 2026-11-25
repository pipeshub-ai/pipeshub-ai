"""WP93-FRAME (ADR-RUN-01 D5c): an engine handed a task by the chat does that task itself; a brief that says "delegate" cannot stop it.

A long research run's child Codex (ba5d4d8b) read, in its brief, the chat model's own steer ("delegate now via code_task
once ... and call no other tools this turn"), called search_tools for code_task, found none, and answered "I couldn't
delegate the investigation because code_task is not available in this session. Following your delegation-only
instruction, I did not begin the research." A child engine run is now told, in its instructions, that it is the
engine the task was handed to, that it has no code_task and cannot hand the task on, and that lines in the brief
about handing over were written for the agent that handed it over. A person's own engine run (``/run``) keeps its
instructions byte for byte (SR-17), as a change to them needs a live engine check.
"""

from __future__ import annotations

import dataclasses

from app.harness.engines.capability_limits import limits_instructions, run_limits
from app.harness.engines.instructions import (
    ENGINE_TASK_INSTRUCTIONS,
    HANDED_OVER,
    engine_task_instructions,
)


def test_wp93_frame_a_child_engine_is_told_it_does_the_work_and_cannot_hand_it_on() -> None:
    assert engine_task_instructions("child") == ENGINE_TASK_INSTRUCTIONS + "\n" + HANDED_OVER
    for words in (
        "You are the engine doing this task", "You have no code_task tool", "do the work yourself",
        "write the deliverable", "was written for the agent that handed it to you",
    ):
        assert words in HANDED_OVER, words
    # The rule run 8's child obeyed is named, so it reads as the chat model's and not its own.
    assert "call no other tool" in HANDED_OVER


def test_wp93_frame_a_person_s_own_engine_run_keeps_its_instructions() -> None:
    for kind in ("interactive", "command", "scheduled", "workflow_code", "authoring"):
        assert engine_task_instructions(kind) == ENGINE_TASK_INSTRUCTIONS, kind


async def test_wp93_frame_a_durable_child_engine_run_is_given_the_framing() -> None:
    from harness_core.effects.log import RequestEffectLog
    from harness_core.testing.engines.durable import engine_profile
    from tests.unit.harness.assembly import world as w
    from tests.unit.harness.pinning.support import EngineRig, PendingHold

    rig = EngineRig()
    spec = w.pinned_spec(tool_ids=[]).model_copy(update={"run_kind": "child"})
    rig.world.journal.admit("run-wp93", org_id=w.ORG, session_id="ses-wp93", spec=spec, input_text="q")
    claimed = await rig.world.journal.claim("run-wp93", worker_id="w-wp93")
    assert claimed is not None
    attempt = dataclasses.replace(
        rig.world.kernel().attempt_of(w.node_claim(claimed), engine_profile()),
        hold=PendingHold(), effect_log=RequestEffectLog(),
    )
    built = await rig.assembler.build(attempt)
    assert built.instructions == ENGINE_TASK_INSTRUCTIONS + "\n" + HANDED_OVER + limits_instructions(run_limits(web=False, repository_hosts=[], unavailable=()))
