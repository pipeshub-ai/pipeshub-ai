"""Shims and an in-memory graph for driving the Folder connector.

The connector's module imports the real application container, which loads without a stub; no module of the process is replaced, so a test
collected later in the same run sees the real ``app.containers.connector``.
"""

# ruff: noqa: ANN201, ANN202, ANN204

from __future__ import annotations

import sys
import types
from pathlib import Path

# --- Import shims (must run before ``from app.connectors...connector import``) ---
try:
    # redis is a pinned dependency (pyproject: redis==5.2.1) and provides every
    # name stubbed below. Stub only when it is genuinely absent: installing the
    # stub over a working install shadows it, and the stub then has to keep pace
    # with every redis symbol the app imports -- which it did not, so importing
    # this module first in a collection run failed outright.
    import redis.asyncio.cluster  # noqa: F401
    from redis.exceptions import NoScriptError  # noqa: F401
except ImportError:
    _redis_exc = types.ModuleType("redis.exceptions")
    _redis_exc.ConnectionError = type("RedisConnectionError", (Exception,), {})
    _redis_exc.TimeoutError = type("RedisTimeoutError", (Exception,), {})
    # app.services.messaging.distributed_concurrency imports this name at module
    # scope; without it the stub shadows a perfectly good installed redis and
    # the module fails to import whenever nothing else pulled redis in first.
    _redis_exc.NoScriptError = type("RedisNoScriptError", (Exception,), {})
    sys.modules["redis.exceptions"] = _redis_exc

    _redis_backoff = types.ModuleType("redis.backoff")

    class _ExponentialBackoff:
        pass

    _redis_backoff.ExponentialBackoff = _ExponentialBackoff
    sys.modules["redis.backoff"] = _redis_backoff

    _redis_retry = types.ModuleType("redis.asyncio.retry")

    class _Retry:
        pass

    _redis_retry.Retry = _Retry
    sys.modules["redis.asyncio.retry"] = _redis_retry

    _redis_asyncio = types.ModuleType("redis.asyncio")
    _redis_asyncio.Redis = type("Redis", (), {})
    sys.modules["redis.asyncio"] = _redis_asyncio

    _redis = types.ModuleType("redis")
    _redis.asyncio = _redis_asyncio
    sys.modules["redis"] = _redis

if "etcd3" not in sys.modules:
    _etcd3 = types.ModuleType("etcd3")
    _etcd3.client = type("client", (), {})

from unittest.mock import AsyncMock, MagicMock

from app.connectors.core.registry.filters import FilterCollection  # noqa: F401
from app.connectors.sources.local_folder import connector as mod
from app.models.entities import User


class Graph:
    """Just enough of the entities processor: records by external id, deleted on request."""

    def __init__(self):
        self.records = {}
        self.groups = []
        self.perms = []
        self.deleted = []
        self.new_calls = []

    async def on_new_records(self, batch):
        self.new_calls.append([r.external_record_id for r, _ in batch])
        for rec, _ in batch:
            self.records[rec.external_record_id] = rec

    async def get_record_by_external_id(self, connector_id, ext):
        return self.records.get(ext)

    async def on_record_deleted(self, record_id):
        for k, r in list(self.records.items()):
            if r.id == record_id:
                del self.records[k]
                self.deleted.append(k)
        return False

    async def get_records_by_status(self, connector_id, statuses, limit=None, offset=0, **kw):
        return list(self.records.values())[offset : offset + limit]

    async def on_new_record_groups(self, groups):
        self.groups += [g for g, _ in groups]
        self.perms += [p for _, ps in groups for p in ps]


class FakeSyncPoint:
    def __init__(self):
        self.doc = {}

    async def read_sync_point(self, key):
        return dict(self.doc)

    async def update_sync_point(self, key, data, encrypt_fields=None):
        # The Neo4j store keeps each value as a node property: a collection inside a collection is refused there.
        for name, value in data.items():
            assert not (isinstance(value, (list, tuple)) and any(isinstance(v, (list, tuple, dict)) for v in value)), f"sync point {name} is not storable"
            assert not isinstance(value, dict), f"sync point {name} is not storable"
        self.doc.update(data)  # merge, like the real stores




class RootedConnector(mod.FolderConnector):
    """The directory sync on the root its test config names (the real subclass takes it from the server's own store)."""

    def _resolve_root(self, sync_cfg):
        return Path(sync_cfg["root"]).resolve()


def make_connector(root, sync=None):
    """A connector on ``root`` whose graph and sync point live in memory."""
    graph = Graph()
    graph.org_id = "org-1"
    user = MagicMock(spec=User)
    user.id, user.org_id, user.email, user.full_name, user.is_active = "u1", "org-1", "dev@example.com", "Dev", True
    graph.get_user_by_user_id = AsyncMock(return_value=user)
    graph.on_new_app_users = AsyncMock()
    graph.reindex_existing_records = AsyncMock()
    sync = {"root": str(root)} if sync is None else sync
    cfg = MagicMock()
    cfg.get_config = AsyncMock(side_effect=lambda path: {"sync": sync})
    c = RootedConnector(MagicMock(), graph, MagicMock(), cfg, "c1", "personal", "u1")
    c.record_sync_point = FakeSyncPoint()
    return c, graph, sync
