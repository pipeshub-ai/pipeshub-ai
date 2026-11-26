"""What a run admitted without a pin reads while it runs, and what today's loop reads per request.

A durable run whose claim carries its pin (``x.pipeshub.resolved``, CPD-6) reads none of this: its approvals, flow
rule, model and Labs stages were decided at admission (``app.harness.run_policy``). A run admitted while
``CONTROL_PLANE_PIN`` is not ``on``, or while no live worker advertised ``resolved_config``, has no pin and reads as
it did before: the approvals document and the engine-profile ceilings per attempt, the deployment's flow settings,
the model configuration behind its ``modelKey``, the model-access document per model call. Everything of that
kind lives in this module so the flip deletes one file (05-migration-plan §1 rule 5); nothing else under
``app/harness`` reads a live policy source (cohesion C-31).

The engine-profile document, written by an extension's org settings in Node (absent: the release profiles)::

    {"orgs": {"<orgId>": {"ceiling": <EngineProfileNarrowing>, "updatedAt": <ms>, "updatedBy": "p_<user>"}}}

The model-access document, written the same way (absent: nothing narrowed)::

    {"orgs": {"<orgId>": {"disabledRules": [<row id>], "consumerPlans": false, ...}}}
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, cast

from app.config.constants.service import config_node_constants
from app.control_plane.runtime.deployment import (
    FLOW_ABOVE_ENV,
    FLOW_LEVEL_ENV,
    deployment_flow_rule,
)
from app.harness.access.policies import policy_from_entry
from app.harness.approvals import UNREADABLE_LAYER, approval_policy, governed
from app.harness.policy_documents import (
    ENV_POLICY_STORE,
    KV,
    ResolvedPolicyRequired,
    policy_documents,
)
from app.harness.run_policy.ports import RunApprovals
from app.harness.run_policy.resolved import RELEASE_SNAPSHOT_TTL_DAYS, RELEASE_TUNING
from harness_core.access.errors import AccessPolicyUnavailable
from harness_core.access.vendor_rules import DEFAULT_ORG_POLICY
from harness_core.effects.flow import layer_rule, strictest_flows
from harness_core.effects.policy import WRITES_ASK, LayeredApprovalPolicy, LayerFlow
from harness_core.engines.errors import ProfileCeilingUnavailable, ProfileInvalid
from harness_core.engines.policy import configured_rules
from harness_core.engines.ports import RELEASED_ACP_ENGINES, AcpEngineOptIn
from harness_core.engines.profiles import EngineProfileResolver, constraint_from_wire
from harness_engines.release import release_profiles

if TYPE_CHECKING:
    from collections.abc import Iterable

    from pydantic import JsonValue

    from app.config.configuration_service import ConfigurationService
    from app.harness.assembly.context import RunContext
    from app.harness.assembly.model import ResolvedModel
    from app.harness.assembly.services import RunServices
    from app.harness.run_policy.resolved import RunTuning
    from harness_core.access.vendor_rules import OrgAccessPolicy
    from harness_core.effects.flow import FlowRule
    from harness_core.effects.types import ApprovalPolicy
    from harness_core.engines.profiles import AcpOptIn
    from harness_core.engines.types import EngineProfile, ProfileConstraint
    from harness_core.logs import HarnessLog


async def _kv_document(config_service: ConfigurationService, node: config_node_constants, **read: bool) -> object:
    """A KV policy document, while it is the record. Once the policy store is, it is no longer written, so it is
    refused here whatever the caller chose: each reader below then fails closed as on a failed read."""
    if policy_documents() != KV:
        raise ResolvedPolicyRequired(f"{node.name} is not the record under {ENV_POLICY_STORE}=on")
    return await config_service.get_config(node.value, raise_on_error=True, **read)  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]


class ConfiguredApprovals:
    def __init__(self, config_service: ConfigurationService) -> None:
        self._config_service = config_service

    async def policy(self, *, org_id: str, agent_key: str | None, log: HarnessLog) -> LayeredApprovalPolicy:
        try:
            document = await _kv_document(self._config_service, config_node_constants.AGENT_HARNESS_APPROVALS)
        except Exception as exc:
            log.warning("approvals.config_unreadable", errorType=type(exc).__name__)
            return LayeredApprovalPolicy((UNREADABLE_LAYER,), ("deployment",))
        policy = approval_policy(cast("JsonValue", document), org_id=org_id, agent_key=agent_key, log=log)
        log.info("approvals.policy", layers=len(policy.layers), agentScoped=agent_key is not None)
        return policy


def flow_rule(log: HarnessLog) -> FlowRule | None:
    """The deployment's flow rule as the environment says it now, or ``None`` while ``HARNESS_FLOW_GUARD`` is off."""
    return deployment_flow_rule(log)


def flow_rules(log: HarnessLog, flows: Iterable[LayerFlow] = ()) -> tuple[FlowRule, ...]:
    """The run's flow rules: the deployment's (``flow_rule``) and each approval layer's (an org's, an agent's),
    the strictest of them all. A layer can switch the rule on with the flag off, never off or wider."""
    return strictest_flows((flow_rule(log), *(layer_rule(flow) for flow in flows)))


def refusing_flow_rules(policy: LayeredApprovalPolicy, log: HarnessLog) -> tuple[FlowRule, ...]:
    """Today's loop's flow rules (QA10-F1): the deployment's and every layer's, as the durable loop's, so an org's or an
    agent's own rule applies with the deployment's switch off. Today's loop applies no other approval rule, so a layer it
    could not read counts as one that asks, where the durable loop would ask before every write."""
    flows = policy.flows
    if any(layer is UNREADABLE_LAYER for layer in policy.layers):
        flows = (*flows, LayerFlow("ask"))
    return flow_rules(log, flows)


class ConfiguredEngineCeilings:
    """``EngineCeilingSource`` over ``ConfigurationService``; a failed read refuses, never falls back."""

    def __init__(self, config_service: ConfigurationService) -> None:
        self._config_service = config_service

    async def ceiling(self, org_id: str) -> ProfileConstraint | None:
        try:
            document = await _kv_document(
                self._config_service, config_node_constants.AGENT_HARNESS_ENGINE_PROFILES, use_cache=False
            )
        except Exception as exc:
            raise ProfileCeilingUnavailable(f"engine profile ceilings unreadable: {type(exc).__name__}") from None
        if document is None:
            return None
        if not isinstance(document, Mapping):
            raise ProfileInvalid("the engine profiles document is not an object")
        orgs = cast("Mapping[str, object]", document).get("orgs")
        if orgs is None:
            return None
        if not isinstance(orgs, Mapping):
            raise ProfileInvalid("the engine profiles document's orgs is not an object")
        entry = cast("Mapping[str, object]", orgs).get(org_id)
        if entry is None:
            return None
        if not isinstance(entry, Mapping) or "ceiling" not in entry:
            raise ProfileInvalid("an org entry without a ceiling")
        return constraint_from_wire(cast("Mapping[str, object]", entry)["ceiling"])


def edition_acp_opt_in() -> AcpOptIn:
    """Who opts an ACP engine in on this build: every released one, unless the edition lets an org's ceiling decide,
    as Node's bound ``EngineProfileCeilings`` says."""
    from app.edition_services import harness_edition

    return harness_edition().port(AcpEngineOptIn, RELEASED_ACP_ENGINES).by


def pipeshub_engine_profiles(
    config_service: ConfigurationService, *, log: HarnessLog | None = None
) -> EngineProfileResolver:
    """The release defaults from ``harness_engines/allowlist.json`` under the org ceilings in configuration: what a
    caller outside Node's admission resolves through (an agent's engine profile checked on write, AG1)."""
    return EngineProfileResolver(
        release_profiles, ConfiguredEngineCeilings(config_service), acp_opt_in=edition_acp_opt_in(), log=log
    )


class ConfiguredOrgAccessPolicies:
    """``OrgAccessPolicies`` over configuration (A1), read afresh at every call. A read that fails, or an entry
    that is not an access policy, refuses (``access_policy_unavailable``): never read as "nothing narrowed"."""

    def __init__(self, config_service: ConfigurationService) -> None:
        self._config_service = config_service

    async def policy(self, tenant: str) -> OrgAccessPolicy:
        try:
            document = await _kv_document(
                self._config_service, config_node_constants.AGENT_HARNESS_MODEL_ACCESS, use_cache=False
            )
        except Exception as exc:
            raise AccessPolicyUnavailable(f"the access policies are unreadable: {type(exc).__name__}") from None
        if document is None:
            return DEFAULT_ORG_POLICY
        if not isinstance(document, Mapping):
            raise AccessPolicyUnavailable("the access policies document is not an object")
        orgs = cast("Mapping[str, object]", document).get("orgs")
        if orgs is None:
            return DEFAULT_ORG_POLICY
        if not isinstance(orgs, Mapping):
            raise AccessPolicyUnavailable("the access policies document's orgs is not an object")
        entry = cast("Mapping[str, object]", orgs).get(tenant)
        return DEFAULT_ORG_POLICY if entry is None else policy_from_entry(entry)


async def resolve_model(run: RunContext, services: RunServices) -> ResolvedModel:
    """The model behind the run's ``modelKey`` as the configuration says it now."""
    from app.api.routes.chatbot import (
        get_llm_for_chat,  # pyright: ignore[reportUnknownVariableType]
    )
    from app.harness.assembly.model import model_refusal, resolved_model
    from app.utils.llm import LLM_MISSING_FOR_CHAT, LLMNotConfiguredError

    try:
        # Legacy's signature says ``str`` for a key and a name it reads as optional.
        bundle = await get_llm_for_chat(  # pyright: ignore[reportUnknownVariableType, reportUnknownMemberType]
            services.config_service(),
            cast("str", run.model.model_key),
            cast("str", run.model.model_name),
            run.chat_mode,
            reasoning_effort=run.model.reasoning_effort,
        )
        if not bundle or cast("object", bundle[0]) is None:
            raise LLMNotConfiguredError(LLM_MISSING_FOR_CHAT)
        llm, config = bundle[0], cast("dict[str, Any]", bundle[1])
    except Exception as exc:
        raise model_refusal(exc) from exc
    return resolved_model(run, llm, config)


async def engine_governance(
    config_service: ConfigurationService,
    org_policy: LayeredApprovalPolicy,
    profile: EngineProfile,
    org_id: str,
    log: HarnessLog,
) -> LayeredApprovalPolicy:
    """``governed`` by the org's profile ceiling as the document says it now. An unreadable ceiling asks for every
    write, as an unreadable approvals document does."""
    try:
        ceiling = configured_rules(await ConfiguredEngineCeilings(config_service).ceiling(org_id))
    except (ProfileCeilingUnavailable, ProfileInvalid) as refused:
        log.warning("approvals.engine_ceiling_unreadable", reasonCode=refused.reason_code)
        ceiling = WRITES_ASK
    return governed(org_policy, profile, ceiling)


class UnpinnedRunPolicy:
    """``RunPolicy`` of a run admitted without a pin: every member read now, as before CPD-6."""

    pinned = False

    def __init__(self, run: RunContext, services: RunServices, log: HarnessLog) -> None:
        self._run = run
        self._services = services
        self._log = log

    async def approvals(self) -> RunApprovals:
        run = self._run
        rules = await ConfiguredApprovals(self._services.config_service()).policy(
            org_id=run.org_id, agent_key=run.agent.agent_key if run.agent is not None else None, log=self._log
        )
        return RunApprovals(rules, flow_rules(self._log, rules.flows))

    async def engine_governance(self, approvals: RunApprovals, profile: EngineProfile) -> LayeredApprovalPolicy:
        return await engine_governance(
            self._services.config_service(), approvals.rules, profile, self._run.org_id, self._log
        )

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
        return await resolve_model(run, services)

    async def fallback_model(self, run: RunContext, services: RunServices) -> ResolvedModel:
        return await resolve_model(run, services)

    def live(self, governance: ApprovalPolicy) -> ApprovalPolicy:
        return governance

    async def allowed_sites(self) -> list[Mapping[str, JsonValue]]:
        # Allowed websites ride the org's live layer, which a run admitted without a pin does not read: none.
        return []

    async def allowed_repository_hosts(self, *, web: bool = False) -> list[Mapping[str, JsonValue]]:
        # Allowed git hosts ride the org's live layer too: none.
        return []

    async def org_only_addresses(self) -> list[object]:
        # So do the org's org-only addresses: none.
        return []


__all__ = [
    "FLOW_ABOVE_ENV",
    "FLOW_LEVEL_ENV",
    "ConfiguredApprovals",
    "ConfiguredEngineCeilings",
    "ConfiguredOrgAccessPolicies",
    "UnpinnedRunPolicy",
    "edition_acp_opt_in",
    "engine_governance",
    "flow_rule",
    "flow_rules",
    "pipeshub_engine_profiles",
    "refusing_flow_rules",
    "resolve_model",
]
