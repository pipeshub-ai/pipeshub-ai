"""BGUI-11 end to end: the chat loop's scripted model reads the presentation note in its code_task result.

The ENGCITE2 rig: the real chat loop (its model scripted) hands the work to an engine child on the engine kernel, and
the test reads the result document the parent's model was given (``test_bgui11_handback.py`` has the unit cases).
"""

from __future__ import annotations

import json

from app.harness.assembly.code_task import PRESENT_KEY, PRESENT_NOTE

# ENGCITE2's world (its deployment) is this file's too.
from tests.unit.harness.engines.test_engcite2_code_task_citations import (  # noqa: F401
    _deployment,
    _run,
)

PROCESS = "The PDF's layout check passed (`layout: clean`), and I visually inspected the rendered pages."


async def test_bgui11_the_scripted_chat_model_reads_the_note_in_its_code_task_result() -> None:
    """End to end on the real chat loop (its model scripted) and an engine child: the result its model read back."""
    ran = await _run(f"Royal Challengers Bengaluru won IPL 2026. {PROCESS}", "Royal Challengers Bengaluru won IPL 2026.")
    result = json.loads(ran.outcome)["content"]
    assert result[PRESENT_KEY] == PRESENT_NOTE, result
    assert PROCESS in result["output"], "the engine's words are handed back as it wrote them; the note frames them"
