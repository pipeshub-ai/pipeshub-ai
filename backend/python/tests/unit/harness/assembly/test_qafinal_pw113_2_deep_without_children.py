"""QAFINAL-FU-PW113-2 (ADR-RUN-04 D4): a deep-mode turn of an agent pinned no children runs as its ordinary loop.

Node pins ``budgets.maxChildren`` from the agent's definition; one that sets ``maxChildren: 0`` grants a deep turn none.
Before, the worker keyed deep mode on the chat mode alone: the root was offered ``create_plan``, ``critique_plan``,
``verify_result`` and ``spawn_agent``, driven through plan, critique and dispatch, and Node refused every spawn
(``CHILDREN_NOT_GRANTED``). Deep mode is planning work and handing it to sub-agents, so a run that can start none runs
as the agent's own loop with the agent's own tools and answers.

The real ``PipesHubRuntimeAssembler`` and ``RunDriver`` over the in-memory journal, with the scripted model.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.harness.assembly.assembler import PipesHubRuntimeAssembler
from app.harness.assembly.context import RunContext
from app.harness.assembly.deep import COORDINATION_TOOLS
from harness_core.contracts import validate_contract
from harness_core.contracts.generated.run_spec import RunSpec
from harness_core.driver import RunDriver
from harness_core.testing import ManualClock, ModelScript, Reply, ScriptedModel, Turn
from harness_core.testing.durable import ScriptedModelPort

from . import world as w


@pytest.fixture(autouse=True)
def _deployment() -> Any:  # noqa: ANN401
    with w.deployment():
        yield


def _spec(max_children: int) -> RunSpec:
    raw = w.pinned_spec(tool_ids=[]).model_dump(by_alias=True, exclude_none=True)
    raw["extensions"]["x.pipeshub"]["agent"]["mode"] = "deep"
    raw["budgets"]["maxChildren"] = max_children
    return validate_contract(RunSpec, raw)


def _port(*replies: Reply) -> ScriptedModelPort:
    return ScriptedModelPort(ScriptedModel(ModelScript(id="pw113-2", turns=[Turn(reply=r) for r in replies])))


class _DeepClaims:
    """The world's claims for a deep chat (``w.ClaimAssembler`` admits a quick one)."""

    def __init__(self, inner: Any) -> None:  # noqa: ANN401
        self._inner = inner

    async def build(self, attempt: Any) -> Any:  # noqa: ANN401
        node = w.claim(attempt.spec, query=attempt.input_text, run_id=attempt.run_id, chat_mode="deep")
        values = {f: getattr(attempt, f) for f in attempt.__dataclass_fields__}
        return await self._inner.build(type(node)(**values, run=node.run))


def test_pw113_2_1_a_deep_root_granted_no_children_neither_is_deep_nor_coordinates() -> None:
    granted = RunContext.from_claim(w.claim(_spec(16), chat_mode="deep"))
    none = RunContext.from_claim(w.claim(_spec(0), chat_mode="deep"))
    assert (granted.deep, granted.coordinates) == (True, True)
    assert (none.deep, none.coordinates) == (False, False)
    assert none.web_scope == "answer", "an ordinary turn keeps an answer's web allowance"


async def test_pw113_2_2_its_runtime_holds_no_coordination_tool_and_no_delegation() -> None:
    assembler = PipesHubRuntimeAssembler(w.Services(), model_factory=w.model_factory(_port(Reply(text="ok"))))
    runtime = await assembler.build(w.claim(_spec(0), chat_mode="deep"))
    try:
        assert runtime.delegation is None
        assert [n for n in sorted(COORDINATION_TOOLS) if runtime.tools.describe(n) is not None] == []
    finally:
        await runtime.close()


async def test_pw113_2_3_its_turn_is_answered_by_the_agents_own_loop() -> None:
    clock = ManualClock()
    journal = w.pipeshub_journal(clock=clock, lease_ms=30_000)
    journal.admit("run-1", org_id=w.ORG, session_id="ses-1", spec=_spec(0), input_text="find the open bugs")
    port = _port(Reply(text="Two bugs are open."))
    assembler = PipesHubRuntimeAssembler(w.Services(), model_factory=w.model_factory(port))
    driver = RunDriver(journal=journal, assembler=_DeepClaims(assembler), clock=clock)
    done = await driver.run_claimed(await w.claim_next(journal))
    assert done.status == "completed"
    assert port.calls, "the model was asked"
    for call in port.calls:
        offered = {t.name for t in call.tools or []}
        assert not offered & COORDINATION_TOOLS, sorted(offered & COORDINATION_TOOLS)
    assert not [e for e in journal.events("ses-1") if e.type == "child.spawned"]
