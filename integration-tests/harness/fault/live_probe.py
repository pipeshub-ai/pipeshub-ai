"""The fault suite's live-layer holder: a worker's live layer for one org, observed from outside.

    python live_probe.py <out-file> <org-id>

Builds what ``app.harness_main`` builds for pinned runs (``worker_live_layer``: Node's live route behind the
cache, the process's own consumer of ``control-plane-events`` on the deployment's broker) and reads the org's layer
ten times a second, as running tool calls would. Each revision it sees is appended, fsynced, with the wall-clock
time it was first seen.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

from app.harness.assembly.services import HarnessServices
from app.harness.inline import node_state_url
from app.harness.journal_node.credentials import ConfiguredWorkerCredential
from app.harness.run_policy.live import worker_live_layer
from harness_core.clock import SystemClock

READ_EVERY_S = 0.1


async def main(out: Path, org_id: str) -> None:
    services = HarnessServices()
    live = worker_live_layer(
        services,
        base_url=node_state_url(),
        credentials=ConfiguredWorkerCredential(services.config_service()),
        clock=SystemClock(),
    )
    seen: tuple[int, bool] | None = None
    announced = False
    with out.open("a", encoding="utf-8") as log:
        while True:
            layer = await live.current(org_id)
            if (layer.revision, layer.stale) != seen:
                seen = (layer.revision, layer.stale)
                _ = log.write(json.dumps({"at": time.time(), "revision": layer.revision, "stale": layer.stale}) + "\n")
                log.flush()
                os.fsync(log.fileno())
            if not announced and await live.joined():
                announced = True
                print("LIVE PROBE READY", flush=True)  # noqa: T201 - the rig's readiness line
            await asyncio.sleep(READ_EVERY_S)


if __name__ == "__main__":
    asyncio.run(main(Path(sys.argv[1]), sys.argv[2]))
