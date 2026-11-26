"""SR-1..SR-7 (S1R): stage-1 ``code_task`` is retired; what an install that turned it on sees, and that it stays gone."""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock

import pytest

from app.agents.agent_loop.factory import (  # pyright: ignore[reportPrivateUsage]
    PipesHubAgentFactory,
    _convert_conversation_turn,
)
from app.services.featureflag.platform_settings import (
    PLATFORM_SETTINGS_KEY,
    platform_flag_default,
)
from harness_core.kernel.core.messages import (
    AssistantMessage,
    ToolCall,
    ToolMessage,
    UserMessage,
)
from harness_core.kernel.core.tool_content import UNKNOWN_TOOL_ERROR_PREFIX
from harness_core.tools.executor import ToolExecutor
from tests.unit.agents.adapter.conftest import make_context

if TYPE_CHECKING:
    from app.agents.agent_loop.context import AgentContext

BACKEND = Path(__file__).resolve().parents[4]
STAGE1_MODULES = (
    "app.agents.agent_loop.code_task",
    "app.harness.engines.stage1",
    "app.harness.engines.query_host",
    "app.harness.engines.leases",
    "app.harness.access.request_grants",
    "harness_core.engines.request_runner",
)
STAGE1_NAMES = (
    "code_task_scope",
    "codeTaskScope",
    "CodeTaskHost",
    "register_code_task_if_enabled",
    "is_code_task_enabled",
    "ENABLE_CODE_TASK",
    "PIPESHUB_CODE_TASK",
    "RequestEngineRunner",
    "SessionTaskLeases",
    "RequestModelGrants",
    "RequestBudgets",
    "RequestBinding",
    "for_request",
    "TOKEN_HANDOUT_PATH",
)
PRODUCT_ROOTS = ("app", "harness_core", "harness_engines")


def _context(flags: dict[str, bool] | None) -> AgentContext:
    context = make_context(conversation_id="conv-s1r")

    async def get_config(key: str, *_args: Any, **_kwargs: Any) -> Any:  # noqa: ANN401
        return {"featureFlags": flags} if flags is not None and key == PLATFORM_SETTINGS_KEY else None

    context.config_service.get_config = AsyncMock(side_effect=get_config)  # pyright: ignore[reportAttributeAccessIssue]
    return context


async def _built(context: AgentContext) -> tuple[list[str], str, Any]:
    agent, runtime, goal, _ = await PipesHubAgentFactory().create(context, context.llm, "quick", query="hello")
    prompt = agent.spec.system_prompt
    rendered = prompt if isinstance(prompt, str) else prompt.build(agent.spec, runtime, goal, [], {})
    return sorted(agent.spec.tool_names), str(rendered), runtime


@pytest.fixture(autouse=True)
def _no_skills(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PIPESHUB_ENABLE_SKILLS", "false")


async def test_sr_1_a_stored_labs_true_changes_nothing() -> None:
    stored = _context({"ENABLE_CODE_TASK": True})
    # The stored value is still there, and no reader can read it: the flag is in no defaults table (UX2 FD-1).
    settings = await stored.config_service.get_config(PLATFORM_SETTINGS_KEY)
    assert settings["featureFlags"]["ENABLE_CODE_TASK"] is True
    with pytest.raises(KeyError):
        platform_flag_default("ENABLE_CODE_TASK")
    stored_on, prompt_on, _ = await _built(stored)
    never_set, prompt_never, _ = await _built(_context(None))
    assert "code_task" not in stored_on
    assert (stored_on, prompt_on) == (never_set, prompt_never)
    assert "code_task" not in prompt_on


def test_sr_3_a_saved_code_task_call_in_history_converts_as_any_tool_call() -> None:
    turn = {
        "role": "bot_response",
        "content": "Revenue grew 11% [source](ref1). The table is in q3.csv.",
        "tool_results": [
            {"tool_name": "code_task", "tool_id": "c1", "status": "success", "args": {"goal": "Analyse q3.csv"},
             "result": "Wrote out/q3.csv"},
        ],
    }
    messages = _convert_conversation_turn(turn)
    assert [type(m) for m in messages] == [AssistantMessage, ToolMessage, AssistantMessage]
    first = messages[0]
    assert isinstance(first, AssistantMessage)
    assert first.tool_calls == [ToolCall(id="c1", name="code_task", arguments={"goal": "Analyse q3.csv"})]
    assert isinstance(_convert_conversation_turn({"role": "user_query", "content": "q"})[0], UserMessage)


async def test_sr_3_a_conversation_that_used_it_builds_without_it() -> None:
    context = _context({"ENABLE_CODE_TASK": True})
    context.previous_conversations = [
        {"role": "user_query", "content": "Analyse q3.csv"},
        {"role": "bot_response", "content": "Done.", "tool_results": [
            {"tool_name": "code_task", "tool_id": "c1", "status": "success", "args": {"goal": "x"}, "result": "ok"},
        ]},
    ]
    tools, _, _ = await _built(context)
    assert "code_task" not in tools


async def test_sr_4_an_agent_naming_code_task_in_its_toolsets_loads_without_it() -> None:
    context = _context(None)
    context.agent_toolsets = [{"name": "code_task", "tools": [{"name": "code_task"}]}]
    tools, _, _ = await _built(context)
    assert "code_task" not in tools


async def test_sr_5_a_model_that_calls_code_task_gets_the_unknown_tool_answer() -> None:
    _, _, runtime = await _built(_context({"ENABLE_CODE_TASK": True}))
    result = await ToolExecutor(runtime.tool_registry).call_tool(ToolCall(id="c1", name="code_task", arguments={"goal": "x"}))
    assert result.is_error and str(result.content).startswith(UNKNOWN_TOOL_ERROR_PREFIX)


@pytest.mark.parametrize("module", STAGE1_MODULES)
def test_sr_6_no_stage_1_module_is_importable(module: str) -> None:
    assert importlib.util.find_spec(module) is None, f"{module} is back: stage 1 was retired (S1R)"


def test_sr_6_no_product_source_names_a_stage_1_piece() -> None:
    found: list[str] = []
    for root in PRODUCT_ROOTS:
        for path in (BACKEND / root).rglob("*.py"):
            if "generated" in path.parts:
                continue
            text = path.read_text(encoding="utf-8")
            found += [f"{path.relative_to(BACKEND)}: {name}" for name in STAGE1_NAMES if re.search(rf"\b{name}\b", text)]
    assert found == []


def test_sr_7_the_query_service_mounts_no_tool_gateway() -> None:
    from app.query_main import app

    paths = {getattr(route, "path", "") for route in app.routes}
    assert not any(path.startswith("/api/v1/harness/tool-gateway") for path in paths)
    assert not hasattr(app.state, "code_task_host")
