"""Which org the PostgreSQL key-value store binds: none for a key ``edition.kv_key_binds_org`` does not claim, the ambient org for
one it claims. The hook is set in each test, so the tests hold whatever a build's own hook answers."""

from __future__ import annotations

import contextlib
import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app import edition
from app.config.providers.postgres_store import PostgresKeyValueStore
from app.core import org_context
from app.core.db import DbConfig, KernelDB

ORG = "a" * 24


class _Tx:
    async def fetchval(self, sql: str, *args):  # noqa: ANN202
        return None if "kv.get" in sql else True

    async def fetch(self, sql: str, *args):  # noqa: ANN202
        return []

    async def execute(self, sql: str, *args):  # noqa: ANN202
        return None


class _Runtime:
    def __init__(self) -> None:
        self.units: list[tuple[bool, bool]] = []      # (write, org) per unit
        self.cfg = SimpleNamespace()

    async def db(self):  # noqa: ANN202
        units = self.units

        class _Db:
            @contextlib.asynccontextmanager
            async def runtime_uow(self, write: bool = False, org: bool = False):  # noqa: ANN202
                units.append((write, org))
                yield _Tx()

        return _Db()


async def _use_every_operation(store: PostgresKeyValueStore, key: str) -> None:
    await store.create_key(key, 1)
    await store.create_key(key, 1, overwrite=False)
    await store.update_value(key, 2)
    await store.get_key(key)
    await store.delete_key(key)
    await store.list_keys_in_directory(key)


def _store(rt: _Runtime) -> PostgresKeyValueStore:
    store = PostgresKeyValueStore(lambda v: json.dumps(v).encode(), json.loads, runtime=rt)
    store._last_purge = float("inf")      # no housekeeping unit in the count
    return store


@pytest.mark.asyncio
async def test_a_hook_that_claims_no_key_binds_no_org_in_any_unit() -> None:
    # the open-source default, set explicitly: a build's own hook may claim keys (its default is tested beside the other hooks)
    rt = _Runtime()
    with patch.object(edition, "kv_key_binds_org", lambda key: False):
        await _use_every_operation(_store(rt), f"{ORG}/services/aiModels")
        await _store(rt).get_all_keys()
    assert len(rt.units) == 7 and all(org is False for _w, org in rt.units)


@pytest.mark.asyncio
async def test_a_key_the_hook_claims_binds_the_ambient_org_and_others_do_not() -> None:
    rt = _Runtime()
    with patch.object(edition, "kv_key_binds_org", lambda key: key.startswith(f"{ORG}/")):
        await _use_every_operation(_store(rt), f"{ORG}/services/aiModels")
        claimed = list(rt.units)
        rt.units.clear()
        await _use_every_operation(_store(rt), "/services/aiModels")
    assert len(claimed) == 6 and all(org is True for _w, org in claimed)
    assert len(rt.units) == 6 and all(org is False for _w, org in rt.units)


def _kernel_db_recording(bound: list[str]) -> KernelDB:
    db = KernelDB(DbConfig(service="query", dsn="postgresql://x@h/db"))

    async def pool_here(*, jobs: bool) -> object:
        return object()

    @contextlib.asynccontextmanager
    async def fake_open(pool: object, *, org_id: str, **kw: object):  # noqa: ANN202
        bound.append(org_id)
        yield None

    db._pool_here = pool_here  # type: ignore[method-assign]
    db._open = fake_open  # type: ignore[method-assign]
    return db


@pytest.mark.asyncio
async def test_runtime_unit_binds_the_ambient_org_only_when_asked() -> None:
    bound: list[str] = []
    db = _kernel_db_recording(bound)
    with org_context.for_org(ORG, job="t"):
        async with db.runtime_uow():
            pass
        async with db.runtime_uow(org=True):
            pass
    assert bound == ["", ORG]


@pytest.mark.asyncio
async def test_runtime_unit_asked_for_an_org_without_one_refuses() -> None:
    bound: list[str] = []
    db = _kernel_db_recording(bound)
    with pytest.raises(org_context.MissingOrgContext):
        async with db.runtime_uow(org=True):
            pass
    with org_context.system("t"), pytest.raises(org_context.SystemContextNotAllowed):
        async with db.runtime_uow(org=True):
            pass
    assert bound == []
