"""Postgres db.query records: one per failed or slow statement with SQLSTATE, class and site; the caller found across the task
hop of a provider transaction (_Txn); a cancelled statement writes nothing."""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

import asyncpg
import pytest

from app.core.db import (
    CommitOutcomeUnknown,
    ConnectionLost,
    DbConfig,
    DbTimeout,
    FailedTransaction,
    KernelDB,
    PrologueFailed,
    uow,
)
from app.core.db.errors import PoolTimeout
from app.core.db.uow import Tx, describe_pg_error
from app.core.org_context import OrgContext, bind
from app.services.graph_db.postgres.base import PgBase
from app.services.graph_db.postgres.kg import flush  # noqa: F401  # PgBase() imports it lazily; loaded here, at collection, a test's patch.dict(sys.modules) can't drop it and a re-import re-register guard.py's metric
from app.utils import error_class as EC
from app.utils.logger import log_record_fields

from . import _fake_kernel

if TYPE_CHECKING:
    from collections.abc import Iterator

EMAIL = "alice@example.com"


class _Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


@pytest.fixture
def records() -> Iterator[list[logging.LogRecord]]:
    logger = logging.getLogger("app.core.db.uow")
    handler = _Capture()
    old_level, old_propagate = logger.level, logger.propagate
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    yield handler.records
    logger.removeHandler(handler)
    logger.setLevel(old_level)
    logger.propagate = old_propagate


def _pg(cls: type, state: str, message: str = "boom") -> Exception:
    e = cls(message)
    e.sqlstate = state
    return e


class _Con:
    """A connection whose statements raise ``error`` (or sleep ``delay`` s) and that stays in its transaction."""

    def __init__(self, error: BaseException | None = None, delay: float = 0.0) -> None:
        self.error, self.delay = error, delay

    async def fetch(self, sql: str, *args: object) -> list:
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error is not None:
            raise self.error
        return []

    def is_in_transaction(self) -> bool:
        return True


def _db() -> KernelDB:
    return KernelDB(DbConfig(service="connectors", dsn="postgresql://x@h/db"))


def _tx(con: _Con, db: KernelDB | None = None) -> Tx:
    return Tx(con, db or _db(), klass="api", idempotent=False, request_id="unit-req")  # type: ignore[arg-type]


async def provider_lookup(tx: Tx) -> list:
    return await tx.fetch("/* pipeshub:graph.get_existing_keys */ SELECT id FROM graph.nodes WHERE id = $1", EMAIL)


def _db_records(records: list[logging.LogRecord]) -> list[logging.LogRecord]:
    return [r for r in records if r.getMessage().startswith("db.query.")]


class TestStatement:
    @pytest.mark.parametrize(("error", "cls", "level"), [
        (_pg(asyncpg.UniqueViolationError, "23505"), "conflict", logging.WARNING),
        (_pg(asyncpg.NotNullViolationError, "23502"), "bad_request", logging.WARNING),
        (_pg(asyncpg.ProgramLimitExceededError, "54000"), "bad_request", logging.WARNING),
        (_pg(asyncpg.InvalidTextRepresentationError, "22P02", f'invalid input syntax for type uuid: "{EMAIL}"'), "bad_request",
         logging.WARNING),
        (_pg(asyncpg.PostgresSyntaxError, "42601"), "internal", logging.ERROR),
        (_pg(asyncpg.InsufficientPrivilegeError, "42501"), "misconfigured", logging.ERROR),
    ])
    async def test_failed_statement(self, records, error: Exception, cls: str, level: int) -> None:
        with pytest.raises(type(error)) as raised:
            await provider_lookup(_tx(_Con(error)))
        (record,) = _db_records(records)
        fields = log_record_fields(record)
        assert record.levelno == level and fields["error_class"] == cls and fields["code"] == error.sqlstate
        assert fields["backend"] == "postgres" and fields["op"] == "fetch" and fields["site"] == "graph.get_existing_keys"
        assert fields["caller"] == "test_query_log_unit.provider_lookup" and record.funcName == "provider_lookup"
        assert fields["db_request_id"] == "unit-req" and EMAIL not in str(vars(record))
        records.clear()
        EC.log_failure(logging.getLogger("app.core.db.uow"), "sync_run.end", raised.value)
        (line,) = records
        assert log_record_fields(line)["error_class"] == cls and EMAIL not in str(vars(line))

    async def test_statement_timeout_is_mapped_and_classed(self, records) -> None:
        with pytest.raises(DbTimeout) as raised:
            await provider_lookup(_tx(_Con(_pg(asyncpg.QueryCanceledError, "57014"))))
        fields = log_record_fields(_db_records(records)[0])
        assert fields["error_class"] == "timeout" and fields["code"] == "57014" and fields["error_type"] == "DbTimeout"
        assert EC.classify_error(raised.value) == "timeout"

    async def test_slow_statement(self, records, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DB_SLOW_QUERY_MS", "20")
        await provider_lookup(_tx(_Con(delay=0.03)))
        (record,) = _db_records(records)
        fields = log_record_fields(record)
        assert record.getMessage() == "db.query.slow" and fields["site"] == "graph.get_existing_keys"
        assert fields["threshold_ms"] == 20 and fields["db_request_id"] == "unit-req"
        assert not [r for r in records if "slow statement" in r.getMessage()]  # main's message-interpolated line is gone

    async def test_cancelled_slow_statement_writes_nothing(self, records, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DB_SLOW_QUERY_MS", "1")
        task = asyncio.ensure_future(provider_lookup(_tx(_Con(delay=1.0))))
        await asyncio.sleep(0.02)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert _db_records(records) == []


@pytest.mark.parametrize(("error", "cls"), [
    (DbTimeout("t"), "timeout"), (PoolTimeout("p"), "timeout"),
    (ConnectionLost("c", retry_safe=True), "unavailable"), (PrologueFailed("p"), "misconfigured"),
])
def test_typed_errors_declare_their_class(error: Exception, cls: str) -> None:
    assert EC.classify_error(error) == cls and describe_pg_error(error)[1] == cls


# ---- the caller across a provider transaction's task hop -----------------------------------------------------

class Provider(PgBase):
    async def get_keys(self, txn: str | None) -> list:
        return await self._q(lambda tx: tx.fetch("/* pipeshub:test.get_keys */ SELECT 1"), txn)

    async def count_keys(self, txn: str | None) -> list:
        return await self._q(lambda tx: tx.fetch("/* pipeshub:test.count_keys */ SELECT 2"), txn)

    async def save(self, txn: str) -> None:
        await self.commit_transaction(txn)


@pytest.fixture
def org(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(uow, "QUERY_LOG_SKIP", (*uow.QUERY_LOG_SKIP, _fake_kernel.__name__))  # the fake plays app.core.db
    with bind(OrgContext("65f0aaaaaaaaaaaaaaaaaaaa", None, "job", "job:test", "t")):
        yield


def _provider(con: _Con, **kw: bool) -> Provider:
    return Provider(logging.getLogger("test.pg_provider"), _fake_kernel.TxnDb(con, **kw))


class TestTxnCaller:
    async def test_in_transaction_statement_names_the_provider_method(self, records, org) -> None:
        provider = _provider(_Con(_pg(asyncpg.UniqueViolationError, "23505")))
        txn = await provider.begin_transaction([], ["nodes"])
        with pytest.raises(asyncpg.UniqueViolationError):
            await provider.get_keys(txn)
        (record,) = _db_records(records)
        fields = log_record_fields(record)
        assert fields["caller"] == "test_query_log_unit.Provider.get_keys"  # main's walk: unknown (the caller is suspended)
        assert record.filename == "test_query_log_unit.py" and record.funcName == "get_keys"
        assert record.lineno == Provider.get_keys.__code__.co_firstlineno + 1  # its await line
        await provider.rollback_transaction(txn)

    async def test_consecutive_calls_on_one_transaction_name_their_own_method(self, records, org, monkeypatch) -> None:
        monkeypatch.setenv("DB_SLOW_QUERY_MS", "1")
        provider = _provider(_Con(delay=0.005))
        txn = await provider.begin_transaction([], ["nodes"])
        await provider.get_keys(txn)
        await provider.count_keys(txn)
        assert [log_record_fields(r)["caller"] for r in _db_records(records)] == [
            "test_query_log_unit.Provider.get_keys", "test_query_log_unit.Provider.count_keys"]
        await provider.rollback_transaction(txn)

    async def test_outside_a_transaction_the_walk_skips_the_plumbing(self, records, org) -> None:
        provider = _provider(_Con(_pg(asyncpg.PostgresSyntaxError, "42601")))
        with pytest.raises(asyncpg.PostgresSyntaxError):
            await provider.get_keys(None)
        assert log_record_fields(_db_records(records)[0])["caller"] == "test_query_log_unit.Provider.get_keys"  # not base.PgBase._q

    async def test_commit_names_the_method_that_committed(self, records, org) -> None:
        provider = _provider(_Con(), commit_fails=True)
        txn = await provider.begin_transaction([], ["nodes"])
        await provider.get_keys(txn)
        await provider.save(txn)
        (record,) = _db_records(records)
        fields = log_record_fields(record)
        assert fields["op"] == "commit" and fields["caller"] == "test_query_log_unit.Provider.save"

    async def test_positive_control_storing_the_immediate_frame_loses_the_caller(self, records, org, monkeypatch) -> None:
        import sys

        from app.services.graph_db.postgres import base

        monkeypatch.setattr(base, "caller_origin", lambda skip: sys._getframe(2))  # what the first plan stored: _q's frame
        provider = _provider(_Con(_pg(asyncpg.UniqueViolationError, "23505")))
        txn = await provider.begin_transaction([], ["nodes"])
        with pytest.raises(asyncpg.UniqueViolationError):
            await provider.get_keys(txn)
        assert log_record_fields(_db_records(records)[0])["caller"] != "test_query_log_unit.Provider.get_keys"
        await provider.rollback_transaction(txn)


# ---- the unit's own statements (KernelDB._open), each failed in turn ----------------------------------------

class _UnitCon:
    """A pooled connection whose step ``fail`` raises ``error`` (asyncpg's API as KernelDB._open uses it)."""

    def __init__(self, fail: str, error: BaseException) -> None:
        self.fail, self.error = fail, error

    def _maybe(self, step: str) -> None:
        if self.fail == step:
            raise self.error

    def transaction(self, **_: object) -> _UnitCon:
        return self

    async def start(self) -> None:
        self._maybe("begin")

    async def commit(self) -> None:
        self._maybe("commit")

    async def rollback(self) -> None:
        return None

    async def fetchrow(self, sql: str, *args: object) -> dict:
        self._maybe("prologue")
        return {"r": "none", "started": 1}

    async def fetchval(self, sql: str, *args: object) -> object:
        if sql == "SELECT now()":
            self._maybe("check_alive")
        return 1

    async def execute(self, sql: str, *args: object) -> str:
        if sql.startswith("ROLLBACK TO SAVEPOINT"):
            self._maybe("rollback_to")
        return "OK"

    async def copy_records_to_table(self, table: str, **_: object) -> None:
        self._maybe("copy")

    def is_in_transaction(self) -> bool:
        return True

    def is_closed(self) -> bool:
        return False

    def terminate(self) -> None:
        return None


class _Pool:
    def __init__(self, con: _UnitCon) -> None:
        self.con = con

    async def acquire(self, timeout: float) -> _UnitCon:
        return self.con

    async def release(self, con: _UnitCon) -> None:
        return None


async def _unit_body(tx: Tx, step: str) -> None:
    if step == "copy":
        await tx.copy_into_temp("stage_rows", {"id": "text"}, [("a",)])
    elif step == "rollback_to":
        async with tx.savepoint("sp"):
            raise ValueError("the body's own failure")  # not a statement: only the ROLLBACK TO is recorded
    elif step == "swallowed":
        tx.failed = True  # a statement error the body swallowed (already recorded when it happened)


async def _run_unit(step: str, error: BaseException) -> None:
    db = KernelDB(DbConfig(service="connectors", dsn="postgresql://x@h/db"))
    db._home_loop = asyncio.get_running_loop()
    db._pool = _Pool(_UnitCon(step, error))
    with bind(OrgContext("65f0aaaaaaaaaaaaaaaaaaaa", None, "job", "job:test", "t")):
        async with db.uow(write=True) as tx:
            await _unit_body(tx, step)


@pytest.mark.parametrize(("step", "error", "raised", "cls", "code"), [
    ("begin", _pg(asyncpg.ConnectionDoesNotExistError, "08003"), ConnectionLost, "unavailable", "08003"),
    ("prologue", _pg(asyncpg.InsufficientPrivilegeError, "42501"), PrologueFailed, "misconfigured", "42501"),
    ("check_alive", _pg(asyncpg.QueryCanceledError, "57014"), DbTimeout, "timeout", "57014"),
    ("copy", _pg(asyncpg.UniqueViolationError, "23505"), asyncpg.UniqueViolationError, "conflict", "23505"),
    ("rollback_to", _pg(asyncpg.AdminShutdownError, "57P01"), ConnectionLost, "unavailable", "57P01"),
    ("commit", OSError("connection reset"), CommitOutcomeUnknown, "unavailable", None),
])
async def test_each_unit_step_writes_one_record(records, step: str, error: Exception, raised: type, cls: str,
                                                code: str | None) -> None:
    with pytest.raises(raised):
        await _run_unit(step, error)
    (record,) = _db_records(records)
    fields = log_record_fields(record)
    assert fields["op"] == step and fields["error_class"] == cls and fields.get("code") == code
    assert fields["error_type"] == raised.__name__ and fields["backend"] == "postgres"


async def test_a_slow_copy_writes_one_slow_record(records, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DB_SLOW_QUERY_MS", "20")

    class _SlowCopy(_UnitCon):
        async def copy_records_to_table(self, table: str, **_: object) -> None:
            await asyncio.sleep(0.03)

    tx = Tx(_SlowCopy("none", RuntimeError("unused")), _db(), klass="api", idempotent=False, request_id="unit-req")  # type: ignore[arg-type]
    await tx.copy_into_temp("stage_rows", {"id": "text"}, [("a",)])
    (record,) = _db_records(records)
    fields = log_record_fields(record)
    assert record.getMessage() == "db.query.slow" and fields["op"] == "copy" and fields["threshold_ms"] == 20


@pytest.mark.parametrize(("step", "error"), [
    ("check_alive", asyncpg.InFailedSQLTransactionError("current transaction is aborted")),  # the probe finds an earlier failure
    ("swallowed", RuntimeError("unused")),  # the tx.failed fast path
])
async def test_check_alive_reporting_an_earlier_failure_writes_nothing(records, step: str, error: Exception) -> None:
    with pytest.raises(FailedTransaction):
        await _run_unit(step, error)
    assert _db_records(records) == []
