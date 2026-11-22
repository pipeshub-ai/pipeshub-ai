"""A real `VectorStore` over any `IVectorDBService`, with only the embedding model stubbed (tests of how records are indexed).

`index` runs what an indexing event does to one record: parse (random block ids, as every parser mints them), assign content ids,
diff against the stored reconciliation metadata, and `VectorStore.apply`.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

from app.config.constants.arangodb import EventTypes
from app.models.blocks import Block, BlocksContainer, BlockType, DataFormat
from app.modules.reconciliation.service import ReconciliationService
from app.modules.transformers.pipeline import IndexingPipeline
from app.modules.transformers.transformer import TransformContext
from app.modules.transformers.vectorstore import VectorStore, _process_text_blocks
from app.services.vector_db.models import VectorDBCapabilities
from tests.support.vector_db import make_collection_registry

if TYPE_CHECKING:
    from app.services.vector_db.interface.vector_db import IVectorDBService

ORG, VRID, RID, COLL = "a" * 24, "vr-race", "rec-race", "records"


def make_store(db: IVectorDBService, dim: int = 2, collection: str = COLL) -> VectorStore:
    """The embedding is `[len(text), ...]` padded to *dim*: distinct per text and stable, so a moved vector can be told from a new one."""
    db.get_capabilities = MagicMock(return_value=VectorDBCapabilities())
    store = VectorStore(
        logger=MagicMock(), config_service=AsyncMock(), graph_provider=AsyncMock(),
        collection_registry=make_collection_registry(collection), vector_db_service=db,
    )
    store.graph_provider.get_document = AsyncMock(return_value={"_key": RID})
    store.get_embedding_model_instance = AsyncMock(return_value=False)
    store._ensure_collection = AsyncMock(return_value=collection)
    store._cleanup_orphaned_embeddings_if_needed = AsyncMock()
    store._resync_membership_after_write = AsyncMock()
    store._embed_documents_with_retry = AsyncMock(side_effect=lambda texts, _rid, witness_out=None: [vector_of(t, dim) for t in texts])
    store._compute_sparse_embeddings = AsyncMock(side_effect=lambda texts: [None for _ in texts])
    return store


def vector_of(text: str, dim: int = 2) -> list[float]:
    return ([float(len(text)), float(sum(map(ord, text)) % 97)] + [1.0] * dim)[:dim]


def parse(text: str) -> BlocksContainer:
    """One block per paragraph, minted with the random ids every parser mints."""
    return BlocksContainer(blocks=[
        Block(index=i, type=BlockType.TEXT, format=DataFormat.TXT, data=p) for i, p in enumerate(text.split("\n\n"))
    ])


def record_of(container: BlocksContainer) -> MagicMock:
    record = MagicMock()
    record.id, record.org_id, record.virtual_record_id = RID, ORG, VRID
    record.block_containers = container
    record.semantic_metadata = None
    return record


def context_of(record: MagicMock, event: str, prev: str | None = VRID) -> MagicMock:
    ctx = MagicMock(spec=TransformContext)
    ctx.record, ctx.event_type, ctx.prev_virtual_record_id = record, event, prev
    ctx.reconciliation_context, ctx.settings = None, {}
    return ctx


class Blob:
    """Holds the reconciliation metadata the way the blob store does, as a dict."""

    def __init__(self) -> None:
        self.saved: dict | None = None
        self.get_reconciliation_metadata = AsyncMock(side_effect=lambda *_, **__: self.saved)


async def index(store: VectorStore, blob: Blob, text: str, *, event: str = EventTypes.UPDATE_RECORD.value) -> None:
    container = parse(text)
    ctx = context_of(record_of(container), event)
    sink = SimpleNamespace(blob_storage=blob, vector_store=store)
    ctx.reconciliation_context = await IndexingPipeline.build_reconciliation_context(ctx, MagicMock(), sink)
    await store.apply(ctx)
    blob.saved = ctx.reconciliation_context.new_metadata


def documents(container: BlocksContainer) -> list:
    return _process_text_blocks(container.blocks, VRID, ORG, None)


async def run(store: VectorStore, blob: Blob, container: BlocksContainer, event: str = EventTypes.UPDATE_RECORD.value) -> object:
    """`index` for a container the caller built (several blocks of one kind, a table, repeats)."""
    ctx = context_of(record_of(container), event)
    sink = SimpleNamespace(blob_storage=blob, vector_store=store)
    ctx.reconciliation_context = await IndexingPipeline.build_reconciliation_context(ctx, MagicMock(), sink)
    result = await store.apply(ctx)
    blob.saved = ctx.reconciliation_context.new_metadata
    return result


async def legacy_record(db: IVectorDBService, container: BlocksContainer, dim: int = 2) -> tuple[VectorStore, Blob]:
    """A record as the code before content ids stored it: random block ids, reconciliation metadata of no scheme, a full index."""
    store = make_store(db, dim)
    await store.index_documents(container, ORG, RID, VRID, record=record_of(container))
    store._embed_documents_with_retry.reset_mock()
    blob = Blob()
    blob.saved = ReconciliationService().build_metadata(container).to_dict()
    assert "idScheme" not in blob.saved
    return store, blob


def embedded_texts(store: VectorStore) -> list[str]:
    return [t for call in store._embed_documents_with_retry.await_args_list for t in call.args[0]]
