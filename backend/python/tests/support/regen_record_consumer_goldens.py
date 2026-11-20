"""Writes the seam-off goldens for the record consumers.

Run it on the commit the goldens are meant to describe (the base commit of the change that adds a hook), from
``backend/python``::

    PYTHONPATH=. venv/bin/python -m tests.support.regen_record_consumer_goldens

The harness does not exist on the base commit, so make a worktree there (``git worktree add --detach <dir> <base>``), copy
``tests/support/record_consumer_world.py`` and this module into its ``backend/python/tests/support/``, run the command
there with the base commit's ``app`` (``PYTHONPATH=.``), and copy the written files back. Do not copy any ``app`` file.

``tests/unit/services/messaging/test_record_consumer_seam_off.py`` replays the same streams and compares.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
from pathlib import Path


async def main() -> None:
    os.environ.update(
        {"MAX_CONCURRENT_INDEXING": "6", "MESSAGE_BATCH_SIZE_INDEXING": "100", "MESSAGE_TIMEOUT_MS": "5"}
    )
    logging.disable(logging.CRITICAL)
    from tests.support import record_consumer_world as world

    out = Path(world.GOLDEN_DIR)
    out.mkdir(parents=True, exist_ok=True)
    for kind in ("kafka", "redis", "pg"):
        record = {"constructor": world.constructor_state(kind), "run": await world.collect(kind)}
        if kind == "pg":
            queue = Path("app/services/messaging/postgres/queue.py")
            record["queue_sha256"] = hashlib.sha256(queue.read_bytes()).hexdigest()
        (out / f"{kind}.json").write_text(json.dumps(record, indent=1, sort_keys=True) + "\n")
        print("wrote", out / f"{kind}.json")


if __name__ == "__main__":
    asyncio.run(main())
