"""pipeshub_db_query_seconds{store,op} at the storage port boundary."""

import asyncio
import time
from typing import Never

import pytest

from app.telemetry.backend import PrometheusBackend
from app.telemetry.modules import db_metrics


@pytest.fixture
def hist(monkeypatch) -> PrometheusBackend:
    backend = PrometheusBackend()
    h = backend.histogram("t_db_seconds", "t", ["store", "op"], buckets=(0.001, 1.0))
    monkeypatch.setattr(db_metrics, "DB_QUERY_SECONDS", h)
    monkeypatch.setattr(db_metrics, "METRICS_BACKEND", backend)
    return backend


class Store:
    async def get_document(self, key) -> str:
        return key

    async def outer(self, key) -> str:
        return await self.get_document(key)  # nested port call

    async def boom(self) -> Never:
        raise RuntimeError("x")

    def sync_helper(self) -> int:
        return 1

    async def _private(self) -> int:
        return 1


def _count(backend, op) -> float:
    for line in backend.serialize().splitlines():
        if line.startswith("t_db_seconds_count") and f'op="{op}"' in line:
            return float(line.split()[-1])
    return 0.0


def test_histogram_increments_per_call_and_keeps_type(hist) -> None:
    s = db_metrics.instrument_store(Store(), "graph")
    assert type(s) is Store
    assert asyncio.run(s.get_document("k")) == "k"
    asyncio.run(s.get_document("k"))
    assert _count(hist, "get_document") == 2
    assert 'store="graph"' in hist.serialize()


def test_error_is_observed_and_propagates(hist) -> None:
    s = db_metrics.instrument_store(Store(), "kv")
    with pytest.raises(RuntimeError):
        asyncio.run(s.boom())
    assert _count(hist, "boom") == 1


def test_labels_bounded_by_method_names(hist) -> None:
    s = db_metrics.instrument_store(Store(), "graph")
    for i in range(500):  # arguments never become labels
        asyncio.run(s.get_document(f"key-{i}"))
    samples = {ln.split("{")[1].split("}")[0] for ln in hist.serialize().splitlines()
               if ln.startswith("t_db_seconds_count")}
    assert samples == {'op="get_document",store="graph"'}
    assert s.sync_helper() == 1  # sync and private methods untouched
    assert s._private.__name__ == "_private"
    assert "sync_helper" not in hist.serialize() and "_private" not in hist.serialize()


def test_nested_port_call_counted_once_as_outer(hist) -> None:
    s = db_metrics.instrument_store(Store(), "graph")
    asyncio.run(s.outer("k"))
    assert _count(hist, "outer") == 1
    assert _count(hist, "get_document") == 0


def test_noop_backend_returns_store_untouched(monkeypatch) -> None:
    from app.telemetry.backend import NoopBackend

    monkeypatch.setattr(db_metrics, "METRICS_BACKEND", NoopBackend())
    s = Store()
    assert db_metrics.instrument_store(s, "kv") is s
    assert "get_document" not in vars(s)


def test_overhead_relative_to_realistic_await(hist) -> None:
    class Fast:
        async def op(self) -> None:
            return None

    async def run(obj: Fast, n: int = 20000) -> float:
        t = time.perf_counter()
        for _ in range(n):
            await obj.op()
        return (time.perf_counter() - t) / n

    instrumented = db_metrics.instrument_store(Fast(), "kv")
    # Interleaved and the fastest of each kept: load on a shared runner then slows both
    # sides of a trial alike instead of only whichever ran last.
    base = inst = float("inf")
    for _ in range(5):
        base = min(base, asyncio.run(run(Fast())))
        inst = min(inst, asyncio.run(run(instrumented)))
    print(f"base {base * 1e6:.1f} us, instrumented {inst * 1e6:.1f} us")
    # The wrapper costs a few microseconds per call: under 2% of a 1 ms store call.
    assert inst - base < 2e-5


def test_lifecycle_ops_not_timed(hist) -> None:
    class S:
        async def connect(self) -> None: ...
        async def subscribe_changes(self) -> None: ...
        async def get_key(self) -> None: ...

    s = db_metrics.instrument_store(S(), "kv")
    for f in (s.connect, s.subscribe_changes, s.get_key):
        asyncio.run(f())
    assert _count(hist, "get_key") == 1
    assert _count(hist, "connect") == 0 and _count(hist, "subscribe_changes") == 0


class _Fake:
    async def search(self) -> None: ...


@pytest.mark.parametrize("on", [True, False])
def test_factories_instrument_when_telemetry_on(monkeypatch, hist, on) -> None:
    import logging

    from app.config.constants.store_type import StoreType
    from app.config.key_value_store_factory import KeyValueStoreFactory, StoreConfig
    from app.services.graph_db.graph_db_provider_factory import GraphDBProviderFactory
    from app.services.vector_db.vector_db_provider_factory import (
        VectorDBProviderFactory,
    )

    if not on:
        from app.telemetry.backend import NoopBackend

        monkeypatch.setattr(db_metrics, "METRICS_BACKEND", NoopBackend())
    log = logging.getLogger("t")
    fake = _Fake()

    async def make(*_a, **_k) -> _Fake:
        return fake

    monkeypatch.setenv("DATA_STORE", "neo4j")
    monkeypatch.setenv("VECTOR_DB_TYPE", "qdrant")
    monkeypatch.setattr(GraphDBProviderFactory, "_create_neo4j_provider", staticmethod(make))
    monkeypatch.setattr(VectorDBProviderFactory, "_create_qdrant", staticmethod(make))

    class KV:
        async def get_key(self, key: str) -> None: ...

    monkeypatch.setattr(KeyValueStoreFactory, "_create_in_memory_store", staticmethod(KV))
    kv = KeyValueStoreFactory.create_store(StoreType.IN_MEMORY, config=StoreConfig(host="h", port=1))
    assert ("get_key" in vars(kv)) is on
    graph = asyncio.run(GraphDBProviderFactory.create_provider(log, None))
    assert ("search" in vars(graph)) is on
    vec = asyncio.run(VectorDBProviderFactory.create_provider(log, None))
    assert ("search" in vars(vec)) is on
