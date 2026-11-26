"""What a run may do while it runs, as one attempt reads it (CPD-6)."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Protocol

from harness_core.security.classification import UNCONFIGURED

if TYPE_CHECKING:
    from collections.abc import Mapping

    from pydantic import JsonValue

    from app.control_plane.runtime.ports import LiveLayerCache
    from app.harness.assembly.context import RunContext
    from app.harness.assembly.model import ResolvedModel
    from app.harness.assembly.services import RunServices
    from app.harness.run_policy.resolved import RunTuning
    from harness_core.effects.flow import FlowRule
    from harness_core.effects.policy import LayeredApprovalPolicy
    from harness_core.effects.types import ApprovalPolicy
    from harness_core.engines.types import EngineProfile
    from harness_core.logs import HarnessLog
    from harness_core.security.classification import Classification


@dataclass(frozen=True, slots=True)
class RunApprovals:
    """The org's and agent's rules (governance: no person's answer grants them), the run's flow rules and the org's
    data classification (OWD-15; ``UNCONFIGURED`` until an org's document has a home, see
    ``app.harness.classification``)."""

    rules: LayeredApprovalPolicy
    flow_rules: tuple[FlowRule, ...] = ()
    classification: Classification = UNCONFIGURED


class RunPolicy(Protocol):
    """One run's policy for one attempt. A pinned run answers from its pin, narrowed by its org's live layer; a
    run without a pin reads each member now."""

    pinned: bool

    async def approvals(self) -> RunApprovals: ...

    async def engine_governance(self, approvals: RunApprovals, profile: EngineProfile) -> LayeredApprovalPolicy:
        """An engine run's governance: ``approvals`` narrowed by what its profile and the org's ceiling narrow."""
        ...

    def stages(self) -> Mapping[str, bool] | None:
        """The Labs stages the run was admitted on; ``None``: read when asked."""
        ...

    def tool_disclosure(self) -> str | None:
        """How the run's tools reach the model (C5.01, ``toolDisclosure.mode``: ``eager``, ``lazy`` or
        ``filesystem``) as the run was admitted on; ``None``: the executor's own default."""
        ...

    def snapshot_ttl_days(self) -> int:
        """How many days after it was taken an engine's sandbox snapshot may still be restored (OWD-12,
        ``snapshots.ttlDays``), as the run was admitted on; the release's for a run without a pin."""
        ...

    def tuning(self) -> RunTuning:
        """C4.10's run-scoped settings (tool-result cap, response_format default, conversation grants, sandbox
        resource caps) as the run was admitted on; the release's for a run without a pin."""
        ...

    def dimension(self, path: str) -> JsonValue | None:
        """A dimension's value as the run was admitted on, by path (one an edition adds included); ``None`` when no
        layer sets it, and for a run without a pin."""
        ...

    async def model(self, run: RunContext, services: RunServices) -> ResolvedModel:
        """The model the run calls."""
        ...

    async def fallback_model(self, run: RunContext, services: RunServices) -> ResolvedModel:
        """The model of a fallback route the run pinned (``run.model`` names it)."""
        ...

    def live(self, governance: ApprovalPolicy) -> ApprovalPolicy:
        """``governance`` narrowed, call by call, by what the org tightened since the attempt was built."""
        ...

    async def allowed_sites(self) -> list[Mapping[str, JsonValue]]:
        """The run's allowed websites as the org's live layer holds them now (WP-63), folded for its agent: read
        again at each engine turn, so a site an admin removes is gone at the next one. None without a live layer."""
        ...

    async def allowed_repository_hosts(self, *, web: bool = False) -> list[Mapping[str, JsonValue]]:
        """The git hosts the run may ask to clone from (WP-63 part A), as the org's live layer holds them now, folded
        for its agent: read again at each call. ``web``: the run has web access, so an org that set no git hosts gets
        the release's default presets (ADR-REPO-01); without it, only what an admin saved. None without a live
        layer."""
        ...

    async def org_only_addresses(self) -> list[object]:
        """The addresses only the run's org can reach (WP83-FU-3, ``websites.orgOnlyAddresses`` at the org layer), as
        its live layer holds them now: read again at each call. None without a live layer."""
        ...


__all__ = ["RunApprovals", "RunPolicy"]


class RunModelPlacement(ABC):
    """Where a run's model routes may be served (MG-15): an extension point, read through
    ``harness_edition().port``. ``RunPolicies.for_run`` hands out every policy through it."""

    @abstractmethod
    def wrap(
        self,
        policy: RunPolicy,
        run: RunContext,
        services: RunServices,
        live: LiveLayerCache | None,
        log: HarnessLog,
    ) -> RunPolicy:
        """``policy``, its routes held to where the run's org lets its model calls be served."""


class NoRunModelPlacement(RunModelPlacement):
    """No placement rule: the run's policy as it is."""

    def wrap(
        self,
        policy: RunPolicy,
        run: RunContext,
        services: RunServices,
        live: LiveLayerCache | None,
        log: HarnessLog,
    ) -> RunPolicy:
        return policy


NO_RUN_MODEL_PLACEMENT: Final = NoRunModelPlacement()
