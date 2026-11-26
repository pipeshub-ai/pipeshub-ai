"""C20.53 / G-52 (WP100-LANG-5..7): PipesHub's engine run says the person's language from the run's pinned locale.

One source for every run kind: the run input's ``locale`` (a chat turn's ``Accept-Language``; a child inherits its
parent's; a schedule pins its owner's). The engine assembler gives that line to the kernel, which says it at the
first task's tail; the instructions stay SR-17's. A child's TaskSpec does not repeat it: the child's own run says it.
"""

from __future__ import annotations

import dataclasses

from app.harness.assembly.task_spec import TaskSpec
from app.harness.engines.capability_limits import limits_instructions, run_limits
from app.harness.engines.instructions import (
    ENGINE_TASK_INSTRUCTIONS,
    engine_answer_language,
)
from app.utils.output_language import build_output_language


async def _built(locale: str | None) -> object:
    from harness_core.effects.log import RequestEffectLog
    from harness_core.testing.engines.durable import engine_profile
    from tests.unit.harness.assembly import world as w
    from tests.unit.harness.pinning.support import EngineRig, PendingHold

    rig = EngineRig()
    rig.world.journal.admit(
        "run-lang", org_id=w.ORG, session_id="ses-lang", spec=w.pinned_spec(tool_ids=[]), input_text="q"
    )
    claimed = await rig.world.journal.claim("run-lang", worker_id="w-lang")
    assert claimed is not None
    node = w.node_claim(claimed)
    assert node.run is not None and node.run.input is not None
    run = node.run.model_copy(update={"input": node.run.input.model_copy(update={"locale": locale})})
    attempt = dataclasses.replace(
        rig.world.kernel().attempt_of(dataclasses.replace(node, run=run), engine_profile()),
        hold=PendingHold(), effect_log=RequestEffectLog(),
    )
    return await rig.assembler.build(attempt)


async def test_wp100_lang_5_the_engine_run_gets_the_line_of_its_pinned_locale() -> None:
    built = await _built("de-DE")

    assert built.answer_language == build_output_language("de-DE")  # type: ignore[attr-defined]
    assert "German (de-DE)" in built.answer_language  # type: ignore[attr-defined]
    assert built.instructions == ENGINE_TASK_INSTRUCTIONS + limits_instructions(run_limits(web=False, repository_hosts=[], unavailable=()))  # type: ignore[attr-defined]


async def test_wp100_lang_6_a_run_with_no_locale_gets_none() -> None:
    built = await _built(None)

    assert built.answer_language == ""  # type: ignore[attr-defined]
    assert engine_answer_language(None) == ""


def test_wp100_lang_7_a_childs_task_spec_does_not_repeat_the_language() -> None:
    spec = TaskSpec.compose(request="Schreib den Bericht", goal="Write the report", context=None)

    assert "Answer Language" not in spec.render()
