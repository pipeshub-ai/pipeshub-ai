"""The store's clock on a real database: `store_time` is the database's time, and a write's lease written by it is not recovered by a sweeper whose
host clock is far ahead.

Needs a migrated database: PG_CORE_URL (a `pipeshub_connectors_login` DSN), PG_POOLER=none.
"""
from __future__ import annotations

import time
import uuid
from types import SimpleNamespace

import pytest

from app.config.constants.arangodb import CollectionNames
from app.services.permissions import structure_generation as sg
from app.services.permissions.structure_generation import (
    OPEN_WRITES_KEY,
    close_write,
    open_write,
    parse_open_writes,
    recover_expired_writes,
    unclosed_writes,
)

pytestmark = pytest.mark.integration


async def test_the_store_clock_is_the_databases_and_a_skewed_sweeper_leaves_a_live_write_alone(pg_provider, org_ctx, monkeypatch) -> None:
    with org_ctx(uuid.uuid4().hex[:24]):
        assert abs(await pg_provider.store_time() - time.time()) < 30
        assert await pg_provider.batch_upsert_nodes([{"_key": "c1", "name": "app"}], CollectionNames.APPS.value)
        token = await open_write(pg_provider, "c1", ["r1"])
        app = await pg_provider.get_document("c1", CollectionNames.APPS.value)
        assert abs(parse_open_writes(app[OPEN_WRITES_KEY])[token]["u"] - await pg_provider.store_time() - sg.OPEN_WRITE_SECONDS) < 30
        monkeypatch.setattr(sg, "time", SimpleNamespace(time=lambda: time.time() + 3600))      # a sweeper host an hour ahead
        assert await recover_expired_writes(pg_provider, "c1") == 0
        assert unclosed_writes(await pg_provider.get_document("c1", CollectionNames.APPS.value)) == 1
        assert await close_write(pg_provider, "c1", token)
