"""The run's model policy (P4.7, ``HARNESS_MODEL_FALLBACK``): fallback over the routes the run pinned.

Off: the run's port as before and no policy. On: a ``RoutedModelPort`` whose primary
is that port; a fallback route's port is built the first time the kernel moves to
it, from the org's configured model with that provider and name, reached and
metered exactly as the primary is. A route the org no longer configures, or one
pinned to a model account (the loop reaches no account but the org's), is refused
and the kernel tries the next route.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.utils.env_flags import HARNESS_MODEL_FALLBACK, env_flag
from harness_core.access.model_roles import MODEL_ROLES_DIMENSION, engine_role_routes
from harness_core.kernel.durable.routing import RoutedModelPort
from harness_core.models.errors import ModelRouteNotPinned
from harness_core.models.policy import ModelPolicy, ModelPolicyConfig

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from app.harness.assembly.context import RunContext
    from app.harness.assembly.model import ResolvedModel
    from app.harness.assembly.services import RunServices
    from harness_core.contracts.generated.run_spec import ModelPolicy as SpecModelPolicy
    from harness_core.contracts.generated.run_spec import ModelRoute
    from harness_core.kernel.durable.ports import DurableModelPort
    from harness_core.logs import HarnessLog
    from harness_core.models.policy import PolicyRoute

async def _configured_key(services: RunServices, provider: str, model: str) -> str | None:
    """The ``modelKey`` of the org's first configured model that lists ``model`` for ``provider`` (a machine name)."""
    from app.harness.access.configured import machine_name, models_of
    from app.harness.credentials.sources import configured_llm_entries

    for entry in await configured_llm_entries(services.config_service()):
        if (
            machine_name(str(entry.get("provider", ""))) == provider
            and model in models_of(entry)
            and isinstance(entry.get("modelKey"), str)
        ):
            return str(entry["modelKey"])
    return None


async def configured_model_key(services: RunServices, route: PolicyRoute) -> str:
    """The ``modelKey`` of the org's configured model that serves ``route``."""
    key = await _configured_key(services, route.provider, route.model)
    if key is None:
        raise ModelRouteNotPinned(f"route {route.route_id!r} is not a model the org configures")
    return key


def compaction_route(run: RunContext, *, primary_account: bool) -> tuple[str, str] | None:
    """GA-15: the route the run's pin maps the compaction role to, folded as Node folds it; ``None``: the primary."""
    if run.resolved is None:
        return None
    entry = run.resolved.effective_config.dimensions.get(MODEL_ROLES_DIMENSION)
    roles = engine_role_routes(entry.value if entry is not None else None, primary_account=primary_account)
    return roles.get("compaction")


async def compaction_model(
    run: RunContext,
    services: RunServices,
    *,
    primary_account: bool,
    resolve: Callable[[RunContext, RunServices], Awaitable[ResolvedModel]],
    meter: Callable[[ResolvedModel, RunContext], ResolvedModel],
    log: HarnessLog,
) -> ResolvedModel | None:
    """The model the run's summaries call when its pin maps the compaction role (GA-15), reached and metered as a
    fallback route is; ``None``: they call the run's own model. A mapped model the org no longer configures is
    logged and the run's own model summarises, so a compaction never fails the run."""
    from app.harness.assembly.context import ModelChoice

    route = compaction_route(run, primary_account=primary_account)
    if route is None:
        return None
    provider, name = route
    key = await _configured_key(services, provider, name)
    if key is None:
        log.warning("run.compaction_model_unavailable", provider=provider, model=name)
        return None
    choice = ModelChoice(provider=provider, model=name, model_key=key, model_name=name)
    routed = run.model_copy(update={"model": choice})
    return meter(await resolve(routed, services), routed)


class ConfiguredRoutePorts:
    """``RoutePorts`` for a PipesHub loop run."""

    def __init__(
        self,
        run: RunContext,
        services: RunServices,
        routes: tuple[ModelRoute, ...],
        *,
        resolve: Callable[[RunContext, RunServices], Awaitable[ResolvedModel]],
        meter: Callable[[ResolvedModel, RunContext], ResolvedModel],
    ) -> None:
        self._run = run
        self._services = services
        self._routes = routes
        self._resolve = resolve
        self._meter = meter

    async def port(self, route: PolicyRoute) -> DurableModelPort:
        from app.harness.assembly.context import ModelChoice

        if self._routes[route.index].access is not None:
            raise ModelRouteNotPinned(f"route {route.route_id!r} names a model account the loop does not reach")
        key = await configured_model_key(self._services, route)
        choice = ModelChoice(
            provider=route.provider,
            model=route.model,
            model_key=key,
            model_name=route.model,
            reasoning_effort=self._run.model.reasoning_effort,
        )
        routed = self._run.model_copy(update={"model": choice})
        return self._meter(await self._resolve(routed, self._services), routed).port


def model_fallback_enabled() -> bool:
    """Whether this deployment moves a run whose provider is down to a pinned alternate (G-48): the loop's kernel here,
    an engine's calls in the Model Gateway (``model_gateway_main``)."""
    return env_flag(HARNESS_MODEL_FALLBACK)


def build_model_policy(
    spec_model: SpecModelPolicy,
    model: ResolvedModel,
    route_ports: Callable[[], ConfiguredRoutePorts],
) -> tuple[DurableModelPort, ModelPolicyConfig | None]:
    """The kernel's model port and policy; flag off: the run's port as before and no policy."""
    if not model_fallback_enabled():
        return model.port, None
    policy = ModelPolicy.from_spec(spec_model)
    return RoutedModelPort(policy, model.port, route_ports()), ModelPolicyConfig()


__all__ = [
    "ConfiguredRoutePorts",
    "build_model_policy",
    "compaction_model",
    "compaction_route",
    "configured_model_key",
    "model_fallback_enabled",
]
