"""A steady load on the PostgreSQL-only stack, run in one process against a database the caller can break, and what must hold afterwards.

Each component drives the product code a service runs (no stand-ins for the units of work, the queue, the inbox or the workers):

- ``RecordWrites``: connector record writes whose record events commit with the graph write (``publish_with_commit``).
- ``InboxConsumer``: a simple consumer of the PostgreSQL queue with the consumer inbox; the producer resends an event whose send failed.
- ``KgLoad``: the typed-graph worker (drain, restamp, counters, bootstrap, verify loop) while the graph keeps changing.
- ``ConfigLoad``: two configuration services on the PostgreSQL key-value store; one writes, the other reads through its cache.
- ``Maintainers``: the placement and fold workers (``python -m db.maintain ... --watch``) as processes of their own.
- ``Search``: pgvector writes as the indexing service and searches as the query service.
- ``Reads``: the permission corpus asked through ``check_access`` and the Knowledge Hub as the query service, again and again.

``check()`` of each returns what is wrong once the database is back and the load has settled; an empty list is a pass. Used by
``test_fault_recovery.py`` (the faults on a throwaway cluster) and ``soak.py`` (hours against the Compose services).
"""
from __future__ import annotations

import asyncio
import collections
import contextlib
import logging
import os
import random
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock

import asyncpg

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import CollectionNames, Connectors, OriginTypes
from app.config.providers.encrypted_store import EncryptedKeyValueStore
from app.connectors.core.base.data_processor import (
    data_source_entities_processor as dsp,
)
from app.connectors.core.base.data_store.graph_data_store import GraphDataStore
from app.core import envelope, org_context
from app.core.db import DbConfig, KernelDB
from app.core.org_context import OrgContext, bind
from app.models.entities import FileRecord, RecordType
from app.services.graph_db.postgres.kg.worker import KgWorker
from app.services.graph_db.postgres.provider import PostgresGraphProvider
from app.services.messaging.config import MessageBrokerType, RedisStreamsConfig
from app.services.messaging.inbox import PgInbox, shared_inbox
from app.services.messaging.messaging_factory import MessagingFactory
from app.services.messaging.postgres.producer import PgMessagingProducer
from app.services.messaging.postgres.queue import PgQueue
from app.services.pg_runtime import RuntimeDb

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

log = logging.getLogger("pg-fault")
BACKEND = Path(__file__).resolve().parents[4]
RECORD_TOPIC = "record-events"
ROLES = ("connectors", "indexing", "query", "node", "jobs")
KEY = envelope.derive_key("pg-fault")


def login(role: str) -> str:
    return "pipeshub_jobs" if role == "jobs" else f"pipeshub_{role}_login"


async def until(cond: Callable[[], Awaitable[bool]], seconds: float, step: float = 0.2) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        with contextlib.suppress(Exception):
            if await cond():
                return True
        await asyncio.sleep(step)
    return False


class Stack:
    """Addresses of one migrated database: ``dsn(role)`` through the pooler (the services' path), ``direct(role)`` to the server (LISTEN, jobs)."""

    def __init__(self, dsn: Callable[[str, int | None], str], server_port: int, pooler_port: int | None, admin_dsn: str, migrate_dsn: str) -> None:
        self._dsn, self.server_port, self.pooler_port, self.admin_dsn, self.migrate_dsn = dsn, server_port, pooler_port, admin_dsn, migrate_dsn

    def dsn(self, role: str) -> str:
        return self._dsn(role, self.pooler_port)

    def direct(self, role: str) -> str:
        return self._dsn(role, self.server_port)

    def config(self, role: str, **kw: Any) -> DbConfig:  # noqa: ANN401
        return DbConfig(service=role, dsn=self.dsn(role), jobs_dsn=self.direct("jobs"),
                        pooler="pgbouncer_txn" if self.pooler_port else "none", **kw)

    def environ(self) -> dict[str, str]:
        """The environment of the services (one process here plays connectors, indexing and query)."""
        return {"PG_CORE_URL": self.dsn("connectors"), "PG_POOLER": "pgbouncer_txn" if self.pooler_port else "none",
                "PG_LISTEN_URL": self.direct("connectors"), "PG_JOBS_URL": self.direct("jobs"), "PG_RUNTIME_SERVICE": "connectors",
                "MESSAGE_BROKER": "postgres", "DATA_STORE": "postgres", "KV_STORE_TYPE": "postgres", "PG_KG_PROJECT": "1",
                "GRAPH_SCHEMA_ENFORCEMENT": "off", "PG_KG_SHADOW_SAMPLE": "0", "PG_QUEUE_RETRY_BASE_S": "0.1",
                "PG_LISTEN_PING_S": "1", "PG_LISTEN_PING_TIMEOUT_S": "2", "CONFIG_CACHE_TTL_SECONDS": "3600",
                "SECRET_KEY": os.environ.get("SECRET_KEY") or uuid.uuid4().hex}

    async def admin(self) -> asyncpg.Connection:
        return await asyncpg.connect(self.admin_dsn)

    async def kernel(self, role: str) -> KernelDB:
        db = KernelDB(self.config(role))
        await db.start()
        return db


class Component:
    name = ""

    def __init__(self) -> None:
        self._tasks: list[asyncio.Task] = []
        self.running = False
        self.errors: collections.Counter[str] = collections.Counter()      # what the load saw fail (expected during a fault)

    def _spawn(self, coro: Awaitable[None]) -> None:
        self._tasks.append(org_context.spawn_detached(coro, name=f"pg-fault-{self.name}"))

    def _failed(self, e: BaseException) -> None:
        self.errors[type(e).__name__] += 1

    async def start(self) -> None:
        self.running = True

    async def stop_load(self) -> None:
        """Stop generating new work; what is in flight finishes."""
        self.running = False
        await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()

    async def settle(self, seconds: float) -> None:
        """Wait (bounded) until the background work caught up; ``check`` reports what did not."""

    async def check(self) -> list[str]:
        return []

    async def close(self) -> None:
        await self.stop_load()


class RecordWrites(Component):
    """Connector record writes: the record and its newRecord event commit together or not at all (``publish_with_commit``)."""

    name = "record-writes"

    def __init__(self, stack: Stack, db: KernelDB) -> None:
        super().__init__()
        self.stack, self.org = stack, uuid.uuid4().hex[:24]
        self.conn = f"conn-{self.org[-8:]}"
        self.provider = PostgresGraphProvider(log, db)
        self.processor = dsp.DataSourceEntitiesProcessor(log, GraphDataStore(log, self.provider), AsyncMock())
        self.processor.org_id = self.org
        self.processor.messaging_producer = PgMessagingProducer(log, queue=PgQueue(RuntimeDb(stack.config("connectors"))))
        self.group = f"fault-records-{self.org[-8:]}"       # nobody claims from it: each committed event stays one delivery row
        self.outcome: dict[str, bool] = {}          # record id -> the write returned (True) or raised (False: either outcome may have committed)
        self._n = 0
        self._soft_delete = dsp.is_soft_delete_enabled

    def _ctx(self) -> contextlib.AbstractContextManager:
        return bind(OrgContext(self.org, None, "job", "job:pg-fault", "t"))

    async def start(self) -> None:
        dsp.is_soft_delete_enabled = AsyncMock(return_value=False)     # no configuration service in this process
        await PgQueue().ensure_group(RECORD_TOPIC, self.group, from_start=False)
        with self._ctx():
            await self.provider.batch_upsert_nodes([{
                "_key": self.conn, "orgId": self.org, "name": self.conn, "type": "Drive", "appGroup": "Google Workspace",
                "scope": "team", "isActive": True, "createdAtTimestamp": 1, "updatedAtTimestamp": 1}], CollectionNames.APPS.value)
        await super().start()
        self._spawn(self._loop())

    def _record(self, rid: str) -> FileRecord:
        return FileRecord(id=rid, org_id=self.org, record_name=f"{rid}.txt", record_type=RecordType.FILE, external_record_id=f"ext-{rid}", version=0,
                          origin=OriginTypes.CONNECTOR, connector_name=Connectors.GOOGLE_DRIVE, connector_id=self.conn, mime_type="text/plain",
                          is_file=True, extension="txt", path=f"{rid}.txt", size_in_bytes=10)

    async def _loop(self) -> None:
        while self.running:
            self._n += 1
            rid = f"r{self._n}-{self.org[-8:]}"
            try:
                with self._ctx():
                    await self.processor.on_new_records([(self._record(rid), [])])
                self.outcome[rid] = True
            except Exception as e:
                self.outcome[rid] = False
                self._failed(e)
                await asyncio.sleep(0.1)
            await asyncio.sleep(0.02)

    async def check(self) -> list[str]:
        con = await self.stack.admin()
        try:
            stored = {r["id"] for r in await con.fetch("SELECT id FROM graph.nodes WHERE org_id = $1 AND coll = 'records'", self.org)}
            events = collections.Counter(r["msg_key"] for r in await con.fetch(
                "SELECT msg_key FROM platform.queue_deliveries WHERE group_id = $1 AND topic = $2 AND msg_key = ANY($3::text[])",
                self.group, RECORD_TOPIC, list(self.outcome)))
        finally:
            await con.close()
        bad = []
        for rid, ok in self.outcome.items():
            if ok and rid not in stored:
                bad.append(f"{rid}: the write returned but the record is not stored")
            if events[rid] != (1 if rid in stored else 0):
                bad.append(f"{rid}: stored={rid in stored}, {events[rid]} newRecord event(s)")
        if not any(self.outcome.values()):
            bad.append("no record write succeeded")
        return bad

    async def close(self) -> None:
        await super().close()
        dsp.is_soft_delete_enabled = self._soft_delete
        with contextlib.suppress(Exception):
            await PgQueue().drop_group(RECORD_TOPIC, self.group)


class InboxConsumer(Component):
    """A simple queue consumer with the consumer inbox. The producer resends the same signed event until a send returns, as a retrying
    producer does, so the inbox sees duplicates; a handler runs again only after an inbox read or record of that event failed."""

    name = "inbox-consumer"

    def __init__(self, stack: Stack) -> None:
        super().__init__()
        self.stack, self.org = stack, uuid.uuid4().hex[:24]
        self.topic, self.group = f"fault-{uuid.uuid4().hex[:8]}", "fault-g"
        self.sent: dict[int, int] = {}                   # event number -> sends that returned
        self.runs: collections.Counter[int] = collections.Counter()
        # by event id: inbox reads and records that failed. Either lets an event run again (the consumer then processes it as new, or
        # acknowledges it unrecorded): at least once, as documented in app.services.messaging.inbox.
        self.inbox_failures: collections.Counter[str] = collections.Counter()
        self.event_ids: dict[int, str] = {}
        self.consumer: Any = None
        self.producer = PgMessagingProducer(log, queue=PgQueue())
        self._inbox: tuple[Any, Any] | None = None

    async def start(self) -> None:
        async def keys() -> tuple[bytes, ...]:
            return (KEY,)
        envelope.configure(keys, KEY)
        shared_inbox.cache_clear()
        assert shared_inbox() is not None, "no consumer inbox: PG_CORE_URL and PG_JOBS_URL are both needed"
        await PgQueue().ensure_group(self.topic, self.group, from_start=True)
        failures = self.inbox_failures

        def counted(real: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
            async def call(inbox: PgInbox, consumer: str, ctx: OrgContext, *a: Any) -> Any:  # noqa: ANN401
                try:
                    return await real(inbox, consumer, ctx, *a)
                except Exception:
                    failures[ctx.event_id] += 1
                    raise
            return call
        self._inbox = (PgInbox.seen, PgInbox.record_and_ack)
        PgInbox.seen, PgInbox.record_and_ack = counted(PgInbox.seen), counted(PgInbox.record_and_ack)       # type: ignore[method-assign]

        rt = RuntimeDb()

        async def handler(m: Any) -> bool:  # noqa: ANN401
            async with (await rt.db()).runtime_uow() as tx:       # handlers read the database: a fault fails them too
                await tx.fetchval("SELECT 1")
            self.runs[m.payload["i"]] += 1
            return True
        cfg = RedisStreamsConfig(topics=[self.topic], group_id=self.group, client_id="c1", batch_size=5, block_ms=500, claim_min_idle_ms=0)
        self.consumer = MessagingFactory.create_consumer(log, cfg, broker_type=MessageBrokerType.POSTGRES)
        await self.consumer.start(handler)
        await super().start()
        self._spawn(self._produce())

    async def _produce(self) -> None:
        i = 0
        while self.running:
            i += 1
            wire = envelope.sign(KEY, self.topic, {"orgId": self.org, "i": i}, OrgContext(self.org, None, "service", "jwt", "r"), event_type="orgUpdated")
            self.event_ids[i] = wire["ctx"]["eventId"]
            while True:                                # the same event until a send returns: a failed send may have committed
                try:
                    await self.producer.send_message(self.topic, wire)
                    self.sent[i] = self.sent.get(i, 0) + 1
                    break
                except Exception as e:
                    self._failed(e)
                    self.sent.setdefault(i, 0)
                    await asyncio.sleep(0.2)
            await asyncio.sleep(0.02)

    async def _open(self) -> dict[str, int]:
        return await PgQueue().backlog(self.group, [self.topic])      # by status: ready, claimed, dead

    async def settle(self, seconds: float) -> None:
        async def done() -> bool:
            b = await self._open()
            return not b.get("ready") and not b.get("claimed") and set(self.sent) <= set(self.runs)
        await until(done, seconds)

    async def check(self) -> list[str]:
        bad = [f"event {i} was sent and never handled" for i in self.sent if i not in self.runs]
        bad += [f"event {i} handled {n} times, {self.inbox_failures[self.event_ids[i]]} inbox read(s) or record(s) failed"
                for i, n in self.runs.items() if n > 1 + self.inbox_failures[self.event_ids[i]]]
        if (b := await self._open()).get("ready") or b.get("claimed") or b.get("dead"):
            bad.append(f"the queue still holds {b}")
        if not self.sent:
            bad.append("nothing was sent")
        return bad

    async def close(self) -> None:
        await super().close()
        if self.consumer is not None:
            await self.consumer.stop()
        if self._inbox is not None:
            PgInbox.seen, PgInbox.record_and_ack = self._inbox      # type: ignore[method-assign]
        with contextlib.suppress(Exception):
            await PgQueue().drop_group(self.topic, self.group)
        shared_inbox.cache_clear()
        envelope.configure(None)


class KgLoad(Component):
    """The typed-graph worker on a world that keeps changing; afterwards its log is drained, kg equals a fresh projection and verify says the same."""

    name = "kg"

    def __init__(self, stack: Stack, db: KernelDB, orgs: list[str]) -> None:
        super().__init__()
        self.stack, self.db, self.org = stack, db, uuid.uuid4().hex[:24]
        self.provider = PostgresGraphProvider(log, db)
        self.others = orgs                       # served too (the permission worlds), so their reads come from kg once it enforces
        self.worker: KgWorker | None = None
        self.passes_at_stop = 0

    async def start(self) -> None:
        from tests.integration.graph_db.postgres.test_kg_projection_races import world2
        await world2(self.provider, self.org)

        async def orgs() -> list[str]:
            return [self.org, *self.others]
        self.worker = KgWorker(self.db, orgs, dsn=self.stack.direct("connectors"), poll_s=1.0)
        self.worker.start()
        await super().start()
        self._spawn(self._mutate())

    async def _mutate(self) -> None:
        from tests.integration.graph_db.postgres.test_kg_projection_races import OPS
        rnd = random.Random(7)
        while self.running:
            try:
                await rnd.choice(OPS)(self.provider, self.org)
            except Exception as e:
                self._failed(e)
                await asyncio.sleep(0.1)
            await asyncio.sleep(0.05)

    async def _log_rows(self) -> int:
        con = await self.stack.admin()
        try:
            return await con.fetchval("SELECT count(*) FROM kg.graph_changes WHERE org_id = ANY($1::text[])", [self.org, *self.others])
        finally:
            await con.close()

    async def settle(self, seconds: float) -> None:
        self.passes_at_stop = self.worker.passes if self.worker else 0

        async def drained() -> bool:
            return await self._log_rows() == 0 and self.worker is not None and self.worker.passes > self.passes_at_stop + 1
        await until(drained, seconds)

    async def check(self) -> list[str]:
        from tests.integration.graph_db.postgres.kg_fresh import differences
        from tests.integration.graph_db.postgres.test_kg_backfill import verify
        assert self.worker is not None
        bad = []
        if self.worker._task is None or self.worker._task.done():
            bad.append("the kg worker loop ended")
        if self.worker.passes <= self.passes_at_stop + 1:
            bad.append(f"the kg worker made no pass after the fault ({self.worker.passes} passes)")
        if (n := await self._log_rows()) != 0:
            bad.append(f"{n} change log row(s) left undrained")
        if await differences(self.provider, self.org):
            bad.append("kg differs from a fresh projection of the graph")
        for app in ("app1", "app2"):
            res = await verify(self.db, self.org, app)
            if not res.ok:
                bad.append(f"kg verify of {app}: {res.summary()}")
        return bad

    async def close(self) -> None:
        await super().close()
        if self.worker is not None:
            await self.worker.stop()


class ConfigLoad(Component):
    """Two configuration services on the key-value store (two processes in production): ``a`` writes a counter, ``b`` reads it through its
    cache. A change ``b``'s LISTEN missed while it was cut off must still reach it (it drops its cache when the listener reconnects)."""

    name = "config"

    def __init__(self) -> None:
        super().__init__()
        self.a = ConfigurationService(log, EncryptedKeyValueStore(log))
        self.b = ConfigurationService(log, EncryptedKeyValueStore(log))
        self.key = f"/services/connectors/fault{uuid.uuid4().hex[:8]}/setting"
        self.acked = 0              # the last value a write of ``a`` returned for
        self.tried = 0              # the last value ``a`` tried to write

    async def start(self) -> None:
        await self.a.set_config(self.key, {"v": 0})
        await asyncio.sleep(1.0)                       # both watch threads LISTEN
        await self.b.get_config(self.key, use_cache=True)
        await super().start()
        self._spawn(self._write())
        self._spawn(self._read())

    async def _write(self) -> None:
        while self.running:
            self.tried += 1
            try:
                if await self.a.set_config(self.key, {"v": self.tried}):
                    self.acked = self.tried
                else:
                    self._failed(RuntimeError("set_config returned False"))
            except Exception as e:
                self._failed(e)
            await asyncio.sleep(0.1)

    async def _read(self) -> None:
        while self.running:
            try:
                await self.b.get_config(self.key, use_cache=True)
            except Exception as e:
                self._failed(e)
            await asyncio.sleep(0.05)

    async def settle(self, seconds: float) -> None:
        async def fresh() -> bool:
            got = await self.b.get_config(self.key, use_cache=True)
            return isinstance(got, dict) and got.get("v", -1) >= self.acked
        await until(fresh, seconds)

    async def check(self) -> list[str]:
        stored = await self.a.get_config(self.key, use_cache=False)
        cached = await self.b.get_config(self.key, use_cache=True)
        bad = []
        if not (isinstance(stored, dict) and self.acked <= stored["v"] <= self.tried):
            bad.append(f"stored {stored}, last acknowledged write {self.acked}, last tried {self.tried}")
        if cached != stored:
            bad.append(f"the other service's cache serves {cached}, the store holds {stored}")
        if not self.acked:
            bad.append("no configuration write succeeded")
        return bad

    async def close(self) -> None:
        await super().close()
        for svc in (self.a, self.b):
            with contextlib.suppress(Exception):
                await svc.close()


class Maintainers(Component):
    """The placement and fold workers, each a process of its own as in Compose; both must keep running (no restart) and do their job afterwards."""

    name = "maintainers"

    def __init__(self, stack: Stack, python: str) -> None:
        super().__init__()
        self.stack, self.python = stack, python
        self.procs: dict[str, subprocess.Popen] = {}
        self.logs: dict[str, Any] = {}          # a file, not a pipe: a pipe nobody reads fills up and blocks the worker

    async def start(self) -> None:
        env = {**os.environ, "PYTHONPATH": str(BACKEND), "PG_JOBS_URL": self.stack.direct("jobs"), "PG_MIGRATE_URL": self.stack.migrate_dsn}
        for job in ("placement", "fold"):
            self.logs[job] = tempfile.TemporaryFile("w+")
            self.procs[job] = subprocess.Popen([self.python, "-m", "db.maintain", job, "--watch", "0.5"], cwd=BACKEND, env=env,
                                               stdout=self.logs[job], stderr=subprocess.STDOUT, text=True)
        await super().start()

    def _output(self, job: str) -> str:
        self.logs[job].seek(0)
        return self.logs[job].read()[-2000:]

    async def check(self) -> list[str]:
        bad = [f"the {job} worker exited with {p.returncode}: {self._output(job)}" for job, p in self.procs.items() if p.poll() is not None]
        return bad or await maintainers_work(self.stack)

    async def close(self) -> None:
        await super().close()
        for p in self.procs.values():
            if p.poll() is None:
                p.terminate()
                with contextlib.suppress(subprocess.TimeoutExpired):
                    p.wait(10)
            if p.poll() is None:
                p.kill()
                p.wait(10)
        for f in self.logs.values():
            f.close()


class Search(Component):
    """pgvector search as the query service while the indexing service keeps writing points (each org's first write waits for its partition
    from the placement worker). A search may answer a superset within its org (a user who reads every connector the org had a few seconds
    ago is searched without the membership arms; the graph check after the search removes the rest, see ``visibility.Scope.narrowed_to``),
    so what must hold is: no point it must find is missing, during the fault (or the search fails) and after it, every acknowledged write is
    found, and no search of one org returns another org's point."""

    name = "search"
    SEED = 20

    def __init__(self, stack: Stack) -> None:
        super().__init__()
        self.stack, self.orgs = stack, [uuid.uuid4().hex[:24] for _ in range(2)]
        self.writer: Any = None
        self.reader: Any = None
        self.acked: set[int] = set()
        self.wrong: list[str] = []
        self.runs = 0
        self._n = self.SEED                 # the last live point tried

    def _point(self, org: str, n: int, conn: str) -> Any:  # noqa: ANN401
        from app.services.vector_db.models import VectorPoint
        meta = {"orgId": org, "virtualRecordId": f"r{n}", "blockId": f"b{n}", "embeddingModel": "p:m:4"}
        return VectorPoint(id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"pg-fault/{org}/{n}")), dense_vector=[1.0, n / 1000, 0.0, 0.0],
                           payload={"page_content": f"text {n}", "metadata": meta, "connectorIds": [conn]})

    async def _ids(self, org: str, conn: str, ns: list[int] | None = None) -> set[str]:
        """The points of ``org`` on connector ``conn`` a search finds (those of records ``r<n>`` for ``ns``, a search per 1,000: its limit)."""
        from app.services.vector_db.models import (
            FieldCondition,
            FilterExpression,
            HybridSearchRequest,
        )
        found: set[str] = set()
        for i in range(0, len(ns), 1000) if ns is not None else [0]:
            must = [FieldCondition(key="metadata.orgId", value=org)]
            if ns is not None:
                must.append(FieldCondition(key="metadata.virtualRecordId", values=[f"r{n}" for n in ns[i:i + 1000]]))
            flt = FilterExpression(must=must, should=[FieldCondition(key="connectorIds", values=[conn])])
            [hits] = await self.reader.query_nearest_points("records", [HybridSearchRequest(dense_query=[1.0, 0.0, 0.0, 0.0], filter=flt, limit=1000)])
            found |= {h.id for h in hits}
        return found

    def _want(self, org: str, ns: set[int]) -> set[str]:
        return {self._point(org, n, "seed").id for n in ns}

    async def start(self) -> None:
        from app.services.vector_db.pgvector.config import PgVectorConfig
        from app.services.vector_db.pgvector.pgvector import PgVectorService
        self.writer = await PgVectorService.create(PgVectorConfig(dsn=self.stack.dsn("indexing"), jobs_dsn=self.stack.direct("jobs"), pool_max=4))
        self.reader = await PgVectorService.create(PgVectorConfig(dsn=self.stack.dsn("query"), pool_max=4))
        for org in self.orgs:
            await self.writer.upsert_points("records", [self._point(org, n, "seed") for n in range(self.SEED)])
        await super().start()
        self._spawn(self._write())
        self._spawn(self._read())

    async def _write(self) -> None:
        while self.running:
            self._n += 1
            n = self._n
            try:
                await self.writer.upsert_points("records", [self._point(self.orgs[0], n, "live")])
                self.acked.add(n)
            except Exception as e:
                self._failed(e)
                await asyncio.sleep(0.1)
            await asyncio.sleep(0.05)

    def _wrong(self, org: str, got: set[str]) -> str | None:
        other = next(o for o in self.orgs if o != org)
        theirs = self._want(other, set(range(self.SEED))) | {self._point(other, n, "live").id for n in range(self.SEED, self._n + 1)}
        missing = self._want(org, set(range(self.SEED))) - got
        return f"{org}: {len(missing)} missing, {len(got & theirs)} of another org" if missing or got & theirs else None

    async def _read(self) -> None:
        while self.running:
            for org in self.orgs:
                try:
                    got = await self._ids(org, "seed")
                    self.runs += 1
                    if wrong := self._wrong(org, got):
                        self.wrong.append(wrong)
                except Exception as e:
                    self._failed(e)
                    await asyncio.sleep(0.1)
            await asyncio.sleep(0.02)

    async def check(self) -> list[str]:
        bad = [f"during the fault: {w}" for w in self.wrong[:20]]
        seeds = {org: await self._ids(org, "seed") for org in self.orgs}
        bad += [f"after the fault: {w}" for org, got in seeds.items() if (w := self._wrong(org, got))]
        live = await self._ids(self.orgs[0], "live", sorted(self.acked))
        if missing := {self._point(self.orgs[0], n, "live").id for n in self.acked} - live:
            bad.append(f"{len(missing)} acknowledged point writes are not found")
        if await self._ids(self.orgs[1], "live"):
            bad.append("another org's search returns the first org's points")
        if not self.acked or not self.runs:
            bad.append(f"the load did not run: {len(self.acked)} writes, {self.runs} searches")
        return bad

    async def close(self) -> None:
        await super().close()
        for svc in (self.writer, self.reader):
            if svc is not None:
                with contextlib.suppress(Exception):
                    await svc.disconnect()


async def maintainers_work(stack: Stack) -> list[str]:
    """A new org asks for its vector partition and a point-count delta waits to be folded: the placement and fold workers must do both."""
    bad = []
    org = uuid.uuid4().hex[:24]
    con = await stack.admin()
    try:
        await con.execute("INSERT INTO retrieval.placement_requests (org_id) VALUES ($1)", org)
        await con.execute("INSERT INTO retrieval.lexical_df_delta (org_id, collection_id, space_id, term, d) VALUES ($1, 1, 1, -1, 3)", org)

        async def placed() -> bool:
            return await con.fetchval("SELECT to_regclass('retrieval.points_o_' || $1) IS NOT NULL", org)

        async def folded() -> bool:
            return await con.fetchval("SELECT n_points FROM retrieval.collection_stats WHERE org_id = $1", org) == 3
        if not await until(placed, 60):
            bad.append("the placement worker did not create a new org's partition after the fault")
        if not await until(folded, 60):
            bad.append("the fold worker did not fold a delta after the fault")
    finally:
        await con.close()
    return bad


class Reads(Component):
    """The permission corpus asked as the query service, in a loop: ``check_access`` per node and from every grant the viewer holds, and the
    Knowledge Hub listing and browse (``kh_layer.check_world``). Every world is copied into kg and promoted first, so the answers come from kg
    (and from the walk where kg cannot answer). A pass that completes during the fault must equal the oracle; a pass may fail with an error,
    never answer wrong. ``check`` asks every world once more afterwards, and asks each world's viewers about another world's nodes (another
    org): nothing may come back."""

    name = "reads"

    def __init__(self, stack: Stack, writer: KernelDB, reader: KernelDB, step: int = 1, kh_step: int = 1) -> None:
        super().__init__()
        from tests.integration.graph_db.postgres.test_conformance import PgLayer
        from tests.integration.graph_db.postgres.test_conformance_kh_pg import _Ns
        from tests.permissions.scenario import load_all

        class KhReader(PgLayer):
            name = "postgres-kh"
            not_ready = 0

            async def _ns(self, world: Any) -> Any:  # noqa: ANN401
                await super()._ns(world)
                return _Ns(self._prefix[world])
        # scenarios can share a world: each is asked once; ``step``/``kh_step`` keep every n-th (a shorter run)
        self.worlds = list({s.final_world(): s.id for s in load_all()}.items())[::step]
        self.kh_worlds = self.worlds[::kh_step]
        self.writer = writer
        self.write_layer = PgLayer(PostgresGraphProvider(log, writer), writer)
        self.reader = PostgresGraphProvider(log, reader)
        self.layers = [PgLayer(self.reader, reader), PgLayer(self.reader, reader, all_grants=True)]
        self.kh = KhReader(self.reader, reader)
        self.runs = 0
        self.wrong: list[str] = []

    def orgs(self) -> list[str]:
        from tests.integration.graph_db.postgres.test_conformance import _hex
        from tests.permissions.graph import Namespace
        return [_hex(Namespace(p).org) for p in self.write_layer._prefix.values()]

    async def preload(self) -> None:
        from tests.integration.graph_db.postgres.test_kg_read import flip
        await self.write_layer.preload([w for w, _ in self.worlds])
        for org in self.orgs():
            await flip(self.writer, org)                  # kg answers for every connector it can hold
        for layer in (*self.layers, self.kh):
            layer._prefix = self.write_layer._prefix       # the same stored worlds, asked as the query service

    async def start(self) -> None:
        await super().start()
        self._spawn(self._loop())

    async def stop_load(self) -> None:
        for t in self._tasks:                              # a pass cut short proves nothing either way: no need to wait for it
            t.cancel()
        await super().stop_load()

    async def _world(self, layer: Any, world: Any, sid: str) -> list[str]:  # noqa: ANN401
        from tests.permissions.runner import ConformanceRunner
        report = await ConformanceRunner(layer).check_world(world, sid, cites=True)
        return [f"{f.kind} {f.scenario} {f.viewer} -> {f.node}: {f.reason}" for f in report.findings]

    async def _kh_world(self, world: Any, sid: str) -> list[str]:  # noqa: ANN401
        from tests.integration.graph_db.postgres.test_conformance_kh_pg import _bad
        from tests.integration.graph_db.postgres.test_conformance_kh_pg import (
            check as kh_check,
        )
        return [f"kh {f.kind} {f.scenario} {f.viewer}: {f.detail}" for f in _bad(await kh_check(self.kh, world, sid))]    # type: ignore[arg-type]

    def _asks(self) -> list[Callable[[], Awaitable[list[str]]]]:
        """One question per reader and world: what the loop and the check ask."""
        kh = {w for w, _ in self.kh_worlds}
        out: list[Callable[[], Awaitable[list[str]]]] = []
        for world, sid in self.worlds:
            out += [lambda layer=layer, world=world, sid=sid: self._world(layer, world, sid) for layer in self.layers]
            if world in kh:
                out.append(lambda world=world, sid=sid: self._kh_world(world, sid))
        return out

    async def _loop(self) -> None:
        asks = self._asks()
        while self.running:
            for ask in asks:
                try:
                    self.wrong += await ask()
                    self.runs += 1
                except Exception as e:
                    self._failed(e)
                    await asyncio.sleep(0.1)

    async def _cross_org(self) -> list[str]:
        from tests.integration.graph_db.postgres.test_conformance import _hex
        from tests.permissions.graph import Namespace
        worlds = [(w, Namespace(p)) for w, p in self.write_layer._prefix.items()]
        bad = []
        for (w, ns), (other, ons) in zip(worlds, worlds[1:] + worlds[:1], strict=True):
            org = _hex(ns.org)
            foreign = [ons.id(n.id) for n in other.nodes]
            for viewer in w.users():
                with bind(OrgContext(org, None, "job", "job:test", "t")):
                    got = await self.reader.check_access(ns.id(viewer), org, node_ids=foreign)
                if got.node_ids:
                    bad.append(f"{ns.id(viewer)} of org {org} is admitted to another org's nodes {sorted(got.node_ids)[:5]}")
        return bad

    async def check(self) -> list[str]:
        bad = [f"during the fault: {w}" for w in self.wrong[:40]]
        asks = self._asks()
        sem = asyncio.Semaphore(4)

        async def one(ask: Callable[[], Awaitable[list[str]]]) -> list[str]:
            async with sem:
                return await ask()
        after = [w for found in await asyncio.gather(*(one(a) for a in asks)) for w in found]
        bad += [f"after the fault: {w}" for w in after[:40]]
        bad += (await self._cross_org())[:20]
        if not self.runs:
            bad.append("no read completed while the load ran")
        return bad

    async def close(self) -> None:
        await super().close()
        with contextlib.suppress(Exception):
            await self.write_layer.release(list(self.write_layer._prefix))
