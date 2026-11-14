"""The pgvector search benchmark's client bookkeeping: the work adds up to what was asked, and a client that dies stops the run."""

from __future__ import annotations

import multiprocessing as mp
import sys

import pytest

from tests.perf import bench_pgvector_search as bench


def test_clients_and_queries_are_split_so_the_parts_add_up() -> None:
    assert [bench._share(18, 4, i) for i in range(4)] == [5, 5, 4, 4]
    assert [bench._share(16, 4, i) for i in range(4)] == [4, 4, 4, 4]
    assert sum(bench._share(2001, 4, i) for i in range(4)) == 2001


def test_a_client_that_exits_before_it_is_ready_stops_the_run_and_says_which() -> None:
    ctx = mp.get_context("fork")
    out = ctx.Queue()
    dead = ctx.Process(target=sys.exit, args=(3,))
    dead.start()
    dead.join(10)
    with pytest.raises(SystemExit, match=r"exited before sending 'ready'.*\(0, 3\)"):
        bench._gather(out, "ready", [dead])


def test_messages_of_every_client_are_collected_by_kind() -> None:
    ctx = mp.get_context("fork")
    out = ctx.Queue()
    for msg in (("ready", 1, None), ("ready", 0, None), ("result", 1, "r1"), ("result", 0, "r0")):
        out.put(msg)

    class Alive:
        exitcode = None

    assert bench._gather(out, "ready", [Alive(), Alive()]) == {1: None, 0: None}
    assert bench._gather(out, "result", [Alive(), Alive()]) == {0: "r0", 1: "r1"}


def test_the_dsn_given_is_the_database_and_the_login_measured_whatever_the_environment_says(monkeypatch: pytest.MonkeyPatch) -> None:
    import argparse
    import asyncio

    from app.services.vector_db.pgvector import pgvector

    seen = []

    async def create(config):  # noqa: ANN202
        seen.append(config)
        return config
    monkeypatch.setattr(pgvector.PgVectorService, "create", staticmethod(create))
    monkeypatch.setenv("PGVECTOR_DSN", "postgresql://pipeshub_indexing_login@elsewhere:5432/other")
    flag = "postgresql://pipeshub_query_login@127.0.0.1:6432/bench"
    asyncio.run(bench._service(argparse.Namespace(pg_dsn=flag, pool=3), "pgvector"))
    [cfg] = seen
    assert cfg.dsn == flag and cfg.to_db_config().service == "query" and cfg.pool_max == 3
    assert cfg.search.validate_quantization is False and cfg.search.sampler_rate == 0       # nothing runs in the background of the window
