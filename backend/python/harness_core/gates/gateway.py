"""A run's gates as the Tool Gateway applies them (G-03, G-04): an engine reaches tools only through the gateway, so
whatever composes a ``RunToolEndpoint`` composes its catalog and policy through ``gated_endpoint``."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from harness_core.gates.plan import PlanGate, plan_gated, plan_tools
from harness_core.gates.spec import dry_run, plan_approval
from harness_core.kernel.fold import RunState

if TYPE_CHECKING:
    from collections.abc import Callable

    from harness_core.contracts.generated.run_spec import RunSpec
    from harness_core.effects.types import ApprovalPolicy
    from harness_core.invoker.ports import ToolCatalog


@dataclass(frozen=True)
class GatedEndpoint:
    catalog: ToolCatalog
    approvals: ApprovalPolicy
    # ``EffectRunner.dry_run``.
    dry_run: bool


def gated_endpoint(
    spec: RunSpec,
    catalog: ToolCatalog,
    approvals: ApprovalPolicy,
    run_state: Callable[[], RunState] | None,
) -> GatedEndpoint:
    """``catalog`` and ``approvals`` under the spec's gates. A host that cannot read the run's state gets a plan gate
    that never sees an approval: closed, never open."""
    gate = None
    if plan_approval(spec):
        gate = PlanGate(run_state or (lambda: RunState(run_id="")))
    return GatedEndpoint(plan_tools(catalog, gate), plan_gated(approvals, gate), dry_run(spec))


__all__ = ["GatedEndpoint", "gated_endpoint"]
