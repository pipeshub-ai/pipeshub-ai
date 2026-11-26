"""LONGTASK-B3: a /compact still compacts when the run that takes it ends with a clarifying question.

Node hands the person's /compact to the next run it admits and clears it from the session. The kernel compacted
before the run's first model request, after the pre-run steps; a run whose intent step asks the person a
clarifying question ends at the pre-run gate and never reaches that request, so the ask was gone with nothing
compacted and no "Context compacted" row. Found by the S3 resume task ("Good morning. Continue where we left off."
after /compact).
"""

from __future__ import annotations

import json

from tests.support.longtask_rig.brain import LongTaskModel, compile_steps
from tests.support.longtask_rig.rig import LongTaskChat
from tests.support.longtask_rig.scenario import SYSTEM_MARKER, code

CLARIFY = "```clarify\n" + json.dumps(
    {
        "user_intent": "continue an earlier task",
        "questions": [{"question": "Which task should I continue?", "multiSelect": False, "options": [{"label": "The cleanup"}, {"label": "The report"}, {"label": "Something else"}]}],
    }
) + "\n```"
READ = "import os\nprint(sorted(os.listdir(os.environ['WORKSPACE_DIR'])))\n"


def _chat() -> LongTaskChat:
    scripts = {
        "A": ("[A] look around", compile_steps("A", [code("Looked.", READ, delegated=False), code("Looked again.", READ, delegated=False), "say Looked twice."])),
        "B": ("[B] continue", compile_steps("B", ["say Continuing."])),
    }
    model = LongTaskModel(scripts=scripts, system_marker=SYSTEM_MARKER, route="quick", intents={"B": CLARIFY})
    return LongTaskChat(model=model, context_length=128_000, chat_mode="auto")


async def test_a_compact_taken_by_a_run_that_asks_first_still_compacts() -> None:
    chat = _chat()
    with chat.deployment(), chat.watching():
        assert (await chat.ask("A", "[A] look around")).status == "completed"
        asked = await chat.ask("B", "[B] continue", compact="keep the file list")
    events = [e for e in chat.events() if e.run_id == asked.run_id]
    assert any(e.type == "x.pipeshub.clarification.presented" or "clarif" in e.type for e in events) or asked.status != "failed", [
        e.type for e in events
    ]
    rows = [e.data for e in events if e.type == "x.pipeshub.context.compacted"]
    assert rows, f"the person's /compact left no 'Context compacted' row: {[e.type for e in events]}"
    assert rows[0]["trigger"] == "user" and rows[0]["outcome"] == "compacted", rows
    assert any(e.type == "context.compacted" for e in events), "the history before the question was not compacted"
