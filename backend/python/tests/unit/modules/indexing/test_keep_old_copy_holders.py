"""The holder predicate of a record's pending virtual record, and the switches of the keep-old-copy swap."""
import pytest

from app.modules.indexing import keep_old_copy
from app.services.graph_db.common.record_visibility import (
    aql_holds_vrid,
    cypher_holds_vrid,
    holds_vrid,
    sql_holds_vrid,
)


def test_a_record_holds_the_vrid_it_names_whatever_its_status():
    assert holds_vrid({"virtualRecordId": "x", "indexingStatus": "FAILED"}, "x")


@pytest.mark.parametrize("status", ["IN_PROGRESS", "QUEUED", "NOT_STARTED"])
def test_a_running_or_waiting_record_holds_its_pending_vrid(status):
    assert holds_vrid({"virtualRecordId": "x", "pendingVirtualRecordId": "y", "indexingStatus": status}, "y")


@pytest.mark.parametrize("status", ["COMPLETED", "FAILED", "EMPTY", "FILE_TYPE_NOT_SUPPORTED", "AUTO_INDEX_OFF"])
def test_any_other_status_releases_the_pending_vrid(status):
    assert not holds_vrid({"virtualRecordId": "x", "pendingVirtualRecordId": "y", "indexingStatus": status}, "y")


def test_the_three_store_fragments_carry_the_same_guard_and_statuses():
    aql, cy, sql = aql_holds_vrid("r", "vid"), cypher_holds_vrid("r", "vid"), sql_holds_vrid("n", "w.v")
    for fragment in (aql, cy, sql):
        assert "pendingVirtualRecordId" in fragment
        for status in ("IN_PROGRESS", "QUEUED", "NOT_STARTED"):
            assert status in fragment
    assert "pendingVirtualRecordId != null" in aql  # the sparse index serves correlated forms only with the guard
    assert "doc ? 'pendingVirtualRecordId'" in sql  # the partial index predicate is repeated


def test_the_flag_is_off_by_default(monkeypatch):
    monkeypatch.delenv(keep_old_copy.ENV_ENABLED, raising=False)
    assert not keep_old_copy.enabled()
    monkeypatch.setenv(keep_old_copy.ENV_ENABLED, "on")
    assert keep_old_copy.enabled()


@pytest.mark.asyncio
async def test_the_swap_is_refused_without_a_confirmed_fleet(monkeypatch):
    monkeypatch.setenv(keep_old_copy.ENV_ENABLED, "on")
    assert not await keep_old_copy.swap_allowed(None)


def test_the_grace_follows_the_cache_ttl(monkeypatch):
    monkeypatch.delenv("PIPESHUB_ACCESSIBLE_RECORDS_CACHE_TTL", raising=False)
    monkeypatch.delenv("REDIS_MODE", raising=False)
    monkeypatch.setenv("KV_STORE_TYPE", "redis")
    assert keep_old_copy.grace_seconds() == 330.0
    monkeypatch.setenv("PIPESHUB_ACCESSIBLE_RECORDS_CACHE_TTL", "100")
    assert keep_old_copy.grace_seconds() == 130.0


def test_the_postgres_statements_carry_the_shared_fragment_verbatim():
    """The provider's statements are literals (a test forbids building them), so the fragment is repeated there: it must not drift."""
    from pathlib import Path

    text = Path("app/services/graph_db/postgres/records.py").read_text(encoding="utf-8")
    assert text.count(sql_holds_vrid("n", "$2")) == 1
    assert text.count(sql_holds_vrid("n", "w.v")) == 2


@pytest.mark.asyncio
async def test_the_postgres_broker_needs_the_operators_statement_and_no_older_claimant(monkeypatch):
    from unittest.mock import AsyncMock, patch

    from app.services.messaging.config import MessageBrokerType

    monkeypatch.setenv(keep_old_copy.ENV_ENABLED, "on")
    monkeypatch.setattr("app.services.messaging.config.get_message_broker_type", lambda: MessageBrokerType.POSTGRES)
    monkeypatch.delenv(keep_old_copy.ENV_PG_BROKER_CONFIRMED, raising=False)
    assert not await keep_old_copy.swap_allowed(None)  # no statement: refused
    monkeypatch.setenv(keep_old_copy.ENV_PG_BROKER_CONFIRMED, "on")
    with patch("app.services.messaging.postgres.queue.PgQueue.untagged_claimants", AsyncMock(return_value=["old-pod"])):
        assert not await keep_old_copy.swap_allowed(None)  # a working older pod: refused
    with patch("app.services.messaging.postgres.queue.PgQueue.untagged_claimants", AsyncMock(side_effect=RuntimeError("down"))):
        assert not await keep_old_copy.swap_allowed(None)  # unknown: refused
    with patch("app.services.messaging.postgres.queue.PgQueue.untagged_claimants", AsyncMock(return_value=[])):
        assert await keep_old_copy.swap_allowed(None)


@pytest.mark.asyncio
async def test_a_refusal_is_logged_once_not_per_run(monkeypatch, caplog):
    import logging

    monkeypatch.setenv(keep_old_copy.ENV_ENABLED, "on")
    keep_old_copy._refusals_logged.clear()
    with caplog.at_level(logging.WARNING, logger="keep_old_copy"):
        for _ in range(5):
            await keep_old_copy.swap_allowed(None)
    assert sum("Keep-old-copy swap refused" in r.getMessage() for r in caplog.records) == 1


@pytest.mark.asyncio
async def test_a_record_that_is_not_indexed_is_left_to_a_later_pass_by_the_named_entity_completion():
    """A record replacing its copy (or that failed doing so) is tried again later: its revision is the attempt's, not the served copy's."""
    from unittest.mock import AsyncMock, MagicMock

    from app.modules.indexing.named_entity_completion import _complete_one
    from app.modules.indexing.named_entity_persist_retry import _BUSY

    graph = MagicMock()
    graph.get_document = AsyncMock(return_value={"virtualRecordId": "x", "indexingStatus": "IN_PROGRESS", "orgId": "o"})
    assert await _complete_one(graph, MagicMock(), MagicMock(), "r1", "o", 0, 0) is _BUSY
