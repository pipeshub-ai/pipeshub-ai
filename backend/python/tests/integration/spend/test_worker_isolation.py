"""Parallel pytest-xdist workers must never share a Redis keyspace: each moves to its own logical database."""

import pytest

from tests.support.spend_world import worker_redis_url

URL = "redis://127.0.0.1:6379/0"


def test_a_plain_run_keeps_the_url(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PYTEST_XDIST_WORKER", raising=False)
    assert worker_redis_url(URL) == URL


@pytest.mark.parametrize(("worker", "db"), [("gw0", 0), ("gw1", 1), ("gw3", 3)])
def test_each_worker_gets_its_own_database(monkeypatch: pytest.MonkeyPatch, worker: str, db: int) -> None:
    monkeypatch.setenv("PYTEST_XDIST_WORKER", worker)
    assert worker_redis_url(URL) == (URL if db == 0 else f"redis://127.0.0.1:6379/{db}")


def test_the_base_database_is_kept_as_an_offset(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PYTEST_XDIST_WORKER", "gw2")
    assert worker_redis_url("redis://:pw@host:6380/5") == "redis://:pw@host:6380/7"


def test_more_workers_than_databases_fails_instead_of_sharing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PYTEST_XDIST_WORKER", "gw16")
    with pytest.raises(RuntimeError):
        worker_redis_url(URL)


@pytest.mark.parametrize("url", ["redis://127.0.0.1:6379/0?db=0", "redis://127.0.0.1:6379?db=2", "redis://127.0.0.1:6379/9?db=2&socket_timeout=3",
                                 "redis://127.0.0.1:6379/0?db=2&db=0", "redis://127.0.0.1:6379/0?db=&db=5", "unix:///tmp/redis.sock?db=0", "unix:///tmp/redis.sock"])
def test_a_db_query_parameter_cannot_undo_the_offset(monkeypatch: pytest.MonkeyPatch, url: str) -> None:
    """redis-py selects ``?db=`` over the path, so the database Redis will select is what must differ per worker."""
    from redis.asyncio.connection import parse_url

    monkeypatch.setenv("PYTEST_XDIST_WORKER", "gw0")
    base = parse_url(worker_redis_url(url)).get("db", 0)
    monkeypatch.setenv("PYTEST_XDIST_WORKER", "gw1")
    shifted = parse_url(worker_redis_url(url))
    assert shifted["db"] == base + 1
    assert parse_url(url).get("db", 0) == base


def test_a_unix_socket_url_keeps_its_socket_path(monkeypatch: pytest.MonkeyPatch) -> None:
    from redis.asyncio.connection import parse_url

    monkeypatch.setenv("PYTEST_XDIST_WORKER", "gw1")
    parsed = parse_url(worker_redis_url("unix:///tmp/redis.sock?db=0"))
    assert parsed["path"] == "/tmp/redis.sock"
    assert parsed["db"] == 1
