"""SPENDCONT, scripted and durable: a recorded cut-short ``code_task`` replayed on the real harness, and its growth.

A manual test asked for a file of every security bug in the PP project. The router kept the request in the loop
(``chat_sized``) and the loop's model called ``code_task`` itself. The Codex child made 131 Jira calls, its budget
stopped it, and its wrap-up wrote an 8.3 KB partial inventory (``truncatedBy: budget``). The parent's model then called
``code_task`` again (refused: one per message) and answered "the requested file isn't ready yet": the partial never
reached the person and no file was saved.

The rig is DEEPROUTE's and ENGBUDGET's (``test_engbudget_replay``): the real ``PipesHubRuntimeAssembler``, ``RunDriver``
and durable kernel, the ``code_task`` child on the engine kernel with its model calls charged as the gateway charges
them; counted in full (the recorded ledger) the child is stopped and its wrap-up answers. The parent's model here
behaves as the recorded one did. Now the child's partial answer is the parent's answer, flagged and labelled partial,
and the file the person asked for is saved, partial, in the conversation's workspace for "continue" to pick up.

The second half measures what bounding an engine's MCP results does to its context, over the recorded result sizes.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import pytest

from harness_core.engines.durable.handoff import HANDOFF_PATH
from harness_core.engines.profiles import profile_json
from harness_core.gateway.result_cap import ENGINE_RESULT_CAP_CHARS
from harness_core.kernel.core.messages import AssistantMessage, TextPart, ToolCall
from harness_core.kernel.core.responses import StopReason, TokenUsage
from harness_core.kernel.durable.ports import SampledTurn
from harness_core.testing.engines.durable import EngineWorld, engine_profile
from harness_core.workspace.forks import session_scope, session_workspace_id
from harness_core.workspace.types import ObjectScope
from tests.support.longtask_rig.deeproute import (
    PARENT,
    Rig,
    _code_task_result,
    deployment,
)
from tests.unit.harness.assembly import world as w
from tests.unit.harness.engines.test_engbudget_replay import (
    WRAP_UP_ANSWER,
    MeteredEngine,
    Tree,
    _full_weight,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Iterator

    from harness_core.kernel.durable.ports import DeltaKind, ModelCall

REQUEST = (
    "gather all the security bugs in the pp project and make a file listing every one of them, "
    "so a coding agent can fix them from it"
)
FILE = "pp-security-bugs.md"
SEARCHES = 22
NOT_READY = "I didn't get to compile or verify the full ticket list, so the requested file isn't ready yet."


@pytest.fixture(autouse=True)
def _deployment() -> Iterator[None]:
    """The deployment the rig's parent runs in."""
    with deployment():
        yield


@dataclass
class RecordedSecurityModel:
    """The recorded parent model: it hands the request to code_task at once, and, given a cut-short result, asks for a
    second code_task and then answers that the file is not ready, as the recorded one did."""

    calls: list[ModelCall] = field(default_factory=list["ModelCall"])
    code_tasks: int = 0

    async def sample(self, call: ModelCall, on_delta: Callable[[DeltaKind, str], Awaitable[None]]) -> SampledTurn:
        self.calls.append(call)
        usage = TokenUsage(input_tokens=9_000, output_tokens=200)
        result = _code_task_result(call)
        if result is None or self.code_tasks < 2 and result.get("truncatedBy") is not None:
            self.code_tasks += 1
            searches = "\n".join(f'tool lookup {{"jql": "project = PP AND text ~ security page {n}"}}' for n in range(SEARCHES))
            goal = f"Gather every PP security bug and make the file {FILE}.\n{searches}\nsay done"
            call_ = ToolCall(id=f"ct{self.code_tasks}", name="code_task", arguments={"goal": goal, "task_id": "pp-sec"})
            message = AssistantMessage(content=[], tool_calls=[call_])
            return SampledTurn(message=message, usage=usage, stop_reason=StopReason.TOOL_USE)
        await on_delta("text_delta", NOT_READY)
        return SampledTurn(message=AssistantMessage(content=[TextPart(text=NOT_READY)]), usage=usage)

    def restore(self, raw: bytes, *, provider: str, transport: str) -> AssistantMessage | None:
        return None


async def _replay(*, auto_route: bool) -> tuple[Rig, RecordedSecurityModel, Any]:
    tree = Tree(_full_weight)
    await tree.open()
    world = EngineWorld(
        engine_authority=profile_json(engine_profile(max_steps=60)), adapter=MeteredEngine(tree), wrap_up=tree.wrap_up
    )
    model = RecordedSecurityModel()
    rig = Rig(model, world=world)  # type: ignore[arg-type]
    done = await rig.drive(REQUEST, auto_route=auto_route)
    return rig, model, done


async def _workspace_file(rig: Rig, path: str) -> str | None:
    head = await rig.world.store.head(ObjectScope(w.ORG), session_workspace_id("ses-1"))
    entry = head.files.get(path)
    if entry is None:
        return None
    return (await rig.world.objects.get(session_scope(w.ORG, "ses-1"), entry.content)).decode("utf-8")


@pytest.mark.parametrize("auto_route", [True, False], ids=["routing-on", "routing-off"])
async def test_spendcont_the_cut_short_child_s_partial_reaches_the_person_and_its_file_is_saved(
    auto_route: bool,
) -> None:
    rig, model, done = await _replay(auto_route=auto_route)
    assert done.status == "completed", done
    (answer,) = [str(e.data.get("answer")) for e in rig.parent("x.pipeshub.answer.finalized")]
    # Before: "… the requested file isn't ready yet." and nothing else.
    assert WRAP_UP_ANSWER in answer and NOT_READY not in answer
    # ADR-RUN-01 D1 / WP-89: the outcome is typed; a chat words it, and a surface with no UI appends the English
    # closing it carries. Either way the person learns the result is partial, where it is saved and how to go on.
    settled = [e.data for e in rig.parent("x.pipeshub.outcome.settled")]
    assert settled and settled[-1]["outcome"]["kind"] == "partial" and settled[-1]["outcome"]["action"] == "continue"
    worded = answer + "".join(str((s.get("presented") or {}).get("closing") or "") for s in settled)
    assert "This result is partial." in worded and f"Saved so far: **{FILE}**" in worded and '"continue"' in worded
    assert [e.data.get("truncatedBy") for e in rig.parent("run.completed")] == ["budget"]
    # The parent's wrap-up was not spent on a second, refused code_task.
    assert model.code_tasks == 1 and len(rig.world.journal.children_of(PARENT)) == 1
    # The child's fork merged into the conversation's workspace: the partial file and the handoff "continue" reads.
    saved = await _workspace_file(rig, f"out/{FILE}")
    assert saved is not None and saved.startswith("> **Partial**") and WRAP_UP_ANSWER in saved
    handoff = json.loads(await _workspace_file(rig, HANDOFF_PATH) or "{}")
    assert handoff["deliverable"] == f"out/{FILE}" and len(handoff["done"]) >= SEARCHES - 4


# ---- context growth: the recorded first child, its tool results in call order (journal result sizes, bytes) ----

# ("J": a JQL search, "G": one issue), as ``tool.outcome.resultRef.size`` recorded them for run 5cdbece8.
RECORDED_RESULTS: tuple[int, ...] = (
    157349, 26703, 121029, 459, 87373, 98387, 40275, 6767, 13382, 87373, 3442, 13382, 3426, 28995, 6900, 213929,
    23585, 91086, 96422, 112734, 6900, 16151, 16641, 14309, 14998, 14384, 14374, 13740, 12905, 212157, 72693, 50442,
    6767, 79013, 100615, 14299, 14384, 14998, 16641, 14002, 13967, 9490, 152220, 73, 11160, 11879, 11880, 11964, 11907,
    13740, 87155, 20614, 170180, 213857, 113010, 91086, 55417, 76101, 184268, 4099, 5155, 14002, 55482, 24903, 11747,
    72693, 3670, 3710, 3640, 3656, 3668, 3575, 3279, 94785, 30521, 24903, 6311, 5857, 4015, 4704, 3857, 3424, 3340,
    3304, 3336, 3255, 3326, 73280, 3485, 3405, 3127, 3389, 4148, 3287, 50751, 6810, 3400, 3080, 3180, 3503, 9543, 13404,
    12649, 3677, 3678, 2750, 1627, 1628, 1712, 1655, 2703, 4090, 4005, 4657, 11965, 16584, 12523, 1554, 77873, 3775,
    3610, 3531, 3670, 3292, 3424, 3320, 3419, 52671, 30813, 22523, 26043,
)
MODEL_CALLS = 30
WINDOW = 272_000
PREFIX = 8_000
SUMMARY = 8_000
CHARS_PER_TOKEN = 3
HINT = 400


@dataclass(frozen=True)
class Growth:
    input_tokens: int
    largest_request: int
    compactions: int


def replay(cap: int | None) -> Growth:
    """Recorded: 30 model calls over its 131 results, each request holding every result so far (the engine keeps them),
    compacting to a summary at 90% of the window as Codex does."""
    context, total, largest, compactions, done = PREFIX, 0, 0, 0, 0
    for call in range(1, MODEL_CALLS + 1):
        upto = round(len(RECORDED_RESULTS) * call / MODEL_CALLS)
        for size in RECORDED_RESULTS[done:upto]:
            tokens = (size if cap is None else min(size, cap + HINT)) // CHARS_PER_TOKEN
            if context + tokens > 0.9 * WINDOW:
                compactions += 1
                total += context
                context = PREFIX + SUMMARY
            context += tokens
        done = upto
        total += context
        largest = max(largest, context)
    return Growth(total, largest, compactions)


def test_spendcont_bounded_results_shrink_the_engine_context() -> None:
    before, after = replay(None), replay(ENGINE_RESULT_CAP_CHARS)
    # Recorded: resent 4.1 MB of results; bounded, the replay reads a fifth less and compacts a third as often, so less
    # of what the engine fetched is summarised away.
    assert sum(RECORDED_RESULTS) == 4_134_809
    assert after.input_tokens <= 0.8 * before.input_tokens, (before, after)
    assert after.compactions * 2 <= before.compactions, (before, after)
