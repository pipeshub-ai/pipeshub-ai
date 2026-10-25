"""The production PRE_MODEL chain (`PipesHubAgentFactory._build_hooks`) on the
layouts real chats produce, call after call.

The customer case: a chat whose current question comes after an earlier turn,
then a long tool loop with large `fetch_record` results. Before the fix the
chain kept the earlier question and dropped the current one, and the model
answered the earlier question (docs/context-engineering.md, CE-01).
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from app.agent_loop_lib.agent.hook_dispatch import dispatch_pre_model
from app.agent_loop_lib.context.base import ContextBudget
from app.agent_loop_lib.context.errors import ContextBudgetExceeded
from app.agent_loop_lib.core.context import RunContext
from app.agent_loop_lib.core.messages import (
    AssistantMessage,
    ImagePart,
    TextPart,
    ToolCall,
    ToolMessage,
    UserMessage,
)
from app.agent_loop_lib.core.scope import RunScope, TurnScope
from app.agent_loop_lib.core.tokens import count_tokens
from app.agent_loop_lib.core.types import Goal
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


def _chain(context: AgentContext | None = None, *, registry: Any = None):  # noqa: ANN202, ANN401
    return PipesHubAgentFactory._build_hooks(
        context or _context(), transport_registry=registry or _Registry(), model_name="m",
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



SEARCH = "knowledgegraph__search"
_TAIL = "\nCite each fact with its [refN] id; ids r9 and r10 were unavailable."


def _tailed_round(stored: list, n: int, chars: int, tool: str) -> str:
    call = ToolCall(id=f"t{n}", name=tool, arguments={"query": f"sms reminders {n}"})
    stored.append(AssistantMessage(content=[TextPart(text=f"Searching {n}.")], tool_calls=[call]))
    stored.append(ToolMessage(content=_text(chars - len(_TAIL)) + _TAIL, tool_call_id=call.id))
    return call.id


def _result(sent: list, call_id: str) -> ToolMessage | None:
    return next((m for m in sent if isinstance(m, ToolMessage) and m.tool_call_id == call_id), None)


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", [SEARCH, FETCH])
async def test_a_small_window_shortens_the_newest_result_instead_of_dropping_it(tool) -> None:
    """SC-11: the nightly 24K-window chat. The message budget was 4293 tokens;
    budget reduction capped a 6778-token search result to 4412, still over the
    budget, so the sliding window evicted it with its call down to the
    29-token question, and the model never saw a result on any of 15 calls."""
    hooks = _chain()
    budget = _budget(24_000, 15_414)
    assert budget.effective_max_tokens == 4_293
    stored = [UserMessage(content=CURRENT)]
    call_id = _tailed_round(stored, 1, 27_100, tool)
    assert count_tokens(stored) > 6_700

    sent = await _send(hooks, stored, budget, 1)

    result = _result(sent, call_id)
    assert result is not None, "the newest result was evicted with its call"
    assert result.content.endswith(_TAIL), "the cut must keep the result's closing instructions"
    assert count_tokens(sent) <= budget.effective_max_tokens
    _assert_current_question_sent(sent)


@pytest.mark.asyncio
@pytest.mark.parametrize("tool", [SEARCH, FETCH])
async def test_a_small_window_evicts_older_results_and_keeps_the_newest(tool) -> None:
    """SC-12: call after call at that window, each call carries the result the
    model just asked for; the older ones go, oldest first."""
    hooks = _chain()
    budget = _budget(24_000, 15_414)
    stored = [UserMessage(content=CURRENT)]
    call_ids: list[str] = []
    for turn in range(1, 7):
        call_ids.append(_tailed_round(stored, turn, 27_100, tool))
        sent = await _send(hooks, stored, budget, turn)
        assert _result(sent, call_ids[-1]) is not None, f"call {turn}: the newest result was evicted"
        assert count_tokens(sent) <= budget.effective_max_tokens
        _assert_current_question_sent(sent)
    kept = [cid for cid in call_ids if _result(sent, cid) is not None]
    assert kept == call_ids[len(call_ids) - len(kept):], f"older results outlived newer ones: {kept}"
    assert call_ids[0] not in kept


@pytest.mark.asyncio
@pytest.mark.parametrize("tools", [
    [FETCH, SEARCH],
    [FETCH, FETCH, SEARCH],
    [SEARCH, FETCH, SEARCH],
    [SEARCH, FETCH, FETCH],
], ids=["fetch-search", "fetch-fetch-search", "search-fetch-search", "search-fetch-fetch"])
async def test_a_small_window_keeps_the_newest_call_whichever_tools_came_before(tools) -> None:
    """SC-13: protected groups (a fetch's [refN] text) outlived unprotected
    ones, so a search after a fetch was evicted and the older fetch sent: the
    same lost result, one call later. The newest call's result goes last."""
    hooks = _chain()
    budget = _budget(24_000, 15_414)
    stored = [UserMessage(content=CURRENT)]
    for turn, tool in enumerate(tools, start=1):
        call_id = _tailed_round(stored, turn, 27_100, tool)
        sent = await _send(hooks, stored, budget, turn)
        assert _result(sent, call_id) is not None, f"call {turn} ({tool}): the newest result was evicted"
        assert count_tokens(sent) <= budget.effective_max_tokens
        _assert_current_question_sent(sent)

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


# --- P2: stable and cheap (CE-12, CE-13, CE-23) ---------------------------------


class _CountingSummaries:
    """An LLM-like summariser: a different text on every call, and a record
    of exactly which messages each call was asked to summarise."""

    def __init__(self) -> None:
        self.inputs: list[list[str]] = []

    def resolve(self, _provider: str) -> "_CountingSummaries":
        return self

    async def complete(self, messages: list, **_: Any) -> Any:  # noqa: ANN401
        self.inputs.append([messages[0].content])
        number = len(self.inputs)

        class _Response:
            message = AssistantMessage(content=[TextPart(text=f"summary #{number} of earlier work")])

        return _Response()


def _run_scope() -> RunScope:
    return RunScope(identity=RunContext(role_name="assistant", model="m"), spec=None, runtime=None,
                    goal=Goal(description=CURRENT))


async def _send_in_run(hooks, stored: list, budget: ContextBudget, turn: int, run: RunScope) -> list:
    return await dispatch_pre_model(
        hooks, stored, budget, turn_index=turn, max_turns=60, scope=TurnScope(run=run, turn_index=turn),
    )


def _wire(messages: list) -> list[str]:
    return [json.dumps(m.model_dump(mode="json"), sort_keys=True) for m in messages]


def _search_round(stored: list, n: int, chars: int, *, tool: str = "knowledgegraph__search") -> None:
    call = ToolCall(id=f"s{n}", name=tool, arguments={"query": f"q{n}"})
    stored.append(AssistantMessage(content=[TextPart(text=f"Searching {n}.")], tool_calls=[call]))
    stored.append(ToolMessage(content=f"[ref{n}] " + _text(chars), tool_call_id=call.id))


def _is_prefix(earlier: list[str], later: list[str]) -> bool:
    return later[: len(earlier)] == earlier


@pytest.mark.asyncio
async def test_consecutive_calls_reuse_the_sent_prefix() -> None:
    """SC-09 / PT-05 on the customer's layout: an earlier turn, the current
    question, then one 40K-char search result per call at a 200K window.
    The current question is sent on every call, and a later call extends the
    earlier one instead of re-shaping it. Before, L4 fired only on every
    fifth turn and L3 rewrote a result on every call: 19 of 29 calls broke
    the prefix."""
    hooks = _chain()
    budget = _budget(200_000, 20_000)
    run = _run_scope()
    stored = [*_history(1), UserMessage(content=CURRENT)]
    previous: list[str] | None = None
    breaks = 0
    for turn in range(30):
        _search_round(stored, turn, 40_000)
        sent = await _send_in_run(hooks, stored, budget, turn, run)
        _assert_current_question_sent(sent)
        wire = _wire(sent)
        if previous is not None and not _is_prefix(previous, wire):
            breaks += 1
        previous = wire
    assert breaks <= 4


def _as_blocks(message: Any) -> tuple:  # noqa: ANN401
    """A LangChain message as Anthropic reads it: a string is one text block."""
    content = message.content
    if isinstance(content, str):
        content = [{"type": "text", "text": content}] if content else []
    return (message.type, json.dumps(content, sort_keys=True), json.dumps(getattr(message, "tool_calls", None)))


@pytest.mark.asyncio
async def test_cache_breakpoint_sits_on_content_the_next_call_resends() -> None:
    """TP-05: the message breakpoint Anthropic-style caching puts on the last
    message of call n is, with everything before it, resent unchanged by
    call n+1, so the next call reads it from the cache."""
    from app.agents.agent_loop.converters import convert_messages_to_langchain
    from app.agents.agent_loop.prompt_cache.strategies import (
        AnthropicCacheStrategy,
        without_cache_markers,
    )

    strategy = AnthropicCacheStrategy({"type": "ephemeral"}, top_level=False)
    hooks = _chain()
    budget = _budget(200_000, 20_000)
    run = _run_scope()
    stored = [*_history(1), UserMessage(content=CURRENT)]
    previous = None
    reused = 0
    for turn in range(20):
        _search_round(stored, turn, 40_000)
        sent = await _send_in_run(hooks, stored, budget, turn, run)
        marked, _ = strategy.finalize(convert_messages_to_langchain(sent))
        unmarked = [_as_blocks(m) for m in without_cache_markers(marked)]
        if previous is not None and unmarked[: len(previous)] == previous:
            reused += 1
        previous = unmarked
    assert reused >= 16


@pytest.mark.asyncio
async def test_same_input_twice_is_shaped_byte_identically() -> None:
    """UC-05: the same history sent twice in a run is shaped identically,
    although the summariser answers differently each time it is called."""
    summaries = _CountingSummaries()
    hooks = _chain(registry=summaries)
    budget = _budget(8_000, 2_000)
    run = _run_scope()
    stored = [*_history(1), UserMessage(content=CURRENT)]
    for n in range(14):
        _search_round(stored, n, 4_000)

    first = await _send_in_run(hooks, stored, budget, 14, run)
    calls_after_first = len(summaries.inputs)
    second = await _send_in_run(hooks, stored, budget, 14, run)

    assert calls_after_first >= 1, "the scenario must reach the LLM summariser"
    assert _wire(first) == _wire(second)
    assert len(summaries.inputs) == calls_after_first


@pytest.mark.asyncio
async def test_the_summariser_never_sees_the_same_messages_twice() -> None:
    """CE-12 cost guard: over 30 consecutive calls the LLM summariser runs at
    most once per new run of messages, and never on a summary it wrote.
    Before, it summarised the whole middle again on every call over the
    trigger, twice when both auto-compact phases fired (48 calls)."""
    summaries = _CountingSummaries()
    hooks = _chain(registry=summaries)
    budget = _budget(8_000, 2_000)
    run = _run_scope()
    stored = [*_history(1), UserMessage(content=CURRENT)]
    for turn in range(30):
        _search_round(stored, turn, 4_000)
        sent = await _send_in_run(hooks, stored, budget, turn, run)
        _assert_current_question_sent(sent)

    assert summaries.inputs, "the scenario must reach the LLM summariser"
    assert len(summaries.inputs) < 30
    # Only what follows the prompt's header: the header names [ref1] as an example.
    conversations = [prompt.split("CONVERSATION TO SUMMARIZE:", 1)[1] for (prompt,) in summaries.inputs]
    summarised_refs = [ref for text in conversations for ref in re.findall(r"\[ref(\d+)\]", text)]
    assert len(summarised_refs) == len(set(summarised_refs)), "a result was summarised twice"
    assert not any("summary #" in text for text in conversations)


@pytest.mark.asyncio
async def test_protected_fetch_citations_survive_every_layer() -> None:
    """UC-03 (CE-13): eleven fetch_record results at a 200K window push the
    chain past extractive and summarising compaction. Every [refN] the model
    read is still sent; before, only the newest three survived L6."""
    hooks = _chain()
    budget = _budget(200_000, 20_000)
    stored = [*_history(1), UserMessage(content=CURRENT)]
    for n in range(11):
        call = ToolCall(id=f"f{n}", name=FETCH, arguments={"record_ids": [f"r{n}"]})
        stored.append(AssistantMessage(content=[TextPart(text=f"Reading manual {n}.")], tool_calls=[call]))
        stored.append(ToolMessage(content=_text(60_000) + f"\n[ref{n}] end of manual {n}", tool_call_id=call.id))

    sent = await _send(hooks, stored, budget, 11)

    assert count_tokens(stored) > budget.trigger_tokens(0.85), "the chain must reach L6/L7"
    blob = "\n".join(_wire(sent))
    assert [n for n in range(11) if f"[ref{n}] end of manual {n}" not in blob] == []
    _assert_current_question_sent(sent)


@_SETTINGS
@given(
    earlier_turns=st.integers(min_value=0, max_value=3),
    calls=st.integers(min_value=2, max_value=14),
    result_chars=st.sampled_from([2_000, 8_000, 30_000]),
    window=st.sampled_from([8_000, 32_000, 200_000]),
    tool=st.sampled_from([FETCH, "knowledgegraph__search"]),
)
@pytest.mark.asyncio
async def test_prefix_is_stable_unless_a_layer_compacts_anew(earlier_turns, calls, result_chars, window, tool) -> None:
    """PT-05 on the production chain: when call n+1 only appended messages
    and no layer compacted anything new (the run's memo did not grow, and
    neither eviction nor the last-resort guard acted), the list sent on
    call n is a prefix of the one sent on call n+1."""
    from app.agent_loop_lib.context.compaction_memo import COMPACTION_MEMO

    hooks = _chain()
    budget = _budget(window, window // 5)
    run = _run_scope()
    stored = [*_history(earlier_turns), UserMessage(content=CURRENT)]
    previous: list[str] | None = None
    for turn in range(calls):
        _search_round(stored, turn, result_chars, tool=tool)
        entries = run.get(COMPACTION_MEMO).entries
        handler = _Records()
        layers_logger = logging.getLogger("app.agent_loop_lib.hooks.middleware.builtin")
        level = layers_logger.level
        layers_logger.addHandler(handler)
        layers_logger.setLevel(logging.INFO)
        try:
            sent = await _send_in_run(hooks, stored, budget, turn, run)
        except ContextBudgetExceeded:
            return
        finally:
            layers_logger.removeHandler(handler)
            layers_logger.setLevel(level)
        wire = _wire(sent)
        last_resort = any(r.startswith(("sliding_window", "synthesis_guard")) for r in handler.lines)
        if previous is not None and run.get(COMPACTION_MEMO).entries == entries and not last_resort:
            assert _is_prefix(previous, wire)
        previous = wire


# --- Batched compaction with a low-water mark (context/watermark.py) ----------


def _common_prefix(earlier: list[str], later: list[str]) -> int:
    n = 0
    while n < min(len(earlier), len(later)) and earlier[n] == later[n]:
        n += 1
    return n


async def _prefix_reuse(window: int, prompt: int, result_chars: int, tool: str) -> tuple[int, float]:
    """Thirty consecutive calls of one run, one tool result each: how many
    calls did not resend the previous call's list as their prefix, and the
    mean share of each call's tokens that prefix covered (what a provider's
    prompt cache can reuse)."""
    hooks = _chain()
    budget = _budget(window, prompt)
    run = _run_scope()
    stored = [*_history(1), UserMessage(content=CURRENT)]
    previous: list[str] | None = None
    breaks = 0
    shares: list[float] = []
    for turn in range(30):
        _search_round(stored, turn, result_chars, tool=tool)
        sent = await _send_in_run(hooks, stored, budget, turn, run)
        _assert_current_question_sent(sent)
        assert count_tokens(sent) <= budget.effective_max_tokens
        wire = _wire(sent)
        if previous is not None:
            shared = _common_prefix(previous, wire)
            breaks += shared < len(previous)
            shares.append(count_tokens(sent[:shared]) / count_tokens(sent))
        previous = wire
    return breaks, sum(shares) / len(shares)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("window", "prompt", "result_chars", "tool", "max_breaks", "min_share"),
    [
        # Before: 28/29 calls broke the prefix, mean cached share 0.33.
        pytest.param(8_000, 1_600, 2_000, "knowledgegraph__search", 10, 0.6, id="8K"),
        # Before: 29/29, 0.31. A result is a quarter of this budget and the
        # summarised tail half of it, so every other call must compact.
        pytest.param(8_000, 2_000, 4_000, "knowledgegraph__search", 16, 0.35, id="8K-large-results"),
        # Before: 27/29, 0.27.
        pytest.param(32_000, 6_400, 8_000, "knowledgegraph__search", 10, 0.55, id="32K"),
        # Before: 21/29, 0.22: the window evicted one protected fetch per call.
        pytest.param(200_000, 20_000, 60_000, FETCH, 6, 0.65, id="200K-protected-fetch"),
    ],
)
async def test_compaction_comes_in_batches_so_most_calls_only_append(
    window: int, prompt: int, result_chars: int, tool: str, max_breaks: int, min_share: float,
) -> None:
    """A layer that has to compact goes down to its low-water mark in one
    pass, and a pass too small to be worth a batch waits; the calls between
    batches resend the previous call unchanged and append to it. Before,
    the turns each layer keeps already sat over its trigger, so every call
    compacted the one result that had just aged out (or the window evicted
    one more fetch) and rewrote the request near its end."""
    breaks, share = await _prefix_reuse(window, prompt, result_chars, tool)

    assert breaks <= max_breaks, f"{breaks}/29 calls broke the sent prefix"
    assert share >= min_share, f"mean cached-prefix share {share:.3f}"


class _Records(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.INFO)
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(record.getMessage())


def test_factory_and_control_plane_build_one_pipeline(monkeypatch) -> None:
    """CE-23: the chat factory and ControlPlane get their PRE_MODEL layers
    from the same function, in the same order."""
    import asyncio

    from app.agent_loop_lib.context import pipeline as pipeline_module
    from app.agent_loop_lib.control_plane.config import ControlPlaneConfig
    from app.agent_loop_lib.control_plane.control_plane import ControlPlane

    built: list[list[str]] = []
    original = pipeline_module.build_context_pipeline

    def _spy(profile):  # noqa: ANN202
        layers = original(profile)
        built.append([layer.__qualname__.split(".<locals>")[0] for layer in layers])
        return layers

    monkeypatch.setattr(pipeline_module, "build_context_pipeline", _spy)
    monkeypatch.setattr("app.agents.agent_loop.factory.build_context_pipeline", _spy)

    _chain()
    asyncio.run(ControlPlane(ControlPlaneConfig(hooks=["context_engine"])).start())

    factory_layers, control_plane_layers = built
    assert factory_layers[0] == "shape_image_injection"
    # Chat runs two auto-compact tails (gentle, then aggressive), the library one.
    assert list(dict.fromkeys(factory_layers[1:])) == list(dict.fromkeys(control_plane_layers))
    assert control_plane_layers[-1] == "shape_context_invariants"
