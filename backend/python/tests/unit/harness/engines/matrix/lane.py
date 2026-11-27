"""The matrix lane: E1's durable lane (Node, the proxy, E3, the scripted model) plus the lane's own Neo4j, the seeded
org and the services the production assembler runs on. One per module; each cell is a run of its own session.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import logging
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.modules.retrieval.retrieval_service import (
    _user_cache as retrieval_users,  # pyright: ignore[reportPrivateUsage]
)
from harness_core.budget.types import Cap, PinnedBudget, RunAccountRef
from tests.support import docker_session, loopback_fakes
from tests.support.in_memory_vector_db import InMemoryVectorDBService
from tests.unit.harness.engines.durable_stack import ORG, durable_lane
from tests.unit.harness.engines.matrix import graph as neo4j
from tests.unit.harness.engines.matrix import jira, mcp, vendors
from tests.unit.harness.engines.matrix.brain import MatrixBrain
from tests.unit.harness.engines.matrix.org import seed, seed_vectors, store
from tests.unit.harness.engines.matrix.services import (
    JIRA_TOKEN,
    MatrixServices,
    StoredFiles,
    agent_doc,
    blob_seam,
    config_service,
    stub_agent_reads,
)
from tests.unit.harness.engines.matrix.worker import run_matrix_attempt

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable
    from pathlib import Path

    from harness_core.sandbox.mcp_runner import SandboxMCPRunner
    from tests.unit.harness.engines.durable_stack import DurableLane, WorkerConfig
    from tests.unit.harness.engines.matrix.arango import ArangoServer
    from tests.unit.harness.engines.matrix.jira import FakeJira
    from tests.unit.harness.engines.matrix.org import Org
    from tests.unit.harness.engines.matrix.vendors import FakeVendors

# Node's journal server admits every run as this actor: alice in the seeded org.
ACTOR = "actor-contract"
TERMINAL = frozenset({"completed", "failed", "cancelled", "quarantined"})
WAITING = frozenset({"waiting_approval", "suspended"})
MAX_ATTEMPTS = 4
# A worker process imports the legacy stack before its first renewal: a 10 s lease does not survive that load.
KILLED_LEASE_MS = 30_000



@dataclass
class RunResult:
    run_id: str
    status: str
    events: list[dict[str, Any]]
    dispositions: list[dict[str, Any]]
    # The scripted model's calls made by this run (the brain is shared by the lane).
    model_calls: list[Any]
    # Every ``Authorization`` the run's tool gateways received (CF-1).
    authorizations: list[str] = field(default_factory=list[str])
    # What the ``held`` hook returned while the run's turn was held.
    held: Any = None

    def types(self) -> list[str]:
        return [str(e.get("type")) for e in self.events]

    def of(self, kind: str) -> list[dict[str, Any]]:
        return [dict(e.get("data") or {}) for e in self.events if e.get("type") == kind]

    def tool_results(self) -> list[str]:
        """Every tool result the engine handed its model, in order."""
        return [m.content for call in self.model_calls[-1:] for m in call.messages if m.role == "tool"]

    def answer(self) -> str:
        finalized = self.of("x.pipeshub.answer.finalized")
        return str(finalized[-1].get("text") or finalized[-1].get("answer") or "") if finalized else ""


@dataclass
class MatrixLane:
    lane: DurableLane
    # The graph backend's container: Neo4j (Bolt 7687) by default, ArangoDB (HTTP 8529) for ``on_arango``.
    server: neo4j.Neo4jServer | ArangoServer
    org: Org
    services: MatrixServices
    workdir: Path
    runs: list[str] = field(default_factory=list[str])
    jira: FakeJira | None = None
    # TG-16: Slack, Google, Microsoft and the image model.
    vendors: FakeVendors | None = None
    files: StoredFiles | None = None
    runner: SandboxMCPRunner | None = None
    # Why the MCP server's tools did not load before any was approved (P2.8: ``mcp_tool_unapproved``).
    first_load: list[str] = field(default_factory=list[str])
    # The configuration of the attempt running now (its tool gateway's port: a CF probe target).
    current: WorkerConfig | None = None
    graph_port: int = 7687
    # A real vector store's bridge address and port (``vector_stores.on_vector_store``); None: the contract fake.
    vector_store: tuple[str, int] | None = None
    # The budget each run is pinned with at admission (uncapped: the scripted model costs nothing). The live matrix
    # pins what is left of its cap, so the model gateway refuses the call that would pass it.
    run_cap: Callable[[], Cap] = Cap

    async def admit(self, run_id: str, prompt: str, spec: dict[str, Any], session: str) -> None:
        body = {"runId": run_id, "sessionId": session, "inputText": prompt, "spec": spec, "orgId": self.lane.org}
        _ = (await self.lane.http.post("/admit", json=body)).raise_for_status()
        self.lane.ledger.pin(RunAccountRef(self.lane.org, run_id), PinnedBudget(self.run_cap()))

    async def run(
        self,
        prompt: str,
        spec: dict[str, Any],
        *,
        decide: Callable[[str], str | None] | None = None,
        session: str | None = None,
        held: Callable[[str], Awaitable[Any]] | None = None,
        attempts: int = MAX_ATTEMPTS,
        scope: str | None = None,
    ) -> RunResult:
        """Admit and drive the run to an end. ``decide(interactionId)`` answers each approval it asks for
        (``approved``/``denied``; ``None`` leaves the run waiting), an approval for ``scope`` when given.
        ``held(run_id)`` runs while the prompt's ``hold`` directive keeps the engine's model call open (the sandbox
        is live), then the turn goes on."""
        run_id = f"run-e2e3-{uuid.uuid4().hex[:8]}"
        self.runs.append(run_id)
        calls_before = len(self.lane.model.model_calls)
        # The brain keys a released hold by its position in the prompt: each run's own holds start unreleased.
        self.lane.model.brain.holds.clear()
        await self.admit(run_id, prompt, spec, session or f"ses-{run_id}")
        dispositions: list[dict[str, Any]] = []
        authorizations: list[str] = []
        observed: Any = None
        status = "queued"
        for _ in range(attempts):
            cfg = self.current = self.lane.config(run_id)
            attempt = asyncio.ensure_future(run_matrix_attempt(cfg, self.services, authorizations=authorizations))
            if held is not None and observed is None:
                observed = await self._while_held(run_id, attempt, held)
            dispositions.append(await asyncio.wait_for(attempt, 600))
            status = (await self.lane.until_claimable(run_id))["status"]
            if status in WAITING and decide is not None:
                waiting = open_interactions(await self.lane.events(run_id))
                decisions = [(i, decide(i)) for i in waiting]
                if not waiting or any(d is None for _, d in decisions):
                    break
                for interaction, decision in decisions:
                    await self.lane.resolve(
                        run_id, interaction, str(decision), scope=scope if decision == "approved" else None
                    )
                status = (await self.lane.run(run_id))["status"]
                continue
            if status in TERMINAL or status in WAITING:
                break
        events = await self.lane.events(run_id)
        return RunResult(
            run_id, status, events, dispositions, self.lane.model.model_calls[calls_before:], authorizations, observed
        )

    async def _while_held(
        self, run_id: str, attempt: asyncio.Future[Any], held: Callable[[str], Awaitable[Any]]
    ) -> Any:  # noqa: ANN401 - whatever ``held`` returns
        """``held(run_id)`` once this attempt's model call is held; ``None`` when the attempt ends first (it
        suspended for an approval: a later attempt reaches the hold)."""
        model = self.lane.model
        waiting = asyncio.ensure_future(model.holding.wait())
        _ = await asyncio.wait({waiting, attempt}, timeout=600, return_when=asyncio.FIRST_COMPLETED)
        if not waiting.done():
            _ = waiting.cancel()
            return None
        try:
            return await held(run_id)
        finally:
            model.release()

    async def attempt(self, run_id: str) -> dict[str, Any]:
        """One attempt in this process; the run's status after it (a dead lease waited out)."""
        cfg = self.current = self.lane.config(run_id)
        disposition = await asyncio.wait_for(run_matrix_attempt(cfg, self.services), 600)
        return {"disposition": disposition, "status": (await self.lane.until_claimable(run_id))["status"]}

    async def killed_attempt(self, run_id: str, point: str) -> dict[str, Any] | None:
        """One attempt in a worker process of its own, ``SIGKILL``ed at the first hit of failpoint ``point``; its
        result, ``None`` when it died."""
        import json
        import os
        import sys

        from tests.support.sandbox_reaper import die_with_parent
        from tests.unit.harness.engines.durable_stack import PYTHON_ROOT
        from tests.unit.harness.engines.matrix.subworker import SubworkerConfig

        assert self.jira is not None
        arms = self.workdir / f"{run_id}-{uuid.uuid4().hex[:6]}.failpoints.json"
        arms.write_text(json.dumps({point: {"action": "kill9", "trigger": {"nth": 1}}}))
        cfg = self.current = dataclasses.replace(self.lane.config(run_id), lease_ms=KILLED_LEASE_MS)
        config = self.workdir / f"{run_id}-{uuid.uuid4().hex[:6]}.subworker.json"
        config.write_text(
            SubworkerConfig(
                worker=cfg.to_json(), neo4j_uri=self.server.uri, neo4j_password=self.server.password,
                org_id=self.org.org_id, suffix=self.org.suffix, alice=self.org.user_id("alice"),
                jira_url=self.jira.base, failpoints_file=str(arms),
            ).to_json()
        )
        log = config.with_suffix(".log").open("wb")
        try:
            process = await asyncio.create_subprocess_exec(
                sys.executable, "-m", "tests.unit.harness.engines.matrix.subworker", str(config),
                cwd=str(PYTHON_ROOT), env={**os.environ, "PYTHONPATH": str(PYTHON_ROOT)}, stdout=log, stderr=log,
                preexec_fn=die_with_parent,
            )
            _ = await asyncio.wait_for(process.wait(), 600)
        finally:
            log.close()
        result = config.with_suffix(".result.json")
        return json.loads(result.read_text()) if result.exists() else None

    def secrets(self) -> tuple[str, ...]:
        """What no sandbox may hold: the worker credential, the proxy's control token, the org's provider key
        (E3's configured entry), the Jira credential, the MCP server's secret, the OAuth toolsets' tokens and app
        secret and the image model's key."""
        from tests.unit.harness.engines.matrix.services import (
            JIRA_TOKEN,
            MCP_SECRET,
            OAUTH_CLIENT_SECRET,
            VENDOR_TOOLSETS,
        )
        from tests.unit.harness.engines.matrix.vendors import IMAGE_KEY

        lane = self.lane
        provider = [str(m["configuration"]["apiKey"]) for m in (lane.gateway_config.ai_models["llm"] if lane.gateway_config else [])]
        tokens = sorted({token for *_, token in VENDOR_TOOLSETS})
        return (
            lane.node.credential, lane.stack.control_token, *provider, JIRA_TOKEN, MCP_SECRET, *tokens,
            OAUTH_CLIENT_SECRET, IMAGE_KEY,
        )

    def forbidden(self) -> tuple[tuple[str, int], ...]:
        """Listeners a sandbox must not reach directly: the scripted model and Node on the upstream bridge, the
        worker's tool gateway, the proxy's upstream side, the lane's graph store and its real vector store."""
        stack = self.lane.stack
        node_port = int(self.lane.node.base_url.rsplit(":", 1)[1])
        proxy = stack.client.api.inspect_container(stack.proxy)["NetworkSettings"]["Networks"][stack.network]["IPAddress"]
        targets = [(stack.host_ip, stack.model_port), (stack.host_ip, node_port), (proxy, 8097), (proxy, 8098)]
        if self.current is not None:
            targets.append((stack.host_ip, self.current.gateway_port))
        targets.append((self.server.address(), self.graph_port))
        if self.vector_store is not None:
            targets.append(self.vector_store)
        return tuple(targets)


def open_interactions(events: list[dict[str, Any]]) -> list[str]:
    requested = [str(e["data"].get("interactionId")) for e in events if e.get("type") == "interaction.requested"]
    resolved = {str(e["data"].get("interactionId")) for e in events if e.get("type") == "interaction.resolved"}
    return [i for i in requested if i not in resolved]


@contextlib.asynccontextmanager
async def matrix_lane(
    workdir: Path, *, models: tuple[dict[str, Any], ...] = (), admission: object | None = None
) -> AsyncIterator[MatrixLane]:
    """The lane's Neo4j (seeded), the fake Jira, the fake Slack, Google, Microsoft and image model (their SDKs
    routed to it), local record storage, the echo MCP server's runner, then E1's durable lane around them.
    ``admission``: an edition's ``McpToolAdmission`` for the lane, under which an admin approved ``echo``, approved
    ``where`` at a digest it has since changed from, and never approved ``env_digest``; None: this build's open
    admission, nothing approved."""
    import shutil
    from pathlib import Path

    import docker
    import pytest

    client = docker.from_env()
    # A killed earlier lane ran no teardown: its runners, image and Neo4j are this suite's to remove.
    _ = await asyncio.to_thread(docker_session.reap_stale, client, mcp.SUITE)
    server = await asyncio.to_thread(neo4j.start_neo4j)
    image = await asyncio.to_thread(mcp.build_runner_image, client)
    graph: Any = None
    # Local storage lives where the product resolves it, under the worker's home (``storage_search``'s mount root).
    mount = f"pxh-tg16-storage-{uuid.uuid4().hex[:8]}"
    mount_root = Path.home() / ".local" / mount
    try:
        logger = logging.getLogger("e2e3.matrix")
        graph = await neo4j.connect(server, logger, None)
        org = await seed(graph, ORG, uuid.uuid4().hex[:6], alice=ACTOR)
        # Every lane seeds alice under the same user id with a new key, and retrieval caches a user's document per
        # process: an earlier module's lane would otherwise answer with its key, and her own records go unread.
        retrieval_users.pop(ACTOR, None)
        store(org, mount_root)
        async with (
            jira.serving(JIRA_TOKEN) as fake_jira,
            vendors.serving() as fake_vendors,
            mcp.echo_runner(image) as runner,
        ):
            config = config_service(org, jira_url=fake_jira.base, images_url=f"{fake_vendors.base}/images", storage_mount=mount)
            graph.config_service = config
            agent = agent_doc(org)
            stub_agent_reads(graph, agent)
            services = MatrixServices(org, graph, config, agent, InMemoryVectorDBService())
            await seed_vectors(org, services)
            files = StoredFiles()
            with (
                blob_seam(org, files),
                vendors.routed(fake_vendors),
                loopback_fakes.admitted(fake_jira.base),
                pytest.MonkeyPatch.context() as patch,
                mcp.mcp_on(patch, runner, image, org, admission=admission),
            ):
                with mcp.echo_template():
                    first_load = (
                        await mcp.first_listing(config, org)
                        if admission is None
                        else await mcp.approve(config, org, approved=("echo",), stale=("where",))
                    )
                async with durable_lane(workdir, org=ORG, models=models) as lane:
                    lane.model.brain = MatrixBrain()
                    yield MatrixLane(
                        lane, server, org, services, workdir, jira=fake_jira, vendors=fake_vendors, runner=runner,
                        first_load=first_load, files=files,
                    )
    finally:
        if graph is not None:
            with contextlib.suppress(Exception):
                await graph.client.disconnect()
        await asyncio.to_thread(neo4j.stop_neo4j, server)
        shutil.rmtree(mount_root, ignore_errors=True)
        with contextlib.suppress(Exception):
            client.images.remove(image, force=True)
        client.close()


@contextlib.asynccontextmanager
async def on_arango(matrix: MatrixLane) -> AsyncIterator[MatrixLane]:
    """E2E4 AR: the same lane, org and services over an ArangoDB of its own instead of Neo4j. The org is seeded with
    the Neo4j org's ids (the blob seam, the agent and the configuration serve both); the vector store is its own."""
    from tests.unit.harness.engines.matrix import arango

    server = await asyncio.to_thread(arango.start_arango)
    graph: Any = None
    try:
        graph = await arango.connect(server, logging.getLogger("e2e4.matrix.arango"))
        org = await seed(graph, matrix.org.org_id, matrix.org.suffix, alice=matrix.org.user_id("alice"))
        assert org.ids == matrix.org.ids
        graph.config_service = matrix.services.config
        stub_agent_reads(graph, matrix.services.agent)
        services = MatrixServices(org, graph, matrix.services.config, matrix.services.agent, InMemoryVectorDBService())
        await seed_vectors(org, services)
        yield dataclasses.replace(matrix, server=server, services=services, runs=[], graph_port=8529)
    finally:
        if graph is not None:
            await arango.disconnect(graph)
        await asyncio.to_thread(arango.stop_arango, server)


__all__ = ["ACTOR", "MatrixLane", "RunResult", "matrix_lane", "on_arango", "open_interactions"]
