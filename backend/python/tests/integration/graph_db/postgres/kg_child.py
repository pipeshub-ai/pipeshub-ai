"""The child process of the kill -9 test: drains one page of an organisation and kills itself with SIGKILL after the projection's writes and before the transaction commits.

    python -m tests.integration.graph_db.postgres.kg_child ORG
"""
from __future__ import annotations

import asyncio
import os
import signal
import sys

from app.core.db import DbConfig, KernelDB
from app.services.graph_db.postgres.kg import drainer
from app.services.graph_db.postgres.kg.writers import KgWriter


async def main(org: str) -> None:
    db = KernelDB(DbConfig.from_env("connectors"))
    await db.start()

    async def die(self, ids) -> int:
        os.kill(os.getpid(), signal.SIGKILL)
        return 0
    KgWriter.changes_delete = die
    await drainer.drain_org(db, org)
    print("finished")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1]))
