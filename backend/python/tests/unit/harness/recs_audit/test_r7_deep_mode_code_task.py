"""Recommendation 7 (recs audit): deep mode hands a coding subtask to an engine session (CTL-6's ``code_task``).

The real ``PipesHubRuntimeAssembler`` for a deep-mode root run whose spec pins an engine authority
(``x.pipeshub.codeTask``): its delegation offers ``spawn_agent`` and ``code_task`` side by side, and a ``code_task``
call becomes a child run on an engine executor. Deep mode's sub-agents (child runs of the loop) are not offered it:
one writer. The child's journey back (its result document, files and merge report on the parent's stream, its own
journal on its own stream) is ``test_children.py``'s CH-3 on the durable kernel.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.harness.assembly.assembler import PipesHubRuntimeAssembler
from harness_core.contracts import validate_contract
from harness_core.contracts.generated.run_spec import RunSpec
from harness_core.kernel.core.messages import ToolCall
from harness_core.kernel.durable.delegation import Delegated, DelegationView
from harness_core.testing import ModelScript, Reply, ScriptedModel, Turn
from harness_core.testing.durable import ScriptedModelPort
from tests.unit.harness.assembly import world as w

ROOT = Path(__file__).resolve().parents[6]
PROFILE = json.loads((ROOT / "contracts/harness/vectors/engine-profiles.json").read_text())["release"][0]


@pytest.fixture(autouse=True)
def _deployment(monkeypatch: pytest.MonkeyPatch) -> Any:  # noqa: ANN401
    monkeypatch.setenv("PIPESHUB_ENABLE_LAZY_TOOLS", "false")
    monkeypatch.setenv("PIPESHUB_ENABLE_FINAL_ANSWER", "false")
    with w.deployment():
        yield


def _spec(*, sub_agent: bool = False) -> RunSpec:
    spec = w.pinned_spec(tool_ids=[], sandbox="agent-code")
    raw = spec.model_dump(by_alias=True, exclude_none=True)
    raw["extensions"]["x.pipeshub"]["codeTask"] = {"profile": PROFILE}
    raw["budgets"]["maxChildren"] = 4
    if sub_agent:
        raw["extensions"]["x.pipeshub"]["subAgent"] = {"role": "coder", "toolNames": []}
    return validate_contract(RunSpec, raw)


async def _runtime(spec: RunSpec, chat_mode: str = "deep") -> Any:  # noqa: ANN401
    port = ScriptedModelPort(ScriptedModel(ModelScript(id="p", turns=[Turn(reply=Reply(text="ok"))])))
    claim = w.claim(spec, query="fix the failing test", chat_mode=chat_mode)
    return await PipesHubRuntimeAssembler(w.Services(agent=w.agent_doc()), model_factory=w.model_factory(port)).build(
        claim
    )


async def test_r7_a_deep_root_run_offers_code_task_beside_spawn_agent_and_hands_it_to_an_engine() -> None:
    runtime = await _runtime(_spec())
    try:
        assert runtime.delegation is not None
        names = {s.name for s in runtime.delegation.schemas()}
        assert {"spawn_agent", "code_task"} <= names, names
        call = ToolCall(id="t1.0", name="code_task", arguments={"goal": "Fix test_parse.py and run it"})
        pending = Delegated(invocation_id="t1.0", call=call, turn=1, state="pending")  # type: ignore[arg-type]
        decided = runtime.delegation.decide(pending, DelegationView(batch=(pending,)))
        assert decided.executor is not None and decided.executor.kind == "engine", decided
        # ADR-RUN-01 D5 (WP93-SPEC): the person's message as they wrote it, then the model's goal as its notes.
        assert decided.goal == (
            "fix the failing test\n\nNotes from the chat agent that handed this task to you:\nFix test_parse.py and run it"
        )
    finally:
        await runtime.close()


async def test_r7_a_deep_sub_agent_is_not_offered_code_task() -> None:
    runtime = await _runtime(_spec(sub_agent=True))
    try:
        names = {s.name for s in runtime.delegation.schemas()} if runtime.delegation is not None else set()
        assert "code_task" not in names, names
    finally:
        await runtime.close()
