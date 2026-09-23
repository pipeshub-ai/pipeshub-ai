"""The etcd KeyValueStore against a real etcd.

``create_key(..., overwrite=False)`` is the claim that
``ConfigurationService.create_config_if_absent`` builds on: True means this
process owns the value, False means read back whoever does. The contract suite
in tests/unit/config pins the answers against a fake inner store; only a real
server can show whether two processes starting at once both get True.

Needs Docker, and skips cleanly when etcd is not reachable:

  docker compose -f deployment/docker-compose/docker-compose.integration.kv.yml up -d --wait
  cd backend/python && pytest tests/integration/kv -m integration

Environment: ETCD_IT_URL (default: http://localhost:2389).
"""
from __future__ import annotations

import asyncio
import logging
import os
import uuid
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]

ETCD_URL = os.environ.get("ETCD_IT_URL", "http://localhost:2389")


@pytest.fixture(scope="module")
async def store() -> AsyncIterator:
    """The encrypted store production builds for KV_STORE_TYPE=etcd."""
    pytest.importorskip("etcd3", reason="etcd3 not installed")
    from app.config.providers.etcd.etcd3_encrypted_store import (
        Etcd3EncryptedKeyValueStore,
    )

    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("ETCD_URL", ETCD_URL)
        mp.setenv("SECRET_KEY", "etcd-it-secret")
        kv = Etcd3EncryptedKeyValueStore(logging.getLogger("etcd-it"))
    try:
        await asyncio.wait_for(kv.get_all_keys(), timeout=10.0)
    except Exception as exc:
        pytest.skip(f"etcd not available at {ETCD_URL} — {exc}")
    yield kv
    await kv.close()


@pytest.fixture
def key() -> str:
    return f"/it/claims/{uuid.uuid4().hex}"


async def test_claiming_an_absent_key_reports_true(store, key) -> None:
    assert await store.create_key(key, {"owner": "a"}, overwrite=False) is True
    assert await store.get_key(key) == {"owner": "a"}


async def test_claiming_a_held_key_reports_false_and_keeps_the_value(store, key) -> None:
    await store.create_key(key, {"owner": "a"}, overwrite=False)

    assert await store.create_key(key, {"owner": "b"}, overwrite=False) is False
    assert await store.get_key(key) == {"owner": "a"}


async def test_overwrite_replaces_the_value(store, key) -> None:
    await store.create_key(key, {"owner": "a"})

    assert await store.create_key(key, {"owner": "b"}, overwrite=True) is True
    assert await store.get_key(key) == {"owner": "b"}


async def test_concurrent_claims_have_exactly_one_winner(store) -> None:
    """Every node of a fresh deployment claims at once. With a read before the
    write, each saw the key absent and each adopted its own value.

    Runs on the plain store the encrypted one delegates to: the wrapper's
    read-back after writing hides most losers, but not all (two winners in
    about 2 of 100 rounds across four clients), so only the inner store makes
    the race show every time.
    """
    inner = store.store
    for _ in range(5):
        key = f"/it/race/{uuid.uuid4().hex}"
        claimants = [f"node-{i}" for i in range(8)]

        results = await asyncio.gather(
            *(inner.create_key(key, value, overwrite=False) for value in claimants)
        )

        assert results.count(True) == 1, results
        assert await inner.get_key(key) == claimants[results.index(True)]
