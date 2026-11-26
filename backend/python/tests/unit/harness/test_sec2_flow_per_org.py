"""SEC2 FO-1, FO-2, FO-4: SEC1's flow rule from the approvals document, never wider than the deployment's flag.

An approval layer (the document's default, an agent's) may carry ``flow {level, above}``. The run's rules are the
strictest of ``HARNESS_FLOW_GUARD``'s and every layer's: a layer can turn the rule on while the flag is off, and can
neither turn it off nor raise its threshold when the flag's is stricter.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from app.harness.approvals import approval_policy, flow_guarded
from app.harness.assembly.assembler import PipesHubRuntimeAssembler
from app.harness.labels import SessionSourceLedger
from app.harness.unpinned import FLOW_ABOVE_ENV, FLOW_LEVEL_ENV, flow_rules
from app.utils.env_flags import HARNESS_FLOW_GUARD
from harness_core.contracts.generated.sink import SinkDescriptor
from harness_core.effects import ToolEffect, tool_id_for
from harness_core.effects.flow import (
    FlowGuardedPolicy,
    FlowRule,
    GrantingLedger,
    layer_rule,
    strictest_flows,
)
from harness_core.effects.policy import ApprovalConfigInvalid, LayerFlow, parse_rules
from harness_core.kernel.core.messages import ToolCall
from harness_core.logs import HarnessLog
from harness_core.provenance.ledger import ProvenanceFeed
from harness_core.testing import ModelScript, Reply, ScriptedModel, Turn
from harness_core.testing.durable import ScriptedModelPort
from tests.unit.harness.assembly import world as w
from tests.unit.harness.engines.test_sec1_flow_guard import (
    READ_THEN_WRITE,
    RUN,
    creates,
    denied_rules,
    world,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

    from pydantic import JsonValue

    from harness_core.effects.types import ApprovalPolicy
    from harness_core.security.labels import SourceLedger

LOG = HarnessLog.named("tests.sec2.flow")
ASK, DENY = FlowRule("ask"), FlowRule("deny")
ASK_PRIVATE, DENY_PRIVATE = FlowRule("ask", "private"), FlowRule("deny", "private")
WRITE = ToolEffect(tool_id_for("post"), "write", False, SinkDescriptor(egress="external", args_leave_boundary=True))
CALL = ToolCall(id="c", name="post", arguments={})


@pytest.fixture(autouse=True)
def _flag_off(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (FLOW_LEVEL_ENV, FLOW_ABOVE_ENV):
        monkeypatch.delenv(name, raising=False)
    # The deployment's rule is on by default since UX1; these cases are about the layers alone.
    monkeypatch.setenv(HARNESS_FLOW_GUARD, "false")


def _deployment(monkeypatch: pytest.MonkeyPatch, level: str, above: str = "public") -> None:
    monkeypatch.setenv(HARNESS_FLOW_GUARD, "true")
    monkeypatch.setenv(FLOW_LEVEL_ENV, level)
    monkeypatch.setenv(FLOW_ABOVE_ENV, above)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ({"flow": {"level": "ask"}}, LayerFlow("ask", "public")),
        ({"flow": {"level": "deny", "above": "private"}}, LayerFlow("deny", "private")),
        ({"flow": {"level": "off"}}, LayerFlow("off", "public")),
        ({"writes": "ask"}, None),
    ],
)
def test_fo1_a_layer_carries_its_flow_rule(raw: JsonValue, expected: LayerFlow | None) -> None:
    assert parse_rules(raw).flow == expected


@pytest.mark.parametrize(
    "raw",
    [
        {"flow": {"level": "maybe"}},
        {"flow": {"above": "private"}},
        {"flow": {"level": "ask", "above": "secret"}},
        {"flow": {"level": "ask", "sink": "external"}},
        {"flow": "ask"},
    ],
)
def test_fo1_an_unreadable_flow_rule_refuses_the_layer(raw: JsonValue) -> None:
    with pytest.raises(ApprovalConfigInvalid):
        _ = parse_rules(raw)


@pytest.mark.parametrize(
    ("rules", "expected"),
    [
        ((), ()),
        ((None,), ()),
        ((ASK,), (ASK,)),
        ((ASK, DENY), (DENY,)),
        ((ASK_PRIVATE, ASK), (ASK,)),
        ((DENY_PRIVATE, ASK), (ASK, DENY_PRIVATE)),
        ((DENY, ASK_PRIVATE, DENY_PRIVATE), (DENY,)),
        ((ASK, ASK, None), (ASK,)),
    ],
)
def test_fo2_the_strictest_rules_are_kept_and_the_covered_ones_dropped(
    rules: tuple[FlowRule | None, ...], expected: tuple[FlowRule, ...]
) -> None:
    assert strictest_flows(rules) == expected


def test_fo2_off_in_a_layer_is_no_rule() -> None:
    assert layer_rule(LayerFlow("off")) is None
    assert layer_rule(LayerFlow("deny", "private")) == DENY_PRIVATE


def _flows(document: JsonValue, agent: str | None = "a1") -> tuple[LayerFlow, ...]:
    return approval_policy(document, org_id="org-1", agent_key=agent, log=LOG).flows


@pytest.mark.parametrize(
    ("document", "deployment", "expected"),
    [
        (None, None, ()),
        ({"default": {"flow": {"level": "ask"}}}, None, (ASK,)),
        ({"default": {"flow": {"level": "ask", "above": "private"}}}, None, (ASK_PRIVATE,)),
        ({"agents": {"a1": {"flow": {"level": "deny"}}}}, None, (DENY,)),
        ({"agents": {"a2": {"flow": {"level": "deny"}}}}, None, ()),
        ({"default": {"flow": {"level": "off"}}}, ("deny", "public"), (DENY,)),
        ({"default": {"flow": {"level": "ask", "above": "private"}}}, ("deny", "public"), (DENY,)),
        ({"default": {"flow": {"level": "deny"}}}, ("ask", "public"), (DENY,)),
        ({"agents": {"a1": {"flow": {"level": "deny", "above": "private"}}}}, ("ask", "public"), (ASK, DENY_PRIVATE)),
        ({}, ("ask", "private"), (ASK_PRIVATE,)),
    ],
)
def test_fo2_the_run_takes_the_strictest_of_the_deployment_and_every_layer(
    monkeypatch: pytest.MonkeyPatch,
    document: JsonValue,
    deployment: tuple[str, str] | None,
    expected: tuple[FlowRule, ...],
) -> None:
    if deployment is not None:
        _deployment(monkeypatch, *deployment)
    assert flow_rules(LOG, _flows(document)) == expected


class _Ledger:
    def __init__(self, records: frozenset[str] | None) -> None:
        self.records = records

    def source_records(self, run_id: str) -> frozenset[str] | None:
        return self.records


@pytest.mark.parametrize(
    ("org", "deployment", "private", "unknown"),
    [
        ({"level": "ask"}, None, "ask", "ask"),
        ({"level": "off"}, ("deny", "public"), "deny", "deny"),
        ({"level": "deny", "above": "private"}, ("ask", "public"), "ask", "deny"),
        ({"level": "ask", "above": "private"}, None, "allow", "ask"),
    ],
)
def test_fo2_the_guarded_policy_decides_as_the_strictest_rule(
    monkeypatch: pytest.MonkeyPatch,
    org: dict[str, str],
    deployment: tuple[str, str] | None,
    private: str,
    unknown: str,
) -> None:
    if deployment is not None:
        _deployment(monkeypatch, *deployment)
    flows = _flows({"default": {"flow": org}})  # type: ignore[dict-item]

    def decide(records: frozenset[str] | None) -> str:
        ledger: SourceLedger = _Ledger(records)  # type: ignore[assignment]
        return flow_guarded(lambda c, e: "allow", rules=flow_rules(LOG, flows), ledger=ledger, run_id="r", log=LOG)(CALL, WRITE)

    assert (decide(frozenset()), decide(frozenset({"rec"})), decide(None)) == ("allow", private, unknown)


def test_fo2_no_rule_anywhere_leaves_the_policy_as_today() -> None:
    base: ApprovalPolicy = lambda c, e: "allow"  # noqa: E731
    assert flow_guarded(base, rules=flow_rules(LOG, _flows({})), ledger=_Ledger(None), run_id="r", log=LOG) is base  # type: ignore[arg-type]


async def test_fo4_engine_a_layer_s_deny_refuses_the_write_with_the_flag_off() -> None:
    flows = _flows({"default": {"flow": {"level": "deny"}}})

    def guard(policy: ApprovalPolicy, ledger: SourceLedger, run_id: str) -> ApprovalPolicy:
        return flow_guarded(policy, rules=flow_rules(LOG, flows), ledger=ledger, run_id=run_id, log=LOG)

    engine = world(guard=guard)
    engine.admit(RUN, READ_THEN_WRITE)
    assert (await engine.attempt(RUN)).status == "completed"
    assert creates(engine) == 0
    assert denied_rules(engine) == ["flow_external_write"]


async def test_fo4_engine_without_a_rule_writes_as_today() -> None:
    engine = world()
    engine.admit(RUN, READ_THEN_WRITE)
    assert (await engine.attempt(RUN)).status == "completed"
    assert creates(engine) == 1


@pytest.fixture
def _w_deployment() -> Iterator[None]:
    with w.deployment():
        yield


async def _loop_runtime(document: Any) -> Any:  # noqa: ANN401
    services = w.Services()
    services.config.put("/services/agentHarness/approvals", document)
    port = ScriptedModelPort(ScriptedModel(ModelScript(id="t", turns=[Turn(reply=Reply(text="ok"))])))
    assembler = PipesHubRuntimeAssembler(services, model_factory=w.model_factory(port))
    return await assembler.build(w.claim(w.pinned_spec(tool_ids=[])))  # type: ignore[arg-type]


@pytest.mark.usefixtures("_w_deployment")
async def test_fo4_loop_the_assembled_run_reads_the_document_s_rule_with_the_flag_off() -> None:
    runtime = await _loop_runtime({"default": {"flow": {"level": "deny"}}})
    assert isinstance(runtime.ledger, GrantingLedger)
    assert isinstance(runtime.ledger.inner, SessionSourceLedger)
    assert isinstance(runtime.approval, FlowGuardedPolicy)
    assert runtime.approval.grants is runtime.ledger.grants
    assert runtime.approval.rule == DENY
    plain = await _loop_runtime({"default": {"writes": "ask"}})
    # Nothing decides from a ledger: no flow rule, no grants. What it is fed is only the session's provenance, for
    # the outcome's gathered sources (ADR-RUN-01 D1 x D3, WP-96), when the journal keeps an outcome ledger.
    assert plain.ledger is None or isinstance(plain.ledger, ProvenanceFeed)
    assert not isinstance(plain.ledger, GrantingLedger)
    assert not isinstance(plain.approval, FlowGuardedPolicy)
