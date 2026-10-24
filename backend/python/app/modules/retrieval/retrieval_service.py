import asyncio
import logging
import math
import os
import time
import traceback
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_core.language_models.chat_models import BaseChatModel

from app.config.configuration_service import ConfigurationService
from app.config.constants.ai_models import (
    DEFAULT_EMBEDDING_MODEL,
    QUERY_EMBEDDING_ATTEMPT_TIMEOUT_SECONDS,
)

# from langchain_cohere import CohereEmbeddings
from app.config.constants.arangodb import (
    CollectionNames,
    RecordTypes,
)
from app.config.constants.service import config_node_constants
from app.exceptions.fastapi_responses import Status
from app.exceptions.graph_db_exceptions import PermissionVerificationUnavailableError
from app.models.blocks import GroupType
from app.models.entities import substitute_user_email
from app.modules.demo_data.access import excluded_demo_connector_ids
from app.modules.retrieval.entity_filters import (
    TOO_BROAD_MESSAGE,
    EntityFilterResolver,
    EntityFilterUnavailableError,
)
from app.modules.retrieval.lexical_search import (
    LexicalHit,
    LexicalSearchResult,
    LexicalSearchUnavailableError,
)
from app.modules.retrieval.record_cut import (
    RecordCut,
    active_record_cut,
    cap_passages_per_record,
    hit_record_id,
    passages_per_record,
    point_record_id,
)
from app.modules.retrieval.source_diversity import (
    SourceDiversity,
    active_source_diversity,
    record_source,
)
from app.modules.retrieval.selection_scope import (
    SELECTION_EMPTY_MESSAGE,
    SELECTION_NOT_READY_MESSAGE,
    SELECTION_TOO_LARGE_MESSAGE,
    SelectionNotReadyError,
    SelectionScope,
    SelectionTooLargeError,
    connection_bound_scope,
    connection_sources_of,
    require_membership_ready,
    resolve_request_scopes,
)
from app.modules.retrieval.result_merging import (
    CollectionResults,
    ResultMerger,
    merge_collection_results,
    merger_for,
)
from app.modules.transformers.blob_storage import BlobStorage
from app.services.graph_db.interface.graph_db_provider import (
    ALLOWED_FILTER_KEYS,
    CONTAINER_SCOPE_FILTER_KEYS,
    SELECTION_APPS_FILTER_KEY,
    SELECTION_FILTER_KEYS,
    AccessibleContainers,
    IGraphDBProvider,
    _unsupported_container_filters,
    requested_scope_ids,
)
from app.services.model_identity.call_context import narrow_workload
from app.services.model_identity.workload import Workload
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
    SearchFailureKind,
)
from app.services.vector_db.search_retry import (
    VectorSearchUnavailableError,
    query_nearest_points_with_retry,
)
from app.services.vector_db.sparse_embeddings import SparseEmbedder
from app.services.vector_db.strategy import ContextAxis, QueryContext
from app.sources.client.http.exception.exception import VectorDBEmptyError
from app.utils.aimodels import (
    embedding_config_hash,
    get_default_embedding_model,
    get_embedding_model,
    get_generator_model,
    is_local_cpu_embedding_provider,
)
from app.utils.chat_helpers import (
    GRAPH_BATCH_CHUNK_SIZE,
    describe_search_records,
    get_flattened_results,
    get_record,
    group_child_results,
    valid_group_labels,
)
from app.utils.embedding_retry import await_with_retry
from app.utils.image_utils import get_extension_from_mimetype
from app.utils.query_instruction import instruct_query
from app.utils.text_fragments.cache import TtlCache

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
# bounds one leg; the product is bounded by the retry cap. The record cut's
# deeper fusion (`record_cut.py`) stays under it as well.
_OVERFETCH_ABSOLUTE_CAP = 300

# How long a search scope that the container filter could not fill keeps going
# straight to the record-id filter. Matches the accessible-records cache TTL, so
# the enumeration it needs is usually still warm.
NARROW_ACCESS_MEMO_TTL_SECONDS = 300
_NARROW_ACCESS_MEMO_MAX_ENTRIES = 10_000
# Share of container-filtered hits the access check must admit for the
# container search to stand. Below it, the search moves to the record-id filter.
_NARROW_ACCESS_MIN_SURVIVAL = 0.8


def narrow_access_fallback_enabled() -> bool:
    """``SEARCH_NARROW_ACCESS_FALLBACK``: on unless set to a false value.

    Read on every search, so a change needs no restart. Off restores the plain
    container search with its single wider retry.
    """
    raw = os.getenv("SEARCH_NARROW_ACCESS_FALLBACK", "true").strip().lower()
    return raw not in ("0", "false", "no", "off")


class _NarrowAccessMemo:
    """Search scopes whose container filter recently came back mostly unreadable.

    A user who can read a small share of the connectors they can enter gets
    candidates under the container filter that the access check then denies,
    so a page cannot be filled within the over-fetch cap. For such a scope the
    record-id filter is both cheaper and complete. This remembers that, per
    process, for a few minutes.

    It only ever routes a search to the record-id path, which ends in the same
    access check, so a stale or wrong entry costs latency and never access.
    """

    def __init__(self) -> None:
        self._entries: dict[tuple, float] = {}

    @staticmethod
    def key(org_id: str, user_id: str, filters: dict[str, Any] | None) -> tuple:
        scope = requested_scope_ids(filters)
        return (org_id, user_id, frozenset(scope) if scope is not None else None)

    def mark(self, key: tuple, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        if len(self._entries) >= _NARROW_ACCESS_MEMO_MAX_ENTRIES and key not in self._entries:
            # Drop the entry closest to expiry; a forgotten entry only costs one
            # container attempt on that scope's next search.
            del self._entries[min(self._entries, key=self._entries.__getitem__)]
        self._entries[key] = now + NARROW_ACCESS_MEMO_TTL_SECONDS

    def is_narrow(self, key: tuple, now: float | None = None) -> bool:
        expires = self._entries.get(key)
        if expires is None:
            return False
        if (time.monotonic() if now is None else now) >= expires:
            self._entries.pop(key, None)
            return False
        return True

    def forget(self, key: tuple) -> None:
        self._entries.pop(key, None)

    def clear(self) -> None:
        self._entries.clear()


narrow_access_memo = _NarrowAccessMemo()

@dataclass
class _RecordIdSearch:
    """What the record-id search after a container shortfall found.

    ``empty_scope`` when the user's record list was empty, so no vector query
    ran: an empty id list would be dropped by every backend and leave an
    org-wide search.
    """

    results: list[dict[str, Any]] = field(default_factory=list)
    accessible: dict[str, str] = field(default_factory=dict)
    empty_scope: bool = False


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
    # Collections the kept attempt could not search, of how many it asked.
    failed_collections: list[str] = field(default_factory=list)
    collections_searched: int = 0
    # Set by `_search_and_adjudicate` when the container filter could not fill
    # the page because the access check denied too much of it: the caller
    # searches again with the record-id filter.
    narrow_shortfall: bool = False

    def incomplete_notice(self) -> str | None:
        if not self.failed_collections:
            return None
        return SEARCH_INCOMPLETE_MESSAGE.format(
            failed=len(self.failed_collections), total=self.collections_searched,
        )

# User-facing guidance when the graph/permissions yield no searchable corpus
ACCESSIBLE_RECORDS_NOT_FOUND_MESSAGE = (
    "No documents are available for you to search yet. Upload files in Collections "
    "and/or connect a data source under Connectors so content can be indexed."
)

# When the graph could not say what this user may read. Nothing is shown, and
# the reader is told why, so "no results" is not mistaken for "no documents".
# The Node gateway passes a 503's message through only when it is on its list
# (libs/errors/reader-friendly.ts), so a change here must be made there too.
PERMISSION_CHECK_UNAVAILABLE_MESSAGE = (
    "We couldn't check which documents you have access to just now, so no results "
    "are shown. Please try again in a minute."
)

# The vector store could not answer, after a retry. Written for the reader, and
# read by the agent too: it must not take this for "nothing matched". Like the
# message above, the Node gateway allowlists these.
SEARCH_INDEX_TIMEOUT_MESSAGE = (
    "The search index took too long to respond, so no results are shown. "
    "Please try again in a minute."
)
SEARCH_INDEX_UNREACHABLE_MESSAGE = (
    "We couldn't reach the search index just now, so no results are shown. "
    "Please try again in a minute."
)
_SEARCH_INDEX_FAILURE_MESSAGES = {
    SearchFailureKind.TIMEOUT: SEARCH_INDEX_TIMEOUT_MESSAGE,
    SearchFailureKind.UNAVAILABLE: SEARCH_INDEX_UNREACHABLE_MESSAGE,
}
# Some collections answered and some failed (timed out, or any other error):
# the results stand, with this said.
SEARCH_INCOMPLETE_MESSAGE = (
    "{failed} of {total} parts of the search index could not be searched, so this "
    "search may have missed documents."
)

# Every empty outcome of an entity-filtered search reads the same, whether nothing
# matched or the matches are in records this user cannot read.
ENTITY_FILTER_NO_MATCH_MESSAGE = "No documents match this search and its entity filter."
ENTITY_FILTER_UNAVAILABLE_MESSAGE = (
    "We couldn't apply the entity filter just now. Please try again in a minute."
)


def _record_cut_for(scope_virtual_ids: list[str] | None) -> RecordCut | None:
    """The record cut for a search, or None when it is off or has nothing to spread.

    A search confined to one record (block selection inside a fetched record,
    say) keeps the plain depth: there are no other records to reach, so the
    deeper vector reads would buy nothing.
    """
    record_cut = active_record_cut()
    if record_cut is None or scope_virtual_ids is None:
        return record_cut
    first = scope_virtual_ids[0] if scope_virtual_ids else None
    if all(vid == first for vid in scope_virtual_ids):
        return None
    return record_cut


def _vary_sources(
    diversity: SourceDiversity,
    hits: list[dict[str, Any]],
    virtual_to_record_map: dict[str, Any],
    plain_points: set[Any],
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, int]]:
    """The plain search's hits, first page varied across sources.

    ``hits`` are the plain search's hits (point ids in ``plain_points``) and
    the deeper candidates. Every record of the plain search is kept with the
    hits it had; a candidate record is kept only when it fills a first-page
    place a capped source gave up, and brings its own hits. Returns those hits
    (in their order), the record map narrowed to the records kept, and each
    kept record's place by record id.
    """
    ranked = sorted(
        (hit for hit in hits if isinstance(hit, dict)),
        key=lambda hit: hit.get("score") or 0.0,
        reverse=True,
    )
    in_plain = {
        id(hit) for hit in ranked
        if (hit.get("metadata") or {}).get("point_id") in plain_points
    }
    plain_vids = {
        vid for hit in ranked
        if id(hit) in in_plain and (vid := hit_record_id(hit)) is not None
        and virtual_to_record_map.get(vid)
    }
    # Records best first: a plain record placed by its plain hits only.
    vids: list[str] = []
    known: set[str] = set()
    for hit in ranked:
        vid = hit_record_id(hit)
        if vid is None or vid in known or not virtual_to_record_map.get(vid):
            continue
        if vid in plain_vids and id(hit) not in in_plain:
            continue
        known.add(vid)
        vids.append(vid)
    order = diversity.order(
        vids,
        plain=plain_vids,
        source_of=lambda vid: record_source(virtual_to_record_map[vid]),
    )
    brought_up = set(order) - plain_vids
    plain_hit_vids = {hit_record_id(hit) for hit in ranked if id(hit) in in_plain}
    dropped = {
        hit_record_id(hit) for hit in ranked if id(hit) not in in_plain
    } - plain_hit_vids - brought_up
    kept_hits = [
        hit for hit in hits
        if not isinstance(hit, dict) or id(hit) in in_plain or hit_record_id(hit) in brought_up
    ]
    kept_map = {vid: record for vid, record in virtual_to_record_map.items() if vid not in dropped}
    if order == [vid for vid in vids if vid in plain_vids]:
        # No source over its share: the plain result, as it was.
        return kept_hits, kept_map, {}
    record_rank: dict[str, int] = {}
    for vid in order:
        record_rank.setdefault(virtual_to_record_map[vid].get("_key"), len(record_rank))
    return kept_hits, kept_map, record_rank


def _graph_filters(filter_groups: dict[str, Any] | None) -> dict[str, Any]:
    """``filter_groups`` in the shape ``get_accessible_virtual_record_ids`` expects."""
    filters: dict[str, Any] = {}
    for key, values in (filter_groups or {}).items():
        # Lowercased it would become a metadata term the graph does not understand.
        if key == "entityFilters":
            continue
        # strictScope is a control flag, not a metadata filter
        # key — lowercasing it to "strictscope" would silently
        # break the empty-project-scope short-circuit in
        # get_accessible_virtual_record_ids.
        if key == "strictScope":
            filters[key] = values
            continue
        # A selection or an allow-list is resolved apart, never a metadata term.
        if key in _SELECTION_REQUEST_KEYS:
            continue
        # Convert key to match collection naming
        filters[key.lower()] = values  # e.g., 'departments', 'categories', etc.
    return filters


def _narrow_virtual_ids(current: list[str] | None, allowed: list[str]) -> list[str]:
    if not current:
        return allowed
    keep = set(allowed)
    return [vid for vid in dict.fromkeys(current) if vid in keep]


# Filter keys that carry a selection below app level or an allow-list. They
# keep their case and never reach the permission queries as metadata filters.
_SELECTION_REQUEST_KEYS = frozenset(
    (*SELECTION_FILTER_KEYS, *ALLOWED_FILTER_KEYS, SELECTION_APPS_FILTER_KEY)
)

# Connector names only label citations; a slow graph must not hold up the search.
CONNECTOR_NAME_LOOKUP_TIMEOUT_SECONDS = 2.0

# Connector instance display names for citations. Each search looked up the App
# of every connector in its results, one query each; a rename shows within a minute.
_connector_name_cache = TtlCache(maxsize=10_000, ttl_seconds=60.0)


class RetrievalService:
    # Local models may load or download on first use, so their queries get no deadline.
    # A class default, so an instance built without __init__ (as some tests do) has it too.
    _cached_embedding_is_local = True

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
            llm_configs = (ai_models or {}).get("llm") or []
            if not llm_configs:
                # The normal state until an admin configures a model, not an error.
                self.logger.info("No LLM configured")
                return self.llm

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
                    is_local = True
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
                    is_local = is_local_cpu_embedding_provider(
                        provider, (selected_config.get("configuration") or {}).get("endpoint")
                    )

                self._cached_dense_embeddings = dense_embeddings
                self._cached_embedding_config_hash = config_hash
                self._cached_embedding_is_local = is_local
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
            embedding_configs = (ai_models or {}).get("embedding") or []
            if embedding_configs:
                # The model that embeds: the default one, else the first, as
                # get_embedding_model_instance picks it.
                config = next(
                    (c for c in embedding_configs if c.get("isDefault", False)),
                    embedding_configs[0],
                )
                model = (config.get("configuration") or {}).get("model")
                if model:
                    return model

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
            # Read, not popped: callers reuse their filter_groups across searches.
            entity_filters = filter_groups.get("entityFilters")

            # Extract KB IDs for response metadata
            kb_ids = filter_groups.get('kb', None) if filter_groups else None

            filters = _graph_filters(filter_groups)
            scope_request: dict[str, Any] = {
                key: values for key, values in (filter_groups or {}).items() if key in _SELECTION_REQUEST_KEYS
            }

            # A selection below app level. The permission queries below keep
            # seeing app ids only: the apps the selection touches.
            selection: SelectionScope | None = None
            scopes: list[SelectionScope] = []
            if scope_request:
                resolved = await self._resolve_selection(user_id, org_id, filters, scope_request)
                if isinstance(resolved, dict):
                    return resolved
                selection, scopes = resolved
                if selection is not None:
                    filters = {k: v for k, v in filters.items() if k not in CONTAINER_SCOPE_FILTER_KEYS}
                    filters["apps"] = sorted(selection.connector_ids)

            fallback_on = narrow_access_fallback_enabled()
            memo_key = narrow_access_memo.key(org_id, user_id, filters)
            remembered = fallback_on and narrow_access_memo.is_narrow(memo_key)
            try:
                try:
                    containers, accessible_virtual_id_to_record_id, user = (
                        await self._resolve_search_scope(
                            user_id, org_id, filters, time_range, prefer_record_ids=remembered,
                        )
                    )
                except PermissionVerificationUnavailableError as exc:
                    if not remembered:
                        raise
                    # The remembered record-id route could not read the record
                    # list; the container route checks hits without it. Forget
                    # the scope and try that once.
                    narrow_access_memo.forget(memo_key)
                    remembered = False
                    self.logger.warning(
                        "record list unreadable for remembered scope (user=%s org=%s); "
                        "trying the container search: %s", user_id, org_id, exc,
                    )
                    containers, accessible_virtual_id_to_record_id, user = (
                        await self._resolve_search_scope(user_id, org_id, filters, time_range)
                    )
                if remembered and containers is None and not accessible_virtual_id_to_record_id:
                    # The remembered route found an empty record list. Access
                    # changes, and the list can lag a grant: forget the scope and
                    # let the containers decide, rather than answering "nothing"
                    # from the memo for minutes. A demo-app exclusion still takes
                    # the record-id path there, as it does without the memo.
                    narrow_access_memo.forget(memo_key)
                    self.logger.info(
                        "empty record list for remembered scope (user=%s org=%s); "
                        "trying the container search", user_id, org_id,
                    )
                    containers, accessible_virtual_id_to_record_id, user = (
                        await self._resolve_search_scope(user_id, org_id, filters, time_range)
                    )
            except PermissionVerificationUnavailableError as exc:
                self.logger.warning(
                    "Could not read what user %s may access in org %s: %s", user_id, org_id, exc
                )
                return self._create_empty_response(
                    PERMISSION_CHECK_UNAVAILABLE_MESSAGE, Status.PERMISSION_CHECK_UNAVAILABLE
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

            # The entity filter runs inside the permission scope: what it matches in
            # records this user cannot read must change neither the answer nor "too broad".
            entity_filtered = False
            if entity_filters is not None:
                entity_vids, early = await self._entity_filter_virtual_ids(
                    org_id, user_id, entity_filters, filters, time_range,
                    None if use_containers else accessible_virtual_id_to_record_id,
                )
                if early is not None:
                    return early
                if entity_vids is not None:
                    entity_filtered = True
                    virtual_record_ids_from_tool = _narrow_virtual_ids(virtual_record_ids_from_tool, entity_vids)
                    if not virtual_record_ids_from_tool:
                        return self._create_empty_response(ENTITY_FILTER_NO_MATCH_MESSAGE, Status.EMPTY_RESPONSE)

            plan = _QueryPlan()

            def empty(message: str, status: Status) -> dict[str, Any]:
                if entity_filtered and status in (Status.EMPTY_RESPONSE, Status.ACCESSIBLE_RECORDS_NOT_FOUND):
                    message, status = ENTITY_FILTER_NO_MATCH_MESSAGE, Status.EMPTY_RESPONSE
                response = self._create_empty_response(message, status)
                incomplete = plan.incomplete_notice()
                if incomplete:
                    # Nothing matched where the search ran; that is not the same
                    # as nothing matching.
                    response["message"] = f"{incomplete} {message}"
                    response["searchIncomplete"] = incomplete
                return response

            # Graph key for KH permission_role checks (Location trails).
            user_key = (user.get("_key") or user.get("id")) if user else None
            if not user_key:
                # The scope above was read for this user: no key means the lookup did not answer.
                self.logger.warning(
                    "No graph user key for user %s in org %s; hits cannot be checked", user_id, org_id
                )
                return self._create_empty_response(
                    PERMISSION_CHECK_UNAVAILABLE_MESSAGE, Status.PERMISSION_CHECK_UNAVAILABLE
                )

            permission_filter = await self._permission_filter(
                org_id, containers, accessible_virtual_id_to_record_id, virtual_record_ids_from_tool, selection,
            )
            if permission_filter is None:
                return empty(ACCESSIBLE_RECORDS_NOT_FOUND_MESSAGE, Status.ACCESSIBLE_RECORDS_NOT_FOUND)
            filter, scope_virtual_ids = permission_filter

            record_cut = _record_cut_for(scope_virtual_ids)
            # The search API's first page, varied across sources: the plain
            # search runs as always, and a deeper read supplies candidates for
            # the places a capped source gives up (SEARCH_SOURCE_DIVERSITY).
            diversity = (
                active_source_diversity(
                    scope_ids=requested_scope_ids(filters),
                    scope_virtual_ids=scope_virtual_ids,
                )
                if knowledge_search
                else None
            )
            enumerated_map: dict[str, str] | None = None
            # The narrow-access fallback replaced the container page.
            replaced_by_record_ids = False
            if use_containers:
                (
                    search_results,
                    accessible_virtual_id_to_record_id,
                    verification_degraded,
                ) = await self._search_and_adjudicate(
                    queries, filter, limit, org_id, user_id, containers,
                    allow_requery=not virtual_record_ids_from_tool,
                    scope_connector_ids=containers.scope_connector_ids,
                    record_cut=record_cut,
                    plan=plan,
                    user_key=user_key,
                    scopes=scopes,
                )
                if verification_degraded:
                    # The graph could not answer. Telling this user to upload
                    # documents would be wrong and unactionable.
                    return self._create_empty_response(
                        PERMISSION_CHECK_UNAVAILABLE_MESSAGE,
                        Status.PERMISSION_CHECK_UNAVAILABLE,
                    )
                if plan.narrow_shortfall:
                    # This user reads too small a share of these containers for
                    # their filter to serve the page. Search again under the
                    # record-id filter, hits checked the same way; and send this
                    # scope's next searches straight there for a while. If that
                    # cannot run, the page the access check already admitted
                    # stands.
                    adjudicated = (search_results, accessible_virtual_id_to_record_id)
                    kept_failed, kept_searched = plan.failed_collections, plan.collections_searched
                    try:
                        replacement = await self._search_by_record_ids(
                            queries, limit, org_id, user_id, user_key, filters, time_range,
                            virtual_record_ids_from_tool, selection, scopes, plan,
                        )
                    except Exception as exc:
                        if not search_results:
                            # Nothing was admitted to keep: report the failure as
                            # the record-id path would, never as "no match".
                            if isinstance(exc, PermissionVerificationUnavailableError):
                                self.logger.warning(
                                    "Could not read what user %s may access in org %s: %s",
                                    user_id, org_id, exc,
                                )
                                return self._create_empty_response(
                                    PERMISSION_CHECK_UNAVAILABLE_MESSAGE,
                                    Status.PERMISSION_CHECK_UNAVAILABLE,
                                )
                            raise
                        self.logger.warning(
                            "record-id search after a container shortfall failed "
                            "(user=%s org=%s); keeping the container results: %s",
                            user_id, org_id, exc,
                        )
                        replacement = None
                        plan.failed_collections, plan.collections_searched = kept_failed, kept_searched
                    if replacement is None:
                        search_results, accessible_virtual_id_to_record_id = adjudicated
                    elif replacement.empty_scope:
                        # The record list is empty. No vector query: an empty id
                        # list is dropped by every backend, which would leave an
                        # org-wide search. What the access check admitted stands.
                        if not search_results:
                            return empty(ACCESSIBLE_RECORDS_NOT_FOUND_MESSAGE, Status.ACCESSIBLE_RECORDS_NOT_FOUND)
                    elif not replacement.results:
                        # The record list over-admits (RESTRICTED pages, a revoked
                        # grant still cached), so its search can be denied
                        # wholesale. A servable answer is never replaced by
                        # nothing, and the scope is not remembered for a route
                        # that served nothing.
                        self.logger.info(
                            "record-id search admitted no hits, the container search %d "
                            "(user=%s org=%s); keeping the container results",
                            len(search_results), user_id, org_id,
                        )
                        search_results, accessible_virtual_id_to_record_id = adjudicated
                        plan.failed_collections, plan.collections_searched = kept_failed, kept_searched
                    else:
                        search_results = replacement.results
                        accessible_virtual_id_to_record_id = replacement.accessible
                        replaced_by_record_ids = True
                        narrow_access_memo.mark(memo_key)
                        self.logger.info(
                            "container search denied too much (user=%s org=%s); "
                            "using the record-id filter for this scope for %ds",
                            user_id, org_id, NARROW_ACCESS_MEMO_TTL_SECONDS,
                        )
            if not use_containers:
                search_results = await self._execute_parallel_searches(
                    queries, filter, limit, org_id, user_id, plan=plan, record_cut=record_cut
                )
                try:
                    enumerated = enumerated_map = accessible_virtual_id_to_record_id
                    accessible_virtual_id_to_record_id = await self._keep_permitted_hits(
                        search_results, enumerated, user_key, org_id,
                        scope_connector_ids=requested_scope_ids(filters),
                        scopes=scopes,
                    )
                    denied = enumerated.keys() - accessible_virtual_id_to_record_id.keys()
                    # A denied hit is gone, so all of them denied reads as no match.
                    search_results = [
                        result for result in search_results
                        if not isinstance(result, dict)
                        or (result.get("metadata") or {}).get("virtualRecordId") not in denied
                    ]
                except Exception:
                    self.logger.exception(
                        "Permission check of search hits failed (user=%s org=%s)", user_id, org_id,
                    )
                    return self._create_empty_response(
                        "Could not verify document permissions right now. "
                        "Please retry shortly.",
                        Status.PERMISSION_CHECK_UNAVAILABLE,
                    )

            if not search_results:
                self.logger.debug("No search results found")
                return empty("No relevant documents found for your search query. Try using different keywords or broader search terms.", Status.EMPTY_RESPONSE)

            plain_points: set[Any] = set()
            if diversity is not None:
                plain_points = {
                    (hit.get("metadata") or {}).get("point_id")
                    for hit in search_results if isinstance(hit, dict)
                }
            candidates: list[dict[str, Any]] = []
            candidate_map: dict[str, str] = {}
            if diversity is not None and diversity.fetch_limit(limit) > limit:
                # Read deeper only when the caller asked for less than the pool;
                # otherwise the plain result is the pool.

                async def read_by_record_ids(depth: int, candidate_plan: _QueryPlan) -> "_RecordIdSearch":
                    return await self._search_by_record_ids(
                        queries, depth, org_id, user_id, user_key, filters, time_range,
                        virtual_record_ids_from_tool, selection, scopes, candidate_plan,
                    )

                candidates, candidate_map = await self._diversity_candidates(
                    queries, filter, diversity.fetch_limit(limit), org_id, user_id, plan,
                    containers=containers if use_containers else None,
                    enumerated=enumerated_map,
                    by_record_ids=read_by_record_ids,
                    record_ids_first=replaced_by_record_ids,
                    record_cut=record_cut,
                    allow_requery=not virtual_record_ids_from_tool,
                    user_key=user_key,
                    scope_connector_ids=requested_scope_ids(filters),
                    scopes=scopes,
                )
            if candidates:
                search_results = [
                    *search_results,
                    *(
                        hit for hit in candidates
                        if (hit.get("metadata") or {}).get("point_id") not in plain_points
                        and hit_record_id(hit) in candidate_map
                    ),
                ]
                for vid, record_id in candidate_map.items():
                    accessible_virtual_id_to_record_id.setdefault(vid, record_id)

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
                return empty(ACCESSIBLE_RECORDS_NOT_FOUND_MESSAGE, Status.ACCESSIBLE_RECORDS_NOT_FOUND)

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
                return empty(ACCESSIBLE_RECORDS_NOT_FOUND_MESSAGE, Status.ACCESSIBLE_RECORDS_NOT_FOUND)

            # Resolve the viewer's email here, before the maps below start sharing
            # these dicts: `virtual_to_record_map` reaches the citation builders via
            # `get_record`, which copies `webUrl` verbatim onto the blob record.
            user_email = user.get("email") if user else None
            for r in fetched_records:
                if r and r.get("webUrl"):
                    r["webUrl"] = substitute_user_email(
                        r["webUrl"], user_email, r.get("connectorName")
                    )

            await self._attach_connector_instance_names(fetched_records)
            limit_sources = connection_sources_of(filter_groups)
            if limit_sources is not None:
                # The app's own name is its source's or collection's name: shown
                # only when the limit covers that app whole, as the Location
                # trail and record details show it.
                whole_apps = set(limit_sources.get("apps") or ())
                for r in fetched_records:
                    if r and r.get("connectorId") not in whole_apps:
                        r.pop("connectorInstanceName", None)

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

            record_rank: dict[str, int] | None = None
            if plain_points:
                search_results, virtual_to_record_map, record_rank = _vary_sources(
                    diversity, search_results, virtual_to_record_map, plain_points,
                )

            unique_record_ids = {r.get("_key") for r in virtual_to_record_map.values() if r}

            if not unique_record_ids:
                return empty(ACCESSIBLE_RECORDS_NOT_FOUND_MESSAGE, Status.ACCESSIBLE_RECORDS_NOT_FOUND)
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
                        result["metadata"]["connectorInstanceName"] = record.get("connectorInstanceName")
                        result["metadata"]["kbId"] = record.get("kbId", None)
                        weburl = record.get("webUrl")
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

                        # Only files and mails can find a link elsewhere; any other record
                        # without one (a SQL table when FRONTEND_PUBLIC_URL is unset) is
                        # still a hit, and dropping it here hid it from search and chat.
                        if not weburl:
                            if record.get("recordType", "") == RecordTypes.FILE.value:
                                file_record_ids_to_fetch.append(record_id)
                                result_to_record_map[idx] = (record_id, "file")
                                continue
                            if record.get("recordType", "") == RecordTypes.MAIL.value:
                                mail_record_ids_to_fetch.append(record_id)
                                result_to_record_map[idx] = (record_id, "mail")
                                continue

                        if knowledge_search:
                            meta = result.get("metadata")
                            is_block_group = meta.get("isBlockGroup")
                            if is_block_group is not None:
                                # Fetch the record once, but flatten every hit on it:
                                # a later hit left raw was neither expanded nor known
                                # as a match, and could repeat a passage already shown.
                                if virtual_id not in virtual_record_id_to_record:
                                    await get_record(virtual_id, virtual_record_id_to_record, self.blob_store, org_id, virtual_to_record_map)
                                if virtual_record_id_to_record[virtual_id] is None:
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

                    # A connected app's limit trims the trail to what it shares;
                    # past it, the trail names nothing rather than everything.
                    try:
                        limit_scope = await connection_bound_scope(
                            self.graph_provider, user_key or "", org_id, filter_groups,
                        )
                    except Exception as limit_exc:
                        self.logger.warning("fetch_locations: limit unresolved — %s", limit_exc)
                        return {}

                    result = await resolve_ancestor_locations(
                        rids,
                        graph_provider=self.graph_provider,
                        org_id=org_id,
                        user_key=user_key or "",
                        record_docs=record_id_to_record_map,
                        connection_limit=limit_scope,
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
                    fallback_mimetype = "text/html"

                if weburl:
                    resolved_weburl = substitute_user_email(
                        weburl, user_email, record.get("connectorName")
                    )
                    result["metadata"]["webUrl"] = resolved_weburl
                    # `record` is the same object virtual_to_record_map holds (see
                    # record_id_to_record_map / _create_virtual_to_record_mapping),
                    # so citation building via chat_helpers.get_record() needs this too.
                    record["webUrl"] = resolved_weburl

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

            # The score of the hit on each matched passage of an expanded section.
            match_scores: dict[int, float] = {}
            if new_type_results:
                is_multimodal_llm = False   #doesn't matter for retrieval service
                flattened_results = await get_flattened_results(new_type_results, self.blob_store, org_id, is_multimodal_llm, virtual_record_id_to_record, from_retrieval_service=True)
                for result in flattened_results:
                    block_type = result.get("block_type")
                    child_results = group_child_results(result)
                    if (
                        block_type == GroupType.TABLE.value or block_type in valid_group_labels
                    ) and child_results is not None:
                        matched = {
                            i: score
                            for i, score in (result.get("hit_block_scores") or {}).items()
                            if isinstance(i, int)
                        }
                        for child in child_results:
                            child_index = child.get("block_index")
                            if matched and isinstance(child_index, int):
                                # 0 is a passage the search matched; the per-record
                                # cap below keeps those first, then their neighbours.
                                child["distanceFromMatch"] = min(abs(child_index - i) for i in matched)
                                if child_index in matched:
                                    # Every child carries the section's best score; a
                                    # matched one is ranked by its own hit's score.
                                    match_scores[id(child)] = matched[child_index]
                            final_search_results.append(child)
                    else:
                        final_search_results.append(result)

            final_search_results = sorted(
                final_search_results,
                key=lambda x: x.get("score") or 0,
                reverse=True,
            )
            if record_rank:
                # The varied first page, then the records it demoted; each
                # record's passages stay in score order.
                last = len(record_rank)
                final_search_results.sort(
                    key=lambda x: record_rank.get((x.get("metadata") or {}).get("recordId"), last)
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
            omitted_passages: dict[str, int] = {}
            if knowledge_search:
                # A match inside a thread, a pull request or a table brings back
                # every passage of that section; one long record could fill the
                # response with thousands of them. Each record keeps the passages
                # the search matched first, best score first, then the passages
                # nearest them. A hit that was not expanded is a match itself.
                search_results, omitted_passages = cap_passages_per_record(
                    search_results,
                    passages_per_record(limit),
                    record_of=lambda r: (r.get("metadata") or {}).get("recordId"),
                    priority=lambda r: (
                        (r.get("distanceFromMatch") or 0) > 0,
                        -match_scores.get(id(r), r.get("score") or 0.0),
                        r.get("distanceFromMatch") or 0,
                    ),
                )
                if omitted_passages:
                    self.logger.debug(
                        "search passages capped at %d per record: %d records lost %d passages",
                        passages_per_record(limit),
                        len(omitted_passages),
                        sum(omitted_passages.values()),
                    )
                # The assistant reads each record's state in its header, and
                # the records it links to under it; an API caller (the MCP
                # search tool) gets the same on each record document.
                await describe_search_records(
                    records, self.graph_provider, org_id, user_id=user_id,
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
                if omitted_passages:
                    # recordId -> passages left out, so a caller can say how many
                    # each record matched in all.
                    response_data["omittedPassages"] = omitted_passages
                incomplete = plan.incomplete_notice()
                if incomplete:
                    response_data["searchIncomplete"] = incomplete

                # Add KB filtering info to response if KB filtering was applied
                if kb_ids:
                    response_data["appliedFilters"] = {
                        "kb": kb_ids,
                        "kb_count": len(kb_ids)
                    }

                return response_data
            else:
                return empty("No relevant documents found for your search query. Try using different keywords or broader search terms.", Status.EMPTY_RESPONSE)
        except VectorSearchUnavailableError as exc:
            self.logger.error("Filtered search could not reach the vector store: %s", exc)
            return self._create_empty_response(
                _SEARCH_INDEX_FAILURE_MESSAGES[exc.kind], Status.SEARCH_INDEX_UNAVAILABLE,
            )
        except VectorDBEmptyError:
            self.logger.error("VectorDBEmptyError")
            return self._create_empty_response(
                    "No records indexed yet. Please upload documents or enable connectors to index content",
                    Status.VECTOR_DB_EMPTY,
                )
        except ValueError as e:
            self.logger.error("Filtered search failed with a ValueError: %s", e, exc_info=True)
            return self._create_empty_response("Unexpected server error during search.", Status.ERROR)
        except Exception as e:
            self.logger.error(f"Filtered search failed: {e}\n{traceback.format_exc()}")
            return self._create_empty_response("Unexpected server error during search.", Status.ERROR)

    async def _diversity_candidates(
        self,
        queries: list[str],
        filter: Any,  # noqa: A002, ANN401
        depth: int,
        org_id: str,
        user_id: str,
        plan: "_QueryPlan",
        *,
        containers: "AccessibleContainers | None",
        enumerated: dict[str, str] | None,
        by_record_ids: "Callable[[int, _QueryPlan], Awaitable[_RecordIdSearch]]",
        record_ids_first: bool,
        record_cut: RecordCut | None,
        allow_requery: bool,
        user_key: str | None,
        scope_connector_ids: "tuple[str, ...] | None",
        scopes: Sequence[SelectionScope],
    ) -> tuple[list[dict[str, Any]], dict[str, str]]:
        """Hits from a deeper read of the same search, checked the same way.

        Candidates for the first page only: the plain result is not changed by
        them. The read takes the path the page took (``record_ids_first`` when
        the narrow-access fallback replaced a container page, or when this
        read falls short the same way), with the same record cut and retry.
        Reuses the plain search's embeddings. Returns the hits the user may
        read and their ``{vrid: recordId}``; nothing when the read or its
        check fails, so the plain result stands.
        """
        candidate_plan = _QueryPlan(
            dense=plan.dense, sparse=plan.sparse, collections=plan.collections,
        )
        try:
            if containers is None:
                if enumerated is None:
                    return [], {}
                hits = await self._execute_parallel_searches(
                    queries, filter, depth, org_id, user_id, plan=candidate_plan,
                    record_cut=record_cut,
                )
                accessible = await self._keep_permitted_hits(
                    hits, enumerated, user_key, org_id,
                    scope_connector_ids=scope_connector_ids, scopes=scopes,
                )
            else:
                if not record_ids_first:
                    hits, accessible, degraded = await self._search_and_adjudicate(
                        queries, filter, depth, org_id, user_id, containers,
                        allow_requery=allow_requery,
                        scope_connector_ids=containers.scope_connector_ids,
                        record_cut=record_cut,
                        plan=candidate_plan,
                        user_key=user_key,
                        scopes=scopes,
                    )
                    if degraded:
                        return [], {}
                    record_ids_first = candidate_plan.narrow_shortfall
                if record_ids_first:
                    # As for the page: the record-id search, with its own cut and check.
                    found = await by_record_ids(depth, candidate_plan)
                    hits, accessible = found.results, found.accessible
        except Exception as exc:
            self.logger.warning("source diversity: deeper read failed, keeping the plain result: %s", exc)
            return [], {}
        kept = [
            hit for hit in hits
            if isinstance(hit, dict) and hit_record_id(hit) in accessible
        ]
        return kept, {vid: accessible[vid] for vid in {hit_record_id(hit) for hit in kept}}

    async def _entity_filter_virtual_ids(
        self,
        org_id: str,
        user_id: str,
        raw: object,
        filters: dict[str, list[str]],
        time_range: dict[str, int] | None,
        accessible: dict[str, str] | None,
    ) -> tuple[list[str] | None, dict[str, Any] | None]:
        """Virtual record ids the entity filter matches among records this user may
        read (None when it constrains nothing), or a response to return instead.

        ``accessible`` is the user's vrid -> recordId map when the search already
        built one; under container scoping it is None and the search adjudicates
        the hits afterwards. A match too broad across the org is counted again over
        the records the user may read, so "too broad" never reflects what they cannot.
        """
        resolver = EntityFilterResolver(self.graph_provider)
        try:
            match = await resolver.match(org_id, raw)
            if match.error == TOO_BROAD_MESSAGE:
                if accessible is None:
                    accessible = await self._get_accessible_virtual_ids_task(
                        user_id, org_id, filters, self.graph_provider, time_range=time_range,
                        exclude_app_ids=await self._excluded_demo_apps(user_id, org_id),
                    )
                match = await resolver.match(org_id, raw, within=list(dict.fromkeys(accessible.values())))
        except EntityFilterUnavailableError:
            self.logger.warning("Named-entity filter lookup failed for org %s", org_id, exc_info=True)
            return None, self._create_empty_response(ENTITY_FILTER_UNAVAILABLE_MESSAGE, Status.ERROR)
        except PermissionVerificationUnavailableError as exc:
            self.logger.warning("Could not read what user %s may access in org %s: %s", user_id, org_id, exc)
            return None, self._create_empty_response(PERMISSION_CHECK_UNAVAILABLE_MESSAGE, Status.PERMISSION_CHECK_UNAVAILABLE)
        if match.error:
            return None, self._create_empty_response(match.error, Status.INVALID_FILTER)
        if not match.constrained:
            return None, None
        vids = match.virtual_record_ids
        if accessible is not None:
            vids = [vid for vid in vids if vid in accessible]
        return vids, None

    async def _resolve_selection(
        self,
        user_id: str,
        org_id: str,
        filters: dict[str, list[str]],
        scope_request: dict[str, Any],
    ) -> "tuple[SelectionScope | None, list[SelectionScope]] | dict[str, Any]":
        """The request's selection below app level and every scope a cited
        record must lie in (the selection, and an agent's or project's
        allow-list), or the response to return when there is nothing to search.
        """
        user = await self._get_user_cached(user_id)
        user_key = (user.get("_key") or user.get("id")) if user else None
        if not user_key:
            return self._create_empty_response(
                PERMISSION_CHECK_UNAVAILABLE_MESSAGE, Status.PERMISSION_CHECK_UNAVAILABLE
            )
        requested = {key: filters.get(key) for key in CONTAINER_SCOPE_FILTER_KEYS}
        requested.update(scope_request)
        try:
            selection, scopes = await resolve_request_scopes(
                self.graph_provider, user_key, org_id, requested,
            )
            if selection is not None:
                if selection.is_empty:
                    return self._create_empty_response(SELECTION_EMPTY_MESSAGE, Status.SELECTION_EMPTY)
                await require_membership_ready(self.graph_provider, selection)
        except SelectionTooLargeError:
            return self._create_empty_response(SELECTION_TOO_LARGE_MESSAGE, Status.SELECTION_TOO_LARGE)
        except SelectionNotReadyError:
            return self._create_empty_response(SELECTION_NOT_READY_MESSAGE, Status.SELECTION_NOT_READY)
        except PermissionVerificationUnavailableError as exc:
            self.logger.warning("Could not resolve the selection of user %s in org %s: %s", user_id, org_id, exc)
            return self._create_empty_response(
                PERMISSION_CHECK_UNAVAILABLE_MESSAGE, Status.PERMISSION_CHECK_UNAVAILABLE
            )
        return selection, scopes

    async def _resolve_search_scope(
        self,
        user_id: str,
        org_id: str,
        filters: dict[str, list[str]],
        time_range: dict[str, int] | None,
        *,
        prefer_record_ids: bool = False,
    ) -> tuple["AccessibleContainers | None", dict[str, str], dict[str, Any] | None]:
        """Decide how this search's permission filter is built.

        Returns ``(containers, accessible_map, user)``. ``containers`` is None
        whenever the record-id path is in force — the request carries a
        record-level predicate no container can express, or the graph declined
        (an unbacklogged connector, a filter too large). The two are never both
        authoritative.
        """
        excluded = await self._excluded_demo_apps(user_id, org_id)

        # Built on demand, never eagerly: an un-awaited coroutine is a
        # RuntimeWarning on every search, and the ON path does not want one.
        def _legacy():
            return self._get_accessible_virtual_ids_task(
                user_id, org_id, filters, self.graph_provider, time_range=time_range,
                exclude_app_ids=excluded,
            )

        user_task = self._get_user_cached(user_id)

        # A container scope cannot leave one app out, so an exclusion keeps the
        # record-id path. So does a scope the container filter recently could
        # not serve (``narrow_access_memo``): the record-id path is the exact
        # one, so preferring it never widens what a search may return.
        if excluded or prefer_record_ids:
            accessible, user = await asyncio.gather(_legacy(), user_task)
            return None, accessible, user

        # Recognised locally, without asking the graph: no container expresses a
        # date range or a per-record metadata term, so those keep the record-id
        # path. This is a capability gap, not a preference. `apps` and `kb` are
        # containers and do not land here.
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

        # The scope is read from the request here as well as by the provider,
        # and the two must agree. Everything downstream — the vector filter and
        # the verifier — narrows by what the provider echoes, so a provider that
        # ignored the scope would otherwise widen a Collection-scoped search to
        # the user's whole corpus without any error.
        requested = requested_scope_ids(filters)
        expected = frozenset(requested) if requested is not None else None
        if containers.scope_connector_ids != expected or (
            expected is not None and not containers.app_ids <= expected
        ):
            self.logger.warning(
                "container filter declined (scope_mismatch: requested=%s echoed=%s); "
                "using record ids for user=%s org=%s",
                sorted(expected) if expected is not None else None,
                sorted(containers.scope_connector_ids)
                if containers.scope_connector_ids is not None
                else None,
                user_id,
                org_id,
            )
            return None, await _legacy(), user
        return containers, {}, user

    async def _permission_filter(
        self,
        org_id: str,
        containers: "AccessibleContainers | None",
        accessible_virtual_id_to_record_id: dict[str, str],
        virtual_record_ids: list[str] | None,
        selection: SelectionScope | None = None,
    ) -> tuple[Any, list[str] | None] | None:
        """The vector filter confining a search to what this user may reach, and
        the virtual record ids it is confined to (None when the containers decide
        and any number of records may match). None when nothing is reachable.
        """
        if containers is not None:
            clauses = self._build_container_clauses(org_id, containers, virtual_record_ids, selection)
            if clauses is None:
                return None
            must, should = clauses
            vector_filter = await self.vector_db_service.filter_collection(must=must, should=should)
            return vector_filter, virtual_record_ids or None
        if virtual_record_ids:
            # Intersect before the vector query: rows outside the
            # accessible set are dropped afterwards anyway, and letting
            # them into the top-k crowds out the ones that survive.
            scoped_virtual_ids = [
                vid for vid in dict.fromkeys(virtual_record_ids)
                if vid in accessible_virtual_id_to_record_id
            ]
            if not scoped_virtual_ids:
                return None
            vector_filter = await self.vector_db_service.filter_collection(
                must={"orgId": org_id, "virtualRecordId": scoped_virtual_ids},
                **({"should": selection.should_clauses()} if selection else {}),
            )
            return vector_filter, scoped_virtual_ids
        scope_virtual_ids = list(accessible_virtual_id_to_record_id.keys())
        vector_filter = await self.vector_db_service.filter_collection(
            must={"orgId": org_id, "virtualRecordId": scope_virtual_ids},
            **({"should": selection.should_clauses()} if selection else {}),
        )
        return vector_filter, scope_virtual_ids

    def _build_container_clauses(
        self,
        org_id: str,
        containers: "AccessibleContainers",
        virtual_record_ids_from_tool: list[str] | None,
        selection: SelectionScope | None = None,
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
        if selection is not None:
            # The selection narrows what the containers admit; each hit is
            # still decided by the access check.
            should = selection.should_clauses(containers.app_ids)
        else:
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
        When nothing needs verifying — an all-app-level tenant — this returns
        exactly ``limit`` and the change costs nothing.

        Counts the same containers the adjudicator actually trusts. ``app_ids``
        is wider than ``app_ids_trusted`` — it admits apps on type alone so
        records carrying no recordGroupIds still have a term to match on — so
        sizing from it would call a tenant fully trusted whose apps have not
        declared a permission model, hand it zero headroom, and then pay for a
        second vector fan-out on every search once a per-record check denied
        anything.
        """
        untrusted_apps = len(containers.app_ids) - len(containers.app_ids_trusted)
        checked = len(containers.record_group_ids_verify) + untrusted_apps
        if checked == 0:
            return limit
        trusted = (
            len(containers.app_ids_trusted) + len(containers.record_group_ids_trusted)
        )
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
        # Nothing granted is a denial, not an outage — the verifier raises when
        # the graph cannot answer — so a larger fetch may still find readable
        # records. That is the common shape of a narrow scope.
        return True

    async def _search_by_record_ids(
        self,
        queries: list[str],
        limit: int,
        org_id: str,
        user_id: str,
        user_key: str,
        filters: dict[str, list[str]],
        time_range: dict[str, int] | None,
        virtual_record_ids_from_tool: list[str] | None,
        selection: "SelectionScope | None",
        scopes: Sequence[SelectionScope],
        plan: "_QueryPlan",
    ) -> "_RecordIdSearch":
        """The record-id path for a search the container filter could not serve.

        The same steps the record-id path takes: enumerate, filter the vector
        query by the ids, keep the hits the access check admits. Raises when any
        step fails; the caller decides what stands instead.
        """
        enumerated = await self._get_accessible_virtual_ids_task(
            user_id, org_id, filters, self.graph_provider, time_range=time_range,
        )
        if not enumerated:
            return _RecordIdSearch(empty_scope=True)
        permission_filter = await self._permission_filter(
            org_id, None, enumerated, virtual_record_ids_from_tool, selection,
        )
        if permission_filter is None:
            return _RecordIdSearch(empty_scope=True)
        id_filter, scope_virtual_ids = permission_filter
        results = await self._execute_parallel_searches(
            queries, id_filter, limit, org_id, user_id, plan=plan,
            record_cut=_record_cut_for(scope_virtual_ids),
        )
        accessible = await self._keep_permitted_hits(
            results, enumerated, user_key, org_id,
            scope_connector_ids=requested_scope_ids(filters), scopes=scopes,
        )
        denied = enumerated.keys() - accessible.keys()
        results = [
            result for result in results
            if not isinstance(result, dict)
            or (result.get("metadata") or {}).get("virtualRecordId") not in denied
        ]
        return _RecordIdSearch(results=results, accessible=accessible)

    @staticmethod
    def _narrow_shortfall(
        *,
        surviving: int,
        raw: int,
        fetch_limit: int,
        max_batch: int,
        denied: int,
        allow_requery: bool,
    ) -> bool:
        """Whether this search should move to the record-id filter.

        True when the access check admitted less than
        ``_NARROW_ACCESS_MIN_SURVIVAL`` of what the container filter returned
        and more was there to fetch. A user who reads a small slice of a large
        connector cannot be served within the over-fetch cap (measured: 6% of
        the record-id results at 1% readable, 17% at 5%), and even a full page
        drifts from the record-id search's order when much was denied (86%
        overlap at 20% readable, against 98% when nearly everything is).

        The admitted share alone decides. A short page with most hits admitted
        is the retry's to fill: one denied chunk, or overlap between expanded
        queries judged against their combined budget, leaves a page short for a
        user who reads nearly everything, and the retry already stops at the
        cap.

        Never True when nothing was denied: then the container filter selected
        what the record-id filter would have. Never True when the corpus is
        exhausted: the record-id filter selects a subset of the same points.
        """
        if not allow_requery or denied <= 0 or raw <= 0:
            return False
        if max_batch < fetch_limit:
            return False
        return surviving / raw < _NARROW_ACCESS_MIN_SURVIVAL

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
        scope_connector_ids: frozenset[str] | None,
        record_cut: RecordCut | None = None,
        plan: "_QueryPlan | None" = None,
        user_key: str | None = None,
        scopes: Sequence[SelectionScope] = (),
    ) -> tuple[list[dict[str, Any]], dict[str, str], bool]:
        """Search under a container filter, then resolve what the user may read.

        Returns ``(results, accessible_map, verification_degraded)``. The third
        element is what stops a graph outage being reported as an empty corpus.

        The accessible map is the *output* here rather than an input. Container
        scoping admits more than the user may see, and this is what narrows it
        back — after which the intersection downstream behaves exactly as it did
        against a precomputed map, so nothing past this point changes.

        ``plan``, when given, is left describing the attempt that was kept.
        """
        plan = plan if plan is not None else _QueryPlan()
        plan.narrow_shortfall = False
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
        best_failed: list[str] = []

        for attempt in range(MAX_SEARCH_ATTEMPTS):
            # Per attempt, not per loop: a later attempt that fails verification
            # must not condemn an earlier one that succeeded.
            attempt_degraded = False
            # Under the record cut each query is fetched at the fused depth and
            # left uncut: cut before verification, its reserve would go to
            # records this user cannot read and drop readable hits for good.
            # The cut runs once, on what was admitted.
            asked = (
                record_cut.fused_limit(fetch_limit, _OVERFETCH_ABSOLUTE_CAP)
                if record_cut is not None
                else fetch_limit
            )
            try:
                search_results = await self._execute_parallel_searches(
                    queries, filter, asked, org_id, user_id, plan=plan
                )
            except Exception as exc:
                if attempt == 0:
                    raise
                # A wider re-query that cannot run must not cost the answer the
                # first attempt already has.
                self.logger.warning(
                    "container_search: re-query failed, keeping attempt 1: %s", exc,
                )
                break
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
                    best_failed = plan.failed_collections
                break

            try:
                accessible = (await self.graph_provider.check_access(
                    user_key or "",
                    org_id,
                    virtual_record_ids=returned_vids,
                    indexed_only=True,
                    connector_ids=scope_connector_ids,
                    scopes=scopes,
                )).records_by_vrid
            except PermissionVerificationUnavailableError as exc:
                # The caller turns this into a 503 rather than telling a user
                # with a full workspace that nothing matched.
                attempt_degraded = True
                accessible = {}
                self.logger.error(
                    "container_search: verification unavailable for %d vrids "
                    "(user=%s org=%s): %s",
                    len(returned_vids), user_id, org_id, exc,
                )
            surviving = sum(
                1
                for result in search_results
                if result
                and isinstance(result, dict)
                and (result.get("metadata") or {}).get("virtualRecordId") in accessible
            )
            denied = len(returned_vids) - len(accessible)

            self.logger.debug(
                "container_search attempt=%d vids=%d granted=%d surviving=%d",
                attempt + 1, len(returned_vids), len(accessible), surviving,
            )

            if surviving > best_surviving:
                best_results, best_accessible, best_surviving = (
                    search_results, accessible, surviving
                )
                best_degraded = attempt_degraded
                best_failed = plan.failed_collections

            if attempt_degraded:
                # Retrying would hammer a graph that has just failed to answer.
                break

            # First attempt only. A page that cleared the share there and was
            # short is the retry's to fill; the retry reads deeper, where the
            # share falls, and must not turn that into a move that replaces
            # the page it widened.
            if attempt == 0 and narrow_access_fallback_enabled() and self._narrow_shortfall(
                surviving=surviving,
                raw=len(search_results),
                fetch_limit=asked,
                max_batch=plan.max_batch if plan.max_batch is not None else len(search_results),
                denied=denied,
                allow_requery=allow_requery,
            ):
                # The caller searches again under the record-id filter; the
                # wider container retry would cost more and still fall short.
                plan.narrow_shortfall = True
                break

            if not self._should_requery(
                attempt=attempt,
                surviving=surviving,
                limit=budget,
                raw=len(search_results),
                fetch_limit=asked,
                max_batch=plan.max_batch if plan.max_batch is not None else len(search_results),
                denied=denied,
                allow_requery=allow_requery,
            ):
                break

            fetch_limit = min(
                _OVERFETCH_ABSOLUTE_CAP,
                math.ceil(fetch_limit * _OVERFETCH_MAX_MULTIPLIER),
            )

        plan.failed_collections = best_failed
        # Over-fetching is a means, not a promise: drop the headroom this path
        # added, best-scoring first, or by record under the record cut.
        # `budget` is the record-id path's *ceiling*, not its typical output —
        # expanded queries overlap, so its deduped union usually lands under
        # it. Matching the ceiling can therefore return somewhat more than the
        # flag-off path; trimming to `limit` returned a fraction of it, which
        # is the failure this replaces.
        admitted = [
            result
            for result in best_results
            if result
            and isinstance(result, dict)
            and (result.get("metadata") or {}).get("virtualRecordId") in best_accessible
        ]
        admitted.sort(key=lambda r: r.get("score") or 0, reverse=True)
        if record_cut is not None:
            return record_cut.cut(admitted, budget, hit_record_id), best_accessible, best_degraded
        return admitted[:budget], best_accessible, best_degraded

    async def lexical_search_available(self) -> bool:
        """Whether this store can look terms up in a lexical index of chunk text."""
        if self._capabilities.supports_server_side_text_search:
            return True
        if not self._capabilities.supports_sparse_vectors:
            return False
        try:
            return await self._ensure_sparse_embedder() is not None
        except Exception:
            self.logger.warning("lexical search: sparse embedder unavailable", exc_info=True)
            return False

    async def lexical_store_chunks(self, org_id: str, user_id: str) -> int | None:
        """Chunks held by the collections this org's search reads, or None
        when a collection reports no count. A collection shared with other
        orgs counts all of its chunks, so this never under-counts. A provider
        that cannot be asked raises."""
        collections = await self._resolve_search_collections(org_id, user_id)
        infos = await asyncio.gather(
            *(self.vector_db_service.get_collection_info(name) for name in collections),
        )
        counts = [getattr(info, "points_count", None) for info in infos]
        if not all(isinstance(count, int) for count in counts):
            return None
        return sum(counts)

    async def search_lexical(
        self,
        queries: list[str],
        user_id: str,
        org_id: str,
        filter_groups: dict[str, Any] | None = None,
        limit: int = 100,
        virtual_record_ids: list[str] | None = None,
    ) -> LexicalSearchResult:
        """Chunks holding any of each query's terms, ranked by the store's own
        keyword scoring, among the records this user may read.

        The same scope and permission filter as ``search_with_filters``: the
        graph decides the accessible set or containers, the filter is applied
        inside the vector query, and container hits are adjudicated per record.
        ``virtual_record_ids`` narrows within that scope, never widens it.

        Raises ``LexicalSearchUnavailableError`` when the store has no lexical
        index, and ``PermissionVerificationUnavailableError`` when the graph
        cannot say what the user may read.
        """
        if not queries:
            return LexicalSearchResult.nothing(0)
        requests_legs = await self._lexical_legs(queries)
        filters = _graph_filters(filter_groups)
        # A selection below app level and an allow-list hold this search as
        # they hold search_with_filters.
        scope_request = {
            key: values for key, values in (filter_groups or {}).items() if key in _SELECTION_REQUEST_KEYS
        }
        selection: SelectionScope | None = None
        scopes: list[SelectionScope] = []
        if scope_request:
            resolved = await self._resolve_selection(user_id, org_id, filters, scope_request)
            if isinstance(resolved, dict):
                if resolved.get("status") == Status.PERMISSION_CHECK_UNAVAILABLE.value:
                    raise PermissionVerificationUnavailableError(resolved.get("message") or "")
                return LexicalSearchResult.nothing(len(queries))
            selection, scopes = resolved
            if selection is not None:
                filters = {k: v for k, v in filters.items() if k not in CONTAINER_SCOPE_FILTER_KEYS}
                filters["apps"] = sorted(selection.connector_ids)
        containers, accessible, user = await self._resolve_search_scope(user_id, org_id, filters, None)
        if (containers is not None and containers.is_empty) or (containers is None and not accessible):
            return LexicalSearchResult.nothing(len(queries))
        permission_filter = await self._permission_filter(
            org_id, containers, accessible, virtual_record_ids, selection,
        )
        if permission_filter is None:
            return LexicalSearchResult.nothing(len(queries))
        vector_filter, _scope = permission_filter

        # A request with neither leg would be a filter-only query: arbitrary chunks.
        sent = [i for i, (sparse, text) in enumerate(requests_legs) if sparse is not None or text is not None]
        requests = [
            HybridSearchRequest(
                dense_query=None,
                sparse_query=requests_legs[i][0],
                text_query=requests_legs[i][1],
                filter=vector_filter,
                limit=limit,
                fusion_method=FusionMethod.RRF,
            )
            for i in sent
        ]
        batches: list[list] = [[] for _ in queries]
        capped = [False] * len(queries)
        failed_collections = searched_collections = 0
        if requests:
            per_collection, failed_collections = await self._lexical_fan_out(
                await self._resolve_search_collections(org_id, user_id), requests, limit,
            )
            searched_collections = len(per_collection) + failed_collections
            for j, i in enumerate(sent):
                # Judged per collection: merging dedupes chunks across them, and
                # a merged list shorter than the limit can hide one that was full.
                capped[i] = any(len(results[j]) >= limit for _name, results in per_collection)
                batches[i] = merge_collection_results(
                    [CollectionResults(collection_name=name, results=results[j]) for name, results in per_collection],
                    limit,
                    self._result_merger,
                )

        returned = {
            vid
            for batch in batches
            for point in batch
            if (vid := (point.payload.get("metadata") or {}).get("virtualRecordId"))
        }
        user_key = ((user or {}).get("_key") or (user or {}).get("id")) or ""
        if containers is not None and returned and scopes:
            check = await self.graph_provider.check_access(
                user_key, org_id, virtual_record_ids=returned, indexed_only=True,
                connector_ids=(
                    frozenset(containers.scope_connector_ids)
                    if containers.scope_connector_ids is not None else None
                ),
                scopes=scopes,
            )
            accessible = dict(check.records_by_vrid)
        elif containers is not None:
            accessible = (
                await self.graph_provider.filter_accessible_virtual_record_ids(
                    list(returned),
                    user_id,
                    org_id,
                    trusted_app_ids=containers.app_ids_trusted,
                    trusted_group_ids=containers.record_group_ids_trusted,
                    scope_connector_ids=containers.scope_connector_ids,
                )
                if returned
                else {}
            ) or {}
        else:
            # The enumerated map does not apply the access rules: every hit is
            # checked, as search_with_filters checks its hits.
            accessible = await self._keep_permitted_hits(
                [{"metadata": {"virtualRecordId": vid}} for vid in returned], accessible, user_key, org_id,
                scope_connector_ids=requested_scope_ids(filters), scopes=scopes,
            )

        hits: list[list[LexicalHit]] = []
        for batch in batches:
            admitted: list[LexicalHit] = []
            for point in batch:
                vid = (point.payload.get("metadata") or {}).get("virtualRecordId")
                if vid in accessible:
                    admitted.append(LexicalHit(
                        virtual_record_id=vid,
                        text=point.payload.get("page_content") or "",
                        score=float(point.score or 0.0),
                    ))
            hits.append(admitted)
        return LexicalSearchResult(
            hits=hits,
            capped=capped,
            empty_query=[sparse is None and text is None for sparse, text in requests_legs],
            failed_collections=failed_collections,
            searched_collections=searched_collections,
        )

    async def _lexical_fan_out(
        self, collections: list[str], requests: list[HybridSearchRequest], limit: int,
    ) -> tuple[list[tuple[str, list[list]]], int]:
        """Each collection's own per-request results, and how many collections
        could not be searched. Raises when none could."""
        semaphore = asyncio.Semaphore(SEARCH_FANOUT_CONCURRENCY)

        async def _one(name: str) -> list[list]:
            async with semaphore:
                return await self._fan_out_searches([name], requests, limit)

        outcomes = await asyncio.gather(*(_one(name) for name in collections), return_exceptions=True)
        answered = [(name, out) for name, out in zip(collections, outcomes) if not isinstance(out, BaseException)]
        failures = [out for out in outcomes if isinstance(out, BaseException)]
        if failures and not answered:
            raise failures[0]
        return answered, len(failures)

    async def _lexical_legs(self, queries: list[str]) -> list[tuple[Any, str | None]]:
        """Per query, the (sparse vector, text query) of a lexical-only request.

        Both None for a query with no term the index keeps (stop words only);
        sending it would match nothing and read as "no match".
        """
        supports_text = self._capabilities.supports_server_side_text_search
        if supports_text:
            return [(None, query if query.strip() else None) for query in queries]
        if not await self.lexical_search_available():
            raise LexicalSearchUnavailableError(
                f"{self.vector_db_service.get_service_name()} has no lexical index to search"
            )
        embedder = await self._ensure_sparse_embedder()
        sparse = await asyncio.gather(*(embedder.embed_query(query) for query in queries))
        return [
            ((vector if getattr(vector, "indices", None) is not None and len(vector.indices) else None), None)
            for vector in sparse
        ]

    async def _keep_permitted_hits(
        self,
        search_results: list[dict[str, Any]],
        accessible: dict[str, str],
        user_key: str | None,
        org_id: str,
        scope_connector_ids: "tuple[str, ...] | None" = None,
        scopes: Sequence[SelectionScope] = (),
    ) -> dict[str, str]:
        """The part of the enumerated ``{vrid: recordId}`` map that the hits use
        and the permission model admits. A substitute copy is cited only from
        the request's scope (``scope_connector_ids``; None is unscoped).

        The enumeration does not apply the access rules (it admits a RESTRICTED
        page to every member of its space), so the record it chose for each hit
        is checked here. A hit whose chosen record is denied keeps another
        accessible, indexed copy if it has one.
        """
        hit_vrids = {
            (result.get("metadata") or {}).get("virtualRecordId")
            for result in search_results
            if isinstance(result, dict)
        }
        hits = {vrid: accessible[vrid] for vrid in hit_vrids if vrid in accessible}
        if not hits:
            return hits
        connector_ids = frozenset(scope_connector_ids) if scope_connector_ids is not None else None
        if scopes:
            # The enumerated record may lie outside the selection, and asking
            # for it by id would keep it: cite only what the scopes admit.
            check = await self.graph_provider.check_access(
                user_key or "", org_id, virtual_record_ids=hits.keys(), indexed_only=True,
                connector_ids=connector_ids, scopes=scopes,
            )
            return {vrid: check.records_by_vrid[vrid] for vrid in hits if vrid in check.records_by_vrid}
        check = await self.graph_provider.check_access(
            user_key or "", org_id, node_ids=hits.values(),
            virtual_record_ids=hits.keys(), indexed_only=True,
            connector_ids=connector_ids,
        )
        # A placeholder is in node_ids so navigate can open it. It is not a
        # citation: a stub has no content, and a real copy of the same virtual
        # record id is the one to name.
        citable = check.node_ids - check.placeholder_ids
        return {
            vrid: record_id if record_id in citable else check.records_by_vrid[vrid]
            for vrid, record_id in hits.items()
            if record_id in citable or vrid in check.records_by_vrid
        }

    async def _get_accessible_virtual_ids_task(
        self,
        user_id: str,
        org_id: str,
        filters: dict[str, list[str]],
        graph_provider: IGraphDBProvider,
        time_range: dict[str, int] | None = None,
        exclude_app_ids: frozenset[str] = frozenset(),
    ) -> dict[str, str]:
        """
        Separate task for getting accessible virtualRecordId -> recordId mapping (optimized version).

        Returns a dict mapping each accessible virtualRecordId to the specific recordId that the
        user has permission to access, preventing cross-connector leakage.

        Raises PermissionVerificationUnavailableError when that could not be read.
        Without the strict read a failure returns {}, which search would report
        as "no documents are available", the wrong thing to tell this user.
        """
        try:
            return await graph_provider.get_accessible_virtual_record_ids(
                user_id=user_id, org_id=org_id, filters=filters, time_range=time_range,
                raise_on_error=True, exclude_app_ids=exclude_app_ids,
            )
        except PermissionVerificationUnavailableError:
            raise
        except Exception as exc:
            raise PermissionVerificationUnavailableError(str(exc)) from exc

    async def _excluded_demo_apps(self, user_id: str, org_id: str) -> frozenset[str]:
        """The Acme Corp demo connectors this person has switched off, if any."""
        try:
            return await excluded_demo_connector_ids(
                self.graph_provider, self.config_service, org_id, user_id
            )
        except Exception as exc:
            # Unreadable setting: search as before rather than fail the search.
            self.logger.warning("demo data setting unreadable for user=%s: %s", user_id, exc)
            return frozenset()

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
        try:
            user_data = await self.graph_provider.get_user_by_user_id(user_id, raise_on_error=True)
        except Exception as exc:
            raise PermissionVerificationUnavailableError(str(exc)) from exc
        if user_data is None:
            # Not cached: a user created a moment later must be found.
            return None

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
        *, plan: "_QueryPlan | None" = None, record_cut: RecordCut | None = None,
    ) -> list[dict[str, Any]]:
        """Execute all searches in parallel using hybrid (dense + sparse) retrieval with RRF fusion.

        The search strategy adapts to provider capabilities:
        - Providers with sparse vector support (Qdrant): full dense+sparse hybrid with client-side BM25.
        - Providers without sparse support (OpenSearch, Redis): dense-only search,
          server-side BM25/text handled by the provider internally.

        Fans out to every collection the active strategy resolves for ``org_id``
        (one today; a per-connector/per-org strategy can resolve more) and
        merges the raw results before the caller's global rerank/sort.

        With ``record_cut``, each query fuses deeper and is cut back to
        ``limit`` by record; without it, by point.
        """
        # Embedding is the only genuinely expensive, uncached step here, and a
        # re-query differs from the first attempt in nothing but `limit`.
        if plan is not None and plan.dense is not None:
            dense_query_embeddings = plan.dense
            sparse_query_embeddings = plan.sparse
            return await self._run_searches(
                queries, filter, limit, org_id, user_id,
                dense_query_embeddings, sparse_query_embeddings, plan=plan, record_cut=record_cut,
            )

        dense_embeddings = await self.get_embedding_model_instance()
        if not dense_embeddings:
            raise ValueError("No dense embeddings found")
        # Read before the next await, while it still describes the instance just returned.
        attempt_timeout = None if self._cached_embedding_is_local else QUERY_EMBEDDING_ATTEMPT_TIMEOUT_SECONDS

        sparse_embedder = await self._ensure_sparse_embedder()

        dense_tasks = [
            await_with_retry(
                lambda q=query: asyncio.wait_for(
                    dense_embeddings.aembed_query(instruct_query(dense_embeddings, q)),
                    timeout=attempt_timeout,
                ),
                max_retries=_RETRIEVAL_EMBED_MAX_RETRIES,
                operation="aembed_query",
                service_name="retrieval",
            )
            for query in queries
        ]
        supports_sparse = self._capabilities.supports_sparse_vectors

        # The tasks gather() starts copy the context when they are created, so the
        # scope has to be open across the awaits, not just where the coroutines are built.
        with narrow_workload(Workload.QUERY_EMBEDDING):
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
            dense_query_embeddings, sparse_query_embeddings, plan=plan, record_cut=record_cut,
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
        record_cut: RecordCut | None = None,
    ) -> list[dict[str, Any]]:
        """Everything downstream of embedding: build requests, fan out, dedupe.

        Split out so a re-query at a larger limit reuses the embeddings — the
        only field that differs between attempts is ``HybridSearchRequest.limit``.
        """
        all_results: list[tuple] = []
        fused_limit = (
            record_cut.fused_limit(limit, _OVERFETCH_ABSOLUTE_CAP)
            if record_cut is not None
            else limit
        )
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
                limit=fused_limit,
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
        failed_collections: list[str] = []
        search_results = await self._fan_out_searches(
            collections, requests, fused_limit, failed=failed_collections,
        )
        if plan is not None:
            plan.failed_collections = failed_collections
            plan.collections_searched = len(collections)
        if record_cut is not None:
            search_results = self._cut_batches(record_cut, search_results, limit)
        # Measured after the cut, so "exhausted" still means a query came back
        # short of `limit`; a retry at a larger limit keeps more of a deep batch.
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

    def _cut_batches(self, record_cut: RecordCut, batches: list[list], limit: int) -> list[list]:
        cut = [record_cut.cut(batch, limit, point_record_id) for batch in batches]
        if self.logger.isEnabledFor(logging.DEBUG):
            # Records a plain cut at `limit` would have shown, against the record cut.
            self.logger.debug(
                "record cut: points fused %s, kept %s; records %s -> %s",
                [len(batch) for batch in batches],
                [len(batch) for batch in cut],
                [len({point_record_id(p) for p in batch[:limit]}) for batch in batches],
                [len({point_record_id(p) for p in batch}) for batch in cut],
            )
        return cut

    async def _fan_out_searches(
        self,
        collections: list[str],
        requests: list[HybridSearchRequest],
        limit: int,
        *,
        failed: list[str] | None = None,
    ) -> list[list]:
        """Query every collection in parallel and reduce each query to one top-K.

        Each collection is asked for the full ``limit`` rather than a share of
        it: nothing knows in advance which collection holds the best matches,
        so splitting the budget would cost recall. That leaves N ranked lists
        per query, which ``result_merging`` reduces according to what the
        provider's scores actually mean.

        A collection that fails degrades the result set instead of failing the
        search — a search over three collections should not error because one
        is briefly unreachable. Each one it could not search is appended to
        ``failed``, so the caller can say the results are incomplete.
        """
        if not collections:
            return [[] for _ in requests]

        semaphore = asyncio.Semaphore(SEARCH_FANOUT_CONCURRENCY)

        async def _search(collection_name: str) -> list[list]:
            async with semaphore:
                return await query_nearest_points_with_retry(
                    self.vector_db_service, collection_name, requests, logger=self.logger,
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
                if failed is not None:
                    failed.append(collection_name)
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
            # A store that timed out everywhere reads differently from a bug;
            # with both, the bug is what gets reported.
            raise next(
                (f for f in failures if not isinstance(f, VectorSearchUnavailableError)),
                failures[0],
            )

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
            Status.INVALID_FILTER: 422,  # the gateway passes the message through on a 422
            Status.SEARCH_INDEX_UNAVAILABLE: 503,  # vector store timed out or unreachable
            Status.SELECTION_TOO_LARGE: 422,
            Status.SELECTION_NOT_READY: 422,
            Status.SELECTION_EMPTY: 404,
        }

        status_code = status_code_mapping.get(status, 500)  # Default to 500 for unknown status

        return {
            "searchResults": [],
            "records": [],
            "status": status.value,
            "status_code": status_code,
            "message": message,
        }

    async def _attach_connector_instance_names(self, records: list[dict[str, Any]]) -> None:
        """Set ``connectorInstanceName``: the name the admin gave the record's connector.

        ``connectorName`` is only the type code (WEB), shared by every instance
        of that type. A missing or unreadable instance leaves the field unset,
        and the UI falls back to the type.
        """
        records = [r for r in records if isinstance(r, dict)]
        connector_ids = sorted({r["connectorId"] for r in records if r.get("connectorId")})
        if not connector_ids:
            return
        names: dict[str, str | None] = {}
        for cid in connector_ids:
            hit, name = _connector_name_cache.get(cid)
            if hit:
                names[cid] = name
        to_fetch = [cid for cid in connector_ids if cid not in names]
        if to_fetch:
            try:
                apps = await asyncio.wait_for(
                    asyncio.gather(
                        *(self.graph_provider.get_document(cid, CollectionNames.APPS.value) for cid in to_fetch),
                        return_exceptions=True,
                    ),
                    timeout=CONNECTOR_NAME_LOOKUP_TIMEOUT_SECONDS,
                )
            except Exception as e:
                # Labels only: search results stay correct, the UI shows the type code.
                self.logger.warning("Could not resolve connector names for citations: %s", e)
                apps = []
            for cid, app in zip(to_fetch, apps):
                if isinstance(app, dict):
                    names[cid] = app.get("name")
                    _connector_name_cache.set(cid, app.get("name"))
        for r in records:
            name = names.get(r.get("connectorId"))
            if name:
                r["connectorInstanceName"] = name

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
