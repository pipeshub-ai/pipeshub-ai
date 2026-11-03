"""Shared plumbing of the PostgreSQL graph provider: row helpers, the transaction worker, the base class every mixin builds on."""
from __future__ import annotations

import asyncio
import json
import uuid
from typing import TYPE_CHECKING, Any

from app.config.constants.arangodb import CollectionNames
from app.core.org_context import current_or_system, spawn_in_request

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from logging import Logger

    from app.core.db import KernelDB, Tx
    from app.core.org_context import OrgContext

_HIER = ("PARENT_CHILD", "ATTACHMENT")


class NotImplementedInPostgres(NotImplementedError):
    pass


def _j(v: Any) -> str:  # noqa: ANN401
    return json.dumps(v, default=str)


def _doc(raw: Any) -> dict:  # noqa: ANN401
    return json.loads(raw) if isinstance(raw, str) else dict(raw)


def _node(doc: dict, coll: str) -> dict:
    """The stored form: ``id`` instead of ``_key``, no ``_id``, a key when none was given."""
    d = dict(doc)
    if "_key" in d:
        d["id"] = d.pop("_key")
    d.pop("_id", None)
    d.setdefault("id", str(uuid.uuid4()))
    return d


def _arango(doc: dict, coll: str) -> dict:
    d = dict(doc)
    d["_key"] = d["id"]
    d["_id"] = f"{coll}/{d['id']}"
    return d


def _edge(e: dict) -> tuple[str, str, str, str, dict]:
    """(from_coll, from_id, to_coll, to_id, props) of an edge in either shape."""
    if "_from" in e and "_to" in e:
        fc, fi = e["_from"].split("/", 1)
        tc, ti = e["_to"].split("/", 1)
    else:
        fc, fi, tc, ti = e.get("from_collection", ""), e.get("from_id", ""), e.get("to_collection", ""), e.get("to_id", "")
    if not (fc and fi and tc and ti):
        raise ValueError(f"edge without both ends: {e}")
    props = {k: v for k, v in e.items() if k not in ("_from", "_to", "from_id", "to_id", "from_collection", "to_collection")}
    return fc, fi, tc, ti, props


def _ekey(coll: str, props: dict) -> str:
    """What tells parallel edges of one pair apart: a link's relation type, a grant's type (a user's own grant and the organization's grant to
    the same record are two edges), an authenticated-as link's connector (one link per connector, as on the other stores); empty for the rest, one edge per pair."""
    if coll == "permission":
        return str(props.get("type") or "")
    if coll == CollectionNames.AUTHENTICATED_AS.value:
        return str(props.get("connectorId") or "")
    rt = props.get("relationshipType")
    return rt if coll == CollectionNames.NODE_RELATIONS.value and rt not in (None, *_HIER) else ""


class TransactionClosed(RuntimeError):
    """A call on an explicit transaction that has already failed, been cancelled or been rolled back."""


class TransactionOrgMismatch(RuntimeError):
    """A call on an explicit transaction ran under another org than the one that began it."""


class _Txn:
    """One explicit transaction: a task that owns a unit of work and runs what it is handed, so the transaction and the callers'
    other (non-transaction) calls never nest.

    Whatever ends the task (the unit failing to open, a lost connection, a cancel, a rollback) resolves every call waiting on it with that
    error, and later calls fail at once; a caller is never left awaiting a task that is gone. Cancelling the calling task rolls the unit back and
    gives the connection up."""

    def __init__(self, db: KernelDB, ctx: OrgContext) -> None:
        self._db = db
        self._who = (ctx.kind, ctx.org_id)       # the org the transaction began under; every call must run under it
        self._q: asyncio.Queue | None = None
        self._task: asyncio.Task | None = None
        self._ended: BaseException | None = None

    async def _main(self) -> None:
        try:
            async with (self._db.jobs_uow(write=True) if current_or_system().kind == "system" else self._db.uow(write=True)) as tx:
                while True:
                    item = await self._q.get()
                    if item in ("commit", "rollback"):
                        if item == "rollback":
                            raise _Rollback
                        break
                    fn, fut = item
                    try:
                        result = await fn(tx)
                    except Exception as e:      # the caller decides; the unit is the caller's to roll back
                        if not fut.done():
                            fut.set_exception(e)
                    else:
                        if not fut.done():
                            fut.set_result(result)
        except _Rollback:
            self._ended = TransactionClosed("the transaction was rolled back")
        except BaseException as e:   # the unit did not open, the connection was lost, the commit failed, or the task was cancelled
            self._ended = e
            if isinstance(e, asyncio.CancelledError):
                raise
        else:
            self._ended = TransactionClosed("the transaction was committed")
        finally:
            while self._q is not None and not self._q.empty():     # nobody is left waiting on a task that ended
                item = self._q.get_nowait()
                if isinstance(item, tuple) and not item[1].done():
                    item[1].set_exception(self._ended_error())

    def _ended_error(self) -> BaseException:
        e = self._ended
        return e if isinstance(e, Exception) else TransactionClosed("the transaction was cancelled")

    async def run(self, fn: Callable[[Tx], Awaitable[Any]]) -> Any:  # noqa: ANN401
        now = current_or_system()
        if (now.kind, now.org_id) != self._who:
            raise TransactionOrgMismatch(f"a transaction begun under org {self._who[1]} was used under org {now.org_id}")
        if self._ended is not None:
            raise self._ended_error()
        if self._task is None:
            self._q = asyncio.Queue()
            self._task = spawn_in_request(self._main())
        fut = asyncio.get_running_loop().create_future()
        await self._q.put((fn, fut))
        try:
            await asyncio.wait({fut, self._task}, return_when=asyncio.FIRST_COMPLETED)
        except asyncio.CancelledError:
            self._abandon()
            raise
        if fut.done():
            return fut.result()
        raise self._ended_error()      # the task ended without answering this call

    def _abandon(self) -> None:
        """The caller was cancelled: roll the unit back and release its connection (the task's unit exits on the cancel)."""
        if self._task is not None and not self._task.done():
            self._task.cancel()
            self._task.add_done_callback(lambda t: t.cancelled() or t.exception())

    async def finish(self, how: str) -> None:
        if self._task is None:
            return
        if self._task.done():
            if how == "commit" and self._ended is not None and not isinstance(self._ended, TransactionClosed):
                raise self._ended      # a commit on a transaction that was lost is not a commit
            return
        await self._q.put(how)
        await asyncio.wait({self._task})
        if how == "commit" and self._ended is not None and not isinstance(self._ended, TransactionClosed):
            raise self._ended


class _Rollback(Exception):
    pass


class PgBase:
    def __init__(self, logger: Logger, db: KernelDB, config_service: Any = None) -> None:  # noqa: ANN401
        self.logger, self._db = logger, db
        self.config_service = config_service  # read by callers that build blob storage from the provider, as on Neo4j/Arango
        self._txns: dict[str, _Txn] = {}

    # ---- plumbing ----------------------------------------------------------------------------------------------
    async def _q(self, fn: Callable[[Tx], Awaitable[Any]], transaction: str | None, *, write: bool = False) -> Any:  # noqa: ANN401
        if transaction:
            return await self._txn(transaction).run(fn)
        # Fails closed: no ambient org raises MissingOrgContext. A system context (org enumeration at start-up) reads through the jobs role.
        return await self._db.run(fn, write=write, jobs=current_or_system().kind == "system")

    async def _system_read(self, fn: Callable[[Tx], Awaitable[Any]]) -> Any:  # noqa: ANN401
        """A cross-org read that goes through the vetted ``graph.system_*`` functions: the service's own role, no org bound, no jobs login."""
        async with self._db.runtime_uow() as tx:
            return await fn(tx)

    async def connect(self) -> bool:
        return True  # the caller starts the KernelDB

    async def disconnect(self) -> bool:
        return True

    async def ensure_schema(self) -> bool:
        return True  # the migration branch `graph` owns the schema

    def _txn(self, transaction: str) -> _Txn:
        try:
            return self._txns[transaction]
        except KeyError:
            raise TransactionClosed(f"no open transaction {transaction}: it was never begun here, or it has ended") from None

    async def begin_transaction(self, read: list[str], write: list[str], explicit: bool | None = None) -> str:
        tid = str(uuid.uuid4())
        self._txns[tid] = _Txn(self._db, current_or_system())     # the org is fixed here
        return tid

    async def commit_transaction(self, transaction: str) -> None:
        self._txn(transaction)
        await self._txns.pop(transaction).finish("commit")

    async def rollback_transaction(self, transaction: str) -> None:
        t = self._txns.pop(transaction, None)
        if t:
            await t.finish("rollback")

    def is_write_conflict(self, error: BaseException) -> bool:
        return type(error).__name__ in ("SerializationConflict", "DeadlockDetected", "LockTimeout")

    def is_transient_error(self, error: BaseException, *, explicit: bool | None = None) -> bool:
        return self.is_write_conflict(error)

    async def _one(self, sql: str, *args: Any, transaction: str | None = None) -> dict | None:  # noqa: ANN401
        r = await self._q(lambda tx: tx.fetchrow(sql, *args), transaction)
        return _doc(r["doc"]) if r else None

    @staticmethod
    def _shape(doc: dict, collection: str, fields: list[str] | None) -> dict:
        """``return_fields=["_key"]`` names the stored ``id`` (callers resolve records and paths that way)."""
        return {f: doc.get("id" if f == "_key" else f) for f in fields} if fields else _arango(doc, collection)


def _field(name: str) -> str:
    return "id" if name == "_key" else name


def _match(filters: dict | None, in_filters: dict | None, first: int, n: str = "n") -> tuple[str, list]:
    """Equality and membership predicates on ``{n}.doc`` as SQL using parameters ``$first``... and their values."""
    sql, args = "", []
    if filters:
        sql += f""" AND NOT EXISTS (SELECT 1 FROM unnest(${first}::text[], ${first + 1}::text[]) f(k, v)
            WHERE {n}.doc->f.k IS DISTINCT FROM f.v::jsonb)"""
        args += [[_field(k) for k in filters], [_j(v) for v in filters.values()]]
        first += 2
    if in_filters:
        sql += f""" AND NOT EXISTS (SELECT 1 FROM jsonb_each(${first}::jsonb) f WHERE NOT EXISTS
            (SELECT 1 FROM jsonb_array_elements(f.value) x WHERE x = {n}.doc->f.key))"""
        args.append(_j({_field(k): list(v) for k, v in in_filters.items()}))
    return sql, args


# Filter keys with an expression index (graph_0001): a literal predicate on the same expression lets the planner use it. Keys are from this list only.
_HOT = {"blocks": ("recordId", "nameFolded"), "codeFiles": ("filePath",)}


def _hot_values(collection: str, filters: dict | None) -> tuple[str | None, str | None, str | None]:
    """Literal values of the indexed keys (recordId, nameFolded, filePath) for a bound, null-when-absent predicate; only the collections of ``_HOT`` have them."""
    f = filters or {}
    keys = _HOT.get(collection, ())
    return tuple(f[k] if k in keys and isinstance(f.get(k), str) else None for k in ("recordId", "nameFolded", "filePath"))  # type: ignore[return-value]


_LINK_COLLS = ["nodeRelations", "recordLinks"]  # a link is a nodeRelations row with a link type, or a recordLinks row

