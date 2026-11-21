"""One worker process of the G4 lane: ``python -m tests.unit.harness.engines.g4_worker <config.json>``.

Runs one attempt and writes its disposition next to the config; a ``kill9`` failpoint or the lane's
``SIGKILL`` ends it with nothing written.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

from tests.support.sandbox_reaper import install_in_worker_process
from tests.unit.harness.engines.durable_stack import WorkerConfig, run_attempt


def main() -> None:
    install_in_worker_process()
    config = Path(sys.argv[1])
    result = asyncio.run(run_attempt(WorkerConfig.from_json(config.read_text())))
    config.with_suffix(".result.json").write_text(json.dumps(result))


if __name__ == "__main__":
    main()
