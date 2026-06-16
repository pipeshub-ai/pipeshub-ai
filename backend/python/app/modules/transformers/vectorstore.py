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
import re
import time
import uuid
from typing import Any, List, Optional

import httpx
import spacy
from langchain_core.documents import Document
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage
from spacy.language import Language
from spacy.tokens import Doc

from app.config.constants.arangodb import CollectionNames
from app.config.constants.service import config_node_constants
from app.exceptions.indexing_exceptions import (
    DocumentProcessingError,
    EmbeddingError,
    IndexingError,
    MetadataProcessingError,
    VectorStoreError,
)
from app.models.blocks import BlocksContainer, SemanticMetadata
from app.models.entities import Record
from app.modules.extraction.prompt_template import prompt_for_image_description
from app.modules.transformers.transformer import TransformContext, Transformer
from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
from app.services.vector_db.interface.vector_db import IVectorDBService
from app.services.vector_db.models import (
    CollectionConfig,
    SparseVector,
    VectorPoint,
)
from app.services.vector_db.sparse_embeddings import SparseEmbedder
from app.utils.aimodels import (
    EmbeddingProvider,
    get_default_embedding_model,
    get_embedding_model,
)
from app.utils.llm import get_llm

# Module-level shared spaCy pipeline to avoid repeated heavy loads
_SHARED_NLP: Optional[Language] = None

RECORD_SUMMARY_BLOCK_ID_SUFFIX = "_summary"


def _get_shared_nlp() -> Language:
    cached = getattr(_get_shared_nlp, "_cached_nlp", None)
    if cached is None:
        nlp = spacy.load("en_core_web_sm")
        if "sentencizer" not in nlp.pipe_names:
            nlp.add_pipe("sentencizer", before="parser")
        if "custom_sentence_boundary" not in nlp.pipe_names:
            try:
                nlp.add_pipe("custom_sentence_boundary", after="sentencizer")
            except Exception:
                pass
        setattr(_get_shared_nlp, "_cached_nlp", nlp)
        return nlp
    return cached


LENGTH_THRESHOLD = 2
_DEFAULT_DOCUMENT_BATCH_SIZE = 50
_DEFAULT_CONCURRENCY_LIMIT = 5
_LOCAL_CPU_DOCUMENT_BATCH_SIZE = 50


class VectorStore(Transformer):

    def __init__(
        self,
        logger,
        config_service,
        graph_provider: IGraphDBProvider,
        collection_name: str,
        vector_db_service: IVectorDBService,
    ) -> None:
        super().__init__()
        self.logger = logger
        self.config_service = config_service
        self.graph_provider = graph_provider
        self.nlp = _get_shared_nlp()
        self.vector_db_service = vector_db_service
        self.collection_name = collection_name

        self.dense_embeddings = None
        self.api_key = None
        self.model_name = None
        self.embedding_provider = None
        self.is_multimodal_embedding = False
        self.region_name = None
        self.aws_access_key_id = None
        self.aws_secret_access_key = None

        self._capabilities = self.vector_db_service.get_capabilities()

        # Sparse embeddings — only for providers that store client-side sparse vectors.
        # SparseEmbedder lazy-initialises in a worker thread on first use.
        self._sparse_embedder: Optional[SparseEmbedder] = None
        self._sparse_embedder_lock: Optional[asyncio.Lock] = None

    # ------------------------------------------------------------------
    # Sparse embedding lazy initialisation
    # ------------------------------------------------------------------

    async def _ensure_sparse_embeddings(self) -> Optional[SparseEmbedder]:
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

    @Language.component("custom_sentence_boundary")
    def custom_sentence_boundary(doc) -> Doc:  # noqa: N805
        for token in doc[:-1]:
            next_token = doc[token.i + 1]
            if token.like_num and next_token.text == ".":
                next_token.is_sent_start = False
            elif (
                token.text.lower()
                in [
                    "mr", "mrs", "dr", "ms", "prof", "sr", "jr", "inc",
                    "ltd", "co", "etc", "vs", "fig", "et", "al", "e.g",
                    "i.e", "vol", "pg", "pp", "pvt", "llc", "llp", "lp",
                    "ll", "corp",
                ]
                and next_token.text == "."
            ):
                next_token.is_sent_start = False
            elif (
                (token.like_num and next_token.text == "." and len(token.text) <= LENGTH_THRESHOLD)
                or (len(token.text) == 1 and token.text.isalpha() and next_token.text == ".")
                or token.text in ["•", "∙", "·", "○", "●", "-", "–", "—"]
            ):
                next_token.is_sent_start = False
            elif (
                token.text.isupper()
                and len(token.text) > 1
                and not any(c.isdigit() for c in token.text)
            ):
                if next_token.i < len(doc) - 1:
                    next_token.is_sent_start = False
            elif token.text == "." and next_token.text == ".":
                next_token.is_sent_start = False
        return doc

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

    async def _normalize_image_to_base64(self, image_uri: str) -> str | None:
        try:
            if not image_uri or not isinstance(image_uri, str):
                return None
            uri = image_uri.strip()
            if uri.startswith("data:"):
                comma_index = uri.find(",")
                if comma_index == -1:
                    return None
                b64_part = uri[comma_index + 1:].strip()
                missing = (-len(b64_part)) % 4
                if missing:
                    b64_part += "=" * missing
                return b64_part
            candidate = uri.replace("\n", "").replace("\r", "").replace(" ", "")
            if not re.fullmatch(r"[A-Za-z0-9+/=_-]+", candidate):
                return None
            missing = (-len(candidate)) % 4
            if missing:
                candidate += "=" * missing
            return candidate
        except Exception:
            return None

    async def describe_image_async(self, base64_string: str, vlm: BaseChatModel) -> str:
        message = HumanMessage(
            content=[
                {"type": "text", "text": prompt_for_image_description},
                {"type": "image_url", "image_url": {"url": base64_string}},
            ]
        )
        response = await vlm.ainvoke([message])
        return response.content

    async def describe_images(
        self, base64_images: List[str], vlm: BaseChatModel
    ) -> List[dict]:
        concurrency_limit = 10
        semaphore = asyncio.Semaphore(concurrency_limit)

        async def describe(i: int, b64: str) -> dict:
            async with semaphore:
                try:
                    desc = await self.describe_image_async(b64, vlm)
                    return {"index": i, "success": True, "description": desc.strip()}
                except Exception as e:
                    return {"index": i, "success": False, "error": str(e)}

        tasks = [describe(i, img) for i, img in enumerate(base64_images)]
        return await asyncio.gather(*tasks)

    # ------------------------------------------------------------------
    # Collection initialisation
    # ------------------------------------------------------------------

    async def _get_existing_vector_dimension(self, collection_name: str) -> Optional[int]:
        info = await self.vector_db_service.get_collection_info(collection_name)
        if info.exists:
            return info.dense_dimension
        return None

    async def _initialize_collection(
        self, embedding_size: int = 1024, sparse_idf: bool = False
    ) -> None:
        existing_dim = await self._get_existing_vector_dimension(self.collection_name)
        if existing_dim is not None:
            if existing_dim != embedding_size:
                raise VectorStoreError(
                    f"Embedding model dimension mismatch: collection "
                    f"'{self.collection_name}' was created with dimension {existing_dim} "
                    f"but the current model produces dimension {embedding_size}. "
                    f"Re-index by deleting the collection and re-running indexing, "
                    f"or switch back to the original embedding model.",
                    details={
                        "collection": self.collection_name,
                        "existing_dim": existing_dim,
                        "required_dim": embedding_size,
                    },
                )
            else:
                self.logger.debug(
                    f"Collection '{self.collection_name}' exists with correct dimension {embedding_size}."
                )
                return

        try:
            await self.vector_db_service.create_collection(
                collection_name=self.collection_name,
                config=CollectionConfig(
                    embedding_size=embedding_size,
                    sparse_idf=sparse_idf,
                    enable_sparse=self._capabilities.supports_sparse_vectors,
                ),
            )
            self.logger.info(f"✅ Created collection '{self.collection_name}'")
            for field_name, schema in [
                ("metadata.virtualRecordId", {"type": "keyword"}),
                ("metadata.orgId", {"type": "keyword"}),
            ]:
                await self.vector_db_service.create_index(
                    collection_name=self.collection_name,
                    field_name=field_name,
                    field_schema=schema,
                )
        except Exception as e:
            self.logger.error(f"❌ Error creating collection '{self.collection_name}': {e}")
            raise VectorStoreError(
                "Failed to create collection",
                details={"collection": self.collection_name, "error": str(e)},
            )

    # ------------------------------------------------------------------
    # Embedding model initialisation
    # ------------------------------------------------------------------

    async def get_embedding_model_instance(self) -> bool:
        """Initialise dense embeddings and ensure collection exists.

        Returns True if multimodal embedding is active.
        """
        self.logger.info("Getting embedding model")

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
            sample = dense_embeddings.embed_query("test")
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
        self.logger.info(f"Using embedding model: {model_name}, size: {embedding_size}")

        await self._ensure_sparse_embeddings()
        await self._initialize_collection(embedding_size=embedding_size)

        self.dense_embeddings = dense_embeddings
        self.embedding_provider = provider
        self.api_key = (
            configuration.get("apiKey") if configuration and "apiKey" in configuration else None
        )
        self.model_name = model_name
        self.region_name = (
            configuration.get("region") if configuration else None
        )
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

            duplicate_records = await self.graph_provider.find_duplicate_records(
                record_key=record_id,
                md5_checksum=md5_checksum,
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
            f"Record {record_id} not found and no MD5 duplicates; "
            f"deleting embeddings for virtual_record_id {virtual_record_id}"
        )
        await self.delete_embeddings(virtual_record_id)

    async def delete_blocks_by_ids(
        self, block_ids: set, virtual_record_id: str
    ) -> None:
        """Delete embeddings for specific block IDs scoped to a virtual record."""
        if not block_ids:
            return
        try:
            filter_dict = await self.vector_db_service.filter_collection(
                must={"blockId": list(block_ids), "virtualRecordId": virtual_record_id}
            )
            await self.vector_db_service.delete_points(self.collection_name, filter_dict)
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

    async def delete_embeddings(self, virtual_record_id: str) -> None:
        try:
            filter_dict = await self.vector_db_service.filter_collection(
                must={"virtualRecordId": virtual_record_id}
            )
            await self.vector_db_service.delete_points(self.collection_name, filter_dict)
            self.logger.info(
                f"✅ Deleted embeddings for virtual record '{virtual_record_id}'"
            )
        except Exception as e:
            self.logger.error(f"Error deleting embeddings: {e}")
            raise EmbeddingError(f"Failed to delete embeddings: {e}")

    # ------------------------------------------------------------------
    # Image embedding helpers (provider-specific)
    # ------------------------------------------------------------------

    async def _process_image_embeddings_cohere(
        self, image_chunks: List[dict], image_base64s: List[str]
    ) -> List[VectorPoint]:
        import cohere

        co = cohere.ClientV2(api_key=self.api_key)
        concurrency_limit = 10
        semaphore = asyncio.Semaphore(concurrency_limit)

        async def embed_single(i: int, image_base64: str) -> Optional[VectorPoint]:
            image_input = {
                "content": [{"type": "image_url", "image_url": {"url": image_base64}}]
            }
            try:
                loop = asyncio.get_running_loop()
                response = await loop.run_in_executor(
                    None,
                    lambda: co.embed(
                        model=self.model_name,
                        input_type="image",
                        embedding_types=["float"],
                        inputs=[image_input],
                    ),
                )
                chunk = image_chunks[i]
                embedding = response.embeddings.float[0]
                return VectorPoint(
                    id=str(uuid.uuid4()),
                    dense_vector=embedding,
                    payload={
                        "metadata": chunk.get("metadata", {}),
                        "page_content": chunk.get("image_uri", ""),
                    },
                )
            except Exception as e:
                if "image size must be at most" in str(e):
                    self.logger.warning(f"Skipping image {i}: {e}")
                    return None
                raise

        async def limited(i, b64):
            async with semaphore:
                return await embed_single(i, b64)

        results = await asyncio.gather(
            *[limited(i, b64) for i, b64 in enumerate(image_base64s)],
            return_exceptions=True,
        )
        return [r for r in results if isinstance(r, VectorPoint)]

    async def _process_image_embeddings_voyage(
        self, image_chunks: List[dict], image_base64s: List[str]
    ) -> List[VectorPoint]:
        batch_size = getattr(self.dense_embeddings, "batch_size", 7)
        concurrency_limit = 5
        semaphore = asyncio.Semaphore(concurrency_limit)

        async def process_batch(batch_start: int, batch_imgs: List[str]) -> List[VectorPoint]:
            async with semaphore:
                try:
                    embeddings = await self.dense_embeddings.aembed_documents(batch_imgs)
                    return [
                        VectorPoint(
                            id=str(uuid.uuid4()),
                            dense_vector=embedding,
                            payload={
                                "metadata": image_chunks[batch_start + i].get("metadata", {}),
                                "page_content": image_chunks[batch_start + i].get("image_uri", ""),
                            },
                        )
                        for i, embedding in enumerate(embeddings)
                    ]
                except Exception as e:
                    self.logger.warning(f"Voyage batch {batch_start} failed: {e}")
                    return []

        batches = [
            (start, image_base64s[start:start + batch_size])
            for start in range(0, len(image_base64s), batch_size)
        ]
        results = await asyncio.gather(*[process_batch(s, imgs) for s, imgs in batches])
        points: List[VectorPoint] = []
        for r in results:
            if isinstance(r, list):
                points.extend(r)
        return points

    async def _process_image_embeddings_bedrock(
        self, image_chunks: List[dict], image_base64s: List[str]
    ) -> List[VectorPoint]:
        import json

        import boto3
        from botocore.exceptions import ClientError, NoCredentialsError

        client_kwargs: dict = {"service_name": "bedrock-runtime"}
        if self.aws_access_key_id and self.aws_secret_access_key and self.region_name:
            client_kwargs.update(
                {
                    "aws_access_key_id": self.aws_access_key_id,
                    "aws_secret_access_key": self.aws_secret_access_key,
                    "region_name": self.region_name,
                }
            )
        try:
            bedrock = boto3.client(**client_kwargs)
        except NoCredentialsError as e:
            raise EmbeddingError("AWS credentials not found for Bedrock image embeddings.") from e

        concurrency_limit = 10
        semaphore = asyncio.Semaphore(concurrency_limit)

        async def embed_single(i: int, image_ref: str) -> Optional[VectorPoint]:
            normalized = await self._normalize_image_to_base64(image_ref)
            if not normalized:
                return None
            try:
                loop = asyncio.get_running_loop()
                response = await loop.run_in_executor(
                    None,
                    lambda: bedrock.invoke_model(
                        modelId=self.model_name,
                        body=json.dumps({
                            "inputImage": normalized,
                            "embeddingConfig": {"outputEmbeddingLength": 1024},
                        }),
                        contentType="application/json",
                        accept="application/json",
                    ),
                )
                body = json.loads(response["body"].read())
                return VectorPoint(
                    id=str(uuid.uuid4()),
                    dense_vector=body["embedding"],
                    payload={
                        "metadata": image_chunks[i].get("metadata", {}),
                        "page_content": image_chunks[i].get("image_uri", ""),
                    },
                )
            except (NoCredentialsError, ClientError) as e:
                self.logger.warning(f"Bedrock embed failed for index {i}: {e}")
                return None

        async def limited(i, ref):
            async with semaphore:
                return await embed_single(i, ref)

        results = await asyncio.gather(
            *[limited(i, ref) for i, ref in enumerate(image_base64s)],
            return_exceptions=True,
        )
        return [r for r in results if isinstance(r, VectorPoint)]

    async def _process_image_embeddings_jina(
        self, image_chunks: List[dict], image_base64s: List[str]
    ) -> List[VectorPoint]:
        batch_size = 32
        concurrency_limit = 5
        semaphore = asyncio.Semaphore(concurrency_limit)

        async def process_batch(
            client: httpx.AsyncClient, batch_start: int, batch_imgs: List[str]
        ) -> List[VectorPoint]:
            async with semaphore:
                try:
                    normalized = await asyncio.gather(
                        *[self._normalize_image_to_base64(img) for img in batch_imgs]
                    )
                    valid = [(batch_start + j, n) for j, n in enumerate(normalized) if n]
                    if not valid:
                        return []
                    resp = await client.post(
                        "https://api.jina.ai/v1/embeddings",
                        headers={
                            "Content-Type": "application/json",
                            "Authorization": f"Bearer {self.api_key}",
                        },
                        json={
                            "model": self.model_name,
                            "input": [{"image": n} for _, n in valid],
                        },
                    )
                    data = resp.json().get("data", [])
                    return [
                        VectorPoint(
                            id=str(uuid.uuid4()),
                            dense_vector=item["embedding"],
                            payload={
                                "metadata": image_chunks[valid[i][0]].get("metadata", {}),
                                "page_content": image_chunks[valid[i][0]].get("image_uri", ""),
                            },
                        )
                        for i, item in enumerate(data)
                    ]
                except Exception as e:
                    self.logger.warning(f"Jina batch {batch_start} failed: {e}")
                    return []

        async with httpx.AsyncClient(timeout=60.0) as client:
            batches = [
                (start, image_base64s[start:start + batch_size])
                for start in range(0, len(image_base64s), batch_size)
            ]
            results = await asyncio.gather(
                *[process_batch(client, s, imgs) for s, imgs in batches]
            )
        points: List[VectorPoint] = []
        for r in results:
            if isinstance(r, list):
                points.extend(r)
        return points

    async def _process_image_embeddings(
        self, image_chunks: List[dict], image_base64s: List[str], record_id: str = ""
    ) -> List[VectorPoint]:
        """Process image embeddings based on the configured provider.

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

        if self.embedding_provider == EmbeddingProvider.COHERE.value:
            return await self._process_image_embeddings_cohere(image_chunks, image_base64s)
        elif self.embedding_provider == EmbeddingProvider.VOYAGE.value:
            return await self._process_image_embeddings_voyage(image_chunks, image_base64s)
        elif self.embedding_provider == EmbeddingProvider.AWS_BEDROCK.value:
            return await self._process_image_embeddings_bedrock(image_chunks, image_base64s)
        elif self.embedding_provider == EmbeddingProvider.JINA_AI.value:
            return await self._process_image_embeddings_jina(image_chunks, image_base64s)
        else:
            self.logger.warning(
                f"Unsupported embedding provider for images: {self.embedding_provider}"
            )
            return []

    async def _store_image_points(self, points: List[VectorPoint]) -> None:
        if not points:
            self.logger.info("No image embeddings to upsert.")
            return
        start = time.perf_counter()
        # Batch image upserts to avoid holding all VectorPoints in one call
        batch_size = 500
        for i in range(0, len(points), batch_size):
            await self.vector_db_service.upsert_points(
                collection_name=self.collection_name, points=points[i:i + batch_size]
            )
        self.logger.info(
            f"✅ Stored {len(points)} image points in {time.perf_counter() - start:.2f}s"
        )

    # ------------------------------------------------------------------
    # Core document upsert (unified, all providers)
    # ------------------------------------------------------------------

    def _is_local_cpu_embedding(self) -> bool:
        return (
            self.embedding_provider is None
            or self.embedding_provider == EmbeddingProvider.DEFAULT.value
            or self.embedding_provider == EmbeddingProvider.SENTENCE_TRANSFOMERS.value
        )

    async def _compute_sparse_embeddings(
        self, texts: List[str]
    ) -> List[Optional[SparseVector]]:
        """Compute BM25 sparse vectors; returns list of None when not supported."""
        embedder = await self._ensure_sparse_embeddings()
        if embedder is None:
            return [None] * len(texts)
        return await embedder.embed_documents(texts)

    async def _embed_and_upsert_documents(
        self, documents: List[Document], record_id: str
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
        loop = asyncio.get_running_loop()

        # Dense embeddings (sync call in thread)
        dense_embeddings = await loop.run_in_executor(
            None, self.dense_embeddings.embed_documents, texts
        )

        # Sparse embeddings (provider-dependent)
        sparse_embeddings = await self._compute_sparse_embeddings(texts)

        points: List[VectorPoint] = [
            VectorPoint(
                id=str(uuid.uuid4()),
                dense_vector=dense,
                sparse_vector=sparse,
                payload={
                    "page_content": doc.page_content,
                    "metadata": doc.metadata,
                },
            )
            for doc, dense, sparse in zip(documents, dense_embeddings, sparse_embeddings)
        ]
        await self.vector_db_service.upsert_points(
            collection_name=self.collection_name, points=points
        )

    async def _process_document_chunks(
        self, langchain_document_chunks: List[Document], record_id: str = ""
    ) -> None:
        self.logger.info(
            f"⏱️ Embedding {len(langchain_document_chunks)} document chunks"
        )
        use_local_sequential = self._is_local_cpu_embedding()
        batch_size = (
            _LOCAL_CPU_DOCUMENT_BATCH_SIZE if use_local_sequential else _DEFAULT_DOCUMENT_BATCH_SIZE
        )

        async def process_batch(batch_start: int, batch: List[Document]) -> int:
            try:
                await self._embed_and_upsert_documents(batch, record_id)
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
        await self.delete_embeddings(virtual_record_id)

        self.logger.info(
            f"📊 Processing {len(langchain_docs)} text + {len(image_chunks)} image chunks"
        )

        if image_chunks:
            image_base64s = [c.get("image_uri") for c in image_chunks]
            points = await self._process_image_embeddings(image_chunks, image_base64s, record_id)
            await self._store_image_points(points)

        if langchain_docs:
            try:
                await self._process_document_chunks(langchain_docs, record_id)
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
        block_ids_to_delete: Optional[set] = None,
        is_reconciliation: bool = False,
        record: Optional["Record"] = None,
    ) -> bool | None:
        try:
            is_multimodal_embedding = await self.get_embedding_model_instance()
        except Exception as e:
            raise IndexingError(
                "Failed to get embedding model instance: " + str(e),
                details={"error": str(e)},
            )

        try:
            llm, config = await get_llm(self.config_service)
            is_multimodal_llm = config.get("isMultimodal")
        except Exception as e:
            raise IndexingError("Failed to get LLM: " + str(e), details={"error": str(e)})

        blocks = block_containers.blocks
        block_groups = block_containers.block_groups

        try:
            # On reconciliation: always refresh the record summary first
            if block_ids_to_delete or is_reconciliation:
                summary_block_id_set = {
                    f"{virtual_record_id}{RECORD_SUMMARY_BLOCK_ID_SUFFIX}"
                }
                await self.delete_blocks_by_ids(summary_block_id_set, virtual_record_id)

                if record is not None:
                    semantic_metadata = getattr(record, "semantic_metadata", None)
                    if semantic_metadata:
                        summary_doc = self._build_record_summary_document(
                            record_id, virtual_record_id, org_id, semantic_metadata
                        )
                        if summary_doc:
                            await self._process_document_chunks([summary_doc], record_id)

            if not blocks and not block_groups:
                if block_ids_to_delete:
                    await self.delete_blocks_by_ids(block_ids_to_delete, virtual_record_id)
                await self._cleanup_orphaned_embeddings_if_needed(
                    record_id, virtual_record_id, record
                )
                return None

            text_blocks = []
            image_blocks = []
            table_blocks = []
            sql_row_blocks = []

            for block in blocks:
                block_type = (
                    str(block.type.value).lower()
                    if hasattr(block.type, "value")
                    else str(block.type).lower()
                )
                if block_type in ["text", "paragraph", "textsection", "heading", "quote"]:
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

            self.logger.info(
                f"📊 Processing {len(blocks)} blocks and {len(block_groups)} block_groups"
            )
            self.logger.debug(
                f"Block classification: text={len(text_blocks)}, image={len(image_blocks)}, "
                f"table={len(table_blocks)}, sql_row={len(sql_row_blocks)}"
            )

            documents_to_embed: List = []

            # ── Text blocks ──
            if text_blocks:
                try:
                    for block in text_blocks:
                        block_text = block.data
                        metadata = {
                            "virtualRecordId": virtual_record_id,
                            "blockId": block.id,
                            "blockIndex": block.index,
                            "orgId": org_id,
                            "isBlockGroup": False,
                        }
                        doc = self.nlp(block_text)
                        sentences = [s.text for s in doc.sents]
                        if len(sentences) > 1:
                            for sentence in sentences:
                                documents_to_embed.append(
                                    Document(
                                        page_content=sentence,
                                        metadata={**metadata, "isBlock": False},
                                    )
                                )
                        documents_to_embed.append(
                            Document(
                                page_content=block_text,
                                metadata={**metadata, "isBlock": True},
                            )
                        )
                    self.logger.info("✅ Added text documents for embedding")
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
                    images_uris = [b.data.get("uri") for b in valid_image_blocks]
                    if images_uris:
                        if is_multimodal_embedding:
                            for block in valid_image_blocks:
                                documents_to_embed.append(
                                    {
                                        "image_uri": block.data.get("uri"),
                                        "metadata": {
                                            "virtualRecordId": virtual_record_id,
                                            "blockId": block.id,
                                            "blockIndex": block.index,
                                            "orgId": org_id,
                                            "isBlock": True,
                                            "isBlockGroup": False,
                                        },
                                    }
                                )
                        elif is_multimodal_llm:
                            description_results = await self.describe_images(images_uris, llm)
                            for result, block in zip(description_results, valid_image_blocks):
                                if result["success"]:
                                    documents_to_embed.append(
                                        Document(
                                            page_content=result["description"],
                                            metadata={
                                                "virtualRecordId": virtual_record_id,
                                                "blockId": block.id,
                                                "blockIndex": block.index,
                                                "orgId": org_id,
                                                "isBlock": True,
                                                "isBlockGroup": False,
                                            },
                                        )
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
                    await self.delete_blocks_by_ids(block_ids_to_delete, virtual_record_id)
                await self._cleanup_orphaned_embeddings_if_needed(
                    record_id, virtual_record_id, record
                )
                return True

            # ── Embed and store ──
            if is_reconciliation:
                # Partial update: no full delete; only changed blocks
                langchain_docs = [d for d in documents_to_embed if isinstance(d, Document)]
                image_chunks = [d for d in documents_to_embed if not isinstance(d, Document)]
                if langchain_docs:
                    await self._process_document_chunks(langchain_docs, record_id)
                if image_chunks:
                    image_base64s = [c.get("image_uri") for c in image_chunks]
                    points = await self._process_image_embeddings(
                        image_chunks, image_base64s, record_id
                    )
                    await self._store_image_points(points)
            else:
                await self._create_embeddings(documents_to_embed, record_id, virtual_record_id)

            if block_ids_to_delete:
                self.logger.debug(f"📊 Deleting {len(block_ids_to_delete)} removed blocks")
                await self.delete_blocks_by_ids(block_ids_to_delete, virtual_record_id)

            self.logger.debug(
                f"✅ Indexing complete for record {record_id}: "
                f"{len(documents_to_embed)} documents"
            )

            await self._cleanup_orphaned_embeddings_if_needed(
                record_id, virtual_record_id, record
            )
            return True

        except (IndexingError, VectorStoreError, DocumentProcessingError, EmbeddingError):
            raise
        except Exception as e:
            raise IndexingError(
                f"Unexpected error during indexing: {str(e)}",
                details={"error_type": type(e).__name__},
            )
