"""UX2 EA-7: ``DefaultAskPolicy``, a host's safety default (an engine run asks before every write) that a person may
answer for the conversation, over admins' rules that are never granted."""

from __future__ import annotations

import pytest

from harness_core.effects.flow import (
    ENGINE_WRITES_RULE,
    FLOW_RULE,
    ConversationGrants,
    DefaultAskPolicy,
    FlowGuardedPolicy,
    FlowRule,
    destination_key,
)
from harness_core.effects.policy import (
    ENGINE_APPROVALS,
    WRITES_ASK,
    ApprovalRules,
    LayeredApprovalPolicy,
)
from harness_core.effects.types import (
    POLICY_DENIAL,
    ExplainsAsk,
    ExplainsDenial,
    allow_all,
)
from tests.unit.harness_core.effects.test_ux1_flow_ask import (
    POST,
    READ,
    Ledger,
    call,
    granted_events,
    where,
)

ORG_ASKS = LayeredApprovalPolicy((WRITES_ASK,))
ORG_DENIES = LayeredApprovalPolicy((ApprovalRules(classes={"write": "deny"}),))
DENY_DEFAULT = LayeredApprovalPolicy((ApprovalRules(classes={"write": "deny"}),))


def policy(base: object = allow_all, *, default: object = ENGINE_APPROVALS, records: frozenset[str] | None = frozenset(), grants: ConversationGrants | None = None) -> DefaultAskPolicy:
    return DefaultAskPolicy(base, default, ENGINE_WRITES_RULE, Ledger(records), "r", destination=where, grants=grants)  # type: ignore[arg-type]


def granted(channel: str = "sales") -> ConversationGrants:
    grants = ConversationGrants()
    grants.apply(granted_events("ix.approve.k1", channel))
    return grants


@pytest.mark.parametrize(
    ("base", "default", "grants", "channel", "decision", "rule", "offer"),
    [
        # The default alone asks: explained as the engine's rule, grantable to a named destination.
        (allow_all, ENGINE_APPROVALS, None, "sales", "ask", ENGINE_WRITES_RULE, True),
        (allow_all, ENGINE_APPROVALS, None, None, "ask", ENGINE_WRITES_RULE, False),
        # An org rule that asks too: its own rule is the one explained (QA6-F6), never granted.
        (ORG_ASKS, ENGINE_APPROVALS, None, "sales", "ask", "writes", False),
        # Granted for this tool and destination: allowed; another destination still asks.
        (allow_all, ENGINE_APPROVALS, "granted", "sales", "allow", None, None),
        (allow_all, ENGINE_APPROVALS, "granted", "general", "ask", ENGINE_WRITES_RULE, True),
        # A grant never answers an org rule, nor a deny.
        # (Only after an org changed its rules mid-conversation: the org holds it, under its own rule.)
        (ORG_ASKS, ENGINE_APPROVALS, "granted", "sales", "ask", "writes", False),
        (ORG_DENIES, ENGINE_APPROVALS, "granted", "sales", "deny", None, None),
        (allow_all, DENY_DEFAULT, "granted", "sales", "deny", None, None),
    ],
)
def test_ea7_decisions_and_reasons(base: object, default: object, grants: str | None, channel: str | None, decision: str, rule: str | None, offer: bool | None) -> None:
    p = policy(base, default=default, grants=granted() if grants else None)
    assert isinstance(p, ExplainsAsk) and isinstance(p, ExplainsDenial)
    assert p(call(channel), POST) == decision
    reason = p.ask_reason(call(channel), POST)
    if rule is None:
        assert reason is None
        return
    assert reason is not None
    assert reason["rule"] == rule
    assert reason["conversationGrant"] is offer
    assert reason["sensitivity"] == "public"


def test_ea7_a_read_is_never_asked_by_the_default() -> None:
    p = policy()
    assert p(call(), READ) == "allow"
    assert p.ask_reason(call(), READ) is None


def test_ea7_the_flow_rules_reason_wins_when_it_asks_and_both_offer_one_grant() -> None:
    flow = FlowGuardedPolicy(allow_all, FlowRule("ask"), Ledger(frozenset({"rec-1"})), "r", destination=where, grants=None)  # type: ignore[arg-type]
    p = policy(flow, records=frozenset({"rec-1"}))
    reason = p.ask_reason(call(), POST)
    assert reason is not None
    assert (reason["rule"], reason["sensitivity"], reason["conversationGrant"]) == (FLOW_RULE, "private", True)
    assert reason["destination"] == {"label": "#sales", "key": destination_key(POST.tool_id, "sales")}


def test_ea7_one_grant_answers_both_the_flow_rule_and_the_default() -> None:
    grants = granted()
    flow = FlowGuardedPolicy(allow_all, FlowRule("ask"), Ledger(frozenset({"rec-1"})), "r", destination=where, grants=grants)  # type: ignore[arg-type]
    assert policy(flow, records=frozenset({"rec-1"}), grants=grants)(call(), POST) == "allow"


def test_ea7_denial_is_the_bases_or_the_policys() -> None:
    flow = FlowGuardedPolicy(allow_all, FlowRule("deny"), Ledger(frozenset({"rec-1"})), "r")  # type: ignore[arg-type]
    assert policy(flow).denial(call(), POST)[0] == FLOW_RULE
    assert policy(default=DENY_DEFAULT).denial(call(), POST) == POLICY_DENIAL


def test_ea7_awaiting_is_an_offer_nobody_answered() -> None:
    grants = ConversationGrants()
    grants.apply(granted_events("ix.approve.k1", "sales")[:1])
    assert grants.awaiting()
    grants.apply(granted_events("ix.approve.k1", "sales")[1:])
    assert not grants.awaiting()
