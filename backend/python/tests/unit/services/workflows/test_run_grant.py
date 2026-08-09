"""`compute_run_grant` is the only thing standing between generated code and
the tools it may call.

The broker now denies on an empty grant rather than falling back to the run's
registry, so the case that matters is the opposite of what it used to be: a
code workflow whose task declares no tools must be pinned to what its committed
source actually contains, or it can do nothing at all.
"""
from __future__ import annotations

from app.services.tasks.domain.models import TaskDefinition, TaskPrincipal
from app.services.workflows.domain.grants import (
    AGENT_PROVISIONING_TOOLSET,
    DRY_RUN_MAX_CALLS,
    compute_run_grant,
)
from app.services.workflows.domain.models import WorkflowVersion


def _make_task(**overrides) -> TaskDefinition:
    defaults = {
        "org_id": "org-1",
        "created_by_user_id": "user-1",
        "principal": TaskPrincipal(org_id="org-1", user_id="user-1", user_email="a@b.com"),
        "title": "Triage",
        "description": "triage new issues",
        "instructions": "Triage new issues",
        "created_at": "2024-01-01T00:00:00+00:00",
        "updated_at": "2024-01-01T00:00:00+00:00",
    }
    defaults.update(overrides)
    return TaskDefinition(**defaults)


def _version(tool_pins: dict[str, str]) -> WorkflowVersion:
    return WorkflowVersion(workflow_id="wf-1", org_id="org-1", tool_pins=tool_pins)


class TestToolPins:
    def test_a_task_with_no_tools_is_pinned_to_the_versions_tool_pins(self) -> None:
        grant = compute_run_grant(
            _make_task(tool_names=[]),
            version=_version({"jira__create_issue": "jira.create_issue"}),
        )
        assert grant.tool_names == frozenset({"jira__create_issue"})

    def test_an_empty_grant_is_never_produced_from_a_pinned_version(self) -> None:
        """A version that pins tools must not collapse to an empty grant, which
        the broker would deny outright."""
        grant = compute_run_grant(
            _make_task(tool_names=[]),
            version=_version({"slack__post_message": "slack.post_message"}),
        )
        assert grant.tool_names

    def test_declared_tool_names_win_over_the_versions_pins(self) -> None:
        """Editing the code cannot widen a task that declared its tools."""
        grant = compute_run_grant(
            _make_task(tool_names=["jira__create_issue"]),
            version=_version({
                "jira__create_issue": "jira.create_issue",
                "slack__post_message": "slack.post_message",
            }),
        )
        assert grant.tool_names == frozenset({"jira__create_issue"})

    def test_no_version_and_no_declared_tools_grants_nothing(self) -> None:
        """Fails closed. The agent path does not route tools through the
        broker, so the only thing this affects is a code run whose pins could
        not be computed -- which must not become "every tool"."""
        grant = compute_run_grant(_make_task(tool_names=[]))
        assert grant.tool_names == frozenset()


class TestAgentPins:
    def test_agent_ids_come_from_the_versions_agent_pins(self) -> None:
        version = WorkflowVersion(
            workflow_id="wf-1", org_id="org-1", agent_pins={"agent-a", "agent-b"},
        )
        grant = compute_run_grant(_make_task(), version=version)
        assert grant.agent_ids == frozenset({"agent-a", "agent-b"})

    def test_a_version_calling_no_agents_grants_none(self) -> None:
        """Org membership is not authorization: an empty `agent_ids` used to
        let a workflow drive any agent the tenant owned."""
        grant = compute_run_grant(_make_task(), version=_version({}))
        assert grant.agent_ids == frozenset()


class TestOtherAuthority:
    def test_agent_creation_is_off_for_agent_mode_without_the_provisioning_toolset(self) -> None:
        """Agent-mode tasks (no version) need the explicit toolset."""
        grant = compute_run_grant(_make_task(toolset_ids=["slack"]))
        assert grant.can_create_agents is False

    def test_agent_creation_is_on_with_the_provisioning_toolset(self) -> None:
        grant = compute_run_grant(_make_task(toolset_ids=[AGENT_PROVISIONING_TOOLSET]))
        assert grant.can_create_agents is True

    def test_agent_creation_is_on_for_code_workflow_runs(self) -> None:
        """Code workflow runs (version present) always allow agent creation
        because the builder generates ctx.create_agent() as its primary
        mechanism for external I/O."""
        grant = compute_run_grant(
            _make_task(toolset_ids=["slack"]),
            version=_version({"jira__search_issues": "jira.search_issues"}),
        )
        assert grant.can_create_agents is True

    def test_a_dry_run_gets_the_reduced_call_budget(self) -> None:
        grant = compute_run_grant(_make_task(), is_dry_run=True)
        assert grant.max_calls == DRY_RUN_MAX_CALLS
        assert compute_run_grant(_make_task()).max_calls > DRY_RUN_MAX_CALLS

    def test_collection_ids_carry_through(self) -> None:
        grant = compute_run_grant(_make_task(collection_ids=["kb-1", "kb-2"]))
        assert grant.collection_ids == frozenset({"kb-1", "kb-2"})
