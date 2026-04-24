import asyncio
import time
import traceback
from typing import Any, Optional

from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_qdrant import FastEmbedSparse
from qdrant_client import models

from app.config.configuration_service import ConfigurationService
from app.config.constants.ai_models import (
    DEFAULT_EMBEDDING_MODEL,
)

# from langchain_cohere import CohereEmbeddings
from app.config.constants.arangodb import (
    CollectionNames,
    RecordTypes,
)
from app.config.constants.service import config_node_constants
from app.exceptions.embedding_exceptions import EmbeddingModelCreationError
from app.exceptions.fastapi_responses import Status
from app.models.blocks import BlockType, GroupType
from app.modules.transformers.blob_storage import BlobStorage
from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
from app.services.vector_db.interface.vector_db import IVectorDBService
from app.sources.client.http.exception.exception import VectorDBEmptyError
from app.utils.aimodels import (
    get_default_embedding_model,
    get_embedding_model,
    get_generator_model,
)
from app.utils.chat_helpers import (
    get_flattened_results,
    get_record,
)
from app.utils.mimetype_to_extension import get_extension_from_mimetype

# OPTIMIZATION: User data cache with TTL
_user_cache: dict[str, tuple] = {}  # {user_id: (user_data, timestamp)}
USER_CACHE_TTL = 300  # 5 minutes
MAX_USER_CACHE_SIZE = 1000  # Max number of users to keep in cache


valid_group_labels = [
        GroupType.LIST.value,
        GroupType.ORDERED_LIST.value,
        GroupType.FORM_AREA.value,
        GroupType.INLINE.value,
        GroupType.KEY_VALUE_AREA.value,
        GroupType.TEXT_SECTION.value,
    ]

class RetrievalService:
    def __init__(
        self,
        logger,
        config_service: ConfigurationService,
        collection_name: str,
        vector_db_service: IVectorDBService,
        graph_provider: IGraphDBProvider,
        blob_store: BlobStorage,
    ) -> None:
        """
        Initialize the retrieval service with necessary configurations.

        Args:
            collection_name: Name of the collection
            vector_db_service: Vector DB service
            config_service: Configuration service
            graph_provider: Graph database provider
        """

        self.logger = logger
        self.config_service = config_service
        self.llm = None
        self.graph_provider = graph_provider
        self.blob_store = blob_store
        # Initialize sparse embeddings
        try:
            self.sparse_embeddings = FastEmbedSparse(model_name="Qdrant/BM25")
        except Exception as e:
            self.logger.error("Failed to initialize sparse embeddings: " + str(e))
            self.sparse_embeddings = None
            raise Exception(
                "Failed to initialize sparse embeddings: " + str(e),
            ) from e
        self.vector_db_service = vector_db_service
        self.collection_name = collection_name
        self.logger.info(f"Retrieval service initialized with collection name: {self.collection_name}")
        self.embedding_model = None
        self.embedding_size = None
        self.embedding_model_instance = None
        # Cache signature for the currently-loaded embedding instance. A tuple
        # of (provider, model_name, dimensions, is_default). Using a full
        # signature (not just the model name) ensures the instance is
        # rebuilt when the user changes dimensions, swaps providers, or flips
        # the default flag — otherwise a dim-only change would reuse a stale
        # instance and trigger "Existing Qdrant collection is configured for
        # dense vectors with N dimensions" on the next query.
        self.embedding_signature: Optional[tuple] = None
        # Serialize concurrent embedding model loads so we only build the model
        # once even if multiple requests race during warmup. Safe to create at
        # import-time on Python 3.10+ (no running loop required).
        self._embedding_model_lock = asyncio.Lock()

    async def get_llm_instance(self, use_cache: bool = True) -> Optional[BaseChatModel]:
        try:
            self.logger.info("Getting LLM")
            ai_models = await self.config_service.get_config(
                config_node_constants.AI_MODELS.value,
                use_cache=use_cache
            )
            llm_configs = ai_models["llm"]

            # For now, we'll use the first available provider that matches our supported types
            # We will add logic to choose a specific provider based on our needs

            for config in llm_configs:
                if config.get("isDefault", False):
                    provider = config["provider"]
                    self.llm = get_generator_model(provider, config)
                if self.llm:
                    break

            if not self.llm:
                self.logger.info("No default LLM found, using first available provider")

            if not self.llm:
                for config in llm_configs:
                    provider = config["provider"]
                    self.llm = get_generator_model(provider, config)
                    if self.llm:
                        break
                if not self.llm:
                    raise ValueError("No supported LLM provider found in configuration")

            self.logger.info("LLM created successfully")
            return self.llm
        except Exception as e:
            self.logger.error(f"Error getting LLM: {str(e)}")
            return None

    async def _get_current_embedding_selection(
        self, use_cache: bool = True
    ) -> tuple[Optional[dict], Optional[str], tuple]:
        """Return the currently-configured embedding config, its model name,
        and a cache signature used to decide whether the in-memory instance
        needs to be rebuilt.

        Signature fields: (provider, model_name, dimensions, is_default).
        When no embedding config exists we fall back to the built-in default
        (HuggingFace BGE), and the signature reflects that so a later switch
        to a real provider invalidates the cache.
        """
        try:
            ai_models = await self.config_service.get_config(
                config_node_constants.AI_MODELS.value,
                use_cache=use_cache,
            )
            if ai_models and ai_models.get("embedding"):
                configs = ai_models["embedding"]
                selected = next(
                    (c for c in configs if c.get("isDefault", False)), None
                )
                if not selected:
                    selected = configs[0]
                provider = selected.get("provider")
                configuration = selected.get("configuration") or {}
                model_name = configuration.get("model")
                dims = configuration.get("dimensions")
                signature = (
                    provider,
                    model_name,
                    dims,
                    bool(selected.get("isDefault", False)),
                )
                return selected, model_name, signature
        except Exception as e:
            self.logger.error(
                f"Error resolving current embedding selection: {str(e)}"
            )
        # Default fallback — encode it in the signature so switching away
        # from default later rebuilds the instance.
        return None, DEFAULT_EMBEDDING_MODEL, ("default", DEFAULT_EMBEDDING_MODEL, None, True)

    async def get_embedding_model_instance(self, use_cache: bool = True) -> Optional[Embeddings]:
        try:
            selected_config, embedding_model, signature = await self._get_current_embedding_selection(
                use_cache
            )

            # Fast path: identical signature already loaded, no lock needed.
            # We key on the full signature (provider + model + dimensions +
            # isDefault) so that a user flipping any one of these in the UI
            # actually rebuilds the instance.
            if (
                self.embedding_signature == signature
                and self.embedding_model_instance is not None
            ):
                return self.embedding_model_instance

            # Serialize the (potentially very slow) model load so concurrent
            # callers don't all download / instantiate the embedding model.
            async with self._embedding_model_lock:
                # Re-check after acquiring the lock in case another coroutine
                # already finished loading while we were waiting.
                if (
                    self.embedding_signature == signature
                    and self.embedding_model_instance is not None
                ):
                    return self.embedding_model_instance

                try:
                    if (
                        not embedding_model
                        or embedding_model == DEFAULT_EMBEDDING_MODEL
                        or selected_config is None
                    ):
                        self.logger.info("Using default embedding model")
                        effective_model = DEFAULT_EMBEDDING_MODEL
                        # HuggingFaceEmbeddings(...) may download ~1GB+ and load a
                        # transformer, which blocks the event loop. Offload to a
                        # worker thread so the server stays responsive.
                        dense_embeddings = await asyncio.to_thread(get_default_embedding_model)
                    else:
                        self.logger.info(
                            f"Using embedding model: {embedding_model} "
                            f"(provider={selected_config.get('provider')}, dims={selected_config.get('configuration', {}).get('dimensions')})"
                        )
                        # Provider clients (Bedrock/OpenAI/etc.) may also do
                        # blocking I/O on construction, so offload here too.
                        dense_embeddings = await asyncio.to_thread(
                            get_embedding_model,
                            selected_config["provider"],
                            selected_config,
                        )
                        effective_model = embedding_model

                except Exception as e:
                    self.logger.error(f"Error creating embedding model: {str(e)}")
                    raise EmbeddingModelCreationError(
                        f"Failed to create embedding model: {str(e)}"
                    ) from e

                self.logger.info(
                    f"Using embedding model: {getattr(effective_model, 'model', effective_model)}"
                )
                self.embedding_model = embedding_model
                self.embedding_model_instance = dense_embeddings
                self.embedding_signature = signature
                return dense_embeddings
        except Exception as e:
            self.logger.error(f"Error getting embedding model: {str(e)}")
            return None

    async def get_current_embedding_model_name(self, use_cache: bool = True) -> Optional[str]:
        """Get the current embedding model name from configuration or instance.

        When AI_MODELS.embedding contains multiple entries, prefer the one
        flagged ``isDefault: True`` — that is the model actually in use and
        the one that would have built any existing index. Fall back to the
        first configured entry for backward compatibility with configs that
        predate the flag.
        """
        try:
            default = await self.get_current_embedding_config(use_cache=use_cache)
            if default and default.get("configuration", {}).get("model"):
                return default["configuration"]["model"]
            return DEFAULT_EMBEDDING_MODEL
        except Exception as e:
            self.logger.error(f"Error getting current embedding model name: {str(e)}")
            return DEFAULT_EMBEDDING_MODEL

    async def get_current_embedding_config(
        self, use_cache: bool = True
    ) -> Optional[dict]:
        """Return the active embedding config entry from AI_MODELS.

        Picks the entry marked ``isDefault: True`` when present; otherwise
        falls back to the first entry that has ``configuration.model`` set.
        Returns ``None`` if no valid entry exists.
        """
        try:
            ai_models = await self.config_service.get_config(
                config_node_constants.AI_MODELS.value,
                use_cache=use_cache,
            )
            embeddings = (ai_models or {}).get("embedding") or []
            if not embeddings:
                return None

            def _has_model(cfg: dict) -> bool:
                return bool(
                    isinstance(cfg, dict)
                    and cfg.get("configuration")
                    and cfg["configuration"].get("model")
                )

            for cfg in embeddings:
                if _has_model(cfg) and cfg.get("isDefault") is True:
                    return cfg

            for cfg in embeddings:
                if _has_model(cfg):
                    return cfg

            return None
        except Exception as e:
            self.logger.error(f"Error getting current embedding config: {str(e)}")
            return None

    def get_embedding_model_name(self, dense_embeddings: Embeddings) -> Optional[str]:
        if hasattr(dense_embeddings, "model_name"):
            return dense_embeddings.model_name
        elif hasattr(dense_embeddings, "model"):
            return dense_embeddings.model
        else:
            return None

    async def _preprocess_query(self, query: str) -> str:
        """
        Preprocess the query text.

        Args:
            query: Raw query text

        Returns:
            Preprocessed query text
        """
        try:
            # Get current model name from config
            model_name = await self.get_current_embedding_model_name(use_cache=False)

            # Check if using BGE model before adding the prefix
            if model_name and "bge" in model_name.lower():
                return f"Represent this document for retrieval: {query.strip()}"
            return query.strip()
        except Exception as e:
            self.logger.error(f"Error in query preprocessing: {str(e)}")
            return query.strip()

    # Base64 alphabet, including URL-safe variants and padding. Kept at
    # module/class scope so membership checks don't rebuild a set on every
    # call.
    _BASE64_ALPHABET = frozenset(
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/=-_"
    )
    # Only treat a string as a bare-base64 image blob when it's at least
    # this long AND passes every structural check below. Tuned to sit
    # comfortably above real-world JWTs (~1–2KB) without being so large
    # that we miss small inline thumbnails: legacy image payloads that
    # made it into ``page_content`` were typically ≥ tens of KB.
    _MIN_BARE_BASE64_LEN = 4096

    @classmethod
    def _looks_like_base64_image(cls, value: Any) -> bool:
        """Best-effort detection for a legacy image point that stored the
        base64 image URI directly in ``page_content``.

        Deliberately conservative: a false negative just means a legacy
        blob is surfaced as-is (same as the old behaviour), while a
        false positive would silently drop user-visible text (e.g. a
        long JWT, hex dump, or minified payload in a real document).

        Accepts two shapes:
          * ``data:image/...;base64,...`` — unambiguous.
          * A bare base64 string that passes **all** of:
              - long enough to plausibly be an image (≥ 4KB);
              - no whitespace anywhere;
              - every character is in the base64 (or URL-safe) alphabet,
                not just the first 256 — the previous prefix-only check
                mis-classified mixed content whose tail contained
                non-base64 punctuation;
              - length is a multiple of 4 (real base64 is always padded);
              - padding (``=``), if present, only appears at the end.

        These together rule out JWTs (contain ``.``), minified JSON/JS
        (contain ``{}:,;``), hex dumps shorter than 4KB, and generally
        any natural-language chunk.
        """
        if not isinstance(value, str):
            return False
        if value.startswith("data:image/"):
            return True

        stripped = value.strip()
        if len(stripped) < cls._MIN_BARE_BASE64_LEN:
            return False
        if len(stripped) % 4 != 0:
            return False
        if any(c.isspace() for c in stripped):
            return False

        alphabet = cls._BASE64_ALPHABET
        first_pad = stripped.find("=")
        if first_pad != -1:
            # Once padding starts it must run to the end, and padding is
            # at most 2 characters.
            tail = stripped[first_pad:]
            if len(tail) > 2 or any(c != "=" for c in tail):
                return False
            body = stripped[:first_pad]
        else:
            body = stripped

        return all(ch in alphabet for ch in body)

    @classmethod
    def _normalise_image_result(cls, metadata: dict, content: Any) -> tuple[dict, str]:
        """Make sure image hits never carry a base64 blob as ``content``.

        The Qdrant payload for image points is deliberately minimal —
        the actual bytes/URI live in blob storage and are resolved
        downstream via ``block.data.uri`` using
        ``virtualRecordId`` + ``blockId``. So all this formatter has to
        do is:

          * New-style: ``metadata.blockType == "image"``. Blank
            ``content`` (it's already empty in the payload, but some
            legacy writers may have left stray text — drop it).
          * Legacy pre-cleanup: no ``blockType`` but ``content`` is a
            base64 blob. Tag it as an image and blank ``content`` so
            the blob never reaches the LLM or the search response.
            Downstream renderers will re-resolve the image from the
            record graph regardless.
          * Plain text: no mutation.
        """
        if not isinstance(metadata, dict):
            return (metadata or {}), (content if isinstance(content, str) else "")

        block_type = metadata.get("blockType")

        if block_type == BlockType.IMAGE.value:
            return metadata, ""

        if cls._looks_like_base64_image(content):
            metadata = {
                **metadata,
                "blockType": BlockType.IMAGE.value,
            }
            return metadata, ""

        return metadata, content if isinstance(content, str) else ""

    def _format_results(self, results: list[tuple]) -> list[dict[str, Any]]:
        """Format search results into a consistent structure with flattened metadata."""
        formatted_results = []
        for doc, score in results:
            metadata, content = self._normalise_image_result(
                dict(doc.metadata or {}), doc.page_content
            )
            formatted_result = {
                "score": float(score),
                "citationType": "vectordb|document",
                "metadata": metadata,
                "content": content,
            }
            formatted_results.append(formatted_result)
        return formatted_results

    async def search_with_filters(
        self,
        queries: list[str],
        user_id: str,
        org_id: str,
        filter_groups: Optional[dict[str, list[str]]] = None,
        limit: int = 20,
        virtual_record_ids_from_tool: Optional[list[str]] = None,
        graph_provider: Optional[IGraphDBProvider] = None,
        knowledge_search:bool = False,
        is_agent:bool = False,
        is_service_account: bool = False,
    ) -> dict[str, Any]:
        """Perform semantic search on accessible records with multiple queries.

        When is_service_account=True, bypasses per-user permission filtering and instead
        fetches ALL records for the configured connectors/KBs (service account "all access").
        """

        try:
            # Get accessible records
            if not self.graph_provider:
                raise ValueError("GraphProvider is required for permission checking")

            filter_groups = filter_groups or {}

            # Extract KB IDs for response metadata
            kb_ids = filter_groups.get('kb', None) if filter_groups else None

            if is_service_account:
                # Service account: bypass user permission checks, use all records for
                # the configured connectors and KBs.
                connector_ids = filter_groups.get('apps', None) if filter_groups else None
                kb_filter_ids = filter_groups.get('kb', None) if filter_groups else None

                # Remove sentinel values
                if kb_filter_ids:
                    kb_filter_ids = [k for k in kb_filter_ids if k and k != "NO_KB_SELECTED"]

                accessible_virtual_id_to_record_id = (
                    await self.graph_provider.get_all_virtual_record_ids_for_knowledge(
                        org_id,
                        connector_ids=connector_ids or [],
                        kb_ids=kb_filter_ids or [],
                    )
                )
                # For URL substitution we still need the user doc, but non-critically
                user = await self._get_user_cached(user_id)
            else:
                # Convert filter_groups to format expected by get_accessible_virtual_record_ids
                filters = {}
                if filter_groups:  # Only process if filter_groups is not empty
                    for key, values in filter_groups.items():
                        # Convert key to match collection naming
                        metadata_key = key.lower()  # e.g., 'departments', 'categories', etc.
                        filters[metadata_key] = values

                init_tasks = [
                    self._get_accessible_virtual_ids_task(user_id, org_id, filters, self.graph_provider),
                    self._get_user_cached(user_id)  # Get user info in parallel with caching
                ]

                accessible_virtual_id_to_record_id, user = await asyncio.gather(*init_tasks)

            if not accessible_virtual_id_to_record_id:
                if is_service_account:
                    self.logger.error(
                        f"No records found for service account agent in org {org_id}. "
                        "Ensure the agent's knowledge sources are configured and indexed."
                    )
                    return self._create_empty_response(
                        "No documents found for this agent's knowledge sources. "
                        "Please ensure knowledge sources are configured and indexed.",
                        Status.ACCESSIBLE_RECORDS_NOT_FOUND
                    )
                self.logger.error(f"No accessible documents found for user {user_id} and org {org_id}")
                return self._create_empty_response("No accessible documents found. Please check your permissions or try different search criteria.", Status.ACCESSIBLE_RECORDS_NOT_FOUND)

            self.logger.info(
                f"[retrieval] accessible virtualRecordIds count={len(accessible_virtual_id_to_record_id)} "
                f"(user_id={user_id}, org_id={org_id})"
            )

            if virtual_record_ids_from_tool:
                filter  = await self.vector_db_service.filter_collection(
                        must={"orgId": org_id,"virtualRecordId": virtual_record_ids_from_tool},
                    )
            else:
                filter = await self.vector_db_service.filter_collection(
                        must={"orgId": org_id},
                        should={"virtualRecordId": list(accessible_virtual_id_to_record_id.keys())}
                    )
            search_results = await self._execute_parallel_searches(queries, filter, limit)

            if not search_results:
                # Promoted from debug -> info so operators can see the zero-hit
                # path without flipping log levels. Accompanied by the filter
                # cardinality so "is it the filter or the vector similarity?"
                # is answerable from a single log line.
                self.logger.info(
                    f"[retrieval] Qdrant returned 0 hits. "
                    f"must.orgId={org_id}, "
                    f"should.virtualRecordId count="
                    f"{len(accessible_virtual_id_to_record_id) if not virtual_record_ids_from_tool else len(virtual_record_ids_from_tool or [])}, "
                    f"collection={self.collection_name}, queries={len(queries)}"
                )
                return self._create_empty_response("No relevant documents found for your search query. Try using different keywords or broader search terms.", Status.EMPTY_RESPONSE)

            self.logger.info(f"[retrieval] Qdrant raw hits={len(search_results)}")

            self.logger.debug("Extracting virtualRecordIds from Qdrant results")
            returned_virtual_record_ids = list({
                result["metadata"]["virtualRecordId"]
                for result in search_results
                if result
                and isinstance(result, dict)
                and result.get("metadata")
                and result["metadata"].get("virtualRecordId") is not None
            })

            self.logger.info(
                f"[retrieval] Qdrant returned {len(returned_virtual_record_ids)} unique virtualRecordIds"
            )

            if not returned_virtual_record_ids:
                return self._create_empty_response("No accessible documents found. Please check your permissions or try different search criteria.", Status.ACCESSIBLE_RECORDS_NOT_FOUND)

            # Resolve only the permission-verified recordIds for the returned virtual IDs.
            # This prevents cross-connector leakage: if multiple connectors share the same
            # virtualRecordId, we only fetch the specific record the user has access to.
            record_ids_to_fetch = list({
                accessible_virtual_id_to_record_id[vid]
                for vid in returned_virtual_record_ids
                if vid in accessible_virtual_id_to_record_id
            })

            self.logger.info(
                f"[retrieval] Fetching {len(record_ids_to_fetch)} records by permission-verified recordIds"
            )
            fetched_records = await self.graph_provider.get_records_by_record_ids(
                record_ids_to_fetch, org_id
            )

            if not fetched_records:
                self.logger.error("Failed to fetch records by record IDs")
                return self._create_empty_response("No accessible documents found. Please check your permissions or try different search criteria.", Status.ACCESSIBLE_RECORDS_NOT_FOUND)

            record_id_to_record_map = {}
            for r in fetched_records:
                if r:
                    record_id_to_record_map[r["_key"]] = r

            virtual_to_record_map = {}
            try:
                self.logger.debug("Creating virtual_to_record_mapping from fetched records")
                virtual_to_record_map = self._create_virtual_to_record_mapping(
                    fetched_records, returned_virtual_record_ids
                )
            except Exception as e:
                self.logger.error("Error in _create_virtual_to_record_mapping: %s", e, exc_info=True)
                raise

            unique_record_ids = {r.get("_key") for r in virtual_to_record_map.values() if r}

            if not unique_record_ids:
                return self._create_empty_response("No accessible documents found. Please check your permissions or try different search criteria.", Status.ACCESSIBLE_RECORDS_NOT_FOUND)
            self.logger.info(f"Unique record IDs count: {len(unique_record_ids)}")

            file_record_ids_to_fetch = []
            mail_record_ids_to_fetch = []
            result_to_record_map = {}  # Map result index to record_id for later URL assignment
            virtual_record_id_to_record = {}
            new_type_results = []
            final_search_results = []
            for idx, result in enumerate(search_results):
                if not result or not isinstance(result, dict):
                    continue
                if not result.get("metadata"):
                    self.logger.warning(f"Result has no metadata: {result}")
                    continue
                virtual_id = result["metadata"].get("virtualRecordId")
                if virtual_id is not None and virtual_id in virtual_to_record_map:
                    record_id = virtual_to_record_map[virtual_id].get("_key")
                    record = record_id_to_record_map.get(record_id)

                    result["metadata"]["recordId"] = record_id
                    if record:
                        result["metadata"]["origin"] = record.get("origin")
                        result["metadata"]["connector"] = record.get("connectorName", None)
                        result["metadata"]["connectorId"] = record.get("connectorId", None)
                        result["metadata"]["kbId"] = record.get("kbId", None)
                        weburl = record.get("webUrl")
                        if weburl and weburl.startswith("https://mail.google.com/mail?authuser="):
                            user_email = user.get("email") if user else None
                            if user_email:
                                weburl = weburl.replace("{user.email}", user_email)
                        result["metadata"]["webUrl"] = weburl
                        result["metadata"]["recordName"] = record.get("recordName")
                        result["metadata"]["previewRenderable"] = record.get("previewRenderable", True)
                        result["metadata"]["hideWeburl"] = record.get("hideWeburl", False)

                        mime_type = record.get("mimeType")
                        if not mime_type:
                            if record.get("recordType", "") == RecordTypes.FILE.value:
                                file_record_ids_to_fetch.append(record_id)
                                result_to_record_map[idx] = (record_id, "file")
                            elif record.get("recordType", "") == RecordTypes.MAIL.value:
                                mail_record_ids_to_fetch.append(record_id)
                                result_to_record_map[idx] = (record_id, "mail")
                            continue
                        else:
                            result["metadata"]["mimeType"] = record.get("mimeType")
                            ext = get_extension_from_mimetype(record.get("mimeType"))
                            if ext:
                                result["metadata"]["extension"] = ext

                        if not weburl:
                            if record.get("recordType", "") == RecordTypes.FILE.value:
                                file_record_ids_to_fetch.append(record_id)
                                result_to_record_map[idx] = (record_id, "file")
                            elif record.get("recordType", "") == RecordTypes.MAIL.value:
                                mail_record_ids_to_fetch.append(record_id)
                                result_to_record_map[idx] = (record_id, "mail")
                            continue

                        if knowledge_search:
                            meta = result.get("metadata")
                            is_block_group = meta.get("isBlockGroup")
                            if is_block_group is not None and virtual_id not in virtual_record_id_to_record:
                                await get_record(virtual_id, virtual_record_id_to_record, self.blob_store, org_id, virtual_to_record_map)
                                record = virtual_record_id_to_record[virtual_id]
                                if record is None:
                                    continue
                                new_type_results.append(result)
                                continue

                final_search_results.append(result)

            files_map = {}
            mails_map = {}

            async def fetch_files() -> dict:
                if not file_record_ids_to_fetch:
                    return {}
                try:
                    file_results = await asyncio.gather(*[
                        self.graph_provider.get_document(record_id, CollectionNames.FILES.value)
                        for record_id in file_record_ids_to_fetch
                    ], return_exceptions=True)
                    return {
                        record_id: result
                        for record_id, result in zip(file_record_ids_to_fetch, file_results)
                        if result and not isinstance(result, Exception)
                    }
                except Exception as e:
                    self.logger.warning(f"Failed to batch fetch files: {str(e)}")
                    return {}

            async def fetch_mails() -> dict:
                if not mail_record_ids_to_fetch:
                    return {}
                try:
                    mail_results = await asyncio.gather(*[
                        self.graph_provider.get_document(record_id, CollectionNames.MAILS.value)
                        for record_id in mail_record_ids_to_fetch
                    ], return_exceptions=True)
                    return {
                        record_id: result
                        for record_id, result in zip(mail_record_ids_to_fetch, mail_results)
                        if result and not isinstance(result, Exception)
                    }
                except Exception as e:
                    self.logger.warning(f"Failed to batch fetch mails: {str(e)}")
                    return {}

            if file_record_ids_to_fetch or mail_record_ids_to_fetch:
                files_map, mails_map = await asyncio.gather(fetch_files(), fetch_mails())

            for idx, (record_id, record_type) in result_to_record_map.items():
                result = search_results[idx]
                record = record_id_to_record_map.get(record_id)
                if not record:
                    continue

                weburl = None
                fallback_mimetype = None
                if record_type == "file" and record_id in files_map:
                    files = files_map[record_id]
                    weburl = files.get("webUrl")
                    fallback_mimetype = files.get("mimeType")
                elif record_type == "mail" and record_id in mails_map:
                    mail = mails_map[record_id]
                    weburl = mail.get("webUrl")
                    if weburl and weburl.startswith("https://mail.google.com/mail?authuser="):
                        user_email = user.get("email") if user else None
                        if user_email:
                            weburl = weburl.replace("{user.email}", user_email)
                    fallback_mimetype = "text/html"

                if weburl:
                    result["metadata"]["webUrl"] = weburl

                if fallback_mimetype:
                    result["metadata"]["mimeType"] = fallback_mimetype
                    fallback_ext = get_extension_from_mimetype(fallback_mimetype)
                    if fallback_ext:
                        result["metadata"]["extension"] = fallback_ext

                final_search_results.append(result)

            # OPTIMIZATION: Get full record documents from Arango using list comprehension
            records = [
                record_id_to_record_map[record_id]
                for record_id in unique_record_ids
                if record_id in record_id_to_record_map
            ]

            if new_type_results:
                is_multimodal_llm = False   #doesn't matter for retrieval service
                flattened_results = await get_flattened_results(new_type_results, self.blob_store, org_id, is_multimodal_llm, virtual_record_id_to_record, from_retrieval_service=True)
                for result in flattened_results:
                    block_type = result.get("block_type")
                    if block_type == GroupType.TABLE.value or block_type in valid_group_labels:
                        _, child_results = result.get("content")
                        for child in child_results:
                            final_search_results.append(child)
                    else:
                        final_search_results.append(result)

            final_search_results = sorted(
                final_search_results,
                key=lambda x: x.get("score") or 0,
                reverse=True,
            )

            # Filter out incomplete results to prevent citation validation failures
            required_fields = ['origin', 'recordName', 'recordId', 'mimeType', "orgId"]
            complete_results = []
            dropped_empty = 0
            dropped_missing_fields = 0

            for result in final_search_results:
                metadata = result.get('metadata', {}) or {}
                # Image points deliberately carry empty ``content`` — the
                # URI/bytes live in blob storage and downstream renderers
                # resolve them via ``block.data.uri`` using
                # ``virtualRecordId`` + ``blockId``. Skip the empty-content
                # gate for those so they survive to the UI.
                is_image_result = (
                    metadata.get("blockType") == BlockType.IMAGE.value
                )
                content = result.get("content")
                if not is_image_result and (content is None or content == ""):
                    dropped_empty += 1
                    continue
                if all(field in metadata and metadata[field] is not None for field in required_fields):
                    complete_results.append(result)
                else:
                    dropped_missing_fields += 1
                    self.logger.warning(f"Filtering out result with incomplete metadata. Virtual ID: {metadata.get('virtualRecordId')}, Missing fields: {[f for f in required_fields if f not in metadata]}")

            search_results = complete_results
            self.logger.info(
                f"[retrieval] post-filter tally: kept={len(complete_results)}, "
                f"dropped_empty_content={dropped_empty}, "
                f"dropped_missing_required_fields={dropped_missing_fields}, "
                f"pre_filter={len(final_search_results)}"
            )
            if search_results or records:
                response_data = {
                    "searchResults": search_results,
                    "records": records,
                    "status": Status.SUCCESS.value,
                    "status_code": 200,
                    "message": "Query processed successfully. Relevant records retrieved.",
                    "virtual_to_record_map": virtual_to_record_map,
                }

                # Add KB filtering info to response if KB filtering was applied
                if kb_ids:
                    response_data["appliedFilters"] = {
                        "kb": kb_ids,
                        "kb_count": len(kb_ids)
                    }

                return response_data
            else:
                return self._create_empty_response("No relevant documents found for your search query. Try using different keywords or broader search terms.", Status.EMPTY_RESPONSE)
        except VectorDBEmptyError:
            self.logger.error("VectorDBEmptyError")
            return self._create_empty_response(
                    "No records indexed yet. Please upload documents or enable connectors to index content",
                    Status.EMPTY_RESPONSE if is_agent else Status.VECTOR_DB_EMPTY,
                )
        except ValueError as e:
            self.logger.error(f"ValueError: {e}")
            return self._create_empty_response(f"Bad request: {str(e)}", Status.ERROR)
        except Exception as e:
            self.logger.error(f"Filtered search failed: {e}\n{traceback.format_exc()}")
            if virtual_record_ids_from_tool:
                return {}
            return self._create_empty_response("Unexpected server error during search.", Status.ERROR)

    async def _get_accessible_virtual_ids_task(
        self, user_id: str, org_id: str, filters: dict[str, list[str]], graph_provider: IGraphDBProvider
    ) -> dict[str, str]:
        """
        Separate task for getting accessible virtualRecordId -> recordId mapping (optimized version).

        Returns a dict mapping each accessible virtualRecordId to the specific recordId that the
        user has permission to access, preventing cross-connector leakage.
        """
        return await graph_provider.get_accessible_virtual_record_ids(
            user_id=user_id, org_id=org_id, filters=filters
        )

    async def _get_user_cached(self, user_id: str) -> Optional[dict[str, Any]]:
        """
        OPTIMIZATION: Get user data with caching to avoid repeated DB calls.
        Cache expires after USER_CACHE_TTL seconds (default 5 minutes).
        """
        global _user_cache

        # Check cache
        if user_id in _user_cache:
            user_data, timestamp = _user_cache[user_id]
            if time.time() - timestamp < USER_CACHE_TTL:
                self.logger.debug(f"User cache hit for user_id: {user_id}")
                return user_data
            else:
                # Cache expired, remove it
                del _user_cache[user_id]

        # Cache miss - fetch from database
        self.logger.debug(f"User cache miss for user_id: {user_id}")
        user_data = await self.graph_provider.get_user_by_user_id(user_id)

        # Store in cache
        _user_cache[user_id] = (user_data, time.time())

        # Simple cache size management - keep only last MAX_USER_CACHE_SIZE users
        if len(_user_cache) > MAX_USER_CACHE_SIZE:
            # Remove oldest entry
            oldest_key = min(_user_cache.keys(), key=lambda k: _user_cache[k][1])
            del _user_cache[oldest_key]

        return user_data

    @staticmethod
    def _combine_with_image_filter(base_filter: models.Filter) -> models.Filter:
        """Return a new filter that ANDs ``base_filter`` with
        ``metadata.blockType == "image"``.

        ``base_filter`` already carries the org + accessible-virtualRecordId
        scoping; we just stack the block-type constraint on top so the
        image-only prefetch respects permissions and tenant isolation.
        Building a fresh :class:`models.Filter` keeps the helper pure —
        we never mutate the caller's filter object, which is reused by
        the text prefetches and the top-level QueryRequest.
        """
        image_condition = models.FieldCondition(
            key="metadata.blockType",
            match=models.MatchValue(value=BlockType.IMAGE.value),
        )

        must = list(getattr(base_filter, "must", None) or [])
        must.append(image_condition)

        return models.Filter(
            must=must,
            should=list(getattr(base_filter, "should", None) or []) or None,
            must_not=list(getattr(base_filter, "must_not", None) or []) or None,
            min_should=getattr(base_filter, "min_should", None),
        )

    # Convert sparse embeddings to Qdrant's SparseVector format; FastEmbedSparse returns
    # LangChain's SparseVector, which Prefetch does not accept.
    @staticmethod
    def to_qdrant_sparse(sparse: models.SparseVector | dict[str, Any] | object) -> models.SparseVector:
        if isinstance(sparse, models.SparseVector):
            return sparse
        if hasattr(sparse, "indices") and hasattr(sparse, "values"):
            return models.SparseVector(indices=list(sparse.indices), values=list(sparse.values))
        if isinstance(sparse, dict) and "indices" in sparse and "values" in sparse:
            return models.SparseVector(indices=sparse["indices"], values=sparse["values"])
        raise ValueError("Cannot convert sparse embedding to Qdrant SparseVector")

    async def _execute_parallel_searches(self, queries, filter, limit) -> list[dict[str, Any]]:
        """Execute all searches in parallel using hybrid (dense + sparse) retrieval with RRF fusion.

        Image points are stored with only a dense vector (no page_content →
        no sparse), so they systematically lose RRF fusion against text
        points that score in both the dense and sparse lanes. On top of
        that, text-to-text similarity is tighter than text-to-image even
        in multimodal embedding spaces, so image hits can fail to even
        reach the dense prefetch's top-N candidate pool. To keep image
        embeddings reachable at retrieval time, we add a third,
        image-only dense prefetch filtered on ``metadata.blockType`` so
        images compete in their own lane inside RRF.
        """
        all_results = []

        dense_embeddings = await self.get_embedding_model_instance()
        if not dense_embeddings:
            raise ValueError("No dense embeddings found")
        if not self.sparse_embeddings:
            raise ValueError("No sparse embeddings found")

        # OPTIMIZATION: Parallelize dense and sparse embedding generation for multiple queries
        dense_tasks = [dense_embeddings.aembed_query(query) for query in queries]
        sparse_tasks = [
            asyncio.to_thread(self.sparse_embeddings.embed_query, query) for query in queries
        ]
        dense_query_embeddings, sparse_query_embeddings = await asyncio.gather(
            asyncio.gather(*dense_tasks),
            asyncio.gather(*sparse_tasks),
        )

        image_only_filter = self._combine_with_image_filter(filter)
        # Reserve a modest budget for image hits so they don't crowd out
        # text results when the collection has a lot of images, while
        # still guaranteeing a handful always survive fusion.
        image_prefetch_limit = max(limit // 2, 5)

        query_requests = [
            models.QueryRequest(
                prefetch=[
                    models.Prefetch(
                        query=dense_embedding,
                        using="dense",
                        limit=limit * 2,  # Fetch more candidates
                    ),
                    models.Prefetch(
                        query=self.to_qdrant_sparse(sparse_embedding),
                        using="sparse",
                        limit=limit * 2,
                    ),
                    # Dedicated dense lane for image-only points. Without
                    # this, image hits get crowded out by text in the
                    # main dense prefetch and drop out of the RRF fusion
                    # entirely — see method docstring.
                    models.Prefetch(
                        query=dense_embedding,
                        using="dense",
                        limit=image_prefetch_limit,
                        filter=image_only_filter,
                    ),
                ],
                query=models.FusionQuery(fusion=models.Fusion.RRF),  # Reciprocal Rank Fusion
                with_payload=True,
                limit=limit,
                filter=filter,
            )
            for dense_embedding, sparse_embedding in zip(dense_query_embeddings, sparse_query_embeddings)
        ]
        search_results = self.vector_db_service.query_nearest_points(
            collection_name=self.collection_name,
            requests=query_requests,
        )
        seen_points = set()
        per_query_hits = []
        for r in search_results:
                points = r.points
                per_query_hits.append(len(points) if points else 0)
                for point in points:
                    if point.id in seen_points:
                        continue
                    seen_points.add(point.id)
                    metadata = point.payload.get("metadata", {})
                    metadata.update({"point_id": point.id})
                    doc = Document(
                        page_content=point.payload.get("page_content", ""),
                        metadata=metadata
                    )
                    score = point.score
                    all_results.append((doc, score))

        # Report raw Qdrant return shape before _format_results so a downstream
        # "0 hits" investigation can distinguish "Qdrant returned nothing" from
        # "we dropped everything in post-processing".
        self.logger.info(
            f"[retrieval] Qdrant query_batch_points returned per-query hits={per_query_hits}, "
            f"total_unique={len(all_results)}"
        )
        return self._format_results(all_results)

    def _create_empty_response(self, message: str, status: Status) -> dict[str, Any]:
        """Helper to create empty response with appropriate HTTP status codes"""
        # Map status types to appropriate HTTP status codes
        status_code_mapping = {
            Status.SUCCESS: 200,
            Status.ERROR: 500,
            Status.ACCESSIBLE_RECORDS_NOT_FOUND: 404,  # Not Found - no accessible records
            Status.VECTOR_DB_EMPTY: 503,  # Service Unavailable - vector DB is empty
            Status.VECTOR_DB_NOT_READY: 503,  # Service Unavailable - vector DB not ready
            Status.EMPTY_RESPONSE: 200,  # OK but no results found
        }

        status_code = status_code_mapping.get(status, 500)  # Default to 500 for unknown status

        return {
            "searchResults": [],
            "records": [],
            "status": status.value,
            "status_code": status_code,
            "message": message,
        }


    def _create_virtual_to_record_mapping(
        self,
        accessible_records: list[dict[str, Any]],
        virtual_record_ids: list[str]
    ) -> dict[str, dict[str, Any]]:
        """
        Create virtual record ID to record mapping from already fetched accessible_records.
        This eliminates the need for an additional database query.
        Args:
            accessible_records: List of accessible record documents (already fetched)
            virtual_record_ids: List of virtual record IDs from search results
        Returns:
            Dict[str, Dict[str, Any]]: Mapping of virtual_record_id -> first accessible record
        """
        # Create a mapping from virtualRecordId to list of records
        virtual_to_records = {}
        for record in accessible_records:
            if record and isinstance(record, dict):
                virtual_id = record.get("virtualRecordId", None)
                record_id = record.get("_key", None)
                if virtual_id and record_id:
                    if virtual_id not in virtual_to_records:
                        virtual_to_records[virtual_id] = []
                    virtual_to_records[virtual_id].append(record)

        # Create the final mapping using only the virtual record IDs from search results
        # Use the first record for each virtual record ID
        mapping = {}
        for virtual_id in virtual_record_ids:
            # Skip None values and ensure virtual_id exists in virtual_to_records
            if virtual_id is not None and virtual_id in virtual_to_records and virtual_to_records[virtual_id]:
                mapping[virtual_id] = virtual_to_records[virtual_id][0]  # Use first record

        return mapping
