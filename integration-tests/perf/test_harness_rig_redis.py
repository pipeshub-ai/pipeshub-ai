"""The latency rig on a Redis it does not run (``--redis-port``) clears only the DB it owns (redisiso).

A FLUSHALL there wiped another stack's config on the host's shared Redis. Run with the backend venv:
``backend/python/venv/bin/python -m pytest integration-tests/perf/test_harness_rig_redis.py``.
"""

from __future__ import annotations

from pathlib import Path

import fakeredis
import pytest
import redis

import harness_rig
from harness_rig import Infra, Rig


@pytest.fixture
def server(monkeypatch: pytest.MonkeyPatch) -> fakeredis.FakeServer:
    shared = fakeredis.FakeServer()
    monkeypatch.setattr(redis, "Redis", lambda *a, **k: fakeredis.FakeRedis(*a, server=shared, **k))
    return shared


def _rig(tmp_path: Path, db: int) -> Rig:
    return Rig(Infra(mongo_uri="mongodb://unused", redis_port=6379, redis_db=db), tmp_path, {})


def test_the_rig_leaves_other_dbs_alone_and_clears_its_own_on_down(server: fakeredis.FakeServer, tmp_path: Path) -> None:
    other = fakeredis.FakeRedis(server=server, db=15)
    other.set("pxh-human-other:kv:/services/aiModels", "theirs")
    rig = _rig(tmp_path, db=9)
    rig._claim_redis()
    mine = fakeredis.FakeRedis(server=server, db=9)
    mine.set("record-events", "the rig's")
    rig._release_redis()
    assert other.get("pxh-human-other:kv:/services/aiModels") == b"theirs"
    assert mine.dbsize() == 0
    assert rig.env()["REDIS_DB"] == "9"


def test_the_rig_refuses_a_db_that_already_holds_keys(server: fakeredis.FakeServer, tmp_path: Path) -> None:
    taken = fakeredis.FakeRedis(server=server, db=0)
    taken.set("pipeshub:kv:/services/aiModels", "someone else's")
    with pytest.raises(RuntimeError, match="holds 1 keys"):
        _rig(tmp_path, db=0)._claim_redis()
    assert taken.get("pipeshub:kv:/services/aiModels") == b"someone else's"
    # A rig that never claimed the DB never clears it.
    _rig(tmp_path, db=0)._release_redis()
    assert taken.dbsize() == 1


def test_the_managed_rig_flushes_only_its_own_compose_redis(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(harness_rig.stack, "compose", lambda *a, **k: calls.append(a))
    rig = Rig(Infra(), tmp_path, {})
    rig._claim_redis()
    assert calls == [("exec", "-T", "redis", "redis-cli", "FLUSHALL")]
