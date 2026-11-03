"""Helpers for the code-graph tests: a provider bound to one org, and stand-ins for methods that live in other modules."""
from __future__ import annotations

import secrets
from typing import Any

from app.core.org_context import OrgContext, bind
from app.services.graph_db.postgres.provider import PostgresGraphProvider, unimplemented


def new_org() -> str:
    return secrets.token_hex(12)


class OrgBound:
    """Every async call of the provider runs in the ambient org, as a request would (a fixture's context does not reach the test)."""

    def __init__(self, provider: Any, org: str, user: str | None = None) -> None:  # noqa: ANN401
        self._p, self.org, self._user = provider, org, user

    def __getattr__(self, name: str) -> Any:  # noqa: ANN401
        attr = getattr(self._p, name)
        if not callable(attr):
            return attr

        async def call(*a: Any, **k: Any) -> Any:  # noqa: ANN401
            with bind(OrgContext(self.org, self._user, "job", "job:test", "t")):
                return await attr(*a, **k)
        return call


def _stand_in(name: str):  # noqa: ANN202
    """Minimal versions of methods that live in other modules, only while those are stubs; the real ones replace them once implemented."""
    from app.core.org_context import current_org_id

    if name == "upsert_sync_point":
        async def upsert_sync_point(self, sync_point_key, sync_point_data, collection, transaction=None):  # noqa: ANN202
            from app.utils.time_conversion import get_epoch_timestamp_in_ms
            now = get_epoch_timestamp_in_ms()
            data = {**sync_point_data, "syncPointKey": sync_point_key, "updatedAtTimestamp": now}
            await self._q(lambda tx: tx.execute("""INSERT INTO graph.nodes (org_id, coll, id, doc) VALUES ($1, $2, $3, $4::jsonb || jsonb_build_object('createdAtTimestamp', $5::bigint))
                ON CONFLICT (org_id, coll, id) DO UPDATE SET doc = graph.nodes.doc || $4::jsonb""",
                                                  current_org_id(), collection, sync_point_key, __import__("json").dumps(data), now), transaction, write=True)
            return True
        return upsert_sync_point
    if name == "get_sync_point":
        async def get_sync_point(self, key, collection, transaction=None, *, raise_on_error=False):  # noqa: ANN202
            r = await self._q(lambda tx: tx.fetchrow("SELECT doc FROM graph.nodes WHERE org_id = $1 AND coll = $2 AND doc->>'syncPointKey' = $3",
                                                     current_org_id(), collection, key), transaction)
            return dict(__import__("json").loads(r["doc"])) if r else None
        return get_sync_point
    if name == "remove_sync_point":
        async def remove_sync_point(self, key, collection, transaction=None):  # noqa: ANN202
            await self._q(lambda tx: tx.execute("DELETE FROM graph.nodes WHERE org_id = $1 AND coll = $2 AND doc->>'syncPointKey' = $3",
                                                current_org_id(), collection, key), transaction, write=True)
        return remove_sync_point
    if name == "get_virtual_record_ids_for_record_ids":
        async def get_virtual_record_ids_for_record_ids(self, record_ids, transaction=None):  # noqa: ANN202
            rows = await self._q(lambda tx: tx.fetch("SELECT id, doc->>'virtualRecordId' v FROM graph.nodes WHERE org_id = $1 AND coll = 'records' AND id = ANY($2::text[])",
                                                     current_org_id(), list(record_ids)), transaction)
            return {r["id"]: r["v"] for r in rows if r["v"]}
        return get_virtual_record_ids_for_record_ids
    raise KeyError(name)


class StandInProvider(PostgresGraphProvider):
    pass


for _n in ("upsert_sync_point", "get_sync_point", "remove_sync_point", "get_virtual_record_ids_for_record_ids"):
    if _n in unimplemented():
        setattr(StandInProvider, _n, _stand_in(_n))
