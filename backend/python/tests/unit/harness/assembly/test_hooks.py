"""PA-3: the legacy hooks that change behaviour, on the durable path.

The oracle is the legacy wiring itself: the registry `PipesHubAgentFactory`
builds for the same environment, and (for the loop-level hooks) the model
requests the legacy route sends for the same script.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from harness_core.kernel.core.messages import ToolCall
from harness_core.kernel.durable.ports import ModelCall, ToolContext
from harness_core.kernel.fold import Invocation, RunState
from harness_core.kernel.hooks.events import HookEvent

from . import world as w
from .test_parity import _durable_turn_one, _full_grant, _legacy

if TYPE_CHECKING:
    from collections.abc import Iterator

CTX = ToolContext("run-1", w.ORG, "t1.0", "att-1", "key")


@pytest.fixture(autouse=True)
def _deployment(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("PIPESHUB_ENABLE_FINAL_ANSWER", "false")
    with w.deployment():
        yield


def _middleware(hooks: Any) -> dict[str, list[str]]:  # noqa: ANN401 - HookRegistry
    return {
        event.value: [mw.__qualname__ for _, mw in hooks.on(event)._stack]
        for event in HookEvent
        if event != HookEvent.PRE_MODEL_CALL
    }


async def test_hk1_the_durable_registry_is_the_legacy_registry_minus_what_is_not_ported() -> None:
    from app.harness.assembly.hooks import DURABLE_ONLY, NOT_PORTED

    agent = w.agent_doc()
    spec = w.pinned_spec(**_full_grant(agent))
    runtime, _, _ = await _durable_turn_one(w.Services(agent=agent), spec, "quick", "find the bug")
    _legacy_agent, legacy_runtime, _ = await _legacy(w.Services(agent=agent), spec, "quick", "find the bug")
    durable = _middleware(runtime.shaper._hooks.registry)  # type: ignore[union-attr]
    legacy = _middleware(legacy_runtime.hooks)
    # WP-23: the durable path's own hooks, each named in DURABLE_ONLY with its reason, and the last of their stage.
    added = {key.split(" (")[0]: key.split("(")[1].rstrip(")").lower() for key in DURABLE_ONLY}
    for name, event in added.items():
        assert durable[event][-1].startswith(name), (event, durable[event])
        assert not any(n.startswith(name) for n in legacy[event])
        durable[event] = [n for n in durable[event] if not n.startswith(name)]
    # QAFIX4 B2, LONGTASK-F4: the shapers that cut history without a journal entry (and the legacy loop's report of
    # what they took off) stay legacy-only.
    unjournaled = (
        "auto_compact", "legacy_flow_guard", "loop_compaction", "sliding_window", "deterministic_compact",
        "artifact_compaction", "tool_result_clearing", "synthesis_guard", "report_context_reduction",
    )
    expected = {
        event: [name for name in names if not any(cut in name for cut in unjournaled)]
        for event, names in legacy.items()
    }
    assert durable == expected
    for key in (
        "shape_artifact_compaction (L2)", "shape_tool_result_clearing (L3)", "shape_loop_compaction (L4)",
        "shape_sliding_window (L5)", "shape_deterministic_compact (L6)", "shape_synthesis_guard (L8)",
    ):
        assert key in NOT_PORTED
    # UX3: today's loop refuses what the durable kernel asks about (FlowGuardedPolicy), so the guard is legacy-only.
    assert any("legacy_flow_guard" in n for n in legacy["pre_tool_use"])
    assert "legacy_flow_guard (PRE_TOOL_USE)" in NOT_PORTED
    assert any("auto_compact" in n for n in legacy["pre_model"]), "the legacy registry has L7"
    assert "shape_auto_compact (L7a/L7b)" in NOT_PORTED
    assert durable["pre_model"], "the context shapers run"
    assert any("citation_tracking" in n for n in durable["post_tool_use"])
    assert any("completion_gate" in n for n in durable["post_model"])


async def test_hk2_a_tool_failing_three_turns_running_is_denied_and_the_streak_survives_a_resume(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def broken(self: object, jql: str) -> str:
        raise RuntimeError("jira is down")

    w.replace_tool(monkeypatch, w.FakeJira, "search_issues", broken)
    agent = w.agent_doc()
    spec = w.pinned_spec(**_full_grant(agent))
    runtime, slots, _ = await _durable_turn_one(w.Services(agent=agent), spec, "quick", "find the bug")
    shaper = runtime.shaper
    state = RunState(run_id="run-1", slots=slots)
    for turn in (1, 2, 3):
        _ = await shaper.shape(ModelCall([], None, None, slots), turn, state)  # type: ignore[union-attr]
        failed = await runtime.tools.execute(
            ToolCall(id=f"c{turn}", name="jira__search_issues", arguments={"jql": "x"}), CTX
        )
        assert failed.status == "error" and "jira is down" in str(failed.content)
    _ = await shaper.shape(ModelCall([], None, None, slots), 4, state)  # type: ignore[union-attr]
    denied = await runtime.tools.execute(
        ToolCall(id="c4", name="jira__search_issues", arguments={"jql": "x"}), CTX
    )
    assert "is paused for 2 turns" in str(denied.content)

    # Another worker: the streak comes back from the journaled outcomes.
    resumed, _, _ = await _durable_turn_one(w.Services(agent=agent), spec, "quick", "find the bug")
    journaled = RunState(
        run_id="run-1",
        slots=slots,
        turn=3,
        invocations={
            f"t{t}.0": Invocation(
                invocation_id=f"t{t}.0", turn=t, index=0, call_id=f"c{t}",
                name="jira__search_issues", args_digest="sha256:x", state="completed", status="error",
            )
            for t in (1, 2, 3)
        },
    )
    fresh_shaper = resumed.shaper
    fresh_shaper._scope = None  # type: ignore[union-attr] - a new attempt has not begun
    _ = await fresh_shaper.shape(ModelCall([], None, None, slots), 4, journaled)  # type: ignore[union-attr]
    again = await resumed.tools.execute(
        ToolCall(id="c5", name="jira__search_issues", arguments={"jql": "x"}), CTX
    )
    assert "is paused for 2 turns" in str(again.content)


def _failed_turns(*turns: int) -> dict[str, Invocation]:
    return {
        f"t{t}.0": Invocation(
            invocation_id=f"t{t}.0", turn=t, index=0, call_id=f"c{t}",
            name="jira__search_issues", args_digest="sha256:x", state="completed", status="error",
        )
        for t in turns
    }


async def test_dp2_a_resumed_dispatch_opens_the_scope_shape_would_have_built() -> None:
    """PA-12: an attempt that starts with turn 4's tools (it never shapes turn
    4) opens the same scope first, so the tool hooks see the calls."""
    agent = w.agent_doc()
    spec = w.pinned_spec(**_full_grant(agent))
    shaped_run, slots, _ = await _durable_turn_one(w.Services(agent=agent), spec, "quick", "find the bug")
    state = RunState(run_id="run-1", slots=slots, turn=4)
    _ = await shaped_run.shaper.shape(ModelCall([], None, None, slots), 4, state)  # type: ignore[union-attr]
    shaped = shaped_run.shaper.tool_scope(ToolCall(id="c", name="jira__search_issues"))  # type: ignore[union-attr]

    resumed, _, _ = await _durable_turn_one(w.Services(agent=agent), spec, "quick", "find the bug")
    fresh = resumed.shaper
    fresh._scope = fresh._turn = None  # type: ignore[union-attr] - a new attempt has not begun
    assert fresh.tool_scope(ToolCall(id="c", name="jira__search_issues")) is None  # type: ignore[union-attr]
    await fresh.enter(4, state)  # type: ignore[union-attr]
    entered = fresh.tool_scope(ToolCall(id="c", name="jira__search_issues"))  # type: ignore[union-attr]
    assert entered is not None
    assert entered.turn.turn_index == shaped.turn.turn_index == 3
    assert entered.tool_path == shaped.tool_path and entered.tool_path
    assert entered.turn.run.goal.description == shaped.turn.run.goal.description
    assert entered.turn.run.visible_tools == shaped.turn.run.visible_tools
    assert entered.turn.run.spec.tool_names == shaped.turn.run.spec.tool_names
    # Entering the turn it is already in changes nothing.
    turn_scope = fresh._turn  # type: ignore[union-attr]
    await fresh.enter(4, state)  # type: ignore[union-attr]
    assert fresh._turn is turn_scope  # type: ignore[union-attr]


async def test_dp2_the_error_tracker_streak_holds_on_a_resumed_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """HK-2 on an attempt that resumes at turn 4's dispatch: the fourth call
    is denied although no turn was shaped in this attempt."""

    async def broken(self: object, jql: str) -> str:
        raise RuntimeError("jira is down")

    w.replace_tool(monkeypatch, w.FakeJira, "search_issues", broken)
    agent = w.agent_doc()
    spec = w.pinned_spec(**_full_grant(agent))
    resumed, slots, _ = await _durable_turn_one(w.Services(agent=agent), spec, "quick", "find the bug")
    fresh = resumed.shaper
    fresh._scope = fresh._turn = None  # type: ignore[union-attr] - a new attempt has not begun
    journaled = RunState(run_id="run-1", slots=slots, turn=4, invocations=_failed_turns(1, 2, 3))
    await fresh.enter(4, journaled)  # type: ignore[union-attr]
    denied = await resumed.tools.execute(
        ToolCall(id="c4", name="jira__search_issues", arguments={"jql": "x"}), CTX
    )
    assert "is paused for 2 turns" in str(denied.content)


async def test_sync3_the_repeated_read_memory_survives_a_resume() -> None:
    """Main's RepeatedReadGuard on a resumed attempt: an MCP read that already ran twice with the same arguments
    (journaled) is refused on the next turn, as on an uninterrupted run."""
    from app.agent_loop_lib.hooks.middleware.context import ToolCallContext
    from app.agent_loop_lib.hooks.middleware.decisions import PreDecision
    from harness_core.effects.types import args_digest
    from tests.unit.agents.adapter.test_repeated_reads import _adapter, _Server

    agent = w.agent_doc()
    spec = w.pinned_spec(**_full_grant(agent))
    resumed, slots, _ = await _durable_turn_one(w.Services(agent=agent), spec, "quick", "find the bug")
    fresh = resumed.shaper
    read = _adapter(fresh._env.context, _Server(), "list_issues", read_only=True)  # type: ignore[union-attr]
    fresh._registry.register_tool(read)  # type: ignore[union-attr]
    fresh._scope = fresh._turn = None  # type: ignore[union-attr] - a new attempt has not begun
    ran = {
        f"t{t}.0": Invocation(
            invocation_id=f"t{t}.0", turn=t, index=0, call_id=f"c{t}", name=read.name,
            args_digest=args_digest({"team": "ENG"}), state="completed", status="ok",
        )
        for t in (1, 2)
    }
    await fresh.enter(3, RunState(run_id="run-1", slots=slots, turn=3, invocations=ran))  # type: ignore[union-attr]

    async def _next() -> None:
        return None

    call = ToolCall(id="c3", name=read.name, arguments={"team": "ENG"})
    ctx = ToolCallContext(tool_path=read.path, tool_input={"team": "ENG"}, scope=fresh.tool_scope(call))  # type: ignore[union-attr]
    await fresh._hooks.repeated_reads.pre_tool_use(ctx, _next)  # type: ignore[union-attr]
    assert ctx.decision == PreDecision.DENY


async def test_hk5_fetch_record_is_offered_from_the_first_request_and_stays() -> None:
    """R4: the loop granted fetch_record once records were known, growing the list mid-run; it is in the chat's
    list from its first request now, and a search that returns records changes nothing."""
    agent = w.agent_doc(knowledge=[])
    spec = w.pinned_spec(**_full_grant(agent))
    runtime, slots, shaped = await _durable_turn_one(w.Services(agent=agent), spec, "quick", "find the bug")
    first = [t.name for t in shaped.tools or []]
    assert "knowledgegraph__fetch_record" in first
    runtime.presenter._context.tool_state["known_record_ids"] = {"rec-1"}  # type: ignore[union-attr]
    _ = await runtime.tools.execute(
        ToolCall(id="c1", name="jira__search_issues", arguments={"jql": "x"}), CTX
    )
    state = RunState(run_id="run-1", slots=slots)
    later = await runtime.shaper.shape(ModelCall([], None, None, slots), 2, state)  # type: ignore[union-attr]
    assert [t.name for t in later.tools or []] == first
    assert runtime.tools.describe("knowledgegraph__fetch_record").effect == "read"  # type: ignore[union-attr]


async def test_hk6_turn_zero_hooks_add_their_notes_to_the_journaled_goal() -> None:
    """PRE_TURN at turn 0 (the follow-up note here) mutates the legacy goal for
    the whole run; the durable goal step journals the same constraints."""
    from app.agent_loop_lib.agent import hook_dispatch
    from app.agent_loop_lib.core.context import RunContext as Identity
    from app.agent_loop_lib.core.scope import RunScope, TurnScope
    from app.agents.agent_loop.factory import PipesHubAgentFactory
    from app.harness.assembly.assembler import PipesHubRuntimeAssembler
    from app.harness.assembly.context import RunContext
    from app.harness.assembly.tool_environment import build_tool_environment
    from harness_core.kernel.durable.slots import PRE_RUN_SLOT_PREFIX
    from harness_core.testing import ModelScript, Reply, ScriptedModel, Turn
    from harness_core.testing.durable import ScriptedModelPort
    from tests.unit.agents.adapter.conftest import FakeChatModel

    previous = [
        {"role": "user_query", "content": "Draft the Jira ticket for the outage"},
        {
            "role": "bot_response",
            "content": "Here is the draft. Shall I create it?",
            "tool_results": [{"tool_name": "jira__search_issues", "result": "3 issues", "status": "success"}],
        },
    ]
    agent = w.agent_doc()
    spec = w.pinned_spec(**_full_grant(agent))
    query = "yes do it"
    port = ScriptedModelPort(ScriptedModel(ModelScript(id="p", turns=[Turn(reply=Reply(text="ok"))])))
    claim = w.claim(spec, query=query, previous=previous)
    runtime = await PipesHubRuntimeAssembler(w.Services(agent=agent), model_factory=w.model_factory(port)).build(claim)
    slots: dict[str, Any] = {}
    for step in runtime.pre_run:
        slots[PRE_RUN_SLOT_PREFIX + step.key] = await step.run(query)

    services = w.Services(agent=agent)
    run = RunContext.from_claim(w.claim(spec, query=query, previous=previous))
    env = await build_tool_environment(run, services, await w.model_factory(port)(run, services))
    legacy_agent, legacy_runtime, goal, _ = await PipesHubAgentFactory().create(
        env.context, FakeChatModel(), "quick", query=query, model_name="gpt-x", model_key="model-1"
    )
    scope = RunScope(
        identity=Identity(role_name="pipeshub-agent", model="gpt-x"),
        spec=legacy_agent.spec, runtime=legacy_runtime, goal=goal,
    )
    await hook_dispatch.dispatch_pre_turn(legacy_runtime.hooks, 0, scope=TurnScope(run=scope, turn_index=0))

    constraints = slots["pre_run.goal"]["constraints"]
    # Main #4534 retired the follow-up note (the person's own words are the request); both loops still agree.
    assert constraints == goal.constraints


async def test_s5b_decision_logs_name_the_run_and_carry_no_content(caplog: pytest.LogCaptureFixture) -> None:
    """Ground rule 9: the pre-run steps, the disclosure decision and the run
    scope's hooks are logged with the run's correlation ids and no content."""
    import logging

    caplog.set_level(logging.DEBUG)
    sentinel = "SENTINEL-QUERY-7731"
    agent = w.agent_doc()
    spec = w.pinned_spec(**_full_grant(agent))
    _ = await _durable_turn_one(w.Services(agent=agent), spec, "react", f"find {sentinel} please")
    events = {getattr(r, "harness_event", None): r for r in caplog.records}
    for name in ("prerun.goal", "prerun.composition", "tools.disclosure", "hook.run_started", "run.catalog"):
        record = events.get(name)
        assert record is not None, f"{name} is logged"
        fields = record.harness  # type: ignore[attr-defined]
        assert (fields["runId"], fields["orgId"], fields["attemptId"]) == ("run-1", w.ORG, "att-1"), name
    harness_lines = [r for r in caplog.records if getattr(r, "harness_event", None)]
    assert all(sentinel not in r.getMessage() and sentinel not in str(getattr(r, "harness", "")) for r in harness_lines)


async def test_c2_an_unknown_tool_runs_the_hooks_and_is_disabled_after_three_turns() -> None:
    """Legacy runs PRE/POST_TOOL_USE on a call to a tool the run lacks (path
    `/unresolved/<name>`), so its error tracker answers the fourth turn's call
    with "paused for 2 turns"; the durable port does the same, and the
    streak comes back from the journal on another worker. Nothing executes."""
    from harness_core.tools.executor import ToolExecutor

    agent = w.agent_doc()
    spec = w.pinned_spec(**_full_grant(agent))
    runtime, slots, _ = await _durable_turn_one(w.Services(agent=agent), spec, "quick", "find the bug")
    legacy_agent, legacy_runtime, _ = await _legacy(w.Services(agent=agent), spec, "quick", "find the bug")
    oracle = ToolExecutor(legacy_runtime.tool_registry, legacy_runtime.hooks)
    shaper = runtime.shaper
    state = RunState(run_id="run-1", slots=slots)
    answers: list[tuple[str, str]] = []
    for turn in (1, 2, 3, 4):
        _ = await shaper.shape(ModelCall([], None, None, slots), turn, state)  # type: ignore[union-attr]
        call = ToolCall(id=f"u{turn}", name="nosuch__tool", arguments={})
        durable = await runtime.tools.execute(call, CTX)
        legacy = await oracle.call_tool(call, scope=_legacy_scope(legacy_agent, legacy_runtime, turn))
        assert durable.status == "error"
        answers.append((str(durable.content), str(legacy.content)))
    assert [d for d, _ in answers] == [lg for _, lg in answers]
    assert answers[0][0] == "Unknown tool: nosuch__tool"
    assert "is paused for 2 turns" in answers[3][0]

    resumed, _, _ = await _durable_turn_one(w.Services(agent=agent), spec, "quick", "find the bug")
    journaled = RunState(
        run_id="run-1",
        slots=slots,
        turn=3,
        invocations={
            f"t{t}.0": Invocation(
                invocation_id=f"t{t}.0", turn=t, index=0, call_id=f"u{t}",
                name="nosuch__tool", args_digest="sha256:x", state="completed", status="error",
            )
            for t in (1, 2, 3)
        },
    )
    fresh = resumed.shaper
    fresh._scope = None  # type: ignore[union-attr] - a new attempt has not begun
    _ = await fresh.shape(ModelCall([], None, None, slots), 4, journaled)  # type: ignore[union-attr]
    again = await resumed.tools.execute(ToolCall(id="u5", name="nosuch__tool", arguments={}), CTX)
    assert "is paused for 2 turns" in str(again.content)


async def test_c2_an_ungranted_but_registered_tool_is_refused_not_run(monkeypatch: pytest.MonkeyPatch) -> None:
    """The unknown-tool route runs the hooks around a refusal: a tool the
    registry holds but the pin does not grant never executes."""
    ran: list[str] = []

    async def create_issue(self: object, title: str) -> str:
        ran.append(title)
        return '{"id": "J-1"}'

    w.replace_tool(monkeypatch, w.FakeJira, "create_issue", create_issue)
    agent = w.agent_doc()
    grant = _full_grant(agent)
    grant["tool_ids"] = [t for t in grant["tool_ids"] if not t.endswith("/create_issue")]
    runtime, slots, _ = await _durable_turn_one(w.Services(agent=agent), w.pinned_spec(**grant), "quick", "q")
    _ = await runtime.shaper.shape(ModelCall([], None, None, slots), 1, RunState(run_id="run-1", slots=slots))  # type: ignore[union-attr]
    refused = await runtime.tools.execute(
        ToolCall(id="c1", name="jira__create_issue", arguments={"title": "x"}), CTX
    )
    assert refused.status == "error" and ran == []


def _legacy_scope(agent: Any, runtime: Any, turn: int) -> Any:  # noqa: ANN401
    """The scope `Agent.step` gives a call in `turn` (the tracker is turn-aware)."""
    from app.agent_loop_lib.core.context import RunContext as Identity
    from app.agent_loop_lib.core.scope import RunScope, ToolScope, TurnScope

    scope = RunScope(identity=Identity(role_name="pipeshub-agent", model="gpt-x", run_id="legacy"), spec=agent.spec,
                     runtime=runtime, goal=None, session_id="ses-1")  # type: ignore[arg-type]
    return ToolScope(turn=TurnScope(run=scope, turn_index=turn - 1), call=None, tool_path="", tags=())  # type: ignore[arg-type]
