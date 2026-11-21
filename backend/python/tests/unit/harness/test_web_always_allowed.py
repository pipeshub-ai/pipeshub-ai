"""The product's own web search always runs, on every path: the flow guard does not hold it after a private or unknown
read, nor does its Rule of Two after outsider-written content. A page fetch runs too, for an address the run was given
(a search result, a link in a page it opened, the person's message); one only the model wrote is refused after a private or unknown read, with no
card, and runs before one. Every other call that sends data out is still held, what a web page brings back still marks
the run as having read outsider-written content. No rule layer asks about the web tools or refuses them here."""

from __future__ import annotations

import dataclasses
from types import SimpleNamespace
from typing import TYPE_CHECKING, Final

import pytest

from app import edition_config
from app.harness.approvals import (
    approval_policy,
    engine_guarded,
    flow_guarded,
    layer_ask_answers,
)
from app.web.addresses import RunAddresses
from harness_core.effects.flow import (
    WEB_ADDRESS_RULE,
    FlowRule,
    GrantingLedger,
    flow_holds,
    sends_out,
)
from harness_core.effects.policy import ENGINE_APPROVALS, ApprovalRules
from harness_core.kernel.core.messages import ToolCall
from harness_core.kernel.durable.effects import classify_tool_effect, with_call_effect
from harness_core.logs import HarnessLog
from harness_core.testing.contracts.source_ledger import Stream
from harness_core.tools.base import Tag
from harness_core.tools.tags import (
    TAG_EFFECT_READ,
    TAG_FLOW_PLATFORM_WEB,
    TAG_PROVIDER_CONNECTOR,
    TAG_SINK_EXTERNAL,
)
from tests.support.web_tools import product_web_tools
from tests.unit.harness.engines.test_source_ledger import (
    DIGEST,
    WEB_ID,
    PipesHubLedgerWorld,
)
from tests.unit.harness.policy.paths import PATHS, Address, Context, Scenario

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from pydantic import JsonValue

    from harness_core.effects.policy import RuleLayer
    from harness_core.effects.types import ApprovalPolicy, ToolEffect

LOG: Final = HarnessLog.named("tests.web_always_allowed")
RUN: Final = "run-1"
# The ids a durable run and the Tool Gateway give the web tools (``tool_sources.WEB_TOOLS``).
WEB_IDS: Final = {"dynamic__web_search": "web@duckduckgo/web_search", "dynamic__fetch_url": "web@duckduckgo/fetch_url"}
WEB: Final = tuple(WEB_IDS)
ASK: Final = FlowRule("ask")


def _web(name: str) -> tuple[ToolCall, ToolEffect]:
    tool = product_web_tools()[name]
    effect = dataclasses.replace(classify_tool_effect(tool.name, tool.tags), tool_id=WEB_IDS[name])
    call = ToolCall(id="c", name=name, arguments={"url": "https://docs.example.com/guide", "query": "Q3 numbers"})
    return call, effect


def _private_then_browsed() -> GrantingLedger:
    """A run that read a company record, then a web page whose outcome is outsider-written (``WEB_RULE``)."""
    world, stream = PipesHubLedgerWorld(), Stream()
    stream.admitted(RUN)
    world.recorded(stream, RUN, "inv-1", ["rec-1"])
    sink: JsonValue = {"egress": "external", "argsLeaveBoundary": True}
    intent = {"invocationId": "inv-2", "toolId": WEB_ID, "argsDigest": DIGEST, "effect": "read", "sink": sink}
    _ = stream.add("tool.intent", intent, run_id=RUN)
    _ = stream.add(
        "tool.outcome", {"invocationId": "inv-2", "status": "ok", "labels": {"integrity": "external"}}, run_id=RUN
    )
    ledger = GrantingLedger(world.ledger)
    ledger.apply(stream.events)
    return ledger


@pytest.mark.parametrize("name", WEB)
def test_the_web_tools_send_out_but_the_flow_rule_does_not_hold_them(name: str) -> None:
    _, effect = _web(name)
    assert effect.platform_web and sends_out(effect) and not flow_holds(effect)
    for sensitivity in ("public", "private", "unknown"):
        assert FlowRule("deny").decide(sensitivity, effect, untrusted=True) == "allow"


@pytest.mark.parametrize(
    "tags",
    [
        (TAG_EFFECT_READ, TAG_SINK_EXTERNAL, TAG_FLOW_PLATFORM_WEB),
        (TAG_EFFECT_READ, TAG_SINK_EXTERNAL, TAG_FLOW_PLATFORM_WEB, TAG_PROVIDER_CONNECTOR),
        (Tag("effect", "write"), TAG_SINK_EXTERNAL, TAG_FLOW_PLATFORM_WEB, Tag("provider", "platform")),
    ],
    ids=["no-provider", "connector", "write"],
)
def test_the_exemption_holds_only_on_a_platform_read(tags: tuple[Tag, ...]) -> None:
    effect = classify_tool_effect("lookup", tags)
    assert not effect.platform_web and flow_holds(effect)


@pytest.mark.parametrize("name", WEB)
def test_a_web_tool_called_as_a_write_is_held_again(name: str) -> None:
    _, effect = _web(name)
    assert flow_holds(with_call_effect(effect, "write"))


PRIVATE_CONTEXTS: Final = ("private", "unknown", "child-private")


def _skip(path: str, context: str) -> None:
    if path == "legacy" and context.startswith("child"):
        pytest.skip("today's loop has no parent run")


@pytest.mark.parametrize("path", sorted(PATHS))
@pytest.mark.parametrize("context", PRIVATE_CONTEXTS)
async def test_search_runs_after_a_private_or_unknown_read_on_every_path(path: str, context: Context) -> None:
    _skip(path, context)
    assert await PATHS[path].decide(Scenario(tool="product:dynamic__web_search", context=context)) == "allow"


@pytest.mark.parametrize("path", sorted(PATHS))
@pytest.mark.parametrize("context", PRIVATE_CONTEXTS)
@pytest.mark.parametrize("address", ["searched", "linked", "user"])
async def test_a_fetch_of_an_address_the_run_was_given_runs_after_a_private_read(
    path: str, context: Context, address: Address
) -> None:
    _skip(path, context)
    if address == "user" and context.startswith("child"):
        pytest.skip("a child's goal is its parent model's words, not the person's")
    scenario = Scenario(tool="product:dynamic__fetch_url", context=context, address=address)
    assert await PATHS[path].decide(scenario) == "allow"


@pytest.mark.parametrize("path", sorted(PATHS))
@pytest.mark.parametrize("context", PRIVATE_CONTEXTS)
async def test_a_made_up_address_is_refused_after_a_private_or_unknown_read_with_no_card(
    path: str, context: Context
) -> None:
    _skip(path, context)
    scenario = Scenario(tool="product:dynamic__fetch_url", context=context, address="made-up")
    assert await PATHS[path].decide(scenario) == f"deny/{WEB_ADDRESS_RULE}"


@pytest.mark.parametrize("path", sorted(PATHS))
@pytest.mark.parametrize("context", ["public", "after-web", "child-public"])
async def test_a_made_up_address_runs_before_any_private_read(path: str, context: Context) -> None:
    _skip(path, context)
    assert await PATHS[path].decide(Scenario(tool="product:dynamic__fetch_url", context=context)) == "allow"


@pytest.mark.parametrize("path", sorted(PATHS))
async def test_a_run_whose_context_is_unlabelled_refuses_a_made_up_address(path: str) -> None:
    """A child whose parent passed no label, or a history whose label cannot be read: unknown, so fail closed."""
    context: Context = "unknown" if path == "legacy" else "child-unlabelled"
    assert await PATHS[path].decide(Scenario(tool="product:dynamic__fetch_url", context=context)) == (
        f"deny/{WEB_ADDRESS_RULE}"
    )


@pytest.mark.parametrize("path", sorted(PATHS))
@pytest.mark.parametrize(("name", "address", "decided"), [
    ("dynamic__web_search", "made-up", "allow"),
    ("dynamic__fetch_url", "searched", "allow"),
    ("dynamic__fetch_url", "made-up", f"deny/{WEB_ADDRESS_RULE}"),
])
async def test_web_under_a_deployment_that_refuses_sends(path: str, name: str, address: Address, decided: str) -> None:
    deny = {"HARNESS_FLOW_GUARD_LEVEL": "deny"}
    scenario = Scenario(tool=f"product:{name}", context="private", deployment=deny, address=address)
    assert await PATHS[path].decide(scenario) == decided


@pytest.mark.parametrize("path", sorted(PATHS))
async def test_with_the_flow_guard_off_a_made_up_address_runs(path: str) -> None:
    """The operator's off switch: nothing is held for what the run read, so no address is refused for it either."""
    off = {"HARNESS_FLOW_GUARD": "false"}
    assert await PATHS[path].decide(Scenario(tool="product:dynamic__fetch_url", context="private", deployment=off)) == (
        "allow"
    )


@pytest.mark.parametrize("path", sorted(PATHS))
@pytest.mark.parametrize("tool", ["write/external", "read/external", "world:web__fetch_url"])
async def test_every_other_send_after_a_private_read_is_still_held(path: str, tool: str) -> None:
    held = "deny/approval_unavailable" if path == "legacy" else "ask/flow_external_write"
    assert await PATHS[path].decide(Scenario(tool=tool, context="private")) == held


def test_a_web_result_still_marks_the_run_and_holds_a_later_send_by_the_rule_of_two() -> None:
    ledger = _private_then_browsed()
    assert ledger.untrusted_read(RUN) is True
    assert ledger.source_records(RUN) == frozenset({"rec-1"})
    send = classify_tool_effect("acme_send", (Tag("effect", "write"), TAG_SINK_EXTERNAL))
    # Above `unknown` no context alone holds a call: only the Rule of Two's untrusted branch can.
    only_rule_of_two = FlowRule("ask", "unknown")
    assert only_rule_of_two.decide("private", send, untrusted=True) == "ask"
    for name in WEB:
        _, effect = _web(name)
        assert only_rule_of_two.decide("private", effect, untrusted=True) == "allow"


GIVEN: Final = RunAddresses({}, user_text=["see https://docs.example.com/guide"])


@pytest.mark.parametrize("name", WEB)
def test_the_durable_chain_runs_web_and_asks_before_a_send(name: str) -> None:
    ledger = _private_then_browsed()
    policy = flow_guarded(
        approval_policy({}, org_id="o", agent_key="a1", log=LOG), rules=(ASK,), ledger=ledger, run_id=RUN, log=LOG,
        addresses=GIVEN,
    )
    call, effect = _web(name)
    assert policy(call, effect) == "allow"
    send = classify_tool_effect("acme_send", (Tag("effect", "write"), TAG_SINK_EXTERNAL))
    assert policy(ToolCall(id="s", name="acme_send", arguments={}), send) == "ask"


@pytest.mark.parametrize("name", WEB)
def test_the_engine_chain_runs_web_and_asks_before_a_send(name: str) -> None:
    ledger = _private_then_browsed()
    governance = approval_policy({}, org_id="o", agent_key="a1", log=LOG)
    policy = engine_guarded(
        ENGINE_APPROVALS, governance, rules=(ASK,), ledger=ledger, run_id=RUN, log=LOG, addresses=GIVEN
    )
    call, effect = _web(name)
    assert policy(call, effect) == "allow"
    send = classify_tool_effect("acme_send", (Tag("effect", "write"), TAG_SINK_EXTERNAL))
    assert policy(ToolCall(id="s", name="acme_send", arguments={}), send) == "ask"


class _OrgLayer:
    """An edition's org layer that names one tool: how an organization's own rule for a web tool reaches a run."""

    def __init__(self, tool_id: str, level: str = "ask") -> None:
        self.tool_id = tool_id
        self.level = level

    def org_layers(
        self, doc: Mapping[str, JsonValue], org_id: str, read: Callable[[JsonValue, str], ApprovalRules]
    ) -> Sequence[tuple[ApprovalRules, RuleLayer]]:
        return [(ApprovalRules(tools={self.tool_id: self.level}), "org")]  # type: ignore[dict-item]

    def agent_layers(
        self, doc: Mapping[str, JsonValue], org_id: str, agent_key: str, read: Callable[[JsonValue, str], ApprovalRules]
    ) -> Sequence[tuple[ApprovalRules, RuleLayer]]:
        return []


def _governed(governance: ApprovalPolicy, *, engine: bool) -> ApprovalPolicy:
    """The run's chain as both assemblers build it: the governance under ``layer_ask_answers`` (none here)."""
    ledger = _private_then_browsed()
    answered = layer_ask_answers(governance, context=SimpleNamespace(user_id=None, tool_state={}), grants=ledger.grants, log=LOG)  # type: ignore[arg-type]
    if engine:
        return engine_guarded(ENGINE_APPROVALS, answered, rules=(ASK,), ledger=ledger, run_id=RUN, log=LOG, addresses=GIVEN)
    return flow_guarded(answered, rules=(ASK,), ledger=ledger, run_id=RUN, log=LOG, addresses=GIVEN)


@pytest.mark.parametrize("engine", [False, True], ids=["durable", "engine"])
@pytest.mark.parametrize("level", ["ask", "deny"])
@pytest.mark.parametrize("name", WEB)
def test_no_rule_layer_governs_the_web_tools_here(
    monkeypatch: pytest.MonkeyPatch, *, name: str, level: str, engine: bool
) -> None:
    """An org layer, the deployment's rule for the tool, or a class rule for reads: none holds search or page opening."""
    call, effect = _web(name)
    monkeypatch.setattr(edition_config, "org_approval_rules", _OrgLayer(effect.tool_id, level), raising=False)
    monkeypatch.setattr(edition_config, "layer_ask_answers", None, raising=False)
    for doc in ({}, {"default": {"tools": {effect.tool_id: level}}}, {"default": {"classes": {"read": level}}}):
        policy = _governed(approval_policy(doc, org_id="o", agent_key="a1", log=LOG), engine=engine)
        assert policy(call, effect) == "allow", doc
    # Every other read still follows the class rule.
    read = classify_tool_effect("jira_get_issue", (TAG_EFFECT_READ,))
    asking = _governed(approval_policy({"default": {"classes": {"read": "ask"}}}, org_id="o", agent_key=None, log=LOG), engine=engine)
    assert asking(ToolCall(id="r", name="jira_get_issue", arguments={}), read) == "ask"
