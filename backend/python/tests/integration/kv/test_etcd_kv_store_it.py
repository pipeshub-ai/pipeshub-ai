"""The etcd KeyValueStore against a real etcd (the cases: ``kv_contract.py``).

Needs Docker, and skips when ETCD_IT_URL is unset:

  docker compose -f deployment/docker-compose/docker-compose.integration.kv.yml up -d --wait etcd-kv-it
  cd backend/python && ETCD_IT_URL=http://localhost:2389 \
    pytest tests/integration/kv/test_etcd_kv_store_it.py -m integration

Environment: ETCD_IT_URL (required; the compose file publishes etcd on http://localhost:2389).
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

from tests.integration.kv.kv_contract import KvStoreContract
from tests.support import it_backends

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]
# One key for the store and the service over it, strong enough for the hardened profile tests/conftest.py sets.
SECRET = "c79dbe7e3afddf1bf57ddbdbbeb5bd49f09979d108f4f77e9e5038d2836be81d"


@pytest.fixture(scope="module")
async def store() -> AsyncIterator:
    """The encrypted store production builds for KV_STORE_TYPE=etcd."""
    etcd_url = it_backends.endpoint("ETCD_IT_URL")
    pytest.importorskip("etcd3", reason="etcd3 not installed")
    from app.config.providers.etcd.etcd3_encrypted_store import (
        Etcd3EncryptedKeyValueStore,
    )

    with pytest.MonkeyPatch.context() as mp:
        mp.setenv("ETCD_URL", etcd_url)
        mp.setenv("SECRET_KEY", SECRET)
        kv = Etcd3EncryptedKeyValueStore(logging.getLogger("etcd-it"))
    try:
        await asyncio.wait_for(kv.get_all_keys(), timeout=10.0)
    except Exception as exc:
        pytest.skip(f"etcd not available at {etcd_url} — {exc}")
    yield kv
    await kv.close()


class TestEtcdKvStore(KvStoreContract):
    async def test_delete_config_of_an_absent_key_reports_false(self, store, key) -> None:
        """Callers branch on this answer: False means there was nothing to delete, as on Redis and PostgreSQL."""
        from app.config.configuration_service import ConfigurationService

        # No watch thread: it would subscribe on the module-scoped store from a loop of its own.
        with pytest.MonkeyPatch.context() as mp, patch.object(ConfigurationService, "_start_watch"):
            mp.setenv("SECRET_KEY", SECRET)
            svc = ConfigurationService(logging.getLogger("etcd-it-svc"), store)
        await store.create_key(key, {"owner": "a"})

        assert await svc.delete_config(key) is True
        assert await svc.delete_config(key) is False
