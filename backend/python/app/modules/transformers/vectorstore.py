"""VectorStore transformer — provider-agnostic indexing pipeline.

Indexing strategy
-----------------
1. Obtain dense embeddings from the configured embedding model.
2. Compute BM25 sparse vectors via SparseEmbedder *only* when the provider declares
   ``capabilities.supports_sparse_vectors == True`` (currently only Qdrant).
3. Always store ``page_content`` as plain text in each VectorPoint payload so
   that server-side text-search providers (Redis, OpenSearch) can use it for
   their lexical leg.
4. Upsert all points in batches via ``await vector_db_service.upsert_points()``.

No LangChain QdrantVectorStore is imported or used.
"""

import asyncio
import os
import time
import uuid
from typing import List, Optional

from langchain_core.documents import Document

from app.config.constants.arangodb import CollectionNames
from app.config.constants.service import config_node_constants
from app.exceptions.indexing_exceptions import (
    DocumentProcessingError,
    EmbeddingError,
    IndexingError,
    VectorStoreError,
)
from app.models.blocks import Block, BlocksContainer, BlockType, SemanticMetadata
from app.models.entities import Record
from app.modules.parsers.text_splitting import detect_language, split_into_sentences
from app.modules.transformers.transformer import TransformContext, Transformer
from app.services.embeddings.multimodal.config import MultimodalProviderConfig
from app.services.embeddings.multimodal.factory import MultimodalEmbeddingFactory
from app.services.embeddings.multimodal.interface import ImageEmbeddingResult
from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
from app.services.vector_db.collection_locator import VirtualRecordCollectionLocator
from app.services.vector_db.collection_registry import CollectionRegistry
from app.services.vector_db.interface.vector_db import IVectorDBService
from app.services.vector_db.membership import (
    reset_membership_context,
    resolve_vector_membership,
    rewrite_or_delete_virtual_record,
    set_membership_context,
    sync_vector_membership,
    vector_point_payload,
)
from app.services.vector_db.models import SparseVector, VectorPoint
from app.services.vector_db.sparse_embeddings import SparseEmbedder
from app.services.vector_db.strategy import RecordContext
from app.utils.aimodels import (
    EmbeddingProvider,
    get_default_embedding_model,
    get_embedding_model,
    is_local_cpu_embedding_provider,
)
from app.utils.image_utils import normalize_image_to_base64

RECORD_SUMMARY_BLOCK_ID_SUFFIX = "_summary"

_DEFAULT_DOCUMENT_BATCH_SIZE = 50


def _resolve_batch_concurrency(env_value: str | None, *, default: int = 5) -> int:
    """Parse EMBEDDING_BATCH_CONCURRENCY, rejecting values below 1.

    asyncio.Semaphore(0) is locked from creation — every remote embedding
    batch would hang forever at `async with semaphore` instead of failing
    visibly, so reject this at configuration time rather than at first use.
    """
    limit = int(env_value) if env_value else default
    if limit < 1:
        raise ValueError(f"EMBEDDING_BATCH_CONCURRENCY must be >= 1, got {limit}")
    return limit


# Bounds concurrent remote-embedding batch calls per record (the local-CPU
# path is sequential and unaffected — see use_local_sequential below).
# Tunable via EMBEDDING_BATCH_CONCURRENCY since it interacts with
# EMBEDDING_SERVER_MAX_CONCURRENCY: too high here just queues on the server.
_DEFAULT_CONCURRENCY_LIMIT = _resolve_batch_concurrency(os.getenv("EMBEDDING_BATCH_CONCURRENCY"))
_LOCAL_CPU_DOCUMENT_BATCH_SIZE = 20

# Blocks are already capped at this size by the parsers (text_splitting.MAX_TEXT_BLOCK_CHARS),
# but connector-authored blocks can bypass that path — guard defensively here too.
_MAX_BLOCK_CHARS_FOR_SENTENCE_SPLIT = 50_000
_OVERSIZED_CHUNK_SIZE = 1500
_OVERSIZED_CHUNK_OVERLAP = 200
_LANGUAGE_DETECTION_SAMPLE_CHARS = 2000
_DEFAULT_SENTENCE_EMBED_MIN_WORDS = 100

# Safety-net timeouts — prevent any single step from blocking the pipeline forever.
# asyncio.to_thread / run_in_executor cannot actually kill the underlying thread on
# timeout, but the caller unblocks, releases semaphores, and the consumer can retry
# or skip the record.
_TEXT_PROCESSING_TIMEOUT_S = 300  # 5 min for sentence-splitting a full record

# Local CPU-served models (default/sentence-transformers/HF) are much slower per
# batch than hosted API embedding providers, so they get a much longer allowance.
_LOCAL_EMBEDDING_BATCH_TIMEOUT_S = 600  # 10 min per embedding batch on local CPU
_REMOTE_EMBEDDING_BATCH_TIMEOUT_S = 120  # 2 min per embedding batch via hosted API


def _detect_record_language(text_blocks: List) -> str:
    """Detect language once per record from a sample of its text blocks.

    Per-block detection is wasteful and unstable on short blocks (headings,
    list items), so a handful of blocks are sampled up to a char budget and
    detected together.
    """
    sample_parts: List[str] = []
    sample_len = 0
    for block in text_blocks:
        text = block.data or ""
        if not text:
            continue
        sample_parts.append(text)
        sample_len += len(text)
        if sample_len >= _LANGUAGE_DETECTION_SAMPLE_CHARS:
            break
    if not sample_parts:
        return "en"
    return detect_language(" ".join(sample_parts))


def _min_words_for_sentence_embeddings() -> int:
    """Blocks at or below this word count embed as one document (no sentence split).

    ``EMBED_SENTENCE_MIN_WORDS`` (default 100). ``0`` restores splitting every
    multi-sentence block.
    """
    raw = os.getenv("EMBED_SENTENCE_MIN_WORDS", str(_DEFAULT_SENTENCE_EMBED_MIN_WORDS))
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return _DEFAULT_SENTENCE_EMBED_MIN_WORDS


def _word_count(text: str) -> int:
    return len(text.split()) if text else 0


def _chunk_oversized_text(
    text: str,
    language: str,
    chunk_size: int = _OVERSIZED_CHUNK_SIZE,
    overlap: int = _OVERSIZED_CHUNK_OVERLAP,
) -> List[str]:
    """Pack sentences into overlapping ~chunk_size windows.

    Used for blocks too large to embed as a single whole-block document.
    Overlap preserves cross-boundary context between adjacent chunks.
    """
    sentences = split_into_sentences(text, language=language)
    if not sentences:
        return [text]

    chunks: List[str] = []
    current: List[str] = []
    current_len = 0

    for sentence in sentences:
        added_len = len(sentence) + (1 if current else 0)
        if current and current_len + added_len > chunk_size:
            chunks.append(" ".join(current))
            overlap_sentences: List[str] = []
            overlap_len = 0
            for s in reversed(current):
                if overlap_len + len(s) > overlap:
                    break
                overlap_sentences.insert(0, s)
                overlap_len += len(s) + 1
            current, current_len = overlap_sentences, overlap_len
            added_len = len(sentence) + (1 if current else 0)
        current.append(sentence)
        current_len += added_len

    if current:
        chunks.append(" ".join(current))

    return chunks or [text]


def _build_text_documents(
    text_blocks: List,
    virtual_record_id: str,
    org_id: str,
    language: str,
) -> List[Document]:
    """Sentence-split each text block into embeddable Documents.

    CPU-bound (regex/rule-based sentence segmentation); callers should run
    this via ``asyncio.to_thread`` to keep the event loop responsive.
    """
    documents: List[Document] = []
    for block in text_blocks:
        # A text block can legitimately carry no text. Blob storage strips keys
        # whose value is "" (_clean_top_level_empty_values), so a block that was
        # empty at parse time re-hydrates with data=None — which is why this only
        # shows up on the blob-backed reindex path and not during normal
        # indexing. There is nothing to embed either way: an empty document would
        # just be a useless retrieval unit.
        block_text = block.data or ""
        if not block_text.strip():
            continue
        metadata = {
            "virtualRecordId": virtual_record_id,
            "blockId": block.id,
            "blockIndex": block.index,
            "orgId": org_id,
            "isBlockGroup": False,
            "blockType": BlockType.TEXT.value,
        }

        if len(block_text) > _MAX_BLOCK_CHARS_FOR_SENTENCE_SPLIT:
            # Too large to also embed as one whole-block document (would be a
            # useless retrieval unit) — pack into overlapping windows instead.
            documents.extend(
                Document(page_content=chunk, metadata={**metadata, "isBlock": False})
                for chunk in _chunk_oversized_text(block_text, language)
            )
            continue

        if _word_count(block_text) > _min_words_for_sentence_embeddings():
            sentences = split_into_sentences(block_text, language=language)
            if len(sentences) > 1:
                documents.extend(
                    Document(page_content=sentence, metadata={**metadata, "isBlock": False})
                    for sentence in sentences
                )
        documents.append(
            Document(
                page_content=block_text,
                metadata={**metadata, "isBlock": True},
            )
        )
    return documents


def _build_code_documents(
    code_blocks: List,
    virtual_record_id: str,
    org_id: str,
) -> List[Document]:
    """One embeddable Document per code symbol.

    Sentence-splitting is meaningless for code, so each symbol is embedded
    whole from ``block.data["text"]`` — the raw source text produced by the
    parser, same pattern every other block type follows.
    """
    documents: List[Document] = []
    for block in code_blocks:
        data = block.data if isinstance(block.data, dict) else {}
        text = data.get("text") or ""
        if not text.strip():
            continue
        metadata = {
            "virtualRecordId": virtual_record_id,
            "blockId": block.id,
            "blockIndex": block.index,
            "orgId": org_id,
            "isBlockGroup": False,
        }
        documents.append(
            Document(page_content=text, metadata={**metadata, "isBlock": True})
        )
    return documents


def _process_text_blocks(
    text_blocks: List,
    virtual_record_id: str,
    org_id: str,
) -> List[Document]:
    """Detect language and build embeddable Documents for a record's text blocks.

    Combines detection + sentence splitting so both run inside one
    ``asyncio.to_thread`` call (see call site in ``index_documents``).
    """
    language = _detect_record_language(text_blocks)
    return _build_text_documents(text_blocks, virtual_record_id, org_id, language)


def _storage_reconcile_enabled() -> bool:
    """Whether to migrate an existing collection's storage layout on startup.

    Defaults off: the rewrite is expensive and unattended-unsafe (see
    VectorStore._reconcile_storage_layout).
    """
    return os.getenv("VECTOR_STORAGE_RECONCILE_ENABLED", "false").strip().lower() in (
        "1",
        "true",
        "yes",
    )


class VectorStore(Transformer):

    def __init__(
        self,
        logger,
        config_service,
        graph_provider: IGraphDBProvider,
        collection_registry: CollectionRegistry,
        vector_db_service: IVectorDBService,
    ) -> None:
        super().__init__()
        self.logger = logger
        self.config_service = config_service
        self.graph_provider = graph_provider
        self.vector_db_service = vector_db_service
        self.collection_registry = collection_registry
        self.collection_locator = VirtualRecordCollectionLocator(
            strategy=collection_registry.strategy,
            manifest_store=collection_registry.manifest_store,
            logger=logger,
        )

        self.dense_embeddings = None
        self.api_key = None
        self.embedding_endpoint = None
        self.model_name = None
        self.embedding_provider = None
        self.embedding_size: int | None = None
        self.is_multimodal_embedding = False
        self.region_name = None
        self.aws_access_key_id = None
        self.aws_secret_access_key = None
        self.base_url: str | None = None

        self._capabilities = self.vector_db_service.get_capabilities()

        # Sparse embeddings — only for providers that store client-side sparse vectors.
        # SparseEmbedder lazy-initialises in a worker thread on first use.
        self._sparse_embedder: SparseEmbedder | None = None
        self._sparse_embedder_lock: asyncio.Lock | None = None

    # ------------------------------------------------------------------
    # Sparse embedding lazy initialisation
    # ------------------------------------------------------------------

    async def _ensure_sparse_embeddings(self) -> SparseEmbedder | None:
        """Return the SparseEmbedder if this provider uses client-side sparse vectors."""
        if not self._capabilities.supports_sparse_vectors:
            return None
        if self._sparse_embedder is not None:
            return self._sparse_embedder
        if self._sparse_embedder_lock is None:
            self._sparse_embedder_lock = asyncio.Lock()
        async with self._sparse_embedder_lock:
            if self._sparse_embedder is None:
                try:
                    embedder = SparseEmbedder()
                    # Trigger init now (in thread) so first index call isn't slow
                    await embedder._ensure_initialized()
                except Exception as e:
                    raise IndexingError(
                        "Failed to initialise sparse embeddings: " + str(e),
                        details={"error": str(e)},
                    )
                self._sparse_embedder = embedder
        return self._sparse_embedder

    # ------------------------------------------------------------------
    # Transformer protocol
    # ------------------------------------------------------------------

    async def apply(self, ctx: TransformContext) -> bool | None:
        record = ctx.record
        record_id = record.id
        virtual_record_id = record.virtual_record_id
        block_containers = record.block_containers
        org_id = record.org_id
        block_ids_to_delete = None
        is_reconciliation = False

        if (
            ctx.reconciliation_context
            and ctx.reconciliation_context.blocks_to_index_ids is not None
        ):
            is_reconciliation = True
            blocks_to_index_ids = ctx.reconciliation_context.blocks_to_index_ids
            block_ids_to_delete = ctx.reconciliation_context.block_ids_to_delete or set()

            if not blocks_to_index_ids and not block_ids_to_delete:
                self.logger.info(
                    f"Reconciliation: No changes detected for record {record_id}"
                )
                return True

            # Shallow copy with only blocks/block_groups that need indexing
            block_containers = BlocksContainer(
                blocks=[b for b in block_containers.blocks if b.id in blocks_to_index_ids],
                block_groups=[
                    bg for bg in block_containers.block_groups
                    if bg.id in blocks_to_index_ids
                ],
            )

        return await self.index_documents(
            block_containers,
            org_id,
            record_id,
            virtual_record_id,
            block_ids_to_delete=block_ids_to_delete,
            is_reconciliation=is_reconciliation,
            record=record,
        )

    # ------------------------------------------------------------------
    # Record summary helpers
    # ------------------------------------------------------------------

    @staticmethod
    def record_summary_block_id(virtual_record_id: str) -> str:
        return f"{virtual_record_id}{RECORD_SUMMARY_BLOCK_ID_SUFFIX}"

    @staticmethod
    def _build_record_summary_document(
        record_id: str,
        virtual_record_id: str,
        org_id: str,
        semantic_metadata: "SemanticMetadata",
    ) -> Document | None:
        summary = (semantic_metadata.summary or "").strip()
        if not summary:
            return None
        metadata: dict = {
            "virtualRecordId": virtual_record_id,
            "blockId": VectorStore.record_summary_block_id(virtual_record_id),
            "orgId": org_id,
            "isBlockGroup": False,
            "isBlock": False,
            "isRecordSummary": True,
            "blockType": BlockType.RECORD_SUMMARY.value,
        }
        return Document(page_content=summary, metadata=metadata)

    async def _refresh_record_summary_documents(
        self,
        documents_to_embed: list,
        record: "Record",
        org_id: str,
        record_id: str,
        virtual_record_id: str,
    ) -> None:
        semantic_metadata = getattr(record, "semantic_metadata", None)
        if semantic_metadata is None:
            return
        summary_doc = self._build_record_summary_document(
            record_id, virtual_record_id, org_id, semantic_metadata
        )
        if summary_doc:
            documents_to_embed.append(summary_doc)

    # ------------------------------------------------------------------
    # Image helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _image_block_description(block: Block) -> str:
        """Best-effort human-readable text for an image block's vector payload.

        Image ``page_content`` must never hold the raw base64 data URI — it is
        useless for lexical/BM25 search, bloats point payloads, and can leak
        image bytes into DB text indexes. The base64 URI itself always remains
        recoverable from blob storage via blockId.

        Prefers the description `ImageDescriber` wrote before the record was
        stored, since parser-captured captions are empty for most PDFs; falls
        back to caption/footnote/annotation metadata (e.g. markdown/HTML alt
        text), then to an empty string.
        """
        image_metadata = getattr(block, "image_metadata", None)
        if image_metadata is None:
            return ""
        described = getattr(image_metadata, "description", None)
        if isinstance(described, str) and described.strip():
            return described.strip()
        parts: List[str] = []
        for field in ("captions", "footnotes", "annotations"):
            values = getattr(image_metadata, field, None)
            if values:
                parts.extend(v for v in values if v)
        return " ".join(parts).strip()

    async def _normalize_image_to_base64(self, image_uri: str) -> str | None:
        """Delegates to the shared ``app.utils.image_utils`` helper.

        Kept as an instance method (rather than removed) since it is part of
        this class's existing public-ish surface and covered by tests; the
        actual parsing logic lives in one place shared with the multimodal
        embedding providers.
        """
        return normalize_image_to_base64(image_uri)

    async def index_record_summary(
        self,
        record_id: str,
        virtual_record_id: str,
        org_id: str,
        semantic_metadata: SemanticMetadata,
        record: Optional["Record"] = None,
    ) -> None:
        """Embed the record-level summary after extraction completes."""
        summary_doc = self._build_record_summary_document(
            record_id, virtual_record_id, org_id, semantic_metadata
        )
        if summary_doc is None:
            return

        # The enrich phase can reach here without the index phase having built
        # the model on this instance — vector-store indexing skipped, or
        # enrichment deferred and resumed elsewhere. Without this
        # self.dense_embeddings is still None and the first use surfaces as an
        # AttributeError far from its cause, after the delete below has already
        # dropped the old summary block.
        if self.dense_embeddings is None:
            try:
                await self.get_embedding_model_instance()
            except Exception as e:
                raise IndexingError(
                    "Failed to get embedding model instance: " + str(e),
                    details={"error": str(e)},
                )

        collection_name = await self._ensure_collection(org_id, record, self.embedding_size)

        summary_block_id_set = {f"{virtual_record_id}{RECORD_SUMMARY_BLOCK_ID_SUFFIX}"}
        await self.delete_blocks_by_ids(summary_block_id_set, virtual_record_id, collection_name)
        tokens = await self._bind_membership(virtual_record_id)
        try:
            await self._process_document_chunks([summary_doc], record_id, collection_name)
        finally:
            reset_membership_context(tokens)
        self.logger.debug("✅ Indexed record summary for record %s", record_id)

    # ------------------------------------------------------------------
    # Collection resolution (delegates lifecycle to CollectionRegistry so a
    # future non-default CollectionStrategy needs no VectorStore changes)
    # ------------------------------------------------------------------

    def _record_context(self, org_id: str, record: Optional["Record"]) -> RecordContext:
        """Build the context this record's collection is resolved from.

        Goes through ``RecordContext.from_record`` rather than reading the
        fields here, because the dedup path builds the same context from a
        graph document and the two are compared to decide whether to skip
        indexing. One normalisation, two entry points.

        The embedding model is carried too: it is not on the record, and
        without it a per-embedding-model strategy would map every model onto
        one collection.
        """
        if record is None:
            return RecordContext(
                org_id=org_id,
                embedding_model=self.model_name,
                embedding_dimension=self.embedding_size,
            )
        return RecordContext.from_record(
            record,
            org_id,
            embedding_model=self.model_name,
            embedding_dimension=self.embedding_size,
        )

    async def _ensure_collection(
        self,
        org_id: str,
        record: Optional["Record"],
        embedding_size: int,
        sparse_idf: bool = False,
    ) -> str:
        """Resolve + create (if needed) the collection this record's points belong in.

        Returned name is threaded explicitly through every downstream call in
        this indexing pass rather than stored on ``self`` — a future
        multi-collection strategy can vary this per record, and concurrent
        records processed by the same VectorStore instance must not race on
        shared mutable state.
        """
        ctx = self._record_context(org_id, record)
        collection_name = await self.collection_registry.ensure_collection(
            ctx, embedding_size, sparse_idf
        )
        await self._reconcile_storage_layout(collection_name, embedding_size, sparse_idf)
        return collection_name

    async def _reconcile_storage_layout(
        self, collection_name: str, embedding_size: int, sparse_idf: bool
    ) -> None:
        """Nudge a pre-existing collection toward the current storage layout.

        Opt-in, and deliberately so. Each step makes the vector store rewrite
        every segment: heavy I/O, a transient near-doubling of disk, and a memory
        spike. Run automatically on every service start it would fire unattended,
        on every replica, with no disk headroom check — and an interrupted rewrite
        leaves both the old and new segments behind, so repeated interruptions
        grow the collection until it can no longer be loaded at all.

        Operators enable it once, with headroom confirmed, via
        VECTOR_STORAGE_RECONCILE_ENABLED. Collections created after the on-disk
        defaults need nothing; this exists only to migrate older ones.
        """
        if not _storage_reconcile_enabled():
            return
        try:
            await self.vector_db_service.reconcile_storage_layout(
                collection_name=collection_name,
                config=self.collection_registry.build_collection_config(
                    embedding_size, sparse_idf
                ),
            )
        except Exception as e:
            self.logger.warning(
                "Storage-layout reconcile skipped for %s: %s",
                collection_name,
                e,
            )

    async def _resync_membership_after_write(
        self,
        virtual_record_id: str,
        record_id: str | None = None,
    ) -> None:
        """Re-apply membership from graph once the points for this VRID exist.

        Membership is bound before embedding and the points land minutes later, so
        any set_payload issued in between (a duplicate attach, a move) is either
        overwritten by the upsert or lands while no points exist yet and silently
        matches nothing. Recomputing after the write closes both windows and is
        idempotent when nothing changed.

        It also closes a window nothing else covers. The per-VRID lock guards the
        membership write and the delete, but not the embed→upsert stretch between
        them — and it cannot, because embedding takes minutes and holding it there
        would block every delete for that VRID. If the record is deleted mid-embed,
        the delete matches no points (none written yet) and removes the VRID→doc
        mapping, and the upsert then lands points for a record that no longer
        exists — unreachable, since the mapping that would find them is gone.

        The orphan branch is entered only after positively confirming that the
        record we just indexed is absent. Inferring it from an empty VRID lookup
        instead would mean every ordinary index could delete the points it just
        wrote if that lookup ever came back empty for an unrelated reason — far
        more blast radius than the stale membership this method otherwise risks.
        """
        try:
            if record_id and await self._record_is_gone(record_id):
                self.logger.warning(
                    "Record %s disappeared while it was being indexed; "
                    "reconciling virtual record %s so its points are not orphaned",
                    record_id,
                    virtual_record_id,
                )
                # Deletes only when no record references the VRID, behind its own
                # confirming re-read.
                await rewrite_or_delete_virtual_record(
                    self.vector_db_service,
                    self.collection_locator,
                    self.graph_provider,
                    virtual_record_id,
                    self.logger,
                )
                return

            await sync_vector_membership(
                self.vector_db_service,
                self.collection_locator,
                self.graph_provider,
                virtual_record_id,
                self.logger,
            )
        except Exception as e:
            # Loud but non-fatal: the points are already written and correct
            # apart from membership, so failing the whole index would discard
            # good work. The per-connector backfill is the designed repair path,
            # so this must be visible enough to trigger one.
            self.logger.error(
                "Post-index membership resync failed for %s: %s — points are "
                "indexed but their connectorIds/recordGroupIds may be stale; "
                "re-run the vector membership backfill for this connector",
                virtual_record_id,
                e,
                exc_info=True,
            )

    async def _record_is_gone(self, record_id: str) -> bool:
        """True only when the graph positively reports the record as absent.

        A lookup failure returns False: not knowing is not the same as knowing it
        is gone, and the caller uses this to decide whether deleting is allowed.
        """
        try:
            return (
                await self.graph_provider.get_document(
                    record_id, CollectionNames.RECORDS.value
                )
            ) is None
        except Exception as e:
            self.logger.warning(
                "Could not confirm whether record %s still exists: %s", record_id, e
            )
            return False

    async def _bind_membership(
        self, virtual_record_id: str, record: Optional["Record"] = None
    ):
        """Resolve VRID membership and bind it for the points about to be written.

        A resolve failure must not degrade to empty arrays: points would be
        indexed with membership that looks legitimately empty, and only a
        backfill could tell the difference later. Fail the indexing attempt
        instead so it retries.
        """
        try:
            connector_ids, record_group_ids = await resolve_vector_membership(
                self.graph_provider, virtual_record_id, current_record=record
            )
        except Exception as e:
            self.logger.error(
                "Failed to resolve vector membership for %s: %s",
                virtual_record_id,
                e,
            )
            raise VectorStoreError(
                f"Could not resolve vector membership for virtual record {virtual_record_id}",
                details={"virtual_record_id": virtual_record_id, "error": str(e)},
            ) from e
        if not connector_ids:
            self.logger.error(
                "Resolved no connectorIds for virtual record %s — points will be "
                "written without membership and will be invisible to instance-scoped "
                "vector filters until backfilled",
                virtual_record_id,
            )
        return set_membership_context(connector_ids, record_group_ids)

    # ------------------------------------------------------------------
    # Embedding model initialisation
    # ------------------------------------------------------------------

    async def get_embedding_model_instance(self) -> bool:
        """Initialise dense embeddings.

        Collection creation is deferred to ``_ensure_collection`` at the point
        a record is actually indexed, since the target collection can depend
        on that record's org/connector under a non-default CollectionStrategy.

        Returns True if multimodal embedding is active.
        """
        self.logger.debug("Getting embedding model")

        ai_models = await self.config_service.get_config(
            config_node_constants.AI_MODELS.value, use_cache=False
        )
        embedding_configs = ai_models["embedding"]
        is_multimodal = False
        provider = None
        configuration = None

        if not embedding_configs:
            dense_embeddings = get_default_embedding_model()
            self.logger.info("Using default embedding model")
        else:
            config = next(
                (c for c in embedding_configs if c.get("isDefault")), embedding_configs[0]
            )
            provider = config["provider"]
            configuration = config["configuration"]
            dense_embeddings = get_embedding_model(provider, config)
            is_multimodal = config.get("isMultimodal")

        try:
            sample = await dense_embeddings.aembed_query("test")
            embedding_size = len(sample)
        except Exception as e:
            raise IndexingError(
                "Failed to get embedding model: " + str(e),
                details={"error": str(e)},
            )

        model_name = (
            getattr(dense_embeddings, "model_name", None)
            or getattr(dense_embeddings, "model", None)
            or getattr(dense_embeddings, "model_id", None)
            or "unknown"
        )
        self.logger.debug(f"Using embedding model: {model_name}, size: {embedding_size}")

        await self._ensure_sparse_embeddings()

        self.dense_embeddings = dense_embeddings
        self.embedding_provider = provider
        self.embedding_size = embedding_size
        self.api_key = (
            configuration.get("apiKey") if configuration and "apiKey" in configuration else None
        )
        self.embedding_endpoint = (
            configuration.get("endpoint") if configuration else None
        )
        self.model_name = model_name
        self.region_name = (
            configuration.get("region") if configuration else None
        )
        # Ollama / OpenAI-compatible / LM Studio multimodal providers need the
        # configured endpoint to reach the right server.
        self.base_url = configuration.get("endpoint") if configuration else None
        if provider == EmbeddingProvider.AWS_BEDROCK.value and configuration:
            self.aws_access_key_id = configuration.get("awsAccessKeyId")
            self.aws_secret_access_key = configuration.get("awsAccessSecretKey")
        self.is_multimodal_embedding = bool(is_multimodal)
        return self.is_multimodal_embedding

    # ------------------------------------------------------------------
    # Orphan / block-level cleanup
    # ------------------------------------------------------------------

    async def _cleanup_orphaned_embeddings_if_needed(
        self,
        record_id: str,
        virtual_record_id: str,
        org_id: str,
        record: Optional["Record"] = None,
    ) -> None:
        """Remove embeddings when the record was deleted and no MD5 duplicate remains."""
        record_doc = await self.graph_provider.get_document(
            record_id, CollectionNames.RECORDS.value
        )
        if record_doc is not None:
            return

        md5_checksum = record.md5_hash if record is not None else None
        if md5_checksum:
            record_type = None
            size_in_bytes = None
            if record is not None:
                record_type = (
                    record.record_type.value
                    if hasattr(record.record_type, "value")
                    else str(record.record_type)
                )
                size_in_bytes = record.size_in_bytes

            # Dedup must never cross org boundaries — see find_duplicate_records.
            duplicate_records = await self.graph_provider.find_duplicate_records(
                record_key=record_id,
                md5_checksum=md5_checksum,
                org_id=org_id,
                record_type=record_type,
                size_in_bytes=size_in_bytes,
            )
            duplicate_records = [r for r in (duplicate_records or []) if r is not None]
            if duplicate_records:
                self.logger.info(
                    f"Record {record_id} not found but {len(duplicate_records)} duplicate(s) "
                    f"with same MD5 exist; keeping embeddings for virtual_record_id "
                    f"{virtual_record_id}"
                )
                return

        self.logger.info(
            f"Record {record_id} not found and no MD5 duplicates in org {org_id}; "
            f"releasing virtual_record_id {virtual_record_id}"
        )
        # Not a raw delete: the MD5 lookup above is org-scoped, so it cannot
        # see a sibling record in another org that shares this VRID (dedup was
        # global before it was scoped, so such pairs exist on upgraded
        # deployments). rewrite_or_delete_virtual_record re-checks the graph
        # for *any* remaining record on this VRID and only deletes when none
        # is left, rewriting membership otherwise.
        await rewrite_or_delete_virtual_record(
            self.vector_db_service,
            self.collection_locator,
            self.graph_provider,
            virtual_record_id,
            self.logger,
        )

    async def delete_blocks_by_ids(
        self, block_ids: set, virtual_record_id: str, collection_name: str
    ) -> None:
        """Delete embeddings for specific block IDs scoped to a virtual record."""
        if not block_ids:
            return
        try:
            filter_dict = await self.vector_db_service.filter_collection(
                must={"blockId": list(block_ids), "virtualRecordId": virtual_record_id}
            )
            await self.vector_db_service.delete_points(collection_name, filter_dict)
            self.logger.info(
                f"✅ Deleted {len(block_ids)} blocks from vector store "
                f"for virtual_record_id {virtual_record_id}"
            )
        except Exception as e:
            self.logger.error(f"Error deleting blocks by IDs: {e}")
            raise EmbeddingError(f"Failed to delete blocks by IDs: {e}")

    # ------------------------------------------------------------------
    # Embeddings deletion (full record)
    # ------------------------------------------------------------------

    async def delete_embeddings(self, virtual_record_id: str, collection_name: str) -> None:
        try:
            filter_dict = await self.vector_db_service.filter_collection(
                must={"virtualRecordId": virtual_record_id}
            )
            await self.vector_db_service.delete_points(collection_name, filter_dict)
            self.logger.debug(
                f"✅ Deleted embeddings for virtual record '{virtual_record_id}'"
            )
        except Exception as e:
            self.logger.error(f"Error deleting embeddings: {e}")
            raise EmbeddingError(f"Failed to delete embeddings: {e}")

    # ------------------------------------------------------------------
    # Image embedding (provider dispatch via MultimodalEmbeddingFactory)
    # ------------------------------------------------------------------

    def _multimodal_provider_config(self) -> MultimodalProviderConfig:
        """Build the config the factory needs from this transformer's state.

        ``normalize_fn`` is bound to ``self._normalize_image_to_base64``
        (rather than the provider defaulting to the module-level utility) so
        tests that patch that instance method keep working unchanged even
        though the normalisation call itself now lives inside the provider.
        """
        return MultimodalProviderConfig(
            provider=self.embedding_provider,
            api_key=self.api_key,
            model_name=self.model_name,
            region_name=self.region_name,
            aws_access_key_id=self.aws_access_key_id,
            aws_secret_access_key=self.aws_secret_access_key,
            base_url=getattr(self, "base_url", None),
            embedding_size=self.embedding_size,
            dense_embeddings=self.dense_embeddings,
            normalize_fn=self._normalize_image_to_base64,
            logger=self.logger,
        )

    def _build_image_points(
        self, image_chunks: List[dict], results: List[ImageEmbeddingResult]
    ) -> List[VectorPoint]:
        """Zip provider results back to their source chunk and build points.

        Skips any index that errored or came back without an embedding —
        provider implementations always return one result per input index
        (never raise), so this is the single place that decides what
        "failed to embed" means for indexing purposes. Also skips any result
        whose dimension doesn't match the collection: mixing dimensions in
        one collection makes cosine similarity meaningless and some vector
        DBs would otherwise reject the whole upsert batch over one bad point.
        """
        points: List[VectorPoint] = []
        for result in results:
            if result.embedding is None:
                if result.error:
                    self.logger.warning(
                        f"Image embedding failed for index {result.index}: {result.error}"
                    )
                continue
            if self.embedding_size is not None and len(result.embedding) != self.embedding_size:
                self.logger.error(
                    f"Image embedding dimension mismatch for index {result.index}: "
                    f"got {len(result.embedding)}, expected {self.embedding_size}. Skipping point."
                )
                continue
            chunk = image_chunks[result.index]
            points.append(
                VectorPoint(
                    id=str(uuid.uuid4()),
                    dense_vector=result.embedding,
                    payload=vector_point_payload(
                        chunk.get("metadata", {}),
                        chunk.get("description", ""),
                    ),
                )
            )
        return points

    async def _process_image_embeddings(
        self, image_chunks: List[dict], image_base64s: List[str], record_id: str = ""
    ) -> List[VectorPoint]:
        """Embed images via the provider the factory resolves for this config.

        Guard: skip entirely if the record was deleted mid-flight.
        """
        record_doc = await self.graph_provider.get_document(
            record_id, CollectionNames.RECORDS.value
        )
        if record_doc is None:
            self.logger.warning(
                f"Record {record_id} not found in database, skipping image embedding"
            )
            return []

        if not image_base64s:
            return []

        provider = MultimodalEmbeddingFactory.create(self._multimodal_provider_config())
        if provider is None or not provider.supports_multimodal():
            self.logger.warning(
                f"Unsupported embedding provider for images: {self.embedding_provider}"
            )
            return []

        results = await provider.embed_images(image_base64s)
        return self._build_image_points(image_chunks, results)

    async def _store_image_points(
        self, points: List[VectorPoint], collection_name: str
    ) -> None:
        if not points:
            self.logger.info("No image embeddings to upsert.")
            return
        start = time.perf_counter()
        # Batch image upserts to avoid holding all VectorPoints in one call
        batch_size = 500
        for i in range(0, len(points), batch_size):
            await self.vector_db_service.upsert_points(
                collection_name=collection_name, points=points[i:i + batch_size]
            )
        self.logger.info(
            f"✅ Stored {len(points)} image points in {time.perf_counter() - start:.2f}s"
        )

    # ------------------------------------------------------------------
    # Core document upsert (unified, all providers)
    # ------------------------------------------------------------------

    def _is_local_cpu_embedding(self) -> bool:
        return is_local_cpu_embedding_provider(self.embedding_provider)

    async def _compute_sparse_embeddings(
        self, texts: List[str]
    ) -> List[SparseVector | None]:
        """Compute BM25 sparse vectors; returns list of None when not supported."""
        embedder = await self._ensure_sparse_embeddings()
        if embedder is None:
            return [None] * len(texts)
        return await embedder.embed_documents(texts)

    async def _embed_and_upsert_documents(
        self, documents: List[Document], record_id: str, collection_name: str
    ) -> None:
        """Embed a batch of LangChain Documents and upsert to the vector DB.

        Guard: aborts if the record was deleted mid-flight (race condition fix
        restored from commit 839a29499).
        """
        # Record-existence guard before upsert
        record_doc = await self.graph_provider.get_document(
            record_id, CollectionNames.RECORDS.value
        )
        if record_doc is None:
            self.logger.warning(
                f"Record {record_id} not found before upsert — skipping batch"
            )
            return

        texts = [doc.page_content for doc in documents]

        embedding_timeout = (
            _LOCAL_EMBEDDING_BATCH_TIMEOUT_S
            if self._is_local_cpu_embedding()
            else _REMOTE_EMBEDDING_BATCH_TIMEOUT_S
        )
        try:
            dense_embeddings = await asyncio.wait_for(
                self.dense_embeddings.aembed_documents(texts),
                timeout=embedding_timeout,
            )
        except asyncio.TimeoutError:
            raise EmbeddingError(
                f"Dense embedding timed out after {embedding_timeout}s "
                f"for batch of {len(texts)} texts (record {record_id})"
            )

        # Sparse embeddings (provider-dependent)
        sparse_embeddings = await self._compute_sparse_embeddings(texts)

        points: List[VectorPoint] = [
            VectorPoint(
                id=str(uuid.uuid4()),
                dense_vector=dense,
                sparse_vector=sparse,
                payload=vector_point_payload(doc.metadata, doc.page_content),
            )
            for doc, dense, sparse in zip(documents, dense_embeddings, sparse_embeddings)
        ]
        await self.vector_db_service.upsert_points(
            collection_name=collection_name, points=points
        )

    async def _process_document_chunks(
        self,
        langchain_document_chunks: List[Document],
        record_id: str,
        collection_name: str,
    ) -> None:
        self.logger.debug(
            f"⏱️ Embedding {len(langchain_document_chunks)} document chunks"
        )
        use_local_sequential = self._is_local_cpu_embedding()
        batch_size = (
            _LOCAL_CPU_DOCUMENT_BATCH_SIZE if use_local_sequential else _DEFAULT_DOCUMENT_BATCH_SIZE
        )

        async def process_batch(batch_start: int, batch: List[Document]) -> int:
            try:
                await self._embed_and_upsert_documents(batch, record_id, collection_name)
                return len(batch)
            except Exception as e:
                self.logger.warning(f"Batch at {batch_start} failed: {e}")
                raise

        batches = [
            (i, langchain_document_chunks[i:i + batch_size])
            for i in range(0, len(langchain_document_chunks), batch_size)
        ]

        if use_local_sequential:
            for idx, (start, batch) in enumerate(batches):
                try:
                    await process_batch(start, batch)
                except Exception as e:
                    raise VectorStoreError(
                        f"Failed to store batch {idx}: {e}",
                        details={"error": str(e), "batch_index": idx},
                    )
        else:
            semaphore = asyncio.Semaphore(_DEFAULT_CONCURRENCY_LIMIT)

            async def limited(start, batch):
                async with semaphore:
                    return await process_batch(start, batch)

            results = await asyncio.gather(
                *[limited(s, b) for s, b in batches], return_exceptions=True
            )
            for idx, result in enumerate(results):
                if isinstance(result, Exception):
                    raise VectorStoreError(
                        f"Failed to store batch {idx}: {result}",
                        details={"error": str(result), "batch_index": idx},
                    )

    # ------------------------------------------------------------------
    # Embedding creation entry point
    # ------------------------------------------------------------------

    async def _create_embeddings(
        self,
        chunks: List,
        record_id: str,
        virtual_record_id: str,
        collection_name: str,
    ) -> None:
        if not chunks:
            raise EmbeddingError("No chunks provided for embedding creation")

        langchain_docs: List[Document] = []
        image_chunks: List[dict] = []

        for chunk in chunks:
            if isinstance(chunk, Document):
                langchain_docs.append(chunk)
            else:
                image_chunks.append(chunk)

        # Delete existing embeddings first (full replace, non-reconciliation path)
        await self.delete_embeddings(virtual_record_id, collection_name)

        self.logger.debug(
            f"📊 Processing {len(langchain_docs)} text + {len(image_chunks)} image chunks"
        )

        if image_chunks:
            image_base64s = [c.get("image_uri") for c in image_chunks]
            points = await self._process_image_embeddings(image_chunks, image_base64s, record_id)
            await self._store_image_points(points, collection_name)

        if langchain_docs:
            try:
                await self._process_document_chunks(langchain_docs, record_id, collection_name)
            except Exception as e:
                raise VectorStoreError(
                    "Failed to store documents in vector store: " + str(e),
                    details={"error": str(e)},
                )

        self.logger.info(f"✅ Embeddings created and stored for record '{record_id}'")

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    async def index_documents(
        self,
        block_containers: BlocksContainer,
        org_id: str,
        record_id: str,
        virtual_record_id: str,
        block_ids_to_delete: set | None = None,
        is_reconciliation: bool = False,
        record: Optional["Record"] = None,
    ) -> bool | None:
        try:
            is_multimodal_embedding = await self.get_embedding_model_instance()
            collection_name = await self._ensure_collection(org_id, record, self.embedding_size)
        except Exception as e:
            raise IndexingError(
                "Failed to get embedding model instance: " + str(e),
                details={"error": str(e)},
            )

        # No LLM is resolved here any more: the only thing it was used for was
        # describing images, which now happens before the record is stored
        # (`ImageDescriber`). Text-only indexing no longer fails on a
        # deployment with no chat model configured.
        blocks = block_containers.blocks
        block_groups = block_containers.block_groups

        tokens = await self._bind_membership(virtual_record_id, record)
        try:
            # On reconciliation: always refresh the record summary first
            if block_ids_to_delete or is_reconciliation:
                summary_block_id_set = {
                    f"{virtual_record_id}{RECORD_SUMMARY_BLOCK_ID_SUFFIX}"
                }
                await self.delete_blocks_by_ids(summary_block_id_set, virtual_record_id, collection_name)

                if record is not None:
                    semantic_metadata = getattr(record, "semantic_metadata", None)
                    if semantic_metadata:
                        summary_doc = self._build_record_summary_document(
                            record_id, virtual_record_id, org_id, semantic_metadata
                        )
                        if summary_doc:
                            await self._process_document_chunks([summary_doc], record_id, collection_name)

            if not blocks and not block_groups:
                if block_ids_to_delete:
                    await self.delete_blocks_by_ids(block_ids_to_delete, virtual_record_id, collection_name)
                await self._cleanup_orphaned_embeddings_if_needed(
                    record_id, virtual_record_id, org_id, record
                )
                return None

            text_blocks = []
            image_blocks = []
            table_blocks = []
            sql_row_blocks = []
            code_blocks = []

            for block in blocks:
                block_type = (
                    str(block.type.value).lower()
                    if hasattr(block.type, "value")
                    else str(block.type).lower()
                )
                if block_type == "code":
                    code_blocks.append(block)
                elif block_type in ["text", "paragraph", "textsection", "heading", "quote"]:
                    text_blocks.append(block)
                elif (
                    block_type in ["image", "drawing"]
                    and isinstance(block.data, dict)
                    and block.data.get("uri")
                ):
                    image_blocks.append(block)
                elif block_type == "table_row":
                    sub_type = ""
                    if hasattr(block, "sub_type") and block.sub_type:
                        sub_type = (
                            str(block.sub_type.value).lower()
                            if hasattr(block.sub_type, "value")
                            else str(block.sub_type).lower()
                        )
                    if sub_type in ["sql_table", "sql_view"]:
                        sql_row_blocks.append(block)
                    else:
                        table_blocks.append(block)
                elif block_type in ["table", "table_cell"]:
                    table_blocks.append(block)

            self.logger.debug(
                f"📊 Processing {len(blocks)} blocks and {len(block_groups)} block_groups"
            )
            self.logger.debug(
                f"Block classification: text={len(text_blocks)}, image={len(image_blocks)}, "
                f"table={len(table_blocks)}, sql_row={len(sql_row_blocks)}, "
                f"code={len(code_blocks)}"
            )

            documents_to_embed: List = []

            # ── Code blocks ──
            if code_blocks:
                documents_to_embed.extend(
                    _build_code_documents(code_blocks, virtual_record_id, org_id)
                )
                self.logger.info("✅ Added code documents for embedding")

            # ── Text blocks ──
            if text_blocks:
                try:
                    text_documents = await asyncio.wait_for(
                        asyncio.to_thread(
                            _process_text_blocks, text_blocks, virtual_record_id, org_id
                        ),
                        timeout=_TEXT_PROCESSING_TIMEOUT_S,
                    )
                    documents_to_embed.extend(text_documents)
                    self.logger.debug("✅ Added text documents for embedding")
                except asyncio.TimeoutError:
                    raise DocumentProcessingError(
                        f"Text processing timed out after {_TEXT_PROCESSING_TIMEOUT_S}s "
                        f"for record {record_id} ({len(text_blocks)} blocks)",
                        details={"record_id": record_id, "block_count": len(text_blocks)},
                    )
                except Exception as e:
                    raise DocumentProcessingError(
                        "Failed to create text document objects: " + str(e),
                        details={"error": str(e)},
                    )

            # ── Image blocks ──
            if image_blocks:
                try:
                    valid_image_blocks = [
                        b for b in image_blocks
                        if isinstance(b.data, dict) and b.data.get("uri")
                    ]
                    if valid_image_blocks:
                        # `ImageDescriber` already wrote the prose before this
                        # record was stored (see `SinkOrchestrator.index`), so
                        # both branches read one description instead of each
                        # deriving its own -- and the text-embedding branch no
                        # longer pays a second vision call per image.
                        for block in valid_image_blocks:
                            description = self._image_block_description(block)
                            point_metadata = {
                                "virtualRecordId": virtual_record_id,
                                "blockId": block.id,
                                "blockIndex": block.index,
                                "orgId": org_id,
                                "isBlock": True,
                                "isBlockGroup": False,
                                "blockType": BlockType.IMAGE.value,
                                "isImage": True,
                            }
                            if is_multimodal_embedding:
                                documents_to_embed.append({
                                    "image_uri": block.data.get("uri"),
                                    "description": description,
                                    "metadata": point_metadata,
                                })
                            elif description:
                                # Text-only embeddings: the description IS the
                                # image as far as this index is concerned, so
                                # an image without one has nothing to embed.
                                documents_to_embed.append(
                                    Document(page_content=description, metadata=point_metadata),
                                )
                except Exception as e:
                    raise DocumentProcessingError(
                        "Failed to create image document objects: " + str(e),
                        details={"error": str(e)},
                    )

            # ── Block groups (SQL tables/views and regular tables) ──
            for block_group in block_groups:
                block_group_type = (
                    str(block_group.type.value).lower()
                    if hasattr(block_group.type, "value")
                    else str(block_group.type).lower()
                )
                sub_type = ""
                if hasattr(block_group, "sub_type") and block_group.sub_type:
                    sub_type = (
                        str(block_group.sub_type.value).lower()
                        if hasattr(block_group.sub_type, "value")
                        else str(block_group.sub_type).lower()
                    )

                if block_group_type in ["table", "view"] and sub_type in [
                    "sql_table", "sql_view"
                ]:
                    block_data = block_group.data or {}
                    fqn = block_data.get("fqn", "")
                    sql_base_metadata = {
                        "virtualRecordId": virtual_record_id,
                        "blockId": block_group.id,
                        "orgId": org_id,
                        "isBlock": False,
                        "isBlockGroup": True,
                        "blockType": sub_type,
                    }

                    if sub_type == "sql_table":
                        ddl = block_data.get("ddl", "")
                        table_summary = block_data.get("table_summary", "")
                        if ddl:
                            parts = []
                            if table_summary:
                                parts.append(f"/* Table Description:\n{table_summary}\n*/")
                            parts.append(ddl)
                            documents_to_embed.append(
                                Document(
                                    page_content="\n\n".join(parts),
                                    metadata=sql_base_metadata,
                                )
                            )
                            self.logger.info(
                                f"📊 Added SQL TABLE DDL+Summary for embedding: {fqn}"
                            )

                    elif sub_type == "sql_view":
                        definition = block_data.get("definition", "") or ""
                        source_tables = block_data.get("source_tables", [])
                        source_tables_summary = block_data.get("source_tables_summary", "")
                        source_table_ddls = block_data.get("source_table_ddls", {})
                        comment = block_data.get("comment", "") or ""
                        is_secure = block_data.get("is_secure", False)

                        view_context_parts = [f"-- View: {fqn}"]
                        if is_secure:
                            view_context_parts.append("-- Note: This is a secure view")
                        if source_tables:
                            view_context_parts.append(
                                f"-- Source Tables: {', '.join(source_tables)}"
                            )
                        if comment:
                            view_context_parts.append(f"-- Comment: {comment}")
                        if source_tables_summary:
                            view_context_parts.append(
                                f"-- Source Table Schemas:\n{source_tables_summary}"
                            )
                        if source_table_ddls:
                            view_context_parts.append("-- Source Table DDLs:")
                            for t_fqn, ddl_text in source_table_ddls.items():
                                view_context_parts.append(f"-- {t_fqn}:\n{ddl_text}")
                        if definition:
                            view_context_parts.append(f"\n{definition}")

                        view_context = "\n".join(view_context_parts)
                        if len(view_context.strip()) > len(f"-- View: {fqn}"):
                            documents_to_embed.append(
                                Document(
                                    page_content=view_context,
                                    metadata=sql_base_metadata,
                                )
                            )
                            self.logger.info(
                                f"📊 Added SQL VIEW for embedding: {fqn}"
                            )
                        else:
                            self.logger.warning(
                                f"⚠️ SQL VIEW {fqn} has no embeddable content, skipping"
                            )

                elif block_group_type == "table":
                    table_data = block_group.data
                    if table_data:
                        table_summary = table_data.get("table_summary", "")
                        if table_summary:
                            documents_to_embed.append(
                                Document(
                                    page_content=table_summary,
                                    metadata={
                                        "virtualRecordId": virtual_record_id,
                                        "blockId": block_group.id,
                                        "orgId": org_id,
                                        "isBlock": False,
                                        "isBlockGroup": True,
                                        "blockType": BlockType.TABLE.value,
                                    },
                                )
                            )

            # ── SQL row blocks ──
            sql_rows_embedded = 0
            for block in sql_row_blocks:
                block_data = block.data or {}
                row_text = block_data.get("row_natural_language_text", "")
                if row_text:
                    documents_to_embed.append(
                        Document(
                            page_content=row_text,
                            metadata={
                                "virtualRecordId": virtual_record_id,
                                "blockId": block.id,
                                "orgId": org_id,
                                "isBlock": True,
                                "isBlockGroup": False,
                                "blockType": BlockType.TABLE_ROW.value,
                            },
                        )
                    )
                    sql_rows_embedded += 1
            if sql_rows_embedded > 0:
                self.logger.debug(f"📊 Added {sql_rows_embedded} SQL row(s) for embedding")

            # ── Regular table blocks ──
            for block in table_blocks:
                block_type = (
                    str(block.type.value).lower()
                    if hasattr(block.type, "value")
                    else str(block.type).lower()
                )
                if block_type == "table":
                    table_data = block.data
                    if table_data:
                        table_summary = table_data.get("table_summary", "")
                        if table_summary:
                            documents_to_embed.append(
                                Document(
                                    page_content=table_summary,
                                    metadata={
                                        "virtualRecordId": virtual_record_id,
                                        "blockId": block.id,
                                        "orgId": org_id,
                                        "isBlock": False,
                                        "isBlockGroup": True,
                                        "blockType": BlockType.TABLE.value,
                                    },
                                )
                            )
                elif block_type == "table_row":
                    table_data = block.data
                    if table_data:
                        row_text = table_data.get("row_natural_language_text", "")
                        if row_text:
                            documents_to_embed.append(
                                Document(
                                    page_content=row_text,
                                    metadata={
                                        "virtualRecordId": virtual_record_id,
                                        "blockId": block.id,
                                        "orgId": org_id,
                                        "isBlock": True,
                                        "isBlockGroup": False,
                                        "blockType": BlockType.TABLE_ROW.value,
                                    },
                                )
                            )

            # Record summary (only on fresh full index, not reconciliation/partial update)
            if record is not None and not (is_reconciliation or block_ids_to_delete):
                await self._refresh_record_summary_documents(
                    documents_to_embed, record, org_id, record_id, virtual_record_id
                )

            if not documents_to_embed:
                self.logger.warning("⚠️ No documents to embed after filtering by block type")
                if block_ids_to_delete:
                    await self.delete_blocks_by_ids(block_ids_to_delete, virtual_record_id, collection_name)
                await self._cleanup_orphaned_embeddings_if_needed(
                    record_id, virtual_record_id, org_id, record
                )
                return True

            # ── Embed and store ──
            if is_reconciliation:
                # Partial update: no full delete; only changed blocks
                langchain_docs = [d for d in documents_to_embed if isinstance(d, Document)]
                image_chunks = [d for d in documents_to_embed if not isinstance(d, Document)]
                if langchain_docs:
                    await self._process_document_chunks(langchain_docs, record_id, collection_name)
                if image_chunks:
                    image_base64s = [c.get("image_uri") for c in image_chunks]
                    points = await self._process_image_embeddings(
                        image_chunks, image_base64s, record_id
                    )
                    await self._store_image_points(points, collection_name)
            else:
                await self._create_embeddings(documents_to_embed, record_id, virtual_record_id, collection_name)

            if block_ids_to_delete:
                self.logger.debug(f"📊 Deleting {len(block_ids_to_delete)} removed blocks")
                await self.delete_blocks_by_ids(block_ids_to_delete, virtual_record_id, collection_name)

            self.logger.debug(
                f"✅ Indexing complete for record {record_id}: "
                f"{len(documents_to_embed)} documents"
            )

            await self._cleanup_orphaned_embeddings_if_needed(
                record_id, virtual_record_id, org_id, record
            )
            await self._resync_membership_after_write(virtual_record_id, record_id)
            return True

        except (IndexingError, VectorStoreError, DocumentProcessingError, EmbeddingError):
            raise
        except Exception as e:
            raise IndexingError(
                f"Unexpected error during indexing: {str(e)}",
                details={"error_type": type(e).__name__},
            )
        finally:
            reset_membership_context(tokens)
