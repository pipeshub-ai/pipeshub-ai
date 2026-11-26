"""The outside-policy extension point (``ExternalPolicyGate``) and the contract its rule keeps in the journal.

XP-1 this build's gate reads no setting when an attempt is assembled and leaves the run's policy as it is.
XP-2 the gate is read through the edition port: an edition's gate replaces the default.
XP-3 the effect runner consults a ``ConsultingPolicy`` about a call before deciding it, so a policy that asks
     something outside the run decides from the answer.
XP-4 the ``external_policy`` rule's reason reaches only a journal whose claim names ``external_policy_asks``, on the
     loop's and on the engine kernel's view.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.harness.external_policy import (
    NO_EXTERNAL_POLICY,
    ExternalPolicyGate,
    NoExternalPolicy,
    external_policy_gate,
)
from harness_core.contracts.generated.sink import SinkDescriptor
from harness_core.edition import NO_HARNESS_EDITION, HarnessEdition
from harness_core.effects import (
    EffectCall,
    EffectRunner,
    RequestEffectLog,
    ToolEffect,
    ToolExecution,
    args_digest,
)
from harness_core.effects.flow import EXTERNAL_POLICY_REASON_RULE, reason_for_journal
from harness_core.effects.types import ConsultingPolicy, tool_id_for
from harness_core.journal.types import EXTERNAL_POLICY_ASKS
from harness_core.kernel.core.messages import ToolCall
from harness_core.logs import HarnessLog
from harness_core.testing.contracts.effect_log import context

if TYPE_CHECKING:
    import pytest
    from pydantic import JsonValue

    from harness_core.effects import ApprovalDecision
    from harness_core.effects.types import ToolContext

LOG = HarnessLog.named("test.external_policy_gate")


class _UnreadableConfig:
    """A configuration service no default may read."""

    async def get_config(self, *args: object, **kwargs: object) -> object:
        raise AssertionError("the default gate read a setting")


def _allow(call: ToolCall, effect: ToolEffect) -> ApprovalDecision:
    return "allow"


def _effect() -> ToolEffect:
    return ToolEffect(tool_id_for("send_mail"), "write", False, SinkDescriptor(egress="external", args_leave_boundary=True))


def _call(effect: ToolEffect) -> EffectCall:
    arguments: dict[str, JsonValue] = {"to": "partner@example.com", "body": "Q3 plan"}
    return EffectCall(
        "run-1", "inv-1", ToolCall(id="inv-1", name="send_mail", arguments=arguments), effect, args_digest(arguments)
    )


class _Ran:
    def __init__(self) -> None:
        self.count = 0

    async def __call__(self, call: ToolCall, ctx: ToolContext) -> ToolExecution:
        self.count += 1
        return ToolExecution(status="ok", content="sent")


async def test_xp1_the_default_gate_reads_nothing_and_leaves_the_policy_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.edition_services.harness_edition", lambda: NO_HARNESS_EDITION)
    gate = external_policy_gate()
    assert gate is NO_EXTERNAL_POLICY
    prepared = await gate.prepare(_UnreadableConfig(), "org-1", LOG)  # type: ignore[arg-type]
    assert prepared is None
    for dry_run in (False, True):
        assert gate.guard(_allow, prepared, org_id="org-1", run_id="run-1", log=LOG, dry_run=dry_run) is _allow


def test_xp2_an_editions_gate_replaces_the_default(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Narrowing(NoExternalPolicy):
        pass

    narrowing = _Narrowing()
    monkeypatch.setattr("app.edition_services.harness_edition", lambda: HarnessEdition({ExternalPolicyGate: narrowing}))
    assert external_policy_gate() is narrowing


class _Consulted:
    """Denies the call unless it was consulted about that call first."""

    def __init__(self) -> None:
        self.consulted: list[str] = []

    def __call__(self, call: ToolCall, effect: ToolEffect) -> ApprovalDecision:
        return "allow" if call.id in self.consulted else "deny"

    async def consult(self, call: ToolCall, effect: ToolEffect) -> None:
        self.consulted.append(call.id)


async def test_xp3_the_runner_consults_a_policy_before_it_decides() -> None:
    policy = _Consulted()
    assert isinstance(policy, ConsultingPolicy)
    ran = _Ran()
    call = _call(_effect())
    result = await EffectRunner(RequestEffectLog(), policy).run(call, context(call), ran)  # type: ignore[arg-type]
    assert (result.disposition, ran.count, policy.consulted) == ("executed", 1, ["inv-1"])


def test_xp4_reason_for_journal_withholds_the_rule_from_a_journal_that_does_not_name_it() -> None:
    assert EXTERNAL_POLICY_ASKS == "external_policy_asks"
    reason: dict[str, JsonValue] = {
        "rule": EXTERNAL_POLICY_REASON_RULE,
        "layer": "org",
        "toolId": "t",
        "conversationGrant": False,
    }
    assert reason_for_journal(reason, layers=True, external_policy=True) == reason
    assert reason_for_journal(reason, layers=True) is None
    assert reason_for_journal(reason, layers=False, external_policy=True) is None


async def test_xp4_the_engine_view_writes_the_rule_only_under_the_capability() -> None:
    from harness_core.engines.durable.kernel import _PresentingView  # pyright: ignore[reportPrivateUsage]
    from harness_core.journal.types import NewEvent

    class _Inner:
        def __init__(self) -> None:
            self.appended: list[NewEvent] = []

        async def append(self, events: list[NewEvent]) -> None:
            self.appended.extend(events)

    class _Sink:
        turn = None

    reason: dict[str, JsonValue] = {
        "rule": EXTERNAL_POLICY_REASON_RULE,
        "layer": "org",
        "toolId": "t",
        "conversationGrant": False,
    }
    for accepted, written in ((True, [EXTERNAL_POLICY_REASON_RULE]), (False, [])):
        inner = _Inner()
        view = _PresentingView(
            inner,  # type: ignore[arg-type]
            _Sink(),  # type: ignore[arg-type]
            explains=True,
            layers=True,
            log=HarnessLog.named("test"),
            external_policy=accepted,
        )
        view.reasons["ix.1"] = reason
        await view.append([NewEvent("interaction.requested", {"interactionId": "ix.1", "kind": "approval"})])
        assert [e.data["rule"] for e in inner.appended if e.type == "interaction.explained"] == written
