"""WP93-STEER and WP93-SPEC (ADR-RUN-01 D5a, D5b): a harness instruction is the harness's, and a hand-off is a
``TaskSpec`` the harness composes.

A recorded research run (root loop e0c9a1a1, child Codex ba5d4d8b): the upfront DEEPROUTE steer reached the
root model as an injected user message (journal seq 11), and the model wrote it into code_task's context as "The only
additional user instruction is to delegate now via code_task once, with the full request and constraints, and call
no other tools this turn." The engine child, which has no code_task, searched its tools for one, found none, and
stopped after 55 s without starting. Now the steer is a ``HarnessInstruction`` for the turn's system channel, and the
child is given the person's request verbatim with the parent's notes, a harness sentence in the notes dropped.
"""

from __future__ import annotations

from typing import Any

from app.harness.assembly import handoff_instructions as instructions
from app.harness.assembly.code_task import (
    CODE_TASK,
    CONTEXT_FIELD,
    CodeTaskDelegation,
    code_task_schema,
)
from app.harness.assembly.deep_route import HANDOFF_SLOT, Handoff, classify
from app.harness.assembly.task_spec import (
    NOTES_HEADING,
    TaskSpec,
    clean_notes,
    is_harness_text,
)
from harness_core.kernel.core.messages import ToolCall
from harness_core.kernel.durable.delegation import Delegated, DelegationView
from harness_core.kernel.durable.ports import TurnResult
from harness_core.logs import HarnessLog
from tests.unit.harness.assembly.test_deeproute import _director, _state

# The person's message, shortened in the middle.
RESEARCH_QUERY = (
    "Do a deep research on how Onyx (onyx.app, github.com/onyx-dot-app/onyx) compares with PipesHub (pipeshub.com, "
    "github.com/pipeshub-ai/pipeshub-ai).\n\nFor both products, check the docs, the GitHub code repo, the website, "
    "Discord (if possible), social media, and any other places.\n\nCite every finding with its source URL. Mark "
    "anything you could not verify.\n\nWrite the final report as one Markdown file named report.md in your output "
    "folder: out/\n"
)
# The root model's code_task arguments (journal seq 14); the context verbatim.
RESEARCH_GOAL = (
    "Conduct the user's full deep research investigation and create ONE final Markdown report in the workspace at "
    "out/report.md. Investigate the actual current public products; reference date Thu Nov 5, 2026 UTC."
)
RESEARCH_CONTEXT = (
    "No prior research, artifacts, files or findings in this conversation. The only additional user instruction is to "
    "delegate now via code_task once, with the full request and constraints, and call no other tools this turn. No "
    "memory request was made. User's requested output is exactly out/report.md."
)
LEAKED = (
    "The only additional user instruction is to delegate now via code_task once, with the full request and "
    "constraints, and call no other tools this turn."
)


class _Log(HarnessLog):
    def __init__(self) -> None:
        self.warnings: list[tuple[str, dict[str, Any]]] = []

    def warning(self, event: str, **fields: Any) -> None:  # type: ignore[override]  # noqa: ANN401
        self.warnings.append((event, fields))


def _decide(delegation: CodeTaskDelegation, arguments: dict[str, Any]) -> str:
    call = ToolCall(id="t1.0", name=CODE_TASK, arguments=arguments)
    delegated = Delegated(invocation_id="t1.0", call=call, turn=1, state="pending")  # type: ignore[arg-type]
    decided = delegation.decide(delegated, DelegationView(batch=(delegated,)))
    return decided.goal  # type: ignore[union-attr]


def test_wp93_steer_run_8_s_upfront_turn_is_a_harness_instruction_not_a_message() -> None:
    """Replay of the recorded turn 1: routed for research, the turn gets one harness instruction and no injected message."""
    route = classify(RESEARCH_QUERY)
    assert route.to == "engine"
    plan = _director(query=RESEARCH_QUERY).before(1, _state([], route=route), [])
    assert plan.inject == (), "nothing reaches the model as a message of the person's turn"
    (instruction,) = plan.harness
    assert instruction == instructions.upfront(route.reason)
    assert instruction.key == instructions.UPFRONT_KEY and not instruction.text.startswith("[PipesHub]")


def test_wp93_steer_every_deeproute_instruction_is_typed_and_worded_in_one_place() -> None:
    assert instructions.escalation("context").text == instructions.ESCALATION_INSTRUCTION.format(
        why="its results no longer fit comfortably"
    )
    assert instructions.engine_failed().text == instructions.ENGINE_FAILED_INSTRUCTION
    assert "“work/1”" in instructions.engine_failed(folder="work/1").text
    assert "3 versions" in instructions.engine_failed(revisions=3).text
    for text in instructions.ALL_TEXTS:
        assert "[PipesHub]" not in text, "the channel's frame names the author"
    # The failed hand-off's briefing goes through the same channel.
    state = _state([(1, 9_000)], tool="code_task", slots={HANDOFF_SLOT: Handoff(1, "escalation", "context").slot()})
    failed = TurnResult(name=CODE_TASK, call_id="c1.0", status="error", content={"success": False})
    plan = _director().before(2, state, [failed])
    assert plan.inject == () and [i.key for i in plan.harness] == [instructions.ENGINE_FAILED_KEY]


def test_wp93_spec_run_8_s_hand_off_gives_the_engine_the_person_s_request_and_drops_the_leaked_steer() -> None:
    log = _Log()
    brief = _decide(
        CodeTaskDelegation(request=RESEARCH_QUERY, deliverable="report.md", log=log), {"goal": RESEARCH_GOAL, CONTEXT_FIELD: RESEARCH_CONTEXT}
    )
    assert brief.startswith(RESEARCH_QUERY.strip()), "the person's request first, as they wrote it"
    assert "The file to deliver: report.md" in brief
    assert f"{NOTES_HEADING}\n{RESEARCH_GOAL}" in brief
    assert "No prior research, artifacts, files or findings in this conversation." in brief
    assert "delegate" not in brief and "call no other tools" not in brief
    ((event, fields),) = log.warnings
    assert event == "code_task.harness_text_dropped" and fields["sentences"] == 1
    assert LEAKED not in str(fields), "the log keeps lengths, not the model's words"


def test_wp93_spec_the_person_s_own_words_are_never_edited() -> None:
    """A request about code_task itself (this repository's developers) reaches the engine whole."""
    request = "Fix the bug where code_task refuses a second delegation in the same message."
    spec = TaskSpec.compose(request=request, goal=request, context="")
    assert spec.render() == request and spec.dropped == ()


def test_wp93_spec_note_sentences_that_are_harness_text() -> None:
    assert is_harness_text(LEAKED)
    assert is_harness_text("Do not call any other tool in this turn.")
    assert is_harness_text("Hand it to the engine now, it is big.")
    # A copied run of the steer's own words, however it was cut.
    assert is_harness_text("PipesHub gives the engine the person's request as they wrote it, so")
    for note in (
        "Cite every finding with its source URL.",
        "The person wants a phase-by-phase plan covering UX and deployment.",
        "Earlier, fetch_url read https://docs.onyx.app/connectors.",
    ):
        assert not is_harness_text(note), note
    kept, dropped = clean_notes(RESEARCH_CONTEXT)
    assert dropped == (LEAKED,) and kept.startswith("No prior research") and kept.endswith("exactly out/report.md.")


def test_wp93_spec_code_task_s_goal_is_the_parent_s_notes_on_the_request() -> None:
    properties = code_task_schema(None).input_schema["properties"]
    assert "as they wrote it" in properties["goal"]["description"]
    assert "PipesHub gives the engine the person's request as they wrote it" in instructions.UPFRONT_INSTRUCTION
    assert "PipesHub gives the engine the person's request as they wrote it" in instructions.ESCALATION_INSTRUCTION


def test_wp93_spec_a_goal_that_repeats_the_request_adds_no_notes() -> None:
    """The director's own hand-off (DR4) carries the goal slot's text, which is the request."""
    assert _decide(CodeTaskDelegation(request=RESEARCH_QUERY, deliverable="report.md"), {"goal": RESEARCH_QUERY}) == (
        RESEARCH_QUERY.strip() + "\n\nThe file to deliver: report.md"
    )
