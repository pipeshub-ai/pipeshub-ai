"""What every KeyValueStore backend must answer against its real server.

``create_key(..., overwrite=False)`` is the claim that
``ConfigurationService.create_config_if_absent`` builds on: True means this
process owns the value, False means read back whoever does. The contract
suites in tests/unit pin the answers against fake inner stores; only a real
server can show whether two processes starting at once both get True.

A backend's module subclasses ``KvStoreContract`` and provides a module-scoped
``store`` fixture: the encrypted store production builds for its
``KV_STORE_TYPE``, whose ``.store`` is the plain store it delegates to.
"""
from __future__ import annotations

import asyncio
import uuid

import pytest


class KvStoreContract:
    @pytest.fixture
    def key(self) -> str:
        return f"/it/claims/{uuid.uuid4().hex}"

    async def test_claiming_an_absent_key_reports_true(self, store, key) -> None:
        assert await store.create_key(key, {"owner": "a"}, overwrite=False) is True
        assert await store.get_key(key) == {"owner": "a"}

    async def test_claiming_a_held_key_reports_false_and_keeps_the_value(self, store, key) -> None:
        await store.create_key(key, {"owner": "a"}, overwrite=False)

        assert await store.create_key(key, {"owner": "b"}, overwrite=False) is False
        assert await store.get_key(key) == {"owner": "a"}

    async def test_overwrite_replaces_the_value(self, store, key) -> None:
        await store.create_key(key, {"owner": "a"})

        assert await store.create_key(key, {"owner": "b"}, overwrite=True) is True
        assert await store.get_key(key) == {"owner": "b"}

    async def test_concurrent_claims_have_exactly_one_winner(self, store) -> None:
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

    async def test_deleting_reports_whether_the_key_was_there(self, store, key) -> None:
        """True when the key was deleted, False when it was not there."""
        await store.create_key(key, {"owner": "a"})

        assert await store.delete_key(key) is True
        assert await store.get_key(key) is None
        assert await store.delete_key(key) is False
