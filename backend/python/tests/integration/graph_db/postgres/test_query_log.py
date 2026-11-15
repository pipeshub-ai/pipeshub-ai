"""db.query records on a real Postgres: one per failed or slow statement, with SQLSTATE and class, no bound value in any record,
and the caller found across a provider transaction's task hop."""
from __future__ import annotations

import logging
import uuid

import asyncpg
import pytest

from app.core.db import DbTimeout
from app.utils.logger import log_record_fields

pytestmark = pytest.mark.integration

EMAIL = "alice.p9a@example.com"


def _records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.getMessage().startswith("db.query.")]


def _no_email(caplog: pytest.LogCaptureFixture) -> None:
    assert not [r for r in caplog.records if EMAIL in r.getMessage() or EMAIL in str(vars(r))]


@pytest.fixture
def org() -> str:
    return uuid.uuid4().hex[:24]  # graph.nodes holds ObjectId-shaped org ids (org_id_format)


async def test_unique_violation(pg_db, org_ctx, org, caplog) -> None:
    sql = ("/* pipeshub:test.query_log_insert */ INSERT INTO graph.nodes (org_id, coll, id, doc) VALUES "
           "(current_setting('app.org_id'), 'people', 'dup', jsonb_build_object('email', $1::text)), "
           "(current_setting('app.org_id'), 'people', 'dup', jsonb_build_object('email', $1::text))")
    with org_ctx(org), caplog.at_level(logging.DEBUG, logger="app.core.db.uow"), pytest.raises(asyncpg.UniqueViolationError):
        await pg_db.run(lambda tx: tx.execute(sql, EMAIL), write=True)
    (record,) = _records(caplog)
    fields = log_record_fields(record)
    assert record.levelno == logging.WARNING and fields["code"] == "23505" and fields["error_class"] == "conflict"
    assert fields["site"] == "test.query_log_insert" and fields["caller"] == "test_query_log.test_unique_violation"
    _no_email(caplog)


async def test_syntax_error(pg_db, org_ctx, org, caplog) -> None:
    with org_ctx(org), caplog.at_level(logging.DEBUG, logger="app.core.db.uow"), pytest.raises(asyncpg.PostgresSyntaxError):
        await pg_db.run(lambda tx: tx.fetch("SELEC id FROM graph.nodes WHERE doc->>'email' = $1", EMAIL))
    (record,) = _records(caplog)
    fields = log_record_fields(record)
    assert record.levelno == logging.ERROR and fields["code"] == "42601" and fields["error_class"] == "internal"
    assert fields["site"] == "untagged"
    _no_email(caplog)


async def test_statement_timeout(pg_db, org_ctx, org, caplog) -> None:
    with org_ctx(org), caplog.at_level(logging.DEBUG, logger="app.core.db.uow"), pytest.raises(DbTimeout):
        await pg_db.run(lambda tx: tx.fetchval("/* pipeshub:test.query_log_sleep */ SELECT pg_sleep(3)"), timeout_ms=300)
    (record,) = _records(caplog)
    fields = log_record_fields(record)
    assert fields["code"] == "57014" and fields["error_class"] == "timeout" and fields["error_type"] == "DbTimeout"
    assert 250 <= fields["duration_ms"] < 3000


async def test_slow_statement(pg_db, org_ctx, org, caplog, monkeypatch) -> None:
    monkeypatch.setenv("DB_SLOW_QUERY_MS", "50")
    with org_ctx(org), caplog.at_level(logging.DEBUG, logger="app.core.db.uow"):
        await pg_db.run(lambda tx: tx.fetchval("/* pipeshub:test.query_log_slow */ SELECT pg_sleep(0.1)"))
    (record,) = _records(caplog)
    fields = log_record_fields(record)
    assert record.getMessage() == "db.query.slow" and record.levelno == logging.WARNING
    assert fields["site"] == "test.query_log_slow" and fields["threshold_ms"] == 50 and fields["duration_ms"] >= 100


async def test_bad_value_is_bad_request_and_never_logged(pg_db, org_ctx, org, caplog) -> None:
    with org_ctx(org), caplog.at_level(logging.DEBUG), pytest.raises(asyncpg.DataError):
        await pg_db.run(lambda tx: tx.fetchval("/* pipeshub:test.query_log_cast */ SELECT $1::text::uuid", EMAIL))
    (record,) = _records(caplog)
    fields = log_record_fields(record)
    assert fields["code"] == "22P02" and fields["error_class"] == "bad_request" and record.levelno == logging.WARNING
    _no_email(caplog)


async def lookup_in_transaction(pg_provider, txn: str) -> object:
    return await pg_provider._q(lambda tx: tx.fetch("/* pipeshub:test.query_log_txn */ SELEC 1"), txn)


async def test_caller_inside_a_provider_transaction(pg_provider, org_ctx, org, caplog) -> None:
    with org_ctx(org), caplog.at_level(logging.DEBUG, logger="app.core.db.uow"):
        txn = await pg_provider.begin_transaction([], ["nodes"])
        try:
            with pytest.raises(asyncpg.PostgresSyntaxError):
                await lookup_in_transaction(pg_provider, txn)
        finally:
            await pg_provider.rollback_transaction(txn)
    (record,) = _records(caplog)
    fields = log_record_fields(record)
    assert fields["caller"] == "test_query_log.lookup_in_transaction"  # the statement ran in _Txn's task
    assert record.funcName == "lookup_in_transaction" and record.filename == "test_query_log.py"
