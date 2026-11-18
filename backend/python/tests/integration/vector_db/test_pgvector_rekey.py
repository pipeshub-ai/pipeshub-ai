"""A record stored under random block ids moves to content-derived ids on pgvector without embedding again.

The real `VectorStore` and the real provider (a PostgreSQL with pgvector, through PgBouncer); only the embedding model is a stub that counts.
Run: KERNEL_TEST_PG_BINS=<pg bin> KERNEL_TEST_PGBOUNCER=<pgbouncer> pytest tests/integration/vector_db/test_pgvector_rekey.py -m integration
"""
from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from app.core import org_context
from app.modules.reconciliation.block_identity import BlockIdentity
from app.modules.reconciliation.service import ReconciliationService
from app.services.vector_db.models import FieldCondition, FilterExpression
from app.services.vector_db.pgvector.config import PgVectorConfig
from app.services.vector_db.pgvector.pgvector import PgVectorService
from tests.integration.db.conftest import cluster, env  # noqa: F401  (fixtures)
from tests.support.index_world import (
    COLL,
    ORG,
    RID,
    VRID,
    Blob,
    documents,
    index,
    make_store,
    parse,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = [pytest.mark.integration, pytest.mark.timeout(600)]
DIM = 8
N = 30


def _doc(changed: tuple[int, ...] = ()) -> str:
    return "\n\n".join(f"Paragraph {i} " + ("was rewritten. " if i in changed else "is untouched. ") + f"Topic {i}." for i in range(N))


@pytest.fixture
async def pg(env) -> AsyncIterator[PgVectorService]:  # noqa: F811
    port = env.start_bouncer()
    svc = await PgVectorService.create(PgVectorConfig(dsn=env.dsn("indexing", port), jobs_dsn=env.dsn("jobs", port), pool_max=4, placement="none"))
    yield svc
    await svc.disconnect()


async def _stored(pg: PgVectorService) -> dict[str, tuple[str, list[float]]]:
    """block id -> (text, vector) of the record's points, read back whole."""
    flt = FilterExpression(must=[FieldCondition(key="metadata.orgId", value=ORG), FieldCondition(key="metadata.virtualRecordId", value=VRID)])
    out: dict[str, tuple[str, list[float]]] = {}
    offset = None
    while True:
        page = await pg.scroll_with_vectors(COLL, flt, 100, offset)
        out.update({p.payload["metadata"]["blockId"]: (p.payload["page_content"], p.dense_vector) for p in page.points})
        if not page.next_offset:
            return out
        offset = page.next_offset


def _content_ids(text: str) -> set[str]:
    container = parse(text)
    BlockIdentity.assign(container, ORG, VRID)
    return {b.id for b in container.blocks}


async def _legacy(pg: PgVectorService) -> tuple[object, Blob]:
    store = make_store(pg, dim=DIM)
    legacy = parse(_doc())  # random ids, as stored before ids followed content
    await store._embed_and_upsert_documents(documents(legacy), RID, COLL)
    store._embed_documents_with_retry.reset_mock()
    blob = Blob()
    blob.saved = ReconciliationService().build_metadata(legacy).to_dict()
    return store, blob


async def test_unchanged_blocks_move_with_their_vectors_and_nothing_is_embedded(pg) -> None:
    with org_context.for_org(ORG, job="test"):
        store, blob = await _legacy(pg)
        before = await _stored(pg)
        assert len(before) == N and not set(before) & _content_ids(_doc())
        await index(store, blob, _doc())
        after = await _stored(pg)
    assert store._embed_documents_with_retry.await_count == 0
    assert set(after) == _content_ids(_doc())
    by_text = dict(before.values())
    assert sorted(by_text) == sorted(t for t, _ in after.values()), "the same texts"
    assert all(v == pytest.approx(by_text[t], abs=1e-2) for t, v in after.values()), "the stored vectors came along"


async def test_only_the_changed_blocks_are_embedded(pg) -> None:
    with org_context.for_org(ORG, job="test"):
        store, blob = await _legacy(pg)
        await index(store, blob, _doc(changed=(4, 17)))
        after = await _stored(pg)
    texts = [t for call in store._embed_documents_with_retry.await_args_list for t in call.args[0]]
    assert len(texts) == 2 and all("was rewritten" in t for t in texts)
    assert set(after) == _content_ids(_doc(changed=(4, 17))) and len(after) == N
