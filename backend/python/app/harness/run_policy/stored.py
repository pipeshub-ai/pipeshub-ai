"""Policy read from the org's stored layers through its live layer, once the policy store is the record.

Under ``CONTROL_PLANE_POLICY_STORE=on`` Node no longer writes the KV policy documents. A run without a pin (one
admitted before the flip: after it no run is admitted without ``resolved_config``) and an agent save's engine
profile check then read what the stored layers say now, which Node serves on the internal live route from the
policy store: the same layers a pinned run is narrowed by. Nothing here reads a KV policy document.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Final, cast

from app.control_plane.runtime.live_policy import LiveNarrowedApprovals
from app.harness.classification import with_org_classification
from app.harness.run_policy.resolved import (
    RELEASE_SNAPSHOT_TTL_DAYS,
    RELEASE_TUNING,
    Dimensions,
    resolved_approvals,
    resolved_engine_governance,
)
from harness_core.engines.errors import ProfileCeilingUnavailable, ProfileInvalid
from harness_core.engines.profiles import constraint_from_wire
from harness_core.policy import DimensionPolicyResolver

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping

    from pydantic import JsonValue

    from app.control_plane.runtime.deployment import DeploymentLayer
    from app.control_plane.runtime.live_layer import LiveLayer
    from app.control_plane.runtime.ports import LiveLayerCache
    from app.harness.assembly.context import RunContext
    from app.harness.assembly.model import ResolvedModel
    from app.harness.assembly.services import RunServices
    from app.harness.run_policy.ports import RunApprovals
    from app.harness.run_policy.resolved import RunTuning
    from harness_core.effects.policy import LayeredApprovalPolicy
    from harness_core.effects.types import ApprovalPolicy
    from harness_core.engines.types import EngineProfile, ProfileConstraint
    from harness_core.logs import HarnessLog


# A run without a pin holds no ceiling of its own: what the layers say is met into nothing, as the documents were
# read. The release sets none of what is read here (approval rules, and who named an engine approval class).
_NO_PIN: Final[Mapping[str, JsonValue]] = {"schemaVersion": 1, "dimensions": {}}


class StoredRunPolicy:
    """``RunPolicy`` of a run without a pin once the policy store is the record: its approvals, flow rules and
    engine governance are the org's live layer, read when the attempt is built and call by call after it, exactly as
    a pin is narrowed by it. A live layer that cannot be read is stale: writes ask.
    The model and the Labs stages are read when asked, as for any run without a pin (neither is a policy
    document the store took over)."""

    pinned = False

    def __init__(
        self,
        run: RunContext,
        *,
        deployment: DeploymentLayer,
        live: LiveLayerCache,
        model: Callable[[RunContext, RunServices], Awaitable[ResolvedModel]],
        log: HarnessLog,
        resolver: DimensionPolicyResolver | None = None,
    ) -> None:
        self._run = run
        self._deployment = deployment
        self._live = live
        self._model = model
        self._log = log
        self._resolver = resolver if resolver is not None else DimensionPolicyResolver()
        self._narrowed: Dimensions | None = None
        self._layer: LiveLayer | None = None

    @property
    def _agent_key(self) -> str | None:
        return self._run.agent.agent_key if self._run.agent is not None else None

    async def _dimensions(self) -> Dimensions:
        if self._narrowed is None:
            layer = self._layer = await self._live.current(self._run.org_id)
            config = self._resolver.narrowed_by_live(_NO_PIN, layer.for_agent(self._agent_key))
            self._log.info("run_policy.live", liveRevision=layer.revision, stale=layer.stale, pinned=False)
            self._narrowed = cast("Dimensions", config["dimensions"])
        return self._narrowed

    async def approvals(self) -> RunApprovals:
        approvals = resolved_approvals(await self._dimensions(), self._deployment)
        self._log.info(
            "approvals.policy",
            layers=len(approvals.rules.layers),
            agentScoped=self._agent_key is not None,
            pinned=False,
            source="live",
        )
        return with_org_classification(approvals, self._layer, self._log)

    async def engine_governance(self, approvals: RunApprovals, profile: EngineProfile) -> LayeredApprovalPolicy:
        return resolved_engine_governance(await self._dimensions(), approvals, profile)

    def stages(self) -> None:
        return None

    def tool_disclosure(self) -> None:
        return None

    def snapshot_ttl_days(self) -> int:
        return RELEASE_SNAPSHOT_TTL_DAYS

    def tuning(self) -> RunTuning:
        return RELEASE_TUNING

    def dimension(self, path: str) -> JsonValue | None:
        return None

    async def model(self, run: RunContext, services: RunServices) -> ResolvedModel:
        return await self._model(run, services)

    async def fallback_model(self, run: RunContext, services: RunServices) -> ResolvedModel:
        return await self._model(run, services)

    async def allowed_sites(self) -> list[Mapping[str, JsonValue]]:
        from app.harness.websites import live_sites

        layer = await self._live.current(self._run.org_id)
        return list(live_sites(layer.websites, self._agent_key))

    async def allowed_repository_hosts(self, *, web: bool = False) -> list[Mapping[str, JsonValue]]:
        from app.harness.repositories import live_repository_hosts

        layer = await self._live.current(self._run.org_id)
        return live_repository_hosts(layer.repositories, self._agent_key, web=web)

    async def org_only_addresses(self) -> list[object]:
        from harness_core.security.reachability import org_only_entries

        layer = await self._live.current(self._run.org_id)
        return org_only_entries(layer.websites)

    def live(self, governance: ApprovalPolicy) -> ApprovalPolicy:
        cache, org_id = self._live, self._run.org_id
        return LiveNarrowedApprovals(
            governance, lambda: cache.current(org_id), agent_key=self._agent_key, log=self._log
        )


class StoredEngineCeilings:
    """``EngineCeilingSource`` over the org's live layer: the ``engineProfile`` section of its org layer, which is
    the ceiling its admin saved (Node copies the KV ``ceiling`` into it unchanged),
    so the same ``constraint_from_wire`` reads both. A layer that cannot be read refuses, never "no ceiling"."""

    def __init__(self, live: LiveLayerCache) -> None:
        self._live = live

    async def ceiling(self, org_id: str) -> ProfileConstraint | None:
        layer = await self._live.current(org_id)
        if layer.stale:
            raise ProfileCeilingUnavailable("engine profile ceilings unreadable: the org's live layer is stale")
        if not layer.engine_ceilings:
            return None
        if len(layer.engine_ceilings) > 1:
            # One org layer holds the ceiling today; two would need a meet nobody has defined for a save's check.
            raise ProfileInvalid("more than one layer sets the org's engine ceiling")
        return constraint_from_wire(layer.engine_ceilings[0])


__all__ = ["StoredEngineCeilings", "StoredRunPolicy"]
