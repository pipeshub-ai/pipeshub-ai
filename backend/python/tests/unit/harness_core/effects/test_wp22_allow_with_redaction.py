"""WP-22 OWD-9: ``allow_with_redaction`` runs a call without a person and redacts what it returns (TG-12).

PE-1 the decision ranks between allow and ask; a configured rule can never say it.
PE-2 the runner runs the call unasked and journals (and returns) the redacted result, with a note saying why.
PE-3 a policy that names its redaction keeps it after a person approves the call (ask over redaction).
PE-4 a result with nothing to redact is returned unchanged, with no note.
PE-5 the durable kernel plans the call as ``run`` and the model reads only the redacted result.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from harness_core.contracts.generated.sink import SinkDescriptor
from harness_core.effects import (
    EffectCall,
    EffectRunner,
    RequestEffectLog,
    ToolEffect,
    ToolExecution,
    args_digest,
)
from harness_core.effects.policy import ApprovalConfigInvalid, parse_rules, stricter
from harness_core.effects.redaction import POLICY_REDACTION, Redaction, redacted
from harness_core.effects.types import ALLOW_WITH_REDACTION, runs_unasked, tool_id_for
from harness_core.kernel.core.messages import ToolCall
from harness_core.testing.contracts.effect_log import context

from ..durable.support import World, reply, scripted

if TYPE_CHECKING:
    from collections.abc import Mapping

    from pydantic import JsonValue

    from harness_core.effects import ApprovalDecision, HoldDecision
    from harness_core.effects.types import ToolContext

CONTACT = "Reach Dana at dana.rivera@example.com or +1 415 555 0100; card 4111 1111 1111 1111."


def _read() -> ToolEffect:
    return ToolEffect(tool_id_for("contacts"), "read", True, SinkDescriptor(egress="self", args_leave_boundary=False))


def _call(effect: ToolEffect) -> EffectCall:
    args: dict[str, JsonValue] = {"q": "dana"}
    return EffectCall("run-1", "inv-1", ToolCall(id="inv-1", name="contacts", arguments=args), effect, args_digest(args))


async def _contacts(call: ToolCall, ctx: ToolContext) -> ToolExecution:
    return ToolExecution(status="ok", content=CONTACT)


def test_pe1_redaction_ranks_between_allow_and_ask_and_no_rule_can_configure_it() -> None:
    assert stricter("allow", ALLOW_WITH_REDACTION) == ALLOW_WITH_REDACTION
    assert stricter(ALLOW_WITH_REDACTION, "ask") == "ask"
    assert stricter("deny", ALLOW_WITH_REDACTION) == "deny"
    assert runs_unasked(ALLOW_WITH_REDACTION) and runs_unasked("allow") and not runs_unasked("ask")
    with pytest.raises(ApprovalConfigInvalid):
        _ = parse_rules({"writes": "allow_with_redaction"})


async def test_pe2_the_call_runs_unasked_and_its_journaled_result_is_redacted() -> None:
    log = RequestEffectLog()

    def policy(call: ToolCall, effect: ToolEffect) -> ApprovalDecision:
        return ALLOW_WITH_REDACTION

    result = await EffectRunner(log, policy).run(_call(_read()), context(_call(_read())), _contacts)

    assert result.disposition == "executed"
    assert result.execution is not None
    shown = str(result.execution.content)
    for secret in ("dana.rivera@example.com", "415 555 0100", "4111 1111 1111 1111"):
        assert secret not in shown
    assert "[redacted email]" in shown and "[redacted phone]" in shown and "[redacted payment card]" in shown
    assert "Policy redacted email, payment card, phone from this result" in shown
    recorded = await log.find(_call(_read()).key)
    assert recorded is not None and recorded.execution is not None
    assert recorded.execution.content == result.execution.content


class _AskAndRedact:
    """Asks a person about every call and names a redaction: the approval answers the ask, not the redaction."""

    def __call__(self, call: ToolCall, effect: ToolEffect) -> ApprovalDecision:
        return "ask"

    def redaction(self, call: ToolCall, effect: ToolEffect) -> Redaction | None:
        return Redaction("classification:restricted")


class _Approves:
    async def hold(self, call: EffectCall) -> HoldDecision | None:
        return "approved"


async def test_pe3_a_persons_approval_does_not_lift_a_layers_redaction() -> None:
    runner = EffectRunner(RequestEffectLog(), _AskAndRedact(), hold=_Approves())

    result = await runner.run(_call(_read()), context(_call(_read())), _contacts)

    assert result.execution is not None
    assert "dana.rivera@example.com" not in str(result.execution.content)
    assert json.dumps("classification:restricted") in str(result.execution.content)


def test_pe4_a_result_with_nothing_to_redact_is_unchanged() -> None:
    plain = ToolExecution(status="ok", content={"rows": [{"title": "Q3 plan", "updated": "2026-10-28"}]})

    same, kinds = redacted(plain, POLICY_REDACTION)

    assert same is plain and kinds == frozenset()


async def test_pe5_the_kernel_runs_it_unasked_and_the_model_reads_only_the_redacted_result() -> None:
    world = World()

    async def contacts(args: Mapping[str, JsonValue], ctx: ToolContext) -> ToolExecution:
        return ToolExecution(status="ok", content=CONTACT)

    world.tools.add("contacts", contacts, tags=[("category", "read")])
    world.admit()

    def policy(call: ToolCall, effect: ToolEffect) -> ApprovalDecision:
        return ALLOW_WITH_REDACTION if call.name == "contacts" else "allow"

    model, port = scripted(reply("", ("contacts", {"q": "dana"})), reply("done"))
    disposition = await world.run(world.kernel(port, approval=policy))

    assert disposition.status == "completed"
    assert [e.type for e in world.events() if e.type.startswith("interaction.")] == []
    seen = str(model.requests[-1])
    assert "dana.rivera@example.com" not in seen
    assert "[redacted email]" in seen
