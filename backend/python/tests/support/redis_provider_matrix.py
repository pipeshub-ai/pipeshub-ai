"""Shared list of Redis transports the `test_*_contract.py` suites under
`tests/unit/services/redis/` parametrise over (T4).

Each of the four existing contract suites (`test_streams_contract.py`,
`test_kv_store_contract.py`, `test_lease_manager_contract.py`,
`test_cache_contract.py`) plus the new `test_retry_tracker_contract.py`
previously defined its own local `PROVIDERS`/`TRANSPORTS` list with the same
two entries (``standalone``, ``cluster``). Centralising them here is what
makes the promise in the original design doc true: an EE `conftest.py` that
runs before these modules are collected can call `register_provider()` /
`register_client_transport()` to append a `memorydb` entry, and every one of
these suites then exercises it with zero changes to the OSS test files.

Two flavours, because the existing suites split along that line:

- `PROVIDERS` -- `IRedisConnectionProvider`-level factories, for suites that
  exercise something built on the provider abstraction directly (streams,
  KV store).
- `CLIENT_TRANSPORTS` -- raw-client-level factories (`FakeRedis` /
  `FakeClusterRedis`), for suites that construct their subject with an
  injected client (lease manager, accessible-records cache).

Both lists hold ``pytest.param(factory, id=...)`` entries so a suite only
has to write ``@pytest.mark.parametrize("make_provider", PROVIDERS)`` (or
``make_client``, for the client-level list).
"""
from __future__ import annotations

from typing import Callable

import fakeredis.aioredis
import pytest

from tests.support.fake_cluster_redis import FakeClusterRedis
from tests.support.fake_redis_connection_provider import FakeRedisConnectionProvider

__all__ = [
    "PROVIDERS",
    "CLIENT_TRANSPORTS",
    "register_provider",
    "register_client_transport",
    "reset_redis_provider_matrix_for_tests",
]


def _make_standalone_provider() -> FakeRedisConnectionProvider:
    return FakeRedisConnectionProvider(is_cluster=False)


def _make_cluster_provider() -> FakeRedisConnectionProvider:
    return FakeRedisConnectionProvider(is_cluster=True)


def _make_standalone_client() -> "fakeredis.aioredis.FakeRedis":
    return fakeredis.aioredis.FakeRedis(decode_responses=True)


def _make_cluster_client() -> FakeClusterRedis:
    return FakeClusterRedis()


def _default_providers() -> list:
    return [
        pytest.param(_make_standalone_provider, id="standalone"),
        pytest.param(_make_cluster_provider, id="cluster"),
    ]


def _default_client_transports() -> list:
    return [
        pytest.param(_make_standalone_client, id="standalone"),
        pytest.param(_make_cluster_client, id="cluster"),
    ]


PROVIDERS: list = _default_providers()
CLIENT_TRANSPORTS: list = _default_client_transports()


def register_provider(make_provider: Callable[[], object], *, id: str) -> None:  # noqa: A002
    """Append a provider-level entry (T4). Must run before the OSS
    `test_*_contract.py` modules that read `PROVIDERS` are imported/collected
    -- e.g. from a `conftest.py` pytest loads before those modules.
    """
    PROVIDERS.append(pytest.param(make_provider, id=id))


def register_client_transport(make_client: Callable[[], object], *, id: str) -> None:  # noqa: A002
    """Append a client-level entry (T4). Same collection-order requirement
    as `register_provider`.
    """
    CLIENT_TRANSPORTS.append(pytest.param(make_client, id=id))


def reset_redis_provider_matrix_for_tests() -> None:
    """Test-only: drop entries appended beyond the OSS standalone/cluster pair."""
    PROVIDERS[:] = _default_providers()
    CLIENT_TRANSPORTS[:] = _default_client_transports()
