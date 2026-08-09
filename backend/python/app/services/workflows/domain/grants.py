"""Host-side computation of a run's authority.

Pure policy: `TaskDefinition` in, `RunGrant` out, no I/O. The broker enforces
what this function decides, and nothing in the sandbox can influence it -- that
separation is the whole point of keeping it here rather than inline in the
executor.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from app.services.workflows.interface.broker import RunGrant

if TYPE_CHECKING:
    from app.services.tasks.domain.models import TaskDefinition
    from app.services.workflows.domain.models import WorkflowVersion

__all__ = ["compute_run_grant", "AGENT_PROVISIONING_TOOLSET", "DRY_RUN_MAX_CALLS"]

# A dry run only needs to reach far enough to prove the shape of the workflow.
DRY_RUN_MAX_CALLS = 50

AGENT_PROVISIONING_TOOLSET = "agent_provisioning"


def compute_run_grant(
    task: "TaskDefinition",
    *,
    is_dry_run: bool = False,
    version: "WorkflowVersion | None" = None,
) -> RunGrant:
    """Derive the authority for one run of `task`.

    A task that declares `tool_names` is pinned to exactly those, so editing
    the generated code cannot silently widen what the workflow can touch. When
    it declares none, the pinned version's `tool_pins` supply the grant: the
    broker reads an empty `tool_names` as "every tool the run's registry
    resolved", which for a code workflow means committing new `ctx.tool()`
    calls would grant itself capabilities the workflow was never created with.

    `agent_ids` is pinned the same way, from `version.agent_pins`. Org
    isolation alone is not authorization: the AGENT_RUN handler resolves the id
    within `principal.org_id`, which stops cross-tenant access but still lets a
    workflow drive any agent its own org happens to own.

    `can_create_agents`: enabled for code workflow runs (``version`` present)
    because the codegen builder generates ``ctx.create_agent()`` calls as the
    primary mechanism for external I/O. Also enabled for agent-mode tasks that
    explicitly declare the agent provisioning toolset.

    An empty set here means "granted nothing", not "granted everything" -- the
    broker denies rather than falls back. A version saved before pins existed
    therefore denies every `ctx.tool()` until it is re-committed, which is the
    intended direction to fail in.
    """
    tool_names = frozenset(task.tool_names)
    agent_ids: frozenset[str] = frozenset()
    if version is not None:
        if not tool_names:
            tool_names = frozenset(version.tool_pins)
        agent_ids = frozenset(version.agent_pins)

    can_create = (
        version is not None
        or AGENT_PROVISIONING_TOOLSET in set(task.toolset_ids)
    )

    return RunGrant(
        tool_names=tool_names,
        agent_ids=agent_ids,
        collection_ids=frozenset(task.collection_ids),
        can_create_agents=can_create,
        max_calls=DRY_RUN_MAX_CALLS if is_dry_run else RunGrant.model_fields["max_calls"].default,
    )
