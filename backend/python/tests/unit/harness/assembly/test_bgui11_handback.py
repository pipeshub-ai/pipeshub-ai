"""BGUI-11: the engine's result, handed back to the chat model, says the person needs the outcome, not the process.

Found in a manual test (a PDF report): the chat answer repeated the engine's process talk ("the PDF's layout check passed
(`layout: clean`), and I visually inspected the rendered pages"), because the parent was told to give the engine's
answer "in full". The code_task result now carries a note beside the engine's words: whose words they are, and that
the person is told what it found and made. A partial or blocked result keeps its own instruction (SPENDCONT).
"""

from __future__ import annotations

from typing import Any

from app.harness.assembly.code_task import (
    CODE_TASK,
    CODE_TASK_GUIDANCE,
    PRESENT_KEY,
    PRESENT_NOTE,
)
from harness_core.kernel.core.messages import ToolCall

# The C6 world (its deployment, artifact registry and host sandbox) is this file's too.
from tests.unit.harness.assembly.test_ctl6_code_task import _build, _deployment, _spec  # noqa: F401

PROCESS = "The PDF's layout check passed (`layout: clean`), and I visually inspected the rendered pages."


async def _shown(document: dict[str, Any]) -> dict[str, Any]:
    runtime = await _build(_spec())
    try:
        call = ToolCall(id="c1", name=CODE_TASK, arguments={"goal": "make the report"})
        outcome = await runtime.delegation.outcome(call, document)
        return dict(outcome.content)  # type: ignore[arg-type]
    finally:
        await runtime.close()


def test_bgui11_the_guidance_asks_for_what_it_found_and_made_not_its_answer_in_full() -> None:
    assert "in full" not in CODE_TASK_GUIDANCE
    assert "what it found and made" in CODE_TASK_GUIDANCE and "keeping its citations" in CODE_TASK_GUIDANCE
    assert "Leave out how it worked" in PRESENT_NOTE and "files it made" in PRESENT_NOTE


async def test_bgui11_a_finished_result_carries_the_note_beside_the_engines_words() -> None:
    shown = await _shown({"output": f"The report is ready. {PROCESS}", "success": True})
    assert shown[PRESENT_KEY] == PRESENT_NOTE
    assert PROCESS in shown["output"], "the engine's words are handed back unchanged; the note frames them"


async def test_bgui11_a_partial_a_blocked_or_a_failed_result_keeps_its_own_instruction() -> None:
    partial = await _shown({"output": "Half done.", "success": True, "truncatedBy": "budget", "stopped_early": "x"})
    blocked = await _shown({"output": "Needs a key.", "success": True, "needs_input": "x"})
    failed = await _shown({"output": "Broke.", "success": False})
    empty = await _shown({"output": "  ", "success": True})
    assert all(PRESENT_KEY not in shown for shown in (partial, blocked, failed, empty))
