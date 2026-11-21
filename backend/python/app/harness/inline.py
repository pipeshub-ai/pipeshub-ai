"""The inline profile (REPORT 6.2.1): the query service claims the runs Node admits for it.

Node admits an interactive run as ``inline`` and hints the query service with
the run's ids; the query service claims it through Node like any worker and
runs it in-process on the same ``RunDriver``, so the first token needs no
queue hop. The hint is advisory: if it is lost or refused, or every slot is
busy, the run waits in the queue and the sweeper hands it to dispatched
workers after ``T_claim``. Once built, the claimer also hears ``run-ready``
(a resumed run after an approval, LAT1) and holds approvals in the run,
woken over Redis, as a dispatched worker does.
"""

from __future__ import annotations

import asyncio
import os
import signal
import socket
from typing import TYPE_CHECKING, Any

from app.harness.assembly.assembler import PipesHubRuntimeAssembler
from app.harness.assembly.services import ContainerServices
from app.harness.journal_node import NodeJournal
from app.harness.journal_node.credentials import ConfiguredWorkerCredential
from app.harness.livebus import RedisLiveBus
from app.harness.provider_gate import provider_admission
from app.harness.run_policy.live import WorkerLiveLayer, worker_live_layer
from app.harness.run_policy.select import RunPolicies
from app.harness.telemetry import harness_tracer
from app.harness.worker.capabilities import (
    advertised,
    code_execution_configured,
    log_capabilities,
)
from app.harness.worker.claimer import RunClaimer, run_slots
from app.harness.workspace.node_store import node_child_workspaces
from harness_core.clock import SystemClock
from harness_core.driver import RunDriver
from harness_core.logs import HarnessLog

if TYPE_CHECKING:
    from app.harness.run_ready import RedisRunReadySignal

LEASE_MS = 60_000
RENEW_S = 20.0
# Node's worker-only listener (AGENT_HARNESS_INTERNAL_PORT), never its public port (C-26).
DEFAULT_STATE_URL = "http://localhost:3002"


def node_state_url() -> str:
    return os.getenv("AGENT_HARNESS_STATE_URL", DEFAULT_STATE_URL)


class InlineClaimer:
    """Built on the first hint: a query service that never serves a durable run pays nothing."""

    def __init__(self, container: Any) -> None:  # noqa: ANN401 - the query service's container
        self._container = container
        self.credentials = ConfiguredWorkerCredential(container.config_service())
        self._journal: NodeJournal | None = None
        self._claimer: RunClaimer | None = None
        self._live: RedisLiveBus | None = None
        self._ready: RedisRunReadySignal | None = None
        self._live_layer: WorkerLiveLayer | None = None
        self._clock = SystemClock()
        self._lock = asyncio.Lock()

    def live_layer(self) -> WorkerLiveLayer:
        """The process's one live layer, built when first asked for: the claimer's runs and the agent-save check
        (AG1 under ``CONTROL_PLANE_POLICY_STORE=on``) read the same cache."""
        if self._live_layer is None:
            self._live_layer = worker_live_layer(
                self._container, base_url=node_state_url(), credentials=self.credentials, clock=self._clock
            )
        return self._live_layer

    async def _ensure(self) -> RunClaimer:
        async with self._lock:
            return await self._build()

    async def _build(self) -> RunClaimer:
        if self._claimer is None:
            # No engine kernel here (E1 registers it only in harness_main): Node must leave engine runs to a worker.
            capabilities = advertised(engines=False, code=code_execution_configured())
            self._journal = NodeJournal(
                base_url=node_state_url(),
                credentials=self.credentials,
                role="inline",
                lease_ms=LEASE_MS,
                capabilities=capabilities,
            )
            clock = self._clock
            self._live = await RedisLiveBus.create(self._container.config_service())
            driver = RunDriver(
                journal=self._journal,
                assembler=PipesHubRuntimeAssembler(
                    ContainerServices(self._container),
                    activity=self._live.activity_bus() if self._live is not None else None,
                    policies=RunPolicies(ContainerServices(self._container), live=self.live_layer()),
                    # G-45: an inline turn's model calls count against the deployment's provider limit too.
                    admission=await provider_admission(self._container.config_service(), clock),
                ),
                clock=clock,
                live=self._live,
                children=self._journal,
                child_workspaces=node_child_workspaces(self._journal),
                renew_interval_s=RENEW_S,
                # A decision and a Stop reach the run in flight over Redis (LAT1, UX3).
                interactions=self._live.interaction_wakeup() if self._live is not None else None,
                cancel_wakeup=self._live.interaction_wakeup() if self._live is not None else None,
                tracer=harness_tracer("inline"),
            )
            self._ready = self._live.run_ready_signal() if self._live is not None else None
            worker_id = f"{socket.gethostname()}-{os.getpid()}-inline"
            self._claimer = RunClaimer(
                journal=self._journal,
                driver=driver,
                worker_id=worker_id,
                slots=run_slots(),
                clock=clock,
                role="inline",
                ready=self._ready,
            )
            log_capabilities(HarnessLog.named("app.harness.worker", workerId=worker_id, role="inline"), capabilities)
            self._claimer.start()
        return self._claimer

    async def hint(self, org_id: str, run_id: str) -> bool:
        return await (await self._ensure()).claim_run(org_id, run_id)

    def begin_drain(self) -> None:
        if self._claimer is not None:
            _ = self._claimer.begin_drain()

    async def aclose(self) -> None:
        if self._claimer is not None:
            await self._claimer.stop()
        if self._live_layer is not None:
            await self._live_layer.aclose()
        if self._ready is not None:
            await self._ready.aclose()
        if self._journal is not None:
            await self._journal.aclose()
        if self._live is not None:
            await self._live.aclose()


def drain_on_termination(state: Any, signals: tuple[int, ...] = (signal.SIGTERM, signal.SIGINT)) -> None:  # noqa: ANN401 - app.state
    """Starts the inline drain when the process is told to stop (RU-1c).

    Uvicorn handles the signal by waiting for open connections (legacy SSE
    streams can hold it for minutes) before the lifespan shutdown runs, so
    the drain chains onto its handler instead of waiting for the lifespan.
    Installed from the lifespan, i.e. after uvicorn took the signals; its
    handler runs first-hand afterwards.
    """
    loop = asyncio.get_running_loop()

    def start_drain() -> None:
        begin = getattr(getattr(state, "harness_inline", None), "begin_drain", None)
        if callable(begin):
            begin()

    for signum in signals:
        previous = signal.getsignal(signum)
        if not callable(previous):
            continue  # not under uvicorn: the lifespan shutdown drains

        def handler(sig: int, frame: Any, previous: Any = previous) -> None:  # noqa: ANN401
            loop.call_soon_threadsafe(start_drain)
            previous(sig, frame)

        _ = signal.signal(signum, handler)


__all__ = ["DEFAULT_STATE_URL", "InlineClaimer", "drain_on_termination", "node_state_url"]
