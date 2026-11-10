"""Redis vector database provider.

Uses Redis >= 8.4 with the Redis Query Engine (RediSearch module built-in).
Hybrid search via ``FT.HYBRID`` combines:
  - Dense KNN leg   (HNSW index on the ``dense_embedding`` field)
  - Lexical BM25 leg (TEXT index on ``page_content``)
  - Fused by native RRF inside Redis.

Storage layout
--------------
Each point is stored as a Redis Hash at key::

    {collection_name}:{point_id}

Hash fields::

    page_content       TEXT    (BM25 lexical leg)
    dense_embedding    bytes   (binary FLOAT16/FLOAT32 blob for HNSW)
    metadata_orgId     str     (TAG-indexed for tenant filtering)
    metadata_virtualRecordId  str  (TAG-indexed)
    connectorIds       str     (comma-joined TAG; instance UUIDs)
    recordGroupIds     str     (comma-joined TAG; record-group UUIDs)
    metadata_*         str     (any additional metadata, stored but not indexed)

The Search index is named::

    {collection_name}_idx

Multiple collections on one Redis instance are isolated by key-prefix / index.
Collection names are resolved by CollectionStrategy and sanitized by
``collections.sanitize_collection_name`` before they reach this provider.

Minimum Redis version: 8.4 (FT.HYBRID command).  Health check enforces this.

Note: The ReJSON module is NOT required — all documents are stored as Redis Hashes
(``ON HASH``), not as JSON documents.  Only the RediSearch module must be loaded.
"""

from __future__ import annotations

import asyncio
import contextlib
import re
import time
from dataclasses import replace
from functools import partial
from typing import TYPE_CHECKING, Any

from app.config.configuration_service import ConfigurationService
from app.config.constants.service import config_node_constants
from app.services.permissions.metrics import (
    SCOPE_PREFILTER_CAPPED,
    SCOPE_PREFILTER_DROPPED,
)
from app.services.permissions.scope_fields import (
    PATH_IDS_FIELD,
    SCOPE_FENCE_FIELD,
    checked_fence,
)
from app.services.vector_db import scope_expr as sx
from app.services.vector_db.const.const import (
    CONNECTOR_IDS_FIELD,
    SCOPE_KEYWORD_INDEXES,
)
from app.services.vector_db.errors import OrgRequired, PartialResult
from app.services.vector_db.filters import (
    ScopeFilterTooLarge,
    build_generic_conditions,
    forbid_fence_in_payload,
    forbid_org_in_payload,
    is_missing,
    keep_org_in_overwrite,
    require_filter_org,
    split_values_count_conditions,
)
from app.services.vector_db.interface.vector_db import IVectorDBService
from app.services.vector_db.models import (
    CollectionConfig,
    DistanceMetric,
    FieldCondition,
    FilterExpression,
    FilterMode,
    FilterValue,
    FusionMethod,
    HealthStatus,
    HybridSearchRequest,
    QuantizationType,
    ScrollResult,
    ScrollWindowExceededError,
    SearchFailureKind,
    SearchResult,
    VectorCollectionInfo,
    VectorDBCapabilities,
    VectorDBHealth,
    VectorPoint,
)
from app.services.vector_db.redis.config import RedisVectorConfig
from app.services.vector_db.redis.errors import classify_redis_search_error
from app.services.vector_db.redis.utils import (
    coerce_payload_hash_value,
    decode_hash_doc,
    filter_expression_to_redis_query,
    hash_doc_to_payload,
    parse_aggregate_rows,
    parse_ft_hybrid_reply,
    parse_ft_search_reply,
    parse_search_rows,
    redisearch_any_term_query,
    refuse_unsafe_ids,
    scope_query,
    vector_point_to_hash_fields,
    vector_to_bytes,
    within_values_count,
)
from app.utils.logger import create_logger
from app.utils.loop_local import LoopLocal

if TYPE_CHECKING:
    from collections.abc import Mapping

    import redis.asyncio as aioredis
    from app.services.redis.connection_provider import RedisClient
    from app.services.vector_db.filters import ScopeFilter

logger = create_logger("redis_vector_service")

#: The most candidates a scoped Redis search asks for while it looks for ``limit`` points that pass the exact rule.
MAX_SCOPE_CANDIDATES = 1000

_REDIS_CAPABILITIES = VectorDBCapabilities(
    supports_sparse_vectors=False,
    supports_server_side_text_search=True,
    supported_fusion_methods=[FusionMethod.RRF],
    supports_multi_collection=True,
    max_recommended_collections=500,
)

# RediSearch caps offset+limit at MAXSEARCHRESULTS; the default is 10000.
_MAX_SEARCH_RESULTS = 10000

# Scans that select points to act on (delete, payload write, scroll) must see
# every match. RediSearch's default ON_TIMEOUT is RETURN: a query past its
# timeout (500 ms by default) answers with the rows it had, and a RESP2 reply
# carries no flag saying so, so a delete or payload write would quietly miss
# points. ``TIMEOUT 0`` lifts the limit for these page-bounded queries. Newer
# servers cap a foreground (``search-workers 0``) query at 60 s, so operators who
# want a hard guarantee can also set ``search-on-timeout FAIL``.
_NO_TIMEOUT = ("TIMEOUT", "0")
# How long a key lookup waits for documents written just before it to show up in the index (0.75 s in all).
_SETTLE_ATTEMPTS = 5
_SETTLE_DELAY_SECONDS = 0.05

# Page size for paged deletes and aggregate-cursor reads.
_DELETE_PAGE_SIZE = 500

# How long RediSearch keeps an idle aggregate cursor alive between reads.
_CURSOR_MAX_IDLE_MS = 300000

# Fallback for when the service was built without a parsed config.
_DEFAULT_MAX_CONCURRENT_SEARCHES = 8

# Minimum Redis version required for FT.HYBRID
_MIN_REDIS_MAJOR = 8
_MIN_REDIS_MINOR = 4

# Distance type mapping (Redis HNSW supports COSINE, L2, IP)
_DIST_MAP = {
    DistanceMetric.COSINE: "COSINE",
    DistanceMetric.L2: "L2",
    DistanceMetric.DOT_PRODUCT: "IP",
}


def _is_unknown_index_error(exc: Exception) -> bool:
    """True only for RediSearch's "index does not exist" reply.

    Everything else — connection refused, timeout, auth, MISCONF — must surface,
    because callers read ``exists=False`` as "not created yet" and respond by
    creating the collection.
    """
    message = str(exc).lower()
    return (
        "unknown index name" in message
        or "no such index" in message
        # Redis 8.8 and later, which the floating redis:8 tag now serves.
        or "search_index_not_found" in message
    )


class RedisVectorService(IVectorDBService):
    """Redis >= 8.4 vector DB provider implementing IVectorDBService."""

    def __init__(
        self,
        config_service: ConfigurationService | RedisVectorConfig,
    ) -> None:
        self.config_service = config_service
        # A redis.asyncio pool binds to the loop that first uses it, and this service is shared
        # by the server loop and every consumer's worker loop. Set by connect().
        self._clients: LoopLocal[RedisClient] | None = None
        # Assigned through `client` (tests); served to every loop as-is.
        self._client_override: RedisClient | None = None
        # Cached collection metadata: name → CollectionConfig used at creation
        self._collection_configs: dict[str, CollectionConfig] = {}
        # Dense vector dtype applied to all indexes; set from config on connect().
        self._dense_dtype: str = "FLOAT16"
        self._max_concurrent_searches: int = _DEFAULT_MAX_CONCURRENT_SEARCHES

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    async def create(
        cls,
        config: ConfigurationService | RedisVectorConfig,
    ) -> "RedisVectorService":
        service = cls(config)
        await service.connect()
        return service

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        try:
            cfg = await self._load_config()
            self._dense_dtype = cfg.dense_dtype
            self._max_concurrent_searches = cfg.max_concurrent_searches

            # Always the standalone provider, regardless of the process-wide
            # REDIS_MODE: RediSearch's FT.HYBRID/FT.SEARCH have no Redis
            # Cluster support in this implementation (VectorDBProviderFactory
            # refuses to select this backend on a cluster/MemoryDB
            # deployment), and `_load_config` already enforces db=0.
            from app.services.redis.config import ClientOptions, RedisConnectionConfig
            from app.services.redis.connection_provider_factory import (
                get_redis_provider,
            )

            provider = get_redis_provider(
                RedisConnectionConfig.from_host_port(
                    host=cfg.host, port=cfg.port, password=cfg.password, db=cfg.db
                ),
                mode="standalone",
            )
            clients: LoopLocal[RedisClient] = LoopLocal(
                partial(
                    provider.create_client,
                    ClientOptions(
                        decode_responses=False,  # we handle bytes ourselves for vectors
                        socket_timeout_seconds=cfg.timeout,
                        socket_connect_timeout_seconds=cfg.timeout,
                    ),
                )
            )
            # Verified on the calling loop; other loops build their client on first use.
            try:
                await clients.get().ping()
            except Exception:
                # Caller-owned client: a failed ping must not leak its connection.
                _ = await clients.aclose_all(lambda client: client.aclose())
                raise
            self._clients = clients
            logger.info(f"Connected to Redis vector store at {cfg.host}:{cfg.port}")
        except Exception as e:
            logger.error(f"Failed to connect to Redis: {e}")
            raise

    @property
    def client(self) -> RedisClient | None:
        """The running loop's client, or None before connect()."""
        if self._client_override is not None:
            return self._client_override
        if self._clients is None:
            return None
        return self._clients.get()

    @client.setter
    def client(self, value: RedisClient | None) -> None:
        self._client_override = value

    async def disconnect(self) -> None:
        """Close every loop's client, each on the loop that owns it."""
        override, self._client_override = self._client_override, None
        clients, self._clients = self._clients, None
        if override is None and clients is None:
            return
        errors = [] if clients is None else await clients.aclose_all(lambda client: client.aclose())
        if override is not None:
            try:
                await override.aclose()
            except Exception as e:
                errors.append(e)
        for error in errors:
            logger.warning(f"Error during Redis disconnect: {error}")
        logger.info("Disconnected from Redis vector store")

    async def _load_config(self) -> RedisVectorConfig:
        if isinstance(self.config_service, ConfigurationService):
            raw = await self.config_service.get_config(
                config_node_constants.REDIS_VECTOR.value
            )
            if not raw:
                # Backward-compatible fallback: reuse the KV Redis config node
                raw = await self.config_service.get_config(
                    config_node_constants.REDIS.value
                )
            if not raw:
                raise ValueError(
                    "Redis vector configuration not found "
                    "(expected /services/redis-vector or /services/redis)"
                )
            cfg = RedisVectorConfig.from_dict(raw)  # type: ignore
        else:
            cfg = self.config_service  # type: ignore
        if cfg.db != 0:
            raise ValueError(
                f"Redis vector store must use DB 0 (RediSearch limitation); "
                f"got db={cfg.db}. Set redis.db=0 in your configuration."
            )
        return cfg

    # ------------------------------------------------------------------
    # Identity
    # ------------------------------------------------------------------

    def get_service_name(self) -> str:
        return "redis"

    def get_service(self) -> "RedisVectorService":
        return self

    def get_service_client(self) -> aioredis.Redis:
        return self.client  # type: ignore

    # ------------------------------------------------------------------
    # Capabilities and health
    # ------------------------------------------------------------------

    def get_capabilities(self) -> VectorDBCapabilities:
        return _REDIS_CAPABILITIES

    def transient_search_error(self, error: BaseException) -> SearchFailureKind | None:
        # The client's own Retry (services/redis/standalone_provider.py) has
        # already re-sent the command by the time an error gets here; the
        # search retry's time budget is what keeps the two from compounding.
        return classify_redis_search_error(error)

    async def health_check(self) -> VectorDBHealth:
        start = time.monotonic()
        if self.client is None:
            return VectorDBHealth(status=HealthStatus.UNHEALTHY, message="Not connected")
        try:
            await self.client.ping()
            # Fetch server version.  With decode_responses=False the client returns
            # bytes; we must decode the version string before comparing.
            info_bytes = await self.client.info("server")
            version_str: str | None = None
            if isinstance(info_bytes, dict):
                raw_version = info_bytes.get("redis_version") or info_bytes.get(b"redis_version")
                if isinstance(raw_version, bytes):
                    raw_version = raw_version.decode(errors="replace")
                version_str = raw_version

            latency_ms = round((time.monotonic() - start) * 1000, 2)

            # Enforce minimum version requirement
            if version_str:
                ok, msg = _check_version(version_str)
                if not ok:
                    return VectorDBHealth(
                        status=HealthStatus.UNHEALTHY,
                        latency_ms=latency_ms,
                        server_version=version_str,
                        message=msg,
                    )

            # Check Search module is available
            try:
                await self.client.execute_command("FT._LIST")
            except Exception as e:
                return VectorDBHealth(
                    status=HealthStatus.UNHEALTHY,
                    latency_ms=latency_ms,
                    server_version=version_str,
                    message=f"Redis Search module not available: {e}",
                )

            return VectorDBHealth(
                status=HealthStatus.HEALTHY,
                latency_ms=latency_ms,
                server_version=version_str,
                message="Redis Search module reachable",
            )
        except Exception as e:
            latency_ms = round((time.monotonic() - start) * 1000, 2)
            return VectorDBHealth(
                status=HealthStatus.UNHEALTHY,
                latency_ms=latency_ms,
                message=str(e),
            )

    # ------------------------------------------------------------------
    # Internal naming helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _index_name(collection_name: str) -> str:
        return f"{collection_name}_idx"

    @staticmethod
    def _key(collection_name: str, point_id: str) -> str:
        return f"{collection_name}:{point_id}"

    # ------------------------------------------------------------------
    # Collection management
    # ------------------------------------------------------------------

    async def create_collection(
        self,
        collection_name: str,
        config: CollectionConfig | None = None,
    ) -> None:
        self._assert_connected()
        if config is None:
            config = CollectionConfig()

        dist = _DIST_MAP.get(config.distance_metric, "COSINE")
        idx = self._index_name(collection_name)

        # Redis holds vectors in memory by definition and RediSearch HNSW accepts
        # only FLOAT32/FLOAT16, so neither CollectionConfig.quantization (INT8) nor
        # quantization_always_ram=False can be honoured here. dense_dtype=FLOAT16
        # is the equivalent lever, giving ~2x reduction against Qdrant's ~4x.
        if config.quantization not in (QuantizationType.NONE, QuantizationType.SCALAR):
            logger.warning(
                "Redis ignores quantization=%s; dense_dtype=%s is the only lever",
                config.quantization.value,
                self._dense_dtype,
            )

        # Idempotency: check if the index already exists with the correct dimension.
        existing_info = await self.get_collection_info(collection_name)
        if existing_info.exists:
            if existing_info.dense_dimension == config.embedding_size:
                logger.info(
                    f"Redis index '{idx}' already exists with correct dim={config.embedding_size}; skipping."
                )
                self._collection_configs[collection_name] = config
                return
            raise ValueError(
                f"Redis index '{idx}' dimension mismatch: "
                f"existing dim={existing_info.dense_dimension}, "
                f"expected {config.embedding_size}. "
                f"Delete the collection explicitly before recreating."
            )

        self._collection_configs[collection_name] = config

        # FT.CREATE schema (ON HASH):
        #   page_content       TEXT  (BM25 lexical leg)
        #   dense_embedding    VECTOR HNSW (binary blob stored directly in hash field)
        #   metadata_orgId     TAG   (tenant filter)
        #   metadata_virtualRecordId TAG
        #
        # No JSONPath (``$.``) prefixes — hash field names map directly to
        # the SCHEMA field names.  The HNSW index reads the binary blob from
        # the ``dense_embedding`` hash field on every HSET, converting it from
        # dense_dtype to the internal graph representation in the background
        # (when search-workers > 0).
        cmd = [
            "FT.CREATE", idx,
            "ON", "HASH",
            "PREFIX", "1", f"{collection_name}:",
            "SCHEMA",
            "page_content", "TEXT",
            "dense_embedding", "VECTOR",
            "HNSW", "6",
            "TYPE", self._dense_dtype,
            "DIM", str(config.embedding_size),
            "DISTANCE_METRIC", dist,
            "metadata_orgId", "TAG",
            "metadata_virtualRecordId", "TAG",
            "connectorIds", "TAG",
            "recordGroupIds", "TAG",
            "rootRecordGroupIds", "TAG",
        ]
        await self.client.execute_command(*cmd)  # type: ignore
        logger.info(
            f"Created Redis collection/index '{idx}' "
            f"(dim={config.embedding_size}, dtype={self._dense_dtype})"
        )

    async def get_collections(self) -> object:
        self._assert_connected()
        return await self.client.execute_command("FT._LIST")  # type: ignore

    async def get_collection(self, collection_name: str) -> object:
        self._assert_connected()
        idx = self._index_name(collection_name)
        return await self.client.execute_command("FT.INFO", idx)  # type: ignore

    async def get_collection_info(self, collection_name: str) -> VectorCollectionInfo:
        self._assert_connected()
        idx = self._index_name(collection_name)
        try:
            raw = await self.client.execute_command("FT.INFO", idx)  # type: ignore
            info = _parse_ft_info(raw)
            num_docs = int(info.get("num_docs", 0) or 0)
            # Prefer cached config (always accurate); fall back to parsing FT.INFO attributes.
            dim: int | None = None
            cfg = self._collection_configs.get(collection_name)
            if cfg:
                dim = cfg.embedding_size
            else:
                dim = _extract_dim_from_ft_info_attributes(info.get("attributes", []))
            return VectorCollectionInfo(
                name=collection_name,
                exists=True,
                dense_dimension=dim,
                points_count=num_docs,
            )
        except Exception as exc:
            if _is_unknown_index_error(exc):
                return VectorCollectionInfo(name=collection_name, exists=False)
            raise

    async def collection_exists(self, collection_name: str) -> bool:
        info = await self.get_collection_info(collection_name)
        return info.exists

    async def delete_collection(self, collection_name: str) -> None:
        self._assert_connected()
        idx = self._index_name(collection_name)
        # Drop index (DD = also delete documents)
        with contextlib.suppress(Exception):
            await self.client.execute_command("FT.DROPINDEX", idx, "DD")  # type: ignore
        self._collection_configs.pop(collection_name, None)
        logger.info(f"Deleted Redis collection '{collection_name}'")

    async def create_index(
        self,
        collection_name: str,
        field_name: str,
        field_schema: dict,
    ) -> None:
        """Add/update a field in the search index via FT.ALTER.

        field_name uses ``metadata.`` prefix convention (e.g. ``metadata.orgId``).
        We convert ``metadata.x`` → ``metadata_x`` for the Redis hash field alias.
        No JSONPath prefix is needed for ON HASH indexes. A failure other than
        "already in the schema" is raised so the caller knows the field is not
        searchable.
        """
        self._assert_connected()
        idx = self._index_name(collection_name)
        redis_alias = field_name.replace(".", "_")
        field_type = {"keyword": "TAG", "integer": "NUMERIC"}.get(field_schema.get("type"), "TEXT")
        try:
            # The scope fields are new in this release line: tags that tell `X` from `x`, so an exclusion can be carried by the query. (The membership
            # tags created with the index in older releases cannot be changed and keep matching without case.)
            case = ["CASESENSITIVE"] if field_type == "TAG" and field_name in {name for name, _ in SCOPE_KEYWORD_INDEXES} else []
            await self.client.execute_command(  # type: ignore
                "FT.ALTER", idx,
                "SCHEMA", "ADD",
                redis_alias, field_type, *case,
            )
        except Exception as e:
            err = str(e).lower()
            # Silently skip fields that are already part of the schema.
            # Redis surfaces this as either "already exists" or
            # "duplicate field in schema".
            if "already exists" in err or "duplicate field" in err:
                return
            raise

    # ------------------------------------------------------------------
    # Filter construction
    # ------------------------------------------------------------------

    async def filter_collection(
        self,
        filter_mode: str | FilterMode = FilterMode.MUST,
        must: dict[str, FilterValue] | None = None,
        should: dict[str, FilterValue] | None = None,
        must_not: dict[str, FilterValue] | None = None,
        min_should_match: int | None = None,
        max_values: dict[str, int] | None = None,
        **kwargs: FilterValue,
    ) -> FilterExpression:
        # Redis does not support min_should_match — reject early before the
        # shared helper is invoked.
        if min_should_match is not None:
            raise NotImplementedError(
                "min_should_match is not supported by the Redis vector provider. "
                "Redis FT.SEARCH only supports implicit OR for SHOULD clauses. "
                "Use a different provider or restructure your filter."
            )

        from app.services.vector_db.filters import build_filter_expression

        return build_filter_expression(
            filter_mode,
            must=must,
            should=should,
            must_not=must_not,
            min_should_match=None,
            max_values=max_values,
            extra_kwargs=kwargs or None,
            build_conditions=_build_generic_conditions,
        )

    # ------------------------------------------------------------------
    # Data operations — all async
    # ------------------------------------------------------------------

    async def upsert_points(
        self,
        collection_name: str,
        points: list[VectorPoint],
        batch_size: int = 500,
    ) -> None:
        self._assert_connected()
        start = time.perf_counter()
        total = len(points)

        for batch_start in range(0, total, batch_size):
            batch = points[batch_start:batch_start + batch_size]
            pipeline = self.client.pipeline(transaction=False)  # type: ignore
            for point in batch:
                key = self._key(collection_name, point.id)
                fields = vector_point_to_hash_fields(point, self._dense_dtype)
                # HSET key field1 val1 field2 val2 ...
                # Flatten the dict into a positional args list for execute_command.
                flat: list[Any] = []
                for fname, fval in fields.items():
                    flat.append(fname)
                    flat.append(fval)
                pipeline.execute_command("HSET", key, *flat)
            results = await pipeline.execute()
            # Check for per-command errors in the pipeline result
            failed = [
                i for i, r in enumerate(results or [])
                if isinstance(r, Exception)
            ]
            if failed:
                raise RuntimeError(
                    f"Redis pipeline upsert had {len(failed)} failed HSET command(s) "
                    f"in batch starting at index {batch_start}. "
                    f"First error: {results[failed[0]] if results else 'unknown'}"
                )

        elapsed = time.perf_counter() - start
        logger.info(
            f"Upserted {total} points into Redis collection '{collection_name}' "
            f"in {elapsed:.2f}s"
        )

    async def delete_points(
        self,
        collection_name: str,
        filter: FilterExpression,  # noqa: A002 - IVectorDBService's parameter name
        refresh: bool = False,  # noqa: FBT001, FBT002 - IVectorDBService's signature
    ) -> None:
        """Delete points matching *filter* using paged deletes.

        With no array-length condition this re-queries from offset 0 after each
        page: the deleted page is gone, so what was page 2 is now page 1, and
        memory stays bounded by page_size rather than by the matching set.

        Array-length conditions have no RediSearch equivalent, so they are
        peeled off and applied to each page here. That makes some matches
        survive, so the offset-0 shortcut no longer holds and a real cursor is
        needed: it advances past the rows this pass deliberately kept.
        """
        if filter.is_empty():
            raise ValueError(
                "delete_points called with an empty filter — this would wipe the entire "
                "collection. Populate at least one filter condition (e.g. virtualRecordId)."
            )
        if not filter.has_positive_match():
            raise ValueError(
                "delete_points called with only array-length conditions — a point "
                "whose field is absent satisfies those too, so this would delete "
                "most of the collection. Pair it with a value match "
                "(e.g. connectorIds)."
            )
        require_filter_org(filter)
        self._assert_connected()

        idx = self._index_name(collection_name)
        base_filter, raw_count_limits = split_values_count_conditions(filter)
        query = filter_expression_to_redis_query(base_filter)
        # Redis hash fields cannot contain dots, so a canonical key like
        # "metadata.x" is stored and echoed back as "metadata_x". Normalise once:
        # looking the value up under the canonical name misses, and a miss reads
        # as zero values, which silently widens the delete.
        count_limits = [
            (key.replace(".", "_"), limit) for key, limit in raw_count_limits
        ]

        if count_limits:
            total_deleted = await self._delete_filtered(
                collection_name, idx, query, count_limits
            )
        else:
            total_deleted = await self._delete_all_matching(collection_name, idx, query)

        logger.info(
            f"Deleted {total_deleted} points from Redis collection '{collection_name}'"
        )

    async def _delete_all_matching(
        self, collection_name: str, idx: str, query: str
    ) -> int:
        """Delete every document matching *query*.

        Re-queries from offset 0 after each page: the deleted page is gone, so
        what was page 2 is now page 1 and neither memory nor the offset grows
        with the size of the matching set.
        """
        total_deleted = 0
        while True:
            try:
                raw = await self.client.execute_command(  # type: ignore
                    "FT.SEARCH", idx, query,
                    "NOCONTENT",
                    "LIMIT", "0", str(_DELETE_PAGE_SIZE),
                    *_NO_TIMEOUT,
                )
            except Exception as e:
                # Do not swallow: callers treat a returned delete as complete and
                # go on to drop the VRID->doc mapping, which would leave the
                # surviving points orphaned *and* no longer discoverable.
                raise RuntimeError(
                    f"Paged delete on '{collection_name}' aborted after "
                    f"{total_deleted} point(s): FT.SEARCH failed: {e}"
                ) from e

            if not raw or not isinstance(raw, (list, tuple)):
                break
            page_keys = [
                k if isinstance(k, bytes) else str(k).encode()
                for k in raw[1:]  # skip the total-count at index 0
            ]
            if not page_keys:
                break

            await self.client.delete(*page_keys)  # type: ignore
            total_deleted += len(page_keys)
            # No short-page exit: only an empty re-query proves nothing is left.
        return total_deleted

    async def _delete_filtered(
        self, collection_name: str, idx: str, query: str, count_limits: list[tuple]
    ) -> int:
        """Delete matches of *query* that also satisfy the array-length limits.

        Array-length conditions have no RediSearch equivalent, so they are
        applied here and some matches deliberately survive. Survivors rule out
        the re-query-from-0 trick and force a forward cursor, which FT.SEARCH
        cannot give: its offset is capped by MAXSEARCHRESULTS, so a connector
        sharing that many chunks would strand every one of them. FT.AGGREGATE
        carries no such bound.
        """
        load = ["@__key"] + [f"@{key}" for key, _ in count_limits]
        try:
            raw = await self.client.execute_command(  # type: ignore
                "FT.AGGREGATE", idx, query,
                "LOAD", str(len(load)), *load,
                *_NO_TIMEOUT,
                "WITHCURSOR", "COUNT", str(_DELETE_PAGE_SIZE),
                "MAXIDLE", str(_CURSOR_MAX_IDLE_MS),
            )
        except Exception as e:
            logger.warning(
                f"FT.AGGREGATE cursor unavailable on '{collection_name}' ({e}); "
                f"falling back to offset paging, which cannot advance past "
                f"{_MAX_SEARCH_RESULTS} retained matches"
            )
            return await self._delete_filtered_paged(
                collection_name, idx, query, count_limits
            )

        total_deleted = 0
        cursor_id = 0
        try:
            while True:
                rows, cursor_id, unparsed = parse_aggregate_rows(raw)
                if unparsed:
                    # Unreadable rows are neither deleted nor retried; say so
                    # rather than let the walk look complete.
                    logger.warning(
                        f"{unparsed} unreadable row(s) on '{collection_name}' were "
                        f"skipped during a filtered delete"
                    )
                page_keys = [
                    key if isinstance(key, bytes) else str(key).encode()
                    for key, fields in rows
                    if within_values_count(fields, count_limits)
                ]
                if page_keys:
                    await self.client.delete(*page_keys)  # type: ignore
                    total_deleted += len(page_keys)
                if not cursor_id:
                    break
                raw = await self.client.execute_command(  # type: ignore
                    "FT.CURSOR", "READ", idx, str(cursor_id),
                    "COUNT", str(_DELETE_PAGE_SIZE),
                )
        except Exception as e:
            if cursor_id:
                with contextlib.suppress(Exception):
                    await self.client.execute_command(  # type: ignore
                        "FT.CURSOR", "DEL", idx, str(cursor_id)
                    )
            raise RuntimeError(
                f"Filtered delete on '{collection_name}' aborted after "
                f"{total_deleted} point(s): {e}"
            ) from e
        return total_deleted

    async def _delete_filtered_paged(
        self, collection_name: str, idx: str, query: str, count_limits: list[tuple]
    ) -> int:
        """Offset-paged fallback for a RediSearch without aggregate cursors.

        Bounded by MAXSEARCHRESULTS, so it raises rather than return short.
        """
        return_fields = [key for key, _ in count_limits]
        total_deleted = 0
        offset = 0

        while True:
            if offset + _DELETE_PAGE_SIZE > _MAX_SEARCH_RESULTS:
                # Retained rows mean the cursor cannot be reset to 0. Fail loudly
                # rather than return as if the delete were complete: the caller
                # would go on to drop the mapping rows for points still present.
                raise RuntimeError(
                    f"Paged delete on '{collection_name}' cannot continue past "
                    f"offset {offset}: FT.SEARCH is bounded by MAXSEARCHRESULTS "
                    f"({_MAX_SEARCH_RESULTS}) and {total_deleted} point(s) were "
                    f"deleted so far. Narrow the filter or raise MAXSEARCHRESULTS."
                )
            try:
                raw = await self.client.execute_command(  # type: ignore
                    "FT.SEARCH", idx, query,
                    "RETURN", str(len(return_fields)), *return_fields,
                    "LIMIT", str(offset), str(_DELETE_PAGE_SIZE),
                    *_NO_TIMEOUT,
                )
            except Exception as e:
                raise RuntimeError(
                    f"Paged delete on '{collection_name}' aborted after "
                    f"{total_deleted} point(s): FT.SEARCH failed: {e}"
                ) from e

            if not raw or not isinstance(raw, (list, tuple)):
                break
            rows, row_count = parse_search_rows(raw)
            if row_count == 0:
                break
            page_keys = [
                key if isinstance(key, bytes) else str(key).encode()
                for key, fields in rows
                if within_values_count(fields, count_limits)
            ]
            unparsed = row_count - len(rows)
            if unparsed:
                logger.warning(
                    f"{unparsed} unparseable row(s) on '{collection_name}' "
                    f"were skipped during a filtered delete"
                )
            if page_keys:
                await self.client.delete(*page_keys)  # type: ignore
                total_deleted += len(page_keys)

            offset += row_count - len(page_keys)  # step over the rows we kept
            if row_count < _DELETE_PAGE_SIZE:
                break
        return total_deleted


    async def overwrite_payload(
        self,
        collection_name: str,
        payload: dict,
        points: FilterExpression,
        refresh: bool = False,  # noqa: FBT001, FBT002 - IVectorDBService's signature
    ) -> None:
        if points.is_empty():
            raise ValueError(
                "overwrite_payload called with an empty filter — this would rewrite "
                "every point in the collection. Populate at least one filter "
                "condition (e.g. virtualRecordId)."
            )
        require_filter_org(points)
        keep_org_in_overwrite(payload, points)
        self._assert_connected()
        await self._hset_matching(collection_name, payload, points)

    async def set_payload(
        self,
        collection_name: str,
        payload: dict,
        filter: FilterExpression,  # noqa: A002 - IVectorDBService's parameter name
        refresh: bool = False,  # noqa: FBT001, FBT002 - IVectorDBService's signature
    ) -> None:
        if filter.is_empty():
            raise ValueError(
                "set_payload called with an empty filter — this would update the entire "
                "collection. Populate at least one filter condition (e.g. virtualRecordId)."
            )
        require_filter_org(filter)
        forbid_org_in_payload(payload)
        self._assert_connected()
        await self._hset_matching(collection_name, payload, filter)

    async def update_payload_by_ids(
        self,
        collection_name: str,
        point_ids: list[str],
        payload: dict,
        *,
        org_id: str,
    ) -> None:
        self._assert_connected()
        if is_missing(org_id):
            raise OrgRequired("update_payload_by_ids needs the org of the points")
        forbid_org_in_payload(payload)
        if not point_ids:
            return
        # HSET on a missing key would create a bare hash; only touch stored points of this org
        # (HGET is nil for a missing key and for another org's point alike).
        pipeline = self.client.pipeline(transaction=False)  # type: ignore
        for point_id in point_ids:
            pipeline.execute_command("HGET", self._key(collection_name, point_id), "metadata_orgId")
        owners = await pipeline.execute()
        keys = [
            self._key(collection_name, point_id)
            for point_id, owner in zip(point_ids, owners)
            if owner is not None and str(owner.decode() if isinstance(owner, bytes) else owner) == org_id
        ]
        await self._hset_keys(collection_name, payload, keys)

    generation_fence = True

    # One script is one atomic step on the server: the org and the fence are read and the hash written with no other command between.
    # A stored fence that is not a number is an error, not a skip or an overwrite. A missing key and another org's point are left alone (HSET would create a bare hash); a hash without a fence counts as the oldest.
    _FENCED_HSET = (
        "if redis.call('HGET', KEYS[1], 'metadata_orgId') ~= ARGV[2] then return 0 end "
        "local f = redis.call('HGET', KEYS[1], ARGV[3]) "
        "if f then if not tonumber(f) then return redis.error_reply('stored scopeFence is not a number') end "
        "if tonumber(f) > tonumber(ARGV[1]) then return 0 end end "
        "redis.call('HSET', KEYS[1], ARGV[3], ARGV[1], unpack(ARGV, 4)) return 1")

    async def update_payloads_by_ids(self, collection_name: str, updates: Mapping[str, dict], *, org_id: str, **extra: object) -> None:
        """With ``fence=n``: one pipeline of ``_FENCED_HSET`` scripts, one per point. Without a fence it is ``update_payload_by_ids`` for
        each distinct payload."""
        fence = extra.pop("fence", None)
        if fence is None:
            await super().update_payloads_by_ids(collection_name, updates, org_id=org_id, **extra)
            return
        fence = checked_fence(fence)
        self._assert_connected()
        if is_missing(org_id):
            raise OrgRequired("update_payload_by_ids needs the org of the points")
        for payload in updates.values():
            forbid_org_in_payload(payload)
            forbid_fence_in_payload(payload)
        if not updates:
            return
        pipeline = self.client.pipeline(transaction=False)  # type: ignore
        for point_id, payload in updates.items():
            flat: list[Any] = []
            for field, value in payload.items():
                flat += [field.replace(".", "_"), coerce_payload_hash_value(value)]
            pipeline.execute_command("EVAL", self._FENCED_HSET, 1, self._key(collection_name, point_id), int(fence), org_id,
                                     SCOPE_FENCE_FIELD, *flat)
        results = await pipeline.execute()
        failed = [r for r in results or [] if isinstance(r, Exception)]
        if failed:
            raise RuntimeError(f"Redis fenced payload write on '{collection_name}' had {len(failed)} failed command(s) of {len(updates)}. "
                               f"First error: {failed[0]}")

    async def _hset_matching(
        self,
        collection_name: str,
        payload: dict,
        filter_expr: FilterExpression,
    ) -> None:
        """Merge ``payload`` into every hash matching ``filter_expr``.

        Redis HSET is inherently a merge, so overwrite and set share one body.
        """
        keys = await self._keys_matching_filter(collection_name, filter_expr)
        await self._hset_keys(collection_name, payload, keys)

    async def _hset_keys(self, collection_name: str, payload: dict, keys: list[str]) -> None:
        """Per-command results are checked for the same reason upsert_points
        checks them: a pipeline reports failures per command rather than raising."""
        if not keys:
            return
        pipeline = self.client.pipeline(transaction=False)  # type: ignore
        for key in keys:
            flat: list[Any] = []
            for field, value in payload.items():
                # ``metadata.x`` → ``metadata_x`` hash-field convention.
                flat.append(field.replace(".", "_"))
                flat.append(coerce_payload_hash_value(value))
            if flat:
                pipeline.execute_command("HSET", key, *flat)
        results = await pipeline.execute()
        failed = [i for i, r in enumerate(results or []) if isinstance(r, Exception)]
        if failed:
            raise RuntimeError(
                f"Redis payload write on '{collection_name}' had {len(failed)} "
                f"failed HSET command(s) of {len(keys)}. "
                f"First error: {results[failed[0]]}"
            )

    async def count_points(self, collection_name: str, count_filter: FilterExpression) -> int:
        self._assert_connected()
        counted = await self.client.execute_command(  # type: ignore
            "FT.SEARCH", self._index_name(collection_name), filter_expression_to_redis_query(count_filter), "NOCONTENT", "LIMIT", "0", "0", *_NO_TIMEOUT,
        )
        if not isinstance(counted, (list, tuple)) or not counted:
            raise PartialResult(f"{collection_name}: unexpected count reply {counted!r}")
        return int(counted[0])

    async def scroll(
        self,
        collection_name: str,
        scroll_filter: FilterExpression,
        limit: int,
        offset: str | None = None,
        with_payload: list[str] | None = None,
    ) -> ScrollResult:
        """Return one page of points.

        ``offset`` is the opaque integer cursor returned in the previous
        ``ScrollResult.next_offset``.  Pass ``None`` (or ``"0"``) for the
        first page.

        ``with_payload`` maps to an ``FT.SEARCH RETURN`` clause. Dots become
        underscores because Redis hash field names cannot contain them.
        """
        self._assert_connected()
        query = filter_expression_to_redis_query(scroll_filter)
        idx = self._index_name(collection_name)

        int_offset = 0
        if offset is not None:
            try:
                int_offset = int(offset)
            except (ValueError, TypeError):
                int_offset = 0

        # RediSearch refuses offsets past MAXSEARCHRESULTS (10k by default).
        # Refused here, not on the page before: that page is still returned,
        # with a next_offset, so the caller sees every point the window holds
        # and cannot mistake the ceiling for the end of the scroll.
        if int_offset >= _MAX_SEARCH_RESULTS:
            raise ScrollWindowExceededError(
                f"Scroll of '{collection_name}' reached the RediSearch result "
                f"ceiling at offset {int_offset} (MAXSEARCHRESULTS). Narrow the "
                f"filter or raise MAXSEARCHRESULTS; continuing would silently "
                f"drop the remainder."
            )

        # With ON HASH and no RETURN/NOCONTENT clause, FT.SEARCH returns all
        # hash fields for each matching document.  dense_embedding is skipped
        # by decode_hash_doc since it's a binary blob with no payload value.
        return_fields = (
            [key.replace(".", "_") for key in with_payload] if with_payload else []
        )
        if return_fields:
            raw = await self.client.execute_command(  # type: ignore
                "FT.SEARCH", idx, query,
                "RETURN", str(len(return_fields)), *return_fields,
                "LIMIT", str(int_offset), str(limit),
                *_NO_TIMEOUT,
            )
        else:
            raw = await self.client.execute_command(  # type: ignore
                "FT.SEARCH", idx, query,
                "LIMIT", str(int_offset), str(limit),
                *_NO_TIMEOUT,
            )

        points: list[VectorPoint] = []
        total_count = 0
        returned_docs = 0
        if raw and isinstance(raw, (list, tuple)) and len(raw) > 1:
            try:
                total_count = int(raw[0])
            except (TypeError, ValueError):
                total_count = 0

            items = list(raw[1:])
            i = 0
            while i < len(items) - 1:
                key = items[i]
                fields_list = items[i + 1]
                i += 2
                returned_docs += 1
                if not isinstance(fields_list, (list, tuple)):
                    continue
                try:
                    from app.services.vector_db.redis.utils import _parse_fields_list
                    doc = decode_hash_doc(_parse_fields_list(fields_list))
                    point_id = (
                        key.decode() if isinstance(key, bytes) else key
                    ).split(":")[-1]
                    points.append(
                        VectorPoint(
                            id=point_id,
                            payload=hash_doc_to_payload(doc),
                        )
                    )
                except Exception:
                    pass

        # LIMIT offset is the result-set position Redis already walked, not
        # how many documents we managed to decode.
        next_page_start = int_offset + returned_docs
        more_available = next_page_start < total_count

        next_offset: str | None = str(next_page_start) if more_available else None
        return ScrollResult(points=points, next_offset=next_offset)

    def scroll_offset_after_delete(
        self, next_offset: str | None, deleted: int,
    ) -> str | None:
        # next_offset is an FT.SEARCH LIMIT position; deleting points already
        # read moves every later match that many places earlier.
        if next_offset is None or deleted <= 0:
            return next_offset
        try:
            return str(max(0, int(next_offset) - deleted))
        except (TypeError, ValueError):
            return next_offset

    async def retrieve_points(
        self,
        collection_name: str,
        ids: list[str],
    ) -> list[VectorPoint]:
        self._assert_connected()
        if not ids:
            return []
        pipeline = self.client.pipeline(transaction=False)  # type: ignore
        for point_id in ids:
            pipeline.execute_command("HGETALL", self._key(collection_name, point_id))
        replies = await pipeline.execute()
        points: list[VectorPoint] = []
        for point_id, reply in zip(ids, replies):
            if isinstance(reply, Exception):
                raise reply
            doc = decode_hash_doc(reply)
            if doc:
                points.append(VectorPoint(id=point_id, payload=hash_doc_to_payload(doc)))
        return points

    async def existing_point_ids(self, collection_name: str, ids: list[str]) -> set[str]:
        self._assert_connected()
        if not ids:
            return set()
        pipeline = self.client.pipeline(transaction=False)  # type: ignore
        for point_id in ids:
            pipeline.execute_command("EXISTS", self._key(collection_name, point_id))
        replies = await pipeline.execute()
        found: set[str] = set()
        for point_id, reply in zip(ids, replies):
            if isinstance(reply, Exception):
                raise reply
            if reply:
                found.add(point_id)
        return found

    async def query_nearest_points(
        self,
        collection_name: str,
        requests: list[HybridSearchRequest],
    ) -> list[list[SearchResult]]:
        self._assert_connected()
        idx = self._index_name(collection_name)
        # Bound the fan-out: one request per agent source means a wide query
        # would otherwise issue unbounded concurrent FT.HYBRID commands.
        semaphore = asyncio.Semaphore(self._max_concurrent_searches)

        async def _bounded(req: HybridSearchRequest) -> list[SearchResult]:
            async with semaphore:
                return await self._run_single_hybrid_query(idx, collection_name, req)

        return list(await asyncio.gather(*[_bounded(r) for r in requests]))

    # ------------------------------------------------------------------
    # Internal query helpers
    # ------------------------------------------------------------------

    async def _run_single_hybrid_query(
        self,
        idx: str,
        collection_name: str,
        req: HybridSearchRequest,
    ) -> list[SearchResult]:
        """One search; with a scope filter, until ``req.limit`` points pass the exact rule or the candidates run out.

        The query is already narrowed by the scope filter, but a tag query compares without case (and the connectors it excludes were read just
        before): a point it returns that the exact rule refuses is dropped (:meth:`_exact_scope`). Dropping after the limit was applied would
        leave fewer than ``limit`` points although the next candidates are valid, so a search that lost points asks for twice as many, up to
        ``MAX_SCOPE_CANDIDATES``, and keeps the first ``limit`` that pass."""
        if req.scope_filter is None:
            return await self._search_once(idx, collection_name, req, req.limit)
        asked = req.limit
        while True:
            found = await self._search_once(idx, collection_name, req, asked)
            kept = self._exact_scope(req, found, count=False)
            if len(kept) >= req.limit or len(found) < asked or asked >= MAX_SCOPE_CANDIDATES:
                if len(kept) != len(found):      # counted once, on the answer that is returned, not on every widening
                    SCOPE_PREFILTER_DROPPED.inc("redis", value=len(found) - len(kept))
                if len(kept) < req.limit and len(found) >= asked:      # the candidates ran into the cap, not out: an answer that may be short
                    SCOPE_PREFILTER_CAPPED.inc("redis")
                    logger.warning("scoped Redis search asked for %d candidates, %d passed the exact rule, %d wanted: the answer may be short",
                                   asked, len(kept), req.limit)
                return kept[: req.limit]
            asked = min(asked * 2, MAX_SCOPE_CANDIDATES)

    async def _search_once(
        self,
        idx: str,
        collection_name: str,
        req: HybridSearchRequest,
        limit: int,
    ) -> list[SearchResult]:
        """Execute one FT.HYBRID query for ``limit`` points and return SearchResult list.

        When ``dense_query`` is None the caller expects text-only search
        (e.g. no embedding model configured).  All other errors propagate so
        callers are aware of real failures rather than silently getting empty
        results.
        """
        if self.client is None:
            return []

        filter_query = ""
        if req.filter is not None and not req.filter.is_empty():
            filter_query = filter_expression_to_redis_query(req.filter)
        if req.scope_filter is not None:
            scope = await self._scope_query(idx, req.scope_filter)
            filter_query = scope if filter_query in ("", "*") else f"({filter_query}) {scope}"      # ANDed with the request's own filter, never in its place

        text_query = redisearch_any_term_query(req.text_query or "")
        search_query = _combine_text_and_filter(text_query, filter_query)

        if req.dense_query is None:
            # Dense vector required for FT.HYBRID; use text-only FT.SEARCH
            return await self._text_only_search(idx, search_query, limit)

        vec_bytes = vector_to_bytes(req.dense_query, self._dense_dtype)
        k = limit
        window = max(k * 2, 20)

        # FT.HYBRID <idx>
        #   SEARCH <text+filter query>            ← lexical BM25 leg + filter
        #   VSIM @dense_embedding $vec
        #       KNN 2 K <window>                  ← 2 = arg-count for "K <value>"
        #       [FILTER <tag_query>]               ← pre-filter on the KNN leg
        #   COMBINE RRF 4 WINDOW <w> CONSTANT 60  ← 4 = arg-count for the 2 pairs
        #   LIMIT 0 <k>
        #   LOAD 2 @__key @__score                ← key + RRF score only; full doc
        #                                            fetched via pipelined HGETALL
        #   PARAMS 2 vec <bytes>                  ← bare name "vec"; "$vec" is only
        #                                            the query-syntax reference form
        #
        # We use LOAD 2 @__key @__score (minimal) + a pipelined HGETALL per key
        # instead of enumerating all possible metadata_* fields in LOAD.  This
        # avoids brittleness when new metadata fields are added and costs only one
        # extra round-trip (pipelined, so no per-result latency).
        cmd: list[Any] = [
            "FT.HYBRID", idx,
            "SEARCH", search_query,
            "VSIM", "@dense_embedding", "$vec",
            "KNN", "2", "K", str(window),
        ]
        # Apply the same filter to the KNN leg so org/tenant isolation is enforced
        # on both the lexical and vector branches.
        if filter_query and filter_query != "*":
            cmd += ["FILTER", filter_query]
        cmd += [
            "COMBINE", "RRF", "4", "WINDOW", str(window), "CONSTANT", "60",
            "LIMIT", "0", str(k),
            "LOAD", "2", "@__key", "@__score",
            "PARAMS", "2", "vec", vec_bytes,
        ]

        # FT.HYBRID errors propagate — do NOT silently fall back to text-only.
        raw = await self.client.execute_command(*cmd)  # type: ignore
        key_score_pairs = parse_ft_hybrid_reply(raw)

        if not key_score_pairs:
            return []

        if not req.with_payload and req.scope_filter is None:
            return [
                SearchResult(
                    id=key.rsplit(":", 1)[-1] if ":" in key else key,
                    score=score,
                )
                for key, score in key_score_pairs
            ]

        # Pipeline HGETALL for each key to retrieve full document payload.
        # All fields including dynamic metadata_* keys are returned in one shot.
        pipeline = self.client.pipeline(transaction=False)  # type: ignore
        for key, _ in key_score_pairs:
            pipeline.hgetall(key)
        hash_docs = await pipeline.execute()

        results: list[SearchResult] = []
        for (key, score), raw_doc in zip(key_score_pairs, hash_docs or []):
            point_id = key.rsplit(":", 1)[-1] if ":" in key else key
            doc = decode_hash_doc(raw_doc)
            payload = hash_doc_to_payload(doc)
            results.append(SearchResult(id=point_id, score=score, payload=payload))

        return results

    @staticmethod
    def _exact_scope(req: HybridSearchRequest, results: list[SearchResult], *, count: bool = True) -> list[SearchResult]:
        """What the scope filter admits, decided again on the points themselves with the exact rule (case-sensitive ids, the connectors the point
        holds now). The query already narrowed the search, but a tag query compares without case and the connectors it excludes were read just
        before the search: a point it let through that the rule refuses is dropped and counted, so the answer never holds more than the rule admits."""
        if req.scope_filter is None:
            return results
        node = sx.admits(req.scope_filter)
        kept = [r for r in results if sx.evaluate(node, r.payload)]
        if count and len(kept) != len(results):
            SCOPE_PREFILTER_DROPPED.inc("redis", value=len(results) - len(kept))
        return kept if req.with_payload else [replace(r, payload={}) for r in kept]

    async def _scope_query(self, idx: str, flt: ScopeFilter) -> str:
        """The scope filter as an FT query. "Every connector is current" needs the connectors the index holds that are not (RediSearch can only
        exclude values): they are read with ``FT.TAGVALS`` just before the search. A connector that first appears in between could be missed; the
        post-check still applies."""
        sx.check_size(flt, "Redis")
        refuse_unsafe_ids(flt)
        node = sx.admits(flt)
        # With a case-sensitive ``pathIds`` the query carries the excluded and pending scopes. Without it they are left out and enforced by the exact
        # check on the points the query returns: a case-insensitive tag query would drop a point that stores ``X`` for an excluded ``x``. Blocked
        # connectors are always left to the exact check (``connectorIds`` is not case-sensitive in the indexes that exist).
        # shortcut: on an index with case-insensitive tags, blocked (always) and pending (and excluded) are filtered after top-k, a recall cost the
        # widening loop covers; it goes away when the index is rebuilt with CASESENSITIVE tags (blocked stays: connectorIds is not case-sensitive)
        leave_out = [sx.Has(CONNECTOR_IDS_FIELD, tuple(sorted(flt.blocked)))]
        if PATH_IDS_FIELD not in await self._case_sensitive_fields(idx):      # an index from before the scope tags were case-sensitive
            leave_out += [sx.Has(PATH_IDS_FIELD, tuple(sorted(flt.excluded))), sx.Has(PATH_IDS_FIELD, tuple(sorted(flt.pending)))]
        node = sx.relax(node, leave_out)
        outside: dict[str, tuple[str, ...]] = {}
        if node != sx.FALSE:
            held = await self.client.execute_command("FT.TAGVALS", idx, CONNECTOR_IDS_FIELD)  # type: ignore
            stale = tuple(sorted({v.decode() if isinstance(v, bytes) else str(v) for v in held} - set(flt.current)))
            if len(stale) > sx.MAX_IDS["Redis"]:
                raise ScopeFilterTooLarge(f"Redis: the index holds more than {sx.MAX_IDS['Redis']} connectors outside the filter")
            outside[CONNECTOR_IDS_FIELD] = stale
        return scope_query(node, outside)

    async def _case_sensitive_fields(self, idx: str) -> frozenset[str]:
        """The TAG fields of the index declared ``CASESENSITIVE`` (read from ``FT.INFO``, kept for a minute; unreadable means none)."""
        cache: dict[str, tuple[float, frozenset[str]]] = self.__dict__.setdefault("_case_fields", {})
        hit = cache.get(idx)
        now = time.monotonic()
        if hit is not None and hit[0] > now:
            return hit[1]
        found: set[str] = set()
        try:
            attributes = _parse_ft_info(await self.client.execute_command("FT.INFO", idx)).get("attributes") or []  # type: ignore
            for attribute in attributes:
                tokens = [t.decode() if isinstance(t, bytes) else str(t) for t in attribute]
                if "CASESENSITIVE" in tokens and "identifier" in tokens:
                    found.add(tokens[tokens.index("identifier") + 1])
        except Exception:
            found = set()
        cache[idx] = (now + 60.0, frozenset(found))
        return cache[idx][1]

    async def _text_only_search(
        self, idx: str, query: str, limit: int
    ) -> list[SearchResult]:
        """Text-only FT.SEARCH — used only when no dense query is provided.

        With ON HASH and no RETURN clause, all hash fields are returned by
        default. ``parse_ft_search_reply`` handles the flat field list and
        ignores the binary ``dense_embedding`` blob via ``decode_hash_doc``.
        """
        raw = await self.client.execute_command(  # type: ignore
            "FT.SEARCH", idx, query or "*",
            "LIMIT", "0", str(limit),
        )
        return parse_ft_search_reply(raw)

    async def _keys_matching_filter(
        self, collection_name: str, filter_expr: FilterExpression
    ) -> list[bytes]:
        """Return all Redis keys in a collection matching a filter expression, waiting briefly for an index
        still settling after a write: a scan that finds fewer than it counted is read again, and raises only
        when it stays short. Callers such as the scope back-fill do not retry, so a short scan would cost them
        the record."""
        delay = _SETTLE_DELAY_SECONDS
        for attempt in range(_SETTLE_ATTEMPTS):
            keys, total = await self._scan_matching_keys(collection_name, filter_expr)
            if len(keys) >= total:
                return keys
            if attempt + 1 < _SETTLE_ATTEMPTS:
                await asyncio.sleep(delay)
                delay *= 2
        raise RuntimeError(f"Key lookup on '{collection_name}' returned {len(keys)} of {total} counted key(s)")

    async def _scan_matching_keys(
        self, collection_name: str, filter_expr: FilterExpression
    ) -> tuple[list[bytes], int]:
        """The keys a scan finds and the number a count query reported for the same filter.

        Read through an aggregate cursor, not ``FT.SEARCH ... LIMIT offset``: an unsorted offset page is not
        stable across calls (documents still settling in the index move between pages), so a page-by-page
        scan repeats some keys and misses others, and a payload write would then skip the missed points.
        A cursor can still repeat a key, so keys are de-duplicated, and a scan that ends with fewer distinct
        keys than a count query reported for the same filter raises instead of answering with a part. The
        count is a separate ``FT.SEARCH ... LIMIT 0 0``: the first integer of an aggregate reply is not
        defined as the number of matches. Documents may change between the two calls, so only a scan that
        finds fewer than counted is short.
        """
        idx = self._index_name(collection_name)
        query = filter_expression_to_redis_query(filter_expr)

        found: dict[bytes, None] = {}
        total = 0
        cursor = 0
        try:
            counted = await self.client.execute_command(  # type: ignore
                "FT.SEARCH", idx, query, "NOCONTENT", "LIMIT", "0", "0", *_NO_TIMEOUT,
            )
            if not isinstance(counted, (list, tuple)) or not counted:
                raise RuntimeError(f"unexpected count reply {counted!r}")
            total = int(counted[0])
            reply = await self.client.execute_command(  # type: ignore
                "FT.AGGREGATE", idx, query, "LOAD", "1", "@__key",
                "WITHCURSOR", "COUNT", "1000", *_NO_TIMEOUT,
            )
            while True:
                if not isinstance(reply, (list, tuple)) or len(reply) != 2:
                    raise RuntimeError(f"unexpected reply {reply!r}")
                rows, cursor = reply
                for row in rows[1:]:
                    fields = dict(zip(row[::2], row[1::2]))
                    key = fields.get(b"__key") or fields.get("__key")
                    if key is not None:
                        found[key if isinstance(key, bytes) else key.encode()] = None
                if not cursor:
                    break
                reply = await self.client.execute_command(  # type: ignore
                    "FT.CURSOR", "READ", idx, cursor, "COUNT", "1000",
                )
        except asyncio.CancelledError:
            # Not an Exception: a cancelled scan would leave its cursor open until the server expires it,
            # and cursors are limited per shard. The delete itself is shielded from the cancellation.
            if cursor:
                with contextlib.suppress(Exception):
                    await asyncio.shield(self.client.execute_command("FT.CURSOR", "DEL", idx, cursor))  # type: ignore
            raise
        except Exception as e:
            if cursor:
                with contextlib.suppress(Exception):  # best effort; the cursor expires on its own
                    await self.client.execute_command("FT.CURSOR", "DEL", idx, cursor)  # type: ignore
            # A partial key list would silently update only some of a VRID's
            # chunks while reporting success.
            raise RuntimeError(
                f"Key lookup on '{collection_name}' failed after {len(found)} key(s): {e}"
            ) from e

        return list(found), total

    # ------------------------------------------------------------------
    # Internal guards
    # ------------------------------------------------------------------

    def _assert_connected(self) -> None:
        if self.client is None:
            raise RuntimeError("Redis client not connected. Call connect() first.")


# ------------------------------------------------------------------
# Module-level helpers
# ------------------------------------------------------------------

def _build_generic_conditions(filters: dict[str, FilterValue]) -> list[FieldCondition]:
    """Build generic FieldCondition list from a key→value dict.
    Redis uses ``metadata_x`` (underscore) as field names in the index.
    We store the canonical ``metadata.x`` key here; conversion to Redis
    query syntax happens in utils.field_conditions_to_redis_query.
    """
    return build_generic_conditions(filters)


def _combine_text_and_filter(text_query: str, filter_query: str) -> str:
    """Combine a free-text query and a tag/filter query into one FT query string."""
    parts = []
    if text_query and text_query.strip():
        parts.append(text_query.strip())
    if filter_query and filter_query.strip() and filter_query.strip() != "*":
        parts.append(filter_query.strip())
    return " ".join(parts) if parts else "*"


def _check_version(version_str: str) -> tuple[bool, str]:
    """Return (ok, message) checking Redis >= 8.4."""
    try:
        m = re.match(r"(\d+)\.(\d+)", version_str)
        if not m:
            return True, ""  # Cannot determine; let it through
        major, minor = int(m.group(1)), int(m.group(2))
        if (major, minor) < (_MIN_REDIS_MAJOR, _MIN_REDIS_MINOR):
            return (
                False,
                f"Redis {version_str} is too old; FT.HYBRID requires >= "
                f"{_MIN_REDIS_MAJOR}.{_MIN_REDIS_MINOR}",
            )
        return True, ""
    except Exception:
        return True, ""


def _parse_ft_info(raw: Any) -> dict:  # noqa: ANN401
    """Parse FT.INFO flat-list reply into a dict."""
    info: dict = {}
    if not isinstance(raw, (list, tuple)):
        return info
    items = list(raw)
    i = 0
    while i < len(items) - 1:
        key = items[i]
        val = items[i + 1]
        if isinstance(key, bytes):
            key = key.decode(errors="replace")
        info[key] = val
        i += 2
    return info


def _extract_dim_from_ft_info_attributes(attributes: Any) -> int | None:  # noqa: ANN401
    """Extract the DIM value for the ``dense_embedding`` VECTOR field from
    the ``attributes`` entry of an FT.INFO reply.

    FT.INFO ``attributes`` is a list of nested flat lists, e.g.::

        [[b'identifier', b'dense_embedding', b'attribute', b'dense_embedding',
          b'type', b'VECTOR', b'ALGORITHM', b'HNSW', b'data_type', b'FLOAT16',
          b'dim', b'1024', b'distance_metric', b'COSINE']]

    We locate the entry whose ``identifier`` is ``dense_embedding`` and
    extract the ``dim`` field from the same flat list.

    Note: ON HASH indexes use the bare field name (``dense_embedding``) as the
    identifier, not a JSONPath (``$.dense_embedding``) as in ON JSON indexes.
    """
    if not isinstance(attributes, (list, tuple)):
        return None
    for attr_entry in attributes:
        if not isinstance(attr_entry, (list, tuple)):
            continue
        items = list(attr_entry)
        entry: dict[str, Any] = {}
        i = 0
        while i < len(items) - 1:
            k = items[i]
            v = items[i + 1]
            if isinstance(k, bytes):
                k = k.decode(errors="replace")
            if isinstance(v, bytes):
                v = v.decode(errors="replace")
            entry[k.lower()] = v
            i += 2
        identifier = entry.get("identifier", "")
        if identifier == "dense_embedding":
            dim_val = entry.get("dim")
            if dim_val is not None:
                try:
                    return int(dim_val)
                except (ValueError, TypeError):
                    pass
    return None
