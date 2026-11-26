"""One matrix worker in its own process: ``python -m tests.unit.harness.engines.matrix.subworker <config.json>``.

The same attempt as ``worker.run_matrix_attempt`` over the same backends (the lane's Neo4j, the fake Jira), built
from the config the lane writes; a ``kill9`` failpoint (``harness_core.failpoints``) ends it with ``SIGKILL``, so
nothing of it runs after the point, as when a worker host dies (CN-4).
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from harness_core import failpoints
from tests.support import loopback_fakes
from tests.support.in_memory_vector_db import InMemoryVectorDBService
from tests.support.sandbox_reaper import install_in_worker_process
from tests.unit.harness.engines.durable_stack import WorkerConfig
from tests.unit.harness.engines.matrix import graph as neo4j
from tests.unit.harness.engines.matrix.org import named
from tests.unit.harness.engines.matrix.services import (
    MatrixServices,
    StoredFiles,
    agent_doc,
    blob_seam,
    config_service,
    stub_agent_reads,
)
from tests.unit.harness.engines.matrix.worker import run_matrix_attempt


@dataclass(frozen=True)
class SubworkerConfig:
    worker: str  # ``WorkerConfig.to_json()``
    neo4j_uri: str
    neo4j_password: str
    org_id: str
    suffix: str
    alice: str
    jira_url: str
    failpoints_file: str | None = None

    def to_json(self) -> str:
        return json.dumps(asdict(self))


async def run(cfg: SubworkerConfig) -> dict[str, Any]:
    if cfg.failpoints_file is not None:
        failpoints.registry.install_file(Path(cfg.failpoints_file))
    logger = logging.getLogger("e2e3.subworker")
    server = SimpleNamespace(uri=cfg.neo4j_uri, password=cfg.neo4j_password)
    graph = await neo4j.connect(server, logger, None)  # type: ignore[arg-type]
    try:
        org = named(cfg.org_id, cfg.suffix, alice=cfg.alice)
        config = config_service(org, jira_url=cfg.jira_url)
        graph.config_service = config
        agent = agent_doc(org)
        stub_agent_reads(graph, agent)
        services = MatrixServices(org, graph, config, agent, InMemoryVectorDBService())
        with blob_seam(org, StoredFiles()), loopback_fakes.admitted(cfg.jira_url):
            return await run_matrix_attempt(WorkerConfig.from_json(cfg.worker), services)
    finally:
        if graph.client is not None:
            await graph.client.disconnect()


def _warm() -> None:
    """The legacy stack the assembler imports on first use, imported before the claim: a cold import takes longer
    than a lease under load, and an attempt that loses its lease never reaches the point it should die at."""
    import app.agents.actions.jira.jira
    import app.agents.agent_loop.tool_loader
    import app.api.routes.agent
    import app.harness.assembly.assembler  # noqa: F401


def main() -> None:
    install_in_worker_process()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    _warm()
    path = Path(sys.argv[1])
    result = asyncio.run(run(SubworkerConfig(**json.loads(path.read_text()))))
    path.with_suffix(".result.json").write_text(json.dumps(result))


if __name__ == "__main__":
    main()
