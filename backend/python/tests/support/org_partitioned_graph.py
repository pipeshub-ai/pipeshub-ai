"""In-memory graphs that read the org context the way the real providers do, for jobs that run org by org.

``PgLikeGraph`` reads the bound org as ``services/graph_db/postgres`` does: an unbound read raises, every read and write is the bound org's
partition, and the org list is every org only under ``system``. ``ArangoLikeGraph`` ignores the context and reads across orgs, as the Arango
and Neo4j providers do. Documents are stored with ``_key``; ``get_nodes_by_field_in`` hands them back with ``id``, as every provider does.
``get_org_apps(org)`` lists an org's partition, as its ``orgAppRelation`` edges would, except the ``unlinked`` keys (an App with no edge).
On ``ArangoLikeGraph`` one document stored under two orgs is one App linked to both.
"""
from __future__ import annotations

import logging
from typing import Any

from app.core import org_context
from app.core.org_context import MissingOrgContext, current_or_system, current_org_id

_graph_log = logging.getLogger("fake-graph")


def ambient_org() -> str | None:
    try:
        return current_org_id()
    except MissingOrgContext:
        return None


def _matches(doc: dict, filters: dict) -> bool:
    return all(doc.get(k) == v for k, v in filters.items())


def _shape(doc: dict, return_fields: list[str] | None) -> dict:
    out = {"id": doc["_key"], **{k: v for k, v in doc.items() if k != "_key"}}
    return {f: out.get(f) for f in return_fields} if return_fields else out


class FakeGraph:
    def __init__(self, orgs: list[dict], apps: dict[str, list[dict]], *, orgs_error: Exception | None = None,
                 failing_orgs: frozenset[str] = frozenset(), raising_orgs: frozenset[str] = frozenset(),
                 unlinked: frozenset[str] = frozenset()) -> None:
        self.orgs, self.orgs_error = orgs, orgs_error
        self.failing_orgs = failing_orgs        # a read error the provider logs and answers with [] (or raises, when asked to)
        self.raising_orgs = raising_orgs        # an unexpected exception out of the org's pass
        self.unlinked = unlinked                # App keys no orgAppRelation edge reaches
        self.apps = [(org, doc) for org, docs in apps.items() for doc in docs]       # (partition org, document)
        self.org_list_calls: list[dict] = []
        self.reads: list[str | None] = []                   # the org each queue read ran under
        self.listings: list[tuple[str | None, str]] = []    # (the org bound, the org listed) per get_org_apps
        self.updates: list[tuple[str | None, str, str, dict]] = []
        self.upserts: list[tuple[str | None, dict]] = []        # (the org bound at the write, payload)

    def _record_org_list(self, active: bool, is_external: bool, raise_on_error: bool) -> None:
        ctx = org_context._ctx.get()          # what the caller bound, whether or not this store reads it
        self.org_list_calls.append({"kind": ctx.kind if ctx else None, "active": active, "is_external": is_external,
                                    "raise_on_error": raise_on_error})

    def _read(self, org: str | None) -> None:
        self.reads.append(org)
        if org in self.raising_orgs:
            raise RuntimeError("unexpected")

    def _orgs(self, active: bool, is_external: bool) -> list[dict]:
        return [o for o in self.orgs if (not active or o.get("isActive")) and bool(o.get("isExternal")) == is_external]

    def _linked(self, org_id: str, active_only: bool, raise_on_error: bool) -> list[dict]:
        self.listings.append((ambient_org(), org_id))
        if org_id in self.failing_orgs:
            _graph_log.error("Get org apps failed")          # the Arango and Neo4j providers log, then raise when asked to
            if raise_on_error:
                raise RuntimeError("org apps down")
            return []
        return [dict(d) for o, d in self.apps
                if o == org_id and d["_key"] not in self.unlinked and (not active_only or d.get("isActive") is True)]

    def _merge(self, partition: str | None, payloads: list[dict]) -> None:
        for p in payloads:
            doc = next(d for o, d in self.apps if (partition is None or o == partition) and d["_key"] == p["id"])
            doc.update({k: v for k, v in p.items() if k != "id"})
            for k in [k for k, v in doc.items() if v is None]:
                del doc[k]


class PgLikeGraph(FakeGraph):
    """As ``PostgresGraphProvider``: every read is the bound org's partition; the org list is all orgs only under ``system``."""

    async def get_all_orgs(self, *, active: bool = True, is_external: bool = False, transaction: str | None = None,
                           raise_on_error: bool = False) -> list[dict]:
        ctx = current_or_system()          # nothing bound: MissingOrgContext, as identity.py
        self._record_org_list(active, is_external, raise_on_error)
        if self.orgs_error:
            raise self.orgs_error          # the system branch raises whatever raise_on_error says
        rows = self._orgs(active, is_external)
        return rows if ctx.kind == "system" else [o for o in rows if o["_key"] == ctx.org_id]

    async def get_nodes_by_filters(self, collection: str, filters: dict[str, Any], return_fields: list[str] | None = None,
                                   transaction: str | None = None, *, raise_on_error: bool = False) -> list[dict]:
        org = current_org_id()             # before the try, as postgres/core.py
        self._read(org)
        if org in self.failing_orgs:
            _graph_log.error("get_nodes_by_filters failed")
            return []
        return [dict(d) for o, d in self.apps if o == org and _matches(d, filters)]      # orgId is an ordinary document filter

    async def get_nodes_by_field_in(self, collection: str, field_name: str, field_values: list[Any],
                                    return_fields: list[str] | None = None, transaction: str | None = None, *,
                                    raise_on_error: bool = False) -> list[dict]:
        org = current_org_id()             # first statement, as postgres/core.py; no try there
        self._read(org)
        return [_shape(d, return_fields) for o, d in self.apps if o == org and d.get(field_name) in field_values]

    async def batch_upsert_nodes(self, nodes: list[dict], collection: str, transaction: str | None = None) -> bool:
        org = current_org_id()
        self.upserts.extend((org, dict(n)) for n in nodes)
        self._merge(org, nodes)
        return True

    async def get_document(self, key: str, collection: str, transaction: str | None = None) -> dict | None:
        org = current_org_id()
        return next((dict(d) for o, d in self.apps if o == org and d["_key"] == key), None)

    async def get_edges_to_node(self, node_id: str, edge_collection: str) -> list[dict]:
        current_org_id()
        return []

    async def update_node(self, key: str, collection: str, updates: dict, transaction: str | None = None) -> bool:
        self.updates.append((current_org_id(), key, collection, updates))
        return True

    async def get_org_apps(self, org_id: str, *, active_only: bool = True, app_type: str | None = None,
                           raise_on_error: bool = False) -> list[dict]:
        if org_id != current_org_id():     # identity.py's _mine: never another org's apps
            return []
        return self._linked(org_id, active_only, raise_on_error)


class ArangoLikeGraph(FakeGraph):
    """As the Arango and Neo4j providers: no context is read, a read spans every org, a failure is logged and answered empty."""

    async def get_all_orgs(self, *, active: bool = True, is_external: bool = False, transaction: str | None = None,
                           raise_on_error: bool = False) -> list[dict]:
        self._record_org_list(active, is_external, raise_on_error)
        if self.orgs_error:
            _graph_log.error(f"Get all orgs failed: {self.orgs_error}")
            if raise_on_error:
                raise self.orgs_error
            return []
        return self._orgs(active, is_external)

    async def get_nodes_by_filters(self, collection: str, filters: dict[str, Any], return_fields: list[str] | None = None,
                                   transaction: str | None = None, *, raise_on_error: bool = False) -> list[dict]:
        self._read(filters.get("orgId"))
        if filters.get("orgId") in self.failing_orgs:
            _graph_log.error("get_nodes_by_filters failed")
            return []
        return [dict(d) for _, d in self.apps if _matches(d, filters)]

    async def get_nodes_by_field_in(self, collection: str, field_name: str, field_values: list[Any],
                                    return_fields: list[str] | None = None, transaction: str | None = None, *,
                                    raise_on_error: bool = False) -> list[dict]:
        self._read(ambient_org())          # the store reads no context; raising_orgs names the pass that fails
        docs = {d["_key"]: d for _, d in self.apps}         # one document, however many orgs it is stored under
        return [_shape(d, return_fields) for d in docs.values() if d.get(field_name) in field_values]

    async def batch_upsert_nodes(self, nodes: list[dict], collection: str, transaction: str | None = None) -> bool:
        self.upserts.extend((ambient_org(), dict(n)) for n in nodes)
        self._merge(None, nodes)
        return True

    async def update_node(self, key: str, collection: str, updates: dict, transaction: str | None = None) -> bool:
        self.updates.append((ambient_org(), key, collection, updates))
        return True

    async def get_org_apps(self, org_id: str, *, active_only: bool = True, app_type: str | None = None,
                           raise_on_error: bool = False) -> list[dict]:
        return self._linked(org_id, active_only, raise_on_error)
