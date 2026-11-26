"""WP93-OWED: the answer of a run that owes its file says only what exists. Expected to fail until ADR-RUN-01 D1.

A long research run's child Codex gathered nothing (one search_tools, no tool outcome) and ended its turn without report.md.
Its answer still said "Everything it gathered is saved in this conversation's files (notes/handoff.json lists it).
Send "continue" to write report.md from it.": no handoff was saved (a turn that ends normally saved one only when its
wrap-up wrote the file) and there was nothing to write it from. A run that gathered sources and ended without its
file, with no wrap-up, says the same, and no handoff is saved either.

These words are not patched here: ADR-RUN-01 D1's OutcomePresenter (WP-95) owns every word the person reads of a
run's outcome, from the ledger of what exists. The cases stay as strict expected failures until it lands, so the
presenter turns them green and nothing else can.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from harness_core.effects.types import ToolExecution
from harness_core.engines.durable.handoff import HANDOFF_PATH
from harness_core.kernel.core.turn_limit import is_cut_short_fallback
from harness_core.testing.engines.durable import (
    ORG,
    SESSION,
    EngineWorld,
    ScriptedEngineAdapter,
    engine_profile,
    engine_run_spec,
)
from harness_core.testing.engines.scripted_wrap_up import ScriptedWrapUp
from harness_core.workspace.types import ObjectScope

if TYPE_CHECKING:
    from harness_core.effects.types import ToolContext
    from harness_core.kernel.core.messages import ToolCall

RUN = "run-wp93-owed"
REQUEST = (
    "Do a deep research comparing Onyx and PipesHub. Cite every finding with its source URL. "
    "Write the final report as one Markdown file named report.md in your output folder: out/"
)
# Run 8's child's last message (journal seq 15), as the engine ended its turn on it.
RUN8_ENGINE_TEXT = (
    "I couldn't delegate the investigation because code_task is not available in this session. Following your "
    "delegation-only instruction, I did not begin the research or create out/report.md."
)


async def _fetch(call: ToolCall, ctx: ToolContext) -> ToolExecution:
    del ctx
    url = str(call.arguments.get("url"))
    page = {"ok": True, "url": url, "title": "Onyx docs", "blocks": [{"type": "text", "content": f"Fact at {url}."}]}
    return ToolExecution(status="ok", content=json.dumps(page))


async def _answer(world: EngineWorld, script: str) -> str:
    world.admit(RUN, script, spec=engine_run_spec(engine_profile(max_steps=40)))
    assert (await world.attempt(RUN)).status == "completed"
    return await world.plain_answer(RUN)


async def _handoff_saved(world: EngineWorld) -> bool:
    head = await world.store.head(ObjectScope(ORG), f"ws:{SESSION}")
    return HANDOFF_PATH in head.files


async def test_wp93_owed_run_8_a_child_that_gathered_nothing_says_it_stopped_before_it_started() -> None:
    """Replay of run 8's child: no tool outcome, no report.md, a wrap-up with nothing to answer from."""
    wrap_up = ScriptedWrapUp()
    world = EngineWorld(adapter=ScriptedEngineAdapter(), handlers={}, wrap_up=lambda claim: wrap_up)
    answer = await _answer(world, "\n".join([REQUEST, f"say {RUN8_ENGINE_TEXT}"]))
    assert wrap_up.prompts == [], "nothing was gathered to write it from"
    assert answer.startswith("**report.md isn't written yet.**") and is_cut_short_fallback(answer)
    assert "before it started on the work" in answer and "gathered nothing" in answer
    assert HANDOFF_PATH not in answer and "saved" not in answer, answer
    assert "write report.md from it" not in answer
    assert 'Send "continue" to try the task again.' in answer
    assert not await _handoff_saved(world), "there is nothing to continue from"


async def test_wp93_owed_a_child_that_gathered_sources_saves_them_before_it_says_they_are_saved() -> None:
    world = EngineWorld(adapter=ScriptedEngineAdapter(), handlers={"lookup": _fetch}, wrap_up=None)
    reads = [f'tool lookup {json.dumps({"url": f"https://docs.onyx.app/page-{n}"})}' for n in range(2)]
    answer = await _answer(world, "\n".join([REQUEST, *reads, "say done"]))
    assert answer.startswith("**report.md isn't written yet.**")
    assert f"({HANDOFF_PATH} lists it)" in answer
    assert await _handoff_saved(world), "the answer's claim is true: continue can pick it up"
