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


def _pull(taken, total: int, workers: int, out) -> None:
    """A client process: ``workers`` threads take queries from the shared counter until it runs out (each search takes 2 ms)."""
    import threading
    import time

    got: list[int] = []

    def worker() -> None:
        while (qi := bench._take(taken, total)) is not None:
            time.sleep(0.002)
            got.append(qi)
    threads = [threading.Thread(target=worker) for _ in range(workers)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    out.put((workers, got))


def test_every_worker_of_every_process_takes_from_one_queue_until_the_total_is_taken() -> None:
    """--clients 7 over 4 processes is 2, 2, 2 and 1 workers: a process's share of the queries must follow its workers, so that no
    worker runs out while another process still has queries (fixed shares of 500 left the 2-worker processes idle half the run)."""
    ctx = mp.get_context("fork")
    taken, out = ctx.Value("q", 0), ctx.Queue()
    procs = [ctx.Process(target=_pull, args=(taken, 400, w, out)) for w in (2, 2, 2, 1)]
    for p in procs:
        p.start()
    got = [out.get(timeout=60) for _ in procs]
    for p in procs:
        p.join(10)
    every = [qi for _, g in got for qi in g]
    assert sorted(every) == list(range(400))                                       # each query once, none left over
    assert bench._take(taken, 400) is None
    one = [len(g) for w, g in got if w == 1]
    two = [len(g) for w, g in got if w == 2]
    assert max(one) < min(two)                                                    # the share follows the workers, not the process count


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
