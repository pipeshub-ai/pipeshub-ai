"""A pinned run's policy: read from ``x.pipeshub.resolved`` and narrowed by the live layer, never read live.

The pin is what admission resolved (``ResolvedRunConfig``): the approval rules and flow rules of every layer
(``approvals``), the org's engine-profile ceiling in the provenance of ``engineProfile.approvals.*``, the Labs
stages (``rollout.*``) and the model (``llm``). ``pin ⊓ live`` is taken when the attempt is built, so a tightening
between attempts applies and a loosening never does; within the attempt ``live`` narrows the governance call by
call. The model's endpoint and key are read when it is built (a secret is never pinned), from an entry that still
serves the pinned provider and model: a ``modelKey`` re-pointed elsewhere does not move the run, and a model the
org no longer configures is refused, never replaced.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final, cast

from app.harness.assembly.model import model_refusal, resolved_model
from app.harness.classification import with_org_classification
from app.harness.run_policy.resolved import (
    Dimensions,
    resolved_approvals,
    resolved_engine_governance,
    resolved_snapshot_ttl_days,
    resolved_tuning,
)
from harness_core.contracts import dump_contract
from harness_core.driver import RunRefused
from harness_core.policy import DimensionPolicyResolver

if TYPE_CHECKING:
    from collections.abc import Mapping

    from pydantic import JsonValue

    from app.control_plane.contracts.generated.resolved_run_config import (
        ResolvedRunConfig,
    )
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
    from harness_core.engines.types import EngineProfile
    from harness_core.logs import HarnessLog

ROLLOUT: Final = "rollout."
TOOL_DISCLOSURE: Final = "toolDisclosure.mode"
# The run's failure code when the model it was admitted on is gone: the web app words it in the reader's language
# (``contracts/harness/vectors/run-error-codes.json``); the message below is what a client without those words shows.
MODEL_NO_LONGER_CONFIGURED_CODE: Final = "model_no_longer_configured"
MODEL_NO_LONGER_CONFIGURED: Final = (
    "The AI model this conversation was using is no longer configured. Send your message again to continue on a "
    "current model, or ask a workspace admin to check it in Workspace → AI Models."
)


def _serves(entry: Mapping[str, Any], provider: str, model: str) -> bool:
    from app.harness.access.configured import machine_name, models_of

    return machine_name(str(entry.get("provider", ""))) == provider and model in models_of(entry)


def serving_entry(entries: list[dict[str, Any]], model_key: str | None, provider: str, model: str) -> dict[str, Any] | None:
    """The configured entry that serves ``provider`` and ``model``: the pinned key's while it still does, else
    another that does. An entry of another provider never lends its key."""
    keyed = next((entry for entry in entries if entry.get("modelKey") == model_key), None)
    if keyed is not None and _serves(keyed, provider, model):
        return keyed
    return next((entry for entry in entries if _serves(entry, provider, model)), None)


class PinnedRunPolicy:
    """``RunPolicy`` over a run's pin."""

    pinned = True

    def __init__(
        self,
        run: RunContext,
        pin: ResolvedRunConfig,
        *,
        deployment: DeploymentLayer,
        live: LiveLayerCache | None,
        log: HarnessLog,
        resolver: DimensionPolicyResolver | None = None,
    ) -> None:
        self._run = run
        self._pin = pin
        self._deployment = deployment
        self._live = live
        self._log = log
        self._resolver = resolver if resolver is not None else DimensionPolicyResolver()
        self._narrowed: Dimensions | None = None
        self._layer: LiveLayer | None = None
        self._pinned_config: Mapping[str, JsonValue] | None = None

    @property
    def _agent_key(self) -> str | None:
        return self._run.agent.agent_key if self._run.agent is not None else None

    def _config(self) -> Mapping[str, JsonValue]:
        if self._pinned_config is None:
            self._pinned_config = cast("Mapping[str, JsonValue]", dump_contract(self._pin.effective_config))
        return self._pinned_config

    async def _dimensions(self) -> Dimensions:
        """``pin ⊓ live`` as this attempt sees it: taken once, when the attempt is built."""
        if self._narrowed is None:
            config = self._config()
            if self._live is not None:
                layer = self._layer = await self._live.current(self._run.org_id)
                config = self._resolver.narrowed_by_live(config, layer.for_agent(self._agent_key))
                self._log.info("run_policy.live", liveRevision=layer.revision, stale=layer.stale)
            self._narrowed = cast("Dimensions", config["dimensions"])
        return self._narrowed

    async def approvals(self) -> RunApprovals:
        approvals = resolved_approvals(await self._dimensions(), self._deployment)
        self._log.info(
            "approvals.policy", layers=len(approvals.rules.layers), agentScoped=self._agent_key is not None, pinned=True
        )
        return with_org_classification(approvals, self._layer, self._log)

    async def engine_governance(self, approvals: RunApprovals, profile: EngineProfile) -> LayeredApprovalPolicy:
        return resolved_engine_governance(await self._dimensions(), approvals, profile)

    def stages(self) -> Mapping[str, bool]:
        dimensions = cast("Mapping[str, Mapping[str, JsonValue]]", self._config()["dimensions"])
        # A switch only: the section also holds routing lists (``rollout.durable.*``), which no flag reader asks for.
        return {
            path.removeprefix(ROLLOUT): entry["value"]
            for path, entry in dimensions.items()
            if path.startswith(ROLLOUT) and isinstance(entry["value"], bool)
        }

    def tool_disclosure(self) -> str | None:
        dimensions = cast("Mapping[str, Mapping[str, JsonValue]]", self._config()["dimensions"])
        entry = dimensions.get(TOOL_DISCLOSURE)
        return entry["value"] if entry is not None and isinstance(entry["value"], str) else None

    def snapshot_ttl_days(self) -> int:
        return resolved_snapshot_ttl_days(cast("Dimensions", self._config()["dimensions"]))

    def tuning(self) -> RunTuning:
        return resolved_tuning(cast("Dimensions", self._config()["dimensions"]))

    def dimension(self, path: str) -> JsonValue | None:
        entry = cast("Mapping[str, Mapping[str, JsonValue]]", self._config()["dimensions"]).get(path)
        return entry["value"] if entry is not None else None

    async def model(self, run: RunContext, services: RunServices) -> ResolvedModel:
        llm = self._pin.llm
        if llm is None:
            # No LLM was configured when the run was admitted: today's answer for that, not a moved model's.
            from app.utils.llm import LLM_MISSING_FOR_CHAT, LLMNotConfiguredError

            self._log.warning("run_policy.model_refused", reasonCode="model_not_pinned")
            raise model_refusal(LLMNotConfiguredError(LLM_MISSING_FOR_CHAT))
        return await self._build(run, services, llm.model_key, llm.provider, llm.model)

    async def fallback_model(self, run: RunContext, services: RunServices) -> ResolvedModel:
        return await self._build(run, services, run.model.model_key, run.model.provider, run.model.model)

    async def _build(
        self, run: RunContext, services: RunServices, model_key: str | None, provider: str, model: str
    ) -> ResolvedModel:
        from app.harness.assembly.context import ModelChoice
        from app.harness.credentials.sources import configured_llm_entries
        from app.utils.aimodels import get_generator_model_async

        entry: dict[str, Any] | None = None
        # The cached document first; one fresh read before the model counts as gone.
        for fresh in (False, True):
            entry = serving_entry(
                await configured_llm_entries(services.config_service(), fresh=fresh), model_key, provider, model
            )
            if entry is not None:
                break
        if entry is None:
            self._log.warning("run_policy.model_refused", reasonCode=MODEL_NO_LONGER_CONFIGURED_CODE, provider=provider)
            raise RunRefused(MODEL_NO_LONGER_CONFIGURED_CODE, MODEL_NO_LONGER_CONFIGURED)
        try:
            llm = await get_generator_model_async(str(entry.get("provider")), entry, model, run.model.reasoning_effort)
        except Exception as exc:
            raise model_refusal(exc) from exc
        choice = ModelChoice(
            provider=provider,
            model=model,
            model_key=str(entry.get("modelKey") or model_key or ""),
            model_name=model,
            reasoning_effort=run.model.reasoning_effort,
        )
        return resolved_model(run.model_copy(update={"model": choice}), llm, entry)

    async def allowed_sites(self) -> list[Mapping[str, JsonValue]]:
        if self._live is None:
            return []
        from app.harness.websites import live_sites

        layer = await self._live.current(self._run.org_id)
        return list(live_sites(layer.websites, self._agent_key))

    async def allowed_repository_hosts(self, *, web: bool = False) -> list[Mapping[str, JsonValue]]:
        if self._live is None:
            return []
        from app.harness.repositories import live_repository_hosts

        layer = await self._live.current(self._run.org_id)
        return live_repository_hosts(layer.repositories, self._agent_key, web=web)

    async def org_only_addresses(self) -> list[object]:
        if self._live is None:
            return []
        from harness_core.security.reachability import org_only_entries

        layer = await self._live.current(self._run.org_id)
        return org_only_entries(layer.websites)

    def live(self, governance: ApprovalPolicy) -> ApprovalPolicy:
        if self._live is None:
            return governance
        from app.control_plane.runtime.live_policy import LiveNarrowedApprovals

        cache, org_id = self._live, self._run.org_id
        return LiveNarrowedApprovals(
            governance, lambda: cache.current(org_id), agent_key=self._agent_key, log=self._log
        )


__all__ = [
    "MODEL_NO_LONGER_CONFIGURED",
    "MODEL_NO_LONGER_CONFIGURED_CODE",
    "PinnedRunPolicy",
    "serving_entry",
]
