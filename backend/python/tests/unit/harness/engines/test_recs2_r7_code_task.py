"""recs2 item 3 (R7 end to end): a deep-mode root run hands a coding subtask to an engine session and gets it back.

The parent is the real ``PipesHubRuntimeAssembler`` in deep mode (the orchestrator played by a scripted model),
offered ``code_task`` by the org's engine authority; the child is an engine run on the engine kernel, on a fork of the
session workspace. The child's answer and files come back to the parent on the join; the child's own journal is on
its own stream, where Node projects it beside the parent's. The docker case runs the child on the real opencode
binary (``RealChild``); the in-process case on ``EngineWorld``'s scripted engine, the same rig otherwise.
"""

from __future__ import annotations

import json
from collections import Counter
from typing import TYPE_CHECKING, Any

import pytest

from app.harness.adapters.content_store import PipesHubContentStore
from harness_core.engines.durable.kernel import ENGINE
from harness_core.engines.profiles import profile_json
from harness_core.testing.engines.durable import EngineWorld, engine_profile
from harness_core.workspace.types import ObjectScope
from tests.unit.harness.assembly import world as w
from tests.unit.harness.engines.r7_rig import (
    PARENT,
    R7Rig,
    engine_authority,
    orchestrator,
)
from tests.unit.harness.engines.real_engine_world import RealEngineAssembler

if TYPE_CHECKING:
    from collections.abc import Iterator

    from app.harness.sandbox.docker_broker import DockerSandboxBroker
    from tests.unit.harness.engines.real_stack import RealStack

GOAL = "write report.md quarterly numbers\nsay child done"
ANSWER = "The report is ready in report.md."


@pytest.fixture(autouse=True)
def _deployment(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("PIPESHUB_ENABLE_LAZY_TOOLS", "false")
    monkeypatch.setenv("PIPESHUB_ENABLE_FINAL_ANSWER", "false")
    with w.deployment():
        yield


async def drive(rig: R7Rig) -> dict[str, Any]:
    """Parent to the join, the child to its end, the parent to its answer; what each left."""
    rig.admit("Write the quarterly report.")
    first = await rig.attempt(PARENT)
    assert (first.status, first.reason) == ("suspended", "join"), first
    child = rig.child()
    child_events = rig.events(child)
    pinned = next(e for e in child_events if e.type == "run.spec_pinned").data["spec"]
    assert isinstance(pinned, dict)
    assert pinned["executor"]["kind"] == ENGINE  # type: ignore[index]
    done_child = await rig.attempt(child)
    assert done_child.status == "completed", done_child
    done = await rig.attempt(PARENT)
    assert done.status == "completed", done
    return {"child": child, "pinned": pinned}


def _assert_parent_got_it_back(rig: R7Rig, child: str) -> None:
    session = rig.events()
    completed = next(e for e in session if e.type == "child.completed")
    assert completed.data["outcome"] == "answered"
    # The parent's tool result is the child's answer (the result document the join reads).
    outcomes = [e for e in session if e.type == "tool.outcome" and e.run_id == PARENT]
    assert outcomes, "the parent journaled the code_task's outcome"
    answers = [e for e in session if e.type == "x.pipeshub.answer.finalized" and e.run_id == PARENT]
    assert answers and ANSWER in str(answers[-1].data.get("answer")), answers
    # The child's journal stays on its own stream: its turn, its engine checkpoint and its workspace commit.
    types = Counter(e.type for e in rig.events(child))
    assert types["turn.started"] >= 1 and types["run.completed"] == 1, types
    assert types["workspace.committed"] >= 1, types


async def test_recs2_r7_a_deep_root_run_gets_a_coding_engines_result_and_files_back() -> None:
    world = EngineWorld(engine_authority=profile_json(engine_profile(max_steps=40)))
    rig = R7Rig(world, orchestrator(GOAL, ANSWER))
    seen = await drive(rig)
    _assert_parent_got_it_back(rig, seen["child"])
    fork = seen["pinned"]["extensions"]["x.pipeshub"]["workspace"]["workspaceId"]
    assert "report.md" in (await world.store.head(ObjectScope(w.ORG), str(fork))).files
    merged = [e.data for e in rig.events() if e.type == "workspace.committed" and e.run_id == PARENT]
    assert merged and "/report.md" in merged[-1]["changed"], merged
    print("R7 child events:", json.dumps(Counter(e.type for e in rig.events(seen["child"]))))


@pytest.mark.docker
@pytest.mark.timeout(900, method="thread")
async def test_recs2_r7_a_deep_root_run_gets_a_real_opencode_sessions_result_and_files_back(
    stack: RealStack, broker: DockerSandboxBroker
) -> None:
    """The same hand-off with the child on the real opencode binary, in the Docker broker's sandbox."""
    world = EngineWorld(engine_authority=engine_authority("opencode"))
    child = RealEngineAssembler(
        world, stack=stack, broker=broker, content=lambda: PipesHubContentStore(world.journal.content),  # type: ignore[attr-defined]
    )
    rig = R7Rig(world, orchestrator(GOAL, ANSWER), engine_assembler=child, broker=broker)
    seen = await drive(rig)
    _assert_parent_got_it_back(rig, seen["child"])
    checkpointed = [e for e in rig.events(seen["child"]) if e.type == "engine.checkpointed"]
    assert {e.data["engine"]["kind"] for e in checkpointed} == {"opencode"}, checkpointed  # type: ignore[index]
    fork = seen["pinned"]["extensions"]["x.pipeshub"]["workspace"]["workspaceId"]
    assert "report.md" in (await world.store.head(ObjectScope(w.ORG), str(fork))).files
    merged = [e.data for e in rig.events() if e.type == "workspace.committed" and e.run_id == PARENT]
    assert merged and "/report.md" in merged[-1]["changed"], merged
    print("R7 real child events:", json.dumps(Counter(e.type for e in rig.events(seen["child"]))))
