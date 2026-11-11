"""Named entities, their value mentions and persist-retry markers: the writes and the sweep.

The answers are those of ``Neo4jProvider`` (``app.modules.named_entities.graph_ops``). A node is a ``graph.nodes`` row of ``namedEntities``,
``valueMentions`` or ``namedEntityPersistRetries`` in the ambient org, a mention an edge ``mentionsEntity`` from a record. An org argument is only
ever the ambient org: every statement also matches it on the document's ``orgId``, so any other org reads and changes nothing. There is no foreign
key between an edge and a node, so a mention can outlive its entity; ``delete_dangling_named_entity_mentions`` removes it.
Every statement is a literal with bound parameters.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from app.config.constants.arangodb import CollectionNames
from app.core.org_context import OrgMismatch, current_org_id
from app.modules.named_entities.graph_ops import (
    PERSIST_RETRY_BASE_MS,
    PERSIST_RETRY_MAX_MS,
)
from app.modules.named_entities.keys import value_mention_key
from app.services.graph_db.postgres.base import (
    _VALIDATOR,
    MENTIONS_BOUND,
    PgBase,
    _doc,
    _j,
    _node,
)
from app.services.graph_db.taxonomy import MAX_MERGE_REDIRECT_HOPS
from app.utils.time_conversion import get_epoch_timestamp_in_ms

if TYPE_CHECKING:
    from app.core.db import Tx

_NE = CollectionNames.NAMED_ENTITIES.value
_VALUES = CollectionNames.VALUE_MENTIONS.value
_RETRIES = CollectionNames.NAMED_ENTITY_PERSIST_RETRIES.value
_ALIAS_CAP = 20
_ORG = "orgId"


def _own(docs: list[dict], org: str) -> None:
    """A document of another org is not written into this one."""
    if any(d.get(_ORG) != org for d in docs):
        raise OrgMismatch("a document names an org other than the ambient org")


def _marker(org: str, org_id: str, record_id: str, **fields: object) -> dict:
    """A persist-retry marker for validation; the statements set the same fields."""
    now = get_epoch_timestamp_in_ms()
    marker = {"orgId": org_id, "recordId": record_id, "attempts": 0, "dueAt": now, "createdAtTimestamp": now, "updatedAtTimestamp": now, **fields}
    if org_id != org:      # a marker of another org would never be worked: the sweep runs it in the org it names
        raise OrgMismatch("the org argument differs from the ambient org")
    return marker


class EntityMixin(PgBase):
    def is_named_entity_write_retryable(self, error: BaseException) -> bool:
        return self.is_write_conflict(error)

    # ---- nodes -------------------------------------------------------------------------------------------------
    async def create_named_entities_if_absent(self, nodes: list[dict]) -> None:
        """Create the nodes that are absent and leave the others as they are, except that an orphan mark is taken off (a writer about to link a
        node takes it back, and the sweep's delete re-checks the mark under the row lock). The whole batch is checked first."""
        org = current_org_id()
        if not nodes:
            return
        docs = [_node(n, _NE) for n in nodes]
        for d in docs:
            _VALIDATOR.validate_node(_NE, d)
        _own(docs, org)
        # a field set to None is not stored (``n += {x: null}``); a repeated id is created once, from its first document
        batch = {d["id"]: {k: v for k, v in d.items() if v is not None} for d in reversed(docs)}
        payload = _j([{"id": i, "doc": batch[i]} for i in sorted(batch)])
        await self._q(lambda tx: tx.execute("""/* pipeshub:graph.create_named_entities_if_absent */ INSERT INTO graph.nodes AS t (org_id, coll, id, doc)
            SELECT $1, 'namedEntities', x.id, x.doc - 'orphanedAt' FROM jsonb_to_recordset($2::jsonb) AS x(id text, doc jsonb) ORDER BY x.id
            ON CONFLICT (org_id, coll, id) DO UPDATE SET doc = t.doc - 'orphanedAt' WHERE t.doc->>'orphanedAt' IS NOT NULL""", org, payload), None, write=True)

    async def add_named_entity_aliases(self, org_id: str, kind: str, key: str, aliases: list[str], normalized: list[str]) -> None:
        """Union the pairs (display, normalized form) into the node's lists, keyed by the normalized form and capped, in one locked read-modify-write.
        A node of another org or kind is not written."""
        org = current_org_id()
        if not aliases:
            return
        aliases, normalized = aliases[:_ALIAS_CAP], normalized[:_ALIAS_CAP]
        now = get_epoch_timestamp_in_ms()
        _VALIDATOR.validate_node_update(_NE, {"aliases": aliases, "normalizedAliases": normalized, "updatedAtTimestamp": now})

        async def go(tx: Tx) -> None:
            r = await tx.fetchrow("""/* pipeshub:graph.add_named_entity_aliases_lock */ SELECT doc FROM graph.nodes WHERE org_id = $1 AND coll = 'namedEntities'
                AND id = $2 AND doc->>'orgId' = $3 AND doc->>'kind' = $4 FOR UPDATE""", org, key, org_id, kind)
            if not r:
                return
            doc = _doc(r["doc"])
            shown, forms = list(doc.get("aliases") or []), list(doc.get("normalizedAliases") or [])
            fresh: dict[str, str] = {}
            for a, n in zip(aliases, normalized, strict=False):
                if n not in forms:
                    fresh.setdefault(n, a)
            if not fresh:
                return
            change = {"aliases": (shown + list(fresh.values()))[:_ALIAS_CAP], "normalizedAliases": (forms + list(fresh))[:_ALIAS_CAP], "updatedAtTimestamp": now}
            await tx.execute("/* pipeshub:graph.add_named_entity_aliases */ UPDATE graph.nodes SET doc = doc || $3::jsonb WHERE org_id = $1 AND coll = 'namedEntities' AND id = $2",
                             org, key, _j(change))      # the row is the one just locked, of this org and kind
        await self._q(go, None, write=True)

    async def resolve_named_entity_redirects(self, org_id: str, ids: list[str]) -> dict[str, str]:
        """Each id mapped to the node it was finally merged into (itself when it was not merged). A chain stops at a cycle or after the hop cap."""
        org = current_org_id()
        final = dict.fromkeys(ids)
        current = {i: i for i in final}
        visited = {i: {i} for i in current}
        for _ in range(MAX_MERGE_REDIRECT_HOPS if current else 0):
            rows = await self._q(lambda tx, pending=sorted(set(current.values())): tx.fetch("""/* pipeshub:graph.resolve_named_entity_redirects */ SELECT id, doc->>'mergedInto' AS merged
                FROM graph.nodes WHERE org_id = $1 AND coll = 'namedEntities' AND id = ANY($2::text[]) AND doc->>'orgId' = $3 AND doc->>'mergedInto' IS NOT NULL""",
                                                    org, pending, org_id), None)
            hops = {r["id"]: r["merged"] for r in rows}
            moved = False
            for origin, node in current.items():
                target = hops.get(node)
                if target and target not in visited[origin]:
                    visited[origin].add(target)
                    current[origin] = target
                    moved = True
            if not moved:
                break
        return current

    # ---- value mentions ----------------------------------------------------------------------------------------
    async def replace_named_entity_values(self, record_id: str, docs: list[dict], transaction: str | None = None) -> None:
        """Make ``docs`` the record's value rows: each replaced whole, the others of the record removed. Every row is checked first."""
        org = current_org_id()
        for d in docs:
            _VALIDATOR.validate_node(_VALUES, d)
        _own(docs, org)
        if any(d.get("recordId") != record_id for d in docs):      # a row of another record would be written over that record's row of the same id
            raise ValueError("a value row names a record other than the one being replaced")
        await self._q(lambda tx: _replace_values(tx, org, record_id, docs), transaction, write=True)

    async def copy_named_entity_mentions(self, source_key: str, target_key: str) -> int:
        """Give ``target_key`` the mention edges of ``source_key`` (a target of the same org as the edge) and, when the target is a live record of the
        org the source's values name, make the source's values its values. In one transaction. Returns the edges and values copied."""
        org = current_org_id()

        async def go(tx: Tx) -> int:
            found = None
            if 0 < len(source_key) <= 200:      # the vetted read of graph_0007
                found = await tx.fetch("/* pipeshub:graph.value_mentions_by_records */ SELECT doc FROM graph.value_mentions_by_records($1::text[]) ORDER BY node_id", [source_key])
                if len(found) >= MENTIONS_BOUND:      # at its bound the answer may be partial
                    found = None
            if found is None:
                found = await tx.fetch("""/* pipeshub:graph.copy_named_entity_mentions_values */ SELECT doc FROM graph.nodes
                    WHERE org_id = $1 AND coll = 'valueMentions' AND doc->>'recordId' = $2 ORDER BY id""", org, source_key)
            rows = [_doc(r["doc"]) for r in found]
            value_org = next((d[_ORG] for d in rows if d.get(_ORG)), None)
            docs: list[dict] = []
            if value_org and await tx.fetchval("""/* pipeshub:graph.copy_named_entity_mentions_target */ SELECT count(*) FROM graph.nodes WHERE org_id = $1 AND coll = 'records'
                AND id = $2 AND doc->>'orgId' = $3 AND coalesce((doc->>'isDeleted')::boolean, false) = false""", org, target_key, value_org):
                docs = [{**{k: v for k, v in d.items() if k not in ("_key", "_id", "_rev", "id")}, "recordId": target_key,
                         "id": value_mention_key(target_key, str(d.get("kind") or ""), str(d.get("normKey") or ""))} for d in rows if d.get(_ORG) == value_org]
                for d in docs:
                    _VALIDATOR.validate_node(_VALUES, d)
            copied = await tx.fetchval("""/* pipeshub:graph.copy_named_entity_mentions */ WITH c AS (
                INSERT INTO graph.edges AS t (org_id, coll, from_coll, from_id, to_coll, to_id, ekey, doc)
                SELECT e.org_id, 'mentionsEntity', 'records', $3, 'namedEntities', e.to_id, '', e.doc FROM graph.edges e
                JOIN graph.nodes n ON n.org_id = e.org_id AND n.coll = 'namedEntities' AND n.id = e.to_id
                JOIN graph.nodes d ON d.org_id = e.org_id AND d.coll = 'records' AND d.id = $3 AND d.doc->>'orgId' = e.doc->>'orgId'
                WHERE e.org_id = $1 AND e.coll = 'mentionsEntity' AND e.from_coll = 'records' AND e.from_id = $2 AND e.to_coll = 'namedEntities' ORDER BY e.to_id
                ON CONFLICT (org_id, coll, from_id, from_coll, to_id, to_coll, ekey) DO UPDATE SET doc = t.doc || EXCLUDED.doc RETURNING 1) SELECT count(*) FROM c""",
                                       org, source_key, target_key)
            if docs:
                await _replace_values(tx, org, target_key, docs)
            return int(copied or 0) + len(docs)
        return await self._q(go, None, write=True)

    async def delete_dangling_named_entity_values(self, org_id: str, batch_size: int = 200) -> int:
        """Remove up to ``batch_size`` value rows whose record no longer exists."""
        org = current_org_id()
        return int(await self._q(lambda tx: tx.fetchval("""/* pipeshub:graph.delete_dangling_named_entity_values */ WITH b AS (
            SELECT v.id FROM graph.nodes v WHERE v.org_id = $1 AND v.coll = 'valueMentions' AND v.doc->>'orgId' = $2
              AND NOT EXISTS (SELECT 1 FROM graph.nodes r WHERE r.org_id = v.org_id AND r.coll = 'records' AND r.id = v.doc->>'recordId') ORDER BY v.id LIMIT $3),
          d AS (DELETE FROM graph.nodes n USING b WHERE n.org_id = $1 AND n.coll = 'valueMentions' AND n.id = b.id RETURNING 1) SELECT count(*) FROM d""",
                                                 org, org_id, max(1, batch_size)), None, write=True) or 0)

    # ---- the orphan sweep --------------------------------------------------------------------------------------
    async def find_orphan_named_entities(self, org_id: str, batch_size: int = 200, marked_before_ms: int | None = None, skip_kinds: list[str] | None = None) -> list[str]:
        """The ids ``delete_orphan_named_entities`` would delete now: no mention, not merged, not of a skipped kind, and with a cutoff marked at or before it."""
        org = current_org_id()
        rows = await self._q(lambda tx: tx.fetch("""/* pipeshub:graph.find_orphan_named_entities */ SELECT n.id FROM graph.nodes n
            WHERE n.org_id = $1 AND n.coll = 'namedEntities' AND n.doc->>'orgId' = $2 AND n.doc->>'mergedInto' IS NULL
              AND ($3::text[] IS NULL OR NOT (n.doc->>'kind' = ANY($3::text[])))
              AND ($4::bigint IS NULL OR (n.doc->>'orphanedAt' IS NOT NULL AND n.doc->'orphanedAt' <= to_jsonb($4::bigint)))
              AND NOT EXISTS (SELECT 1 FROM graph.edges e WHERE e.org_id = n.org_id AND e.coll = 'mentionsEntity' AND e.to_coll = 'namedEntities' AND e.to_id = n.id)
            ORDER BY n.id LIMIT $5""", org, org_id, skip_kinds, marked_before_ms, max(1, batch_size)), None)
        return [r["id"] for r in rows]

    async def delete_orphan_named_entities(self, org_id: str, batch_size: int = 200, marked_before_ms: int | None = None, entity_ids: list[str] | None = None) -> list[str]:
        """Delete up to ``batch_size`` orphans (``entity_ids``: only those of them still orphaned, none for an empty list), each checked again under its row lock, so a writer
        that took the mark off first keeps its node. A locked row is left for the next pass."""
        org = current_org_id()
        rows = await self._q(lambda tx: tx.fetch("""/* pipeshub:graph.delete_orphan_named_entities */ WITH b AS (
            SELECT n.id FROM graph.nodes n WHERE n.org_id = $1 AND n.coll = 'namedEntities' AND n.doc->>'orgId' = $2 AND n.doc->>'mergedInto' IS NULL
              AND ($3::text[] IS NULL OR n.id = ANY($3::text[]))
              AND ($4::bigint IS NULL OR (n.doc->>'orphanedAt' IS NOT NULL AND n.doc->'orphanedAt' <= to_jsonb($4::bigint)))
              AND NOT EXISTS (SELECT 1 FROM graph.edges e WHERE e.org_id = n.org_id AND e.coll = 'mentionsEntity' AND e.to_coll = 'namedEntities' AND e.to_id = n.id)
            ORDER BY n.id LIMIT $5 FOR UPDATE OF n SKIP LOCKED)
          DELETE FROM graph.nodes d USING b WHERE d.org_id = $1 AND d.coll = 'namedEntities' AND d.id = b.id RETURNING d.id""",
                                               org, org_id, entity_ids, marked_before_ms, max(1, batch_size)), None, write=True)
        return sorted(r["id"] for r in rows)

    async def mark_orphan_named_entities(self, org_id: str, now_ms: int, batch_size: int = 200) -> int:
        """Mark up to ``batch_size`` unmarked, unmerged nodes that no record mentions as orphaned at ``now_ms``."""
        org = current_org_id()
        _VALIDATOR.validate_node_update(_NE, {"orphanedAt": now_ms})
        return int(await self._q(lambda tx: tx.fetchval("""/* pipeshub:graph.mark_orphan_named_entities */ WITH b AS (
            SELECT n.id FROM graph.nodes n WHERE n.org_id = $1 AND n.coll = 'namedEntities' AND n.doc->>'orgId' = $2
              AND n.doc->>'orphanedAt' IS NULL AND n.doc->>'mergedInto' IS NULL
              AND NOT EXISTS (SELECT 1 FROM graph.edges e WHERE e.org_id = n.org_id AND e.coll = 'mentionsEntity' AND e.to_coll = 'namedEntities' AND e.to_id = n.id)
            ORDER BY n.id LIMIT $3 FOR UPDATE OF n SKIP LOCKED),
          u AS (UPDATE graph.nodes t SET doc = t.doc || jsonb_build_object('orphanedAt', $4::bigint) FROM b WHERE t.org_id = $1 AND t.coll = 'namedEntities' AND t.id = b.id RETURNING 1)
          SELECT count(*) FROM u""", org, org_id, max(1, batch_size), now_ms), None, write=True) or 0)

    async def clear_orphan_named_entity_marks(self, org_id: str, batch_size: int = 200) -> int:
        """Take the mark off up to ``batch_size`` marked nodes that a record mentions again."""
        org = current_org_id()
        return int(await self._q(lambda tx: tx.fetchval("""/* pipeshub:graph.clear_orphan_named_entity_marks */ WITH b AS (
            SELECT n.id FROM graph.nodes n WHERE n.org_id = $1 AND n.coll = 'namedEntities' AND n.doc->>'orgId' = $2 AND n.doc->>'orphanedAt' IS NOT NULL
              AND EXISTS (SELECT 1 FROM graph.edges e WHERE e.org_id = n.org_id AND e.coll = 'mentionsEntity' AND e.to_coll = 'namedEntities' AND e.to_id = n.id)
            ORDER BY n.id LIMIT $3 FOR UPDATE OF n SKIP LOCKED),
          u AS (UPDATE graph.nodes t SET doc = t.doc - 'orphanedAt' FROM b WHERE t.org_id = $1 AND t.coll = 'namedEntities' AND t.id = b.id RETURNING 1)
          SELECT count(*) FROM u""", org, org_id, max(1, batch_size)), None, write=True) or 0)

    async def delete_dangling_named_entity_mentions(self, entity_ids: list[str]) -> int:
        """Remove the mention edges that point at one of ``entity_ids`` that no longer exists (a writer's edge that committed while the sweep deleted
        the node; a node created anew keeps its edges). Neo4j removes a node's edges with it and answers 0; here the edge is not tied to its node."""
        org = current_org_id()
        if not entity_ids:
            return 0
        return int(await self._q(lambda tx: tx.fetchval("""/* pipeshub:graph.delete_dangling_named_entity_mentions */ WITH d AS (
            DELETE FROM graph.edges e WHERE e.org_id = $1 AND e.coll = 'mentionsEntity' AND e.to_coll = 'namedEntities' AND e.to_id = ANY($2::text[])
              AND NOT EXISTS (SELECT 1 FROM graph.nodes n WHERE n.org_id = e.org_id AND n.coll = 'namedEntities' AND n.id = e.to_id) RETURNING 1)
          SELECT count(*) FROM d""", org, list(dict.fromkeys(entity_ids))), None, write=True) or 0)

    # ---- persist retries and re-extraction jobs ----------------------------------------------------------------
    async def schedule_named_entity_persist_retry(self, org_id: str, record_id: str, error: str, attempts_so_far: int = 0) -> int:
        """Record a failed write of ``record_id``: due again after a backoff that doubles with each failure (from the larger of the stored count and
        ``attempts_so_far``), capped. Returns the failure count."""
        org = current_org_id()
        now = get_epoch_timestamp_in_ms()
        marker = _marker(org, org_id, record_id, attempts=attempts_so_far + 1, lastError=error[:200])
        _VALIDATOR.validate_node(_RETRIES, marker)
        due = now + int(min(PERSIST_RETRY_MAX_MS, PERSIST_RETRY_BASE_MS * 2.0 ** min(attempts_so_far, 64)))
        # one statement, so two first failures of a record count both: the second waits for the first's row and adds to it
        attempts = await self._q(lambda tx: tx.fetchval("""/* pipeshub:graph.schedule_named_entity_persist_retry */ INSERT INTO graph.nodes AS t (org_id, coll, id, doc)
            VALUES ($1, 'namedEntityPersistRetries', $2, $3::jsonb) ON CONFLICT (org_id, coll, id) DO UPDATE SET doc = t.doc || jsonb_build_object(
              'attempts', greatest(CASE WHEN jsonb_typeof(t.doc->'attempts') = 'number' THEN (t.doc->>'attempts')::numeric ELSE 0 END, $4::numeric) + 1,
              'dueAt', $5::bigint + least($6::numeric, floor($7::numeric * power(2::numeric, least(greatest(
                CASE WHEN jsonb_typeof(t.doc->'attempts') = 'number' THEN (t.doc->>'attempts')::numeric ELSE 0 END, $4::numeric), 64)))),
              'lastError', $8::text, 'updatedAtTimestamp', $5::bigint)
            RETURNING (t.doc->>'attempts')::int""", org, record_id, _j({**marker, "id": record_id, "dueAt": due}), attempts_so_far, now,
                                                         PERSIST_RETRY_MAX_MS, PERSIST_RETRY_BASE_MS, error[:200]), None, write=True)
        return int(attempts)

    async def schedule_named_entity_reextract(self, org_id: str, record_id: str) -> None:
        """Queue ``record_id`` to be extracted again from its stored blocks, due now. A pending re-extraction keeps its failure count and an earlier
        due time; a pending write retry becomes the job with its count reset."""
        org = current_org_id()
        marker = _marker(org, org_id, record_id, reextract=True)
        _VALIDATOR.validate_node(_RETRIES, marker)
        await self._q(lambda tx: tx.execute("""/* pipeshub:graph.schedule_named_entity_reextract */ INSERT INTO graph.nodes AS t (org_id, coll, id, doc)
            VALUES ($1, 'namedEntityPersistRetries', $2, $3::jsonb) ON CONFLICT (org_id, coll, id) DO UPDATE SET doc = t.doc || jsonb_build_object(
              'reextract', true, 'updatedAtTimestamp', $4::bigint,
              'attempts', CASE WHEN t.doc->'reextract' = 'true'::jsonb THEN coalesce(t.doc->'attempts', '0'::jsonb) ELSE '0'::jsonb END,
              'dueAt', CASE WHEN t.doc->>'dueAt' IS NOT NULL AND t.doc->'dueAt' < to_jsonb($4::bigint) THEN t.doc->'dueAt' ELSE to_jsonb($4::bigint) END)""",
                                             org, record_id, _j({**marker, "id": record_id}), marker["updatedAtTimestamp"]), None, write=True)

    async def schedule_named_entity_reproject(self, org_id: str, record_id: str) -> None:
        """Queue ``record_id``'s stored extraction to be written again, due now, counting no failure; a record with a pending marker keeps it."""
        org = current_org_id()
        marker = _marker(org, org_id, record_id, lastError="reproject")
        _VALIDATOR.validate_node(_RETRIES, marker)
        await self._q(lambda tx: tx.execute("""/* pipeshub:graph.schedule_named_entity_reproject */ INSERT INTO graph.nodes (org_id, coll, id, doc)
            VALUES ($1, 'namedEntityPersistRetries', $2, $3::jsonb) ON CONFLICT (org_id, coll, id) DO NOTHING""", org, record_id, _j({**marker, "id": record_id})), None, write=True)

    async def get_due_named_entity_persist_retries(self, limit: int, *, reextract: bool = False) -> list[dict]:
        """The retries due now (or the re-extraction jobs), oldest first, of the ambient org, or of every org under a system context."""
        now = get_epoch_timestamp_in_ms()
        rows: list[dict] = []
        errors: list[tuple[str, Exception]] = []
        for org_id in await self._sweep_orgs():
            with self._sweep(org_id, errors):
                org = current_org_id()
                rows += [_doc(r["doc"]) for r in await self._q(lambda tx, org=org: tx.fetch("""/* pipeshub:graph.get_due_named_entity_persist_retries */ SELECT doc FROM graph.nodes
                    WHERE org_id = $1 AND coll = 'namedEntityPersistRetries' AND doc->>'dueAt' IS NOT NULL AND doc->'dueAt' <= to_jsonb($2::bigint)
                      AND coalesce(doc->'reextract' = 'true'::jsonb, false) = $3 ORDER BY doc->'dueAt', id LIMIT $4""", org, now, reextract, max(1, limit)), None)]
        self._raise_failed(errors)
        rows.sort(key=lambda d: (d["dueAt"], d["id"]))
        return [{"recordId": d["id"], "orgId": d.get(_ORG), "attempts": d.get("attempts"), "dueAt": d["dueAt"]} for d in rows[:max(1, limit)]]

    async def clear_named_entity_persist_retry(self, record_id: str, due_at: int | None, transaction: str | None = None) -> bool:
        """Drop the record's retry, only while its due time is still ``due_at`` (whatever is pending when none). Whether one was dropped. Without a
        transaction, a system context drops it from whichever org holds it."""
        dropped = False
        errors: list[tuple[str, Exception]] = []
        for org_id in [None] if transaction else await self._sweep_orgs():
            with self._sweep(org_id, errors):
                org = current_org_id()
                dropped |= bool(await self._q(lambda tx, org=org: tx.fetch("""/* pipeshub:graph.clear_named_entity_persist_retry */ DELETE FROM graph.nodes WHERE org_id = $1
                    AND coll = 'namedEntityPersistRetries' AND id = $2 AND ($3::bigint IS NULL OR doc->'dueAt' = to_jsonb($3::bigint)) RETURNING 1""",
                                                                  org, record_id, due_at), transaction, write=True))
        self._raise_failed(errors)
        return dropped


async def _replace_values(tx: Tx, org: str, record_id: str, docs: list[dict]) -> None:
    rows = {d["id"]: {k: v for k, v in d.items() if v is not None} for d in docs}      # a repeated id: the last one wins; a null is not stored (``SET v = doc``)
    held = None
    if 0 < len(record_id) <= 200:
        # One statement finds the rows by the vetted read of graph_0007 and deletes them, so a row committed in between cannot be missed, as with the
        # plain delete. It deletes nothing when the read is at its bound (it may be partial): the plain delete then runs.
        held = await tx.fetchval("""/* pipeshub:graph.replace_named_entity_values_stale_vetted */ WITH h AS MATERIALIZED (
                SELECT node_id FROM graph.value_mentions_by_records($2::text[])),
              d AS (DELETE FROM graph.nodes WHERE org_id = $1 AND coll = 'valueMentions' AND id IN (SELECT node_id FROM h) AND doc->>'recordId' = $5
                AND id <> ALL($3::text[]) AND (SELECT count(*) FROM h) < $4 RETURNING 1)
            SELECT count(*) FROM h""", org, [record_id], list(rows), MENTIONS_BOUND, record_id)
    if held is None or held >= MENTIONS_BOUND:
        await tx.execute("""/* pipeshub:graph.replace_named_entity_values_stale */ DELETE FROM graph.nodes WHERE org_id = $1 AND coll = 'valueMentions'
            AND doc->>'recordId' = $2 AND id <> ALL($3::text[])""", org, record_id, list(rows))
    if rows:
        await tx.execute("""/* pipeshub:graph.replace_named_entity_values */ INSERT INTO graph.nodes AS t (org_id, coll, id, doc)
            SELECT $1, 'valueMentions', x.id, x.doc FROM jsonb_to_recordset($2::jsonb) AS x(id text, doc jsonb) ORDER BY x.id
            ON CONFLICT (org_id, coll, id) DO UPDATE SET doc = EXCLUDED.doc""", org, _j([{"id": i, "doc": rows[i]} for i in sorted(rows)]))
