"""The production PRE_MODEL chain (`PipesHubAgentFactory._build_hooks`) on the
layouts real chats produce, call after call.

The customer case: a chat whose current question comes after an earlier turn,
then a long tool loop with large `fetch_record` results. Before the fix the
chain kept the earlier question and dropped the current one, and the model
answered the earlier question (docs/context-engineering/REPORT.md, CE-01).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from app.agent_loop_lib.agent.hook_dispatch import dispatch_pre_model
from app.agent_loop_lib.context.base import ContextBudget
from app.agent_loop_lib.context.errors import ContextBudgetExceeded
from app.agent_loop_lib.core.messages import (
    AssistantMessage,
    ImagePart,
    TextPart,
    ToolCall,
    ToolMessage,
    UserMessage,
)
from app.agent_loop_lib.core.tokens import count_tokens
from app.agent_loop_lib.hooks.events import HookEvent
from app.agent_loop_lib.hooks.middleware.builtin._message_boundaries import (
    repair_tool_pairing,
)
from app.agents.agent_loop.context import AgentContext
from app.agents.agent_loop.factory import (
    PipesHubAgentFactory,
    _append_turn,
    _convert_conversation_turn,
)

if TYPE_CHECKING:
    from app.agent_loop_lib.hooks.registry import HookRegistry


FETCH = "knowledgegraph__fetch_record"
CURRENT = "I have access to more than one practice. How do I pick which practice to log into and set a default?"
_IMAGE = {"type": "image_url", "image_url": {"url": "data:image/png;base64,iVBORw0KGgo="}}
_WORDS = "practice calendar patient invoice reminder settings audit role clinician export billing claim "


class _SummaryTransport:
    async def complete(self, messages: list, **_: Any) -> Any:  # noqa: ANN401
        class _Response:
            message = AssistantMessage(content=[TextPart(text="summary of earlier work")])

        return _Response()


class _Registry:
    def resolve(self, _provider: str) -> _SummaryTransport:
        return _SummaryTransport()


def _context(**fields: Any) -> AgentContext:  # noqa: ANN401
    return AgentContext(org_id="org", user_id="user", user_email="user@example.com", **fields)


def _chain(context: AgentContext | None = None, *, multipart: bool = True) -> "HookRegistry":
    return PipesHubAgentFactory._build_hooks(
        context or _context(), transport_registry=_Registry(), model_name="m",
        supports_multipart_tool_result=multipart,
    )


def _budget(window: int, prompt: int) -> ContextBudget:
    return ContextBudget.for_model("m", context_window=window, reserved_prompt_tokens=prompt)


def _text(chars: int) -> str:
    return (_WORDS * (chars // len(_WORDS) + 1))[:chars]


def _history(turns: int) -> list:
    """Earlier turns, seeded exactly as `_seed_conversation_history` does."""
    history: list = []
    tool_results_at: dict[str, int] = {}
    for t in range(turns):
        question = {"role": "user_query", "content": f"QUESTION {t}: how do SMS appointment reminders work?"}
        answer = {
            "role": "bot_response",
            "content": f"ANSWER {t}: enable reminders in Practice Settings. " + _text(1_500),
            "tool_results": [
                {"tool_name": "knowledgegraph__search", "tool_id": f"h{t}_{i}", "result": _text(800),
                 "args": {"query": "sms reminders"}, "status": "success"}
                for i in range(2)
            ],
        }
        _append_turn(history, _convert_conversation_turn(question), tool_results_at)
        _append_turn(history, _convert_conversation_turn(answer), tool_results_at)
    return history


def _tool_round(stored: list, n: int, chars: int, *, parallel: int = 1) -> None:
    calls = [ToolCall(id=f"f{n}_{p}", name=FETCH, arguments={"record_ids": [f"r{n}_{p}"]}) for p in range(parallel)]
    stored.append(AssistantMessage(content=[TextPart(text=f"Reading manual {n}.")], tool_calls=calls))
    stored.extend(ToolMessage(content=_text(chars), tool_call_id=call.id) for call in calls)


def _humans(messages: list) -> list:
    return [m for m in messages if isinstance(m, UserMessage) and not m.injected]


def _text_of(message: UserMessage) -> str:
    if isinstance(message.content, str):
        return message.content
    return "".join(p.text for p in message.content if isinstance(p, TextPart))


def _assert_current_question_sent(sent: list) -> None:
    humans = _humans(sent)
    assert humans, "no human message was sent"
    assert _text_of(humans[-1]) == CURRENT, f"last human message sent: {_text_of(humans[-1])[:60]!r}"


async def _send(hooks, stored: list, budget: ContextBudget, turn: int) -> list:
    return await dispatch_pre_model(hooks, stored, budget, turn_index=turn, max_turns=30)


def test_pipeline_order_runs_least_lossy_first() -> None:
    names = [mw.__qualname__.split(".<locals>")[0] for _, mw in _chain().on(HookEvent.PRE_MODEL)._stack]
    shapers = [n for n in names if n.startswith("shape_")]
    assert shapers == [
        "shape_image_injection",
        "shape_budget_reduction",
        "shape_artifact_compaction",
        "shape_tool_result_clearing",
        "shape_loop_compaction",
        "shape_deterministic_compact",
        "shape_auto_compact",
        "shape_auto_compact",
        "shape_sliding_window",
        "shape_synthesis_guard",
        "shape_context_invariants",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize("fetch_chars", [20_000, 64_000, 146_000])
async def test_customer_case_current_question_survives_a_long_fetch_loop(fetch_chars, caplog) -> None:
    """SC-01: earlier question, then the current one, then 12 large fetches
    at a 200K window. Before the fix the current question was gone from the
    5th call on (fetches of 64K chars or more)."""
    hooks = _chain()
    budget = _budget(200_000, 20_000)
    stored = [*_history(1), UserMessage(content=CURRENT)]
    compacted = False
    with caplog.at_level(logging.INFO):
        for turn in range(1, 13):
            _tool_round(stored, turn, fetch_chars)
            sent = await _send(hooks, stored, budget, turn)
            _assert_current_question_sent(sent)
            compacted = compacted or count_tokens(sent) < count_tokens(stored)
    assert "context_invariant_repaired" not in caplog.text
    if fetch_chars >= 64_000:
        assert compacted, "the scenario must push the chain into compaction"


@pytest.mark.asyncio
async def test_small_window_with_five_earlier_turns() -> None:
    """SC-02: an 8K window and five earlier turns: every call still carries
    the current question, or the call stops with a typed error."""
    hooks = _chain()
    budget = _budget(8_000, 2_000)
    stored = [*_history(5), UserMessage(content=CURRENT)]
    for turn in range(1, 16):
        _tool_round(stored, turn, 3_000)
        sent = await _send(hooks, stored, budget, turn)
        _assert_current_question_sent(sent)
        assert count_tokens(sent) <= budget.hard_limit_tokens


@pytest.mark.asyncio
async def test_single_question_run_keeps_its_goal() -> None:
    """SC-03: with no earlier turns the goal is the first message, as before."""
    hooks = _chain()
    budget = _budget(32_000, 6_000)
    stored = [UserMessage(content=CURRENT)]
    for turn in range(1, 21):
        _tool_round(stored, turn, 8_000)
        sent = await _send(hooks, stored, budget, turn)
        assert sent[0].content == CURRENT
        _assert_current_question_sent(sent)


@pytest.mark.asyncio
async def test_pinned_plan_is_sent_verbatim_on_every_call() -> None:
    """SC-04: a plan-execute run's pinned plan is never shortened."""
    plan = UserMessage(content="PLAN\n" + "\n".join(f"{i}. read manual {i}" for i in range(40)), injected=True, pinned=True)
    hooks = _chain()
    budget = _budget(32_000, 6_000)
    stored = [*_history(1), UserMessage(content=CURRENT), plan]
    for turn in range(1, 21):
        _tool_round(stored, turn, 8_000)
        sent = await _send(hooks, stored, budget, turn)
        assert [m.content for m in sent if getattr(m, "pinned", False)] == [plan.content]
        _assert_current_question_sent(sent)


@pytest.mark.asyncio
async def test_attachment_image_goes_on_the_current_question() -> None:
    """SC-07: the current turn's attachment rides on the current question,
    never on the earlier one, and stored history is not changed."""
    context = _context()
    context.attachment_image_blocks = [_IMAGE]
    hooks = _chain(context)
    stored = [*_history(2), UserMessage(content=CURRENT)]
    before = [m.model_dump() for m in stored]
    for turn in range(1, 4):
        _tool_round(stored, turn, 2_000)
        sent = await _send(hooks, stored, _budget(200_000, 20_000), turn)
        with_images = [m for m in sent if isinstance(m, UserMessage) and isinstance(m.content, list)
                       and any(isinstance(p, ImagePart) for p in m.content)]
        assert [_text_of(m) for m in with_images] == [CURRENT]
    assert [m.model_dump() for m in stored[: len(before)]] == before


@pytest.mark.asyncio
async def test_retrieved_images_are_delivered_once_on_the_current_question() -> None:
    """SC-07b: non-multipart providers get a search's images once, on the
    current question; stored history keeps no copy."""
    context = _context()
    hooks = _chain(context, multipart=False)
    stored = [*_history(1), UserMessage(content=CURRENT)]
    _tool_round(stored, 1, 2_000)
    context.tool_state["pending_tool_images"] = [_IMAGE, _IMAGE]

    first = await _send(hooks, stored, _budget(200_000, 20_000), 1)
    second = await _send(hooks, stored, _budget(200_000, 20_000), 2)

    current_first = _humans(first)[-1]
    assert isinstance(current_first.content, list)
    assert sum(isinstance(p, ImagePart) for p in current_first.content) >= 1
    assert isinstance(_humans(second)[-1].content, str), "a batch is delivered once"
    assert all(isinstance(m.content, str) for m in stored if isinstance(m, UserMessage))


_SETTINGS = settings(max_examples=40, deadline=None, suppress_health_check=[HealthCheck.too_slow])


@_SETTINGS
@given(
    earlier_turns=st.integers(min_value=0, max_value=4),
    rounds=st.integers(min_value=0, max_value=18),
    result_chars=st.sampled_from([500, 4_000, 20_000, 60_000]),
    parallel=st.integers(min_value=1, max_value=3),
    window=st.sampled_from([8_000, 32_000, 128_000, 200_000]),
    prompt_share=st.sampled_from([0.05, 0.2, 0.5]),
    nudge_every=st.sampled_from([0, 3]),
    with_plan=st.booleans(),
)
@pytest.mark.asyncio
async def test_invariants_hold_for_any_chat(
    earlier_turns, rounds, result_chars, parallel, window, prompt_share, nudge_every, with_plan,
) -> None:
    """PT-01..04, PT-06 on the production chain: the current question is sent
    verbatim as the last human message, pinned messages are verbatim, tool
    pairing holds, stored history is untouched, and the request fits the
    window or the call stops with ContextBudgetExceeded."""
    hooks = _chain()
    budget = _budget(window, int(window * prompt_share))
    stored: list = [*_history(earlier_turns), UserMessage(content=CURRENT)]
    plan = UserMessage(content="PLAN: read every manual", injected=True, pinned=True)
    if with_plan:
        stored.append(plan)
    for n in range(1, rounds + 1):
        _tool_round(stored, n, result_chars, parallel=parallel)
        if nudge_every and n % nudge_every == 0:
            stored.append(UserMessage(content="Keep going: check the next manual.", injected=True))

    before = [m.model_dump() for m in stored]
    try:
        sent = await _send(hooks, stored, budget, rounds)
    except ContextBudgetExceeded:
        assert [m.model_dump() for m in stored] == before
        return

    _assert_current_question_sent(sent)
    if with_plan:
        assert [m.content for m in sent if getattr(m, "pinned", False)] == [plan.content]
    assert repair_tool_pairing(sent)[1] == 0
    assert count_tokens(sent) <= budget.hard_limit_tokens
    assert [m.model_dump() for m in stored] == before


class _SeededAgent:
    def __init__(self) -> None:
        self.context = None

    def seed_context(self, context: Any) -> None:  # noqa: ANN401
        self.context = context


@pytest.mark.asyncio
@pytest.mark.parametrize("failed_row", [None, {"role": "bot_response", "content": "", "status": "failed"}])
async def test_errored_previous_turn_is_answered_on_every_call(failed_row) -> None:
    """SC-08: an earlier turn that failed before an answer (Node either drops its error row or
    sends it with a status). Every call, compacted or not, closes it with a placeholder answer,
    so it never sits next to the current question as a second open question."""
    previous = [
        {"role": "user_query", "content": "QUESTION A: how do SMS appointment reminders work?"},
        {"role": "bot_response", "content": "ANSWER A: enable reminders in Practice Settings."},
        {"role": "user_query", "content": "QUESTION B: list every open billing ticket"},
        *([failed_row] if failed_row else []),
    ]
    agent = _SeededAgent()
    await PipesHubAgentFactory._seed_conversation_history(agent, previous, _context())
    stored = [*await agent.context.messages(), UserMessage(content=CURRENT)]
    hooks = _chain()
    budget = _budget(32_000, 4_000)
    for turn in range(1, 9):
        _tool_round(stored, turn, 6_000)
        sent = await _send(hooks, stored, budget, turn)
        _assert_current_question_sent(sent)
        humans = [isinstance(m, UserMessage) and not m.injected for m in sent]
        assert not any(a and b for a, b in zip(humans, humans[1:])), f"call {turn}: two human turns in a row"
