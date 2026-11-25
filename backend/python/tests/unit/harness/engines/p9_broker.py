"""The sandbox broker as its own process for the P9 lane: ``python -m tests.unit.harness.engines.p9_broker <config.json>``.

The Docker broker of the durable lane (same label namespace, egress wiring and images) behind the broker HTTP
API (``create_broker_app``), as D1 deploys ``app.sandbox_broker_main``; workers reach it with
``RemoteSandboxBroker``. Prints ``BROKER READY <port>`` once it serves. A SIGKILL is the FM-2 fault; a
restarted broker adopts the sandboxes it finds by their labels.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from aiohttp import web

from app.harness.sandbox.docker_broker import DockerSandboxBroker
from app.harness.sandbox.docker_client import DockerClientProvider
from app.harness.sandbox.docker_egress import DockerEgressWiring
from harness_core.clock import SystemClock
from harness_core.ids import RandomIds
from harness_core.sandbox.auth import StaticBearerAuth
from harness_core.sandbox.egress.addresses import SubnetPool
from harness_core.sandbox.egress.client import RemoteEgressBindings
from harness_core.sandbox.remote.server import create_broker_app
from tests.support.sandbox_reaper import install_in_worker_process
from tests.unit.harness.engines.durable_stack import EGRESS_POOL, release_images
from tests.unit.harness.engines.lane_releases import lane_admits_candidates
from tests.unit.harness.gateway.binding_keys import TEST_ATTESTER


async def serve(config: dict[str, str | int]) -> None:
    clock = SystemClock()
    provider = DockerClientProvider(max_workers=16)
    broker = await DockerSandboxBroker.create(
        provider=provider,
        clock=clock,
        ids=RandomIds(),
        allowed_images=frozenset(release_images().values()),
        admit_candidate_images=lane_admits_candidates(),
        label_namespace=str(config["namespace"]),
        volume_prefix=str(config["prefix"]),
        egress=DockerEgressWiring(
            provider=provider,
            clock=clock,
            proxy_container=str(config["proxy"]),
            bindings=RemoteEgressBindings(str(config["control_url"]), StaticBearerAuth(str(config["control_token"]))),
            pool=SubnetPool(EGRESS_POOL),
            # Signed as the production broker signs, under the key journal-http-server.ts verifies (R38IDENT-FU-7).
            attester=TEST_ATTESTER,
            network_prefix=f"{config['prefix']}-net-",
        ),
    )
    runner = web.AppRunner(create_broker_app(broker, StaticBearerAuth(str(config["token"]))))
    await runner.setup()
    await web.TCPSite(runner, str(config["host"]), int(config["port"]), reuse_address=True).start()
    print(f"BROKER READY {config['port']}", flush=True)
    await asyncio.Event().wait()


def main() -> None:
    install_in_worker_process()
    asyncio.run(serve(json.loads(Path(sys.argv[1]).read_text())))


if __name__ == "__main__":
    main()
