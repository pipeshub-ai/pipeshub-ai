"""The completeness pass (ANSWER_COMPLETENESS_PASS): the gate, the request, and the POST_MODEL send-back."""

from __future__ import annotations

import pytest

from app.agent_loop_lib.agent.spec import AgentSpec, ModelSpec
from app.agent_loop_lib.core.context import RunContext
from app.agent_loop_lib.core.messages import AssistantMessage, UserMessage
from app.agent_loop_lib.core.scope import RunScope, TurnScope
from app.agent_loop_lib.core.types import Goal
from app.agent_loop_lib.runtime.runtime import AgentRuntime
from app.agents.agent_loop.completeness_pass import (
    REQUEST,
    completeness_pass_enabled,
    render_request,
    should_check,
)
from app.agents.agent_loop.context import AgentContext
from app.agents.agent_loop.hooks.completeness_pass import (
    COMPLETENESS_PASS_SENT,
    record_completeness_pass,
)
from tests.unit.agents.adapter.support.hook_helpers import run_post_model

_REF_TO_URL = {"ref1": "http://localhost:3000/record/rec-guide/preview#blockIndex=3"}
_DRAFT = "**Reseat the badge reader's ribbon cable, then power-cycle the door controller** [source](ref1)."


class _RefMapper:
    ref_to_url = _REF_TO_URL


def _context() -> AgentContext:
    context = AgentContext(
        org_id="o", user_id="u", user_email="u@example.com", query="Why did the east door reject badges?",
    )
    context.tool_state["citation_ref_mapper"] = _RefMapper()
    return context


def _scope(*, depth: int = 0, max_turns: int = 15) -> TurnScope:
    spec = AgentSpec(name="a", system_prompt="x", tool_names=["retrieval__search_internal_knowledge"],
                     model=ModelSpec(provider="scripted", model="m"), max_turns=max_turns)
    run = RunScope(identity=RunContext(role_name="a", model="m", spawn_depth=depth), spec=spec,
                   runtime=AgentRuntime(), goal=Goal(description="g"))
    return TurnScope(run=run, turn_index=1)


def test_off_unless_true(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANSWER_COMPLETENESS_PASS", raising=False)
    assert not completeness_pass_enabled()
    monkeypatch.setenv("HIGH_ACCURACY_MODE", "true")
    assert not completeness_pass_enabled()
    monkeypatch.setenv("ANSWER_COMPLETENESS_PASS", "true")
    assert completeness_pass_enabled()


@pytest.mark.parametrize(
    ("draft", "expected"),
    [
        (_DRAFT, True),
        ("The reader logs show retries [x](http://localhost:3000/record/rec-2/preview#blockIndex=0).", True),
        ("**The reader rejected badges after a firmware push.** No record is cited.", False),
        ("", False),
        ("The records do not mention who approved the firmware push [source](ref1).", False),
    ],
)
def test_checks_a_draft_that_relies_on_a_record_and_answers(draft: str, expected: bool) -> None:
    assert should_check(draft, _REF_TO_URL) is expected


def test_the_request_keeps_scope_and_asks_for_the_answer_only(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANSWER_SCOPE_GUARD", raising=False)
    assert render_request() == REQUEST
    assert "another purpose or another part" in REQUEST and "what a record does not say" in REQUEST
    assert REQUEST.endswith("Reply with the complete final answer only, with nothing before or after it.")


class TestSendBack:
    async def test_a_final_draft_goes_back_once(self) -> None:
        context = _context()
        hook = record_completeness_pass(context)
        first = await run_post_model(hook, AssistantMessage(content=_DRAFT), scope=_scope())
        assert isinstance(first.recovery_message, UserMessage) and first.recovery_message.injected
        assert first.recovery_message.content == render_request()
        assert context.tool_state[COMPLETENESS_PASS_SENT] is True
        second = await run_post_model(hook, AssistantMessage(content=_DRAFT), scope=_scope())
        assert second.recovery_message is None

    async def test_a_draft_another_check_sends_back_is_left_to_it(self) -> None:
        async def _other(ctx, next_fn) -> None:
            ctx.recovery_message = UserMessage(content="search for the gaps", injected=True)

        context = _context()
        hook = record_completeness_pass(context)

        async def _chain(ctx, _next) -> None:
            await hook(ctx, lambda: _other(ctx, None))

        ctx = await run_post_model(_chain, AssistantMessage(content=_DRAFT), scope=_scope())
        assert ctx.recovery_message.content == "search for the gaps"
        assert COMPLETENESS_PASS_SENT not in context.tool_state

    async def test_tool_calls_and_a_not_found_answer_are_not_sent_back(self) -> None:
        hook = record_completeness_pass(_context())
        with_tools = await run_post_model(hook, AssistantMessage(content=_DRAFT), tool_calls=[object()], scope=_scope())
        assert with_tools.recovery_message is None
        absent = await run_post_model(
            hook, AssistantMessage(content="The records do not say who approved it [source](ref1)."), scope=_scope(),
        )
        assert absent.recovery_message is None

    @pytest.mark.parametrize("scope", [_scope(depth=1), _scope(max_turns=2)])
    async def test_sub_agents_and_the_last_turns_keep_the_draft(self, scope: TurnScope) -> None:
        ctx = await run_post_model(record_completeness_pass(_context()), AssistantMessage(content=_DRAFT), scope=scope)
        assert ctx.recovery_message is None


def test_scope_guard_keeps_other_scopes_to_one_line(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANSWER_SCOPE_GUARD", raising=False)
    off = render_request()
    assert off == REQUEST
    monkeypatch.setenv("HIGH_ACCURACY_MODE", "true")
    assert render_request() == REQUEST
    monkeypatch.setenv("ANSWER_SCOPE_GUARD", "true")
    on = render_request()
    assert on.startswith(off) and "another scope gives at most one attributed sentence" in on
    # A same-scope record that corrects the cited one may still change the answer.
    assert "says it corrects, updates or replaces the record the answer cites" in on
    # Same-scope records that disagree without a revision are both given, not classified.
    assert "neither revises the other, give both values, each with its source, and say they disagree" in on
