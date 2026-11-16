"""The run rule's statement fragments against the Python reference, on a real store.

Stored records are generated with every shape a field can take in a store that does not check types (absent, JSON
null, a number, a float, a string, a bool), then one write of each kind is applied to all of them *by the fragment,
inside the store's own statement*, and each result is compared with ``record_diagnostics.reference``.

The scratch records live in a collection / label / org of their own and are removed afterwards. They bypass the
provider's schema validation on purpose: a real ``records`` collection refuses a string ``firstQueuedAt``, and the
rule must still read one as "unknown" (an older or hand-edited record, or a store with no validator in front).
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from app.config.constants.arangodb import ProgressStatus
from app.core.org_context import OrgContext, bind
from app.modules.indexing.record_diagnostics import (
    WriteKind,
    apply_reference,
    aql_parts,
    cypher_parts,
    is_number,
    sql_patch,
    with_run_patch,
)

if TYPE_CHECKING:
    import random

NOW = 2_000_000_000_000
REV = "rev-1"
FIELDS = (
    "indexingStatus", "firstQueuedAt", "queuedAtTimestamp", "indexingAttempts", "failedAt",
    "lastIndexTimestamp", "restoredAtTimestamp", "contentRev", "pageCount",
)
_ABSENT = object()
STATUSES = [s.value for s in ProgressStatus][:12]

# (kind, status the statement writes, revision) -- every way the product writes a run field
CASES: list[tuple[WriteKind, str | None, str | None]] = [
    (WriteKind.QUEUE, "QUEUED", None),
    (WriteKind.QUEUE, "NOT_STARTED", None),
    (WriteKind.ATTEMPT_START, "IN_PROGRESS", REV),
    (WriteKind.ATTEMPT_START, "IN_PROGRESS", None),
    (WriteKind.FAILED, "FAILED", None),
    (WriteKind.SINK_END, "NOT_STARTED", None),
    (WriteKind.START, None, None),
    (WriteKind.STATUS_VAR, "FAILED", None),
    (WriteKind.STATUS_VAR, "COMPLETED", None),
    (WriteKind.STATUS_VAR, "NOT_STARTED", None),
    (WriteKind.STATUS_VAR, "QUEUED", None),
]


def generate(rng: random.Random, n: int) -> list[dict[str, Any]]:
    """``n`` stored records. Numbers are close together so that ``firstQueuedAt`` falls on either side of an outcome."""
    big = lambda: rng.choice([NOW - 10**9, NOW - 10**6, NOW - 10**3, NOW - 7, NOW - 1])  # noqa: E731
    pools: dict[str, list[Any]] = {
        "indexingStatus": [*STATUSES, _ABSENT],
        "firstQueuedAt": [_ABSENT, None, big(), big(), float(big()), "abc", True, 1.5e12],
        "queuedAtTimestamp": [_ABSENT, None, big(), "x", False],
        "indexingAttempts": [_ABSENT, None, rng.randint(0, 9), "x", 2.0],
        "failedAt": [_ABSENT, _ABSENT, None, big(), "x"],
        "lastIndexTimestamp": [_ABSENT, _ABSENT, None, big(), "x", True],
        "restoredAtTimestamp": [_ABSENT, _ABSENT, _ABSENT, None, big(), "x"],
        "contentRev": [_ABSENT, REV, "rev-2", 5],
        "pageCount": [_ABSENT, _ABSENT, 7],
    }
    rows = []
    for i in range(n):
        doc: dict[str, Any] = {"k": f"c{i}"}
        for name, pool in pools.items():
            value = rng.choice(pool)
            if name in ("firstQueuedAt", "queuedAtTimestamp", "failedAt", "lastIndexTimestamp", "restoredAtTimestamp") and value is not _ABSENT and is_number(value):
                value = rng.choice([NOW - 10**9, NOW - 10**6, NOW - 10**3, NOW - 7, NOW - 1, NOW - 10**6 + 1])
            if value is not _ABSENT:
                doc[name] = value
        rows.append(doc)
    return rows


def _norm(doc: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for name in FIELDS:
        value = doc.get(name)
        if value is None:
            continue
        out[name] = float(value) if is_number(value) else value
    return out


def mismatches(stored: list[dict[str, Any]], got: dict[str, dict[str, Any]], case: tuple[WriteKind, str | None, str | None]) -> list[tuple[Any, Any, Any]]:
    kind, status, rev = case
    bad = []
    for doc in stored:
        want = apply_reference(doc, kind, NOW, status=status, content_rev=rev)
        if status is not None:
            want["indexingStatus"] = status
        if rev is not None:
            want["contentRev"] = rev
        if _norm(want) != _norm(got[doc["k"]]):
            bad.append((doc, _norm(want), _norm(got[doc["k"]])))
    return bad


@dataclass
class Controls:
    """A part of the rule left out (a transform of the generated text), to prove the comparison sees it."""

    aql: Any = None
    cypher: Any = None
    sql: Any = None
    cypher_second_set: bool = False


# ---- ArangoDB ----------------------------------------------------------------------------------------------


class ArangoRunner:
    name = "arango"

    def __init__(self, provider: Any) -> None:  # noqa: ANN401
        self.client = provider.http_client
        self.coll = f"rdconf_{uuid.uuid4().hex[:8]}"

    async def start(self) -> None:
        assert await self.client.create_collection(self.coll, edge=False, schema=None)

    async def stop(self) -> None:
        await self.client.delete_collection(self.coll)

    async def load(self, docs: list[dict[str, Any]]) -> None:
        await self.client.execute_aql("FOR d IN @@c REMOVE d IN @@c", {"@c": self.coll})
        rows = [{**d, "_key": d["k"]} for d in docs]
        await self.client.execute_aql("FOR x IN @rows INSERT x INTO @@c", {"@c": self.coll, "rows": rows})

    async def apply(self, case: tuple[WriteKind, str | None, str | None], controls: Controls | None = None) -> dict[str, dict[str, Any]]:
        kind, status, rev = case
        lets, patch = aql_parts("doc", kind, now="@now", status="@status", rev="@rev" if rev else None)
        if controls and controls.aql:
            lets, patch = controls.aql(lets, patch)
        written = "@written" if status is not None else "{}"
        query = f"FOR doc IN @@c {lets} UPDATE doc WITH MERGE({written}, {patch}) IN @@c OPTIONS {{ keepNull: false }}"
        binds: dict[str, Any] = {"@c": self.coll}
        offered = {"now": NOW, "status": status, "rev": rev, "written": {"indexingStatus": status, **({"contentRev": rev} if rev else {})}}
        binds.update({name: value for name, value in offered.items() if f"@{name}" in query})
        await self.client.execute_aql(query, binds)
        rows = await self.client.execute_aql("FOR d IN @@c RETURN d", {"@c": self.coll})
        return {r["k"]: r for r in rows}


# ---- Neo4j -------------------------------------------------------------------------------------------------


class Neo4jRunner:
    name = "neo4j"

    def __init__(self, provider: Any) -> None:  # noqa: ANN401
        self.client = provider.client
        self.run = uuid.uuid4().hex[:10]

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        await self.client.execute_query("MATCH (n:RdConf {run: $r}) DETACH DELETE n", parameters={"r": self.run})

    async def load(self, docs: list[dict[str, Any]]) -> None:
        await self.client.execute_query("MATCH (n:RdConf {run: $r}) DETACH DELETE n", parameters={"r": self.run})
        rows = [{**d, "run": self.run} for d in docs]
        await self.client.execute_query("UNWIND $rows AS x CREATE (n:RdConf) SET n = x", parameters={"rows": rows})

    async def apply(self, case: tuple[WriteKind, str | None, str | None], controls: Controls | None = None) -> dict[str, dict[str, Any]]:
        kind, status, rev = case
        with_clause, patch = cypher_parts("n", kind, now="$now", status="$status", rev="$rev" if rev else None)
        if controls and controls.cypher:
            with_clause, patch = controls.cypher(with_clause, patch)
        set_status = "n.indexingStatus = $status, " if status is not None else ""
        set_rev = "n.contentRev = $rev, " if rev else ""
        if controls and controls.cypher_second_set:
            # the rule written as a second SET clause reads the status the first one wrote
            reads = {"_st": "n.indexingStatus", "_fq": "n.firstQueuedAt", "_qa": "n.queuedAtTimestamp", "_at": "n.indexingAttempts",
                     "_fa": "n.failedAt", "_li": "n.lastIndexTimestamp", "_ra": "n.restoredAtTimestamp", "_cr": "n.contentRev"}
            for alias, read in reads.items():
                patch = re.sub(rf"\b{alias}\b", read, patch)
            query = f"MATCH (n:RdConf {{run: $run}}) SET {set_status}{set_rev}n.run = $run SET n += {patch}"
        else:
            query = f"MATCH (n:RdConf {{run: $run}}) {with_clause} SET {set_status}{set_rev}n += {patch}"
        params = {"run": self.run, "now": NOW, "status": status, "rev": rev}
        await self.client.execute_query(query, parameters={k: v for k, v in params.items() if f"${k}" in query})
        rows = await self.client.execute_query("MATCH (n:RdConf {run: $r}) RETURN properties(n) AS p", parameters={"r": self.run})
        return {r["p"]["k"]: r["p"] for r in rows}


# ---- PostgreSQL ----------------------------------------------------------------------------------------------


class PostgresRunner:
    name = "postgres"

    def __init__(self, db: Any, org: str) -> None:  # noqa: ANN401
        self.db, self.org = db, org

    def _ctx(self) -> Any:  # noqa: ANN401
        return bind(OrgContext(self.org, None, "job", "job:test", "t"))

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        with self._ctx():
            await self.db.run(lambda tx: tx.execute("DELETE FROM graph.nodes WHERE org_id = $1", self.org), write=True)

    async def load(self, docs: list[dict[str, Any]]) -> None:
        with self._ctx():
            await self.db.run(lambda tx: tx.execute("DELETE FROM graph.nodes WHERE org_id = $1", self.org), write=True)
            await self.db.run(lambda tx: tx.execute(
                "INSERT INTO graph.nodes (org_id, coll, id, doc) SELECT $1, 'records', x->>'k', x FROM jsonb_array_elements($2::jsonb) x",
                self.org, json.dumps(docs)), write=True)

    async def apply(self, case: tuple[WriteKind, str | None, str | None], controls: Controls | None = None) -> dict[str, dict[str, Any]]:
        kind, status, rev = case
        patch = sql_patch("doc", kind, now="$2::bigint", status="$3::text", rev="$4::text" if rev else None)
        if controls and controls.sql:
            patch = controls.sql(patch)
        written = " || jsonb_build_object('indexingStatus', $3::text)" if status is not None else ""
        if rev:
            written += " || jsonb_build_object('contentRev', $4::text)"
        # every positional parameter is named, whatever the case, so that the numbering is dense
        statement = with_run_patch(
            f"UPDATE graph.nodes SET doc = doc{written} || {{RUN_PATCH}} WHERE org_id = $1 AND coll = 'records' "
            "AND ($3::text IS NULL OR true) AND ($4::text IS NULL OR true) AND $2::bigint IS NOT NULL", patch)
        args: list[Any] = [self.org, NOW, status, rev]
        with self._ctx():
            await self.db.run(lambda tx: tx.execute(statement, *args), write=True)
            rows = await self.db.run(lambda tx: tx.fetch("SELECT doc FROM graph.nodes WHERE org_id = $1 AND coll = 'records'", self.org))
        return {json.loads(r["doc"])["k"] if isinstance(r["doc"], str) else r["doc"]["k"]: (json.loads(r["doc"]) if isinstance(r["doc"], str) else r["doc"]) for r in rows}


# ---- controls: the rule with one part left out ---------------------------------------------------------------

CONTROLS: dict[str, Controls] = {
    "aql: queue time read without its number guard": Controls(
        aql=lambda lets, patch: (lets.replace("(IS_NUMBER(doc.queuedAtTimestamp) ? doc.queuedAtTimestamp : @now)", "(doc.queuedAtTimestamp != null ? doc.queuedAtTimestamp : @now)"), patch)),
    "aql: outcome stamps compared without a number guard": Controls(
        aql=lambda lets, patch: (re.sub(r"IS_NUMBER\(doc\.(failedAt|lastIndexTimestamp|restoredAtTimestamp)\) \? doc\.\1 : null", r"doc.\1", lets), patch)),
    "cypher: `<> null` instead of a number test": Controls(
        cypher=lambda w, p: (w, p.replace("(_fq IS :: INTEGER NOT NULL OR _fq IS :: FLOAT NOT NULL)", "(_fq <> null)"))),
    "cypher: plain `<>` revision test (absent revision keeps a stale count)": Controls(
        cypher=lambda w, p: (w, p.replace("_cr IS NULL OR NOT _cr =", "NOT _cr ="))),
    "cypher: the rule in a second SET clause": Controls(cypher_second_set=True),
    "sql: `doc->'f' IS NOT NULL` instead of a number test": Controls(
        sql=lambda p: p.replace("coalesce(jsonb_typeof(doc->'firstQueuedAt') = 'number', false)", "(doc->'firstQueuedAt' IS NOT NULL)")),
}
CONTROL_STORE = {
    "aql: queue time read without its number guard": "arango",
    "aql: outcome stamps compared without a number guard": "arango",
    "cypher: `<> null` instead of a number test": "neo4j",
    "cypher: plain `<>` revision test (absent revision keeps a stale count)": "neo4j",
    "cypher: the rule in a second SET clause": "neo4j",
    "sql: `doc->'f' IS NOT NULL` instead of a number test": "postgres",
}
