"""LONGTASK-B1: a react-mode run's code runs through ``coding_agent``, and what it writes to ``$WORKSPACE_DIR`` is kept.

React and auto compose domain agents: ``run_code`` is offered only inside ``coding_agent``, whose child loop calls the
tool without the durable route (QAFIX4 B1 staged and saved the chat's workspace only there). The child's code saw an
empty workspace, its files were dropped at the end of the call, and the tool's description still told it that files
written there are kept. Found by the S1 long-task scenario.
"""

from __future__ import annotations

import json

from tests.support.longtask_rig.brain import LongTaskModel, compile_steps
from tests.support.longtask_rig.rig import LongTaskChat
from tests.support.longtask_rig.scenario import SYSTEM_MARKER, code

NOTES = "# Notes\n\nKept across turns.\n"
WRITE = (
    "import os, pathlib\n"
    "p = pathlib.Path(os.environ['WORKSPACE_DIR']) / 'wiki' / 'notes.md'\n"
    "p.parent.mkdir(parents=True, exist_ok=True)\n"
    f"p.write_text({NOTES!r})\n"
    "pathlib.Path('scratch.txt').write_text('x')\n"
    "print('saved')\n"
)
READ = "import os, pathlib\nprint(pathlib.Path(os.environ['WORKSPACE_DIR'], 'wiki', 'notes.md').read_text())\n"


def _chat() -> tuple[LongTaskChat, LongTaskModel]:
    scripts = {
        "A": ("[A] keep notes", compile_steps("A", [code("Saved the notes.", WRITE), "say saved"])),
        "B": ("[B] read the notes", compile_steps("B", [code("Read the notes.", READ), "say read"])),
    }
    model = LongTaskModel(scripts=scripts, system_marker=SYSTEM_MARKER)
    return LongTaskChat(model=model, context_length=128_000), model


def _delegate_result(model: LongTaskModel, needle: str) -> dict[str, object]:
    """The ``run_code`` result the delegate read, found by what its code printed."""
    for request in model.requests:
        if request.kind != "delegate":
            continue
        for content in request.tool_results.values():
            if needle in content:
                return json.loads(content[content.index("{") : content.rindex("}") + 1])
    raise AssertionError(f"no delegated run_code result printed {needle!r}")


async def test_a_delegated_run_code_saves_the_workspace_and_the_next_turn_reads_it() -> None:
    chat, model = _chat()
    with chat.deployment(), chat.watching():
        first = await chat.ask("A", "[A] keep notes")
        assert first.status == "completed"
        assert await chat.files() == {"wiki/notes.md": NOTES}, "the file coding_agent's code wrote is the workspace's next revision"
        second = await chat.ask("B", "[B] read the notes")
        assert second.status == "completed"
    assert NOTES.strip() in str(_delegate_result(model, "Kept across turns")["stdout"]), (
        "a delegated call in the next turn is given the workspace"
    )


async def test_a_delegated_run_code_is_told_what_it_wrote_outside_the_workspace_is_not_kept() -> None:
    chat, model = _chat()
    with chat.deployment(), chat.watching():
        assert (await chat.ask("A", "[A] keep notes")).status == "completed"
    result = _delegate_result(model, "saved")
    assert result.get("not_kept") == ["scratch.txt"], result
