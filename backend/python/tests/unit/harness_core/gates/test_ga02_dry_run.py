"""GA-02 (G-03, C20.04): a dry run reads for real and records every outside effect as what it would have done.

The scenario: an agent in a dry run on a task that would send a message and create a ticket. No effect reaches the
service; each call's outcome is journaled with ``shadow`` (and whether it would have asked); the run says it was a
dry run. Enforced where calls are executed (``EffectRunner``), for the loop and for an engine's Tool Gateway alike,
never by the prompt; an engine's other ways out (the repository push, the model gateway's vendor tools) are closed.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import pytest

from harness_core.contracts.generated.run_spec import RunGates, RunSpec
from harness_core.contracts.generated.sink import SinkDescriptor
from harness_core.effects.log import RequestEffectLog
from harness_core.effects.policy import LayeredApprovalPolicy, parse_rules
from harness_core.effects.runner import EffectRunner
from harness_core.effects.types import (
    EffectCall,
    ToolContext,
    ToolEffect,
    ToolExecution,
    args_digest,
    tool_id_for,
)
from harness_core.engines.git_checkout import GitCheckout, agent_policy
from harness_core.gates.dry_run import shadowed
from harness_core.gates.instructions import DRY_RUN_NOTE, gated_instructions
from harness_core.kernel.core.messages import ToolCall
from harness_core.sandbox.types import EgressPolicy
from harness_core.testing.engines.durable import EngineWorld, engine_run_spec
from harness_core.testing.run_spec import minimal_run_spec
from tests.unit.harness_core.durable.support import World, reply, scripted

if TYPE_CHECKING:
    from collections.abc import Mapping

    from pydantic import JsonValue

    from harness_core.effects.types import EffectKind

ORG_ASKS = LayeredApprovalPolicy((parse_rules({"writes": "ask"}),), ("org",))
DENY_POSTS = LayeredApprovalPolicy((parse_rules({"tools": {tool_id_for("post_message"): "deny"}}),), ("org",))


def dry(spec: RunSpec | None = None) -> RunSpec:
    return (spec or minimal_run_spec()).model_copy(update={"gates": RunGates(dryRun=True)})


def effect(kind: EffectKind, egress: str = "external", *, mark: str | None = None) -> ToolEffect:
    sink = SinkDescriptor(egress=egress, args_leave_boundary=egress == "external")  # type: ignore[arg-type]
    return ToolEffect(tool_id_for("t"), kind, False, sink, mark=mark)  # type: ignore[arg-type]


def _ticket_and_message() -> object:
    return scripted(
        reply("", ("lookup", {"q": "open bugs"})),
        reply("", ("create_issue", {"title": "Login fails"}), ("post_message", {"to": "ops@acme.test", "text": "hi"})),
        reply("I would have filed the bug and emailed ops; nothing was actually done."),
    )[1]


def _world(spec: RunSpec | None = None) -> World:
    world = World()
    world.admit(spec=spec or dry())
    return world


# GA-02-1: the whole scenario on the loop.
async def test_ga02_1_a_dry_run_reads_for_real_and_makes_no_outside_effect(caplog: pytest.LogCaptureFixture) -> None:
    world = _world()
    with caplog.at_level(logging.INFO, logger="harness_core.kernel.durable"):
        disposition = await world.run(world.kernel(_ticket_and_message(), approval=ORG_ASKS))
    assert disposition.status == "completed"
    assert world.saas.effects == []
    # Nobody is asked about a call that is never made.
    assert "interaction.requested" not in world.types()
    outcomes = world.events("tool.outcome")
    assert [o.data.get("shadow") for o in outcomes] == [None, {"wouldAsk": True}, {"wouldAsk": True}]
    intents = world.events("tool.intent")
    assert [i.data["effect"] for i in intents] == ["read", "write", "write"]
    shadowed_lines = [r for r in caplog.records if r.getMessage().startswith("tool.shadowed")]
    assert len(shadowed_lines) == 2


async def test_ga02_1_the_model_reads_what_would_have_been_done_with_its_arguments() -> None:
    world = _world()
    port = _ticket_and_message()
    assert (await world.run(world.kernel(port, approval=ORG_ASKS))).status == "completed"
    last = port.calls[-1]  # type: ignore[attr-defined]
    texts = [m.model_dump_json() for m in last.messages]
    assert any("Dry run: create_issue was not called" in t and "Login fails" in t for t in texts)
    assert any("would have asked a person" in t for t in texts)


async def test_ga02_1_the_run_is_told_it_is_a_dry_run_before_its_first_turn() -> None:
    world = _world()
    port = _ticket_and_message()
    assert (await world.run(world.kernel(port))).status == "completed"
    first = port.calls[0]  # type: ignore[attr-defined]
    assert any(DRY_RUN_NOTE[:60] in m.model_dump_json() for m in first.messages)
    injected = [e for e in world.events("message.completed") if e.data.get("injected")]
    assert len(injected) == 1


# GA-02-2: a denial stays a denial, as it would outside the dry run.
async def test_ga02_2_a_denied_call_is_still_denied_and_not_recorded_as_done() -> None:
    world = _world()
    port = scripted(reply("", ("post_message", {"text": "hi"})), reply("it was refused"))[1]
    assert (await world.run(world.kernel(port, approval=DENY_POSTS))).status == "completed"
    assert world.saas.effects == []
    assert len(world.events("tool.denied")) == 1
    assert all("shadow" not in o.data for o in world.events("tool.outcome"))


# GA-02-3: what stays the run's own runs; everything that can reach out is shadowed.
@pytest.mark.parametrize(
    ("e", "expected"),
    [
        (effect("read"), False),
        (effect("workspace", "none"), False),
        (effect("execute", "none"), False),
        (effect("execute", "self"), True),
        (effect("execute", "external"), True),
        (effect("write", "none"), True),
        (effect("write", "internal_audience"), True),
        (effect("write", "external"), True),
        (effect("read", "external", mark="destructive"), True),
        (effect("workspace", "none", mark="financial"), True),
    ],
    ids=lambda v: str(v) if isinstance(v, bool) else f"{v.effect}-{v.sink.egress}-{v.mark}",
)
def test_ga02_3_what_a_dry_run_shadows(e: ToolEffect, expected: bool) -> None:
    assert shadowed(e) is expected


async def test_ga02_3_without_the_gate_the_same_calls_happen() -> None:
    world = _world(minimal_run_spec())
    assert (await world.run(world.kernel(_ticket_and_message()))).status == "completed"
    assert sorted(e.op for e in world.saas.effects) == ["create_issue", "post_message"]
    assert all("shadow" not in o.data for o in world.events("tool.outcome"))


# GA-02-4: the Tool Gateway (an engine's only way to a tool) enforces it for engines.
async def test_ga02_4_an_engines_write_through_the_gateway_is_recorded_never_made() -> None:
    world = EngineWorld()
    world.admit("run-dry", 'tool lookup {"q": "x"}\ntool create {"title": "ticket"}\nsay done', spec=dry(engine_run_spec()))
    assert (await world.attempt("run-dry")).status == "completed"
    assert [name for name, _ in world.effects.elements()] == ["lookup"]
    outcomes = [e for e in world.events("run-dry") if e.type == "tool.outcome"]
    assert [o.data.get("shadow") is not None for o in outcomes] == [False, True]
    assert "interaction.requested" not in world.types("run-dry")


async def test_ga02_4_the_same_engine_run_without_the_gate_waits_to_make_the_call() -> None:
    world = EngineWorld()
    world.admit("run-real", 'tool create {"title": "ticket"}\nsay done')
    assert (await world.attempt("run-real")).status == "suspended"
    assert "interaction.requested" in world.types("run-real")
    assert all(o.data.get("shadow") is None for o in world.events("run-real") if o.type == "tool.outcome")


def test_ga02_4_an_engine_is_told_it_is_a_dry_run_and_the_words_are_not_the_enforcement() -> None:
    assert DRY_RUN_NOTE in gated_instructions("Do the task.", dry())
    assert gated_instructions("Do the task.", minimal_run_spec()) == "Do the task."


# GA-02-5: a shadowed call never runs, whatever path reaches the runner, and recovers as nothing happened.
def _call(e: ToolEffect) -> EffectCall:
    call = ToolCall(id="c1", name="post", arguments={"text": "hi"})
    return EffectCall("r", "inv_1", call, e, args_digest(call.arguments))


async def _never(call: ToolCall, ctx: ToolContext) -> ToolExecution:
    raise AssertionError("a dry run made the call")


CTX = ToolContext("r", "o", "inv_1", "a", "k")


async def test_ga02_5_perform_and_read_never_hand_a_shadowed_call_to_its_tool() -> None:
    write = effect("write")
    runner = EffectRunner(RequestEffectLog(), dry_run=True)
    performed = await runner.perform(_call(write), CTX, _never, write_intent=True)
    assert performed is not None and performed.shadow is not None and performed.status == "ok"
    read = await runner.read(_call(write), CTX, _never)
    assert read.shadow is not None
    ran = await EffectRunner(RequestEffectLog(), dry_run=True).run(_call(write), CTX, _never)
    assert ran.disposition == "executed" and ran.execution is not None and ran.execution.shadow is not None


def test_ga02_5_an_intent_with_no_outcome_is_retried_in_a_dry_run_never_marked_unknown() -> None:
    write = effect("write")
    assert EffectRunner(RequestEffectLog()).recover(write) == "unknown"
    assert EffectRunner(RequestEffectLog(), dry_run=True).recover(write) == "retry"


async def test_ga02_5_a_shadowed_call_asks_nobody_and_records_that_it_would_have() -> None:
    runner = EffectRunner(RequestEffectLog(), ORG_ASKS, dry_run=True)
    call = _call(effect("write"))
    assert runner.approve(call.call, call.effect) == "allow"
    simulated = runner.simulated(call)
    assert simulated.shadow is not None and simulated.shadow.would_ask is True


# GA-02-6: an engine's own way out of the gateway: a repository it works on is fetched, never pushed.
def test_ga02_6_a_dry_runs_checkout_may_fetch_and_never_push() -> None:
    checkout = GitCheckout("https://github.com/acme/app.git", "pipeshub/session-s1")
    pushing = agent_policy(EgressPolicy(), checkout, "ph", None)
    fetching = agent_policy(EgressPolicy(), GitCheckout(checkout.url, checkout.session_branch, push=False), "ph", None)
    (pushed,) = [d.git for d in pushing.destinations if d.git is not None]
    (fetched,) = [d.git for d in fetching.destinations if d.git is not None]
    # Since WP-63 no agent phase pushes by itself: a push needs a person's approval and a one-push grant (GITF-19),
    # and a dry run's checkout (push=False) is never offered repository_push, so nothing can push it at all.
    assert pushed.push_branch is None
    assert fetched.push_branch is None


# GA-02-7: the model gateway never runs a vendor's tools for an engine (nothing to intercept there).
@pytest.mark.parametrize(
    "request_body",
    [
        {"tools": [{"type": "web_search_20250305", "name": "web_search"}]},
        {"mcp_servers": [{"type": "url", "url": "https://mcp.example.test", "name": "x"}]},
        {"container": "c1"},
    ],
    ids=["hosted-tool", "mcp-servers", "code-container"],
)
def test_ga02_7_the_model_gateway_refuses_vendor_side_tools(request_body: Mapping[str, JsonValue]) -> None:
    from harness_core.gateway.errors import UnsupportedParameter
    from harness_core.gateway.messages_wire import parse_messages_request

    body = {"model": "m", "max_tokens": 10, "messages": [{"role": "user", "content": "hi"}], **request_body}
    with pytest.raises(UnsupportedParameter):
        parse_messages_request(body)
