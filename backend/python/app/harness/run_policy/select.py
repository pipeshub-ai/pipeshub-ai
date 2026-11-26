"""Where policy is read from: a run's pin when its claim carries one, else what ``policy_documents`` says.

Every reader that can still reach a KV policy document is chosen here, by the one mode check
(``app.harness.policy_documents``): a run without a pin, the model gateway's narrowing, an agent save's engine
ceiling, and today's loop's flow rules. While the KV documents are the record each reads as before
(``app.harness.unpinned``); once the policy store is, none of them does. Nothing else imports the KV readers
(cohesion C-31).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.control_plane.runtime.deployment import DeploymentLayer, deployment_flow_rule
from app.harness.access.live_policies import LiveOrgAccessPolicies
from app.harness.policy_documents import (
    ENV_POLICY_STORE,
    KV,
    ResolvedPolicyRequired,
    policy_documents,
)
from app.harness.run_policy.pinned import PinnedRunPolicy
from app.harness.run_policy.ports import NO_RUN_MODEL_PLACEMENT, RunModelPlacement
from app.harness.run_policy.stored import StoredEngineCeilings, StoredRunPolicy
from app.harness.unpinned import (
    ConfiguredApprovals,
    ConfiguredOrgAccessPolicies,
    UnpinnedRunPolicy,
    edition_acp_opt_in,
    pipeshub_engine_profiles,
    refusing_flow_rules,
    resolve_model,
)
from harness_core.effects.flow import strictest_flows
from harness_core.engines.profiles import EngineProfileResolver
from harness_core.logs import HarnessLog
from harness_engines.release import release_profiles

if TYPE_CHECKING:
    from collections.abc import Callable

    from app.config.configuration_service import ConfigurationService
    from app.control_plane.runtime.ports import LiveLayerCache
    from app.harness.assembly.context import RunContext
    from app.harness.assembly.services import RunServices
    from app.harness.run_policy.ports import RunPolicy
    from harness_core.access.ports import OrgAccessPolicies
    from harness_core.effects.flow import FlowRule


class RunPolicies:
    """One per process: the deployment layer read when it is built, and the live layer its runs share."""

    def __init__(
        self,
        services: RunServices,
        *,
        deployment: DeploymentLayer | None = None,
        live: LiveLayerCache | None = None,
    ) -> None:
        self._services = services
        self._deployment = (
            deployment
            if deployment is not None
            else DeploymentLayer.from_environment(HarnessLog.named("app.harness.run_policy"))
        )
        self._live = live
        from app.edition_services import harness_edition

        self._placement = harness_edition().port(RunModelPlacement, NO_RUN_MODEL_PLACEMENT)
        # A value the switch does not know stops the process here, when it starts, as it stops Node.
        _ = policy_documents()

    def for_run(self, run: RunContext, log: HarnessLog) -> RunPolicy:
        """The run's policy, its routes held to the placement extension point (MG-15)."""
        return self._placement.wrap(self._policy(run, log), run, self._services, self._live, log)

    def _policy(self, run: RunContext, log: HarnessLog) -> RunPolicy:
        if run.resolved is not None:
            return PinnedRunPolicy(run, run.resolved, deployment=self._deployment, live=self._live, log=log)
        if policy_documents() == KV:
            return UnpinnedRunPolicy(run, self._services, log)
        if self._live is None:
            raise ResolvedPolicyRequired(f"a run without a pin needs the org's live layer under {ENV_POLICY_STORE}=on")
        return StoredRunPolicy(run, deployment=self._deployment, live=self._live, model=resolve_model, log=log)


def org_access_policies(
    config_service: ConfigurationService, live: Callable[[], LiveLayerCache], *, pin_on: bool
) -> OrgAccessPolicies:
    """The model gateway's narrowing of the vendor rules per org: the model-access document, read at every call,
    only while it is the record and the pin is not ``on``; else the org's live layer (CPD-6). The policy store as
    the record implies the live layer whatever the gateway's own ``CONTROL_PLANE_PIN`` says."""
    if policy_documents() == KV and not pin_on:
        return ConfiguredOrgAccessPolicies(config_service)
    return LiveOrgAccessPolicies(live())


def engine_profiles(
    config_service: ConfigurationService, live: Callable[[], LiveLayerCache], *, log: HarnessLog | None = None
) -> EngineProfileResolver:
    """What a caller outside Node's admission resolves an engine profile through (an agent's, checked on write:
    AG1): the release under the org's ceiling, from the engine-profile document while it is the record, else
    from the org's live layer. ``live`` is asked for only then."""
    if policy_documents() == KV:
        return pipeshub_engine_profiles(config_service, log=log)
    return EngineProfileResolver(
        release_profiles, StoredEngineCeilings(live()), acp_opt_in=edition_acp_opt_in(), log=log
    )


async def legacy_flow_rules(
    config_service: ConfigurationService,
    *,
    org_id: str,
    agent_key: str | None,
    resolved: tuple[FlowRule, ...] | None,
    log: HarnessLog,
) -> tuple[FlowRule, ...]:
    """Today's loop's flow rules for one request (QA10-F1). ``resolved``: the rules Node resolved for the turn
    (``X-PipesHub-Resolved``), met with this deployment's own. Without them the approvals document is read while
    it is the record, exactly as before; once the policy store is, the request is refused."""
    deployment = deployment_flow_rule(log)
    if resolved is not None:
        return strictest_flows((deployment, *resolved))
    if policy_documents() != KV:
        raise ResolvedPolicyRequired(f"today's loop needs the turn's resolved flow rules under {ENV_POLICY_STORE}=on")
    if deployment is not None and deployment.above == "public":
        # Every rule refuses here and none starts lower: the org's and agent's rules cannot add a refusal.
        return (deployment,)
    policy = await ConfiguredApprovals(config_service).policy(org_id=org_id, agent_key=agent_key, log=log)
    return refusing_flow_rules(policy, log)


__all__ = ["RunPolicies", "engine_profiles", "legacy_flow_rules", "org_access_policies"]
