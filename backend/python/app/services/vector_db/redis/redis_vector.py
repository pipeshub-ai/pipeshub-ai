"""Redis vector database provider.

Uses Redis >= 8.4 with the Redis Query Engine (RediSearch module built-in).
Hybrid search via ``FT.HYBRID`` combines:
  - Dense KNN leg   (HNSW index on the ``dense_embedding`` field)
  - Lexical BM25 leg (TEXT index on ``page_content``)
  - Fused by native RRF inside Redis.

Storage layout
--------------
Each point is stored as a Redis JSON document at key::

    {collection_name}:{point_id}

The Search index is named::

    {collection_name}_idx

Multiple collections on one Redis instance are isolated by key-prefix / index.
Future tenant separation adds a ``{tenant}_{collection}`` prefix via CollectionResolver.

Minimum Redis version: 8.4 (FT.HYBRID command).  Health check enforces this.
"""

import asyncio
import json
import re
import time
from typing import Any, Dict, List, Optional, Union

import redis.asyncio as aioredis

from app.config.configuration_service import ConfigurationService
from app.config.constants.service import config_node_constants
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
    ScrollResult,
    SearchResult,
    VectorCollectionInfo,
    VectorDBCapabilities,
    VectorDBHealth,
    VectorPoint,
)
from app.services.vector_db.redis.config import RedisVectorConfig
from app.services.vector_db.redis.utils import (
    escape_redisearch_text,
    escape_tag_value,
    field_conditions_to_redis_query,
    filter_expression_to_redis_query,
    parse_ft_hybrid_reply,
    parse_ft_search_reply,
    vector_point_to_json_doc,
    vector_to_bytes,
)
from app.utils.logger import create_logger

logger = create_logger("redis_vector_service")

_REDIS_CAPABILITIES = VectorDBCapabilities(
    supports_sparse_vectors=False,
    supports_server_side_text_search=True,
    supported_fusion_methods=[FusionMethod.RRF],
)

# Minimum Redis version required for FT.HYBRID
_MIN_REDIS_MAJOR = 8
_MIN_REDIS_MINOR = 4

# Distance type mapping (Redis HNSW supports COSINE, L2, IP)
_DIST_MAP = {
    DistanceMetric.COSINE: "COSINE",
    DistanceMetric.L2: "L2",
    DistanceMetric.DOT_PRODUCT: "IP",
}


class RedisVectorService(IVectorDBService):
    """Redis >= 8.4 vector DB provider implementing IVectorDBService."""

    def __init__(
        self,
        config_service: ConfigurationService | RedisVectorConfig,
    ) -> None:
        self.config_service = config_service
        self.client: Optional[aioredis.Redis] = None
        # Cached collection metadata: name → CollectionConfig used at creation
        self._collection_configs: Dict[str, CollectionConfig] = {}

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
            self.client = aioredis.Redis(
                host=cfg.host,
                port=cfg.port,
                password=cfg.password,
                db=cfg.db,
                socket_timeout=cfg.timeout,
                socket_connect_timeout=cfg.timeout,
                decode_responses=False,  # we handle bytes ourselves for vectors
            )
            # Verify connectivity
            await self.client.ping()
            logger.info(f"Connected to Redis vector store at {cfg.host}:{cfg.port}")
        except Exception as e:
            logger.error(f"Failed to connect to Redis: {e}")
            raise

    async def disconnect(self) -> None:
        if self.client is not None:
            try:
                await self.client.aclose()
                logger.info("Disconnected from Redis vector store")
            except Exception as e:
                logger.warning(f"Error during Redis disconnect: {e}")
            finally:
                self.client = None

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

    async def health_check(self) -> VectorDBHealth:
        start = time.monotonic()
        if self.client is None:
            return VectorDBHealth(status=HealthStatus.UNHEALTHY, message="Not connected")
        try:
            await self.client.ping()
            # Fetch server version.  With decode_responses=False the client returns
            # bytes; we must decode the version string before comparing.
            info_bytes = await self.client.info("server")
            version_str: Optional[str] = None
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
        config: Optional[CollectionConfig] = None,
    ) -> None:
        self._assert_connected()
        if config is None:
            config = CollectionConfig()

        dist = _DIST_MAP.get(config.distance_metric, "COSINE")
        idx = self._index_name(collection_name)

        # Idempotency: check if the index already exists with the correct dimension.
        existing_info = await self.get_collection_info(collection_name)
        if existing_info.exists:
            if existing_info.dense_dimension == config.embedding_size:
                logger.info(
                    f"Redis index '{idx}' already exists with correct dim={config.embedding_size}; skipping."
                )
                self._collection_configs[collection_name] = config
                return
            # Dimension mismatch — drop and recreate
            logger.warning(
                f"Redis index '{idx}' has dim={existing_info.dense_dimension}, "
                f"expected {config.embedding_size} — recreating."
            )
            try:
                await self.client.execute_command("FT.DROPINDEX", idx, "DD")  # type: ignore
            except Exception:
                pass

        self._collection_configs[collection_name] = config

        # FT.CREATE schema:
        #   page_content  TEXT  (BM25 lexical leg)
        #   dense_embedding VECTOR HNSW 6 FLOAT32 (dense KNN leg)
        #   metadata_orgId TAG
        #   metadata_virtualRecordId TAG
        cmd = [
            "FT.CREATE", idx,
            "ON", "JSON",
            "PREFIX", "1", f"{collection_name}:",
            "SCHEMA",
            "$.page_content", "AS", "page_content", "TEXT",
            "$.dense_embedding", "AS", "dense_embedding", "VECTOR",
            "HNSW", "6",
            "TYPE", "FLOAT32",
            "DIM", str(config.embedding_size),
            "DISTANCE_METRIC", dist,
            "$.metadata_orgId", "AS", "metadata_orgId", "TAG",
            "$.metadata_virtualRecordId", "AS", "metadata_virtualRecordId", "TAG",
        ]
        await self.client.execute_command(*cmd)  # type: ignore
        logger.info(f"Created Redis collection/index '{idx}' (dim={config.embedding_size})")

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
            dim: Optional[int] = None
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
        except Exception:
            return VectorCollectionInfo(name=collection_name, exists=False)

    async def collection_exists(self, collection_name: str) -> bool:
        info = await self.get_collection_info(collection_name)
        return info.exists

    async def delete_collection(self, collection_name: str) -> None:
        self._assert_connected()
        idx = self._index_name(collection_name)
        # Drop index (DD = also delete documents)
        try:
            await self.client.execute_command("FT.DROPINDEX", idx, "DD")  # type: ignore
        except Exception:
            pass
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
        We convert ``metadata.x`` → ``metadata_x`` for the Redis field alias.
        """
        self._assert_connected()
        idx = self._index_name(collection_name)
        redis_alias = field_name.replace(".", "_")
        json_path = f"$.{field_name.replace('.', '_')}"  # stored flat
        field_type = "TAG" if field_schema.get("type") == "keyword" else "TEXT"
        try:
            await self.client.execute_command(  # type: ignore
                "FT.ALTER", idx,
                "SCHEMA", "ADD",
                json_path, "AS", redis_alias, field_type,
            )
        except Exception as e:
            # Ignore "already exists" errors
            if "already exists" not in str(e).lower():
                logger.warning(f"FT.ALTER failed for field '{field_name}': {e}")

    # ------------------------------------------------------------------
    # Filter construction
    # ------------------------------------------------------------------

    async def filter_collection(
        self,
        filter_mode: Union[str, FilterMode] = FilterMode.MUST,
        must: Optional[Dict[str, FilterValue]] = None,
        should: Optional[Dict[str, FilterValue]] = None,
        must_not: Optional[Dict[str, FilterValue]] = None,
        min_should_match: Optional[int] = None,
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
            extra_kwargs=kwargs or None,
            build_conditions=_build_generic_conditions,
        )

    # ------------------------------------------------------------------
    # Data operations — all async
    # ------------------------------------------------------------------

    async def upsert_points(
        self,
        collection_name: str,
        points: List[VectorPoint],
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
                doc = vector_point_to_json_doc(point)
                pipeline.execute_command("JSON.SET", key, "$", json.dumps(doc))
            results = await pipeline.execute()
            # Check for per-command errors in the pipeline result
            failed = [
                i for i, r in enumerate(results or [])
                if isinstance(r, Exception) or r is False
            ]
            if failed:
                raise RuntimeError(
                    f"Redis pipeline upsert had {len(failed)} failed JSON.SET command(s) "
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
        filter: FilterExpression,
    ) -> None:
        """Delete points matching *filter* using paged deletes.

        Re-queries from offset 0 after each page is deleted to avoid the
        stale-offset problem: FT.SEARCH offsets shift when keys are removed.
        The max number of keys held in memory at once == page_size (not the
        entire matching set), which keeps memory bounded for large collections.
        """
        if filter.is_empty():
            raise ValueError(
                "delete_points called with an empty filter — this would wipe the entire "
                "collection. Populate at least one filter condition (e.g. virtualRecordId)."
            )
        self._assert_connected()

        idx = self._index_name(collection_name)
        query = filter_expression_to_redis_query(filter)
        page_size = 500
        total_deleted = 0

        while True:
            # Always fetch from offset 0: after deleting the previous page,
            # what was page 2 is now page 1.
            try:
                raw = await self.client.execute_command(  # type: ignore
                    "FT.SEARCH", idx, query,
                    "NOCONTENT",
                    "LIMIT", "0", str(page_size),
                )
            except Exception as e:
                logger.warning(f"FT.SEARCH during paged delete failed: {e}")
                break

            if not raw or not isinstance(raw, (list, tuple)):
                break
            page_keys = [
                k if isinstance(k, bytes) else k.encode()
                for k in raw[1:]  # skip total-count at index 0
            ]
            if not page_keys:
                break

            await self.client.delete(*page_keys)  # type: ignore
            total_deleted += len(page_keys)

            # If we got fewer than a full page, we're done
            if len(page_keys) < page_size:
                break

        logger.info(
            f"Deleted {total_deleted} points from Redis collection '{collection_name}'"
        )

    async def overwrite_payload(
        self,
        collection_name: str,
        payload: dict,
        points: FilterExpression,
    ) -> None:
        self._assert_connected()
        keys = await self._keys_matching_filter(collection_name, points)
        pipeline = self.client.pipeline(transaction=False)  # type: ignore
        for key in keys:
            for field, value in payload.items():
                json_path = f"$.{field}"
                pipeline.execute_command("JSON.SET", key, json_path, json.dumps(value))
        if keys:
            await pipeline.execute()

    async def scroll(
        self,
        collection_name: str,
        scroll_filter: FilterExpression,
        limit: int,
        offset: Optional[str] = None,
    ) -> ScrollResult:
        """Return one page of points.

        ``offset`` is the opaque integer cursor returned in the previous
        ``ScrollResult.next_offset``.  Pass ``None`` (or ``"0"``) for the
        first page.
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

        raw = await self.client.execute_command(  # type: ignore
            "FT.SEARCH", idx, query,
            "LIMIT", str(int_offset), str(limit),
            "RETURN", "1", "$",
        )

        points: List[VectorPoint] = []
        total_count = 0
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
                if not isinstance(fields_list, (list, tuple)):
                    continue
                fields: Dict = {}
                fi = 0
                while fi < len(fields_list) - 1:
                    fname = fields_list[fi]
                    if isinstance(fname, bytes):
                        fname = fname.decode(errors="replace")
                    fields[fname] = fields_list[fi + 1]
                    fi += 2
                json_blob = fields.get("$")
                if json_blob:
                    try:
                        if isinstance(json_blob, bytes):
                            json_blob = json_blob.decode()
                        doc = json.loads(json_blob)
                        point_id = (
                            key.decode() if isinstance(key, bytes) else key
                        ).split(":")[-1]
                        points.append(
                            VectorPoint(
                                id=point_id,
                                payload={
                                    "page_content": doc.get("page_content", ""),
                                    "metadata": doc.get("metadata", {}),
                                },
                            )
                        )
                    except Exception:
                        pass

        # Return a next_offset cursor when there are more results
        next_page_start = int_offset + len(points)
        next_offset: Optional[str] = (
            str(next_page_start) if next_page_start < total_count else None
        )

        return ScrollResult(points=points, next_offset=next_offset)

    async def query_nearest_points(
        self,
        collection_name: str,
        requests: List[HybridSearchRequest],
    ) -> List[List[SearchResult]]:
        self._assert_connected()
        idx = self._index_name(collection_name)
        tasks = [
            self._run_single_hybrid_query(idx, collection_name, req)
            for req in requests
        ]
        return list(await asyncio.gather(*tasks))

    # ------------------------------------------------------------------
    # Internal query helpers
    # ------------------------------------------------------------------

    async def _run_single_hybrid_query(
        self,
        idx: str,
        collection_name: str,
        req: HybridSearchRequest,
    ) -> List[SearchResult]:
        """Execute one FT.HYBRID query and return SearchResult list.

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

        # Escape the free-text query for RediSearch syntax before embedding it
        # in the combined query string.  Unescaped characters like { } @ : - can
        # break the query parser.
        text_query = escape_redisearch_text(req.text_query or "")
        search_query = _combine_text_and_filter(text_query, filter_query)

        if req.dense_query is None:
            # Dense vector required for FT.HYBRID; use text-only FT.SEARCH
            return await self._text_only_search(idx, search_query, req.limit)

        vec_bytes = vector_to_bytes(req.dense_query)
        k = req.limit
        window = max(k * 2, 20)

        # FT.HYBRID <idx>
        #   SEARCH <text+filter query>            ← lexical BM25 leg + filter
        #   VSIM @dense_embedding $vec
        #       KNN 2 K <window>                  ← 2 = arg-count for "K <value>"
        #       [FILTER <tag_query>]               ← pre-filter on the KNN leg
        #   COMBINE RRF 4 WINDOW <w> CONSTANT 60  ← 4 = arg-count for the 2 pairs
        #   LIMIT 0 <k>
        #   LOAD 1 $                              ← return full JSON document
        #   PARAMS 2 $vec <bytes>                 ← 2 = param-count (1 name + 1 value)
        cmd: List[Any] = [
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
            "LOAD", "1", "$",
            "PARAMS", "2", "$vec", vec_bytes,
        ]

        # FT.HYBRID errors propagate — do NOT silently fall back to text-only.
        raw = await self.client.execute_command(*cmd)  # type: ignore
        return parse_ft_hybrid_reply(raw, with_payload=req.with_payload)

    async def _text_only_search(
        self, idx: str, query: str, limit: int
    ) -> List[SearchResult]:
        """Text-only FT.SEARCH — used only when no dense query is provided."""
        raw = await self.client.execute_command(  # type: ignore
            "FT.SEARCH", idx, query or "*",
            "LIMIT", "0", str(limit),
            "RETURN", "1", "$",
        )
        return parse_ft_search_reply(raw)

    async def _keys_matching_filter(
        self, collection_name: str, filter_expr: FilterExpression
    ) -> List[bytes]:
        """Return all Redis keys in a collection matching a filter expression.

        Uses iterative re-query-from-offset-0 after each page deletion to avoid
        the stale-offset problem that arises when keys are deleted mid-iteration.
        For pure reads (delete_points = False context), returns all matching keys.
        """
        idx = self._index_name(collection_name)
        query = filter_expression_to_redis_query(filter_expr)

        # Single bulk read: paginate until exhausted.
        # Caller is responsible for not modifying the index between calls.
        page_size = 1000
        offset = 0
        all_keys: List[bytes] = []

        while True:
            try:
                raw = await self.client.execute_command(  # type: ignore
                    "FT.SEARCH", idx, query,
                    "NOCONTENT",
                    "LIMIT", str(offset), str(page_size),
                )
            except Exception as e:
                logger.warning(f"FT.SEARCH during key lookup failed: {e}")
                break

            if not raw or not isinstance(raw, (list, tuple)):
                break
            total = int(raw[0]) if raw else 0
            keys_in_page = [
                k if isinstance(k, bytes) else k.encode()
                for k in raw[1:]
            ]
            if not keys_in_page:
                break
            all_keys.extend(keys_in_page)
            offset += len(keys_in_page)
            if offset >= total:
                break

        return all_keys

    # ------------------------------------------------------------------
    # Internal guards
    # ------------------------------------------------------------------

    def _assert_connected(self) -> None:
        if self.client is None:
            raise RuntimeError("Redis client not connected. Call connect() first.")


# ------------------------------------------------------------------
# Module-level helpers
# ------------------------------------------------------------------

def _build_generic_conditions(filters: Dict[str, FilterValue]) -> List[FieldCondition]:
    """Build generic FieldCondition list from a key→value dict.
    Redis uses ``metadata_x`` (underscore) as field names in the index.
    We store the canonical ``metadata.x`` key here; conversion to Redis
    query syntax happens in utils.field_conditions_to_redis_query.
    """
    conditions: List[FieldCondition] = []
    for key, value in filters.items():
        if value is None:
            continue
        if isinstance(value, (list, tuple)):
            filtered = [v for v in value if v is not None]
            if filtered:
                conditions.append(FieldCondition(key=f"metadata.{key}", values=filtered))
        elif isinstance(value, str) and not value.strip():
            continue
        else:
            conditions.append(FieldCondition(key=f"metadata.{key}", value=value))
    return conditions


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


def _parse_ft_info(raw: Any) -> Dict:
    """Parse FT.INFO flat-list reply into a dict."""
    info: Dict = {}
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


def _extract_dim_from_ft_info_attributes(attributes: Any) -> Optional[int]:
    """Extract the DIM value for the ``dense_embedding`` VECTOR field from
    the ``attributes`` entry of an FT.INFO reply.

    FT.INFO ``attributes`` is a list of nested flat lists, e.g.::

        [[b'identifier', b'$.dense_embedding', b'attribute', b'dense_embedding',
          b'type', b'VECTOR', b'ALGORITHM', b'HNSW', b'data_type', b'FLOAT32',
          b'dim', b'1024', b'distance_metric', b'COSINE']]

    We locate the entry whose ``identifier`` is ``$.dense_embedding`` and
    extract the ``dim`` field from the same flat list.
    """
    if not isinstance(attributes, (list, tuple)):
        return None
    for attr_entry in attributes:
        if not isinstance(attr_entry, (list, tuple)):
            continue
        items = list(attr_entry)
        entry: Dict[str, Any] = {}
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
        if identifier == "$.dense_embedding":
            dim_val = entry.get("dim")
            if dim_val is not None:
                try:
                    return int(dim_val)
                except (ValueError, TypeError):
                    pass
    return None
