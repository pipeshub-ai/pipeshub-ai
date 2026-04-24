import asyncio
import re
import time
import uuid
from typing import Any, Awaitable, Callable, Dict, List, Optional

import httpx
import spacy
from langchain_core.documents import Document
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import HumanMessage
from langchain_qdrant import FastEmbedSparse, QdrantVectorStore, RetrievalMode
from qdrant_client.http.models import PointStruct
from spacy.language import Language
from spacy.tokens import Doc

from app.config.constants.service import config_node_constants
from app.exceptions.indexing_exceptions import (
    DocumentProcessingError,
    EmbeddingError,
    IndexingError,
    MetadataProcessingError,
    VectorStoreError,
)
from app.models.blocks import BlockType, BlocksContainer
from app.modules.extraction.prompt_template import prompt_for_image_description
from app.modules.transformers.transformer import TransformContext, Transformer
from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
from app.services.vector_db.const.const import normalize_identity as _normalize_identity
from app.services.vector_db.interface.vector_db import IVectorDBService

from app.utils.aimodels import (
    EmbeddingProvider,
    get_default_embedding_model,
    get_embedding_model,
)
from app.utils.llm import get_llm

# Module-level shared spaCy pipeline to avoid repeated heavy loads
_SHARED_NLP: Optional[Language] = None

def _get_shared_nlp() -> Language:
    # Avoid global mutation; attach cache to function attribute
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
OUTPUT_DIMENSION = 1536
HTTP_OK = 200
_DEFAULT_DOCUMENT_BATCH_SIZE = 50
_DEFAULT_CONCURRENCY_LIMIT = 5
# Small batch size for local/CPU embedding models to avoid memory/CPU thrashing
_LOCAL_CPU_DOCUMENT_BATCH_SIZE = 3

# Providers we have a *specialized* image-embedding code path for. Being in
# this set does not, by itself, decide whether we attempt direct image
# embedding: that decision is driven by the user-set ``isMultimodal``
# flag (see :func:`_provider_supports_image_embeddings`). This set only
# controls which handler the dispatcher routes to.
#
# ``azureAI`` is included because Azure AI Foundry hosts Cohere's
# multimodal embedding models (``embed-v-4-0``, ``Cohere-embed-v3-*``).
# ``gemini`` uses the Gemini API's ``embedContent`` endpoint with
# ``inline_data`` image parts. Google's multimodal embedding models
# (``gemini-embedding-2-preview`` and successors) accept images, video,
# audio, and PDFs directly on this endpoint; the older text-only
# ``gemini-embedding-001`` rejects ``inline_data`` with HTTP 400, which
# our handler surfaces as :class:`EmbeddingError` so the caller can
# fall back to VLM captioning instead of silently dropping images.
_NATIVE_IMAGE_EMBEDDING_PROVIDERS = frozenset({
    EmbeddingProvider.COHERE.value,
    EmbeddingProvider.VOYAGE.value,
    EmbeddingProvider.AWS_BEDROCK.value,
    EmbeddingProvider.JINA_AI.value,
    EmbeddingProvider.AZURE_AI.value,
    EmbeddingProvider.GEMINI.value,
})

# Back-compat alias retained for any external callers that imported the
# old name. New code should use :data:`_NATIVE_IMAGE_EMBEDDING_PROVIDERS`.
_MULTIMODAL_IMAGE_EMBEDDING_PROVIDERS = _NATIVE_IMAGE_EMBEDDING_PROVIDERS


def _normalize_azure_ai_endpoint_for_cohere(endpoint: Optional[str]) -> Optional[str]:
    """Convert an Azure AI Foundry endpoint URL into a ``base_url`` the
    Cohere SDK can use.

    Users most often paste the OpenAI-compatible URL shown in the UI
    placeholder (``https://<resource>.services.ai.azure.com/openai/v1/``)
    because that's what the Azure Portal surfaces. That suffix only
    serves OpenAI-shaped text embeddings; Cohere's image-capable
    ``/embed`` route lives at the resource root. Strip the well-known
    OpenAI suffixes and hand the root to ``cohere.ClientV2``, which will
    append its own ``/v2/embed`` path.
    """
    if not endpoint:
        return endpoint
    url = endpoint.strip().rstrip("/")
    for suffix in ("/openai/v1", "/openai"):
        if url.endswith(suffix):
            url = url[: -len(suffix)]
            break
    return url.rstrip("/") or endpoint


def _provider_has_native_image_path(provider: Optional[str]) -> bool:
    """Whether ``provider`` has a hand-rolled image-embedding handler.

    Used purely for dispatching: providers in the set are routed to a
    specialized method (Cohere/Voyage/Bedrock/Jina/Azure/Gemini), others
    fall through to :meth:`VectorStore._process_image_embeddings_generic`
    which tries the LangChain ``Embeddings.aembed_documents`` interface
    with data URLs. Either path can still fail at runtime, in which case
    the caller falls back to VLM captioning.
    """
    return provider in _NATIVE_IMAGE_EMBEDDING_PROVIDERS


# Retained for back-compat with callers/tests that reference the old name.
_provider_supports_image_embeddings = _provider_has_native_image_path


def _read_is_multimodal_flag(config: dict) -> bool:
    """Read the ``isMultimodal`` toggle from a model config.

    Mirrors :func:`app.utils.aimodels.is_multimodal_llm`: the flag can live
    either at the root of the config or nested under ``configuration`` depending
    on how/when it was written by the UI.
    """
    if not isinstance(config, dict):
        return False
    if config.get("isMultimodal"):
        return True
    inner = config.get("configuration") or {}
    return bool(inner.get("isMultimodal"))


def _build_embedding_signature(
    provider: str,
    model_name: str,
    dimension: int,
    is_multimodal: Optional[bool] = None,
) -> dict:
    """Build the canonical signature payload stored on a collection.

    ``is_multimodal`` is included only when explicitly known. Leaving it
    off on legacy call-sites keeps on-disk signatures compatible — a
    missing field is interpreted by :func:`_signatures_match` as
    "unknown" rather than "False", which preserves the existing trust
    semantics for pre-signature collections.
    """
    payload: dict = {
        "embedding_provider": _normalize_identity(provider),
        "embedding_model": _normalize_identity(model_name),
        "embedding_dimension": int(dimension),
    }
    if is_multimodal is not None:
        payload["is_multimodal"] = bool(is_multimodal)
    return payload


def _signatures_match(
    stored: Optional[dict],
    new_provider: Optional[str],
    new_model: Optional[str],
    new_is_multimodal: Optional[bool] = None,
) -> bool:
    """Return True iff the stored signature matches (provider, model,
    optional is_multimodal) after normalization.

    ``is_multimodal`` participates in the comparison only when both the
    stored and the new value are known (not None). This preserves
    backward compatibility with signatures written before the flag was
    tracked: an absent ``is_multimodal`` on either side is treated as
    "unknown" and therefore does not contribute to a mismatch.
    """
    if not stored:
        return False
    if not new_provider or not new_model:
        return False
    if (
        _normalize_identity(stored.get("embedding_provider"))
        != _normalize_identity(new_provider)
    ):
        return False
    if (
        _normalize_identity(stored.get("embedding_model"))
        != _normalize_identity(new_model)
    ):
        return False

    stored_multimodal = stored.get("is_multimodal")
    if stored_multimodal is not None and new_is_multimodal is not None:
        if bool(stored_multimodal) != bool(new_is_multimodal):
            return False
    return True

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
        # Reuse a single spaCy pipeline across instances to avoid memory bloat
        self.nlp = _get_shared_nlp()
        self.vector_db_service = vector_db_service
        self.collection_name = collection_name
        self.vector_store = None
        self.dense_embeddings = None
        self.api_key = None
        self.model_name = None
        self.embedding_provider = None
        self.is_multimodal_embedding = False
        self.region_name = None
        self.aws_access_key_id = None
        self.aws_secret_access_key = None
        self.endpoint = None
        # User-configured ``output_dimensions`` from the UI. When set,
        # providers that expose a REST image-embedding path (Gemini,
        # Vertex AI multimodal) must forward it so the resulting image
        # vectors match the collection's dimension — otherwise upsert
        # will fail with a shape mismatch.
        self.output_dimensions: Optional[int] = None
        # Cache key for :meth:`get_embedding_model_instance`. A tuple of
        # ``(provider, configured_model_name, output_dims, is_multimodal)``
        # taken from AI_MODELS config. When it matches the last successful
        # load we skip the provider round-trip (``embed_query("test")``)
        # and the QdrantVectorStore re-construction. ``None`` means "no
        # successful load yet" — the first call always rebuilds.
        self._embedding_config_signature: Optional[tuple] = None

        try:
            # Initialize sparse embeddings
            try:
                self.sparse_embeddings = FastEmbedSparse(model_name="Qdrant/BM25")
            except Exception as e:
                raise IndexingError(
                    "Failed to initialize sparse embeddings: " + str(e),
                    details={"error": str(e)},
                )



        except (IndexingError, VectorStoreError):
            raise
        except Exception as e:
            raise IndexingError(
                "Failed to initialize indexing pipeline: " + str(e),
                details={"error": str(e)},
            )

    @staticmethod
    def _build_image_point(
        embedding: List[float],
        chunk: dict,
    ) -> PointStruct:
        """Build a ``PointStruct`` for an image embedding.

        The Qdrant payload is identity-only: ``virtualRecordId`` +
        ``blockId`` + ``orgId`` (plus whatever the caller already put on
        ``chunk["metadata"]``) and a ``blockType`` marker. The image
        bytes themselves are NOT stored here — they live in blob
        storage and are resolved at read time by ``chat_helpers`` via
        ``block.data.uri`` in the record graph. Stashing the (often
        megabyte-scale) base64 data URL on ``metadata.imageUri`` would
        bloat the Qdrant payload, blow up shard memory, and slow down
        every scroll / query — for no read-path benefit, since the
        graph round-trip happens anyway when the record is fetched to
        render citations. ``page_content`` stays ``""`` for the same
        reason: so the blob never leaks into an LLM prompt or search
        response. The ``blockType`` marker lets the retrieval layer
        short-circuit empty-content filters.
        """
        base_metadata = chunk.get("metadata", {}) or {}
        metadata = {
            **base_metadata,
            "blockType": BlockType.IMAGE.value,
        }
        return PointStruct(
            id=str(uuid.uuid4()),
            vector={"dense": embedding},
            payload={
                "metadata": metadata,
                "page_content": "",
            },
        )

    async def _normalize_image_to_base64(self, image_uri: str) -> str | None:
        """
        Normalize an image reference into a raw base64-encoded string (no data: prefix).
        - data URLs (data:image/...;base64,xxxxx) -> returns the part after the comma
        - http/https URLs -> downloads bytes then base64-encodes
        - raw base64 strings -> returns as-is (after trimming/padding)

        Returns None if normalization fails.
        """
        result = await self._normalize_image_with_mime(image_uri)
        return result[0] if result else None

    async def _normalize_image_with_mime(
        self, image_uri: str
    ) -> tuple[str, str] | None:
        """Normalize an image reference into a ``(base64, mime_type)`` tuple.

        The mime type is extracted from ``data:`` URL prefixes when
        present. For raw base64 strings with no mime hint we default to
        ``image/jpeg`` — JPEG is accepted by every embedding API we
        currently dispatch to (Gemini, Vertex, Cohere, Jina, Bedrock
        Titan) and PNG decoders also accept it silently in practice for
        misflagged payloads.

        Returns ``None`` on any normalization failure so callers can
        skip the offending chunk without crashing the whole batch.
        """
        try:
            if not image_uri or not isinstance(image_uri, str):
                return None

            uri = image_uri.strip()

            if uri.startswith("data:"):
                comma_index = uri.find(",")
                if comma_index == -1:
                    return None
                head = uri[:comma_index]
                b64_part = uri[comma_index + 1 :].strip()
                mime_type = "image/jpeg"
                if ";" in head:
                    declared = head.split(";", 1)[0].removeprefix("data:").strip()
                    if declared.startswith("image/"):
                        mime_type = declared
                missing = (-len(b64_part)) % 4
                if missing:
                    b64_part += "=" * missing
                return b64_part, mime_type

            candidate = uri.replace("\n", "").replace("\r", "").replace(" ", "")
            if not re.fullmatch(r"[A-Za-z0-9+/=_-]+", candidate):
                return None
            missing = (-len(candidate)) % 4
            if missing:
                candidate += "=" * missing
            return candidate, "image/jpeg"
        except Exception:
            return None

    async def apply(self, ctx: TransformContext) -> bool|None:
        record = ctx.record
        record_id = record.id
        virtual_record_id = record.virtual_record_id
        block_containers = record.block_containers
        org_id = record.org_id
        mime_type = record.mime_type

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
                    f"📊 Reconciliation: No changes detected for record {record_id}"
                )
                return True

            # Shallow copy with only blocks/block_groups that need indexing
            block_containers = BlocksContainer(
                blocks=[b for b in block_containers.blocks if b.id in blocks_to_index_ids],
                block_groups=[bg for bg in block_containers.block_groups if bg.id in blocks_to_index_ids],
            )

        return await self.index_documents(
            block_containers, org_id, record_id, virtual_record_id, mime_type,
            block_ids_to_delete=block_ids_to_delete,
            is_reconciliation=is_reconciliation,
        )

    @Language.component("custom_sentence_boundary")
    def custom_sentence_boundary(doc) -> Doc:
        for token in doc[:-1]:  # Avoid out-of-bounds errors
            next_token = doc[token.i + 1]

            # If token is a number and followed by a period, don't treat it as a sentence boundary
            if token.like_num and next_token.text == ".":
                next_token.is_sent_start = False
            # Handle common abbreviations
            elif (
                token.text.lower()
                in [
                    "mr",
                    "mrs",
                    "dr",
                    "ms",
                    "prof",
                    "sr",
                    "jr",
                    "inc",
                    "ltd",
                    "co",
                    "etc",
                    "vs",
                    "fig",
                    "et",
                    "al",
                    "e.g",
                    "i.e",
                    "vol",
                    "pg",
                    "pp",
                    "pvt",
                    "llc",
                    "llp",
                    "lp",
                    "ll",
                    "ltd",
                    "inc",
                    "corp",
                ]
                and next_token.text == "."
            ):
                next_token.is_sent_start = False
            # Handle bullet points and list markers
            elif (
                # Numeric bullets with period (1., 2., etc)
                (
                    token.like_num and next_token.text == "." and len(token.text) <= LENGTH_THRESHOLD
                )  # Limit to 2 digits
                or
                # Letter bullets with period (a., b., etc)
                (
                    len(token.text) == 1
                    and token.text.isalpha()
                    and next_token.text == "."
                )
                or
                # Common bullet point markers
                token.text in ["•", "∙", "·", "○", "●", "-", "–", "—"]
            ):
                next_token.is_sent_start = False

            # Check for potential headings (all caps or title case without period)
            elif (
                # All caps text likely a heading
                token.text.isupper()
                and len(token.text) > 1  # Avoid single letters
                and not any(c.isdigit() for c in token.text)  # Avoid serial numbers
            ):
                if next_token.i < len(doc) - 1:
                    next_token.is_sent_start = False

            # Handle ellipsis (...) - don't split
            elif token.text == "." and next_token.text == ".":
                next_token.is_sent_start = False
        return doc

    def _create_custom_tokenizer(self, nlp) -> Language:
        """
        Creates a custom tokenizer that handles special cases for sentence boundaries.
        """
        # Add the custom rule to the pipeline
        if "sentencizer" not in nlp.pipe_names:
            nlp.add_pipe("sentencizer", before="parser")

        # Add custom sentence boundary detection
        if "custom_sentence_boundary" not in nlp.pipe_names:
            nlp.add_pipe("custom_sentence_boundary", after="sentencizer")

        # Configure the tokenizer to handle special cases
        special_cases = {
            "e.g.": [{"ORTH": "e.g."}],
            "i.e.": [{"ORTH": "i.e."}],
            "etc.": [{"ORTH": "etc."}],
            "...": [{"ORTH": "..."}],
        }

        for case, mapping in special_cases.items():
            nlp.tokenizer.add_special_case(case, mapping)
        return nlp

    async def _create_collection_with_indexes(
        self,
        embedding_size: int,
        sparse_idf: bool,
        embedding_provider: Optional[str] = None,
        embedding_model_name: Optional[str] = None,
        is_multimodal: Optional[bool] = None,
    ) -> None:
        """Create the collection, install payload indexes, and (if provider+model
        are known) write a collection signature so later health checks can tell
        which embedding model built this collection. Raises VectorStoreError on
        failure."""
        try:
            await self.vector_db_service.create_collection(
                embedding_size=embedding_size,
                collection_name=self.collection_name,
                sparse_idf=sparse_idf,
            )
            self.logger.info(
                f"✅ Successfully created collection {self.collection_name}"
            )
            await self.vector_db_service.create_index(
                collection_name=self.collection_name,
                field_name="metadata.virtualRecordId",
                field_schema={"type": "keyword"},
            )
            await self.vector_db_service.create_index(
                collection_name=self.collection_name,
                field_name="metadata.orgId",
                field_schema={"type": "keyword"},
            )
            # Index the sentinel-marker field so
            # :meth:`count_user_points` can do an indexed filtered count
            # instead of a full scan. Best-effort: on failure we fall
            # back to the (slower) unindexed path or to
            # ``points_count - sentinel`` subtraction.
            try:
                await self.vector_db_service.create_index(
                    collection_name=self.collection_name,
                    field_name="_kind",
                    field_schema={"type": "keyword"},
                )
            except Exception as idx_err:
                self.logger.warning(
                    f"Failed to create '_kind' payload index on "
                    f"{self.collection_name}: {idx_err}. "
                    f"count_user_points will use the slower fallback."
                )

            if embedding_provider and embedding_model_name:
                try:
                    await self.vector_db_service.set_collection_signature(
                        collection_name=self.collection_name,
                        signature=_build_embedding_signature(
                            provider=embedding_provider,
                            model_name=embedding_model_name,
                            dimension=embedding_size,
                            is_multimodal=is_multimodal,
                        ),
                        embedding_size=embedding_size,
                    )
                except Exception as sig_err:
                    # Signature write is best-effort. Failing here would force
                    # us to drop an otherwise-valid collection; log loudly
                    # instead so legacy-style behaviour still works.
                    self.logger.error(
                        f"⚠️ Failed to write collection signature for "
                        f"{self.collection_name}: {sig_err}"
                    )
        except Exception as e:
            self.logger.error(
                f"❌ Error creating collection {self.collection_name}: {str(e)}"
            )
            raise VectorStoreError(
                "Failed to create collection",
                details={"collection": self.collection_name, "error": str(e)},
            )

    async def _initialize_collection(
        self,
        embedding_size: int = 1024,
        sparse_idf: bool = False,
        embedding_provider: Optional[str] = None,
        embedding_model_name: Optional[str] = None,
        is_multimodal: Optional[bool] = None,
    ) -> None:
        """Initialize Qdrant collection with proper configuration.

        Behaviour:
          * Collection missing           -> create it (with signature).
          * Collection exists, dims match, signature matches -> reuse.
          * Collection exists, dims match, signature mismatch, user data empty
                                          -> drop and recreate (safe: no data loss).
          * Collection exists, dims match, signature mismatch, has user data
                                          -> refuse: the same-dimension swap is
                                             exactly the case the plain
                                             dimension check cannot detect.
          * Collection exists, dims differ, collection is empty
                                          -> drop and recreate.
          * Collection exists, dims differ, has user data
                                          -> refuse.

        "User data empty" means no non-sentinel points exist; the signature
        sentinel itself is always treated as bookkeeping.
        """
        try:
            collection_info = await self.vector_db_service.get_collection(self.collection_name)
        except Exception as get_err:
            # Treat any error from get_collection as "collection does not exist"
            # and create it fresh. Underlying qdrant_client raises
            # UnexpectedResponse / ValueError for missing collections.
            self.logger.info(
                f"Collection {self.collection_name} not found "
                f"({type(get_err).__name__}: {get_err}); creating new collection."
            )
            await self._create_collection_with_indexes(
                embedding_size,
                sparse_idf,
                embedding_provider=embedding_provider,
                embedding_model_name=embedding_model_name,
                is_multimodal=is_multimodal,
            )
            return

        try:
            current_vector_size = collection_info.config.params.vectors["dense"].size
        except Exception as inspect_err:
            raise VectorStoreError(
                "Existing collection is not compatible with the hybrid "
                "(dense + sparse) layout; cannot read 'dense' vector params.",
                details={
                    "collection": self.collection_name,
                    "error": str(inspect_err),
                },
            )

        # Prefer an authoritative user-point count (excludes the signature
        # sentinel). Fall back to raw counters if and only if the service
        # doesn't expose the helper (older/test implementations) — mixing the
        # filtered count with ``vectors_count`` would re-introduce the bug
        # this signature feature was supposed to close: a collection whose
        # only point is the sentinel would report ``vectors_count >= 1`` and
        # be flagged as "has user data", blocking a legitimate model swap
        # on an otherwise-empty collection.
        #
        # Use strict isinstance checks rather than truthy/int() coercion:
        # MagicMock.__int__ defaults to 1 and its truthiness is True, so a
        # naive path would interpret every AsyncMock-returned value as
        # "there is user data" and flip control flow in tests.
        user_points: Optional[int] = None
        user_points_authoritative = False
        try:
            raw = await self.vector_db_service.count_user_points(self.collection_name)
            if isinstance(raw, int) and not isinstance(raw, bool):
                user_points = raw
                user_points_authoritative = True
        except AttributeError:
            pass
        except Exception as cnt_err:
            self.logger.warning(
                f"count_user_points failed for {self.collection_name}: {cnt_err}"
            )

        raw_vc = getattr(collection_info, "vectors_count", None)
        vectors_count = (
            raw_vc if isinstance(raw_vc, int) and not isinstance(raw_vc, bool) else 0
        )

        if user_points_authoritative:
            # Trust the sentinel-aware count; vectors_count is kept only for
            # diagnostics because Qdrant includes the sentinel in it.
            has_user_data = user_points > 0  # type: ignore[operator]
        else:
            raw_pc = getattr(collection_info, "points_count", None)
            user_points = (
                raw_pc if isinstance(raw_pc, int) and not isinstance(raw_pc, bool) else 0
            )
            has_user_data = user_points > 0 or vectors_count > 0

        # Same dimension: check signature identity to catch same-dim model swaps
        # (two different embedding models happening to produce vectors of the
        # same dimensionality, which the dimension check cannot detect).
        if current_vector_size == embedding_size:
            stored_sig: Optional[dict] = None
            try:
                raw_sig = await self.vector_db_service.get_collection_signature(
                    self.collection_name
                )
                # Strict isinstance: ignore MagicMock-style test doubles that
                # truthily satisfy "not None" but aren't real payloads.
                if isinstance(raw_sig, dict) and raw_sig:
                    stored_sig = raw_sig
            except AttributeError:
                stored_sig = None
            except Exception as sig_err:
                self.logger.warning(
                    f"Signature lookup failed for {self.collection_name}: {sig_err}"
                )

            signature_matches = _signatures_match(
                stored=stored_sig,
                new_provider=embedding_provider,
                new_model=embedding_model_name,
                new_is_multimodal=is_multimodal,
            )

            if signature_matches or stored_sig is None:
                # Match or legacy-unknown: reuse. Legacy collections pre-date
                # signature tracking; we can't second-guess them here.
                if stored_sig is None and has_user_data and embedding_provider and embedding_model_name:
                    self.logger.warning(
                        f"Collection {self.collection_name} has no embedding "
                        f"signature on record; assuming the currently configured "
                        f"model matches the one that built it. The health-check "
                        f"flow should be used before switching models."
                    )
                elif not has_user_data and embedding_provider and embedding_model_name:
                    # Empty collection with either matching or legacy-unknown
                    # signature: refresh the signature to reflect the current
                    # configuration so future runs are authoritative.
                    try:
                        await self.vector_db_service.set_collection_signature(
                            collection_name=self.collection_name,
                            signature=_build_embedding_signature(
                                provider=embedding_provider,
                                model_name=embedding_model_name,
                                dimension=embedding_size,
                                is_multimodal=is_multimodal,
                            ),
                            embedding_size=embedding_size,
                        )
                    except Exception as sig_err:
                        self.logger.warning(
                            f"Failed to refresh signature on empty collection "
                            f"{self.collection_name}: {sig_err}"
                        )
                return

            # Stored signature disagrees with what we're about to use.
            if has_user_data:
                msg = (
                    f"Embedding model identity mismatch for existing non-empty "
                    f"collection '{self.collection_name}': collection was built "
                    f"with provider='{stored_sig.get('embedding_provider')}', "
                    f"model='{stored_sig.get('embedding_model')}' (dim="
                    f"{stored_sig.get('embedding_dimension')}), but the current "
                    f"configuration is provider='{embedding_provider}', "
                    f"model='{embedding_model_name}' (dim={embedding_size}). "
                    f"Even though vector dimensions match, the two models "
                    f"produce incompatible vector spaces and mixing them will "
                    f"silently corrupt search results. Refusing to index "
                    f"against {user_points} existing points. Re-index or "
                    f"delete the collection before switching models."
                )
                self.logger.error(f"❌ {msg}")
                raise VectorStoreError(
                    msg,
                    details={
                        "collection": self.collection_name,
                        "existing_provider": stored_sig.get("embedding_provider"),
                        "existing_model": stored_sig.get("embedding_model"),
                        "existing_dimension": stored_sig.get("embedding_dimension"),
                        "new_provider": embedding_provider,
                        "new_model": embedding_model_name,
                        "new_dimension": embedding_size,
                        "points_count": user_points,
                    },
                )

            # Empty collection: drop and recreate so the signature matches.
            self.logger.warning(
                f"Collection {self.collection_name} has a stale signature "
                f"({stored_sig.get('embedding_provider')}/"
                f"{stored_sig.get('embedding_model')}) and no user data. "
                f"Dropping and recreating with the current model."
            )
            try:
                await self.vector_db_service.delete_collection(self.collection_name)
            except Exception as del_err:
                raise VectorStoreError(
                    "Failed to delete empty collection during signature-mismatch recreation.",
                    details={
                        "collection": self.collection_name,
                        "error": str(del_err),
                    },
                )
            await self._create_collection_with_indexes(
                embedding_size,
                sparse_idf,
                embedding_provider=embedding_provider,
                embedding_model_name=embedding_model_name,
                is_multimodal=is_multimodal,
            )
            return

        # Dimension mismatch: only destroy the collection if it has no user data.
        if has_user_data:
            msg = (
                f"Embedding model dimension mismatch for existing non-empty "
                f"collection '{self.collection_name}': collection was built with "
                f"dimension {current_vector_size} but the currently configured "
                f"embedding model produces dimension {embedding_size}. Refusing "
                f"to delete {user_points} existing points. Either revert to an "
                f"embedding model with dimension {current_vector_size}, or "
                f"explicitly reindex/delete the collection before switching models."
            )
            self.logger.error(f"❌ {msg}")
            raise VectorStoreError(
                msg,
                details={
                    "collection": self.collection_name,
                    "existing_dimension": current_vector_size,
                    "required_dimension": embedding_size,
                    "points_count": user_points,
                    "vectors_count": vectors_count,
                },
            )

        self.logger.warning(
            f"Collection {self.collection_name} has dimension {current_vector_size} "
            f"but {embedding_size} is required, and the collection has no user data. "
            "Dropping and recreating."
        )
        try:
            await self.vector_db_service.delete_collection(self.collection_name)
        except Exception as del_err:
            raise VectorStoreError(
                "Failed to delete empty collection during dimension-mismatch recreation.",
                details={
                    "collection": self.collection_name,
                    "error": str(del_err),
                },
            )
        await self._create_collection_with_indexes(
            embedding_size,
            sparse_idf,
            embedding_provider=embedding_provider,
            embedding_model_name=embedding_model_name,
            is_multimodal=is_multimodal,
        )



    async def get_embedding_model_instance(self) -> bool:
        try:
            self.logger.info("Getting embedding model")

            dense_embeddings = None
            ai_models = await self.config_service.get_config(
                config_node_constants.AI_MODELS.value,use_cache=False
            )
            embedding_configs = ai_models["embedding"]
            is_multimodal = False
            provider = None
            configured_model_name: Optional[str] = None
            configuration = None
            # Parse the user-configured output dimension up-front so it
            # participates in the cache signature. Some providers report
            # different dims for the same model depending on this value
            # (OpenAI ``text-embedding-3-*``, Gemini embeddings, etc.),
            # which means we must rebuild when it changes.
            config_output_dims: Optional[int] = None
            if not embedding_configs:
                self.logger.info("Using default embedding model")
            else:
                # Find the default config, or fall back to the first one.
                config = next((c for c in embedding_configs if c.get("isDefault")), embedding_configs[0])

                provider = config["provider"]
                configuration = config["configuration"]
                model_names = [name.strip() for name in configuration["model"].split(",") if name.strip()]
                # Capture the *configured* model name up-front. This is the
                # stable identity we persist in the collection signature:
                # it matches what the health-check endpoint later reads out
                # of AI_MODELS, so the two compare like-for-like. The
                # SDK-reported ``dense_embeddings.model_name`` below can
                # differ (prefixes, casing, deployment aliases) and would
                # produce spurious mismatches on re-save of the same config.
                configured_model_name = model_names[0] if model_names else None
                is_multimodal = _read_is_multimodal_flag(config)
                raw_dims = configuration.get("dimensions")
                if raw_dims not in (None, "", 0):
                    try:
                        parsed = int(raw_dims)
                        if parsed > 0:
                            config_output_dims = parsed
                    except (ValueError, TypeError):
                        # Ignore silently here; the detailed warning is logged
                        # later when we finalise ``self.output_dimensions``.
                        pass

            # Fast path: config-level signature is unchanged and we still
            # have a usable vector store from the previous load. This
            # avoids:
            #   (a) a second ``get_embedding_model`` construction (which
            #       can do blocking provider I/O);
            #   (b) the ``embed_query("test")`` round-trip below (which
            #       hits the provider over the network); and
            #   (c) a redundant ``_initialize_collection`` call on every
            #       record. The signature deliberately includes the
            #       ``isMultimodal`` flag and the configured output
            #       dimension so any UI toggle forces a rebuild.
            new_config_signature = (
                provider,
                configured_model_name,
                config_output_dims,
                bool(is_multimodal),
            )
            if (
                self._embedding_config_signature is not None
                and self._embedding_config_signature == new_config_signature
                and self.vector_store is not None
                and self.dense_embeddings is not None
            ):
                self.logger.debug(
                    "Reusing cached embedding model instance "
                    "(provider=%s, model=%s, dims=%s, isMultimodal=%s)",
                    provider,
                    configured_model_name,
                    config_output_dims,
                    bool(is_multimodal),
                )
                return self.is_multimodal_embedding

            if not embedding_configs:
                dense_embeddings = get_default_embedding_model()
            else:
                dense_embeddings = get_embedding_model(provider, config)
            # Get the embedding dimensions from the model
            try:
                sample_embedding = dense_embeddings.embed_query("test")
                embedding_size = len(sample_embedding)
            except Exception as e:
                self.logger.warning(
                    f"Error with configured embedding model, falling back to default: {str(e)}"
                )
                raise IndexingError(
                    "Failed to get embedding model: " + str(e),
                    details={"error": str(e)},
                )

            # SDK-reported model name, used only for diagnostic logging and
            # downstream code paths that don't care about signature identity.
            sdk_model_name = None
            if hasattr(dense_embeddings, "model_name"):
                sdk_model_name = dense_embeddings.model_name
            elif hasattr(dense_embeddings, "model"):
                sdk_model_name = dense_embeddings.model
            elif hasattr(dense_embeddings, "model_id"):
                sdk_model_name = dense_embeddings.model_id
            else:
                sdk_model_name = "unknown"

            # Prefer the configured name for everything downstream. Fall back
            # to the SDK-reported name only when no config was available
            # (default-embedding path).
            model_name = configured_model_name or sdk_model_name

            self.logger.info(
                f"Using embedding model: {model_name} "
                f"(sdk_reported={sdk_model_name}), embedding_size: {embedding_size}"
            )

            # Initialize collection with correct embedding size. Pass the
            # provider + *configured* model identity so new collections get
            # a signature that matches what the health check later reads
            # out of AI_MODELS. Also forward ``is_multimodal`` so toggling
            # the flag on the same provider/model is treated as an
            # identity change rather than silently mixing vector spaces.
            await self._initialize_collection(
                embedding_size=embedding_size,
                embedding_provider=provider,
                embedding_model_name=model_name,
                is_multimodal=bool(is_multimodal),
            )

            # Initialize vector store with same configuration

            self.vector_store: QdrantVectorStore = QdrantVectorStore(
                client=self.vector_db_service.get_service_client(),
                collection_name=self.collection_name,
                vector_name="dense",
                sparse_vector_name="sparse",
                embedding=dense_embeddings,
                sparse_embedding=self.sparse_embeddings,
                retrieval_mode=RetrievalMode.HYBRID,
            )

            self.dense_embeddings = dense_embeddings
            self.embedding_provider = provider
            self.api_key = configuration["apiKey"] if configuration and "apiKey" in configuration else None
            self.model_name = model_name
            self.region_name = configuration["region"] if configuration and "region" in configuration else None
            # Needed by providers (e.g. Azure AI) that route image embeddings
            # through a Cohere-compatible endpoint using a user-supplied URL.
            self.endpoint = configuration.get("endpoint") if configuration else None
            # ``config_output_dims`` was parsed up-front for the cache
            # signature; surface a warning here if the raw value was
            # present but unparseable (same diagnostic the previous
            # implementation logged).
            if configuration:
                raw_dims = configuration.get("dimensions")
                if raw_dims not in (None, "", 0) and config_output_dims is None:
                    self.logger.warning(
                        "Ignoring non-numeric embedding dimensions config: %r",
                        raw_dims,
                    )
            self.output_dimensions = config_output_dims
            # Persist AWS credentials when using Bedrock so we can call image embedding runtime directly
            if provider == EmbeddingProvider.AWS_BEDROCK.value and configuration:
                self.aws_access_key_id = configuration.get("awsAccessKeyId")
                self.aws_secret_access_key = configuration.get("awsAccessSecretKey")
            self.is_multimodal_embedding = bool(is_multimodal)
            # Record the signature only after every field above has been
            # set. On failure we leave the previous cache in place so a
            # partially-initialised instance can never be reused.
            self._embedding_config_signature = new_config_signature
            return self.is_multimodal_embedding
        except IndexingError as e:
            self.logger.error(f"Error getting embedding model: {str(e)}")
            raise IndexingError(
                "Failed to get embedding model: " + str(e), details={"error": str(e)}
            )

    async def delete_embeddings(self, virtual_record_id: str) -> None:
        try:
            filter_dict = await self.vector_db_service.filter_collection(
                must={"virtualRecordId": virtual_record_id}
            )

            self.vector_db_service.delete_points(self.collection_name, filter_dict)

            self.logger.info(f"✅ Successfully deleted embeddings for record {virtual_record_id}")
        except Exception as e:
            self.logger.error(f"Error deleting embeddings: {str(e)}")
            raise EmbeddingError(f"Failed to delete embeddings: {str(e)}")

    async def delete_blocks_by_ids(
        self, block_ids: set, virtual_record_id: str
    ) -> None:
        """
        Args:
            block_ids: Set of block IDs to delete
            virtual_record_id: Virtual record ID for scoping the deletion
        """
        if not block_ids:
            return

        try:
            filter_dict = await self.vector_db_service.filter_collection(
                must={"blockId": list(block_ids), "virtualRecordId": virtual_record_id}
            )
            self.vector_db_service.delete_points(self.collection_name, filter_dict)
            self.logger.info(
                f"✅ Deleted {len(block_ids)} blocks from vector store "
                f"for virtual_record_id {virtual_record_id}"
            )
        except Exception as e:
            self.logger.error(f"Error deleting blocks by IDs: {str(e)}")
            raise EmbeddingError(f"Failed to delete blocks by IDs: {str(e)}")

    async def _process_image_embeddings_cohere(
        self,
        image_chunks: List[dict],
        image_base64s: List[str],
        base_url: Optional[str] = None,
    ) -> List[PointStruct]:
        """Process image embeddings using Cohere's image-embedding API.

        When ``base_url`` is supplied the Cohere SDK is pointed at a
        Cohere-compatible host other than ``api.cohere.com`` — this is how
        we reuse the same code path for Cohere embedding models deployed
        on Azure AI Foundry (``embed-v-4-0``, ``Cohere-embed-v3-*`` etc.).
        """
        import cohere

        client_kwargs: Dict[str, Any] = {"api_key": self.api_key}
        if base_url:
            client_kwargs["base_url"] = base_url
        co = cohere.ClientV2(**client_kwargs)
        points = []

        async def embed_single_image(i: int, image_base64: str) -> Optional[PointStruct]:
            """Embed a single image with Cohere API."""
            image_input = {
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {"url": image_base64}
                    }
                ]
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
                    )
                )
                chunk = image_chunks[i]
                embedding = response.embeddings.float[0]
                return self._build_image_point(embedding, chunk)
            except Exception as cohere_error:
                error_text = str(cohere_error)
                if "image size must be at most" in error_text:
                    self.logger.warning(
                        f"Skipping image {i} embedding due to size limit: {error_text}"
                    )
                    return None
                raise

        concurrency_limit = 10
        semaphore = asyncio.Semaphore(concurrency_limit)

        async def limited_embed(i: int, image_base64: str) -> Optional[PointStruct]:
            async with semaphore:
                return await embed_single_image(i, image_base64)

        tasks = [limited_embed(i, img) for i, img in enumerate(image_base64s)]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for result in results:
            if isinstance(result, PointStruct):
                points.append(result)
            elif isinstance(result, Exception):
                self.logger.warning(f"Failed to embed image: {str(result)}")

        return points

    async def _process_image_embeddings_voyage(
        self, image_chunks: List[dict], image_base64s: List[str]
    ) -> List[PointStruct]:
        """Process image embeddings using Voyage AI.

        ``VoyageEmbeddings._invocation_params`` switches between the
        standard ``/v1/embeddings`` and the multimodal
        ``/v1/multimodalembeddings`` endpoint purely by sniffing whether
        each input starts with ``data:image``. If we hand it a bare
        base64 string (which is the common shape we get from block
        storage) the SDK silently routes to the text endpoint and
        either errors out or, worse, returns a text embedding that
        lives in the wrong vector space.

        Normalize every input to a ``data:{mime};base64,{b64}`` URL up
        front so the multimodal endpoint is hit reliably, and track
        which chunk each normalized input corresponds to so we don't
        misalign embeddings when some inputs fail normalization.
        """
        batch_size = getattr(self.dense_embeddings, 'batch_size', 7)
        points = []

        # Normalize once, up-front, keeping a parallel list of the
        # source chunk index so we can recover the original chunk after
        # filtering. This mirrors the Jina path.
        normalized_results = await asyncio.gather(
            *[self._normalize_image_with_mime(uri) for uri in image_base64s],
            return_exceptions=True,
        )
        valid_chunk_indices: List[int] = []
        voyage_inputs: List[str] = []
        for idx, normalized in enumerate(normalized_results):
            if (
                not normalized
                or isinstance(normalized, BaseException)
                or not isinstance(normalized, tuple)
                or len(normalized) != 2
            ):
                self.logger.warning(
                    "Skipping Voyage image index=%s: unable to normalize to base64", idx
                )
                continue
            b64, mime_type = normalized
            voyage_inputs.append(f"data:{mime_type};base64,{b64}")
            valid_chunk_indices.append(idx)

        if not voyage_inputs:
            return []

        async def process_voyage_batch(
            batch_start: int, batch_inputs: List[str]
        ) -> List[PointStruct]:
            """Process a single batch of images with Voyage AI."""
            try:
                embeddings = await self.dense_embeddings.aembed_documents(batch_inputs)
                batch_points = []
                for i, embedding in enumerate(embeddings):
                    chunk_idx = valid_chunk_indices[batch_start + i]
                    image_chunk = image_chunks[chunk_idx]
                    batch_points.append(self._build_image_point(embedding, image_chunk))
                self.logger.info(
                    f"✅ Processed Voyage batch starting at {batch_start}: {len(embeddings)} image embeddings"
                )
                return batch_points
            except Exception as voyage_error:
                self.logger.warning(
                    f"Failed to process Voyage batch starting at {batch_start}: {str(voyage_error)}"
                )
                return []

        batches = []
        for batch_start in range(0, len(voyage_inputs), batch_size):
            batch_end = min(batch_start + batch_size, len(voyage_inputs))
            batches.append((batch_start, voyage_inputs[batch_start:batch_end]))

        concurrency_limit = 5
        semaphore = asyncio.Semaphore(concurrency_limit)

        async def limited_voyage_batch(batch_start: int, batch_inputs: List[str]) -> List[PointStruct]:
            async with semaphore:
                return await process_voyage_batch(batch_start, batch_inputs)

        tasks = [limited_voyage_batch(start, inputs) for start, inputs in batches]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for result in results:
            if isinstance(result, list):
                points.extend(result)
            elif isinstance(result, Exception):
                self.logger.warning(f"Voyage batch processing exception: {str(result)}")

        return points

    async def _process_image_embeddings_bedrock(
        self, image_chunks: List[dict], image_base64s: List[str]
    ) -> List[PointStruct]:
        """Process image embeddings using AWS Bedrock."""
        import json

        import boto3
        from botocore.exceptions import ClientError, NoCredentialsError

        try:
            client_kwargs = {
                "service_name": "bedrock-runtime",
            }
            if self.aws_access_key_id and self.aws_secret_access_key and self.region_name:
                client_kwargs.update({
                    "aws_access_key_id": self.aws_access_key_id,
                    "aws_secret_access_key": self.aws_secret_access_key,
                    "region_name": self.region_name,
                })
            bedrock = boto3.client(**client_kwargs)
        except NoCredentialsError as cred_err:
            raise EmbeddingError(
                "AWS credentials not found for Bedrock image embeddings. Provide awsAccessKeyId/awsAccessSecretKey or configure a credential source."
            ) from cred_err

        points = []

        async def embed_single_bedrock_image(i: int, image_ref: str) -> Optional[PointStruct]:
            """Embed a single image with AWS Bedrock."""
            normalized_b64 = await self._normalize_image_to_base64(image_ref)
            if not normalized_b64:
                self.logger.warning("Skipping image: unable to normalize to base64 (index=%s)", i)
                return None

            # Titan Multimodal supports 256 / 384 / 1024. Honor the UI's
            # "Output Dimensions" so the image vectors match the size
            # the collection was created with (the text probe uses the
            # same value via LangChain at model-construction time).
            request_body = {
                "inputImage": normalized_b64,
                "embeddingConfig": {
                    "outputEmbeddingLength": self.output_dimensions or 1024,
                },
            }

            try:
                loop = asyncio.get_running_loop()
                response = await loop.run_in_executor(
                    None,
                    lambda: bedrock.invoke_model(
                        modelId=self.model_name,
                        body=json.dumps(request_body),
                        contentType='application/json',
                        accept='application/json'
                    )
                )
                response_body = json.loads(response['body'].read())
                image_embedding = response_body['embedding']

                image_chunk = image_chunks[i]
                return self._build_image_point(image_embedding, image_chunk)
            except NoCredentialsError as cred_err:
                raise EmbeddingError(
                    "AWS credentials not found while invoking Bedrock model."
                ) from cred_err
            except ClientError as client_err:
                self.logger.warning("Bedrock image embedding failed for index=%s: %s", i, str(client_err))
                return None
            except Exception as bedrock_err:
                self.logger.warning("Unexpected Bedrock error for image index=%s: %s", i, str(bedrock_err))
                return None

        concurrency_limit = 10
        semaphore = asyncio.Semaphore(concurrency_limit)

        async def limited_bedrock_embed(i: int, image_ref: str) -> Optional[PointStruct]:
            async with semaphore:
                return await embed_single_bedrock_image(i, image_ref)

        tasks = [limited_bedrock_embed(i, img) for i, img in enumerate(image_base64s)]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for result in results:
            if isinstance(result, PointStruct):
                points.append(result)
            elif isinstance(result, Exception):
                self.logger.warning(f"Failed to embed image with Bedrock: {str(result)}")

        return points

    async def _process_image_embeddings_jina(
        self, image_chunks: List[dict], image_base64s: List[str]
    ) -> List[PointStruct]:
        """Process image embeddings using Jina AI."""

        batch_size = 32
        points = []

        async def process_jina_batch(client: httpx.AsyncClient, batch_start: int, batch_images: List[str]) -> List[PointStruct]:
            """Process a single batch of images with Jina AI."""
            try:
                url = 'https://api.jina.ai/v1/embeddings'
                headers = {
                    'Content-Type': 'application/json',
                    'Authorization': 'Bearer ' + self.api_key
                }
                normalized_images = await asyncio.gather(*[
                    self._normalize_image_to_base64(image_base64)
                    for image_base64 in batch_images
                ])
                # Track which original indices correspond to successfully normalized images
                valid_indices = []
                valid_normalized_images = []
                for idx, normalized_b64 in enumerate(normalized_images):
                    if normalized_b64 is not None:
                        valid_indices.append(batch_start + idx)
                        valid_normalized_images.append(normalized_b64)

                if not valid_normalized_images:
                    self.logger.warning(
                        f"No valid images in Jina AI batch starting at {batch_start} after normalization"
                    )
                    return []

                data = {
                    "model": self.model_name,
                    "input": [
                        {"image": normalized_b64}
                        for normalized_b64 in valid_normalized_images
                    ]
                }

                response = await client.post(url, headers=headers, json=data)
                response_body = response.json()
                embeddings = [r["embedding"] for r in response_body["data"]]

                batch_points = []
                for i, embedding in enumerate(embeddings):
                    # Use the tracked valid index instead of simple increment
                    chunk_idx = valid_indices[i]
                    image_chunk = image_chunks[chunk_idx]
                    batch_points.append(self._build_image_point(embedding, image_chunk))
                self.logger.info(
                    f"✅ Processed Jina AI batch starting at {batch_start}: {len(embeddings)} image embeddings"
                )
                return batch_points
            except Exception as jina_error:
                self.logger.warning(
                    f"Failed to process Jina AI batch starting at {batch_start}: {str(jina_error)}"
                )
                return []

        async with httpx.AsyncClient(timeout=60.0) as client:
            batches = []
            for batch_start in range(0, len(image_base64s), batch_size):
                batch_end = min(batch_start + batch_size, len(image_base64s))
                batch_images = image_base64s[batch_start:batch_end]
                batches.append((batch_start, batch_images))

            concurrency_limit = 5
            semaphore = asyncio.Semaphore(concurrency_limit)

            async def limited_process_batch(batch_start: int, batch_images: List[str]) -> List[PointStruct]:
                async with semaphore:
                    return await process_jina_batch(client, batch_start, batch_images)

            tasks = [limited_process_batch(start, imgs) for start, imgs in batches]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            for result in results:
                if isinstance(result, list):
                    points.extend(result)
                elif isinstance(result, Exception):
                    self.logger.warning(f"Jina AI batch processing exception: {str(result)}")

        return points

    async def _process_image_embeddings_gemini(
        self, image_chunks: List[dict], image_base64s: List[str]
    ) -> List[PointStruct]:
        """Embed images via the Gemini ``embedContent`` REST endpoint.

        Works with Gemini's multimodal embedding models
        (``gemini-embedding-2-preview`` and newer). The same endpoint
        exists for text-only models (``gemini-embedding-001``) but
        returns HTTP 400 for ``inline_data`` parts — we surface that as
        :class:`EmbeddingError` so the caller can fall back to VLM
        captioning instead of silently emitting partial points.

        The user-configured ``Output Dimensions`` value is forwarded as
        ``output_dimensionality`` so the image vectors match the size
        the collection was created with (the text probe at startup uses
        the same SDK-level setting via ``GoogleGenerativeAIEmbeddings``).
        """
        model = (self.model_name or "").removeprefix("models/")
        if not model:
            raise EmbeddingError(
                "Gemini embedding model name is missing; cannot build "
                "embedContent URL for image input."
            )
        if not self.api_key:
            raise EmbeddingError(
                "Gemini API key is not configured; cannot embed images."
            )

        url = (
            "https://generativelanguage.googleapis.com/v1beta/"
            f"models/{model}:embedContent"
        )
        headers = {
            "Content-Type": "application/json",
            # Google's current-recommended auth; the legacy ``?key=``
            # query parameter still works but is deprecated in their
            # own REST examples.
            "x-goog-api-key": self.api_key,
        }
        concurrency_limit = 5
        semaphore = asyncio.Semaphore(concurrency_limit)
        # Signalling for the "this Gemini embedding model doesn't accept
        # image input" case (e.g. ``gemini-embedding-001``). We only
        # need to learn this once per batch; after the first hard-400
        # we short-circuit the rest so the caller can fall back to
        # captioning quickly.
        unsupported = asyncio.Event()

        async def embed_single_image(
            client: httpx.AsyncClient, i: int, image_uri: str
        ) -> Optional[PointStruct]:
            if unsupported.is_set():
                return None
            normalized = await self._normalize_image_with_mime(image_uri)
            if not normalized:
                self.logger.warning(
                    "Skipping Gemini image index=%s: unable to normalize to base64", i
                )
                return None
            b64, mime_type = normalized
            body: Dict[str, Any] = {
                "content": {
                    "parts": [
                        {
                            "inline_data": {
                                "mime_type": mime_type,
                                "data": b64,
                            }
                        }
                    ]
                },
                # CRITICAL: text chunks go through LangChain's
                # ``GoogleGenerativeAIEmbeddings.embed_documents`` which
                # sets ``task_type="RETRIEVAL_DOCUMENT"``, and query-time
                # encoding uses ``RETRIEVAL_QUERY``. Gemini's embedding
                # models are trained so that DOC and QUERY vectors align
                # across that asymmetric pairing; without a task type,
                # the API defaults to ``RETRIEVAL_QUERY`` which would
                # leave image vectors in *query* subspace and make them
                # unreachable from a text search (the multimodal
                # retrieval 0-hit bug). Keeping this explicit so the
                # image path stays in sync with the text path.
                "taskType": "RETRIEVAL_DOCUMENT",
            }
            if self.output_dimensions:
                # Per ``ai.google.dev/gemini-api/docs/embeddings``; keep
                # it consistent with the text probe so all vectors in
                # the collection share a dimension.
                body["output_dimensionality"] = self.output_dimensions
            try:
                async with semaphore:
                    response = await client.post(url, headers=headers, json=body)
                if response.status_code == 400:
                    # Treat an explicit 400 as "this model doesn't do
                    # images"; signal the whole batch so callers can
                    # stop and hand off to the caption fallback.
                    unsupported.set()
                    raise EmbeddingError(
                        f"Gemini embedding model '{model}' rejected image "
                        f"input (HTTP 400). Body: {response.text[:300]}"
                    )
                response.raise_for_status()
                data = response.json()
                embedding = (data.get("embedding") or {}).get("values")
                if not embedding:
                    self.logger.warning(
                        "Gemini embedContent returned no embedding for image "
                        "index=%s: %s", i, str(data)[:200]
                    )
                    return None
                chunk = image_chunks[i]
                return self._build_image_point(embedding, chunk)
            except EmbeddingError:
                raise
            except httpx.HTTPStatusError as http_err:
                self.logger.warning(
                    "Gemini embedContent failed for image index=%s: %s %s",
                    i,
                    http_err.response.status_code,
                    http_err.response.text[:200],
                )
                return None
            except Exception as err:
                self.logger.warning(
                    "Unexpected Gemini embedContent error for image index=%s: %s",
                    i,
                    str(err),
                )
                return None

        points: List[PointStruct] = []
        async with httpx.AsyncClient(timeout=60.0) as client:
            tasks = [
                embed_single_image(client, i, img)
                for i, img in enumerate(image_base64s)
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)

        for result in results:
            if isinstance(result, PointStruct):
                points.append(result)
            elif isinstance(result, EmbeddingError):
                # Propagate the first "model doesn't accept images" signal
                # — the caller will switch to captioning.
                raise result
            elif isinstance(result, Exception):
                self.logger.warning(
                    "Failed to embed image with Gemini: %s", str(result)
                )

        if not points and unsupported.is_set():
            raise EmbeddingError(
                f"Gemini embedding model '{model}' does not accept image "
                "inputs; all image requests were rejected with HTTP 400."
            )
        return points

    async def _process_image_embeddings_generic(
        self, image_chunks: List[dict], image_base64s: List[str]
    ) -> List[PointStruct]:
        """Best-effort image embedding for providers without a native
        code path.

        Some multimodal embedding models accept
        ``data:image/...;base64,...`` strings on the same
        ``aembed_documents`` call they use for text. When the user ticks
        ``isMultimodal`` for such a provider we honor that intent by
        attempting the call directly. Any failure raises
        :class:`EmbeddingError` so :meth:`index_documents` can fall back
        to the VLM-caption path without losing data.
        """
        # Short-circuit on empty batches so callers can probe the
        # dispatcher (tests included) without needing a fully
        # initialized embedding model.
        if not image_base64s:
            return []

        if self.dense_embeddings is None:
            raise EmbeddingError(
                "Dense embedding model is not initialized; cannot attempt "
                "generic image embedding."
            )

        inputs: List[str] = []
        keep_indices: List[int] = []
        for i, image_uri in enumerate(image_base64s):
            normalized = await self._normalize_image_with_mime(image_uri)
            if not normalized:
                self.logger.warning(
                    "Skipping image index=%s for generic image embedding: "
                    "unable to normalize to base64", i
                )
                continue
            b64, mime_type = normalized
            inputs.append(f"data:{mime_type};base64,{b64}")
            keep_indices.append(i)

        if not inputs:
            return []

        try:
            embeddings = await self.dense_embeddings.aembed_documents(inputs)
        except Exception as err:
            # Bubble up as EmbeddingError; the caller decides whether to
            # fall back to captioning or surface the error.
            raise EmbeddingError(
                f"Embedding provider '{self.embedding_provider}' did not "
                f"accept image input via aembed_documents: {err}"
            ) from err

        if len(embeddings) != len(inputs):
            raise EmbeddingError(
                f"Embedding provider '{self.embedding_provider}' returned "
                f"{len(embeddings)} embeddings for {len(inputs)} image inputs; "
                "refusing to produce misaligned points."
            )

        points: List[PointStruct] = []
        for vec, original_idx in zip(embeddings, keep_indices):
            if not vec:
                continue
            chunk = image_chunks[original_idx]
            points.append(self._build_image_point(vec, chunk))
        return points

    async def _process_image_embeddings(
        self, image_chunks: List[dict], image_base64s: List[str]
    ) -> List[PointStruct]:
        """Dispatch image embedding to the right handler for the
        configured provider.

        Raises :class:`EmbeddingError` when the attempt fails in a way
        the caller can recover from by falling back to VLM captioning
        (model doesn't actually accept images, SDK raises, etc.).
        """
        if self.embedding_provider == EmbeddingProvider.COHERE.value:
            return await self._process_image_embeddings_cohere(image_chunks, image_base64s)
        elif self.embedding_provider == EmbeddingProvider.VOYAGE.value:
            return await self._process_image_embeddings_voyage(image_chunks, image_base64s)
        elif self.embedding_provider == EmbeddingProvider.AWS_BEDROCK.value:
            return await self._process_image_embeddings_bedrock(image_chunks, image_base64s)
        elif self.embedding_provider == EmbeddingProvider.JINA_AI.value:
            return await self._process_image_embeddings_jina(image_chunks, image_base64s)
        elif self.embedding_provider == EmbeddingProvider.GEMINI.value:
            return await self._process_image_embeddings_gemini(image_chunks, image_base64s)
        elif self.embedding_provider == EmbeddingProvider.AZURE_AI.value:
            # Azure AI Foundry hosts Cohere's multimodal embedding models
            # (e.g. ``embed-v-4-0``). The user has asserted the deployment
            # is multimodal via ``isMultimodal``; reuse the Cohere client
            # with the Azure endpoint as ``base_url`` so image inputs flow
            # through Cohere's native ``/embed`` route instead of the
            # OpenAI-compatible ``/embeddings`` route (which is text-only).
            base_url = _normalize_azure_ai_endpoint_for_cohere(self.endpoint)
            if not base_url:
                raise EmbeddingError(
                    "Azure AI endpoint is not configured; cannot embed "
                    "images. Set the embedding configuration's 'endpoint' "
                    "field to the Azure AI Foundry resource URL."
                )
            return await self._process_image_embeddings_cohere(
                image_chunks, image_base64s, base_url=base_url
            )
        else:
            # ``isMultimodal`` is the user's assertion that the model
            # accepts images. Honor it for providers we don't have a
            # native path for by attempting the LangChain Embeddings
            # interface. If the SDK rejects image inputs, the generic
            # helper raises :class:`EmbeddingError`, which the caller
            # uses to trigger the VLM-caption fallback.
            return await self._process_image_embeddings_generic(
                image_chunks, image_base64s
            )

    async def _store_image_points(self, points: List[PointStruct]) -> None:
        """Store image embedding points in the vector database."""
        if points:
            start_time = time.perf_counter()
            self.logger.info(f"⏱️ Starting image embeddings insertion for {len(points)} points")

            loop = asyncio.get_running_loop()
            await loop.run_in_executor(
                None,
                lambda: self.vector_db_service.upsert_points(
                    collection_name=self.collection_name, points=points
                ),
            )

            elapsed_time = time.perf_counter() - start_time
            self.logger.info(
                f"✅ Successfully added {len(points)} image embeddings to vector store in {elapsed_time:.2f}s"
            )
        else:
            self.logger.info(
                "No image embeddings to upsert; all images were skipped or failed to embed"
            )

    def _is_local_cpu_embedding(self) -> bool:
        """True when embedding model runs locally on CPU (default or sentence transformers)."""
        return (
            self.embedding_provider is None
            or self.embedding_provider == EmbeddingProvider.DEFAULT.value
            or self.embedding_provider == EmbeddingProvider.SENTENCE_TRANSFOMERS.value
        )

    async def _process_document_chunks(self, langchain_document_chunks: List[Document]) -> None:
        """Process and store document chunks in the vector store."""
        time.perf_counter()
        # Final safety net: drop chunks that would cause the hybrid
        # (dense + sparse) Qdrant batch to raise
        # "Mismatched length between dense and sparse embeddings".
        # This happens when the cloud dense embedder and FastEmbedSparse
        # disagree about whether an input is "embeddable". We require a
        # non-empty string that contains at least one alphanumeric
        # character so that strings consisting only of punctuation,
        # whitespace or zero-width/control characters are filtered out
        # before the batch is built.
        def _is_embeddable(text: object) -> bool:
            if not isinstance(text, str):
                return False
            if not text.strip():
                return False
            return any(ch.isalnum() for ch in text)

        original_count = len(langchain_document_chunks)
        langchain_document_chunks = [
            doc for doc in langchain_document_chunks
            if _is_embeddable(doc.page_content)
        ]
        dropped = original_count - len(langchain_document_chunks)
        if dropped:
            self.logger.warning(
                f"🧹 Dropped {dropped}/{original_count} chunks with empty/non-embeddable page_content before embedding"
            )
        if not langchain_document_chunks:
            self.logger.warning("⚠️ No non-empty chunks to embed; skipping vector store insertion")
            return

        self.logger.info(f"⏱️ Starting langchain document embeddings insertion for {len(langchain_document_chunks)} documents")

        use_local_sequential = self._is_local_cpu_embedding()
        batch_size = (
            _LOCAL_CPU_DOCUMENT_BATCH_SIZE if use_local_sequential else _DEFAULT_DOCUMENT_BATCH_SIZE
        )

        async def _upsert_single_document(doc: Document, position: int) -> bool:
            """Insert a single document, returning True on success and False if
            this specific chunk must be skipped (e.g. the dense embedder
            silently rejects it while BM25 accepts it, producing a mismatch)."""
            try:
                await self.vector_store.aadd_documents([doc])
                return True
            except ValueError as single_ve:
                if "Mismatched length between dense and sparse embeddings" in str(single_ve):
                    preview = (doc.page_content or "")[:200].replace("\n", " ")
                    self.logger.warning(
                        f"⚠️ Skipping chunk at position {position}: dense embedder "
                        f"returned no vector while sparse succeeded (or vice versa). "
                        f"content_len={len(doc.page_content or '')}, preview={preview!r}"
                    )
                    return False
                raise
            except Exception as single_err:
                preview = (doc.page_content or "")[:200].replace("\n", " ")
                self.logger.warning(
                    f"⚠️ Skipping chunk at position {position} due to embedding error: "
                    f"{str(single_err)}. content_len={len(doc.page_content or '')}, preview={preview!r}"
                )
                return False

        async def process_document_batch(batch_start: int, batch_documents: List[Document]) -> int:
            """Process a single batch of documents.

            When the hybrid Qdrant store raises
            "Mismatched length between dense and sparse embeddings" it means
            either the dense embedder (e.g. cloud API) or FastEmbed BM25 has
            silently dropped one or more non-empty inputs. We can't tell which
            entry is responsible from the batch call, so we retry the batch
            one document at a time to isolate and skip the offending chunk(s)
            instead of failing the entire record.
            """
            try:
                await self.vector_store.aadd_documents(batch_documents)
                self.logger.debug(
                    f"✅ Processed document batch starting at {batch_start}: {len(batch_documents)} documents"
                )
                return len(batch_documents)
            except ValueError as ve:
                if "Mismatched length between dense and sparse embeddings" not in str(ve):
                    self.logger.warning(
                        f"Failed to process document batch starting at {batch_start}: {str(ve)}"
                    )
                    raise
                self.logger.warning(
                    f"⚠️ Dense/sparse length mismatch in batch at {batch_start} "
                    f"({len(batch_documents)} docs). Falling back to per-document "
                    "insertion to isolate the offending chunk(s)."
                )
                added = 0
                for offset, doc in enumerate(batch_documents):
                    if await _upsert_single_document(doc, batch_start + offset):
                        added += 1
                self.logger.info(
                    f"✅ Per-document fallback inserted {added}/{len(batch_documents)} "
                    f"chunks from batch at {batch_start}"
                )
                return added
            except Exception as batch_error:
                self.logger.warning(
                    f"Failed to process document batch starting at {batch_start}: {str(batch_error)}"
                )
                raise

        batches = []
        for batch_start in range(0, len(langchain_document_chunks), batch_size):
            batch_end = min(batch_start + batch_size, len(langchain_document_chunks))
            batch_documents = langchain_document_chunks[batch_start:batch_end]
            batches.append((batch_start, batch_documents))

        if use_local_sequential:
            # Process one batch at a time, no concurrent tasks - avoids CPU/memory thrashing
            for i, (batch_start, batch_documents) in enumerate(batches):
                try:
                    await process_document_batch(batch_start, batch_documents)
                except Exception as e:
                    self.logger.error(f"Document batch {i} failed: {str(e)}")
                    raise VectorStoreError(
                        f"Failed to store document batch {i} in vector store: {str(e)}",
                        details={"error": str(e), "batch_index": i},
                    )
        else:
            concurrency_limit = _DEFAULT_CONCURRENCY_LIMIT
            semaphore = asyncio.Semaphore(concurrency_limit)

            async def limited_process_batch(batch_start: int, batch_documents: List[Document]) -> int:
                async with semaphore:
                    return await process_document_batch(batch_start, batch_documents)

            tasks = [limited_process_batch(start, docs) for start, docs in batches]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            for i, result in enumerate(results):
                if isinstance(result, Exception):
                    self.logger.error(f"Document batch {i} failed: {str(result)}")
                    raise VectorStoreError(
                        f"Failed to store document batch {i} in vector store: {str(result)}",
                        details={"error": str(result), "batch_index": i},
                    )



    async def _create_embeddings(
        self,
        chunks: List[Document],
        record_id: str,
        virtual_record_id: str,
        caption_fallback: Optional[
            Callable[[List[dict]], Awaitable[List[Document]]]
        ] = None,
    ) -> None:
        """
        Create both sparse and dense embeddings for document chunks and store them in vector store.
        Handles both text and image embeddings

        Args:
            chunks: List of document chunks to embed
            record_id: Record ID for status updates
            virtual_record_id: Virtual record ID for filtering embeddings
            caption_fallback: Optional async callable that converts pending
                image chunks into text ``Document`` objects via a VLM.
                Invoked when direct image embedding raises
                :class:`EmbeddingError` (e.g. the deployed embedding model
                doesn't actually accept images). When absent, image
                chunks that fail to embed are silently dropped with an
                error log — matching the prior "no data loss" contract
                for at least the text side of the record.

        Raises:
            EmbeddingError: If there's an error creating embeddings
            VectorStoreError: If there's an error storing embeddings
            MetadataProcessingError: If there's an error processing metadata
            DocumentProcessingError: If there's an error updating document status
        """
        try:
            if not chunks:
                raise EmbeddingError("No chunks provided for embedding creation")

            # Separate chunks by type
            langchain_document_chunks = []
            image_chunks = []
            for chunk in chunks:
                if isinstance(chunk, Document):
                    langchain_document_chunks.append(chunk)
                else:
                    image_chunks.append(chunk)

            await self.delete_embeddings(virtual_record_id)

            self.logger.info(
                f"📊 Processing {len(langchain_document_chunks)} langchain document chunks and {len(image_chunks)} image chunks"
            )

            # Process image chunks if any
            if image_chunks:
                image_base64s = [chunk.get("image_uri") for chunk in image_chunks]
                try:
                    points = await self._process_image_embeddings(
                        image_chunks, image_base64s
                    )
                    await self._store_image_points(points)
                except EmbeddingError as embed_err:
                    # The provider's embedding API rejected image input
                    # (most often: ``isMultimodal`` was toggled on for a
                    # text-only model like ``gemini-embedding-001``).
                    # Fall back to VLM captioning so the images still
                    # become searchable instead of disappearing.
                    fallback_action = (
                        "falling back to VLM captions"
                        if caption_fallback is not None
                        else "no multimodal LLM is configured, image blocks will be skipped"
                    )
                    self.logger.warning(
                        "⚠️ Direct image embedding failed for %d image(s): %s. %s.",
                        len(image_chunks),
                        embed_err,
                        fallback_action,
                    )
                    if caption_fallback is not None:
                        try:
                            caption_docs = await caption_fallback(image_chunks)
                        except Exception as caption_err:
                            self.logger.error(
                                "VLM caption fallback failed: %s", caption_err
                            )
                            caption_docs = []
                        if caption_docs:
                            langchain_document_chunks.extend(caption_docs)
                            self.logger.info(
                                f"✅ Converted {len(caption_docs)} image(s) to "
                                f"VLM captions for text embedding."
                            )

            # Process document chunks if any
            if langchain_document_chunks:
                try:
                    await self._process_document_chunks(langchain_document_chunks)
                except Exception as e:
                    raise VectorStoreError(
                        "Failed to store langchain documents in vector store: " + str(e),
                        details={"error": str(e)},
                    )

            self.logger.info(f"✅ Embeddings created and stored for record: {record_id}")

        except (
            EmbeddingError,
            VectorStoreError,
            MetadataProcessingError,
            DocumentProcessingError,
        ):
            raise
        except Exception as e:
            raise IndexingError(
                "Unexpected error during embedding creation: " + str(e),
                details={"error": str(e)},
            )

    async def describe_image_async(self, base64_string: str, vlm: BaseChatModel) -> str:
        message = HumanMessage(
            content=[
                {"type": "text", "text": prompt_for_image_description},
                {"type": "image_url", "image_url": {"url": base64_string}},
            ]
        )
        response = await vlm.ainvoke([message])
        return response.content

    async def describe_images(self, base64_images: List[str],vlm:BaseChatModel) -> List[dict]:

        async def describe(i: int, base64_string: str) -> dict:
            try:
                description = await self.describe_image_async(base64_string, vlm)
                return {"index": i, "success": True, "description": description.strip()}
            except Exception as e:
                return {"index": i, "success": False, "error": str(e)}

        # Limit concurrency to avoid memory growth when many images
        concurrency_limit = 10
        semaphore = asyncio.Semaphore(concurrency_limit)

        async def limited_describe(i: int, base64_string: str) -> dict:
            async with semaphore:
                return await describe(i, base64_string)

        tasks = [limited_describe(i, img) for i, img in enumerate(base64_images)]
        results = await asyncio.gather(*tasks)
        return results

    async def index_documents(
        self,
        block_containers: BlocksContainer,
        org_id: str,
        record_id: str,
        virtual_record_id: str,
        mime_type: str,
        block_ids_to_delete: Optional[set] = None,
        is_reconciliation: bool = False,
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
            raise IndexingError(
                "Failed to get LLM: " + str(e),
                details={"error": str(e)},
            )

        blocks = block_containers.blocks
        block_groups = block_containers.block_groups

        try:
            if not blocks and not block_groups:
                if block_ids_to_delete:
                    await self.delete_blocks_by_ids(block_ids_to_delete, virtual_record_id)
                return None

            text_blocks = []
            image_blocks = []
            table_blocks = []
            sql_row_blocks = []

            for block in blocks:
                if hasattr(block.type, 'value'):
                    block_type = str(block.type.value).lower()
                else:
                    block_type = str(block.type).lower()

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
                    if hasattr(block, 'sub_type') and block.sub_type:
                        if hasattr(block.sub_type, 'value'):
                            sub_type = str(block.sub_type.value).lower()
                        else:
                            sub_type = str(block.sub_type).lower()
                    if sub_type in ["sql_table", "sql_view"]:
                        sql_row_blocks.append(block)
                    else:
                        table_blocks.append(block)
                elif block_type in ["table", "table_cell"]:
                    table_blocks.append(block)

            self.logger.info(f"📊 Processing {len(blocks)} blocks and {len(block_groups)} block_groups")
            self.logger.debug(
                f"📊 Block classification: text={len(text_blocks)}, image={len(image_blocks)},"
                f" table={len(table_blocks)}, sql_row={len(sql_row_blocks)}"
            )

            documents_to_embed = []
            # ── Text blocks ──
            if text_blocks:
                try:
                    for block in text_blocks:
                        block_text = block.data
                        # Skip blocks with empty/whitespace-only or non-string text.
                        # Mixing empty strings with non-empty ones into a hybrid
                        # Qdrant batch causes "Mismatched length between dense and
                        # sparse embeddings" because cloud dense embedders often
                        # silently drop empty inputs while FastEmbedSparse does not.
                        if not isinstance(block_text, str) or not block_text.strip():
                            continue
                        metadata = {
                            "virtualRecordId": virtual_record_id,
                            "blockId": block.id,
                            "orgId": org_id,
                            "isBlockGroup": False,
                        }
                        doc = self.nlp(block_text)
                        sentences = [sent.text for sent in doc.sents]
                        if len(sentences) > 1:
                            for sentence in sentences:
                                if not sentence or not sentence.strip():
                                    continue
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
                    # Pair each block with its URI so later branches stay aligned
                    # even when some blocks have missing/invalid payloads.
                    valid_image_blocks: List = []
                    valid_image_uris: List[str] = []
                    for block in image_blocks:
                        image_data = block.data
                        if not isinstance(image_data, dict):
                            continue
                        image_uri = image_data.get("uri")
                        if not isinstance(image_uri, str) or not image_uri.strip():
                            self.logger.warning(
                                f"⚠️ Skipping image block {block.id}: "
                                f"missing/empty 'uri'"
                            )
                            continue
                        valid_image_blocks.append(block)
                        valid_image_uris.append(image_uri)

                    # Decide how to embed the images. The UI exposes a
                    # generic ``isMultimodal`` toggle; we trust it. When
                    # the user marks the embedding model as multimodal we
                    # attempt direct image embedding for *any* provider
                    # (native path if we have one, generic LangChain path
                    # otherwise). If that attempt later raises
                    # :class:`EmbeddingError` — which happens when the
                    # deployed model is actually text-only despite the
                    # toggle (e.g. ``gemini-embedding-001``) — the
                    # caller in :meth:`_create_embeddings` catches it and
                    # we fall back here to VLM captions + text
                    # embeddings, preserving the data.
                    provider_can_embed_images = bool(is_multimodal_embedding)
                    should_caption = (
                        bool(valid_image_uris)
                        and not provider_can_embed_images
                        and is_multimodal_llm
                    )

                    if valid_image_uris and not provider_can_embed_images:
                        if should_caption:
                            self.logger.info(
                                f"Embedding provider '{self.embedding_provider}' is "
                                f"not marked as multimodal; using VLM captions + "
                                f"text embeddings for {len(valid_image_uris)} "
                                f"image block(s)."
                            )
                        else:
                            self.logger.error(
                                f"❌ Embedding provider '{self.embedding_provider}' "
                                f"is not marked as multimodal and no multimodal "
                                f"LLM is configured to describe images. "
                                f"{len(valid_image_uris)} image block(s) will be "
                                f"skipped. Either enable 'Multimodal' on the "
                                f"embedding model, or configure a multimodal LLM "
                                f"so images can be captioned."
                            )

                    if provider_can_embed_images:
                        for block, image_uri in zip(valid_image_blocks, valid_image_uris):
                            metadata = {
                                "virtualRecordId": virtual_record_id,
                                "blockId": block.id,
                                "orgId": org_id,
                                "isBlock": True,
                                "isBlockGroup": False,
                            }
                            documents_to_embed.append(
                                {"image_uri": image_uri, "metadata": metadata}
                            )
                    elif should_caption:
                        description_results = await self.describe_images(
                            valid_image_uris, llm
                        )
                        for result, block in zip(description_results, valid_image_blocks):
                            if not result["success"]:
                                self.logger.warning(
                                    f"⚠️ VLM caption failed for image block "
                                    f"{block.id}: {result.get('error')}"
                                )
                                continue
                            description = result["description"]
                            # Skip when the VLM returned nothing usable to
                            # avoid empty page_content in the embedding batch.
                            if not isinstance(description, str) or not description.strip():
                                continue
                            metadata = {
                                "virtualRecordId": virtual_record_id,
                                "blockId": block.id,
                                "orgId": org_id,
                                "isBlock": True,
                                "isBlockGroup": False,
                            }
                            documents_to_embed.append(
                                Document(
                                    page_content=description, metadata=metadata
                                )
                            )
                except Exception as e:
                    raise DocumentProcessingError(
                        "Failed to create image document objects: " + str(e),
                        details={"error": str(e)},
                    )

            # ── Block groups (SQL tables/views and regular tables) ──
            for block_group in block_groups:
                if hasattr(block_group.type, 'value'):
                    block_group_type = str(block_group.type.value).lower()
                else:
                    block_group_type = str(block_group.type).lower()

                sub_type = ""
                if hasattr(block_group, 'sub_type') and block_group.sub_type:
                    if hasattr(block_group.sub_type, 'value'):
                        sub_type = str(block_group.sub_type.value).lower()
                    else:
                        sub_type = str(block_group.sub_type).lower()

                if block_group_type in ["table", "view"] and sub_type in ["sql_table", "sql_view"]:
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

                        if isinstance(ddl, str) and ddl.strip():
                            combined_content_parts = []
                            if isinstance(table_summary, str) and table_summary.strip():
                                combined_content_parts.append(f"/* Table Description:\n{table_summary}\n*/")
                            combined_content_parts.append(ddl)
                            combined_content = "\n\n".join(combined_content_parts)

                            documents_to_embed.append(Document(
                                page_content=combined_content,
                                metadata={
                                    **sql_base_metadata,
                                },
                            ))
                            self.logger.info(f"📊 Added SQL TABLE DDL+Summary for embedding: {fqn}")

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
                            view_context_parts.append(f"-- Source Tables: {', '.join(source_tables)}")
                        if comment:
                            view_context_parts.append(f"-- Comment: {comment}")
                        if source_tables_summary:
                            view_context_parts.append(f"-- Source Table Schemas:\n{source_tables_summary}")
                        if source_table_ddls:
                            view_context_parts.append("-- Source Table DDLs:")
                            for table_fqn, ddl_text in source_table_ddls.items():
                                view_context_parts.append(f"-- {table_fqn}:\n{ddl_text}")
                        if definition:
                            view_context_parts.append(f"\n{definition}")

                        view_context = "\n".join(view_context_parts)
                        if len(view_context.strip()) > len(f"-- View: {fqn}"):
                            documents_to_embed.append(Document(
                                page_content=view_context,
                                metadata={
                                    **sql_base_metadata,
                                },
                            ))
                            self.logger.info(
                                f"📊 Added SQL VIEW {'definition' if definition else 'metadata'} for embedding: {fqn}"
                            )
                        else:
                            self.logger.warning(
                                f"⚠️ SQL VIEW {fqn} has no embeddable content, skipping embedding"
                            )

                elif block_group_type in ["table"]:
                    table_data = block_group.data
                    if table_data:
                        table_summary = table_data.get("table_summary", "")
                        # Guard against empty/whitespace strings — mixing
                        # them with non-empty inputs breaks hybrid
                        # dense/sparse batches on cloud embedders.
                        if isinstance(table_summary, str) and table_summary.strip():
                            documents_to_embed.append(Document(
                                page_content=table_summary,
                                metadata={
                                    "virtualRecordId": virtual_record_id,
                                    "blockId": block_group.id,
                                    "orgId": org_id,
                                    "isBlock": False,
                                    "isBlockGroup": True,
                                },
                            ))

            sql_rows_embedded = 0
            for block in sql_row_blocks:
                block_data = block.data or {}
                row_text = block_data.get("row_natural_language_text", "")
                if isinstance(row_text, str) and row_text.strip():
                    documents_to_embed.append(Document(
                        page_content=row_text,
                        metadata={
                            "virtualRecordId": virtual_record_id,
                            "blockId": block.id,
                            "orgId": org_id,
                            "isBlock": True,
                            "isBlockGroup": False,
                        },
                    ))
                    sql_rows_embedded += 1

            if sql_rows_embedded > 0:
                self.logger.debug(f"📊 Added {sql_rows_embedded} SQL row(s) for embedding")

            # ── Regular table blocks ──
            for block in table_blocks:
                if hasattr(block.type, 'value'):
                    block_type = str(block.type.value).lower()
                else:
                    block_type = str(block.type).lower()

                if block_type in ["table"]:
                    table_data = block.data
                    if table_data:
                        table_summary = table_data.get("table_summary", "")
                        if isinstance(table_summary, str) and table_summary.strip():
                            documents_to_embed.append(Document(
                                page_content=table_summary,
                                metadata={
                                    "virtualRecordId": virtual_record_id,
                                    "blockId": block.id,
                                    "orgId": org_id,
                                    "isBlock": False,
                                    "isBlockGroup": True,
                                },
                            ))
                elif block_type == "table_row":
                    table_data = block.data
                    if table_data:
                        table_row_text = table_data.get("row_natural_language_text")
                        if isinstance(table_row_text, str) and table_row_text.strip():
                            documents_to_embed.append(Document(
                                page_content=table_row_text,
                                metadata={
                                    "virtualRecordId": virtual_record_id,
                                    "blockId": block.id,
                                    "orgId": org_id,
                                    "isBlock": True,
                                    "isBlockGroup": False,
                                },
                            ))

            if not documents_to_embed:
                self.logger.warning("⚠️ No documents to embed after filtering by block type")
                if block_ids_to_delete:
                    await self.delete_blocks_by_ids(block_ids_to_delete, virtual_record_id)
                return True

            # Prepare a caption fallback so image chunks that fail direct
            # embedding (e.g. the user ticked ``isMultimodal`` for a
            # model that doesn't actually accept images, such as the
            # text-only ``gemini-embedding-001``) are recovered as VLM
            # captions instead of being silently dropped. Multimodal
            # embedders like ``gemini-embedding-2-preview`` never hit
            # this path — they succeed natively.
            caption_fallback: Optional[
                Callable[[List[dict]], Awaitable[List[Document]]]
            ] = None
            if is_multimodal_llm:
                async def caption_fallback(
                    pending_image_chunks: List[dict],
                ) -> List[Document]:
                    uris = [c.get("image_uri") for c in pending_image_chunks]
                    results = await self.describe_images(uris, llm)
                    docs: List[Document] = []
                    for result, chunk in zip(results, pending_image_chunks):
                        if not result.get("success"):
                            self.logger.warning(
                                "⚠️ VLM caption failed for image chunk: %s",
                                result.get("error"),
                            )
                            continue
                        description = result.get("description")
                        if (
                            not isinstance(description, str)
                            or not description.strip()
                        ):
                            continue
                        docs.append(
                            Document(
                                page_content=description,
                                metadata=dict(chunk.get("metadata", {})),
                            )
                        )
                    return docs

            # ── Create and store embeddings ──
            try:
                if is_reconciliation:
                    # Reconciliation operates on a pre-classified subset of
                    # blocks (only the ones whose checksum changed), so we
                    # route text and image chunks to their specialised
                    # processors directly rather than going through
                    # :meth:`_create_embeddings`, which would otherwise
                    # re-run ``delete_embeddings`` on the whole virtual
                    # record and wipe the blocks we're NOT touching here.
                    #
                    # That said, we still want the same "model rejected
                    # images → fall back to VLM captions" behaviour the
                    # full-index path has: without it, a user toggling
                    # ``isMultimodal`` on a text-only model (e.g.
                    # ``gemini-embedding-001``) would lose the image blocks
                    # on every reconciliation until they either fix the
                    # config or re-index. Wire the same fallback in.
                    langchain_docs = [d for d in documents_to_embed if isinstance(d, Document)]
                    image_chunks = [d for d in documents_to_embed if not isinstance(d, Document)]

                    if image_chunks:
                        image_base64s = [c.get("image_uri") for c in image_chunks]
                        try:
                            points = await self._process_image_embeddings(
                                image_chunks, image_base64s
                            )
                            await self._store_image_points(points)
                        except EmbeddingError as embed_err:
                            fallback_action = (
                                "falling back to VLM captions"
                                if caption_fallback is not None
                                else "no multimodal LLM is configured, image "
                                "blocks will be skipped"
                            )
                            self.logger.warning(
                                "⚠️ Reconciliation: direct image embedding "
                                "failed for %d image(s): %s. %s.",
                                len(image_chunks),
                                embed_err,
                                fallback_action,
                            )
                            if caption_fallback is not None:
                                try:
                                    caption_docs = await caption_fallback(image_chunks)
                                except Exception as caption_err:
                                    self.logger.error(
                                        "Reconciliation VLM caption fallback "
                                        "failed: %s",
                                        caption_err,
                                    )
                                    caption_docs = []
                                if caption_docs:
                                    langchain_docs.extend(caption_docs)
                                    self.logger.info(
                                        "✅ Reconciliation: converted %d "
                                        "image(s) to VLM captions for text "
                                        "embedding.",
                                        len(caption_docs),
                                    )

                    if langchain_docs:
                        await self._process_document_chunks(langchain_docs)
                else:
                    await self._create_embeddings(
                        documents_to_embed,
                        record_id,
                        virtual_record_id,
                        caption_fallback=caption_fallback,
                    )
            except Exception as e:
                raise EmbeddingError(
                    "Failed to create or store embeddings: " + str(e),
                    details={"error": str(e)},
                )

            if block_ids_to_delete:
                self.logger.debug(f"📊 Deleting {len(block_ids_to_delete)} removed blocks")
                await self.delete_blocks_by_ids(block_ids_to_delete, virtual_record_id)

            self.logger.debug(f"✅ Indexing complete for record {record_id}: {len(documents_to_embed)} documents")
            return True

        except (IndexingError, VectorStoreError, DocumentProcessingError, EmbeddingError):
            raise
        except Exception as e:
            raise IndexingError(
                f"Unexpected error during indexing: {str(e)}",
                details={"error_type": type(e).__name__},
            )

