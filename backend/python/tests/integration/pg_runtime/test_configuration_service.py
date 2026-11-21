"""The configuration service on the PostgreSQL store: encrypted values at rest, and a change made through one service reaches
the cache of another (LISTEN / NOTIFY), which is what Redis Pub/Sub does for it today."""

import asyncio
import logging

from app.config.configuration_service import ConfigurationService
from app.config.providers.encrypted_store import EncryptedKeyValueStore


def make(monkeypatch, runtime) -> ConfigurationService:
    monkeypatch.setenv("KV_STORE_TYPE", "postgres")
    monkeypatch.setenv("PG_LISTEN_URL", runtime.cfg.dsn)
    log = logging.getLogger("cfg-test")
    return ConfigurationService(log, EncryptedKeyValueStore(log))


async def test_a_change_in_one_service_invalidates_the_cache_of_another(
    monkeypatch, runtime, uid
) -> None:
    a, b = make(monkeypatch, runtime), make(monkeypatch, runtime)
    key = f"/services/connectors/test{uid}/setting"
    try:
        await asyncio.sleep(1.0)  # both watch threads have their LISTEN in place
        assert await a.set_config(key, {"v": 1})
        assert await b.get_config(key, use_cache=True) == {"v": 1}  # now cached in b
        assert await a.set_config(key, {"v": 2})
        for _ in range(60):
            if await b.get_config(key, use_cache=True) == {"v": 2}:
                break
            await asyncio.sleep(0.1)
        assert await b.get_config(key, use_cache=True) == {"v": 2}
    finally:
        await a.close()
        await b.close()


async def test_values_are_encrypted_in_the_table(monkeypatch, runtime, uid) -> None:
    svc = make(monkeypatch, runtime)
    key = f"/services/connectors/test{uid}/secret"
    try:
        assert await svc.set_config(key, {"password": "hunter2"})
        db = await runtime.db()
        async with db.runtime_uow() as tx:
            raw = await tx.fetchval(
                "/* pipeshub:test.raw */ SELECT value FROM platform.kv_entries WHERE key = $1",
                key,
            )
        assert raw is not None and b"hunter2" not in bytes(raw)
        assert await svc.get_config(key) == {"password": "hunter2"}
    finally:
        await svc.close()


async def test_read_modify_write_from_two_services_loses_no_update(monkeypatch, runtime, uid) -> None:
    """Two processes append to one encrypted list at once; compare_and_set_config makes the loser re-read."""
    a, b = make(monkeypatch, runtime), make(monkeypatch, runtime)
    key = f"/services/connectors/test{uid}/sourceAccounts/00"     # a prefix the connectors service may write
    try:
        async def append(svc: ConfigurationService, item: str) -> None:
            for _ in range(50):
                current = await svc.get_config(key, use_cache=False, raise_on_error=True)
                if await svc.compare_and_set_config(key, current, [*(current or []), item]):
                    return
            raise AssertionError("never won the compare-and-set")

        await asyncio.gather(*(append(svc, f"{n}-{i}") for i in range(5) for n, svc in (("a", a), ("b", b))))
        assert sorted(await a.get_config(key, use_cache=False)) == sorted(f"{n}-{i}" for i in range(5) for n in "ab")
    finally:
        await a.close()
        await b.close()
