"""The kg statistics job (``python -m db.maintain analyze``, and every pass of ``placement --watch``): a connector's first load asks (``kg.w_analyze_request``), the job
ANALYZEs the kg browse tables as the owner and deletes the requests the new statistics cover, at most once per table per interval. Against a migrated database
(PostgreSQL 17 and 18)."""

import contextlib

import psycopg
import pytest
from psycopg import errors as pg

from db import maintain
from db.lib import kg_stats
from db.migrate import locked

from .rls_fixture import ORG_A, ORG_B, uow
from .test_retrieval import connect_as

ASK = "SELECT kg.w_analyze_request(%s)"


def env(monkeypatch, cluster, name, every: str = "0"):
    monkeypatch.setenv("PG_MIGRATE_URL", cluster.url(name, "pipeshub_migrate"))
    monkeypatch.setenv("PG_JOBS_URL", f"postgresql://pipeshub_jobs@127.0.0.1:{cluster.port}/{name}")
    monkeypatch.setenv("PG_KG_ANALYZE_EVERY_S", every)


def ask(cluster, name, org, app_sk: int = 5) -> list:
    with connect_as(cluster, name, "pipeshub_connectors_login") as c, uow(c, "pipeshub_connectors", org):
        return c.execute(ASK, (app_sk,)).fetchall()


def requests(cluster, name) -> list:
    with cluster.admin(name) as c:
        return sorted(r[0] for r in c.execute("SELECT org_id FROM kg.analyze_requests"))


def last(cluster, name) -> dict:
    with cluster.admin(name) as c:
        return {t: c.execute("SELECT last_analyze FROM pg_stat_all_tables WHERE relid = %s::regclass", (t,)).fetchone()[0] for t in kg_stats.TABLES}


def test_a_first_load_asks_and_the_job_analyzes_every_browse_table_as_the_owner_and_clears_the_request(rdb, monkeypatch):
    cluster, name = rdb
    assert ask(cluster, name, ORG_A) == [(True,)] and ask(cluster, name, ORG_A, 6) == [(True,)]          # asking twice keeps one request per org
    assert ask(cluster, name, ORG_B) == [(True,)]
    with connect_as(cluster, name, "pipeshub_connectors_login") as c, uow(c, "pipeshub_connectors", None):
        assert c.execute(ASK, (5,)).fetchall() == [(False,)]                                 # no org: no request
    with connect_as(cluster, name, "pipeshub_query_login") as c, uow(c, "pipeshub_query", ORG_A), pytest.raises(pg.InsufficientPrivilege):
        c.execute(ASK, (5,))                                                                  # only the connectors service asks
    with cluster.admin(name) as c:
        assert c.execute("SELECT app_sk FROM kg.analyze_requests WHERE org_id = %s", (ORG_A,)).fetchone()[0] == 5
    assert requests(cluster, name) == sorted([ORG_A, ORG_B])
    before = last(cluster, name)
    env(monkeypatch, cluster, name)
    assert maintain.main(["analyze"]) == 0
    after = last(cluster, name)
    assert all(after[t] is not None and after[t] != before[t] for t in kg_stats.TABLES), after
    assert requests(cluster, name) == []
    assert maintain.main(["analyze"]) == 0 and last(cluster, name) == after                   # nothing asked: nothing analyzed
    with cluster.admin(name) as c:
        assert c.execute("SELECT count(*) FROM kernel.rls_violations").fetchone()[0] == 0


def test_a_table_analyzed_within_the_interval_waits_and_its_request_stays_open_until_a_later_pass(rdb, monkeypatch):
    cluster, name = rdb
    env(monkeypatch, cluster, name)
    ask(cluster, name, ORG_A)
    assert maintain.main(["analyze"]) == 0
    first = last(cluster, name)
    ask(cluster, name, ORG_B)                                                                 # a second first load, inside the interval
    env(monkeypatch, cluster, name, every="3600")
    assert maintain.main(["analyze"]) == 0
    assert last(cluster, name) == first and requests(cluster, name) == [ORG_B]                # coalesced: not analyzed again, still asked for
    env(monkeypatch, cluster, name, every="0")
    assert maintain.main(["analyze"]) == 0
    assert all(v != first[t] for t, v in last(cluster, name).items()) and requests(cluster, name) == []


def test_statistics_taken_after_a_request_close_it_without_another_analyze(rdb, monkeypatch):
    cluster, name = rdb
    env(monkeypatch, cluster, name)
    ask(cluster, name, ORG_A)
    with cluster.admin(name) as c:                                                            # as autovacuum would have
        for t in kg_stats.TABLES:
            c.execute(f"ANALYZE {t}")
    seen = last(cluster, name)
    with psycopg.connect(f"postgresql://pipeshub_jobs@127.0.0.1:{cluster.port}/{name}", autocommit=True) as jobs:
        assert kg_stats.due(jobs) == [] and kg_stats.close_covered(jobs) == 1
    assert maintain.main(["analyze"]) == 0 and last(cluster, name) == seen


def test_a_later_ask_moves_the_request_so_statistics_taken_while_a_load_is_copied_do_not_close_it(rdb, monkeypatch):
    cluster, name = rdb
    env(monkeypatch, cluster, name)
    ask(cluster, name, ORG_A)                                                                 # a first seed, while most of the connector is still to be copied
    with cluster.admin(name) as c:                                                            # autovacuum, before the load ends
        for t in kg_stats.TABLES:
            c.execute(f"ANALYZE {t}")
    ask(cluster, name, ORG_A)                                                                 # the promotion that ends the first load
    with cluster.admin(name) as c:
        c.execute(f"ANALYZE {kg_stats.TABLES[0]}")                                            # one table is not every table
    with psycopg.connect(f"postgresql://pipeshub_jobs@127.0.0.1:{cluster.port}/{name}", autocommit=True) as jobs:
        assert kg_stats.close_covered(jobs) == 0 and sorted(kg_stats.due(jobs)) == sorted(kg_stats.TABLES[1:])
    seen = last(cluster, name)
    assert maintain.main(["analyze"]) == 0
    assert all(v != seen[t] for t, v in last(cluster, name).items() if t != kg_stats.TABLES[0]) and requests(cluster, name) == []


def jobs_conn(cluster, name):
    return psycopg.connect(f"postgresql://pipeshub_jobs@127.0.0.1:{cluster.port}/{name}", autocommit=True)


def test_a_request_is_timed_when_it_is_made_not_when_its_transaction_began(rdb, monkeypatch):
    cluster, name = rdb
    env(monkeypatch, cluster, name)
    with connect_as(cluster, name, "pipeshub_connectors_login") as c, uow(c, "pipeshub_connectors", ORG_A):   # a seed's transaction: it counts first, then asks
        with cluster.admin(name) as a:                                                        # statistics taken while it counts
            for t in kg_stats.TABLES:
                a.execute(f"ANALYZE {t}")
        assert c.execute(ASK, (5,)).fetchall() == [(True,)]
    with jobs_conn(cluster, name) as jobs:
        assert kg_stats.close_covered(jobs) == 0 and requests(cluster, name) == [ORG_A]


def test_a_table_analyzed_after_an_older_request_is_due_again_for_a_newer_one(rdb, monkeypatch):
    cluster, name = rdb
    env(monkeypatch, cluster, name)
    ask(cluster, name, ORG_A)
    with cluster.admin(name) as c:                                                            # one table analyzed; the others were skipped (locked)
        c.execute(f"ANALYZE {kg_stats.TABLES[0]}")
    ask(cluster, name, ORG_B)                                                                 # a later org's first load
    with jobs_conn(cluster, name) as jobs:
        assert sorted(kg_stats.due(jobs)) == sorted(kg_stats.TABLES)


def test_a_pass_that_waited_for_the_lock_checks_again_what_is_due(rdb, monkeypatch):
    cluster, name = rdb
    env(monkeypatch, cluster, name, every="3600")
    ask(cluster, name, ORG_A)
    real, seen = maintain.locked, {}

    @contextlib.contextmanager
    def behind_another_pass(url):
        with cluster.admin(name) as c:                                                        # the pass it waited behind analyzed every table
            for t in kg_stats.TABLES:
                c.execute(f"ANALYZE {t}")
        seen.update(last(cluster, name))
        with real(url) as owner:
            yield owner
    monkeypatch.setattr(maintain, "locked", behind_another_pass)
    assert maintain._analyze_pass(maintain.migrate_url()) == []
    assert last(cluster, name) == seen and requests(cluster, name) == []


def test_a_full_placement_pass_still_runs_the_analyze_pass(monkeypatch):
    class Stop(BaseException):
        pass
    calls: list[str] = []

    def full_pass(url):
        calls.append("placement")
        if calls.count("placement") > 3:
            raise Stop
        return [f"leaf{i}" for i in range(maintain.placement.MAX_PER_PASS)], [], []
    monkeypatch.setattr(maintain, "_placement_pass", full_pass)
    monkeypatch.setattr(maintain, "_analyze_pass", lambda url: calls.append("analyze") or [])
    monkeypatch.setattr(maintain.time, "sleep", lambda s: calls.append("sleep"))
    with pytest.raises(Stop):
        maintain._watch("postgresql://unused", 1.0)
    assert calls[:6] == ["placement", "analyze"] * 3                                          # every pass analyzes; a full one does not sleep


def test_an_idle_job_never_takes_the_migrate_lock_and_a_due_one_waits_for_it(rdb, monkeypatch):
    cluster, name = rdb
    env(monkeypatch, cluster, name)
    monkeypatch.setenv("PG_MIGRATE_LOCK_TIMEOUT_S", "1")
    with locked(cluster.url(name, "pipeshub_migrate")):                                     # a deployment holds the lock
        assert maintain._analyze_pass(maintain.migrate_url()) == []                           # nothing asked: no wait, no lock
        ask(cluster, name, ORG_A)
        assert maintain.main(["analyze"]) == 3                                                # a table is due: it waits for the lock and says so
    assert maintain.main(["analyze"]) == 0 and requests(cluster, name) == []


def test_the_job_analyzes_only_the_browse_tables_and_a_bad_interval_is_a_configuration_error(rdb, monkeypatch):
    cluster, name = rdb
    with pytest.raises(ValueError):
        kg_stats.analyze(None, ["kg.records; DROP TABLE kg.records"])
    env(monkeypatch, cluster, name, every="ten")
    assert maintain.main(["analyze"]) == 2
    assert maintain.main(["placement"]) == 2
