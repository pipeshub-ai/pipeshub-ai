"""Where an org's vector rows live: the placement port and its default.

Every org's rows are either in the shared default partition tree or in a LIST leaf of the org's own. The default policy,
:class:`OwnLeafPolicy`, makes sure the leaf exists BEFORE the org's first vector write, so an org's rows never enter the shared
tree and are never moved. Creating a leaf is DDL, which only the migrate credential may run, so the writing service files a
request (the vetted ``org_leaf_request``) and waits for the placement worker to close it (``org_leaf_status``). Waiting is per process: once an org is placed it is remembered, and concurrent first writers of
one process share one wait; other processes poll the same request. Only a write that follows the wait is planned against the new
partition map. If the worker does not answer in time the write fails with the command to run; it never falls back to the shared
tree. A different policy (keep small orgs shared, promote large ones in a maintenance window) implements the same port.
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Any, Protocol

from app.core import org_context
from app.services.vector_db.pgvector.errors import OrgMismatch, PgVectorError
from app.services.vector_db.pgvector.scope import ambient_org

if TYPE_CHECKING:
    from collections.abc import Coroutine

    import asyncpg

    from app.core.db import KernelDB, Tx


class PlacementPolicy(Protocol):
    async def before_first_write(self, org: str) -> None:
        """Called before an org's vector rows are written (every write; cheap once the org is placed). Must be idempotent and safe
        to call concurrently, in one process and across processes, and must return only when the org's placement is settled."""


class OwnLeafPolicy:
    def __init__(self, db: KernelDB, *, wait: float = 30.0, poll: float = 0.1) -> None:
        self._db, self._wait, self._poll = db, wait, poll
        self._placed: set[str] = set()
        self._asking: dict[tuple[asyncio.AbstractEventLoop, str], asyncio.Task[None]] = {}    # per loop: a task cannot be awaited from another loop

    async def before_first_write(self, org: str) -> None:
        # the requests below act for the ambient org: refuse a call that names another one, a placed org included
        if ambient_org() != org:
            raise OrgMismatch("placement was asked for a different organisation than the one the call runs for")
        if org in self._placed:
            return
        key = (asyncio.get_running_loop(), org)      # the indexing consumers each run a loop of their own against one service
        task = self._asking.get(key)
        if task is None:
            # request-scoped child work: it keeps the caller's org context (the same org for every waiter)
            task = self._asking[key] = org_context.spawn_in_request(self._place(org), name=f"pgvector-placement-{org}")
            task.add_done_callback(lambda _t, key=key: self._asking.pop(key, None))
        await asyncio.shield(task)

    async def _place(self, org: str) -> None:
        async def ask(tx: Tx) -> None:
            await tx.fetchval("/* pipeshub:pgvector.org_leaf_request */ SELECT requested FROM retrieval.org_leaf_request()")

        async def status(tx: Tx) -> asyncpg.Record | None:
            return await tx.fetchrow("/* pipeshub:pgvector.org_leaf_status */ SELECT placed, note FROM retrieval.org_leaf_status()")

        # the caller runs inside the org's scope; a concurrent first request is settled inside the function
        deadline = time.monotonic() + self._wait          # the request and every poll count against it
        await self._within(deadline, self._db.run(ask, write=True, idempotent=True, custom_plan=True))
        delay = self._poll
        while True:
            row = await self._within(deadline, self._db.run(status, custom_plan=True))
            if row is not None and row["placed"]:
                self._placed.add(org)
                return
            # a note is written when the worker closes the request as failed (the schema's contract), never while it still retries
            if row is not None and row["note"]:
                raise PgVectorError(f"the organisation cannot get a leaf of its own: {row['note']}")
            if time.monotonic() >= deadline:
                raise self._late()
            await asyncio.sleep(min(delay, max(deadline - time.monotonic(), 0)))
            delay = min(delay * 2, 1.0)

    async def _within(self, deadline: float, call: Coroutine[Any, Any, Any]) -> Any:  # noqa: ANN401
        try:
            async with asyncio.timeout(max(deadline - time.monotonic(), 0)):
                return await call
        except TimeoutError:
            raise self._late() from None

    def _late(self) -> PgVectorError:
        return PgVectorError(
            f"placement did not finish in time ({self._wait:g}s): the organisation's own partition has not been created. Likely causes: "
            "the placement worker is not running, it is busy or waiting for the migrate lock during a deployment, or its database "
            "connection is failing; check its log"
        )
