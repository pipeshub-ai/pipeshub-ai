"""A property test of the re-key under policy C: random tables, windows, failed reads and unmovable points.

Random records: 1-3 tables of 3-150 rows with their summaries and paragraphs, a random rows-per-point, and in half the trials a small
token ceiling that cuts groups into windows sharing a straddling row. A random read batch size (5-60 old ids), random read batches
fail (PartialResult), and in-memory only, random stored points lose their vector (cannot move). The legacy index embeds with V1,
the re-key run with V2, so a moved point is told from a newly embedded one on any store.

Invariants after the re-key:
  I1 every row of a fresh index is listed by at least one point (blockId or rowBlockIds);
  I2 no row is listed both by a moved point and by a newly embedded one;
  I3 no point references an old id;
  I4 without straddling windows every row is listed by exactly one point (with them a re-embedded subset of rows is grouped and
     cut at other places than a fresh index, so only I1-I2 bound it);
  I5 every moved point equals the fresh index's point of the same id (text, metadata, vector): retrieval over moved points is a
     fresh index's; with no failure injected and nothing unmovable, the whole point set equals a fresh index's;
  I7 a row held only by moved points keeps every point a fresh index gives it (no piece of a split row is lost);
  I6 every table summary and paragraph text (or, when the ceiling cuts it, each of its pieces in a fresh index) is in some point.
"""
from __future__ import annotations

import hashlib
import os
import random
from collections import Counter
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

from app.config.constants.arangodb import EventTypes
from app.modules.transformers import vectorstore as vs_mod
from app.services.vector_db.errors import PartialResult
from tests.support.in_memory_vector_db import InMemoryVectorDBService
from tests.support.index_world import ORG, RID, VRID, Blob, make_store, record_of, run

if TYPE_CHECKING:
    from collections.abc import Callable

    from app.models.blocks import BlocksContainer
    from app.modules.transformers.vectorstore import VectorStore
    from app.services.vector_db.interface.vector_db import IVectorDBService
    from app.services.vector_db.models import ScrollResult

SEED = int(os.getenv("REKEY_PROPERTY_SEED", "7"))
WORDS = "alpha beta gamma delta epsilon zeta eta theta iota kappa lambda mu nu xi omicron pi rho sigma tau phi chi psi omega".split()


def vec(text: str, dim: int, salt: str) -> list[float]:
    r = random.Random(hashlib.sha256((salt + text).encode()).digest())
    return [round(r.uniform(-1, 1), 4) for _ in range(dim)]


def build(rng: random.Random) -> tuple[Callable[[], BlocksContainer], list[str]]:
    from app.models.blocks import (
        Block,
        BlockGroup,
        BlockGroupChildren,
        BlocksContainer,
        BlockType,
        DataFormat,
        GroupType,
    )

    def sent(n: int) -> str:
        return " ".join(rng.choice(WORDS) for _ in range(n)).capitalize() + "."

    blocks, groups, rows_all, texts = [], [], [], []

    def text(t: str) -> None:
        blocks.append(Block(index=len(blocks), type=BlockType.TEXT, format=DataFormat.TXT, data=t))
        texts.append(t)

    text("Intro. " + sent(8))
    for t in range(rng.randint(1, 3)):
        if rng.random() < 0.7:
            text(f"Table {t} title")
        gi = len(groups)
        rows = [f"row {t}.{i}: " + sent(rng.randint(3, 9)) for i in range(rng.randint(3, 150))]
        members = [Block(index=len(blocks) + i, type=BlockType.TABLE_ROW, format=DataFormat.JSON, parent_index=gi,
                         data={"row_natural_language_text": r, "row_number": i}) for i, r in enumerate(rows)]
        blocks.extend(members)
        rows_all.extend(rows)
        summary = f"Summary of table {t}: " + sent(6)
        groups.append(BlockGroup(index=gi, type=GroupType.TABLE, data={"table_summary": summary},
                                 children=BlockGroupChildren.from_indices(block_indices=[m.index for m in members])))
        texts.append(summary)
        if rng.random() < 0.6:
            text(f"After {t}. " + sent(7))
    return (lambda: BlocksContainer(blocks=[b.model_copy(deep=True) for b in blocks], block_groups=[g.model_copy(deep=True) for g in groups])), texts


def listed(points: dict) -> dict[str, list]:
    by_row: dict[str, list] = {}
    for pid, (_text, meta, _v) in points.items():
        if meta.get("rowBlockIds") or "row" in str(meta.get("blockType", "")).lower():
            for b in meta.get("rowBlockIds") or [meta.get("blockId")]:
                by_row.setdefault(b, []).append(pid)
    return by_row


async def snapshot(svc, col) -> dict[str, tuple]:
    await svc.make_searchable(col)
    flt = await svc.filter_collection(must={"orgId": ORG, "virtualRecordId": VRID})
    out, offset = {}, None
    while True:
        page = await svc.scroll_with_vectors(col, flt, 200, offset)
        for p in page.points:
            out[p.id] = (p.payload.get("page_content"), dict(p.payload.get("metadata") or {}),
                         tuple(round(x, 3) for x in (p.dense_vector or ())))
        if not page.next_offset:
            return out
        offset = page.next_offset


def _store(svc: IVectorDBService, col: str, dim: int, salt: str) -> VectorStore:
    store = make_store(svc, dim=dim, collection=col)
    store._embed_documents_with_retry = AsyncMock(side_effect=lambda ts, _rid, witness_out=None: [vec(t, dim, salt) for t in ts])
    return store


async def one_trial(svc, col, fresh_col, dim, rng, monkeypatch, *, unmovable: bool, clear=None) -> dict:
    """``clear``: a store with one collection (pgvector) indexes the fresh reference first in *col*, then clears it."""
    straddle = rng.random() < 0.5
    monkeypatch.setenv("EMBED_POINT_POLICY", "C")
    monkeypatch.setenv("EMBED_TABLE_ROWS_PER_POINT", str(rng.randint(2, 8)))
    if straddle:
        monkeypatch.setenv("PIPESHUB_EMBED_TOKEN_LIMIT", str(rng.randint(40, 120)))
    else:
        monkeypatch.delenv("PIPESHUB_EMBED_TOKEN_LIMIT", raising=False)
    monkeypatch.setattr(vs_mod, "_REKEY_BLOCKS_PER_READ", rng.randint(5, 60))
    container, texts = build(rng)
    if clear is not None:
        await run(_store(svc, col, dim, "V1"), Blob(), container(), EventTypes.NEW_RECORD.value)
        fresh = await snapshot(svc, col)
        await clear()
    legacy_c = container()
    legacy = _store(svc, col, dim, "V1")
    await legacy.index_documents(legacy_c, ORG, RID, VRID, record=record_of(legacy_c))
    from app.modules.reconciliation.service import ReconciliationService

    blob = Blob()
    blob.saved = ReconciliationService().build_metadata(legacy_c).to_dict()
    before = await snapshot(svc, col)
    old_ids = {b for _, m, _ in before.values() for b in [m.get("blockId"), *(m.get("rowBlockIds") or ())]}
    broken = 0
    if unmovable and isinstance(svc, InMemoryVectorDBService):
        for p in rng.sample(list(svc.collections[col].values()), k=rng.randint(0, max(1, len(before) // 6))):
            p.dense_vector = None
            broken += 1
    p_fail = rng.choice([0.0, 0.0, 0.2, 0.5])
    real = svc.scroll_with_vectors
    calls = {"n": 0, "failed": 0}

    async def faulty(*a: object, **k: object) -> ScrollResult:
        calls["n"] += 1
        if rng.random() < p_fail:
            calls["failed"] += 1
            raise PartialResult("injected")
        return await real(*a, **k)

    svc.scroll_with_vectors = faulty
    try:
        await run(_store(svc, col, dim, "V2"), blob, container())
    finally:
        svc.scroll_with_vectors = real
    if clear is None:
        await run(_store(svc, fresh_col, dim, "V1"), Blob(), container(), EventTypes.NEW_RECORD.value)
        fresh = await snapshot(svc, fresh_col)
    after = await snapshot(svc, col)
    info = {"straddle": straddle, "failed": calls["failed"], "reads": calls["n"], "broken": broken, "points": len(after)}
    tag = f"{info}"
    rows_fresh, rows_after = listed(fresh), listed(after)
    def near(v: list[float] | tuple[float, ...], w: list[float]) -> float:
        """Distance by direction (1 - cosine): a store that normalises vectors (Qdrant, cosine) keeps the direction, not the length."""
        dot = sum(a * b for a, b in zip(v, w, strict=False))
        norms = (sum(a * a for a in v) ** 0.5) * (sum(b * b for b in w) ** 0.5)
        return 1.0 - dot / norms if norms else 2.0

    # moved: the stored vector is V1's (the legacy embed), not V2's (this run's); float32 stores round, so compare by distance
    moved = {pid for pid, (t, m, v) in after.items() if v and near(v, vec(t, dim, "V1")) < near(v, vec(t, dim, "V2"))}
    lost = set(rows_fresh) - set(rows_after)
    assert not lost, f"I1 {tag}: {len(lost)} of {len(rows_fresh)} rows in no point"
    both = [r for r, ps in rows_after.items() if {p in moved for p in ps} == {True, False}]
    assert not both, f"I2 {tag}: {len(both)} rows held by a moved point and a new one"
    stale = [pid for pid, (_, m, _) in after.items() if {m.get("blockId"), *(m.get("rowBlockIds") or ())} & old_ids]
    assert not stale, f"I3 {tag}: {len(stale)} points reference old ids"
    counts = Counter({r: len(ps) for r, ps in rows_after.items()})
    over = [] if straddle else [r for r, c in counts.items() if c > 1]
    assert not over, f"I4 {tag}: {len(over)} rows in more points than allowed"
    # I7: a row held only by moved points keeps every point a fresh index gives it (a row cut across windows keeps all its pieces)
    cut = [r for r, ps in rows_after.items() if all(p in moved for p in ps) and not set(rows_fresh.get(r, ())) <= set(after)]
    assert not cut, f"I7 {tag}: {len(cut)} moved rows lost a piece of their text (a point of theirs is gone)"
    differs = [pid for pid in moved if after[pid] != fresh.get(pid)]
    assert not differs, f"I5 {tag}: {len(differs)} moved points differ from the fresh index"
    if not calls["failed"] and not broken:
        assert set(after) == set(fresh), f"I5 {tag}: no failure, yet the point set differs from a fresh index"
    joined = "\n".join(str(t) for t, _, _ in after.values())
    fresh_joined = "\n".join(str(t) for t, _, _ in fresh.values())
    gone = [t for t in texts if t in fresh_joined and t not in joined]
    # a text cut by the small ceiling is compared piece by piece: every piece of the fresh index's that holds it is in some point
    for t in texts:
        if t not in fresh_joined:
            pieces = {str(x) for x, m, _ in fresh.values() if str(x) and (str(x) in t or t.startswith(str(x)[:10]))}
            gone += [x for x in pieces if x not in joined]
    assert not gone, f"I6 {tag}: {gone[:2]} in no point"
    return info
