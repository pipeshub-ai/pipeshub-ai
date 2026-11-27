"""WP-56 item 13 (WP56-WEB-1..3): a loop research run draws on a run's web allowance, as an engine run does (WP-62).

WP-62 gave engine runs ``WebBudget.for_run()``; the native loop still built every run's web tools with a chat
answer's count (10 searches, 20 page reads; 24/40 with deep research). A deep run's researcher children are the loop's
long, many-source work, the same work DEEPROUTE hands an engine, and stopped at 20 page reads with most of their USD
budget left. The loop decides it where it builds the run's tools: a deep root run and its children research on the
run's allowance; any other loop run is one answer and keeps the answer's count.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.harness.assembly import assembler
from app.harness.assembly.assembler import PipesHubRuntimeAssembler
from app.web.budget import RUN, STANDARD, WEB_BUDGET_KEY, WebBudget
from harness_core.contracts import validate_contract
from harness_core.contracts.generated.run_spec import RunSpec
from harness_core.testing import ModelScript, Reply, ScriptedModel, Turn
from harness_core.testing.durable import ScriptedModelPort
from tests.unit.harness.assembly import world as w


@pytest.fixture(autouse=True)
def _deployment(monkeypatch: pytest.MonkeyPatch) -> Any:  # noqa: ANN401
    monkeypatch.setenv("PIPESHUB_ENABLE_LAZY_TOOLS", "false")
    monkeypatch.setenv("PIPESHUB_ENABLE_FINAL_ANSWER", "false")
    with w.deployment():
        yield


def _spec(*, sub_agent: bool = False) -> RunSpec:
    spec = w.pinned_spec(tool_ids=[], max_children=0 if sub_agent else w.DEEP_CHILDREN)
    if not sub_agent:
        return spec
    raw = spec.model_dump(by_alias=True, exclude_none=True)
    raw["extensions"]["x.pipeshub"]["subAgent"] = {"role": "researcher", "toolNames": []}
    return validate_contract(RunSpec, raw)


async def _scope_when_tools_load(
    monkeypatch: pytest.MonkeyPatch, spec: RunSpec, chat_mode: str, *, trigger: dict[str, Any] | None = None
) -> str:
    seen: list[str] = []
    original = assembler.load_run_tools

    async def spy(*args: object, **kwargs: object) -> object:
        seen.append(args[2].context.web_scope)  # type: ignore[attr-defined]
        return await original(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(assembler, "load_run_tools", spy)
    port = ScriptedModelPort(ScriptedModel(ModelScript(id="p", turns=[Turn(reply=Reply(text="ok"))])))
    built = PipesHubRuntimeAssembler(w.Services(agent=w.agent_doc()), model_factory=w.model_factory(port))
    runtime = await built.build(w.claim(spec, query="compare these two products", chat_mode=chat_mode, trigger=trigger))
    await runtime.close()
    assert len(seen) == 1
    return seen[0]


async def test_wp56_web_1_a_deep_root_loop_run_researches_on_the_runs_allowance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert await _scope_when_tools_load(monkeypatch, _spec(), "deep") == "run"


async def test_wp56_web_2_a_deep_runs_child_researches_on_its_runs_allowance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert await _scope_when_tools_load(monkeypatch, _spec(sub_agent=True), "deep") == "run"


@pytest.mark.parametrize("chat_mode", ["quick", "planExecute"])
async def test_wp56_web_3_any_other_loop_run_is_one_answer_and_keeps_the_answers_count(
    monkeypatch: pytest.MonkeyPatch, chat_mode: str
) -> None:
    assert await _scope_when_tools_load(monkeypatch, _spec(), chat_mode) == "answer"


def test_wp56_web_1_the_runs_allowance_is_the_engines_and_says_what_is_left() -> None:
    """The loop's run allowance is WP-62's, with its line of what is left and its "finish" refusal."""
    run = WebBudget.for_context(scope="run", deep_research=False)
    answer = WebBudget.for_context(scope="answer", deep_research=False)
    assert (run.searches, run.pages) == RUN and (answer.searches, answer.pages) == STANDARD
    assert run.allowance_line() is not None and answer.allowance_line() is None
    assert WEB_BUDGET_KEY == "web_budget"


_CRON = {"kind": "cron", "scheduleId": "sch-1", "firingId": "f-1", "slotAt": "2026-11-02T09:00:00Z"}
_WEBHOOK = {"kind": "webhook", "scheduleId": "sch-1", "firingId": "f-2", "slotAt": "2026-11-02T09:05:00Z"}


@pytest.mark.parametrize("trigger", [_CRON, _WEBHOOK], ids=["scheduled", "triggered"])
@pytest.mark.parametrize("chat_mode", ["quick", "planExecute"])
async def test_wp56_web_4_a_scheduled_or_triggered_loop_run_researches_on_the_runs_allowance(
    monkeypatch: pytest.MonkeyPatch, chat_mode: str, trigger: dict[str, Any]
) -> None:
    """An unattended run has nobody to say "continue" when an answer's count runs out, so
    it draws on the run's allowance whatever its mode; its USD budget stays the real limit."""
    assert await _scope_when_tools_load(monkeypatch, _spec(), chat_mode, trigger=trigger) == "run"


async def test_wp56_web_4_a_quick_chat_run_keeps_an_answers_count(monkeypatch: pytest.MonkeyPatch) -> None:
    assert await _scope_when_tools_load(monkeypatch, _spec(), "quick", trigger=None) == "answer"
