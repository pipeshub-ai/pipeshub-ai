"""Generic documents and edges."""
from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from app.config.constants.arangodb import CollectionNames
from app.config.constants.neo4j import parse_node_id
from app.core.org_context import current_or_system, current_org_id
from app.services.graph_db.conditions import check_conditions
from app.utils import error_class

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Collection, Sequence

    from app.core.db import Tx
    from app.services.graph_db.conditions import Cond

from app.services.graph_db.postgres.base import (
    _VALIDATOR,
    SCOPE_FIELDS,
    PgBase,
    _arango,
    _doc,
    _edge,
    _edge_scope,
    _ekey,
    _field,
    _hot,
    _j,
    _node,
    _sync_code_file_scope,
)


def _system_apps(collection: str) -> bool:
    """A system context reading the ``apps`` collection: the schedule sweep lists connector instances across orgs, through the vetted
    ``graph.system_app*`` functions. Any other collection still needs a single org."""
    return current_or_system().kind == "system" and collection == "apps"


# The vetted reads of graph_0003 (a lookup by record, by connector and name, or by code file path): one page after an id, ascending. Each takes its
# leading arguments from ``_hot``, then the filter keys and JSON values, whether a null filter also matches an absent key, the id to continue after
# and the page size.
_HOT_SQL = {
    "blocks_by_record": "/* pipeshub:graph.blocks_by_record */ SELECT node_id, doc FROM graph.blocks_by_record($1, $2::text[], $3::jsonb[], $4, $5, $6)",
    "blocks_by_name": "/* pipeshub:graph.blocks_by_name */ SELECT node_id, doc FROM graph.blocks_by_name($1, $2, $3::text[], $4::jsonb[], $5, $6, $7)",
    "code_files_by_path": "/* pipeshub:graph.code_files_by_path */ SELECT node_id, doc FROM graph.code_files_by_path($1, $2::text[], $3::jsonb[], $4, $5, $6)",
    "code_files_by_scope": "/* pipeshub:graph.code_files_by_scope */ SELECT node_id, doc FROM graph.code_files_by_scope($1, $2, $3::text[], $4::jsonb[], $5, $6, $7)",
    "code_files_by_repo": "/* pipeshub:graph.code_files_by_repo */ SELECT node_id, doc FROM graph.code_files_by_repo($1, $2, $3, $4::text[], $5::jsonb[], $6, $7, $8)",
    "record_groups_by_repo_key": "/* pipeshub:graph.record_groups_by_repo_key */ SELECT node_id, doc FROM graph.record_groups_by_repo_key($1, $2::text[], $3::jsonb[], $4, $5, $6)",
    "record_groups_by_parent": "/* pipeshub:graph.record_groups_by_parent */ SELECT node_id, doc FROM graph.record_groups_by_parent($1, $2, $3::text[], $4::jsonb[], $5, $6, $7)",
}
_HOT_PAGE = 1000


async def _hot_rows(tx: Tx, hot: tuple[str, tuple[str, ...]], keys: list[str], vals: list[str], after: str, limit: int, *, null_absent: bool) -> list:
    """One page of the vetted read: ``limit`` documents (at most ``_HOT_PAGE``) ascending by id after ``after``."""
    fn, lead = hot
    return await tx.fetch(_HOT_SQL[fn], *lead, keys, vals, null_absent, after, limit)


class CoreMixin(PgBase):
    """Generic documents and edges (collection-addressed), as the other modules use them."""

    # ---- generic documents -------------------------------------------------------------------------------------
    async def get_document(self, document_key: str, collection: str, transaction: str | None = None, *, raise_on_error: bool = False) -> dict | None:
        org = None if _system_apps(collection) else current_org_id()     # a missing org raises: it is not an empty graph
        try:
            if org is None:
                row = await self._system_read(lambda tx: tx.fetchrow("/* pipeshub:graph.system_app */ SELECT org_id, app_id, node_json FROM graph.system_app($1)", document_key))
                # the partition is the app's org: a legacy instance whose document has no orgId still names it, as in _system_app_pages
                return {**_arango(_doc(json.loads(row["node_json"])), collection), "orgId": row[0]} if row else None      # row[0]: the row's org_id
            r = await self._q(lambda tx: tx.fetchrow("/* pipeshub:graph.get_document */ SELECT doc FROM graph.nodes WHERE org_id = $1 AND coll = $2 AND id = $3",
                                                    org, collection, document_key), transaction)
            return _arango(_doc(r["doc"]), collection) if r else None
        except Exception as e:
            if raise_on_error:
                raise
            error_class.log_failure(self.logger, "graph.provider.failed", e, include_error_text=False, op="get_document")
            return None

    async def get_all_documents(self, collection: str, transaction: str | None = None) -> list[dict]:
        org = current_org_id()
        rows = await self._q(lambda tx: tx.fetch("/* pipeshub:graph.get_all_documents */ SELECT doc FROM graph.nodes WHERE org_id = $1 AND coll = $2", org, collection), transaction)
        return [_arango(_doc(r["doc"]), collection) for r in rows]

    async def get_documents_paginated(self, collection: str, skip: int = 0, limit: int = 50, filters: dict[str, Any] | None = None,
                                      sort_field: str | None = None, transaction: str | None = None, *, raise_on_error: bool = False,
                                      after_key: str | None = None, return_fields: list[str] | None = None) -> list[dict]:
        """A keyset or offset page; ``return_fields`` projects, and a lookup by an indexed key goes through the vetted read of ``_hot``."""
        key_sort = sort_field in (None, "_key", "id")
        if after_key is not None and not key_sort:
            raise ValueError("after_key continues an order by key; with another sort_field use skip and limit")
        if _system_apps(collection):
            return await self._system_apps_page(skip, limit, filters, after_key, return_fields)
        org = current_org_id()          # before the unit opens: a system scope reads no other collection
        keys, vals = [_field(k) for k in (filters or {})], [_j(v) for v in (filters or {}).values()]
        hot = _hot(collection, filters)
        if key_sort and hot and 0 < skip + limit <= _HOT_PAGE and len(after_key or "") <= 200:      # served by the vetted read; the skipped rows are dropped here
            rows = (await self._q(lambda tx: _hot_rows(tx, hot, keys, vals, after_key or "", skip + limit, null_absent=False), transaction))[skip:]
        elif key_sort:      # the primary-key order: the limit stops the scan
            rows = await self._q(lambda tx: tx.fetch("""/* pipeshub:graph.get_documents_paginated */ SELECT n.doc FROM graph.nodes n
                WHERE n.org_id = $1 AND n.coll = $2 AND ($3::text IS NULL OR n.id > $3)
                  AND NOT EXISTS (SELECT 1 FROM unnest($6::text[], $7::text[]) f(k, v) WHERE n.doc->f.k IS DISTINCT FROM f.v::jsonb)
                ORDER BY n.id OFFSET $4 LIMIT $5""", org, collection, after_key, skip, limit, keys, vals), transaction)
        else:
            rows = await self._q(lambda tx: tx.fetch("""/* pipeshub:graph.get_documents_paginated_by_field */ SELECT n.doc FROM graph.nodes n
                WHERE n.org_id = $1 AND n.coll = $2
                  AND NOT EXISTS (SELECT 1 FROM unnest($6::text[], $7::text[]) f(k, v) WHERE n.doc->f.k IS DISTINCT FROM f.v::jsonb)
                ORDER BY n.doc->>$3, n.id OFFSET $4 LIMIT $5""", org, collection, sort_field, skip, limit, keys, vals), transaction)
        docs = [_arango(_doc(r["doc"]), collection) for r in rows]
        return [{f: d.get(f) for f in return_fields} for d in docs] if return_fields else docs


    async def _system_apps_page(self, skip: int, limit: int, filters: dict[str, Any] | None, after_key: str | None, return_fields: list[str] | None) -> list[dict]:
        """The connector instances across orgs (vetted ``graph.system_apps``): in (id, org) order, optionally only the active ones. Nothing else is filterable.
        ``after_key`` continues after that id, whichever orgs hold it."""
        extra = set(filters or {}) - {"isActive"}
        if extra or (filters and filters.get("isActive") is not True):
            raise ValueError(f"a cross-org read of connector instances filters on isActive=true only, not on {sorted(extra) or 'isActive=false'}")
        if limit <= 0:
            return []
        docs: list[dict] = []
        async for page in self._system_app_pages(only_active=bool(filters), after_key=after_key or "", page_size=min(1000, skip + limit)):
            docs.extend(d for d in page if not (after_key and d["_key"] == after_key))
            if len(docs) >= skip + limit:
                break
        docs = docs[skip:skip + limit]
        return [{f: d.get(f) for f in return_fields} for d in docs] if return_fields else docs

    async def _system_app_pages(self, *, only_active: bool = False, after_key: str = "", after_org: str = "", page_size: int = 1000) -> AsyncIterator[list[dict]]:
        """Every connector instance across orgs after the (``after_key``, ``after_org``) row, in (id, org) order, ``page_size`` (1..1000) at a time to the end.
        The cursor is the last row's (id, org): an id alone would skip the other orgs' copies of an id that ends a page."""
        after, cur_org = after_key, after_org
        while True:
            rows = await self._system_read(lambda tx, a=after, o=cur_org: tx.fetch(
                "/* pipeshub:graph.system_apps */ SELECT org_id, app_id, node_json FROM graph.system_apps($1, $2, $3, $4)", only_active, a, o, page_size))
            if rows:
                # the partition is the app's org: a legacy instance whose document has no orgId still names it
                yield [{**_arango(_doc(json.loads(r["node_json"])), "apps"), "orgId": r[0]} for r in rows]      # r[0]: the row's org_id
            if len(rows) < page_size:
                return
            after, cur_org = rows[-1][1], rows[-1][0]      # (app_id, org_id) of the last row

    async def batch_upsert_nodes(self, nodes: list[dict], collection: str, transaction: str | None = None) -> bool | None:
        if not nodes:
            return True
        from app.services.graph_db.common.utils import (
            merge_same_id_record_docs,
            record_created_at_insert_only,
        )

        org = current_org_id()
        if collection == CollectionNames.RECORDS.value and record_created_at_insert_only():
            merged: dict[str, dict] = {}
            for n in nodes:
                d = _node(n, collection)
                merged[d["id"]] = merge_same_id_record_docs(merged[d["id"]], d) if d["id"] in merged else d
            docs = list(merged.values())      # a repeated id: one document, by the rule every store applies
        else:
            docs = list({d["id"]: d for d in (_node(n, collection) for n in nodes)}.values())     # a repeated id: the last one wins
        await self._validate_upserts(collection, docs, transaction)      # what is checked is what is written
        if collection == CollectionNames.RECORDS.value:
            # insert-only fields: the merge below must neither overwrite them nor, through the clears step, remove them
            await self._upsert_record_nodes(docs, transaction)
            return True
        # a field set to None is removed (``SET n += {x: null}``), so the nulls are cleared before the merge
        payload = _j([{"id": d["id"], "doc": d} for d in docs])
        clears = _j([{"id": d["id"], "nulls": nulls} for d in docs if (nulls := [k for k, v in d.items() if v is None])])

        async def go(tx: Tx) -> None:
            if clears != "[]":
                await tx.execute("""/* pipeshub:graph.batch_upsert_nodes_clear */ UPDATE graph.nodes n SET doc = n.doc - x.nulls
                    FROM jsonb_to_recordset($3::jsonb) AS x(id text, nulls text[]) WHERE n.org_id = $1 AND n.coll = $2 AND n.id = x.id""", org, collection, clears)
            await tx.execute("""/* pipeshub:graph.batch_upsert_nodes */ INSERT INTO graph.nodes (org_id, coll, id, doc)
                SELECT $1, $2, x.id, (SELECT coalesce(jsonb_object_agg(_nv.key, _nv.value), '{}'::jsonb) FROM jsonb_each(x.doc) AS _nv WHERE _nv.value <> 'null'::jsonb) FROM jsonb_to_recordset($3::jsonb) AS x(id text, doc jsonb)
                ON CONFLICT (org_id, coll, id) DO UPDATE SET doc = (SELECT coalesce(jsonb_object_agg(_nv.key, _nv.value), '{}'::jsonb) FROM jsonb_each(graph.nodes.doc || EXCLUDED.doc) AS _nv WHERE _nv.value <> 'null'::jsonb)""", org, collection, payload)
        await self._q(go, transaction, write=True)
        return True

    async def _upsert_record_nodes(self, docs: list[dict], transaction: str | None) -> None:
        """``batch_upsert_nodes`` for ``records``: the nulls of the other fields clear as before, but the insert-only
        fields and the trash state are left alone on an existing record (their nulls clear nothing)."""
        from app.services.graph_db.common.utils import (
            RECORD_UPDATE_DROPPED_FIELDS,
            record_created_at_insert_only,
        )
        from app.services.graph_db.postgres.records import _upsert_record_docs

        org = current_org_id()
        kept = set(RECORD_UPDATE_DROPPED_FIELDS) if record_created_at_insert_only() else {"incarnationId"}      # a null never clears the id, with either switch
        clears = _j([{"id": d["id"], "nulls": nulls} for d in docs if (nulls := [k for k, v in d.items() if v is None and k not in kept])])

        async def go(tx: Tx) -> None:
            if clears != "[]":
                await tx.execute("""/* pipeshub:graph.batch_upsert_nodes_clear */ UPDATE graph.nodes n SET doc = n.doc - x.nulls
                    FROM jsonb_to_recordset($3::jsonb) AS x(id text, nulls text[]) WHERE n.org_id = $1 AND n.coll = $2 AND n.id = x.id""", org, CollectionNames.RECORDS.value, clears)
            await _upsert_record_docs(tx, org, docs)
        await self._q(go, transaction, write=True)

    async def create_node_if_absent(self, key: str, collection: str, node: dict[str, Any], transaction: str | None = None) -> bool:
        """One insert that leaves an existing node alone; True iff this call created it (a racing creator that commits first makes this False)."""
        org = current_org_id()
        doc = _node(node, collection)
        doc["id"] = key
        _VALIDATOR.validate_node(collection, doc)
        r = await self._q(lambda tx: tx.fetchval("""/* pipeshub:graph.create_node_if_absent */ WITH i AS (INSERT INTO graph.nodes (org_id, coll, id, doc)
            VALUES ($1, $2, $3, (SELECT coalesce(jsonb_object_agg(_nv.key, _nv.value), '{}'::jsonb) FROM jsonb_each($4::jsonb) AS _nv WHERE _nv.value <> 'null'::jsonb)) ON CONFLICT (org_id, coll, id) DO NOTHING RETURNING 1) SELECT count(*) FROM i""",
                                                 org, collection, key, _j(doc)), transaction, write=True)
        return bool(r)

    async def get_existing_keys(self, collection: str, keys: list[str], transaction: str | None = None) -> set[str]:
        """The subset of ``keys`` present in ``collection``. A failed read raises: a delete is complete only when the store says none remain."""
        unique = [k for k in dict.fromkeys(keys) if isinstance(k, str) and k]
        if not unique:
            return set()
        rows = await self._q(lambda tx: tx.fetch("/* pipeshub:graph.get_existing_keys */ SELECT id FROM graph.nodes WHERE org_id = $1 AND coll = $2 AND id = ANY($3::text[])",
                                                 current_org_id(), collection, unique), transaction)
        return {r["id"] for r in rows}

    async def update_node(self, key: str, collection: str, node_updates: dict, transaction: str | None = None) -> bool:
        org = current_org_id()
        upd = {k: v for k, v in node_updates.items() if k not in ("_key", "_id", "id")}
        _VALIDATOR.validate_node_update(collection, upd)
        scoped = collection == CollectionNames.RECORDS.value and any(f in upd for f in SCOPE_FIELDS)

        async def go(tx: Tx) -> int:
            n = await tx.fetchval("""/* pipeshub:graph.update_node */ WITH u AS (UPDATE graph.nodes SET doc = (SELECT coalesce(jsonb_object_agg(_nv.key, _nv.value), '{}'::jsonb) FROM jsonb_each(doc || $4::jsonb) AS _nv WHERE _nv.value <> 'null'::jsonb)
                WHERE org_id = $1 AND coll = $2 AND id = $3 RETURNING 1) SELECT count(*) FROM u""", org, collection, key, _j(upd))
            if scoped and n:
                await _sync_code_file_scope(tx, org, [key])
            return n
        return bool(await self._q(go, transaction, write=True))

    async def store_time(self) -> float:
        return float(await self._q(lambda tx: tx.fetchval("/* pipeshub:graph.store_time */ SELECT extract(epoch FROM clock_timestamp())::float8"), None))

    async def increment_node_field(self, key: str, collection: str, field: str, transaction: str | None = None) -> int | None:
        """One UPDATE that adds one to the stored value under the row's lock (two callers get two different values); None when no node
        matches."""
        org = current_org_id()
        _VALIDATOR.validate_node_update(collection, {field: 1})
        r = await self._q(lambda tx: tx.fetchval("""/* pipeshub:graph.increment_node_field */ UPDATE graph.nodes
            SET doc = jsonb_set(doc, ARRAY[$4::text], to_jsonb(coalesce((doc->>$4)::bigint, 0) + 1))
            WHERE org_id = $1 AND coll = $2 AND id = $3 RETURNING (doc->>$4)::bigint""", org, collection, key, field), transaction, write=True)
        return None if r is None else int(r)

    async def batch_update_nodes(self, nodes: list[dict], collection: str, transaction: str | None = None) -> bool:
        """One statement, so a batch outside a transaction is all or nothing; a node that does not exist is left alone."""
        org = current_org_id()
        items = [{"id": k, "doc": {f: v for f, v in it.items() if f not in ("id", "_key", "_id")}} for it in nodes if (k := it.get("id") or it.get("_key"))]
        for it in items:
            _VALIDATOR.validate_node_update(collection, it["doc"])
        scoped = [it["id"] for it in items if any(f in it["doc"] for f in SCOPE_FIELDS)] if collection == CollectionNames.RECORDS.value else []

        async def go(tx: Tx) -> None:
            await tx.execute("""/* pipeshub:graph.batch_update_nodes */ UPDATE graph.nodes n SET doc = (SELECT coalesce(jsonb_object_agg(_nv.key, _nv.value), '{}'::jsonb) FROM jsonb_each(n.doc || x.doc) AS _nv WHERE _nv.value <> 'null'::jsonb)
                FROM jsonb_to_recordset($3::jsonb) AS x(id text, doc jsonb) WHERE n.org_id = $1 AND n.coll = $2 AND n.id = x.id""", org, collection, _j(items))
            await _sync_code_file_scope(tx, org, scoped)
        if items:
            await self._q(go, transaction, write=True)
        return True

    async def delete_nodes(self, keys: list[str], collection: str, transaction: str | None = None) -> bool:
        if not keys:
            return True
        if collection == "records":
            # A record goes with its type documents and every edge of them (``RecordsMixin``), as ``_delete_records_with_their_types`` on Neo4j.
            return await self._delete_records_with_their_types(keys, transaction) == len(set(keys))
        org = current_org_id()

        async def go(tx: Tx) -> int:
            await tx.execute("""/* pipeshub:graph.delete_edges_of */ DELETE FROM graph.edges WHERE org_id = $1
                AND ((from_coll = $2 AND from_id = ANY($3::text[])) OR (to_coll = $2 AND to_id = ANY($3::text[])))""", org, collection, keys)
            return await tx.fetchval("/* pipeshub:graph.delete_nodes */ WITH d AS (DELETE FROM graph.nodes WHERE org_id = $1 AND coll = $2 AND id = ANY($3::text[]) RETURNING 1) SELECT count(*) FROM d",
                                     org, collection, keys)
        n = await self._q(go, transaction, write=True)
        return n == len(set(keys))

    async def delete_node_if_match(self, key: str, collection: str, expected: dict, graph_name: str = "knowledgeGraph",
                                   transaction: str | None = None) -> bool:
        if not expected:
            raise ValueError("delete_node_if_match needs an expectation")
        if collection == CollectionNames.RECORDS.value:
            raise ValueError("records are deleted with their type documents: use delete_nodes_and_edges")
        org = current_org_id()

        async def go(tx: Tx) -> bool:
            # The row lock first, in a statement of its own (as update_node_fields_if_match): the expectation is an anti-join, which a deleter that
            # waited for the lock does not re-check against the row it then deletes. The edges go only when the node did.
            await tx.execute("/* pipeshub:graph.lock_node */ SELECT 1 FROM graph.nodes WHERE org_id = $1 AND coll = $2 AND id = $3 FOR UPDATE", org, collection, key)
            deleted = await tx.fetchval("""/* pipeshub:graph.delete_node_if_match */ WITH d AS (DELETE FROM graph.nodes n WHERE n.org_id = $1 AND n.coll = $2 AND n.id = $3
                AND NOT EXISTS (SELECT 1 FROM jsonb_each($4::jsonb) f WHERE n.doc->f.key IS DISTINCT FROM f.value AND NOT (f.value = 'null'::jsonb AND n.doc->f.key IS NULL))
                RETURNING 1) SELECT count(*) FROM d""", org, collection, key, _j(expected))
            if not deleted:
                return False
            await tx.execute("""/* pipeshub:graph.delete_edges_of */ DELETE FROM graph.edges WHERE org_id = $1
                AND ((from_coll = $2 AND from_id = ANY($3::text[])) OR (to_coll = $2 AND to_id = ANY($3::text[])))""", org, collection, [key])
            return True
        return await self._q(go, transaction, write=True)

    async def delete_nodes_and_edges(self, keys: list[str], collection: str, graph_name: str = "knowledgeGraph", transaction: str | None = None) -> None:
        await self.delete_nodes(keys, collection, transaction)

    async def get_nodes_by_filters(self, collection: str, filters: dict[str, Any], return_fields: list[str] | None = None,
                                   transaction: str | None = None, *, raise_on_error: bool = False) -> list[dict]:
        """A null filter matches an absent or null field; a lookup by an indexed key goes through the vetted read of ``_hot``."""
        org = current_org_id()
        keys, vals = list(filters), [_j(v) for v in filters.values()]
        hot = _hot(collection, filters)
        fields = [_field(k) for k in keys]
        try:
            async def read(tx: Tx, *, paged: bool) -> list:
                # Every page of the vetted read after the last id, so more matches than a page are still read by the key's index (the plain
                # statement reads every document of the collection in the org). Paged reads run in one repeatable-read unit: one snapshot, as
                # the one statement had. In a caller's transaction, and after an id the function refuses, the plain statement reads them all.
                rows: list = []
                after = ""
                while hot and len(after) <= 200:
                    page = await _hot_rows(tx, hot, fields, vals, after, _HOT_PAGE, null_absent=True)
                    rows += page
                    if len(page) < _HOT_PAGE:
                        return rows
                    if not paged:
                        break
                    after = page[-1]["node_id"]
                return await tx.fetch("""/* pipeshub:graph.get_nodes_by_filters */ SELECT n.doc FROM graph.nodes n WHERE n.org_id = $1 AND n.coll = $2
                    AND NOT EXISTS (SELECT 1 FROM unnest($3::text[], $4::text[]) f(k, v)
                        WHERE CASE WHEN f.v = 'null' THEN coalesce(n.doc->f.k, 'null'::jsonb) <> 'null'::jsonb ELSE n.doc->f.k IS DISTINCT FROM f.v::jsonb END)""",
                                      org, collection, fields, vals)
            if hot and not transaction:
                rows = await self._db.run(lambda tx: read(tx, paged=True), isolation="repeatable_read")
            else:
                rows = await self._q(lambda tx: read(tx, paged=False), transaction)
        except Exception as e:
            if raise_on_error:
                raise
            error_class.log_failure(self.logger, "graph.provider.failed", e, include_error_text=False, op="get_nodes_by_filters")
            return []
        return [self._shape(_doc(r["doc"]), collection, return_fields) for r in rows]


    async def get_nodes_by_field_in(self, collection: str, field_name: str, field_values: list[Any], return_fields: list[str] | None = None,
                                    transaction: str | None = None, *, raise_on_error: bool = False) -> list[dict]:
        """``field_name="_key"`` is the stored ``id``; ``"_id"`` is ``collection/id`` and names this collection only."""
        org = current_org_id()
        if field_name in ("_key", "id", "_id"):   # the primary key
            if field_name == "_id":
                keys = [v.split("/", 1)[1] for v in field_values if isinstance(v, str) and v.startswith(f"{collection}/")]
            else:
                keys = [str(v) for v in field_values if v is not None]
            rows = await self._q(lambda tx: tx.fetch("""/* pipeshub:graph.get_nodes_by_ids */ SELECT doc FROM graph.nodes n
                WHERE n.org_id = $1 AND n.coll = $2 AND n.id = ANY($3::text[])""", org, collection, keys), transaction)
        elif all(isinstance(v, str) for v in field_values):    # text compare, so an expression index on ``doc->>'field'`` serves it
            rows = await self._q(lambda tx: tx.fetch("""/* pipeshub:graph.get_nodes_by_text_field_in */ SELECT doc FROM graph.nodes n WHERE n.org_id = $1 AND n.coll = $2
                AND n.doc->>$3::text = ANY ($4::text[])""", org, collection, _field(field_name), list(field_values)), transaction)
        else:
            vals = [_j(v) for v in field_values]
            rows = await self._q(lambda tx: tx.fetch("""/* pipeshub:graph.get_nodes_by_field_in */ SELECT doc FROM graph.nodes n WHERE n.org_id = $1 AND n.coll = $2
                AND n.doc->$3::text = ANY (SELECT v::jsonb FROM unnest($4::text[]) v)""", org, collection, _field(field_name), vals), transaction)
        return [self._shape(_doc(r["doc"]), collection, return_fields) for r in rows]

    async def update_node_fields_if_match(self, key: str, collection: str, updates: dict, expected: dict, transaction: str | None = None, *,
                                          clock_at_most: float | None = None, unset: Sequence[str] = ()) -> bool:
        if not expected:
            raise ValueError("update_node_fields_if_match needs an expectation")
        org = current_org_id()
        upd = {k: v for k, v in updates.items() if k not in ("_key", "_id", "id")}      # as update_node; a None removes the field
        _VALIDATOR.validate_node_update(collection, upd)
        async def go(tx: Tx) -> int:
            # The row lock first, in a statement of its own (as update_node_fields_if): the expectation is an anti-join, which a writer that waited for
            # the lock does not re-check against the row it then updates, and the UPDATE reads its clock before it waits; either way a write would
            # apply on a row that no longer matches.
            await tx.execute("/* pipeshub:graph.lock_node */ SELECT 1 FROM graph.nodes WHERE org_id = $1 AND coll = $2 AND id = $3 FOR UPDATE", org, collection, key)
            return await tx.fetchval("""/* pipeshub:graph.update_node_fields_if_match */ WITH u AS (UPDATE graph.nodes n SET doc = (SELECT coalesce(jsonb_object_agg(_nv.key, _nv.value), '{}'::jsonb) FROM jsonb_each((n.doc || $4::jsonb) - $7::text[]) AS _nv WHERE _nv.value <> 'null'::jsonb)
            WHERE org_id = $1 AND coll = $2 AND id = $3 AND NOT EXISTS (SELECT 1 FROM jsonb_each($5::jsonb) f WHERE n.doc->f.key IS DISTINCT FROM f.value
                AND NOT (f.value = 'null'::jsonb AND n.doc->f.key IS NULL))
                AND ($6::float8 IS NULL OR extract(epoch FROM clock_timestamp()) <= $6::float8) RETURNING 1) SELECT count(*) FROM u""",
                                     org, collection, key, _j(upd), _j(expected), clock_at_most, list(unset))
        return bool(await self._q(go, transaction, write=True))

    async def update_node_fields_if(self, key: str, collection: str, updates: dict, conditions: Sequence[Cond], transaction: str | None = None, *,
                                    unset: Sequence[str] = ()) -> bool:
        """``update_node_fields_if_match`` with the operators of ``Cond``, each as ``conditions.holds`` reads it: an absent or null field passes
        every operator but ``eq``; ``null_or_le`` passes only a stored number (a boolean or a string is never "not newer")."""
        check_conditions(conditions)
        org = current_org_id()
        upd = {k: v for k, v in updates.items() if k not in ("_key", "_id", "id")}
        _VALIDATOR.validate_node_update(collection, upd)
        conds = _j([{"f": c.field, "op": c.op, "v": c.value} for c in conditions])

        async def go(tx: Tx) -> int:
            # The row lock first, in a statement of its own: the NOT EXISTS below is planned as an anti-join, which a writer that waited for the lock
            # does not re-check against the row it then updates, so two writers holding the same counter would both apply.
            await tx.execute("/* pipeshub:graph.lock_node */ SELECT 1 FROM graph.nodes WHERE org_id = $1 AND coll = $2 AND id = $3 FOR UPDATE", org, collection, key)
            return await tx.fetchval("""/* pipeshub:graph.update_node_fields_if */ WITH u AS (UPDATE graph.nodes n
            SET doc = (SELECT coalesce(jsonb_object_agg(_nv.key, _nv.value), '{}'::jsonb) FROM jsonb_each((n.doc || $4::jsonb) - $6::text[]) AS _nv WHERE _nv.value <> 'null'::jsonb)
            WHERE n.org_id = $1 AND n.coll = $2 AND n.id = $3 AND NOT EXISTS (SELECT 1 FROM jsonb_to_recordset($5::jsonb) AS c(f text, op text, v jsonb) WHERE NOT CASE
                WHEN coalesce(n.doc->c.f, 'null'::jsonb) = 'null'::jsonb THEN c.op NOT IN ('eq', 'one_of')
                WHEN c.op IN ('eq', 'null_or_eq') THEN n.doc->c.f = c.v
                WHEN c.op = 'one_of' THEN jsonb_typeof(n.doc->c.f) = 'string' AND c.v ? (n.doc->>c.f)
                WHEN c.op = 'null_or_le' THEN jsonb_typeof(n.doc->c.f) = 'number' AND (n.doc->c.f)::numeric <= c.v::numeric
                ELSE false END) RETURNING 1) SELECT count(*) FROM u""", org, collection, key, _j(upd), conds, list(unset))
        return bool(await self._q(go, transaction, write=True))

    async def set_record_properties(self, key: str, *, values: dict[str, Any], previous_names: Collection[str], controls: dict[str, Any],
                                    conditions: Sequence[Cond], transaction: str | None = None) -> bool:
        """The values in one ``recordProps`` sub-document, replaced whole as on ArangoDB, so ``previous_names`` has nothing left to remove."""
        return await self.update_node_fields_if(key, CollectionNames.RECORDS.value, {"recordProps": dict(values), **controls}, conditions, transaction)

    # ---- generic edges -----------------------------------------------------------------------------------------
    async def batch_create_edges(
        self, edges: list[dict], collection: str, transaction: str | None = None, *, count_created: bool = False
    ) -> bool:
        if count_created:  # this store does not report which edges a write created; the store's before/after comparison covers it
            raise NotImplementedError("batch_create_edges(count_created=True) is not supported on the PostgreSQL graph store")
        if not edges:
            return True
        org = current_org_id()
        rows: dict[tuple, dict] = {}
        for e in edges:  # one edge per pair (MERGE): the last of a duplicate wins
            fc, fi, tc, ti, props = _edge(e)
            _VALIDATOR.validate_edge(collection, props)
            rows[(fc, fi, tc, ti, _ekey(collection, props))] = {"fc": fc, "fi": fi, "tc": tc, "ti": ti, "k": _ekey(collection, props), "doc": props}
        payload = _j(list(rows.values()))

        async def go(tx: Tx) -> None:
            if collection == "permission":   # one grant per pair, the last type wins (as a MERGE does); the types written together stay together
                # Probe each pair of the batch through the edge key (an index lookup per pair), then delete what was found: a statement that
                # joins the batch to the table scans every permission edge of the org when there are no statistics.
                pairs = {}
                for r in rows.values():
                    pairs.setdefault((r["fc"], r["fi"], r["tc"], r["ti"]), []).append(r["k"])
                keys = list(pairs)
                stale = await tx.fetch("""/* pipeshub:graph.batch_create_edges_retype_probe */ SELECT f.fc, f.fi, f.tc, f.ti, e.ekey
                    FROM unnest($2::text[], $3::text[], $4::text[], $5::text[], $6::text[]) AS f(fc, fi, tc, ti, ks)
                    CROSS JOIN LATERAL (SELECT x.ekey FROM graph.edges x WHERE x.org_id = $1 AND x.coll = 'permission' AND x.from_id = f.fi AND x.from_coll = f.fc
                      AND x.to_id = f.ti AND x.to_coll = f.tc AND NOT (f.ks::jsonb ? x.ekey)) e""",
                                       org, [k[0] for k in keys], [k[1] for k in keys], [k[2] for k in keys], [k[3] for k in keys], [_j(pairs[k]) for k in keys])
                if stale:
                    await tx.execute("""/* pipeshub:graph.batch_create_edges_retype */ DELETE FROM graph.edges e USING unnest($2::text[], $3::text[], $4::text[], $5::text[], $6::text[]) AS d(fc, fi, tc, ti, k)
                        WHERE e.org_id = $1 AND e.coll = 'permission' AND e.from_id = d.fi AND e.from_coll = d.fc AND e.to_id = d.ti AND e.to_coll = d.tc AND e.ekey = d.k""",
                                     org, [r["fc"] for r in stale], [r["fi"] for r in stale], [r["tc"] for r in stale], [r["ti"] for r in stale], [r["ekey"] for r in stale])
            await tx.execute("""/* pipeshub:graph.batch_create_edges */ INSERT INTO graph.edges (org_id, coll, from_coll, from_id, to_coll, to_id, ekey, doc)
                SELECT $1, $2, x.fc, x.fi, x.tc, x.ti, x.k, (SELECT coalesce(jsonb_object_agg(_nv.key, _nv.value), '{}'::jsonb) FROM jsonb_each(x.doc) AS _nv WHERE _nv.value <> 'null'::jsonb)
                FROM jsonb_to_recordset($3::jsonb) AS x(fc text, fi text, tc text, ti text, k text, doc jsonb)
                ON CONFLICT (org_id, coll, from_coll, from_id, to_coll, to_id, ekey) DO UPDATE SET doc = EXCLUDED.doc, from_coll = EXCLUDED.from_coll, to_coll = EXCLUDED.to_coll""",
                             org, collection, payload)
        await self._q(go, transaction, write=True)
        return True

    async def get_edge(self, from_id: str, from_collection: str, to_id: str, to_collection: str, collection: str,
                       transaction: str | None = None, *, raise_on_error: bool = False) -> dict | None:
        """One edge between the pair. In ``nodeRelations`` that is the hierarchy edge (a link is a separate edge, as on Neo4j); ``recordLinks`` the links."""
        org = current_org_id()
        colls, kind = _edge_scope(collection, pair=True)
        r = await self._q(lambda tx: tx.fetchrow("""/* pipeshub:graph.get_edge */ SELECT doc FROM graph.edges e WHERE e.org_id = $1 AND e.coll = ANY($2::text[])
            AND e.from_coll = $3 AND e.from_id = $4 AND e.to_coll = $5 AND e.to_id = $6
            AND ($7::int = 0 OR ($7::int = 1 AND e.ekey = '') OR ($7::int = 2 AND (e.coll = 'recordLinks' OR e.ekey <> ''))) ORDER BY e.ekey LIMIT 1""",
                                                  org, colls, from_collection, from_id, to_collection, to_id, kind), transaction)
        if not r:
            return None
        return {**_doc(r["doc"]), "from_id": from_id, "from_collection": from_collection, "to_id": to_id, "to_collection": to_collection}

    # A node's edges: in ``nodeRelations`` the hierarchy rows and the links, in ``recordLinks`` the links only, wherever they are stored
    # (``_edge_scope``); both edge shapes are given.
    async def _node_edges(self, node_id: str, edge_collection: str, *, org: str, incoming: bool, transaction: str | None) -> list[dict]:
        coll, key = node_id.split("/", 1) if "/" in node_id else ("records", node_id)
        colls, kind = _edge_scope(edge_collection)
        if incoming:
            rows = await self._q(lambda tx: tx.fetch("""/* pipeshub:graph.get_edges_to_node */ SELECT e.from_coll AS fc, e.from_id AS fi, e.doc FROM graph.edges e
                WHERE e.org_id = $1 AND e.coll = ANY($2::text[]) AND e.to_coll = $3 AND e.to_id = $4 AND ($5::int = 0 OR ($5::int = 1 AND e.ekey = '') OR ($5::int = 2 AND (e.coll = 'recordLinks' OR e.ekey <> '')))""", org, colls, coll, key, kind), transaction)
        else:
            rows = await self._q(lambda tx: tx.fetch("""/* pipeshub:graph.get_edges_from_node */ SELECT e.to_coll AS fc, e.to_id AS fi, e.doc FROM graph.edges e
                WHERE e.org_id = $1 AND e.coll = ANY($2::text[]) AND e.from_coll = $3 AND e.from_id = $4 AND ($5::int = 0 OR ($5::int = 1 AND e.ekey = '') OR ($5::int = 2 AND (e.coll = 'recordLinks' OR e.ekey <> '')))""", org, colls, coll, key, kind), transaction)
        out = []
        for r in rows:
            far_ref = f"{r['fc']}/{r['fi']}"
            a, b = (far_ref, node_id) if incoming else (node_id, far_ref)
            ends = ({"from_id": r["fi"], "from_collection": r["fc"], "to_id": key, "to_collection": coll} if incoming
                    else {"from_id": key, "from_collection": coll, "to_id": r["fi"], "to_collection": r["fc"]})
            out.append({**_doc(r["doc"]), "_from": a, "_to": b, **ends})
        return out

    async def get_edges_to_node(self, node_id: str, edge_collection: str, transaction: str | None = None, *, raise_on_error: bool = False) -> list[dict]:
        org = current_org_id()
        try:
            return await self._node_edges(node_id, edge_collection, org=org, incoming=True, transaction=transaction)
        except Exception as e:
            if raise_on_error:
                raise
            error_class.log_failure(self.logger, "graph.provider.failed", e, include_error_text=False, op="get_edges_to_node")
            return []

    async def get_edges_from_node(self, node_id: str, edge_collection: str, transaction: str | None = None, *, raise_on_error: bool = False) -> list[dict]:
        org = current_org_id()
        try:
            return await self._node_edges(node_id, edge_collection, org=org, incoming=False, transaction=transaction)
        except Exception as e:
            if raise_on_error:
                raise
            error_class.log_failure(self.logger, "graph.provider.failed", e, include_error_text=False, op="get_edges_from_node")
            return []

    async def batch_delete_edges(self, edges: list[dict], collection: str, transaction: str | None = None) -> int:
        """Delete the edges between node pairs; returns how many went. In ``nodeRelations`` that is the hierarchy edge of the pair only (a link
        between them stays), as the relationship type of the Neo4j provider's."""
        pairs = []
        for e in edges:
            try:
                fc, fi, tc, ti, _ = _edge(e)
            except ValueError as err:  # the caller's edge, not our bug
                error_class.log_failure(self.logger, "graph.provider.step_failed", err, include_error_text=False, level=logging.WARNING,
                                        op="batch_delete_edges", step="parse_edge", collection=collection)
                continue
            pairs.append({"fc": fc, "fi": fi, "tc": tc, "ti": ti})
        if not pairs:
            return 0
        colls, kind = _edge_scope(collection, pair=True)
        return await self._q(lambda tx: tx.fetchval("""/* pipeshub:graph.batch_delete_edges */ WITH d AS (DELETE FROM graph.edges e USING
            jsonb_to_recordset($3::jsonb) x(fc text, fi text, tc text, ti text) WHERE e.org_id = $1 AND e.coll = ANY($2::text[]) AND e.from_coll = x.fc
            AND e.from_id = x.fi AND e.to_coll = x.tc AND e.to_id = x.ti AND ($4::int = 0 OR ($4::int = 1 AND e.ekey = '') OR ($4::int = 2 AND (e.coll = 'recordLinks' OR e.ekey <> '')))
            RETURNING 1) SELECT count(*) FROM d""", current_org_id(), colls, _j(pairs), kind), transaction, write=True)

    async def delete_edge(self, from_id: str, from_collection: str, to_id: str, to_collection: str, collection: str, transaction: str | None = None) -> bool:
        return await self.batch_delete_edges([{"from_id": from_id, "from_collection": from_collection, "to_id": to_id, "to_collection": to_collection}],
                                             collection, transaction) > 0

    async def delete_edges_from(self, from_id: str, from_collection: str, collection: str, transaction: str | None = None) -> int:
        org = current_org_id()
        colls, kind = _edge_scope(collection)
        return await self._q(lambda tx: tx.fetchval("""/* pipeshub:graph.delete_edges_from */ WITH d AS (DELETE FROM graph.edges e WHERE e.org_id = $1
            AND e.coll = ANY($2::text[]) AND e.from_coll = $3 AND e.from_id = $4 AND ($5::int = 0 OR ($5::int = 1 AND e.ekey = '') OR ($5::int = 2 AND (e.coll = 'recordLinks' OR e.ekey <> ''))) RETURNING 1) SELECT count(*) FROM d""",
                                                     org, colls, from_collection, from_id, kind), transaction, write=True)

    async def delete_all_edges_for_node(self, node_key: str, collection: str, transaction: str | None = None) -> int:
        """Every edge of ``collection`` that starts or ends at ``node_key`` (``coll/key``); 0 for a key with no collection. ``nodeRelations`` is the
        hierarchy edges only and ``recordLinks`` the links only (the node-wide reads and ``delete_edges_from`` / ``_to`` also take the links with ``nodeRelations``)."""
        coll, _, key = node_key.partition("/")
        if not key:
            self.logger.warning("Could not parse collection from node_key: %s", node_key)
            return 0
        org = current_org_id()
        colls, kind = _edge_scope(collection, pair=True)      # the relationship type of the collection, as Neo4j: nodeRelations is the hierarchy, recordLinks the links
        return await self._q(lambda tx: tx.fetchval("""/* pipeshub:graph.delete_all_edges_for_node */ WITH d AS (DELETE FROM graph.edges e WHERE e.org_id = $1
            AND e.coll = ANY($2::text[]) AND ((e.from_coll = $3 AND e.from_id = $4) OR (e.to_coll = $3 AND e.to_id = $4)) AND ($5::int = 0 OR ($5::int = 1 AND e.ekey = '') OR ($5::int = 2 AND (e.coll = 'recordLinks' OR e.ekey <> '')))
            RETURNING 1) SELECT count(*) FROM d""", org, colls, coll, key, kind), transaction, write=True)

    async def delete_inherit_permissions_relation_record_group(self, record_id: str, record_group_id: str, transaction: str | None = None) -> None:
        """The record's INHERIT_PERMISSIONS edge to the record group."""
        await self.delete_edge(record_id, CollectionNames.RECORDS.value, record_group_id, CollectionNames.RECORD_GROUPS.value,
                               CollectionNames.INHERIT_PERMISSIONS.value, transaction)

    async def delete_edges_to(self, to_id: str, to_collection: str, collection: str, transaction: str | None = None) -> int:
        org = current_org_id()
        colls, kind = _edge_scope(collection)
        return await self._q(lambda tx: tx.fetchval("""/* pipeshub:graph.delete_edges_to */ WITH d AS (DELETE FROM graph.edges e WHERE e.org_id = $1
            AND e.coll = ANY($2::text[]) AND e.to_coll = $3 AND e.to_id = $4 AND ($5::int = 0 OR ($5::int = 1 AND e.ekey = '') OR ($5::int = 2 AND (e.coll = 'recordLinks' OR e.ekey <> ''))) RETURNING 1) SELECT count(*) FROM d""",
                                                     org, colls, to_collection, to_id, kind), transaction, write=True)

    # ---- checks on documents and edges, and the update of an edge -------------------------------------------------------------------
    async def check_collection_has_document(self, collection_name: str, document_id: str, transaction: str | None = None) -> bool:
        org = current_org_id()
        try:
            return bool(await self._q(lambda tx: tx.fetchval("/* pipeshub:graph.check_collection_has_document */ SELECT EXISTS (SELECT 1 FROM graph.nodes WHERE org_id = $1 AND coll = $2 AND id = $3)",
                                                           org, collection_name, document_id), transaction))
        except Exception as e:
            error_class.log_failure(self.logger, "graph.provider.failed", e, include_error_text=False,
                                    op="check_collection_has_document")
            return False

    async def check_edge_exists(self, from_key: str, to_key: str, edge_collection: str, transaction: str | None = None) -> bool:
        """Whether an edge of the collection runs from the node with this key to the node with that one (the nodes are named by key alone, as on Neo4j)."""
        org = current_org_id()
        colls, kind = _edge_scope(edge_collection, pair=True)
        try:
            return bool(await self._q(lambda tx: tx.fetchval("""/* pipeshub:graph.check_edge_exists */ SELECT EXISTS (SELECT 1 FROM graph.edges e WHERE e.org_id = $1 AND e.coll = ANY($2::text[])
                AND e.to_id = $4 AND e.from_id = $3 AND ($5::int = 0 OR ($5::int = 1 AND e.ekey = '') OR ($5::int = 2 AND (e.coll = 'recordLinks' OR e.ekey <> ''))))""",
                                                           org, colls, from_key, to_key, kind), transaction))
        except Exception as e:
            error_class.log_failure(self.logger, "graph.provider.failed", e, include_error_text=False, op="check_edge_exists")
            return False

    async def update_edge(self, from_key: str, to_key: str, edge_updates: dict, collection: str, transaction: str | None = None) -> bool:
        """Merges ``edge_updates`` into the edges of the collection between two nodes named ``<collection>/<key>`` (a null removes a property); True when there was
        one. An edge whose key changes with the update (a grant's type, an authenticated-as link's connector) is re-keyed, and the change is the capture's like
        any other write of a grant. A failure raises."""
        (fc, fid), (tc, tid) = parse_node_id(from_key), parse_node_id(to_key)
        if not fc or not tc:
            raise ValueError(f"update_edge needs '<collection>/<key>' ids, got {from_key!r} and {to_key!r}")
        org = current_org_id()
        colls, kind = _edge_scope(collection, pair=True)
        _VALIDATOR.validate_edge_update(collection, edge_updates)      # before the first statement, as the other edge writers do

        async def go(tx: Tx) -> int:
            rows = await tx.fetch("""/* pipeshub:graph.update_edge_read */ SELECT e.coll, e.ekey, e.doc FROM graph.edges e WHERE e.org_id = $1 AND e.coll = ANY($2::text[])
                AND e.from_coll = $3 AND e.from_id = $4 AND e.to_coll = $5 AND e.to_id = $6
                AND ($7::int = 0 OR ($7::int = 1 AND e.ekey = '') OR ($7::int = 2 AND (e.coll = 'recordLinks' OR e.ekey <> ''))) ORDER BY e.coll, e.ekey FOR UPDATE""",
                                  org, colls, fc, fid, tc, tid, kind)
            docs = [{k: v for k, v in {**_doc(r["doc"]), **edge_updates}.items() if v is not None} for r in rows]
            if len(rows) > 1 and any(_ekey(r["coll"], d) != r["ekey"] for r, d in zip(rows, docs, strict=True)):
                raise ValueError(f"update_edge would change the key of {len(rows)} edges between {from_key} and {to_key}: name one of them")
            for r, doc in zip(rows, docs, strict=True):
                await tx.execute("""/* pipeshub:graph.update_edge */ UPDATE graph.edges SET doc = $7::jsonb, ekey = $8 WHERE org_id = $1 AND coll = $2 AND from_coll = $3 AND from_id = $4
                    AND to_coll = $5 AND to_id = $6 AND ekey = $9""", org, r["coll"], fc, fid, tc, tid, _j(doc), _ekey(r["coll"], doc), r["ekey"])
            return len(rows)
        return await self._q(go, transaction, write=True) > 0
