"""Model gateway process role (E3): OpenAI chat completions for engines, on ``MODEL_GATEWAY_PORT`` (8096).

Run with ``python -m app.model_gateway_main``. Sandboxes reach it only through
the egress proxy (``http://model-gateway:8097`` → this listener), which injects
the run's gateway token. The process holds no worker credential: it verifies
tokens against Node's published gateway keys, reads each run's model grant
from Node with that run's own token, reaches models through the org's
configured keys, and reserves and charges each call on the run's budget ledger
account in Node (P6.5), with the same token.

A process of its own rather than a listener in the query service or the worker:
the only sandbox-reachable listener that needs no run state then lives in a
process that holds model keys and nothing else (no connector credentials, no
graph or vector clients, no worker credential), it can be restarted or scaled
apart from chat and runs, and it costs about 200 MB (measured peak RSS with a
resolved route, LangChain's optional tokenizer import skipped). The process
monitor starts it only where engines can run (with the durable worker), so a
laptop or eval install without engines pays nothing.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import sys
from typing import TYPE_CHECKING, cast

from app.control_plane.runtime.live_layer import ReloadingLiveLayerCache
from app.harness.access.budget import NodeBudgetLedger
from app.harness.access.configured import ConfiguredModelAccess
from app.harness.access.grants import NodeRunModelGrants
from app.harness.access.offering import harness_offering
from app.harness.access.refusals import NodeRefusalSink
from app.harness.access.spend import RedisSpendCounter
from app.harness.assembly.model_policy import model_fallback_enabled
from app.harness.classification import classification_port
from app.harness.gateway.keys import ConfiguredGatewayKeys
from app.harness.gateway.org_binding import OrgBoundGatewayTokenVerifier
from app.harness.inline import node_state_url
from app.harness.journal_node.credentials import ConfiguredWorkerCredential
from app.harness.journal_node.live_layer import NodeLiveLayerSource
from app.harness.metrics_exposition import worker_metrics_exposition
from app.harness.placement import model_regions
from app.harness.provider_gate import provider_admission
from app.harness.run_policy.live import worker_live_layer
from app.harness.run_policy.select import org_access_policies as policy_source
from app.harness.telemetry import harness_tracer
from app.telemetry.modules.harness_gateway_metrics import BackendGatewayMetrics
from app.telemetry.pusher import MetricsPusher
from app.utils.gc_startup import freeze_startup_heap
from app.utils.output_limits import model_output_limits
from harness_core.access.guard import GuardedModelAccess
from harness_core.access.spend import OutputAllowance
from harness_core.access.token_count import preload_in_background
from harness_core.clock import SystemClock
from harness_core.gateway.cut_calls import CutCalls
from harness_core.gateway.model_gateway import ModelGateway
from harness_core.gateway.model_server import MODEL_GATEWAY_PORT, ModelGatewayServer
from harness_core.gateway.refusal_reports import RefusalReporter
from harness_core.ids import RandomIds
from harness_core.logs import HarnessLog
from harness_engines.release import release_model_apis
from harness_engines.vendor_rules import release_vendor_rules

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from app.config.configuration_service import ConfigurationService
    from app.control_plane.runtime.ports import LiveLayerCache
    from harness_core.access.ports import (
        ConversationClassification,
        OrgAccessPolicies,
        SpendCounter,
    )
    from harness_core.clock import Clock

ENV_PORT = "MODEL_GATEWAY_PORT"
ENV_ALLOWANCE = "MODEL_GATEWAY_OUTPUT_ALLOWANCE_TOKENS"
ENV_DEFAULT_ALLOWANCE = "MODEL_GATEWAY_DEFAULT_OUTPUT_TOKENS"
ENV_CONTROL_PLANE_PIN = "CONTROL_PLANE_PIN"
CONTROL_PLANE_PIN_MODES = ("off", "shadow", "on")
_LOG = HarnessLog.named("app.model_gateway")


def output_allowance() -> OutputAllowance:
    """The model's published maximum output, or the default for a model the table does not
    know; ``MODEL_GATEWAY_OUTPUT_ALLOWANCE_TOKENS`` caps it (smaller reservations, a looser bound)."""
    default = OutputAllowance()
    cap, unknown = os.getenv(ENV_ALLOWANCE), os.getenv(ENV_DEFAULT_ALLOWANCE)
    return OutputAllowance(
        default_tokens=int(unknown) if unknown and unknown.isdigit() else default.default_tokens,
        cap_tokens=int(cap) if cap and cap.isdigit() else None,
    )


async def legacy_spend(config_service: ConfigurationService) -> SpendCounter | None:
    """E3's Redis counter, read once per run to carry a run admitted before the ledger into
    it (P6.5 migration); none when the deployment has no Redis (nothing to carry)."""
    from app.services.redis.config import ClientOptions, RedisConnectionConfig
    from app.services.redis.connection_provider_factory import get_redis_provider

    try:
        provider = get_redis_provider(RedisConnectionConfig.from_redis_config(await config_service.get_redis_config()))
        client = provider.create_client(ClientOptions(decode_responses=True))
        _ = await client.ping()  # pyright: ignore[reportUnknownMemberType]
    except Exception as exc:
        _LOG.info("model_gateway.legacy_spend_absent", reason="redis_unreachable", errorType=type(exc).__name__)
        return None
    return RedisSpendCounter(client, namespace=provider.key_namespace or "")


class InvalidControlPlanePin(ValueError):
    reason_code = "invalid_control_plane_pin"


def control_plane_pin() -> str:
    """``CONTROL_PLANE_PIN`` as Node parses it (``parseControlPlanePin``): ``on`` unset or blank (C4.04), and a
    value it does not know stops the role instead of running on a guess."""
    value = (os.getenv(ENV_CONTROL_PLANE_PIN) or "").strip().lower()
    if not value:
        return "on"
    if value not in CONTROL_PLANE_PIN_MODES:
        raise InvalidControlPlanePin(f"{ENV_CONTROL_PLANE_PIN} is not one of {', '.join(CONTROL_PLANE_PIN_MODES)}")
    return value


def live_layers(
    config_service: ConfigurationService, *, node_url: str, clock: Clock, container: object | None = None
) -> Callable[[], LiveLayerCache]:
    """The orgs' live layers, built once when first asked. With the process's ``container`` the layer is kept
    current by the change topic; without one, by its time to live alone."""
    credentials = ConfiguredWorkerCredential(config_service)
    built: list[LiveLayerCache] = []

    def live() -> LiveLayerCache:
        if not built:
            built.append(
                worker_live_layer(container, base_url=node_url, credentials=credentials, clock=clock)
                if container is not None
                else ReloadingLiveLayerCache(NodeLiveLayerSource(base_url=node_url, credentials=credentials), clock)
            )
        return built[0]

    return live


def org_access_policies(
    config_service: ConfigurationService, *, node_url: str, clock: Clock, container: object | None = None,
    live: Callable[[], LiveLayerCache] | None = None,
) -> OrgAccessPolicies:
    """Each org's narrowing of the vendor rules. With the control plane's pin ``on``, or the policy store as the
    record (which Node allows only with the pin ``on``), it is the org's live layer (CPD-6: a row switched off
    reaches a run's next model call within the live bound, with no document read per call); otherwise the
    model-access document, read at every call as before."""
    layers = live or live_layers(config_service, node_url=node_url, clock=clock, container=container)
    return policy_source(config_service, layers, pin_on=control_plane_pin() == "on")


def conversation_classification(live: Callable[[], LiveLayerCache]) -> ConversationClassification | None:
    """Whether a run's conversation may reach the call's model (OWD-15, GA-16): the org's classification is a section
    of its policy layer, which only the policy store holds, served with the live layer; with the pin off nothing is
    classified."""
    if control_plane_pin() != "on":
        return None
    return classification_port().gateway(live())


def guarded_access(
    config_service: ConfigurationService,
    policies: OrgAccessPolicies,
    declared_region: Callable[[str, str, str], Awaitable[str | None]] | None = None,
) -> GuardedModelAccess:
    """The org's configured models and accounts, behind the signed vendor rules, the install's offering
    and each org's narrowing, region pin included (A1, MG-15). The offering is read now: an invalid value stops
    the role. ``declared_region``: the region an org declared for a model (its classification), for a route whose
    configuration names none."""
    return GuardedModelAccess(
        ConfiguredModelAccess(config_service),
        rules=release_vendor_rules,
        policies=policies,
        offering=harness_offering(),
        engine_apis=release_model_apis(),
        declared_region=declared_region,
        regions=model_regions(),
    )


def model_declared_regions(live: Callable[[], LiveLayerCache]) -> Callable[[str, str, str], Awaitable[str | None]] | None:
    """The regions orgs declared for their models: their classification is served with the live layer, so with the
    pin off none is declared."""
    if control_plane_pin() != "on":
        return None
    return classification_port().declared_regions(live())


async def create_gateway(
    config_service: ConfigurationService, *, node_url: str | None = None, container: object | None = None
) -> ModelGateway:
    clock = SystemClock()
    base_url = node_url or node_state_url()
    live = live_layers(config_service, node_url=base_url, clock=clock, container=container)
    return ModelGateway(
        verifier=OrgBoundGatewayTokenVerifier(ConfiguredGatewayKeys(config_service), clock),
        grants=NodeRunModelGrants(base_url=base_url),
        resolver=guarded_access(
            config_service,
            org_access_policies(config_service, node_url=base_url, clock=clock, live=live),
            model_declared_regions(live),
        ),
        budget=NodeBudgetLedger(base_url=base_url),
        clock=clock,
        ids=RandomIds(),
        allowance=output_allowance(),
        limits=model_output_limits(),
        legacy_spend=await legacy_spend(config_service),
        tracer=harness_tracer("model-gateway"),
        refusals=RefusalReporter(NodeRefusalSink(base_url=base_url)),
        classification=conversation_classification(live),
        # G-45: the deployment's one limit per provider, which the workers' loop runs take their calls from too.
        gate=await provider_admission(config_service, clock),
        # WP-66: how long a cut call's bound stays held, and a model's output rate before one of its calls finished.
        cut_calls=CutCalls.from_env(),
        # G-48 (WP-100, HARNESS_MODEL_FALLBACK): an engine's call moves to the run's pinned alternate as the loop's kernel moves.
        fallback=model_fallback_enabled(),
    )


async def run_gateway(port: int | None = None) -> None:
    from app.harness.assembly.services import HarnessServices

    services = HarnessServices()
    config_service = services.config_service()
    # The budget's tokenizer, loaded off the event loop while the gateway starts (R14-LOOP).
    _ = preload_in_background()
    server = ModelGatewayServer(
        await create_gateway(config_service, container=services),
        metrics=BackendGatewayMetrics(),
        # C9.04: what Node's control-plane metrics route scrapes, with the worker credential.
        exposition=worker_metrics_exposition(config_service),
    )
    bound = await server.start(host="0.0.0.0", port=port or int(os.getenv(ENV_PORT) or MODEL_GATEWAY_PORT))
    _LOG.info("model_gateway.started", port=bound)
    # Its request metrics leave the process as every service's do (a no-op with telemetry off).
    pusher = MetricsPusher(config_service, "model_gateway", services.logger())
    await pusher.start()
    # As the worker's (WP-77): a gen-2 collection over the imported app paused every stream in flight.
    _ = freeze_startup_heap(_LOG)
    stopped = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(signum, stopped.set)
    try:
        _ = await stopped.wait()
    finally:
        await pusher.stop()
        await server.stop()
        shutdown = cast("object", services.shutdown_resources())  # pyright: ignore[reportUnknownMemberType]
        if asyncio.iscoroutine(shutdown):
            await shutdown


def skip_unused_tokenizer() -> None:
    """LangChain imports ``transformers`` (and with it torch, about 500 MB measured) only
    for a GPT-2 token counter; the gateway counts no tokens through LangChain."""
    _ = sys.modules.setdefault("transformers", None)  # pyright: ignore[reportArgumentType]


if __name__ == "__main__":
    skip_unused_tokenizer()
    asyncio.run(run_gateway())
