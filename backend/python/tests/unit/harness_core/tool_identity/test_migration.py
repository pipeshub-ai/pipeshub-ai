"""MG-1 (P2.2): planning the move of type-keyed definitions to instances, and its revert (pure)."""

from __future__ import annotations

from harness_core.tool_identity import (
    DefinitionBinding,
    InstanceCandidate,
    OwnerBindings,
    Rekey,
    plan_rekeys,
    plan_reverts,
)

ORG = "org-1"
JIRA_A = InstanceCandidate(ORG, "jira", "inst-a", "Acme Jira")
JIRA_B = InstanceCandidate(ORG, "jira", "inst-b", "Beta Jira")
SLACK = InstanceCandidate(ORG, "Slack", "inst-s", "Slack")
OTHER_ORG_JIRA = InstanceCandidate("org-2", "jira", "inst-o", "Other")


def owner(key: str, *bindings: DefinitionBinding, tenant: str | None = ORG) -> OwnerBindings:
    return OwnerBindings(key, tenant, bindings)


def test_mg1_one_candidate_is_rekeyed() -> None:
    plan = plan_rekeys([owner("agent-1", DefinitionBinding("ts-1", "slack", None))], [SLACK, JIRA_A, OTHER_ORG_JIRA])
    assert plan.rekeys == (Rekey("agent-1", "ts-1", "slack", "inst-s", "Slack"),)
    assert plan.unresolved == ()
    assert plan.held == ()


def test_mg1_two_candidates_are_ambiguous_and_hold_the_whole_agent() -> None:
    plan = plan_rekeys(
        [owner("agent-1", DefinitionBinding("ts-1", "jira", None), DefinitionBinding("ts-2", "slack", None))],
        [JIRA_B, JIRA_A, SLACK],
    )
    assert plan.rekeys == ()
    assert plan.held == ("agent-1",)
    [unresolved] = plan.unresolved
    assert (unresolved.binding, unresolved.reason, unresolved.candidates) == ("ts-1", "ambiguous", ("inst-a", "inst-b"))


def test_mg1_no_instance_duplicate_and_unknown_tenant_are_reported() -> None:
    plan = plan_rekeys(
        [
            owner("agent-1", DefinitionBinding("ts-1", "github", None)),
            owner("agent-2", DefinitionBinding("ts-2", "jira", None), DefinitionBinding("ts-3", "jira", "inst-a")),
            owner("agent-3", DefinitionBinding("ts-4", "jira", None), tenant=None),
        ],
        [JIRA_A],
    )
    reasons = {u.owner: u.reason for u in plan.unresolved}
    assert reasons == {"agent-1": "no_instance", "agent-2": "duplicate_instance", "agent-3": "tenant_unknown"}
    assert plan.held == ("agent-1", "agent-2", "agent-3")
    assert plan.rekeys == ()


def test_mg1_bound_bindings_are_counted_not_touched() -> None:
    plan = plan_rekeys(
        [owner("agent-1", DefinitionBinding("ts-1", "jira", "inst-a"), DefinitionBinding("ts-2", "slack", None))],
        [JIRA_A, JIRA_B, SLACK],
    )
    assert plan.already_keyed == 1
    assert [r.binding for r in plan.rekeys] == ["ts-2"]


def test_mg1_the_plan_is_deterministic() -> None:
    owners = [owner(f"agent-{i}", DefinitionBinding(f"ts-{i}", "slack", None)) for i in (3, 1, 2)]
    assert plan_rekeys(owners, [SLACK]) == plan_rekeys(list(reversed(owners)), [SLACK])
    assert [r.owner for r in plan_rekeys(owners, [SLACK]).rekeys] == ["agent-1", "agent-2", "agent-3"]


def test_mg5_mg6_revert_only_what_still_holds_the_migrated_instance() -> None:
    applied = (
        Rekey("agent-1", "ts-1", "slack", "inst-s"),
        Rekey("agent-2", "ts-2", "slack", "inst-s"),
        Rekey("agent-3", "ts-3", "slack", "inst-s"),
    )
    plan = plan_reverts(applied, {"ts-1": "inst-s", "ts-2": "inst-other"})
    assert plan.reverts == (applied[0],)
    assert [(d.rekey.binding, d.reason) for d in plan.drifted] == [("ts-2", "changed"), ("ts-3", "gone")]
    unkeyed = plan_reverts(applied, {"ts-1": None, "ts-2": None, "ts-3": None})
    assert (unkeyed.reverts, unkeyed.drifted, unkeyed.settled) == ((), (), applied)
