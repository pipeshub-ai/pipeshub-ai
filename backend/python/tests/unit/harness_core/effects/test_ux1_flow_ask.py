"""UX1 AR-1..AR-3, AR-5 and CG-3..CG-6: why the flow rule asks, and conversation grants, on the pure policy."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

import pytest

from harness_core.contracts.generated.sink import SinkDescriptor
from harness_core.effects.flow import (
    FLOW_RULE,
    MAX_ASK_SOURCES,
    AskDestination,
    ConversationGrants,
    FlowGuardedPolicy,
    FlowRule,
    GrantingLedger,
    destination_key,
)
from harness_core.effects.policy import WRITES_ASK, LayeredApprovalPolicy
from harness_core.effects.types import ExplainsAsk, ToolEffect, allow_all, tool_id_for
from harness_core.journal.types import JournalEvent
from harness_core.kernel.core.messages import ToolCall
from harness_core.security.labels import AskSource

POST = ToolEffect(tool_id_for("post", source="slack"), "write", False, SinkDescriptor(egress="external", args_leave_boundary=True))
SAVE = ToolEffect(tool_id_for("save_artifact"), "write", False, SinkDescriptor(egress="none", args_leave_boundary=False))
READ = ToolEffect(tool_id_for("search"), "read", True, SinkDescriptor(egress="self", args_leave_boundary=False))


def call(channel: str | None = "sales") -> ToolCall:
    return ToolCall(id="c", name="post", arguments={"channel": channel} if channel else {})


def where(c: ToolCall, effect: ToolEffect) -> AskDestination | None:
    channel = c.arguments.get("channel")
    return AskDestination(f"#{channel}", destination_key(effect.tool_id, str(channel))) if channel else None


@dataclass
class Ledger:
    records: frozenset[str] | None
    described: tuple[AskSource, ...] = ()
    events: list[JournalEvent] = field(default_factory=list[JournalEvent])

    def apply(self, events: list[JournalEvent]) -> None:
        self.events.extend(events)

    def source_records(self, run_id: str) -> frozenset[str] | None:
        return self.records

    def describe_sources(self, run_id: str) -> tuple[AskSource, ...]:
        return self.described


def policy(ledger: object, *, base: object = allow_all, rule: FlowRule = FlowRule("ask"), grants: ConversationGrants | None = None) -> FlowGuardedPolicy:
    return FlowGuardedPolicy(base, rule, ledger, "r", destination=where, grants=grants)  # type: ignore[arg-type]


def test_ar1_a_flow_ask_names_its_rule_sources_and_destination() -> None:
    p = policy(Ledger(frozenset({"rec-1"}), (AskSource("Q3 plan", "rec-1"),)))
    assert isinstance(p, ExplainsAsk)
    assert p(call(), POST) == "ask"
    reason = p.ask_reason(call(), POST)
    assert reason == {
        "rule": FLOW_RULE,
        "layer": "flow",
        "toolId": POST.tool_id,
        "sensitivity": "private",
        "sources": [{"title": "Q3 plan", "recordId": "rec-1"}],
        "moreSources": 0,
        "conversationGrant": True,
        "destination": {"label": "#sales", "key": destination_key(POST.tool_id, "sales")},
    }


def test_ar1_unknown_context_says_so() -> None:
    reason = policy(Ledger(None)).ask_reason(call(), POST)
    assert reason is not None and reason["sensitivity"] == "unknown"


def test_ar2_no_flow_reason_when_the_flow_rule_does_not_ask() -> None:
    assert policy(Ledger(frozenset())).ask_reason(call(), POST) is None
    assert policy(Ledger(frozenset({"rec-1"}))).ask_reason(call(), READ) is None
    assert policy(Ledger(frozenset({"rec-1"}))).ask_reason(call(), SAVE) is None
    asks_anyway = policy(Ledger(frozenset()), base=LayeredApprovalPolicy((WRITES_ASK,)))
    assert asks_anyway(call(), POST) == "ask"
    # QA6-F6: the reason is then the base's own rule, never the flow rule's.
    held_by_base = asks_anyway.ask_reason(call(), POST)
    assert held_by_base is not None and held_by_base["rule"] == "writes"


def test_ar3_at_most_ten_sources_the_rest_counted_and_bounded_to_the_schema() -> None:
    many = tuple(AskSource(f"Doc {i}", f"rec-{i}") for i in range(14))
    reason = policy(Ledger(frozenset({"rec-1"}), many)).ask_reason(call(), POST)
    assert reason is not None
    assert len(reason["sources"]) == MAX_ASK_SOURCES  # type: ignore[arg-type]
    assert reason["moreSources"] == 4
    odd = (AskSource("x" * 600, "bad id with spaces", "javascript:alert(1)"), AskSource(None, None, "https://example.com/a"))
    wired = policy(Ledger(frozenset({"rec-1"}), odd)).ask_reason(call(), POST)
    assert wired is not None
    first, second = wired["sources"]  # type: ignore[misc]
    assert len(first["title"]) == 512 and "recordId" not in first and "url" not in first
    assert second == {"url": "https://example.com/a"}


def test_ar3_a_ledger_that_cannot_describe_gives_no_sources() -> None:
    class Plain:
        def apply(self, events: object) -> None: ...

        def source_records(self, run_id: str) -> frozenset[str] | None:
            return frozenset({"rec-1"})

    reason = policy(Plain()).ask_reason(call(), POST)
    assert reason is not None and reason["sources"] == [] and reason["moreSources"] == 0


def test_ar5_a_grant_is_offered_only_for_an_ask_nothing_else_holds_to_a_known_destination() -> None:
    ledger = Ledger(frozenset({"rec-1"}))
    assert policy(ledger).ask_reason(call(), POST)["conversationGrant"] is True  # type: ignore[index]
    no_destination = policy(ledger).ask_reason(call(None), POST)
    assert no_destination is not None and no_destination["conversationGrant"] is False and "destination" not in no_destination
    base_asks = policy(ledger, base=LayeredApprovalPolicy((WRITES_ASK,))).ask_reason(call(), POST)
    assert base_asks is not None and base_asks["conversationGrant"] is False


# CG


_seq = iter(range(1, 10_000))


def event(type_: str, writer: str, **data: object) -> JournalEvent:
    return JournalEvent.from_envelope(
        {
            "orgId": "o",
            "sessionId": "s",
            "streamId": "s",
            "seq": next(_seq),
            "runId": "r",
            "type": type_,
            "schemaVersion": 1,
            "critical": False,
            "ts": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            "writer": writer,
            "visibility": "owner",
            "data": data,
        }
    )


def granted_events(ix: str, channel: str, *, offered: bool = True, scope: bool = True, approved: bool = True) -> list[JournalEvent]:
    events = [
        event(
            "interaction.explained",
            "worker",
            interactionId=ix,
            rule=FLOW_RULE,
            toolId=POST.tool_id,
            sensitivity="private",
            sources=[],
            moreSources=0,
            destination={"label": f"#{channel}", "key": destination_key(POST.tool_id, channel)},
            conversationGrant=offered,
        ),
        event("interaction.resolved", "node", interactionId=ix, decision="approved" if approved else "denied", by="p_u", channel="web"),
    ]
    if scope:
        events.append(event("interaction.granted", "node", interactionId=ix, scope="conversation"))
    return events


def guarded(events: list[JournalEvent], *, rule: FlowRule = FlowRule("ask")) -> FlowGuardedPolicy:
    ledger = GrantingLedger(Ledger(frozenset({"rec-1"})))
    ledger.apply(events)
    return policy(ledger, rule=rule, grants=ledger.grants)


def test_cg3_the_same_tool_and_destination_is_allowed() -> None:
    assert guarded(granted_events("ix.approve.a", "sales"))(call("sales"), POST) == "allow"


@pytest.mark.parametrize(
    ("events", "the_call", "effect", "rule", "expected"),
    [
        (granted_events("ix.approve.a", "sales"), call("general"), POST, FlowRule("ask"), "ask"),
        (
            granted_events("ix.approve.a", "sales"),
            call("sales"),
            ToolEffect(tool_id_for("email", source="slack"), "write", False, SinkDescriptor(egress="external", args_leave_boundary=True)),
            FlowRule("ask"),
            "ask",
        ),
        (granted_events("ix.approve.a", "sales"), call("sales"), POST, FlowRule("deny"), "deny"),
        ([], call("sales"), POST, FlowRule("ask"), "ask"),
    ],
    ids=["other-destination", "other-tool", "deny-rule", "another-session"],
)
def test_cg4_a_grant_holds_for_nothing_else(
    events: list[JournalEvent], the_call: ToolCall, effect: ToolEffect, rule: FlowRule, expected: str
) -> None:
    assert guarded(events, rule=rule)(the_call, effect) == expected


@pytest.mark.parametrize(
    "kwargs", [{"offered": False}, {"scope": False}, {"approved": False}], ids=["not-offered", "no-scope", "denied"]
)
def test_cg5_a_grant_needs_the_offer_the_approval_and_the_scope(kwargs: dict[str, bool]) -> None:
    assert guarded(granted_events("ix.approve.a", "sales", **kwargs))(call("sales"), POST) == "ask"


def test_cg5_a_worker_written_grant_is_ignored() -> None:
    events = granted_events("ix.approve.a", "sales", scope=False)
    forged = event("interaction.granted", "worker", interactionId="ix.approve.a", scope="conversation")
    assert guarded([*events, forged])(call("sales"), POST) == "ask"


def test_cg6_rebuilt_from_the_journal_on_a_new_worker() -> None:
    events = granted_events("ix.approve.a", "sales")
    first, again = ConversationGrants(), ConversationGrants()
    first.apply(events)
    again.apply(events[:1])
    again.apply(events)
    key = destination_key(POST.tool_id, "sales")
    assert first.allows(POST.tool_id, key) and again.allows(POST.tool_id, key)
