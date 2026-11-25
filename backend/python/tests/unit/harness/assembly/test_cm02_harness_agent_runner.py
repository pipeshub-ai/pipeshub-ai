"""CM-02 (design-plan §5.6): deep mode's ``spawn_agent(role="coder")`` runs on the run's coding engine.

The real ``PipesHubRuntimeAssembler`` in deep mode with an engine authority pinned (``x.pipeshub.codeTask``): a coder
spawn is decided as deep mode decides every spawn (the phase gate, batch validation, the budget share), then asks
for an engine child on a fork of the workspace holding only its share of the tokens; its answer and files come back
as ``code_task``'s do. One coding hand-off runs per message, whichever tool asked. Other roles stay loop children.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from app.agents.agent_loop.loops import orchestrator as legacy
from app.harness.artifacts.events import PRESENTED
from app.harness.assembly.assembler import PipesHubRuntimeAssembler
from app.harness.assembly.code_task import CODER_ROLE, ONE_ENGINE_PER_MESSAGE
from app.harness.assembly.deep import DEEP_PHASE_SLOT, DeepPhase
from app.harness.assembly.harness_agent_runner import CODER_HELP
from harness_core.contracts import validate_contract
from harness_core.contracts.generated.run_spec import RunSpec
from harness_core.journal.children import ChildRequest
from harness_core.kernel.core.messages import ToolCall
from harness_core.kernel.durable.delegation import Delegated, DelegationView, Refuse
from harness_core.testing import ModelScript, Reply, ScriptedModel, Turn
from harness_core.testing.durable import ScriptedModelPort

from . import world as w

if TYPE_CHECKING:
    from collections.abc import Iterator

ROOT = Path(__file__).resolve().parents[6]
PROFILE = json.loads((ROOT / "contracts/harness/vectors/engine-profiles.json").read_text())["release"][0]
APPROVED = {DEEP_PHASE_SLOT: DeepPhase(phase="programmatic", approved=True).slot()}
FILE = {"artifactId": "art-1", "fileName": "report.md", "mimeType": "text/markdown", "sizeBytes": 12}


@pytest.fixture(autouse=True)
def _deployment(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("PIPESHUB_ENABLE_LAZY_TOOLS", "false")
    monkeypatch.setenv("PIPESHUB_ENABLE_FINAL_ANSWER", "false")
    with w.deployment():
        yield


def _spec(*, engine: bool = True) -> RunSpec:
    spec = w.pinned_spec(tool_ids=["jira@" + w.JIRA + "/search_issues"], kb=["kb-1"])
    raw = spec.model_dump(by_alias=True, exclude_none=True)
    raw["budgets"]["maxChildren"] = 16
    if engine:
        raw["extensions"]["x.pipeshub"]["codeTask"] = {"profile": PROFILE}
    return validate_contract(RunSpec, raw)


async def _delegation(*, engine: bool = True) -> Any:  # noqa: ANN401
    port = ScriptedModelPort(ScriptedModel(ModelScript(id="p", turns=[Turn(reply=Reply(text="ok"))])))
    claim = w.claim(_spec(engine=engine), query="fix the failing test", chat_mode="deep")
    runtime = await PipesHubRuntimeAssembler(w.Services(agent=w.agent_doc()), model_factory=w.model_factory(port)).build(
        claim
    )
    assert runtime.delegation is not None
    return runtime.delegation


def _spawn(call_id: str, role: str = CODER_ROLE, **arguments: Any) -> ToolCall:  # noqa: ANN401
    return ToolCall(id=call_id, name="spawn_agent", arguments={"role": role, "goal": "fix tests", **arguments})


def _batch(*calls: ToolCall, slots: dict[str, Any] | None = None, earlier: tuple[Delegated, ...] = ()) -> DelegationView:
    batch = tuple(Delegated(f"t2.{i}", call, 2, "pending") for i, call in enumerate(calls))
    return DelegationView(batch, earlier, APPROVED if slots is None else slots)


async def test_cm02_the_orchestrator_is_told_of_the_coder_role_beside_code_task() -> None:
    schemas = {s.name: s for s in (await _delegation()).schemas()}
    assert {"spawn_agent", "code_task"} <= set(schemas)
    spawn = schemas["spawn_agent"]
    assert spawn.description.endswith(CODER_HELP)
    assert spawn.input_schema["properties"]["role"]["description"].endswith(CODER_HELP)
    without = {s.name: s for s in (await _delegation(engine=False)).schemas()}
    assert "code_task" not in without and CODER_HELP not in without["spawn_agent"].description


async def test_cm02_a_coder_spawn_is_an_engine_child_with_its_share_of_the_tokens() -> None:
    delegation = await _delegation()
    view = _batch(_spawn("c1", task_id="fix"), _spawn("c2", role="researcher", task_id="look"))
    coder, researcher = (delegation.decide(d, view) for d in view.batch)
    assert isinstance(coder, ChildRequest) and isinstance(researcher, ChildRequest)
    assert coder.executor is not None and coder.executor.kind == "engine"
    assert coder.definition is None and coder.task_id == "fix" and coder.goal == "fix tests"
    assert coder.grants.mounts == ("/workspace",) and coder.grants.tools is None
    # CM-03: the engine child holds deep mode's share of the parent's tokens, as its loop siblings do.
    assert coder.grants.max_tokens == researcher.grants.max_tokens and coder.grants.max_tokens is not None
    assert researcher.executor is None and researcher.definition is not None


async def test_cm02_the_phase_gate_and_batch_rules_still_decide_a_coder_spawn() -> None:
    delegation = await _delegation()
    view = _batch(_spawn("c1"), slots={})
    assert delegation.decide(view.batch[0], view) == Refuse(legacy.SPAWN_BLOCKED)
    view = _batch(_spawn("c1", task_id="b", depends_on=["missing"]))
    assert isinstance(delegation.decide(view.batch[0], view), Refuse)


async def test_cm02_one_coding_hand_off_per_message_whichever_tool_asks() -> None:
    delegation = await _delegation()
    view = _batch(_spawn("c1", task_id="a"), _spawn("c2", task_id="b"))
    assert isinstance(delegation.decide(view.batch[0], view), ChildRequest)
    assert delegation.decide(view.batch[1], view) == Refuse(ONE_ENGINE_PER_MESSAGE)
    code_task = Delegated("t1.0", ToolCall(id="k", name="code_task", arguments={"goal": "g"}), 1, "joined")
    after = _batch(_spawn("c3"), earlier=(code_task,))
    assert after.batch and delegation.decide(after.batch[0], after) == Refuse(ONE_ENGINE_PER_MESSAGE)
    spawned = Delegated("t1.0", _spawn("c0", task_id="z"), 1, "joined")
    then = DelegationView((Delegated("t2.0", ToolCall(id="k2", name="code_task", arguments={"goal": "g"}), 2, "pending"),), (spawned,), APPROVED)
    assert delegation.decide(then.batch[0], then) == Refuse(ONE_ENGINE_PER_MESSAGE)
    refused = Delegated("t1.0", _spawn("c0"), 1, "refused")
    retry = _batch(_spawn("c4"), earlier=(refused,))
    assert isinstance(delegation.decide(retry.batch[0], retry), ChildRequest)


async def test_cm02_the_engine_childs_answer_and_files_come_back_as_a_code_tasks_do() -> None:
    delegation = await _delegation()
    document = {"output": "Fixed: the test passes.", "success": True, "files": [FILE]}
    shown = await delegation.outcome(_spawn("c1"), document)
    assert not shown.error
    assert shown.content["output"] == "Fixed: the test passes." and shown.content["files"] == ["report.md"]
    # WP-56: the result's own plain frame first, then the file's card.
    assert [(e.type, e.data.get("artifactId")) for e in shown.events] == [
        ("x.pipeshub.tool.result_presented", None),
        (PRESENTED, "art-1"),
    ]
    assert shown.events[0].data["resultSummary"] == "The coding engine finished the task and saved 1 file."
    planned = await delegation.outcome(_spawn(f"{legacy.PROGRAMMATIC_CALL_PREFIX}s1_x"), document)
    assert planned.content == "Fixed: the test passes.\n\n[FILES] report.md"
    failed = await delegation.outcome(_spawn("c2"), {"output": "", "success": False, "error": "x"})
    assert failed.error


async def test_cm02_without_an_engine_a_coder_is_a_loop_sub_agent() -> None:
    delegation = await _delegation(engine=False)
    view = _batch(_spawn("c1"))
    decided = delegation.decide(view.batch[0], view)
    assert isinstance(decided, ChildRequest) and decided.executor is None
