"""Child worker for the kill -9 tests: ``python crash_child.py <root> <scenario> <failpoints.json>``."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

PYTHON_ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(PYTHON_ROOT))

from harness_core.failpoints import registry  # noqa: E402
from tests.unit.harness_core.durable.scenarios import SCENARIOS  # noqa: E402
from tests.unit.harness_core.durable.support import World  # noqa: E402


async def main(root: Path, scenario: str) -> None:
    world = World(root=root)
    _, port = SCENARIOS[scenario].child()
    _ = await world.run(world.kernel(port))


if __name__ == "__main__":
    registry.install_file(Path(sys.argv[3]))
    asyncio.run(main(Path(sys.argv[1]), sys.argv[2]))
    print("finished without being killed", flush=True)
