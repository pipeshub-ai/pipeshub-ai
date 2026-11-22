"""the record lease a stage and a re-index share is kept on PostgreSQL state even with the capacity switch off."""

import pytest

from app import indexing_main


@pytest.mark.parametrize(
    ("switch", "redis_mode", "expected"),
    [("true", "standalone", True), ("false", "standalone", False), ("false", "postgres", True), ("true", "postgres", True)],
)
def test_the_lease_manager_is_kept_on_pg_state(monkeypatch, switch, redis_mode, expected) -> None:
    monkeypatch.setenv("DISTRIBUTED_INDEXING_CONCURRENCY", switch)
    monkeypatch.setenv("REDIS_MODE", redis_mode)
    assert indexing_main._distributed_leases_wanted() is expected
