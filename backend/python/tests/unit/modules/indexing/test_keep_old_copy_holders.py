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
