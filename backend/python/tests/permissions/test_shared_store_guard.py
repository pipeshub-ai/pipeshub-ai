"""The store-backed permission tests refuse to run on stores that several pytest-xdist workers would share."""

from __future__ import annotations

import pytest

from tests.integration.graph_permissions import conftest as harness

NEO4J = {"uri": "bolt://127.0.0.1:47999", "username": "neo4j", "password": "x", "database": "neo4j"}
ARANGO = {"url": "http://127.0.0.1:48999", "username": "root", "password": "x", "db": "kh_perm_test"}


@pytest.mark.asyncio
@pytest.mark.parametrize("wipe, settings", [(harness._wipe_neo4j, NEO4J), (harness._drop_arango_db, ARANGO)])
async def test_a_wipe_under_several_workers_fails_before_touching_the_store(monkeypatch, wipe, settings) -> None:
    """Each module wipes the one database it shares with the other workers: a world vanishes mid-check and reads as
    an under-admit. The wipe must refuse before it connects (nothing listens on these ports)."""
    monkeypatch.setenv("PYTEST_XDIST_WORKER_COUNT", "4")
    with pytest.raises(RuntimeError, match="workers would share"):
        await wipe(settings)


@pytest.mark.parametrize("count", [None, "1"])
def test_one_worker_or_none_is_allowed(monkeypatch, count) -> None:
    if count is None:
        monkeypatch.delenv("PYTEST_XDIST_WORKER_COUNT", raising=False)
    else:
        monkeypatch.setenv("PYTEST_XDIST_WORKER_COUNT", count)
    harness._assert_one_worker()
