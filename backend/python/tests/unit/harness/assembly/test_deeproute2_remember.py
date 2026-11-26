"""DEEPROUTE2: the hand-off call keeps what the person asked to have remembered.

A routed turn ends with the engine's answer (DEEPROUTE-3), so the chat's model has one call to keep a "remember for
later" ask: ``code_task`` itself. On a run whose pin grants a memory write scope, ``code_task`` takes ``remember``
(``remember``'s own items) and its outcome proposes them into the run's memory as ``remember`` does; Node settles them
with the turn. Measured live: told to call ``remember`` beside ``code_task``, the model did in 2 of 4 hand-offs.
"""

from __future__ import annotations

import dataclasses
from contextlib import ExitStack
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import pytest

from app.harness.assembly.code_task import CODE_TASK, REMEMBER_FIELD
from app.harness.assembly.deep_route import (
    ESCALATION_INSTRUCTION,
    REMEMBER_IN_HANDOFF,
    UPFRONT_INSTRUCTION,
)
from harness_core.contracts import validate_contract
from harness_core.contracts.generated.run_spec import RunSpec
from harness_core.engines.profiles import profile_json
from harness_core.kernel.core.messages import ToolCall
from harness_core.memories.memory import InMemoryMemorySource
from harness_core.memories.tool import RememberTool
from harness_core.testing.engines.durable import engine_profile
from tests.harness_replay.artifacts import ParityArtifactRegistry
from tests.unit.harness.assembly import world as w

if TYPE_CHECKING:
    from collections.abc import Iterator

BUDGET = "Our budget cap is 2,000 USD per month."
SCOPES = ("session", "user")


@pytest.fixture
def deployment() -> Iterator[None]:
    with ExitStack() as stack:
        stack.enter_context(w.deployment())
        stack.enter_context(
            patch("app.services.artifact_registry.ArtifactRegistryService", new=ParityArtifactRegistry(w.ORG, "ses-1"))
        )
        yield


def _spec(*, memory: bool) -> RunSpec:
    raw = w.pinned_spec(tool_ids=[], sandbox="agent-code").model_dump(by_alias=True, exclude_none=True)
    raw["extensions"]["x.pipeshub"]["codeTask"] = {"profile": profile_json(engine_profile())}
    raw["budgets"]["maxChildren"] = 1
    if memory:
        raw["extensions"]["x.pipeshub"]["memory"] = {"recalled": [], "writeScopes": list(SCOPES)}
    return validate_contract(RunSpec, raw)


async def _runtime(source: InMemoryMemorySource | None) -> Any:  # noqa: ANN401
    from app.harness.assembly.assembler import PipesHubRuntimeAssembler
    from harness_core.testing import ModelScript, Reply, ScriptedModel, Turn
    from harness_core.testing.durable import ScriptedModelPort

    port = ScriptedModelPort(ScriptedModel(ModelScript(id="t", turns=[Turn(reply=Reply(text="ok"))])))
    assembler = PipesHubRuntimeAssembler(w.Services(), model_factory=w.model_factory(port))
    claim = w.claim(_spec(memory=source is not None), query="Add the comparison. Also remember our budget cap.")
    return await assembler.build(dataclasses.replace(claim, memories=source))


def _schema(runtime: Any) -> dict[str, Any]:  # noqa: ANN401
    return next(s for s in runtime.delegation.schemas() if s.name == CODE_TASK).input_schema


def _answered() -> dict[str, Any]:
    return {"success": True, "output": "Added wiki/comparison.md."}


async def test_dr2_6_a_run_that_keeps_memories_hands_off_with_remember(deployment: None) -> None:
    source = InMemoryMemorySource([], write_scopes=SCOPES, run_id="run-1")
    runtime = await _runtime(source)
    try:
        field = _schema(runtime)["properties"][REMEMBER_FIELD]
        memories = RememberTool(source, SCOPES).to_schema().input_schema["properties"]["memories"]
        assert field["items"] == memories["items"]
        assert "do not also call remember" in field["description"]
        assert REMEMBER_FIELD not in _schema(runtime)["required"]
        item = {"content": BUDGET, "kind": "fact", "scope": "user"}
        call = ToolCall(id="c1", name=CODE_TASK, arguments={"goal": "Add the comparison.", REMEMBER_FIELD: [item]})
        shown = await runtime.delegation.outcome(call, _answered())
        (proposal,) = source.proposed.values()
        assert (proposal.content, proposal.kind, proposal.scope) == (BUDGET, "fact", "user")
        assert shown.content[REMEMBER_FIELD]["memories"][0]["status"] == "proposed for review"
        assert shown.content["output"] == "Added wiki/comparison.md." and not shown.error
        # A re-joined call (a re-claimed attempt) proposes nothing twice.
        _ = await runtime.delegation.outcome(call, _answered())
        assert len(source.proposed) == 1
        # The engine failing does not lose what the person asked to keep.
        other = ToolCall(id="c2", name=CODE_TASK, arguments={"goal": "x", REMEMBER_FIELD: [{**item, "content": "Prefers tables."}]})
        failed = await runtime.delegation.outcome(other, {"success": False, "error": "engine_error"})
        assert failed.error and len(source.proposed) == 2
    finally:
        await runtime.close()


async def test_dr2_6_a_bad_item_is_refused_and_nothing_is_kept(deployment: None) -> None:
    source = InMemoryMemorySource([], write_scopes=SCOPES, run_id="run-1")
    runtime = await _runtime(source)
    try:
        call = ToolCall(id="c1", name=CODE_TASK, arguments={"goal": "g", REMEMBER_FIELD: [{"content": BUDGET}]})
        shown = await runtime.delegation.outcome(call, _answered())
        assert "error" in shown.content[REMEMBER_FIELD]
        assert source.proposed == {}
        no_ask = ToolCall(id="c2", name=CODE_TASK, arguments={"goal": "g"})
        assert REMEMBER_FIELD not in (await runtime.delegation.outcome(no_ask, _answered())).content
    finally:
        await runtime.close()


async def test_dr2_6_a_run_without_memory_has_no_remember_field(deployment: None) -> None:
    runtime = await _runtime(None)
    try:
        assert REMEMBER_FIELD not in _schema(runtime)["properties"]
        call = ToolCall(id="c1", name=CODE_TASK, arguments={"goal": "g", REMEMBER_FIELD: [{"content": BUDGET}]})
        assert REMEMBER_FIELD not in (await runtime.delegation.outcome(call, _answered())).content
    finally:
        await runtime.close()


def test_dr2_4_the_hand_off_asks_for_remember_in_the_call_and_the_persons_writing_rules() -> None:
    for instruction in (UPFRONT_INSTRUCTION.format(why="planning"), ESCALATION_INSTRUCTION.format(why="many steps")):
        assert instruction.endswith(REMEMBER_IN_HANDOFF)
        assert "every rule they set for how it is written" in instruction
    assert "list each thing in code_task's remember" in REMEMBER_IN_HANDOFF
    assert "already remembered and needs no file" in REMEMBER_IN_HANDOFF
    assert "Do not call any other tool in this turn." in REMEMBER_IN_HANDOFF
