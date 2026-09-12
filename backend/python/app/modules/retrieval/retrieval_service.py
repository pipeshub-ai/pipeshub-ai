import asyncio
import math
import os
import time
import traceback
from dataclasses import dataclass
from enum import Enum
from typing import Any

from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_core.language_models.chat_models import BaseChatModel

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
from app.exceptions.fastapi_responses import Status
from app.models.blocks import GroupType
from app.modules.retrieval.result_merging import (
    CollectionResults,
    ResultMerger,
    merge_collection_results,
    merger_for,
)
from app.modules.transformers.blob_storage import BlobStorage
from app.services.featureflag.config.config import CONFIG
from app.services.featureflag.platform_settings import read_platform_feature_flag
from app.services.graph_db.interface.graph_db_provider import (
    AccessibleContainers,
    IGraphDBProvider,
    _unsupported_container_filters,
)
from app.services.vector_db.collection_registry import CollectionRegistry
from app.services.vector_db.const.const import (
    CONNECTOR_IDS_FIELD,
    RECORD_GROUP_IDS_FIELD,
    ROOT_RECORD_GROUP_IDS_FIELD,
)
from app.services.vector_db.interface.vector_db import IVectorDBService
from app.services.vector_db.models import (
    FusionMethod,
    HybridSearchRequest,
)
from app.services.vector_db.sparse_embeddings import SparseEmbedder
from app.services.vector_db.strategy import ContextAxis, QueryContext
from app.sources.client.http.exception.exception import VectorDBEmptyError
from app.utils.aimodels import (
    embedding_config_hash,
    get_default_embedding_model,
    get_embedding_model,
    get_generator_model,
)
from app.utils.embedding_retry import await_with_retry
from app.utils.chat_helpers import (
    GRAPH_BATCH_CHUNK_SIZE,
    get_flattened_results,
    get_record,
)
from app.utils.image_utils import get_extension_from_mimetype

# OPTIMIZATION: User data cache with TTL
_user_cache: dict[str, tuple] = {}  # {user_id: (user_data, timestamp)}
USER_CACHE_TTL = 300  # 5 minutes
MAX_USER_CACHE_SIZE = 1000  # Max number of users to keep in cache

# Applied when a caller passes no limit at all. Matches `search_with_filters`'s
# own default so the None path and the omitted path retrieve the same amount.
DEFAULT_SEARCH_LIMIT = 20

_RETRIEVAL_EMBED_MAX_RETRIES = 3

# Caps concurrent per-collection queries during search fan-out. One under the
# default SingleCollectionStrategy; a multi-collection strategy must not turn
# one user search into an unbounded burst against the vector DB.
def _fanout_concurrency() -> int:
    """Read at import; a malformed value must not take the service down."""
    raw = os.getenv("SEARCH_FANOUT_CONCURRENCY", "5")
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return 5


SEARCH_FANOUT_CONCURRENCY = _fanout_concurrency()


# One retry. A shortfall the first over-fetch did not cover is rare by design;
# a user who can see a sliver of a huge record-level group would never converge,
# and an uncapped loop turns a recall problem into a latency one as well.
MAX_SEARCH_ATTEMPTS = 2

# Fraction of `verify` containers assumed to be denied when sizing the first
# fetch. Assuming all of them (1.0) makes the multiplier diverge as the verify
# share approaches 1; half is a defensible prior and the retry covers the tail.
_ASSUMED_DENY_RATE = 0.5

# Ceiling on the first fetch's multiplier and the growth factor for the retry.
# Note the first fetch never reaches it: with a deny rate of 0.5 the multiplier
# is 1/(1 - p/2), which tops out at 2x as the verify share approaches 1. It
# binds only on the retry.
_OVERFETCH_MAX_MULTIPLIER = 3.0

# The vector limit is already amplified downstream — OpenSearch takes
# `max(limit*2, 20)` per leg, Redis `max(k*2, 20)`, Qdrant `limit*2` per prefetch
# leg — and `_fan_out_searches` asks *each* collection for the full limit. This
# bounds one leg; the product is bounded by the retry cap.
_OVERFETCH_ABSOLUTE_CAP = 300

@dataclass
class _QueryPlan:
    """Per-search cache so a re-query does not pay for embeddings twice.

    Owned by the caller and filled in by ``_execute_parallel_searches``,
    rather than hoisting the embedding step into its own method: ~40 tests
    replace that method with an ``AsyncMock``, and a hoisted helper would
    become real code running in every one of them, building a real embedding
    client. Passing state through keeps the expensive work inside the mocked
    boundary.
    """

    dense: list | None = None
    sparse: list | None = None
    collections: list[str] | None = None
    # Largest single-query batch of the last attempt, before the cross-query
    # dedupe. Exhaustion is a per-query property: the merged total cannot show
    # it, because overlapping queries shrink it for a reason that has nothing
    # to do with the corpus running out.
    max_batch: int | None = None

# User-facing guidance when the graph/permissions yield no searchable corpus
ACCESSIBLE_RECORDS_NOT_FOUND_MESSAGE = (
    "No documents are available for you to search yet. Upload files in Collections "
    "and/or connect a data source under Connectors so content can be indexed."
)


valid_group_labels = [
        GroupType.LIST.value,
        GroupType.ORDERED_LIST.value,
        GroupType.FORM_AREA.value,
        GroupType.INLINE.value,
        GroupType.KEY_VALUE_AREA.value,
        GroupType.TEXT_SECTION.value,
        GroupType.CODE.value,
    ]

class RetrievalService:
    def __init__(
        self,
        logger,
        config_service: ConfigurationService,
        collection_registry: CollectionRegistry,
        vector_db_service: IVectorDBService,
        graph_provider: IGraphDBProvider,
        blob_store: BlobStorage,
    ) -> None:
        """
        Initialize the retrieval service with necessary configurations.

        Args:
            collection_registry: Resolves which collection(s) a search fans out to
            vector_db_service: Vector DB service
            config_service: Configuration service
            graph_provider: Graph database provider
        """

        self.logger = logger
        self.config_service = config_service
        self.llm = None
        self.graph_provider = graph_provider
        self.blob_store = blob_store
        self.vector_db_service = vector_db_service
        self.collection_registry = collection_registry

        # Capability negotiation — determines which embedding legs are needed.
        self._capabilities = vector_db_service.get_capabilities()

        # Sparse embeddings — only for providers that store client-side sparse vectors.
        # SparseEmbedder lazy-initialises in a worker thread on first use.
        self._sparse_embedder: SparseEmbedder | None = None
        self._sparse_embedder_lock = asyncio.Lock()

        # Dense embedding model cache: re-read config on every call to detect
        # provider changes, but reuse the built instance when config is stable.
        self._cached_dense_embeddings: Embeddings | None = None
        self._cached_embedding_config_hash: str | None = None
        self._embedding_model_lock = asyncio.Lock()

        self.logger.info(
            f"Retrieval service initialised: strategy='{collection_registry.strategy_name}', "
            f"provider='{vector_db_service.get_service_name()}', "
            f"supports_sparse={self._capabilities.supports_sparse_vectors}, "
            f"supports_text_search={self._capabilities.supports_server_side_text_search}"
        )

    @property
    def _result_merger(self) -> ResultMerger:
        """How several collections' results may be combined.

        Derived from the provider's declared score semantics rather than stored
        alongside them: two copies of the same fact drift, and this one decides
        whether a multi-collection search is ranked correctly. Memoised because
        the answer cannot change for a live service — capabilities are read
        once at construction.
        """
        merger = getattr(self, "_merger_cache", None)
        if merger is None:
            merger = merger_for(self._capabilities.score_semantics)
            self._merger_cache = merger
        return merger

    async def _ensure_sparse_embedder(self) -> SparseEmbedder | None:
        """Return the SparseEmbedder if this provider uses client-side sparse vectors."""
        if not self._capabilities.supports_sparse_vectors:
            return None
        if self._sparse_embedder is not None:
            return self._sparse_embedder
        async with self._sparse_embedder_lock:
            if self._sparse_embedder is None:
                embedder = SparseEmbedder()
                await embedder._ensure_initialized()
                self._sparse_embedder = embedder
        return self._sparse_embedder

    async def get_llm_instance(self, use_cache: bool = False) -> BaseChatModel | None:
        try:
            self.logger.debug("Getting LLM")
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
                    self.llm = await asyncio.to_thread(get_generator_model, provider, config)
                if self.llm:
                    break

            if not self.llm:
                self.logger.info("No default LLM found, using first available provider")

            if not self.llm:
                for config in llm_configs:
                    provider = config["provider"]
                    self.llm = await asyncio.to_thread(get_generator_model, provider, config)
                    if self.llm:
                        break
                if not self.llm:
                    raise ValueError("No supported LLM provider found in configuration")

            self.logger.info("LLM created successfully")
            return self.llm
        except Exception as e:
            self.logger.error(f"Error getting LLM: {str(e)}")
            return None

    async def get_embedding_model_instance(self, use_cache: bool = False) -> Embeddings | None:
        """Return the dense embedding model, cached across calls while config is stable.

        The config is re-read every call so a provider/model change in the admin
        UI takes effect immediately; but when the config hash hasn't changed the
        previously-built model instance is reused, avoiding the heavy
        construction cost on every query.
        """
        try:
            ai_models = await self.config_service.get_config(
                config_node_constants.AI_MODELS.value, use_cache=use_cache
            )
            embedding_configs = (ai_models or {}).get("embedding")
            config_hash = embedding_config_hash(embedding_configs)

            if (
                self._cached_dense_embeddings is not None
                and self._cached_embedding_config_hash == config_hash
            ):
                return self._cached_dense_embeddings

            async with self._embedding_model_lock:
                if (
                    self._cached_dense_embeddings is not None
                    and self._cached_embedding_config_hash == config_hash
                ):
                    return self._cached_dense_embeddings

                if not embedding_configs:
                    self.logger.info("No embedding config found; using default embedding model")
                    dense_embeddings = await asyncio.to_thread(get_default_embedding_model)
                else:
                    selected_config = next(
                        (c for c in embedding_configs if c.get("isDefault", False)),
                        embedding_configs[0],
                    )
                    provider = selected_config["provider"]
                    self.logger.info(f"Using embedding provider: {provider}")
                    dense_embeddings = await asyncio.to_thread(
                        get_embedding_model, provider, selected_config
                    )

                self._cached_dense_embeddings = dense_embeddings
                self._cached_embedding_config_hash = config_hash
                return dense_embeddings
        except Exception as e:
            self.logger.error(f"Error getting embedding model: {str(e)}")
            return None

    async def get_current_embedding_model_name(self, use_cache: bool = False) -> str | None:
        """Get the current embedding model name from configuration or instance."""
        try:
            # First try to get from AI_MODELS config
            ai_models = await self.config_service.get_config(
                config_node_constants.AI_MODELS.value,
                use_cache=use_cache
            )
            if ai_models and "embedding" in ai_models and ai_models["embedding"]:
                for config in ai_models["embedding"]:
                    # Only one embedding model is supported
                    if "configuration" in config and "model" in config["configuration"]:
                        return config["configuration"]["model"]

            # Return default model if no embedding config found
            return DEFAULT_EMBEDDING_MODEL
        except Exception as e:
            self.logger.error(f"Error getting current embedding model name: {str(e)}")
            return DEFAULT_EMBEDDING_MODEL

    def get_embedding_model_name(self, dense_embeddings: Embeddings) -> str | None:
        if hasattr(dense_embeddings, "model_name"):
            return dense_embeddings.model_name
        elif hasattr(dense_embeddings, "model"):
            return dense_embeddings.model
        else:
            return None

    @staticmethod
    def to_qdrant_sparse(sparse: Any) -> Any:
        """Convert a sparse embedding to Qdrant SparseVector format.

        Kept for backward compatibility with callers that pre-date the generic
        ``to_generic_sparse_vector`` helper.  New code should use
        ``app.services.vector_db.models.to_generic_sparse_vector`` instead.
        """
        try:
            from qdrant_client import models as qdrant_models  # type: ignore
            SparseVector = qdrant_models.SparseVector
        except Exception:
            SparseVector = None  # type: ignore[assignment]

        if SparseVector is not None and isinstance(sparse, SparseVector):
            return sparse
        if hasattr(sparse, "indices") and hasattr(sparse, "values"):
            if SparseVector is not None:
                return SparseVector(indices=list(sparse.indices), values=list(sparse.values))
            return sparse
        if isinstance(sparse, dict) and "indices" in sparse and "values" in sparse:
            if SparseVector is not None:
                return SparseVector(indices=sparse["indices"], values=sparse["values"])
            return sparse
        raise ValueError("Cannot convert sparse embedding to Qdrant SparseVector")

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

    def _format_results(self, results: list[tuple]) -> list[dict[str, Any]]:
        """Format search results into a consistent structure with flattened metadata."""
        formatted_results = []
        for doc, score in results:
            formatted_result = {
                "score": float(score),
                "citationType": "vectordb|document",
                "metadata": doc.metadata,
                "content": doc.page_content
            }
            formatted_results.append(formatted_result)
        return formatted_results

    async def search_with_filters(
        self,
        queries: list[str],
        user_id: str,
        org_id: str,
        filter_groups: dict[str, list[str]] | None = None,
        limit: int | None = 20,
        virtual_record_ids_from_tool: list[str] | None = None,
        knowledge_search: bool = False,
        time_range: dict[str, int] | None = None,
    ) -> dict[str, Any]:
        """Perform semantic search on records the given user may access (graph permission checks)."""

        try:
            # Get accessible records
            if not self.graph_provider:
                raise ValueError("GraphProvider is required for permission checking")

            # `None` reaches here from the prefetch path, which forwards the
            # request's optional `limit` verbatim (chat_modes/bridge.py ->
            # prefetch.py). A default argument cannot cover that -- an explicit
            # None overrides it -- and HybridSearchRequest is a plain dataclass,
            # so the None survived all the way to `req.limit * 2` in
            # qdrant/utils.py and took the whole search down with a TypeError
            # that the except below logged as "Filtered search failed",
            # returning an empty result set. The turn then answered with no
            # retrieved context and no visible error.
            if limit is None:
                limit = DEFAULT_SEARCH_LIMIT

            filter_groups = filter_groups or {}

            # Extract KB IDs for response metadata
            kb_ids = filter_groups.get('kb', None) if filter_groups else None

            # Convert filter_groups to format expected by get_accessible_virtual_record_ids
            filters = {}
            if filter_groups:  # Only process if filter_groups is not empty
                for key, values in filter_groups.items():
                    # Convert key to match collection naming
                    metadata_key = key.lower()  # e.g., 'departments', 'categories', etc.
                    filters[metadata_key] = values

            containers, accessible_virtual_id_to_record_id, user = (
                await self._resolve_search_scope(user_id, org_id, filters, time_range)
            )
            use_containers = containers is not None

            # Under container scoping the accessible map is not built up front —
            # it is the *output* of adjudicating what the search returned. So
            # "reaches nothing" is a property of the containers instead.
            #
            # warning, not error, on both legs: #3254 downgraded this for the
            # record-id path because a user who reaches nothing is not a system
            # fault. The container leg states the same condition, so it matches.
            if use_containers:
                if containers.is_empty:
                    self.logger.warning(f"No accessible containers for user {user_id} and org {org_id}")
                    return self._create_empty_response(ACCESSIBLE_RECORDS_NOT_FOUND_MESSAGE, Status.ACCESSIBLE_RECORDS_NOT_FOUND)
            elif not accessible_virtual_id_to_record_id:
                self.logger.warning(f"No accessible documents found for user {user_id} and org {org_id}")
                return self._create_empty_response(ACCESSIBLE_RECORDS_NOT_FOUND_MESSAGE, Status.ACCESSIBLE_RECORDS_NOT_FOUND)

            # Graph key for KH permission_role checks (Location trails).
            user_key = (user.get("_key") or user.get("id")) if user else None

            if use_containers:
                clauses = self._build_container_clauses(
                    org_id, containers, virtual_record_ids_from_tool
                )
                if clauses is None:
                    return self._create_empty_response(ACCESSIBLE_RECORDS_NOT_FOUND_MESSAGE, Status.ACCESSIBLE_RECORDS_NOT_FOUND)
                must, should = clauses
                filter = await self.vector_db_service.filter_collection(
                    must=must, should=should
                )
            elif virtual_record_ids_from_tool:
                filter  = await self.vector_db_service.filter_collection(
                        must={"orgId": org_id,"virtualRecordId": virtual_record_ids_from_tool},
                    )
            else:
                filter = await self.vector_db_service.filter_collection(
                        must={"orgId": org_id, "virtualRecordId": list(accessible_virtual_id_to_record_id.keys())}
                    )

            if use_containers:
                (
                    search_results,
                    accessible_virtual_id_to_record_id,
                    verification_degraded,
                ) = await self._search_and_adjudicate(
                    queries, filter, limit, org_id, user_id, containers,
                    allow_requery=not virtual_record_ids_from_tool,
                )
                if verification_degraded:
                    # The graph could not answer. Telling this user to upload
                    # documents would be wrong and unactionable.
                    return self._create_empty_response(
                        "Could not verify document permissions right now. "
                        "Please retry shortly.",
                        Status.PERMISSION_CHECK_UNAVAILABLE,
                    )
            else:
                search_results = await self._execute_parallel_searches(
                    queries, filter, limit, org_id, user_id
                )

            if not search_results:
                self.logger.debug("No search results found")
                return self._create_empty_response("No relevant documents found for your search query. Try using different keywords or broader search terms.", Status.EMPTY_RESPONSE)

            self.logger.debug(f"Search results count: {len(search_results) if search_results else 0}")

            self.logger.debug("Extracting virtualRecordIds from search results")
            returned_virtual_record_ids = list({
                result["metadata"]["virtualRecordId"]
                for result in search_results
                if result
                and isinstance(result, dict)
                and result.get("metadata")
                and result["metadata"].get("virtualRecordId") is not None
            })

            self.logger.debug(f"Vector DB returned {len(returned_virtual_record_ids)} unique virtualRecordIds")

            if not returned_virtual_record_ids:
                return self._create_empty_response(ACCESSIBLE_RECORDS_NOT_FOUND_MESSAGE, Status.ACCESSIBLE_RECORDS_NOT_FOUND)

            # Resolve only the permission-verified recordIds for the returned virtual IDs.
            # This prevents cross-connector leakage: if multiple connectors share the same
            # virtualRecordId, we only fetch the specific record the user has access to.
            record_ids_to_fetch = list({
                accessible_virtual_id_to_record_id[vid]
                for vid in returned_virtual_record_ids
                if vid in accessible_virtual_id_to_record_id
            })

            self.logger.debug(f"Fetching {len(record_ids_to_fetch)} records by permission-verified record IDs")
            fetched_records = await self.graph_provider.get_records_by_record_ids(
                record_ids_to_fetch, org_id
            )

            if not fetched_records:
                self.logger.error("Failed to fetch records by record IDs")
                return self._create_empty_response(ACCESSIBLE_RECORDS_NOT_FOUND_MESSAGE, Status.ACCESSIBLE_RECORDS_NOT_FOUND)

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
                return self._create_empty_response(ACCESSIBLE_RECORDS_NOT_FOUND_MESSAGE, Status.ACCESSIBLE_RECORDS_NOT_FOUND)
            self.logger.debug(f"Unique record IDs count: {len(unique_record_ids)}")

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
                else:
                    # A vid absent from the map did not survive permission
                    # resolution, so there is no record to attribute this chunk
                    # to. The completeness filter below would drop it anyway for
                    # want of recordId/origin/mimeType — but that is a metadata
                    # check standing in for a permission boundary, which one
                    # refactor could quietly remove.
                    continue

                final_search_results.append(result)

            files_map = {}
            mails_map = {}

            async def _fetch_by_ids(record_ids: list[str], collection: str, label: str) -> dict:
                """One query per collection instead of one per chunk.

                The id lists are appended per search result, so a record matched
                by several chunks was previously fetched once per chunk.
                """
                if not record_ids:
                    return {}
                unique_ids = list(dict.fromkeys(record_ids))
                try:
                    nodes: list[dict] = []
                    for start in range(0, len(unique_ids), GRAPH_BATCH_CHUNK_SIZE):
                        nodes.extend(
                            await self.graph_provider.get_nodes_by_field_in(
                                collection, "id", unique_ids[start:start + GRAPH_BATCH_CHUNK_SIZE]
                            )
                            or []
                        )
                except Exception as e:
                    self.logger.warning(
                        f"Failed to batch fetch {label}, per-id fallback: {str(e)}"
                    )
                    nodes = []

                resolved = {}
                for node in nodes:
                    key = (node or {}).get("id") or (node or {}).get("_key")
                    if key:
                        resolved[key] = node

                # Losing the batch strips webUrl/mimeType from every record in
                # it, and a result without mimeType is dropped outright by the
                # required_fields filter below -- the citation disappears from
                # the answer with no error. The except above cannot catch that:
                # get_nodes_by_field_in swallows its own errors and returns [],
                # so a failure looks exactly like "no rows". Recover on which
                # ids actually came back instead.
                missing = [rid for rid in unique_ids if rid not in resolved]
                if missing:
                    per_id = await asyncio.gather(
                        *[
                            self.graph_provider.get_document(rid, collection)
                            for rid in missing
                        ],
                        return_exceptions=True,
                    )
                    resolved.update({
                        rid: doc
                        for rid, doc in zip(missing, per_id)
                        if doc and not isinstance(doc, BaseException)
                    })
                return resolved

            async def fetch_files() -> dict:
                return await _fetch_by_ids(
                    file_record_ids_to_fetch, CollectionNames.FILES.value, "files"
                )

            async def fetch_mails() -> dict:
                return await _fetch_by_ids(
                    mail_record_ids_to_fetch, CollectionNames.MAILS.value, "mails"
                )

            async def fetch_locations() -> dict[str, str]:
                """Resolve permission-aware Location trails for retrieved records.

                One adjacency query + KH permission_role batch on ancestors.
                Soft-fail is App-only (never unfiltered RG/parent ids).
                """
                try:
                    from app.agents.actions.knowledge_graph.location import (
                        resolve_ancestor_locations,
                    )

                    rids = list(unique_record_ids)
                    if not rids:
                        return {}

                    result = await resolve_ancestor_locations(
                        rids,
                        graph_provider=self.graph_provider,
                        org_id=org_id,
                        user_key=user_key or "",
                        record_docs=record_id_to_record_map,
                    )
                    self.logger.debug(
                        "fetch_locations: %d/%d trails resolved",
                        len(result),
                        len(rids),
                    )
                    return result
                except Exception as loc_exc:
                    self.logger.warning("fetch_locations: skipped — %s", loc_exc, exc_info=True)
                    return {}

            locations_map: dict[str, str] = {}
            if file_record_ids_to_fetch or mail_record_ids_to_fetch or unique_record_ids:
                files_map, mails_map, locations_map = await asyncio.gather(
                    fetch_files(), fetch_mails(), fetch_locations()
                )
            else:
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

            # Inject location into virtual_to_record_map entries so get_record()
            # (chat_helpers.py) can forward it to Record.to_llm_context().
            if locations_map:
                for vr_entry in virtual_to_record_map.values():
                    if vr_entry is None:
                        continue
                    rid = vr_entry.get("_key")
                    if rid and rid in locations_map:
                        vr_entry["location"] = locations_map[rid]

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

            for result in final_search_results:
                if result.get("content") is None or result.get("content") == "":
                    continue
                metadata = result.get('metadata', {})
                if all(field in metadata and metadata[field] is not None for field in required_fields):
                    complete_results.append(result)
                else:
                    self.logger.warning(f"Filtering out result with incomplete metadata. Virtual ID: {metadata.get('virtualRecordId')}, Missing fields: {[f for f in required_fields if f not in metadata]}")

            search_results = complete_results
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
                    Status.VECTOR_DB_EMPTY,
                )
        except ValueError as e:
            self.logger.error(f"ValueError: {e}")
            return self._create_empty_response(f"Bad request: {str(e)}", Status.ERROR)
        except Exception as e:
            self.logger.error(f"Filtered search failed: {e}\n{traceback.format_exc()}")
            if virtual_record_ids_from_tool:
                return {}
            return self._create_empty_response("Unexpected server error during search.", Status.ERROR)

    async def _container_filter_enabled(self) -> bool:
        """Whether searches scope by container instead of by record id.

        Read per request, uncached, so an admin toggling it in Labs takes
        effect on the next search rather than after a restart. Defaults on, and
        an unreadable setting keeps it on: the container path adjudicates every
        vid it returns, so falling back to it is the stricter of the two.
        """
        return await read_platform_feature_flag(
            CONFIG.ENABLE_CONTAINER_PERMISSION_FILTER,
            self.config_service,
            default=True,
        )

    async def _resolve_search_scope(
        self,
        user_id: str,
        org_id: str,
        filters: dict[str, list[str]],
        time_range: dict[str, int] | None,
    ) -> tuple["AccessibleContainers | None", dict[str, str], dict[str, Any] | None]:
        """Decide how this search's permission filter is built.

        Returns ``(containers, accessible_map, user)``. ``containers`` is None
        whenever the legacy record-id path is in force — the flag is off, the
        request carries a record-level predicate no container can express, or
        the graph declined (an unbacklogged connector, a filter too large).
        The two are never both authoritative.
        """
        # Built on demand, never eagerly: an un-awaited coroutine is a
        # RuntimeWarning on every search, and the ON path does not want one.
        def _legacy():
            return self._get_accessible_virtual_ids_task(
                user_id, org_id, filters, self.graph_provider, time_range=time_range
            )

        user_task = self._get_user_cached(user_id)

        # Awaited before the branch rather than gathered with `user_task`: it is
        # a single ~0.2ms KV read, and every branch below overlaps `user_task`
        # with its own expensive call. Gathering the flag here instead would
        # leave that call serialised behind the user lookup.
        if not await self._container_filter_enabled():
            accessible, user = await asyncio.gather(_legacy(), user_task)
            return None, accessible, user

        # Recognised locally, without asking the graph: no container expresses a
        # date range or a per-record metadata term, so those keep the record-id
        # path. This is a capability gap, not a preference.
        if _unsupported_container_filters(filters, time_range):
            accessible, user = await asyncio.gather(_legacy(), user_task)
            return None, accessible, user

        containers, user = await asyncio.gather(
            self.graph_provider.get_accessible_containers(
                user_id, org_id, filters, time_range
            ),
            user_task,
        )
        if containers.fallback_reason:
            # A data-readiness condition, not a rollout switch: an app whose
            # vector membership arrays were never built cannot be filtered by
            # container without silently dropping its records.
            self.logger.info(
                "container filter declined (%s); using record ids for user=%s org=%s",
                containers.fallback_reason,
                user_id,
                org_id,
            )
            return None, await _legacy(), user
        return containers, {}, user

    def _build_container_clauses(
        self,
        org_id: str,
        containers: "AccessibleContainers",
        virtual_record_ids_from_tool: list[str] | None,
    ) -> tuple[dict[str, Any], dict[str, Any]] | None:
        """``(must, should)`` for a container-scoped filter, or None if nothing is reachable.

        Two shapes here disclose the whole corpus rather than erroring, so both
        are guarded deliberately:

        ``must`` must never be empty. With an empty ``must`` and a populated
        ``should``, OpenSearch does not set ``minimum_should_match``, the should
        clauses become score-only, and every document in the index matches.
        ``orgId`` is what prevents that.

        ``should`` must never be built from empty lists. ``build_conditions``
        skips them, ``min_should_match`` is then dropped, and the filter
        degenerates to ``orgId`` alone — the same disclosure by another route.
        Hence None, and a 404, rather than an empty ``should``.

        ``min_should_match`` is deliberately not passed: Redis raises
        ``NotImplementedError`` on it even with no should clauses, and all three
        providers already mean "at least one" when ``must`` is non-empty.
        """
        should: dict[str, Any] = {}
        if containers.app_ids:
            should[CONNECTOR_IDS_FIELD] = sorted(containers.app_ids)
        group_ids = containers.record_group_ids
        if group_ids:
            should[RECORD_GROUP_IDS_FIELD] = sorted(group_ids)
        if containers.root_group_ids:
            should[ROOT_RECORD_GROUP_IDS_FIELD] = sorted(containers.root_group_ids)
        if containers.direct_records:
            should["virtualRecordId"] = sorted(containers.direct_records)

        if not should:
            return None

        must: dict[str, Any] = {"orgId": org_id}
        if virtual_record_ids_from_tool:
            # Narrowing, not authorising — the containers in `should` still
            # decide what this user may see.
            must["virtualRecordId"] = list(virtual_record_ids_from_tool)
        return must, should

    @staticmethod
    def _overfetch_limit(limit: int, containers: "AccessibleContainers") -> int:
        """How much to fetch so verification drops still leave ``limit`` results.

        Sized from the trusted/verify split, which is known before querying, so
        the retry stays the exception rather than a routine second round trip.
        When nothing needs verifying — an all-Collections or all-app-level
        tenant — this returns exactly ``limit`` and the change costs nothing.
        """
        checked = len(containers.record_group_ids_verify)
        if checked == 0:
            return limit
        trusted = len(containers.app_ids) + len(containers.record_group_ids_trusted)
        p_verify = checked / (trusted + checked)
        survival = max(
            1.0 - p_verify * _ASSUMED_DENY_RATE, 1.0 / _OVERFETCH_MAX_MULTIPLIER
        )
        multiplier = min(max(1.0 / survival, 1.0), _OVERFETCH_MAX_MULTIPLIER)
        # The cap bounds the *over*-fetch, never the caller's own request:
        # returning less than `limit` would under-fetch a large search while
        # claiming to have widened it.
        return max(
            limit,
            min(math.ceil(limit * multiplier), _OVERFETCH_ABSOLUTE_CAP),
        )

    @staticmethod
    def _should_requery(
        *,
        attempt: int,
        surviving: int,
        limit: int,
        raw: int,
        fetch_limit: int,
        max_batch: int,
        denied: int,
        granted: int,
        allow_requery: bool,
    ) -> bool:
        """Whether a shortfall is worth a second, larger search.

        Every clause here exists to stop a retry that cannot help.
        """
        if not allow_requery or attempt + 1 >= MAX_SEARCH_ATTEMPTS:
            return False
        if surviving >= limit:
            return False
        if max_batch < fetch_limit:
            # Every query came back short of what it was asked for, so the
            # corpus is exhausted and a bigger limit produces nothing. Judged
            # per query on purpose: the merged total is reduced by overlap
            # between expanded queries, which says nothing about the corpus,
            # and comparing against it suppresses the retry almost always.
            return False
        if fetch_limit >= _OVERFETCH_ABSOLUTE_CAP:
            # Per query, so it stays comparable to the cap that produced it —
            # the fan-out total would trip this early on a multi-query search.
            return False
        if denied <= 0:
            # The shortfall is not permission-related: a stale membership array,
            # say, or chunks whose records have since been deleted. Fetching
            # more only amplifies it.
            return False
        if granted == 0:
            # Nothing granted at all is indistinguishable from the verifier
            # failing, which also returns an empty map. Retrying would hammer a
            # graph that may already be down.
            return False
        return True

    async def _search_and_adjudicate(
        self,
        queries: list[str],
        filter: Any,
        limit: int,
        org_id: str,
        user_id: str,
        containers: "AccessibleContainers",
        *,
        allow_requery: bool,
    ) -> tuple[list[dict[str, Any]], dict[str, str], bool]:
        """Search under a container filter, then resolve what the user may read.

        Returns ``(results, accessible_map, verification_degraded)``. The third
        element is what stops a graph outage being reported as an empty corpus.

        The accessible map is the *output* here rather than an input. Container
        scoping admits more than the user may see, and this is what narrows it
        back — after which the intersection downstream behaves exactly as it did
        against a precomputed map, so nothing past this point changes.
        """
        plan = _QueryPlan()
        fetch_limit = self._overfetch_limit(limit, containers)
        # `limit` is per query — `_run_searches` issues one request per expanded
        # query and concatenates the deduped batches — so every comparison below
        # is against the whole fan-out, not one query's share.
        budget = limit * max(1, len(queries))
        # The incumbent. A retry exists only to *improve* recall, so an attempt
        # that comes back worse — because the vector call flaked, or the graph
        # went down between rounds — must not replace a servable answer.
        best_results: list[dict[str, Any]] = []
        best_accessible: dict[str, str] = {}
        best_surviving = -1
        best_degraded = False

        for attempt in range(MAX_SEARCH_ATTEMPTS):
            # Per attempt, not per loop: a later attempt that fails verification
            # must not condemn an earlier one that succeeded.
            attempt_degraded = False
            search_results = await self._execute_parallel_searches(
                queries, filter, fetch_limit, org_id, user_id, plan=plan
            )
            returned_vids = {
                result["metadata"]["virtualRecordId"]
                for result in search_results
                if result
                and isinstance(result, dict)
                and result.get("metadata")
                and result["metadata"].get("virtualRecordId") is not None
            }
            if not returned_vids:
                if best_surviving < 0:
                    best_results, best_accessible, best_surviving = search_results, {}, 0
                break

            accessible = await self.graph_provider.filter_accessible_virtual_record_ids(
                list(returned_vids), user_id, org_id
            )
            surviving = sum(
                1
                for result in search_results
                if result
                and isinstance(result, dict)
                and (result.get("metadata") or {}).get("virtualRecordId") in accessible
            )
            denied = len(returned_vids) - len(accessible)

            if not accessible:
                # Both providers fail closed to {}, so this is indistinguishable
                # from total denial here. The caller turns it into a 503 rather
                # than telling a user with a full workspace to go add content.
                attempt_degraded = True
                self.logger.error(
                    "container_search: verification returned nothing for %d vrids "
                    "(user=%s org=%s) — denial and graph failure look alike here",
                    len(returned_vids), user_id, org_id,
                )

            self.logger.info(
                "container_search attempt=%d limit=%d fetch_limit=%d raw=%d "
                "vids=%d granted=%d denied=%d surviving=%d "
                "apps=%d trusted_groups=%d verify_groups=%d direct=%d",
                attempt + 1, limit, fetch_limit, len(search_results),
                len(returned_vids), len(accessible), denied, surviving,
                len(containers.app_ids),
                len(containers.record_group_ids_trusted),
                len(containers.record_group_ids_verify),
                len(containers.direct_records),
            )

            if surviving > best_surviving:
                best_results, best_accessible, best_surviving = (
                    search_results, accessible, surviving
                )
                best_degraded = attempt_degraded

            if not self._should_requery(
                attempt=attempt,
                surviving=surviving,
                limit=budget,
                raw=len(search_results),
                fetch_limit=fetch_limit,
                max_batch=plan.max_batch if plan.max_batch is not None else len(search_results),
                denied=denied,
                granted=len(accessible),
                allow_requery=allow_requery,
            ):
                break

            fetch_limit = min(
                _OVERFETCH_ABSOLUTE_CAP,
                math.ceil(fetch_limit * _OVERFETCH_MAX_MULTIPLIER),
            )

        # Over-fetching is a means, not a promise: drop the headroom this path
        # added, best-scoring first. `budget` is the record-id path's *ceiling*,
        # not its typical output — expanded queries overlap, so its deduped
        # union usually lands under it. Matching the ceiling can therefore
        # return somewhat more than the flag-off path; trimming to `limit`
        # returned a fraction of it, which is the failure this replaces.
        admitted = [
            result
            for result in best_results
            if result
            and isinstance(result, dict)
            and (result.get("metadata") or {}).get("virtualRecordId") in best_accessible
        ]
        admitted.sort(key=lambda r: r.get("score") or 0, reverse=True)
        return admitted[:budget], best_accessible, best_degraded

    async def _get_accessible_virtual_ids_task(
        self,
        user_id: str,
        org_id: str,
        filters: dict[str, list[str]],
        graph_provider: IGraphDBProvider,
        time_range: dict[str, int] | None = None,
    ) -> dict[str, str]:
        """
        Separate task for getting accessible virtualRecordId -> recordId mapping (optimized version).

        Returns a dict mapping each accessible virtualRecordId to the specific recordId that the
        user has permission to access, preventing cross-connector leakage.
        """
        return await graph_provider.get_accessible_virtual_record_ids(
            user_id=user_id, org_id=org_id, filters=filters, time_range=time_range
        )

    async def _get_user_cached(self, user_id: str) -> dict[str, Any] | None:
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

    async def _accessible_connector_names(
        self, user_id: str | None, org_id: str
    ) -> list[str] | None:
        """Connector types to narrow the fan-out with, or None to not narrow.

        Only gathered when the active strategy says it would help — under
        ``single`` there is one collection and the query would be pure
        overhead. Failures return None rather than an empty list: an empty list
        means "this user reaches no connector type", which would resolve to no
        collections and silently return nothing.
        """
        if ContextAxis.CONNECTOR_NAME not in self.collection_registry.strategy.read_narrowing_axes:
            return None
        if not user_id or not self.graph_provider:
            return None
        try:
            names = await self.graph_provider.get_accessible_connector_types(
                user_id, org_id
            )
        except Exception as e:
            self.logger.warning(
                "Could not resolve accessible connector types; searching every "
                "managed collection instead: %s",
                e,
            )
            return None
        return names or None

    async def _resolve_search_collections(
        self, org_id: str, user_id: str | None = None
    ) -> list[str]:
        """Which collection(s) this org's search should fan out to.

        An empty list is the honest answer on a deployment where nothing has
        been indexed yet: ``resolve_for_query`` already filters to collections
        that exist, so the only names it drops are ones a search would find
        nothing in. Falling back to a fabricated name would, under a strategy
        that resolves per org or per connector, name a collection belonging to
        nobody.
        """
        connector_names = await self._accessible_connector_names(user_id, org_id)
        return await self.collection_registry.resolve_for_query(
            QueryContext(org_id=org_id, accessible_connector_names=connector_names)
        )

    async def _execute_parallel_searches(
        self, queries, filter, limit, org_id: str, user_id: str | None = None,
        *, plan: "_QueryPlan | None" = None,
    ) -> list[dict[str, Any]]:
        """Execute all searches in parallel using hybrid (dense + sparse) retrieval with RRF fusion.

        The search strategy adapts to provider capabilities:
        - Providers with sparse vector support (Qdrant): full dense+sparse hybrid with client-side BM25.
        - Providers without sparse support (OpenSearch, Redis): dense-only search,
          server-side BM25/text handled by the provider internally.

        Fans out to every collection the active strategy resolves for ``org_id``
        (one today; a per-connector/per-org strategy can resolve more) and
        merges the raw results before the caller's global rerank/sort.
        """
        # Embedding is the only genuinely expensive, uncached step here, and a
        # re-query differs from the first attempt in nothing but `limit`.
        if plan is not None and plan.dense is not None:
            dense_query_embeddings = plan.dense
            sparse_query_embeddings = plan.sparse
            return await self._run_searches(
                queries, filter, limit, org_id, user_id,
                dense_query_embeddings, sparse_query_embeddings, plan=plan,
            )

        dense_embeddings = await self.get_embedding_model_instance()
        if not dense_embeddings:
            raise ValueError("No dense embeddings found")

        sparse_embedder = await self._ensure_sparse_embedder()

        dense_tasks = [
            await_with_retry(
                lambda q=query: dense_embeddings.aembed_query(q),
                max_retries=_RETRIEVAL_EMBED_MAX_RETRIES,
                operation="aembed_query",
                service_name="retrieval",
            )
            for query in queries
        ]
        supports_sparse = self._capabilities.supports_sparse_vectors

        if sparse_embedder is not None and supports_sparse:
            # Parallelise dense and sparse embedding generation
            sparse_tasks = [sparse_embedder.embed_query(query) for query in queries]
            (dense_query_embeddings, sparse_query_embeddings) = await asyncio.gather(
                asyncio.gather(*dense_tasks),
                asyncio.gather(*sparse_tasks),
            )
        else:
            dense_query_embeddings = await asyncio.gather(*dense_tasks)
            sparse_query_embeddings = [None] * len(queries)

        if plan is not None:
            plan.dense = dense_query_embeddings
            plan.sparse = sparse_query_embeddings

        return await self._run_searches(
            queries, filter, limit, org_id, user_id,
            dense_query_embeddings, sparse_query_embeddings, plan=plan,
        )

    async def _run_searches(
        self,
        queries: list[str],
        filter: Any,
        limit: int,
        org_id: str,
        user_id: str | None,
        dense_query_embeddings: list,
        sparse_query_embeddings: list,
        *,
        plan: "_QueryPlan | None" = None,
    ) -> list[dict[str, Any]]:
        """Everything downstream of embedding: build requests, fan out, dedupe.

        Split out so a re-query at a larger limit reuses the embeddings — the
        only field that differs between attempts is ``HybridSearchRequest.limit``.
        """
        all_results: list[tuple] = []
        supports_sparse = self._capabilities.supports_sparse_vectors
        supports_text = self._capabilities.supports_server_side_text_search

        requests = [
            HybridSearchRequest(
                dense_query=dense_embedding,
                # Only send sparse vectors to providers that store them (Qdrant)
                sparse_query=sparse_embedding if supports_sparse else None,
                # Send text query to providers that do server-side BM25 (OpenSearch, Redis)
                text_query=query if supports_text else None,
                filter=filter,
                limit=limit,
                fusion_method=FusionMethod.RRF,
            )
            for query, dense_embedding, sparse_embedding in zip(
                queries, dense_query_embeddings, sparse_query_embeddings
            )
        ]

        if plan is not None and plan.collections is not None:
            collections = plan.collections
        else:
            collections = await self._resolve_search_collections(org_id, user_id)
            if plan is not None:
                plan.collections = collections
        search_results = await self._fan_out_searches(collections, requests, limit)
        if plan is not None:
            plan.max_batch = max((len(batch) for batch in search_results), default=0)

        seen_points: set = set()
        for batch in search_results:
            for point in batch:
                if point.id in seen_points:
                    continue
                seen_points.add(point.id)
                metadata = point.payload.get("metadata") or {}
                metadata["point_id"] = point.id
                doc = Document(
                    page_content=point.payload.get("page_content", ""),
                    metadata=metadata,
                )
                all_results.append((doc, point.score))

        return self._format_results(all_results)

    async def _fan_out_searches(
        self, collections: list[str], requests: list[HybridSearchRequest], limit: int
    ) -> list[list]:
        """Query every collection in parallel and reduce each query to one top-K.

        Each collection is asked for the full ``limit`` rather than a share of
        it: nothing knows in advance which collection holds the best matches,
        so splitting the budget would cost recall. That leaves N ranked lists
        per query, which ``result_merging`` reduces according to what the
        provider's scores actually mean.

        A collection that fails degrades the result set instead of failing the
        search — a search over three collections should not error because one
        is briefly unreachable.
        """
        if not collections:
            return [[] for _ in requests]

        semaphore = asyncio.Semaphore(SEARCH_FANOUT_CONCURRENCY)

        async def _search(collection_name: str) -> list[list]:
            async with semaphore:
                return await self.vector_db_service.query_nearest_points(
                    collection_name=collection_name,
                    requests=requests,
                )

        outcomes = await asyncio.gather(
            *[_search(name) for name in collections],
            return_exceptions=True,
        )

        # Per query index, one CollectionResults per collection that answered.
        per_query: list[list[CollectionResults]] = [[] for _ in requests]
        failures: list[BaseException] = []
        for collection_name, outcome in zip(collections, outcomes):
            if isinstance(outcome, BaseException):
                failures.append(outcome)
                self.logger.warning(
                    "Search against collection '%s' failed; continuing with the "
                    "remaining %d collection(s): %s",
                    collection_name,
                    len(collections) - 1,
                    outcome,
                )
                continue
            for i, batch in enumerate(outcome):
                if i < len(per_query):
                    per_query[i].append(
                        CollectionResults(collection_name=collection_name, results=batch)
                    )

        # Degrading when one collection is unreachable is the point of
        # return_exceptions; degrading when *every* one failed is not — that is
        # an outage, and reporting it as "no documents found" sends the user
        # off to reword a query that was never run.
        if failures and len(failures) == len(collections):
            raise failures[0]

        return [
            merge_collection_results(batches, limit, self._result_merger)
            for batches in per_query
        ]

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
            Status.PERMISSION_CHECK_UNAVAILABLE: 503,  # graph could not adjudicate
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
