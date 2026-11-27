"""CHILDREF: a child's long answer reaches its parent, and a commit Node refuses is not retried.

A Codex child finished its turn four times. Each time the kernel's completion carried the child's result inline
(the store inlines up to 16 KiB) and Node's commit route refused it: ``childResult.resultRef.ref`` was capped at
4,096 characters, while every journal event takes the contract's ``ContentRef``. The kernel treated the 400 as a
crash, released the run, and the next attempt ran the whole engine turn again, until the run was quarantined.

* CHILDREF-1/2: a long answer (over 8 KB) through the engine kernel and through the loop driver, each completion
  checked by Node's own commit-route schema.
* CHILDREF-3: a refusal no retry changes fails the run once (``commit_rejected``), without running the turn again.
* CHILDREF-4: a child result a receiver refuses inline is stored sealed and committed again in the same attempt.
* CHILDREF-5: the store never writes a ref longer than the contract's bound.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import pytest

from app.harness.adapters.content_store import (
    INLINE_MAX_BYTES,
    InMemorySealedContent,
    PipesHubContentStore,
)
from harness_core.contracts.generated.common import ContentRef
from harness_core.journal.children import ChildExecutorRequest, ChildGrantRequest
from harness_core.journal.inline import (
    CONTENT_REF_MAX_CHARS,
    INLINE_REF_PREFIX,
    inline_ref,
)
from harness_core.journal.types import CommitRejected, Complete
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
    ScriptedEngineAdapter,
    engine_profile,
)
from harness_core.testing.run_spec import minimal_run_spec
from harness_core.workspace.forks import StoreChildWorkspaces
from tests.unit.harness.node_commits import (
    completion_body,
    node_checked,
    node_verdicts,
    require_node,
)
from tests.unit.harness_core.durable.support import RUN, World, reply, scripted

if TYPE_CHECKING:
    from collections.abc import Callable

    from harness_core.contracts.generated.run_spec import RunSpec
    from harness_core.journal.memory import InMemoryJournal
    from harness_core.journal.types import CommitReceipt, CommitRequest
    from harness_core.testing.durable import ScriptedModelPort

PARENT = "run-parent"
# Past 8 KB and under the store's 16 KiB inline bound: the recorded shape, a result that rides inline.
LONG = " ".join(f"finding-{i:04d}" for i in range(800))


def _loop_parent() -> RunSpec:
    from harness_core.contracts import validate_contract
    from harness_core.contracts.generated.run_spec import RunSpec

    spec = minimal_run_spec(max_children=2).model_dump(mode="json", by_alias=True, exclude_none=True)
    spec["schemaVersion"] = 3
    spec["executor"] = {"kind": "loop"}
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


def _store() -> PipesHubContentStore:
    return PipesHubContentStore(InMemorySealedContent())


def _refusing(journal: InMemoryJournal, refuse: Callable[[CommitRequest], bool]) -> list[str]:
    """Refuse each completion ``refuse`` names as Node's route would; returns the refs it refused."""
    commit = journal.commit
    refused: list[str] = []

    async def checked(request: CommitRequest) -> CommitReceipt:
        transition = request.transition
        if isinstance(transition, Complete) and refuse(request):
            refused.append(transition.child_result.result_ref.ref if transition.child_result else "")
            raise CommitRejected("VALIDATION_ERROR")
        return await commit(request)

    journal.commit = checked  # type: ignore[method-assign]
    return refused


def _inline_result(request: CommitRequest) -> bool:
    result = getattr(request.transition, "child_result", None)
    return result is not None and result.result_ref.ref.startswith(INLINE_REF_PREFIX)


async def _result(store: PipesHubContentStore, data: object) -> dict[str, Any]:
    assert isinstance(data, dict)
    raw = await store.get(ContentRef.model_validate(data["resultRef"]))
    document = json.loads(raw)
    assert isinstance(document, dict)
    return document


# ---- the engine kernel ----


def _engine_world() -> EngineWorld:
    from harness_core.engines.profiles import profile_json

    return EngineWorld(
        approvals={"create": "allow"},
        engine_authority=profile_json(engine_profile(max_steps=40)),
        content=_store(),  # type: ignore[arg-type]
    )


def _engine_parent(world: EngineWorld, port: ScriptedModelPort) -> DurableKernel:
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


async def _engine_child(world: EngineWorld, answer: str) -> str:
    world.journal.admit(PARENT, org_id=ORG, session_id=SESSION, spec=_loop_parent(), input_text="triage the tickets")
    _, port = scripted(reply("", ("code_task", {"goal": f"say {answer}", "task_id": "triage"})), reply("done"))
    claim = await world.journal.claim(PARENT, worker_id="w")
    assert claim is not None
    first = await _engine_parent(world, port).run_claimed(claim)
    assert (first.status, first.reason) == ("suspended", "join")
    (child,) = world.journal.children_of(PARENT)
    return child


def _child_completed(world: EngineWorld) -> dict[str, Any]:
    (completed,) = [e for e in world.journal.events(SESSION) if e.type == "child.completed"]
    return dict(completed.data)


async def test_childref_1_an_engine_childs_long_answer_reaches_its_parent() -> None:
    require_node()
    world = _engine_world()
    child = await _engine_child(world, LONG)
    verdicts = node_checked(world.journal)
    done = await world.attempt(child)
    assert verdicts and all(v["ok"] for v in verdicts), verdicts
    assert done.status == "completed", done
    completed = _child_completed(world)
    assert completed["outcome"] == "answered"
    assert (await _result(world.content, completed))["output"] == LONG  # type: ignore[arg-type]
    assert world.journal.attempts(child) == 1


async def test_childref_3_an_engine_child_refused_for_good_fails_once_without_running_its_turn_again() -> None:
    world = _engine_world()
    child = await _engine_child(world, LONG)
    refused = _refusing(world.journal, lambda _request: True)
    done = await world.attempt(child)
    assert (done.status, done.reason) == ("failed", "commit_rejected")
    # Inline, then sealed: the recovery tried once, and the run ended on the second refusal.
    assert [r.split(":", 1)[0] for r in refused] == ["inline", "sealed"]
    assert world.journal.status(child) == "failed"
    adapter = world.adapter
    assert isinstance(adapter, ScriptedEngineAdapter)
    assert len(adapter.restored) == 1, "the engine's turn ran once"
    assert world.journal.attempts(child) == 1
    completed = _child_completed(world)
    assert (completed["outcome"], completed["errorType"]) == ("failed", "commit_rejected")


async def test_childref_4_an_engine_childs_result_refused_inline_is_sealed_and_committed_in_the_same_attempt() -> None:
    world = _engine_world()
    child = await _engine_child(world, LONG)
    refused = _refusing(world.journal, _inline_result)
    done = await world.attempt(child)
    assert done.status == "completed", done
    assert len(refused) == 1 and refused[0].startswith(INLINE_REF_PREFIX)
    completed = _child_completed(world)
    assert completed["outcome"] == "answered"
    assert str(completed["resultRef"]["ref"]).startswith("sealed:")
    assert (await _result(world.content, completed))["output"] == LONG  # type: ignore[arg-type]
    assert world.journal.attempts(child) == 1


# ---- the loop driver ----


def _loop_world() -> World:
    world = World()
    world.content = _store()  # type: ignore[assignment]
    world.admit(spec=minimal_run_spec(max_children=2))
    return world


def _loop_parent_kernel(world: World, port: ScriptedModelPort) -> DurableKernel:
    delegation = NamedDelegation({"delegate": DelegatingTool(goal_tool("delegate", "Hand one task to a sub-agent"))})
    return world.kernel(port, children=world.journal, delegation=delegation)


async def _loop_child(world: World) -> str:
    _, port = scripted(reply("", ("delegate", {"goal": "summarize the tickets", "task_id": "a"})), reply("done"))
    first = await world.run(_loop_parent_kernel(world, port))
    assert (first.status, first.reason) == ("suspended", "join")
    (child,) = world.journal.children_of(RUN)
    return child


def _loop_child_completed(world: World) -> dict[str, Any]:
    (completed,) = world.events("child.completed")
    return dict(completed.data)


async def test_childref_2_a_loop_sub_agents_long_answer_reaches_its_parent() -> None:
    require_node()
    world = _loop_world()
    child = await _loop_child(world)
    verdicts = node_checked(world.journal)
    _, port = scripted(reply(LONG))
    done = await world.run(world.kernel(port), child)
    assert verdicts and all(v["ok"] for v in verdicts), verdicts
    assert done.status == "completed", done
    completed = _loop_child_completed(world)
    assert completed["outcome"] == "answered"
    assert (await _result(world.content, completed))["output"] == LONG  # type: ignore[arg-type]


async def test_childref_3_a_loop_child_refused_for_good_fails_once_with_the_reason() -> None:
    world = _loop_world()
    child = await _loop_child(world)
    refused = _refusing(world.journal, lambda _request: True)
    _, port = scripted(reply(LONG), reply(LONG))
    done = await world.run(world.kernel(port), child)
    assert (done.status, done.reason) == ("failed", "commit_rejected")
    assert [r.split(":", 1)[0] for r in refused] == ["inline", "sealed"]
    assert len(port.calls) == 1, "the model was asked once"
    assert world.journal.status(child) == "failed"
    completed = _loop_child_completed(world)
    assert (completed["outcome"], completed["errorType"]) == ("failed", "commit_rejected")


async def test_childref_4_a_loop_childs_result_refused_inline_is_sealed_and_committed_in_the_same_attempt() -> None:
    world = _loop_world()
    child = await _loop_child(world)
    refused = _refusing(world.journal, _inline_result)
    _, port = scripted(reply(LONG))
    done = await world.run(world.kernel(port), child)
    assert done.status == "completed", done
    assert len(refused) == 1
    completed = _loop_child_completed(world)
    assert str(completed["resultRef"]["ref"]).startswith("sealed:")
    assert (await _result(world.content, completed))["output"] == LONG  # type: ignore[arg-type]
    assert world.journal.attempts(child) == 1


# ---- the store against every receiver ----


async def test_childref_5_the_store_never_writes_a_ref_a_receiver_refuses() -> None:
    store = _store()
    largest = await store.put(b"x" * INLINE_MAX_BYTES)
    assert largest.ref.startswith(INLINE_REF_PREFIX)
    assert len(largest.ref) <= CONTENT_REF_MAX_CHARS
    over = await store.put(b"x" * (INLINE_MAX_BYTES + 1))
    assert over.ref.startswith("sealed:")
    # A store told to inline more than any ref carries seals what would not fit instead.
    generous = PipesHubContentStore(InMemorySealedContent(), inline_max_bytes=CONTENT_REF_MAX_CHARS)
    assert (await generous.put(b"x" * CONTENT_REF_MAX_CHARS)).ref.startswith("sealed:")
    with pytest.raises(ValueError, match="at most"):
        _ = ContentRef(ref="inline:" + "A" * CONTENT_REF_MAX_CHARS, hash="sha256:" + "0" * 64, size=1)


async def test_childref_5_nodes_commit_route_takes_the_largest_child_result_the_store_inlines() -> None:
    require_node()
    store = _store()
    data = b"x" * INLINE_MAX_BYTES
    largest = await store.put(data)
    assert largest == inline_ref(data)
    from harness_core.journal.types import AttemptToken, ChildResultRef, CommitRequest

    request = CommitRequest(
        "c-1",
        AttemptToken("run-1", "a-1", 1),
        (),
        Complete("answered", child_result=ChildResultRef(result_ref=largest, labels={"integrity": "internal"})),
    )
    over = {**completion_body(request)}
    over["transition"] = {
        **over["transition"],
        "childResult": {
            "resultRef": {"ref": "inline:" + "A" * CONTENT_REF_MAX_CHARS, "hash": largest.hash, "size": 1},
            "labels": {"integrity": "internal"},
        },
    }
    took, refused = node_verdicts([completion_body(request), over])
    assert took == {"ok": True}
    assert not refused["ok"] and "resultRef" in refused["error"]
