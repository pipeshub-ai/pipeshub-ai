"""The connectors service starts the projection worker for a PostgreSQL graph, and refuses to start with the projection off there: the knowledge hub lists a PostgreSQL
graph's sources from the typed graph, so with no projection every source would stay "preparing"."""
import logging
from unittest.mock import MagicMock

import pytest

from app.services.graph_db.postgres.kg import worker
from app.services.graph_db.postgres.provider import PostgresGraphProvider

LOG = logging.getLogger("test")


@pytest.mark.parametrize("value", [None, "0"])
def test_a_graph_that_is_not_postgresql_starts_no_worker(monkeypatch, value) -> None:
    monkeypatch.delenv("PG_KG_PROJECT", raising=False)
    if value is not None:
        monkeypatch.setenv("PG_KG_PROJECT", value)
    assert worker.start_for(object(), LOG) is None


def test_the_projection_off_on_a_postgresql_graph_is_refused_with_a_message_that_names_the_setting(monkeypatch) -> None:
    monkeypatch.setenv("PG_KG_PROJECT", "0")
    with pytest.raises(ValueError, match="PG_KG_PROJECT=0"):
        worker.start_for(MagicMock(spec=PostgresGraphProvider), LOG)


@pytest.mark.parametrize("value", [None, "1"])
async def test_a_postgresql_graph_starts_the_worker_by_default(monkeypatch, value) -> None:
    if value is None:
        monkeypatch.delenv("PG_KG_PROJECT", raising=False)
    else:
        monkeypatch.setenv("PG_KG_PROJECT", value)
    monkeypatch.setattr("app.services.pg_runtime.listen_dsn", lambda *a: None)
    started = []
    monkeypatch.setattr(worker.KgWorker, "start", lambda self: started.append(self))
    provider = MagicMock(spec=PostgresGraphProvider)
    provider._db = object()
    w = worker.start_for(provider, LOG)
    assert w is not None and started == [w]
