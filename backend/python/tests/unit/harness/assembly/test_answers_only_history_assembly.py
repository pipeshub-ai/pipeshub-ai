"""A run admitted in a chat others read (``x.pipeshub.history``), as the worker assembles it.

The pin reaches the kernel (``answers_only_history``); the history its input carries comes without the answers' tool
results and reference data, and so does the legacy history its session imported. Questions, their attachments, the
answers' text and the question cards the person answered stay. A run without the pin is unchanged.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import pytest

from app.harness.assembly.assembler import PipesHubRuntimeAssembler
from app.harness.assembly.context import RunContext
from app.harness.assembly.history import LegacyHistory, answers_only_turns
from app.harness.worker.capabilities import WORKER_CAPABILITIES
from harness_core.contracts import canonical_json, dump_contract, validate_contract
from harness_core.contracts.generated.run_spec import RunSpec
from harness_core.kernel.core.messages import AssistantMessage, ToolMessage
from harness_core.testing import ModelScript, Reply, ScriptedModel, Turn
from harness_core.testing.durable import ScriptedModelPort

from . import world as w

if TYPE_CHECKING:
    from collections.abc import Iterator

    from harness_core.driver.run_driver import AssembledRuntime

PRIVATE = "Alpha budget 4.2M, the board has not seen it"
ASK = "internaltools__ask_user_question"

HISTORY: list[dict[str, Any]] = [
    {"role": "user_query", "content": "what do our plans say?", "attachments": [{"recordId": "R"}]},
    {
        "role": "bot_response",
        "content": "The public Charlie plan lists three themes.",
        "tool_results": [
            {"tool_id": "c1", "tool_name": "search", "result": PRIVATE, "status": "success"},
            {"tool_id": "ask_user_question", "tool_name": ASK, "result": '{"answers": ["EU"]}', "status": "success"},
        ],
        "referenceData": [{"name": "Alpha", "id": "a1"}],
    },
    {"role": "user_query", "content": "and its budget?"},
    {
        "role": "bot_response",
        "content": "It is not in the public plan.",
        "tool_results": [{"tool_id": "c2", "tool_name": "search", "result": PRIVATE, "status": "success"}],
    },
]


@pytest.fixture(autouse=True)
def _deployment(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    # As the port contracts assemble a runtime.
    monkeypatch.setenv("PIPESHUB_ENABLE_LAZY_TOOLS", "false")
    monkeypatch.setenv("PIPESHUB_ENABLE_FINAL_ANSWER", "false")
    with w.deployment():
        yield


async def _assembled(**extension: Any) -> AssembledRuntime:  # noqa: ANN401
    """A quick-mode run as the worker assembles it, with ``extension`` added to its ``x.pipeshub`` pin."""
    spec = w.pinned_spec(tool_ids=[f"jira@{w.JIRA}/search_issues"], kb=["kb-1"])
    if extension:
        spec = spec.model_copy(update={"extensions": {"x.pipeshub": {**(spec.extensions or {})["x.pipeshub"], **extension}}})
    port = ScriptedModelPort(ScriptedModel(ModelScript(id="p", turns=[Turn(reply=Reply(text="ok"))])))
    claim = w.claim(spec, query="and its budget?", chat_mode="quick", current_time="2026-10-01T12:00:00Z")
    return await PipesHubRuntimeAssembler(w.Services(agent=w.agent_doc()), model_factory=w.model_factory(port)).build(claim)


def _read_by_others(spec: RunSpec) -> RunSpec:
    doc = dump_contract(spec)
    doc["extensions"]["x.pipeshub"]["history"] = {"earlierTurns": "answers"}  # type: ignore[index]
    return validate_contract(RunSpec, doc)


def test_answers_only_turns_keep_the_words_and_the_answered_cards_and_drop_what_tools_returned() -> None:
    turns = answers_only_turns(HISTORY)
    assert PRIVATE not in json.dumps(turns)
    assert [t["content"] for t in turns] == [t["content"] for t in HISTORY]
    assert turns[0]["attachments"] == [{"recordId": "R"}]
    assert [r["tool_name"] for r in turns[1]["tool_results"]] == [ASK]
    assert "referenceData" not in turns[1]
    assert "tool_results" not in turns[3]


def test_the_pin_reaches_the_run_and_its_input_history_comes_without_tool_results() -> None:
    run = RunContext.from_claim(w.claim(_read_by_others(w.pinned_spec(tool_ids=[])), previous=HISTORY))
    assert run.history_answers_only is True
    assert PRIVATE not in json.dumps([dict(t) for t in run.previous_conversations])
    assert [dict(t)["content"] for t in run.previous_conversations] == [t["content"] for t in HISTORY]


def test_without_the_pin_the_run_and_its_history_are_unchanged() -> None:
    run = RunContext.from_claim(w.claim(w.pinned_spec(tool_ids=[]), previous=HISTORY))
    assert run.history_answers_only is False
    assert [dict(t) for t in run.previous_conversations] == HISTORY


def test_the_worker_advertises_answers_only_history() -> None:
    assert "answers_only_history" in [f.root for f in WORKER_CAPABILITIES.features]


async def test_the_imported_history_reaches_the_model_without_its_tool_results_in_a_chat_others_read() -> None:
    runtime = await _assembled(history={"earlierTurns": "answers"})
    assert runtime.answers_only_history is True
    history = runtime.history
    assert isinstance(history, LegacyHistory)
    messages = await history.messages(canonical_json(HISTORY))
    rendered = json.dumps([m.model_dump(mode="json") for m in messages])
    assert PRIVATE not in rendered
    assert not any(isinstance(m, ToolMessage) and m.tool_call_id in ("c1", "c2") for m in messages)
    assert any(isinstance(m, AssistantMessage) and "three themes" in m.text for m in messages)


async def test_a_chat_nobody_else_reads_replays_the_imported_tool_results_as_before() -> None:
    runtime = await _assembled()
    assert runtime.answers_only_history is False
    history = runtime.history
    assert isinstance(history, LegacyHistory)
    messages = await history.messages(canonical_json(HISTORY))
    assert PRIVATE in json.dumps([m.model_dump(mode="json") for m in messages])
    assert any(isinstance(m, ToolMessage) for m in messages)
