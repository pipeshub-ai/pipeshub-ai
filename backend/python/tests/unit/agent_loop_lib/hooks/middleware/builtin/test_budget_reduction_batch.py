"""BUDGETEND: L1 caps a batch's results to a shared slice of the model's window, on both loops.

A durable run (the shaped path, which runs this shaper) kept up to eight Slack results of 64,000 characters each
per turn. A batch now shares 12% of the window: one result takes all of it, many results each get less, a tool
with its own cap (fetch_record) keeps it, and a cut result names its artifact to read the rest.
"""

from __future__ import annotations

from app.agent_loop_lib.context.base import ContextBudget
from app.agent_loop_lib.core.messages import (
    AssistantMessage,
    ToolCall,
    ToolMessage,
    ToolMessageMeta,
    UserMessage,
)
from app.agent_loop_lib.hooks.middleware.builtin.budget_reduction import (
    shape_budget_reduction,
)
from app.agent_loop_lib.hooks.middleware.context import ModelCallContext
from harness_core.context.results import RESULT_CAP_CHARS, batch_result_cap

WINDOW = 128_000


def _batch(n: int, chars: int, name: str = "slack__search_messages") -> list:
    calls = [ToolCall(id=f"c{i}", name=name, arguments={}) for i in range(n)]
    results = [
        ToolMessage(
            content="s" * chars,
            tool_call_id=c.id,
            artifact_meta=ToolMessageMeta(artifact_id=f"art-{c.id}", tool_name=name, turn_index=1),
        )
        for c in calls
    ]
    return [UserMessage(content="what's happening in slack?"), AssistantMessage(tool_calls=calls), *results]


async def _shaped(messages: list, **options: object) -> list:
    ctx = ModelCallContext(messages=messages, budget=ContextBudget.for_model("gpt-x", context_window=WINDOW))

    async def done() -> None:
        return None

    await shape_budget_reduction(**options)(ctx, done)  # type: ignore[arg-type]
    return ctx.messages


async def test_one_result_takes_the_whole_share() -> None:
    whole = batch_result_cap(1, WINDOW, RESULT_CAP_CHARS)
    assert (await _shaped(_batch(1, whole)))[-1].text == "s" * whole, "within the batch's whole share: untouched"
    assert len((await _shaped(_batch(1, 2 * whole)))[-1].text) <= whole + 200


async def test_eight_results_share_the_batch_slice() -> None:
    shaped = await _shaped(_batch(8, 70_000))
    cap = batch_result_cap(8, WINDOW, RESULT_CAP_CHARS)
    results = [m for m in shaped if isinstance(m, ToolMessage)]
    assert all(len(m.text) <= cap + 200 for m in results), [len(m.text) for m in results]
    assert sum(len(m.text) for m in results) < 8 * RESULT_CAP_CHARS // 3
    assert 'retrieve_artifact_content(artifact_id="art-c0")' in results[0].text


async def test_a_tool_with_its_own_cap_keeps_it() -> None:
    # One result, so the window holds it whole: what binds is the batch share (below 60,000 here) or the own cap.
    assert batch_result_cap(1, WINDOW, RESULT_CAP_CHARS) < 60_000
    shaped = await _shaped(_batch(1, 70_000, name="fetch_record"), tool_caps={"fetch_record": 60_000})
    assert all(50_000 < len(m.text) <= 60_000 + 200 for m in shaped if isinstance(m, ToolMessage))


async def test_an_own_cap_still_fits_the_window() -> None:
    # Eight results at their own 60,000 would not fit a 128k window beside the request; the sliding window would
    # then evict them, so each is cut to what fits instead (main's L1 fitting caps).
    shaped = await _shaped(_batch(8, 70_000, name="fetch_record"), tool_caps={"fetch_record": 60_000})
    results = [m for m in shaped if isinstance(m, ToolMessage)]
    assert sum(len(m.text) for m in results) < WINDOW * 4


async def test_the_cut_is_the_same_bytes_on_every_turn() -> None:
    first = await _shaped(_batch(4, 90_000))
    again = await _shaped([*first, AssistantMessage(content="noted")])
    assert [m.text for m in first if isinstance(m, ToolMessage)] == [m.text for m in again if isinstance(m, ToolMessage)]
