"""UX4 ES-10, ES-11: an engine run, through the run's real tool endpoint, effect runner and journal under
``engine_guarded`` as the assembler composes it, asks before an external write and not before a write to the user's
own space; an org rule that asks still holds both."""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING

import pytest

from app.harness.labels import SessionSourceLedger
from app.harness.unpinned import FLOW_ABOVE_ENV, FLOW_LEVEL_ENV
from app.utils.env_flags import HARNESS_FLOW_GUARD
from harness_core.contracts.generated.sink import SinkDescriptor
from harness_core.effects.flow import ENGINE_WRITES_RULE, FLOW_RULE, GrantingLedger
from harness_core.effects.policy import WRITES_ASK, LayeredApprovalPolicy
from harness_core.testing.contracts.tool_catalog import OfferedTool, offered
from harness_core.testing.engines.durable import CREATE, EngineWorld
from tests.unit.harness.engines.test_ux2_engine_reasons import (
    READ,
    RUN,
    TOOLS,
    create,
    explained,
    requested,
    world,
)

if TYPE_CHECKING:
    from harness_core.effects.types import ApprovalPolicy

_draft = offered("draft", "write")
DRAFT = OfferedTool("draft", dataclasses.replace(_draft.effect, sink=SinkDescriptor(egress="self", args_leave_boundary=False)))
ENGINE_TOOLS = (*TOOLS, DRAFT)


@pytest.fixture(autouse=True)
def _defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (HARNESS_FLOW_GUARD, FLOW_LEVEL_ENV, FLOW_ABOVE_ENV):
        monkeypatch.delenv(name, raising=False)


def draft(text: str) -> str:
    return f'tool draft {{"to": "me@example.com", "text": "{text}"}}'


def drafts(w: EngineWorld) -> int:
    return sum(count for (name, _), count in w.effects.items() if name == "draft")


def engine_world(org: ApprovalPolicy | None = None) -> EngineWorld:
    names = {t.effect.tool_id: t.name for t in ENGINE_TOOLS}
    return world(org, tools=ENGINE_TOOLS, ledger=lambda: GrantingLedger(SessionSourceLedger(tool_names=names)))


# ES-10


async def test_es10_an_own_space_write_runs_and_an_external_write_asks() -> None:
    w = engine_world()
    w.admit(RUN, f"{draft('notes')}\n{create('sales', 'hello')}\nsay waiting")
    assert (await w.attempt(RUN)).status == "suspended"
    assert drafts(w) == 1
    [request] = requested(w)
    [reason] = explained(w)
    assert reason.data["toolId"] == CREATE.effect.tool_id
    # The draft's result is a connector's (opaque), so the context is unknown and the flow rule's reason, the more
    # specific one, is what the card shows (UX2 D-1); either way the external write is held.
    assert (reason.data["rule"], reason.data["sensitivity"]) == (FLOW_RULE, "unknown")
    assert reason.data["interactionId"] == request.data["interactionId"]


async def test_es10_an_external_write_alone_is_held_by_the_engine_default() -> None:
    w = engine_world()
    w.admit(RUN, f"{create('sales', 'hello')}\nsay waiting")
    assert (await w.attempt(RUN)).status == "suspended"
    [reason] = explained(w)
    assert reason.data["rule"] == ENGINE_WRITES_RULE


async def test_es10_after_a_read_an_own_space_write_still_runs() -> None:
    w = engine_world()
    w.admit(RUN, f"{READ}\n{draft('board notes')}\nsay done")
    assert (await w.attempt(RUN)).status == "completed"
    assert drafts(w) == 1
    assert requested(w) == []


# ES-11


async def test_es11_an_org_that_asks_for_every_write_still_holds_an_own_space_write() -> None:
    w = engine_world(org=LayeredApprovalPolicy((WRITES_ASK,)))
    w.admit(RUN, f"{draft('notes')}\nsay waiting")
    assert (await w.attempt(RUN)).status == "suspended"
    assert drafts(w) == 0
    assert len(requested(w)) == 1
    # QA6-F6: held by the org's rule, which the card names; the engine's default would not have asked.
    [reason] = explained(w)
    assert (reason.data["rule"], reason.data["conversationGrant"]) == ("writes", False)


# PF (QA10-F3): what the profile narrows is the org's rule; only ENGINE_APPROVALS is the default.


def test_pf1_the_release_profile_is_the_engineering_default() -> None:
    from harness_core.access.types import AccessRouteRef
    from harness_core.effects.policy import ENGINE_APPROVALS
    from harness_core.engines.policy import profile_rules
    from harness_core.engines.types import ProfileApprovals
    from harness_engines.release import release_profiles

    releases = release_profiles(AccessRouteRef("scripted:scripted-1", "d"))
    assert releases and all(p.approvals == ProfileApprovals() for p in releases)
    assert all(profile_rules(p).classes == {} for p in releases)
    assert ENGINE_APPROVALS.layers[0].classes == {k: v for k, v in ProfileApprovals().as_mapping().items() if v != "allow"}


def test_pf1_a_narrowed_class_is_an_org_rule() -> None:
    from harness_core.engines.policy import profile_rules
    from harness_core.engines.types import ProfileApprovals
    from harness_core.testing.engines.durable import engine_profile

    narrowed = dataclasses.replace(engine_profile(), approvals=ProfileApprovals(read="ask", write="deny"))
    assert profile_rules(narrowed).classes == {"read": "ask", "write": "deny"}


async def _governance(monkeypatch: pytest.MonkeyPatch, ceiling: object) -> ApprovalPolicy:
    from app.harness import unpinned
    from harness_core.engines.errors import ProfileCeilingUnavailable
    from harness_core.logs import HarnessLog
    from harness_core.testing.engines.durable import engine_profile

    async def read(self: object, org_id: str) -> object:
        if ceiling == "unreadable":
            raise ProfileCeilingUnavailable("store down")
        return ceiling

    monkeypatch.setattr(unpinned.ConfiguredEngineCeilings, "ceiling", read)
    return await unpinned.engine_governance(
        object(), LayeredApprovalPolicy(), engine_profile(), "org-1", HarnessLog.named("tests.ux4")  # type: ignore[arg-type]
    )


def _own_space_write(policy: ApprovalPolicy) -> str:
    from harness_core.kernel.core.messages import ToolCall

    return policy(ToolCall(id="c", name="draft", arguments={}), DRAFT.effect)


async def test_pf2_a_ceiling_that_restates_the_release_ask_is_the_orgs_rule(monkeypatch: pytest.MonkeyPatch) -> None:
    from harness_core.engines.types import ProfileConstraint

    governance = await _governance(monkeypatch, ProfileConstraint(approvals={"write": "ask"}))
    assert _own_space_write(governance) == "ask"
    assert _own_space_write(await _governance(monkeypatch, None)) == "allow"


async def test_pf2_an_engine_run_holds_the_own_space_write_and_no_grant_answers_it(monkeypatch: pytest.MonkeyPatch) -> None:
    from harness_core.engines.types import ProfileConstraint

    w = engine_world(await _governance(monkeypatch, ProfileConstraint(approvals={"write": "ask"})))
    w.admit(RUN, f"{draft('notes')}\nsay waiting")
    assert (await w.attempt(RUN)).status == "suspended"
    assert drafts(w) == 0
    [reason] = explained(w)
    assert (reason.data["rule"], reason.data["layer"], reason.data["conversationGrant"]) == ("writes", "org", False)


async def test_pf3_an_unreadable_ceiling_asks_for_every_write(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    import logging

    with caplog.at_level(logging.WARNING, logger="tests.ux4"):
        governance = await _governance(monkeypatch, "unreadable")
    assert _own_space_write(governance) == "ask"
    assert [r.__dict__.get("harness_event") for r in caplog.records] == ["approvals.engine_ceiling_unreadable"]
