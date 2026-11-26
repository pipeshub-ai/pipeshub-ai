"""CH-1..CH-4, EK-22 (E1): engine child runs. The ⊆ rule's ``executor`` dimension; a loop parent delegates
to an engine child that works on its own fork, completes with its result, and is merged on join."""

from __future__ import annotations

import json
from typing import Any

import pytest

from harness_core.contracts import validate_contract
from harness_core.contracts.generated.run_spec import RunSpec
from harness_core.engines.durable.kernel import ENGINE
from harness_core.engines.profiles import (
    profile_digest,
    profile_from_wire,
    profile_json,
)
from harness_core.journal.children import (
    ChildExecutorRequest,
    ChildGrantRequest,
    GrantDecision,
    child_executor,
)
from harness_core.kernel.durable import DurableKernel
from harness_core.kernel.durable.delegation import (
    DelegatingTool,
    NamedDelegation,
    goal_tool,
)
from harness_core.testing.durable import FunctionToolPort
from harness_core.testing.engines.durable import (
    ORG,
    SESSION,
    EngineWorld,
    engine_profile,
    engine_run_spec,
)
from harness_core.testing.run_spec import minimal_run_spec
from harness_core.workspace.forks import StoreChildWorkspaces
from harness_core.workspace.types import ObjectScope

from ...durable.support import reply, scripted

PARENT = "run-parent"
PROFILE = profile_json(engine_profile(max_steps=40))


def _loop_parent(*, mounts: bool = True) -> RunSpec:
    spec = minimal_run_spec(max_children=2).model_dump(mode="json", by_alias=True, exclude_none=True)
    spec["schemaVersion"] = 3
    spec["executor"] = {"kind": "loop"}
    if mounts:
        spec["extensions"] = {
            "x.pipeshub": {
                "knowledge": {"apps": [], "kb": [], "strictScope": False},
                "mcp": [],
                "skills": [],
                "mounts": ["/workspace"],
                "via": "web",
            }
        }
    return validate_contract(RunSpec, spec)


def test_ch1_no_executor_asked_is_the_loop() -> None:
    assert child_executor(_loop_parent(), None) == {"kind": "loop"}


def test_ch1_a_loop_parent_without_an_engine_authority_cannot_have_an_engine_child() -> None:
    refused = child_executor(_loop_parent(), ChildExecutorRequest())
    assert isinstance(refused, GrantDecision) and refused.refusal is not None
    assert (refused.refusal["code"], refused.refusal["dimension"]) == ("CHILD_GRANT_TOO_LARGE", "executor")


def test_ch1_an_engine_child_narrows_its_parents_engine_and_is_pinned_with_its_digest() -> None:
    granted = child_executor(engine_run_spec(engine_profile(max_steps=40)), ChildExecutorRequest(profile={"limits": {"maxSteps": 10}}))
    assert isinstance(granted, dict) and granted["kind"] == "engine"
    engine: Any = granted["engine"]
    profile = profile_from_wire(engine["profile"], access=engine_profile().access)
    assert profile.limits.max_steps == 10
    assert engine["profileDigest"] == profile_digest(profile)


def test_ch1_a_child_asking_more_than_its_parents_engine_is_refused() -> None:
    refused = child_executor(engine_run_spec(engine_profile(max_steps=10)), ChildExecutorRequest(profile={"limits": {"maxSteps": 40}}))
    assert isinstance(refused, GrantDecision) and refused.refusal is not None
    assert refused.refusal["dimension"] == "executor"


def test_ch1_a_loop_parent_uses_the_orgs_engine_authority() -> None:
    granted = child_executor(_loop_parent(), ChildExecutorRequest(), PROFILE)
    assert isinstance(granted, dict) and granted["kind"] == "engine"


@pytest.fixture
def world() -> EngineWorld:
    return EngineWorld(approvals={"create": "allow"}, engine_authority=PROFILE)


def _parent_kernel(world: EngineWorld, port: Any) -> DurableKernel:  # noqa: ANN401 - a ScriptedModelPort
    return DurableKernel(
        journal=world.journal,
        content=world.content,
        model=port,
        tools=FunctionToolPort(),
        clock=world.clock,
        children=world.journal,
        delegation=NamedDelegation(
            {
                "code_task": DelegatingTool(
                    goal_tool("code_task", "Hand a hands-on task to a coding engine"),
                    ChildGrantRequest(mounts=("/workspace",)),
                    ChildExecutorRequest(),
                )
            }
        ),
        child_workspaces=StoreChildWorkspaces(lambda _token: (world.store, world.writers)),
    )


async def test_ch3_a_loop_parent_delegates_to_an_engine_child_on_its_fork_merged_on_join(world: EngineWorld) -> None:
    world.journal.admit(PARENT, org_id=ORG, session_id=SESSION, spec=_loop_parent(), input_text="make the report")
    _, port = scripted(
        reply("", ("code_task", {"goal": "write report.md quarterly\nsay child done", "task_id": "report"})),
        reply("the report is ready"),
    )
    claim = await world.journal.claim(PARENT, worker_id="w")
    assert claim is not None
    first = await _parent_kernel(world, port).run_claimed(claim)
    assert (first.status, first.reason) == ("suspended", "join")
    (child,) = world.journal.children_of(PARENT)
    child_events = world.journal.events(child)
    pinned = next(e for e in child_events if e.type == "run.spec_pinned").data["spec"]
    assert isinstance(pinned, dict)
    assert pinned["executor"]["kind"] == ENGINE  # type: ignore[index]
    fork = pinned["extensions"]["x.pipeshub"]["workspace"]["workspaceId"]  # type: ignore[index]

    disposition = await world.attempt(child)
    assert disposition.status == "completed"
    saved = [str(e.data.get("workspaceId")) for e in world.journal.events(child) if e.type == "workspace.committed"]
    assert saved and set(saved) == {str(fork)}  # pyright: ignore[reportUnknownArgumentType]
    assert "report.md" in (await world.store.head(ObjectScope(ORG), str(fork))).files  # pyright: ignore[reportUnknownArgumentType]
    assert "report.md" not in (await world.store.head(ObjectScope(ORG), f"ws:{SESSION}")).files
    completed = next(e for e in world.journal.events(SESSION) if e.type == "child.completed")
    assert completed.data["outcome"] == "answered"

    claim = await world.journal.claim(PARENT, worker_id="w")
    assert claim is not None
    done = await _parent_kernel(world, port).run_claimed(claim)
    assert done.status == "completed"
    result = [m for m in port.calls[-1].messages if type(m).__name__ == "ToolMessage"]
    assert json.loads(result[0].text)["output"] == "child done"  # type: ignore[attr-defined]
    assert "report.md" in (await world.store.head(ObjectScope(ORG), f"ws:{SESSION}")).files
    merged = [
        e.data for e in world.journal.events(SESSION) if e.type == "workspace.committed" and e.run_id == PARENT
    ]
    assert merged and merged[-1]["changed"] == ["/report.md"]


async def test_ch3_an_engine_childs_failure_reaches_its_parent_with_its_reason(world: EngineWorld) -> None:
    from harness_core.testing.engines.durable import ScriptedEngineAdapter

    world.journal.admit(PARENT, org_id=ORG, session_id=SESSION, spec=_loop_parent(), input_text="go")
    _, port = scripted(reply("", ("code_task", {"goal": "say x"})), reply("it failed"))
    claim = await world.journal.claim(PARENT, worker_id="w")
    assert claim is not None
    _ = await _parent_kernel(world, port).run_claimed(claim)
    (child,) = world.journal.children_of(PARENT)
    adapter = world.adapter
    assert isinstance(adapter, ScriptedEngineAdapter)
    adapter.fail_with.append("grant_refused")
    assert (await world.attempt(child)).status == "failed"
    completed = next(e for e in world.journal.events(SESSION) if e.type == "child.completed")
    assert (completed.data["outcome"], completed.data["errorType"]) == ("failed", "grant_refused")
