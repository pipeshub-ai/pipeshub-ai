"""The calls of the vetted kg writers (kg_0002). Only the connectors service may execute them (the database says so); this module is the one
place the application calls them from, and ``db.lint`` fails on any other source file that names one.

Every call is chunked at the writers' cap of 5,000 items, runs in the caller's unit and returns the rows it changed.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from app.services.graph_db.postgres.kg.model import COLL_CODE, Key

if TYPE_CHECKING:
    from collections.abc import Iterator
    from datetime import datetime

    from app.core.db import Tx

CAP = 5000
STAMP_COLS = ("sk", "coll", "id", "kind", "path", "class_id", "self_lit", "seed", "own_grant", "under_deleted", "legacy_reason", "leaf")
RECORD_COLS = ("sk", "coll", "id", "kind", "app_sk", "group_sk", "parent_sk", "rule", "model", "vrid", "name", "name_key", "updated_ms", "created_ms",
               "size_bytes", "record_type", "origin", "external_id", "deleted", "placeholder", "granted")


def _chunks(rows: list, n: int = CAP) -> Iterator[list]:
    for i in range(0, len(rows), n):
        yield rows[i:i + n]


class KgWriter:
    """Wraps a ``Tx`` (connectors role, write unit, org bound by the prologue)."""

    def __init__(self, tx: Tx) -> None:
        self.tx = tx

    async def nodes_upsert(self, keys: set[Key]) -> dict[Key, int]:
        """Insert-if-absent and return the sk of every key. Sorted by (collection code, id): no deadlock between overlapping batches."""
        out: dict[Key, int] = {}
        ks = sorted(((COLL_CODE[k[0]], k[1]) for k in keys))
        inv = {v: k for k, v in COLL_CODE.items()}
        for part in _chunks(ks):
            rows = await self.tx.fetch("/* pipeshub:kg.w_nodes_upsert */ SELECT * FROM kg.w_nodes_upsert($1::smallint[], $2::text[])", [c for c, _ in part], [i for _, i in part])
            for r in rows:
                out[(inv[r["o_coll"]], r["o_id"])] = r["o_sk"]
        return out

    async def records_upsert(self, rows: list[dict]) -> int:
        n = 0
        for part in _chunks(sorted(rows, key=lambda r: r["sk"])):       # key order: two writers that overlap take their row locks in the same order
            args = [[r[c] for r in part] for c in RECORD_COLS]
            n += await self.tx.fetchval("/* pipeshub:kg.w_records_upsert */ SELECT kg.w_records_upsert($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17, $18, $19, $20, $21)", *args)
        return n

    async def classes_set(self, rows: list[tuple[int, int, int | None, str, bytes]]) -> int:
        """New classes, by id (an existing id is left as it is): (id, app_sk, group_sk, clauses as JSON text, hash)."""
        return sum([await self.tx.fetchval("/* pipeshub:kg.w_classes_set */ SELECT kg.w_classes_set($1::bigint[], $2::bigint[], $3::bigint[], $4::text[]::jsonb[], $5::bytea[])",
                                           [r[0] for r in p], [r[1] for r in p], [r[2] for r in p], [r[3] for r in p], [r[4] for r in p]) for p in _chunks(sorted(rows))])

    async def class_literals_set(self, rows: list[tuple[int, int]]) -> int:
        return sum([await self.tx.fetchval("/* pipeshub:kg.w_class_literals_set */ SELECT kg.w_class_literals_set($1::bigint[], $2::bigint[])",
                                           [r[0] for r in p], [r[1] for r in p]) for p in _chunks(sorted(rows))])

    async def app_facts_set(self, app_sk: int, *, opens: bool, group_level: bool) -> bool:
        """What the stamps of the connector were compiled with: when either changes, every record of it is restamped."""
        return await self.tx.fetchval("/* pipeshub:kg.w_app_facts_set */ SELECT kg.w_app_facts_set($1, $2, $3)", app_sk, opens, group_level)

    async def app_cross_set(self, app_sks: list[int]) -> int:
        """Remember that records of these connectors have hierarchy parents or groups in another connector (sticky): a pending change of one can outdate the other."""
        return sum([await self.tx.fetchval("/* pipeshub:kg.w_app_cross_set */ SELECT kg.w_app_cross_set($1::bigint[])", p) for p in _chunks(sorted(set(app_sks)))])

    async def records_delete(self, sks: list[int]) -> int:
        return sum([await self.tx.fetchval("/* pipeshub:kg.w_records_delete */ SELECT kg.w_records_delete($1::bigint[])", p) for p in _chunks(sorted(sks))])

    async def links_set(self, rows: list[tuple[int, int, int]]) -> int:
        return sum([await self.tx.fetchval("/* pipeshub:kg.w_links_set */ SELECT kg.w_links_set($1::bigint[], $2::smallint[], $3::bigint[])",
                                           [r[0] for r in p], [r[1] for r in p], [r[2] for r in p]) for p in _chunks(sorted(rows))])

    async def links_delete(self, rows: list[tuple[int, int, int]]) -> int:
        return sum([await self.tx.fetchval("/* pipeshub:kg.w_links_delete */ SELECT kg.w_links_delete($1::bigint[], $2::smallint[], $3::bigint[])",
                                           [r[0] for r in p], [r[1] for r in p], [r[2] for r in p]) for p in _chunks(sorted(rows))])

    async def grants_set(self, rows: list[tuple[int, int, str, str | None, int | None, bool]]) -> int:
        return sum([await self.tx.fetchval("/* pipeshub:kg.w_grants_set */ SELECT kg.w_grants_set($1::bigint[], $2::bigint[], $3::text[], $4::text[], $5::bigint[], $6::boolean[])",
                                           [r[0] for r in p], [r[1] for r in p], [r[2] for r in p], [r[3] for r in p], [r[4] for r in p], [r[5] for r in p])
                    for p in _chunks(sorted(rows, key=lambda r: r[:3]))])

    async def grants_delete(self, rows: list[tuple[int, int, str]]) -> int:
        return sum([await self.tx.fetchval("/* pipeshub:kg.w_grants_delete */ SELECT kg.w_grants_delete($1::bigint[], $2::bigint[], $3::text[])",
                                           [r[0] for r in p], [r[1] for r in p], [r[2] for r in p]) for p in _chunks(sorted(rows))])

    async def closure_set(self, rows: list[tuple[int, int, int, str]]) -> int:
        return sum([await self.tx.fetchval("/* pipeshub:kg.w_closure_set */ SELECT kg.w_closure_set($1::bigint[], $2::bigint[], $3::bigint[], $4::text[])",
                                           [r[0] for r in p], [r[1] for r in p], [r[2] for r in p], [r[3] for r in p]) for p in _chunks(sorted(rows))])

    async def closure_delete(self, rows: list[tuple[int, int, int]]) -> int:
        return sum([await self.tx.fetchval("/* pipeshub:kg.w_closure_delete */ SELECT kg.w_closure_delete($1::bigint[], $2::bigint[], $3::bigint[])",
                                           [r[0] for r in p], [r[1] for r in p], [r[2] for r in p]) for p in _chunks(sorted(rows))])

    async def stamps_set(self, rows: list[dict]) -> int:
        """The access stamps of records (path, class, flags): one upsert whose update list is the stamp columns and nothing else."""
        n = 0
        for part in _chunks(rows):
            n += await self.tx.fetchval(
                "/* pipeshub:kg.w_stamps_set */ SELECT kg.w_stamps_set($1::bigint[], $2::smallint[], $3::text[], $4::smallint[], $5::bytea[], $6::bigint[], $7::boolean[], $8::boolean[], $9::boolean[], "
                "$10::boolean[], $11::smallint[], $12::boolean[])", *[[r[c] for r in part] for c in STAMP_COLS])
        return n

    async def fence_rows(self, rows: list[tuple[int, int, str, int]], job: int | None) -> int:
        """Mark records that kg holds as hidden until restamp job ``job`` stamps them (None: lift the mark): rows are (sk, coll code, id, kind code). The writer is an upsert, so the
        caller passes rows it has read from kg.records: a key kg holds no row for would get a row of nothing but a key."""
        n = 0
        for part in _chunks(sorted(rows)):
            n += await self.tx.fetchval("/* pipeshub:kg.w_fence_rows */ SELECT kg.w_fence_rows($1::bigint[], $2::smallint[], $3::text[], $4::smallint[], $5::bigint[])",
                                        [r[0] for r in part], [r[1] for r in part], [r[2] for r in part], [r[3] for r in part], [job] * len(part))
        return n

    async def queue_add(self, job: int, app_sk: int, kind: str, reason: str, *, fenced: bool) -> bool:
        """Queue a restamp job; an id already taken is left as it is."""
        return await self.tx.fetchval("/* pipeshub:kg.w_queue_add */ SELECT kg.w_queue_add($1, $2, $3, $4, $5)", job, app_sk, kind, reason, fenced)

    async def queue_touch(self, job: int, seen: int, worker: str, attempts: int, cursor: str | None) -> int | None:
        """Claim or heartbeat a job if nobody has touched it since ``seen`` (compare and set on the heartbeat): returns the new heartbeat, which is the database's clock, or None when
        another worker took the job."""
        return await self.tx.fetchval("/* pipeshub:kg.w_queue_touch */ SELECT kg.w_queue_touch($1, $2, $3, $4, $5)", job, seen, worker, attempts, cursor) or None

    async def queue_release(self, job: int, worker: str) -> bool:
        """Give back this worker's own claim on a job (the cursor and the heartbeat stay): False when the job is not claimed by ``worker`` in this org."""
        return await self.tx.fetchval("/* pipeshub:kg.w_queue_release */ SELECT kg.w_queue_release($1, $2)", job, worker)

    async def queue_done(self, job: int) -> bool:
        """Delete a finished job; False when it was not there."""
        return await self.tx.fetchval("/* pipeshub:kg.w_queue_done */ SELECT kg.w_queue_done($1)", job)

    async def changes_ack(self) -> int:
        """This transaction's own rows of kg.graph_changes."""
        return await self.tx.fetchval("/* pipeshub:kg.w_changes_ack */ SELECT kg.w_changes_ack()")

    async def changes_delete(self, ids: list[int]) -> int:
        """Rows other transactions left, by their ids."""
        return sum([await self.tx.fetchval("/* pipeshub:kg.w_changes_delete */ SELECT kg.w_changes_delete($1::bigint[])", p) for p in _chunks(sorted(ids))])

    async def projection_set(self, app_sk: int, *, epoch: int, mode: str, digest: str | None, cursor: str | None, verified_at: datetime | None) -> bool:
        return await self.tx.fetchval("/* pipeshub:kg.w_projection_set */ SELECT kg.w_projection_set($1, $2, $3, $4, $5, $6)", app_sk, epoch, mode, digest, cursor, verified_at)
