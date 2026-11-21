"""Dedicated trusted process for durable agent runs.

Run with ``python -m app.harness_main``. The process is disabled by deployment
configuration unless explicitly added as a worker service; it does not expose
an HTTP port and never starts a sandbox with its worker credential or attempt
tokens.

It claims runs from Node's queue (the Mongo run collection) into run slots
and runs each on ``RunDriver``; the message broker only wakes it. Its
container is ``HarnessServices``, not the query service's: it connects no
retrieval, graph, vector or LLM client until a run needs one (P0.9), though it
imports their code at start (C-17).
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import socket
from contextlib import suppress
from typing import TYPE_CHECKING

from app.core.envelope import configure_from_config as configure_envelope
from app.core.org_context import spawn_detached, spawn_in_request
from app.harness.adapters.pipeshub_object_store import PipesHubBinaryObjectStore
from app.harness.assembly.assembler import PipesHubRuntimeAssembler
from app.harness.assembly.services import ContainerServices, HarnessServices
from app.harness.engines.worker import (
    carries_sign_ins,
    engine_worker,
    plan_engines,
    references_job,
)
from app.harness.inline import node_state_url
from app.harness.journal_node import NodeJournal
from app.harness.journal_node.credentials import ConfiguredWorkerCredential
from app.harness.livebus import RedisLiveBus
from app.harness.mcp_hub.hub import process_mcp_hub
from app.harness.mcp_runner.process import process_mcp_runner
from app.harness.provider_gate import provider_admission
from app.harness.run_policy.live import worker_live_layer
from app.harness.run_policy.select import RunPolicies
from app.harness.sandbox.client import process_broker_client
from app.harness.supply_chain.revocations import RevocationRefresher, revocations_url
from app.harness.telemetry import harness_tracer
from app.harness.worker.capabilities import (
    WORKER_CAPABILITIES,
    advertised,
    code_execution_configured,
    log_capabilities,
)
from app.harness.worker.claimer import (
    DRAIN_S,
    DispatchHints,
    RunClaimer,
    preempt_min_slice_s,
    preempt_overcommit,
    run_slots,
)
from app.harness.worker.warmup import warm_up
from app.harness.workspace.node_store import node_child_workspaces
from app.modules.transformers.blob_storage import (
    close_shared_redis,
    close_shared_session,
)
from app.services.messaging.config import ConsumerType, Topic, get_message_broker_type
from app.services.messaging.messaging_factory import MessagingFactory
from app.services.messaging.utils import MessagingUtils
from app.telemetry.pusher import MetricsPusher
from app.utils.env_flags import validate_env_flags
from app.utils.gc_startup import freeze_startup_heap
from app.utils.process_hardening import mark_process_non_dumpable
from harness_core.access.token_count import preload_in_background
from harness_core.clock import SystemClock
from harness_core.driver import RunDriver
from harness_core.engines.durable.kernel import ENGINE
from harness_core.interactions import NoWakeup
from harness_core.logs import HarnessLog
from harness_core.priority import PreemptRequests

if TYPE_CHECKING:
    from harness_core.contracts.generated.worker_capabilities import WorkerCapabilities
    from harness_core.driver import RuntimeAssembler

WORKER_LEASE_MS = 60_000
WORKER_RENEW_MS = 20_000
WORKER_GROUP_ID = "agent-harness-workers"


async def run_worker(
    *,
    assembler: RuntimeAssembler | None = None,
    driver_type: type[RunDriver] = RunDriver,
    lease_ms: int = WORKER_LEASE_MS,
    renew_ms: int = WORKER_RENEW_MS,
    capabilities: WorkerCapabilities = WORKER_CAPABILITIES,
    drain_s: float = DRAIN_S,
) -> None:
    """Serve runs until SIGINT or SIGTERM.

    Production passes no arguments, and imports the assembly's stack before
    it claims (``warm_up``). The L4 fault suite
    (integration-tests/harness/fault) runs this same composition with a
    scripted assembler (so nothing to warm), a recording driver, a short
    lease and drain bound and, for the mixed-release scenarios, another
    build's advertisement.
    """
    validate_env_flags()
    # The worker spawns STDIO MCP servers as the query service does (main #3887).
    mark_process_non_dumpable()
    # The budget's tokenizer for loop runs, loaded off the event loop while the worker starts (R14-LOOP).
    _ = preload_in_background()
    services = HarnessServices()
    logger = services.logger()  # the consumer's own logging
    worker_id = os.getenv("AGENT_HARNESS_WORKER_ID", f"{socket.gethostname()}-{os.getpid()}")
    run_services = ContainerServices(services)
    # The run hints and control-plane changes it consumes are signed envelopes (main #4703): without the key, an
    # enforcing deployment rejects every one of them as no_key.
    await configure_envelope(services.config_service())
    # Decided before the journal client exists: its advertisement says whether this worker runs engines.
    engine_plan = await plan_engines(run_services) if assembler is None else None
    # Before the advertisement too: whether this worker carries a person's own sign-in needs its relay (SIGNGAP).
    live = await RedisLiveBus.create(services.config_service())
    if capabilities is WORKER_CAPABILITIES:
        capabilities = advertised(
            engines=engine_plan is not None,
            code=code_execution_configured(),
            signin=carries_sign_ins(relay=live is not None),
        )
    credentials = ConfiguredWorkerCredential(services.config_service())
    journal = NodeJournal(
        base_url=node_state_url(),
        credentials=credentials,
        role="dispatched",
        lease_ms=lease_ms,
        capabilities=capabilities,
    )
    clock = SystemClock()
    # What pinned runs are narrowed by while they run (CPD-6); nothing is joined or read until one is claimed.
    live_layer = worker_live_layer(services, base_url=node_state_url(), credentials=credentials, clock=clock)
    policies = RunPolicies(run_services, live=live_layer)
    # Without Redis a held approval still sees its decision, at the next journal poll.
    wakeup = live.interaction_wakeup() if live is not None else NoWakeup()
    # G-45: the claimer asks a background run to give its slot to a higher class; the kernels yield it at a boundary.
    overcommit = preempt_overcommit()
    preempt = PreemptRequests() if overcommit > 0 else None
    # G-45: the loop's own model calls take the same provider slots as engine calls at the model gateway.
    admission = await provider_admission(services.config_service(), clock)
    engines = (
        engine_worker(
            engine_plan,
            services=run_services,
            journal=journal,
            clock=clock,
            objects=PipesHubBinaryObjectStore(await run_services.blob_store()),
            interactions=wakeup,
            renew_interval_s=renew_ms / 1000,
            activity=live.activity_bus() if live is not None else None,
            policies=policies,
            signin_relay=live.signin_relay() if live is not None else None,
            preempt=preempt,
            admission=admission,
        )
        if engine_plan is not None
        else None
    )
    driver = driver_type(
        journal=journal,
        assembler=assembler
        or PipesHubRuntimeAssembler(
            run_services,
            activity=live.activity_bus() if live is not None else None,
            policies=policies,
            admission=admission,
        ),
        clock=clock,
        live=live,
        children=journal,
        child_workspaces=node_child_workspaces(journal),
        renew_interval_s=renew_ms / 1000,
        interactions=wakeup,
        # Stop reaches a model call or reads in flight over Redis (UX3); without Redis, at the next step.
        cancel_wakeup=live.interaction_wakeup() if live is not None else None,
        kernels={ENGINE: engines.kernel} if engines is not None else {},
        preempt=preempt,
        tracer=harness_tracer("worker"),
    )
    slots = run_slots()
    # Hears runs Node made claimable (LAT1); without Redis the poll alone claims.
    ready = live.run_ready_signal() if live is not None else None
    claimer = RunClaimer(
        journal=journal,
        driver=driver,
        worker_id=worker_id,
        slots=slots,
        clock=clock,
        drain_s=drain_s,
        ready=ready,
        preempt=preempt,
        overcommit=overcommit,
        min_slice_s=preempt_min_slice_s(),
    )
    config = await MessagingUtils.create_consumer_config(
        services,
        client_id=worker_id,
        group_id=WORKER_GROUP_ID,
        topics=[Topic.AGENT_RUN_EVENTS.value],
    )
    consumer = MessagingFactory.create_consumer(
        logger=logger,
        config=config,
        broker_type=get_message_broker_type(),
        consumer_type=ConsumerType.SIMPLE,
    )
    log = HarnessLog.named("app.harness.worker", workerId=worker_id, role="dispatched")
    # Before the first claim: a SIGTERM from then on drains (RU-1), however long the broker takes to join.
    stopped = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        with suppress(NotImplementedError):  # Windows
            loop.add_signal_handler(signum, stopped.set)
    # R41SUPPLY-FU-6: the stored revocation list is in force before the first claim verifies any release data; the
    # worker also fetches the published list and stores what is newer, for itself and the broker.
    revocations = RevocationRefresher(services.config_service(), url=revocations_url())
    await revocations.start()
    if assembler is None:
        # Before the first claim, so a fresh worker's first run (often a recovery) does not pay for it (C-17).
        await warm_up(log)
    serving = contextlib.AsyncExitStack()
    if engines is not None:
        # Pushed first, so its runs' shared clients close after the gateway stops serving them.
        _ = serving.push_async_callback(engines.aclose)
        # The proxy forwards engine runs' tool calls here: up before the first claim.
        await serving.enter_async_context(engines.serving())
    # The Tool Gateway's request metrics leave the process as every service's do (a no-op with telemetry off).
    pusher = MetricsPusher(services.config_service(), "agent_harness_worker", logger)
    await pusher.start()
    # Everything imported and built so far lives as long as the process: no gen-2 collection walks it again (WP-77).
    _ = freeze_startup_heap(log)
    if engine_plan is not None:
        # WP84-FU-3: the admins' Git hosts screen shows an unwritable or full repository cache before any clone.
        from app.harness.repositories import publish_cache_status

        await publish_cache_status(services.config_service())
    claimer.start()
    # C20.20: the org references' background work an edition supplies, beside the runs; none here.
    fills = references_job(engine_plan, journal, worker_id, clock) if engine_plan is not None else None
    if fills is not None:
        fills.start()
    log_capabilities(log, capabilities)
    log.info(
        "worker.started",
        slots=slots,
        leaseMs=lease_ms,
        livebus="redis" if live is not None else "none",
    )
    # The worker serves by polling from here; hints only wake it, and joining a Kafka group takes seconds.
    hints = spawn_detached(consumer.start(DispatchHints(claimer).handle))
    stop = spawn_in_request(stopped.wait())
    _ = await asyncio.wait({hints, stop}, return_when=asyncio.FIRST_COMPLETED)
    if hints.done() and (failure := hints.exception()) is not None:
        log.warning("worker.hints_unavailable", reason="broker_down_polling_only", errorType=type(failure).__name__)
    elif not hints.done():
        _ = hints.cancel()
    try:
        await stopped.wait()
    finally:
        _ = stop.cancel()
        if fills is not None:
            await fills.stop()
        await claimer.stop()
        try:
            _ = await process_mcp_hub.aclose()
        except Exception as exc:
            log.warning("worker.mcp_hub_stop_failed", errorType=type(exc).__name__)
        try:
            _ = await process_mcp_runner.aclose()
        except Exception as exc:
            log.warning("worker.mcp_runners_stop_failed", errorType=type(exc).__name__)
        try:
            await process_broker_client.aclose()
        except Exception as exc:
            log.warning("worker.broker_client_stop_failed", errorType=type(exc).__name__)
        try:
            await consumer.stop()
        except Exception as exc:
            log.warning("worker.hints_stop_failed", errorType=type(exc).__name__)
        await serving.aclose()
        await revocations.stop()
        await pusher.stop()
        try:
            await live_layer.aclose()
        except Exception as exc:
            log.warning("worker.live_layer_stop_failed", errorType=type(exc).__name__)
        if ready is not None:
            await ready.aclose()
        await journal.aclose()
        if live is not None:
            await live.aclose()
        try:
            await close_shared_session()
            await close_shared_redis()
        except Exception as exc:
            log.warning("worker.blob_session_stop_failed", errorType=type(exc).__name__)
        log.info("worker.stopped")
        shutdown = services.shutdown_resources()
        if asyncio.iscoroutine(shutdown):
            await shutdown


if __name__ == "__main__":
    asyncio.run(run_worker())
