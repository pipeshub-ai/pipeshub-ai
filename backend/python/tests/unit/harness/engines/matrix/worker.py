"""One matrix worker: ``RunDriver`` with the ``EngineKernel`` and the production ``PipesHubEngineAssembler``.

Everything around the assembler is ``durable_stack.run_attempt``'s (Node's journal with the worker credential, the
worker's own tool gateway verifying Node's published keys, a Docker broker on the lane's namespace behind the real
egress proxy); the assembler is the one a deployment's worker runs, over ``MatrixServices``.
"""

from __future__ import annotations

import secrets
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx

from app.harness.engines.durable import DurableEngineServices, PipesHubEngineAssembler
from app.harness.gateway.host import HttpToolGatewayHost
from app.harness.gateway.keys import parse_published
from app.harness.gateway.mcp import ToolGatewayApp
from app.harness.gateway.org_binding import OrgBoundGatewayTokenVerifier
from app.harness.journal_node import NodeJournal
from app.harness.sandbox.docker_broker import DockerSandboxBroker
from app.harness.sandbox.docker_client import DockerClientProvider
from app.harness.sandbox.docker_egress import DockerEgressWiring
from app.harness.skills.bundles import GraphProviderSkillSource, session_skill_bundles
from harness_core.clock import SystemClock
from harness_core.driver import RunDriver
from harness_core.engines.controller import EngineControllerConfig
from harness_core.engines.durable.kernel import ENGINE, EngineKernel
from harness_core.gateway.memory import StaticGatewayKeySource
from harness_core.ids import RandomIds
from harness_core.sandbox.auth import StaticBearerAuth
from harness_core.sandbox.egress.addresses import SubnetPool
from harness_core.sandbox.egress.client import RemoteEgressBindings
from harness_core.sandbox.types import Resources
from harness_engines.release import release_registry
from tests.unit.harness.engines.durable_stack import (
    EGRESS_POOL,
    GATEWAY_MOUNT,
    DirectoryObjectStore,
    _Credential,  # pyright: ignore[reportPrivateUsage]
    _gateway_server,  # pyright: ignore[reportPrivateUsage]
    _NoLoop,  # pyright: ignore[reportPrivateUsage]
    release_images,
)
from tests.unit.harness.engines.lane_releases import (
    lane_admits_candidates,
    lane_releases,
)
from tests.unit.harness.engines.matrix.assembly import scripted_model
from tests.unit.harness.engines.real_stack import RecordingGateway
from tests.unit.harness.gateway.binding_keys import TEST_ATTESTER

if TYPE_CHECKING:
    from app.harness.assembly.services import RunServices
    from tests.unit.harness.engines.durable_stack import WorkerConfig


async def run_matrix_attempt(
    cfg: WorkerConfig, services: RunServices, *, authorizations: list[str] | None = None
) -> dict[str, Any]:
    """Claim ``cfg.run_id`` and run one attempt; the disposition, or ``not_claimed``. ``authorizations`` collects
    every ``Authorization`` the worker's tool gateway received (CF-1)."""
    clock = SystemClock()
    journal = NodeJournal(base_url=cfg.node_url, credentials=_Credential(cfg.credential), lease_ms=cfg.lease_ms)
    async with httpx.AsyncClient(timeout=30) as http:
        published = (await http.get(f"{cfg.node_control}/gateway-public-keys")).json()
    gateway = RecordingGateway(
        ToolGatewayApp(OrgBoundGatewayTokenVerifier(StaticGatewayKeySource(parse_published(published)), clock))  # type: ignore[arg-type]
    )
    if authorizations is not None:
        gateway.authorizations = authorizations
    provider = DockerClientProvider(max_workers=16)
    broker = await DockerSandboxBroker.create(
        provider=provider,
        clock=clock,
        ids=RandomIds(),
        allowed_images=frozenset(release_images().values()),
        admit_candidate_images=lane_admits_candidates(),
        label_namespace=cfg.namespace,
        volume_prefix=cfg.prefix,
        egress=DockerEgressWiring(
            provider=provider,
            clock=clock,
            proxy_container=cfg.proxy,
            bindings=RemoteEgressBindings(cfg.control_url, StaticBearerAuth(cfg.control_token)),
            pool=SubnetPool(EGRESS_POOL),
            # Signed as the production broker signs, under the key journal-http-server.ts verifies (R38IDENT-FU-7).
            attester=TEST_ATTESTER,
            network_prefix=f"{cfg.prefix}-net-",
        ),
    )
    engines = DurableEngineServices(
        broker=broker,
        tool_host=HttpToolGatewayHost(gateway.app, base_url=f"http://{cfg.host_ip}:{cfg.gateway_port}{GATEWAY_MOUNT}"),
        engines=release_registry(releases=lane_releases()),
        config=EngineControllerConfig(
            images=dict(release_images()),
            resources=Resources(cpus=1.0, memory_mb=1024, pids=256, scratch_mb=256),
            tick_s=1.0,
        ),
        objects=DirectoryObjectStore(Path(cfg.objects_dir)),
        route_for=journal.attempt_route,
        clock=clock,
        placeholder_key=secrets.token_bytes(32),
        # As the production worker builds it (``app.harness.engines.worker.engine_services``).
        skill_bundles_for=session_skill_bundles(GraphProviderSkillSource(services.graph_provider)),
    )
    kernel = EngineKernel(
        journal=journal,
        assembler=PipesHubEngineAssembler(services, engines, model_factory=scripted_model),
        clock=clock,
        broker=broker,
        renew_interval_s=cfg.lease_ms / 3000,
        poll_s=1.0,
    )
    driver = RunDriver(
        journal=journal,
        assembler=_NoLoop(),  # type: ignore[arg-type]
        clock=clock,
        renew_interval_s=cfg.lease_ms / 3000,
        kernels={ENGINE: kernel},
    )
    try:
        async with _gateway_server(gateway, cfg.host_ip, cfg.gateway_port):  # type: ignore[arg-type]
            claim = await journal.claim(cfg.run_id, worker_id="e2e3-worker", org_id=cfg.org_id)
            if claim is None:
                return {"status": "not_claimed"}
            disposition = await driver.run_claimed(claim)
            return {"status": disposition.status, "reason": disposition.reason, "attempt": claim.attempt_id}
    finally:
        await broker.aclose()
        await journal.aclose()
        provider.close()


__all__ = ["run_matrix_attempt"]
