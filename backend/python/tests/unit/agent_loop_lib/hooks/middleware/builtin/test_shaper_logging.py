"""Every context shaper that changes what the model sees logs one INFO line
when it acts, and nothing when it does not."""

from __future__ import annotations

import logging

import pytest

from app.agent_loop_lib.context.base import ContextBudget
from app.agent_loop_lib.core.messages import (
    AssistantMessage,
    ToolCall,
    ToolMessage,
    ToolMessageMeta,
    UserMessage,
)
from app.agent_loop_lib.hooks.middleware.builtin.artifact_compaction import (
    shape_artifact_compaction,
)
from app.agent_loop_lib.hooks.middleware.builtin.auto_compact import shape_auto_compact
from app.agent_loop_lib.hooks.middleware.builtin.budget_reduction import (
    shape_budget_reduction,
)
from app.agent_loop_lib.hooks.middleware.builtin.deterministic_compact import (
    shape_deterministic_compact,
)
from app.agent_loop_lib.hooks.middleware.builtin.loop_compaction import (
    shape_loop_compaction,
)
from app.agent_loop_lib.hooks.middleware.builtin.offload import shape_offload
from app.agent_loop_lib.hooks.middleware.builtin.sliding_window import (
    shape_sliding_window,
)
from app.agent_loop_lib.hooks.middleware.builtin.tool_result_clearing import (
    shape_tool_result_clearing,
)
from app.agent_loop_lib.hooks.middleware.builtin.truncation_recovery import (
    default_truncation_recovery,
)
from app.agent_loop_lib.hooks.middleware.context import (
    ModelCallContext,
    ModelResponseContext,
)

_BUDGET = ContextBudget(max_tokens=20_000)
_TURN = 10


def _conversation(result_chars: int, *, with_artifacts: bool = True) -> list:
    messages: list = [UserMessage(content="find it")]
    for turn in range(8):
        call_id = f"c{turn}"
        messages.append(AssistantMessage(tool_calls=[ToolCall(id=call_id, name="search", arguments={})]))
        meta = ToolMessageMeta(
            artifact_id=f"a{turn}", summary="rows", original_token_count=result_chars // 4, turn_index=turn,
        ) if with_artifacts else None
        messages.append(ToolMessage(content="r" * result_chars, tool_call_id=call_id, artifact_meta=meta))
    return messages


_LARGE = 12_000  # 8 results of 3K tokens: 24K, over every ratio of a 20K budget
_SMALL = 400

# shaper name -> (middleware factory, messages it acts on, messages it leaves alone)
_CASES = {
    "budget_reduction": (
        lambda: shape_budget_reduction(max_result_chars=1_000), _conversation(_SMALL * 4), _conversation(_SMALL),
    ),
    "artifact_compaction": (
        lambda: shape_artifact_compaction(keep_last_n_turns=2), _conversation(_LARGE), _conversation(_SMALL),
    ),
    "tool_result_clearing": (
        lambda: shape_tool_result_clearing(), _conversation(_LARGE), _conversation(_SMALL),
    ),
    "loop_compaction": (lambda: shape_loop_compaction(), _conversation(_LARGE), _conversation(_SMALL)),
    "sliding_window": (lambda: shape_sliding_window(), _conversation(_LARGE), _conversation(_SMALL)),
    "offload": (
        lambda: shape_offload(threshold_tokens=500), _conversation(_SMALL * 8), _conversation(_SMALL),
    ),
    "deterministic_compact": (
        lambda: shape_deterministic_compact(), _conversation(_LARGE), _conversation(_SMALL),
    ),
    "auto_compact": (lambda: shape_auto_compact(), _conversation(_LARGE), _conversation(_SMALL)),
}


async def _noop() -> None:
    return None


def _lines(caplog, name: str) -> list[logging.LogRecord]:
    return [
        r for r in caplog.records
        if r.levelno == logging.INFO and r.getMessage().startswith(f"{name}:")
    ]


async def _shape(name: str, messages: list) -> list:
    factory, _, _ = _CASES[name]
    ctx = ModelCallContext(messages=list(messages), budget=_BUDGET, turn_index=_TURN)
    await factory()(ctx, _noop)
    return ctx.messages


class TestShaperLogLine:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("name", list(_CASES))
    async def test_one_line_when_it_acts(self, name, caplog) -> None:
        acts_on = _CASES[name][1]
        with caplog.at_level(logging.INFO):
            shaped = await _shape(name, acts_on)

        assert shaped != acts_on
        lines = _lines(caplog, name)
        assert len(lines) == 1
        text = lines[0].getMessage()
        assert "→" in text and "tokens" in text
        assert f"turn={_TURN}" in text and f"budget={_BUDGET.effective_max_tokens}" in text

    @pytest.mark.asyncio
    @pytest.mark.parametrize("name", list(_CASES))
    async def test_silent_when_it_does_not_act(self, name, caplog) -> None:
        left_alone = _CASES[name][2]
        with caplog.at_level(logging.INFO):
            shaped = await _shape(name, left_alone)

        assert shaped == left_alone
        assert _lines(caplog, name) == []

    @pytest.mark.asyncio
    async def test_artifact_compaction_silent_when_nothing_is_compactable(self, caplog) -> None:
        # Over the whole budget, but no result carries an artifact to compact.
        messages = _conversation(_LARGE, with_artifacts=False)
        with caplog.at_level(logging.INFO):
            shaped = await _shape("artifact_compaction", messages)

        assert shaped == messages
        assert _lines(caplog, "artifact_compaction") == []


def _response_ctx(*, truncated: bool, with_call: bool) -> ModelResponseContext:
    calls = [ToolCall(id="c1", name="search", arguments={})] if with_call else []
    response = AssistantMessage(content="partial", tool_calls=calls or None, truncated=truncated)
    return ModelResponseContext(response=response, tool_calls=calls, turn_index=_TURN)


class TestTruncationRecoveryLogLine:
    @pytest.mark.asyncio
    @pytest.mark.parametrize("with_call", [True, False])
    async def test_one_line_when_it_acts(self, with_call, caplog) -> None:
        with caplog.at_level(logging.INFO):
            await default_truncation_recovery()(_response_ctx(truncated=True, with_call=with_call), _noop)

        lines = _lines(caplog, "truncation_recovery")
        assert len(lines) == 1
        assert f"turn={_TURN}" in lines[0].getMessage()

    @pytest.mark.asyncio
    async def test_silent_when_the_response_is_complete(self, caplog) -> None:
        with caplog.at_level(logging.INFO):
            await default_truncation_recovery()(_response_ctx(truncated=False, with_call=True), _noop)

        assert _lines(caplog, "truncation_recovery") == []
