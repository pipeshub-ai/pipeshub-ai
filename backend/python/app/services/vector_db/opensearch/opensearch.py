"""OpenSearch vector database provider.

Fully async — uses AsyncOpenSearch everywhere.
Hybrid search: BM25 ``match`` (text_query) + k-NN dense, fused via
OpenSearch RRF ``score-ranker-processor`` pipeline (requires OpenSearch >= 2.19).

Key design decisions
--------------------
- Filters are embedded **inside each hybrid sub-query** (not post_filter) to
  preserve k-NN recall.
- The RRF pipeline is created idempotently at collection creation.
- Dense embedding stored as ``knn_vector`` field ``dense_embedding``.
- Metadata stored as explicit keyword-mapped fields under ``metadata.*``.
"""

import asyncio
import json
import time
from typing import Any, Dict, List, Optional, Union

from opensearchpy import AsyncOpenSearch, helpers as os_helpers  # type: ignore

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
from app.services.vector_db.opensearch.config import OpenSearchConfig
from app.services.vector_db.opensearch.utils import OpenSearchUtils
from app.utils.logger import create_logger

logger = create_logger("opensearch_service")

_SPACE_TYPE_MAP = {
    DistanceMetric.COSINE: "cosinesimil",
    DistanceMetric.L2: "l2",
    DistanceMetric.DOT_PRODUCT: "innerproduct",
}

_OS_CAPABILITIES = VectorDBCapabilities(
    supports_sparse_vectors=False,
    supports_server_side_text_search=True,
    supported_fusion_methods=[FusionMethod.RRF],
)


class OpenSearchService(IVectorDBService):
    """Fully-async OpenSearch provider implementing IVectorDBService."""

    def __init__(
        self,
        config_service: ConfigurationService | OpenSearchConfig,
    ) -> None:
        self.config_service = config_service
        self.client: Optional[AsyncOpenSearch] = None
        self._cfg: Optional[OpenSearchConfig] = None
        self._client_loop: Optional[asyncio.AbstractEventLoop] = None

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    async def create(
        cls,
        config: ConfigurationService | OpenSearchConfig,
        is_async: bool = True,  # kept for backward compat; always async now
    ) -> "OpenSearchService":
        service = cls(config)
        await service.connect()
        return service

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        """Parse and validate config.

        The ``AsyncOpenSearch`` client (and its ``aiohttp`` session) is NOT
        created here.  It is deferred to ``_ensure_client()`` so the session is
        always born inside the asyncio Task that first needs it, avoiding
        ``RuntimeError("Timeout context manager should be used inside a task")``
        when a Kafka/Redis consumer handler calls the service from a task that
        differs from the startup task.
        """
        try:
            if isinstance(self.config_service, ConfigurationService):
                raw = await self.config_service.get_config(
                    config_node_constants.OPENSEARCH.value
                )
            else:
                raw = self.config_service.opensearch_config

            if not raw:
                raise ValueError("OpenSearch configuration not found")

            self._cfg = OpenSearchConfig.from_dict(raw)
            logger.info(
                f"OpenSearch config loaded for {self._cfg.host}:{self._cfg.port} "
                f"(client will be created lazily on first use)"
            )
        except Exception as e:
            logger.error(f"Failed to load OpenSearch config: {e}")
            raise

    @staticmethod
    def _build_client(cfg: "OpenSearchConfig") -> AsyncOpenSearch:
        """Construct AsyncOpenSearch with the given config.

        This is the pluggable auth seam.  Supported auth_type values:

        - ``"basic"``  — HTTP basic auth (username + password).  Credentials
          are omitted when both are empty, which is correct for clusters where
          the security plugin is disabled (e.g. local dev).
        - ``"none"``   — Explicit no-auth (same behaviour as empty basic creds).

        Future AWS SigV4 support adds a new branch here — no call-site changes.
        """
        if cfg.auth_type in ("basic", "none"):
            kwargs: dict = {
                "hosts": [{"host": cfg.host, "port": cfg.port}],
                "use_ssl": cfg.use_ssl,
                "verify_certs": cfg.verify_certs,
                "ssl_show_warn": cfg.ssl_show_warn,
                "timeout": cfg.timeout,
            }
            # Only attach credentials when both are provided; omitting http_auth
            # is correct when the OpenSearch security plugin is disabled.
            if cfg.username and cfg.password:
                kwargs["http_auth"] = (cfg.username, cfg.password)
            return AsyncOpenSearch(**kwargs)
        raise ValueError(
            f"auth_type '{cfg.auth_type}' not supported yet. "
            "Supported values: 'basic', 'none'. To add AWS SigV4 support, add a new "
            "branch to OpenSearchService._build_client() and extend OpenSearchConfig."
        )

    async def _ensure_client(self) -> AsyncOpenSearch:
        """Return the live client, creating it on the current event loop if needed.

        The ``aiohttp.ClientSession`` inside ``AsyncOpenSearch`` is bound to the
        event loop where it was created.  The indexing consumer runs a dedicated
        worker thread with its own ``asyncio.new_event_loop()``, so a client
        created on the main loop cannot be reused there.

        This method detects a loop mismatch and transparently recreates the
        client on the current loop — no caller changes required.
        """
        current_loop = asyncio.get_running_loop()
        if self.client is not None:
            if self._client_loop is None or self._client_loop is current_loop:
                return self.client
            # Loop mismatch — the old aiohttp session cannot be used here.
            self.client = None
            self._client_loop = None

        if self._cfg is None:
            raise RuntimeError(
                "OpenSearch config not loaded. Call connect() first."
            )

        self.client = self._build_client(self._cfg)
        self._client_loop = current_loop
        try:
            info = await self.client.info()
            version = info.get("version", {}).get("number", "unknown")
            logger.info(
                f"Connected to OpenSearch {version} at "
                f"{self._cfg.host}:{self._cfg.port} "
                f"(loop id={id(current_loop)})"
            )
        except Exception:
            try:
                await self.client.close()
            except Exception:
                pass
            self.client = None
            self._client_loop = None
            raise
        return self.client

    async def disconnect(self) -> None:
        if self.client is not None:
            try:
                await self.client.close()
                logger.info("Disconnected from OpenSearch")
            except Exception as e:
                logger.warning(f"Error during OpenSearch disconnect: {e}")
            finally:
                self.client = None

    # ------------------------------------------------------------------
    # Identity
    # ------------------------------------------------------------------

    def get_service_name(self) -> str:
        return "opensearch"

    def get_service(self) -> "OpenSearchService":
        return self

    def get_service_client(self) -> AsyncOpenSearch:
        return self.client  # type: ignore

    # ------------------------------------------------------------------
    # Capabilities and health
    # ------------------------------------------------------------------

    def get_capabilities(self) -> VectorDBCapabilities:
        return _OS_CAPABILITIES

    async def health_check(self) -> VectorDBHealth:
        start = time.monotonic()
        if self._cfg is None and self.client is None:
            return VectorDBHealth(status=HealthStatus.UNHEALTHY, message="Not connected")
        try:
            client = await self._ensure_client()
            info = await client.info()
            latency_ms = round((time.monotonic() - start) * 1000, 2)
            version = info.get("version", {}).get("number")
            # Version gate: RRF score-ranker-processor requires OpenSearch >= 2.19
            if version:
                ok, msg = _check_version_supports_rrf(version)
                if not ok:
                    return VectorDBHealth(
                        status=HealthStatus.DEGRADED,
                        latency_ms=latency_ms,
                        server_version=version,
                        message=msg,
                    )
            return VectorDBHealth(
                status=HealthStatus.HEALTHY,
                latency_ms=latency_ms,
                server_version=version,
                message="OpenSearch reachable",
            )
        except Exception as e:
            latency_ms = round((time.monotonic() - start) * 1000, 2)
            return VectorDBHealth(
                status=HealthStatus.UNHEALTHY,
                latency_ms=latency_ms,
                message=str(e),
            )

    # ------------------------------------------------------------------
    # Collection management
    # ------------------------------------------------------------------

    async def create_collection(
        self,
        collection_name: str = "records",
        config: Optional[CollectionConfig] = None,
    ) -> None:
        await self._assert_connected()
        if config is None:
            config = CollectionConfig()

        space_type = _SPACE_TYPE_MAP.get(config.distance_metric, "cosinesimil")

        # Only create if not already present
        exists = await self.client.indices.exists(index=collection_name)  # type: ignore
        if not exists:
            index_body = {
                "settings": {
                    "index.knn": True,
                    "number_of_shards": 1,
                    "number_of_replicas": 0,
                },
                "mappings": {
                    "properties": {
                        "dense_embedding": {
                            "type": "knn_vector",
                            "dimension": config.embedding_size,
                            "method": {
                                "engine": "lucene",
                                "space_type": space_type,
                                "name": "hnsw",
                                "parameters": {
                                    "ef_construction": 256,
                                    "m": 48,
                                },
                            },
                        },
                        "page_content": {"type": "text"},
                        # Explicit keyword fields for filtering (avoid dynamic mapping)
                        "metadata": {
                            "type": "object",
                            "properties": {
                                "orgId": {"type": "keyword"},
                                "virtualRecordId": {"type": "keyword"},
                            },
                        },
                    }
                },
            }
            await self.client.indices.create(index=collection_name, body=index_body)  # type: ignore
            logger.info(f"Created OpenSearch index '{collection_name}'")

        # Create / update the RRF search pipeline idempotently
        await self._ensure_rrf_pipeline(collection_name)

    async def _ensure_rrf_pipeline(self, collection_name: str) -> None:
        """Create the RRF score-ranker pipeline only when it doesn't already exist.

        Idempotent: skips the PUT if the pipeline is already present.
        OpenSearch >= 2.19 is required for the ``score-ranker-processor``.
        """
        pipeline_name = f"{collection_name}-rrf-pipeline"
        # Check existence before writing (avoids unnecessary traffic on every
        # create_collection call, especially in tests and cold starts).
        try:
            await self.client.transport.perform_request(  # type: ignore
                "GET", f"/_search/pipeline/{pipeline_name}"
            )
            logger.debug(f"RRF pipeline '{pipeline_name}' already exists, skipping creation")
            return
        except Exception:
            pass  # Pipeline absent — proceed to create

        pipeline_body = {
            "description": "RRF hybrid search pipeline",
            "phase_results_processors": [
                {
                    "score-ranker-processor": {
                        "combination": {
                            "technique": "rrf",
                            "rank_constant": 60,
                        }
                    }
                }
            ],
        }
        await self.client.transport.perform_request(  # type: ignore
            "PUT",
            f"/_search/pipeline/{pipeline_name}",
            body=pipeline_body,
        )
        logger.info(f"Created RRF pipeline '{pipeline_name}'")

    async def get_collections(self) -> object:
        await self._assert_connected()
        return await self.client.indices.get_alias(index="*")  # type: ignore

    async def get_collection(self, collection_name: str) -> object:
        await self._assert_connected()
        return await self.client.indices.get(index=collection_name)  # type: ignore

    async def get_collection_info(self, collection_name: str) -> VectorCollectionInfo:
        """Return normalised collection metadata.

        Only swallows NotFoundError (index doesn't exist).  Connectivity / auth
        errors propagate so callers can distinguish "not created yet" from "outage".
        """
        await self._assert_connected()
        try:
            exists = bool(await self.client.indices.exists(index=collection_name))  # type: ignore
            if not exists:
                return VectorCollectionInfo(name=collection_name, exists=False)

            mapping = await self.client.indices.get(index=collection_name)  # type: ignore
            props = mapping.get(collection_name, {}).get("mappings", {}).get("properties", {})
            dense_dim: Optional[int] = None
            dense_props = props.get("dense_embedding", {})
            if "dimension" in dense_props:
                dense_dim = int(dense_props["dimension"])
            count_resp = await self.client.count(index=collection_name)  # type: ignore
            points_count = count_resp.get("count", 0)
            return VectorCollectionInfo(
                name=collection_name,
                exists=True,
                dense_dimension=dense_dim,
                points_count=points_count,
            )
        except Exception as exc:
            if _is_not_found_error(exc):
                return VectorCollectionInfo(name=collection_name, exists=False)
            raise

    async def collection_exists(self, collection_name: str) -> bool:
        """Return True if the index exists; False on 404; re-raise on connectivity errors."""
        await self._assert_connected()
        try:
            return bool(await self.client.indices.exists(index=collection_name))  # type: ignore
        except Exception as exc:
            if _is_not_found_error(exc):
                return False
            raise

    async def delete_collection(self, collection_name: str) -> None:
        await self._assert_connected()
        if await self.client.indices.exists(index=collection_name):  # type: ignore
            await self.client.indices.delete(index=collection_name)  # type: ignore
            logger.info(f"Deleted OpenSearch index '{collection_name}'")

        pipeline_name = f"{collection_name}-rrf-pipeline"
        try:
            await self.client.transport.perform_request(  # type: ignore
                "DELETE", f"/_search/pipeline/{pipeline_name}"
            )
        except Exception as exc:
            # Only ignore genuine 404s; surface unexpected errors
            if not _is_not_found_error(exc):
                logger.warning(f"Unexpected error deleting pipeline '{pipeline_name}': {exc}")

    async def create_index(
        self,
        collection_name: str,
        field_name: str,
        field_schema: dict,
    ) -> None:
        await self._assert_connected()
        # field_name e.g. "metadata.virtualRecordId"
        # Build nested mapping path
        parts = field_name.split(".")
        if len(parts) == 2:
            parent, child = parts
            os_type = "keyword" if field_schema.get("type") == "keyword" else "text"
            mapping_body = {
                "properties": {
                    parent: {
                        "type": "object",
                        "properties": {
                            child: {"type": os_type}
                        },
                    }
                }
            }
        else:
            os_type = "keyword" if field_schema.get("type") == "keyword" else "text"
            mapping_body = {"properties": {field_name: {"type": os_type}}}

        await self.client.indices.put_mapping(index=collection_name, body=mapping_body)  # type: ignore

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
        from app.services.vector_db.filters import build_filter_expression

        return build_filter_expression(
            filter_mode,
            must=must,
            should=should,
            must_not=must_not,
            min_should_match=min_should_match,
            extra_kwargs=kwargs or None,
            build_conditions=OpenSearchUtils.build_conditions,
        )

    # ------------------------------------------------------------------
    # Data operations — all async
    # ------------------------------------------------------------------

    async def scroll(
        self,
        collection_name: str,
        scroll_filter: FilterExpression,
        limit: int,
        offset: Optional[str] = None,
    ) -> ScrollResult:
        """Scroll a page of points.

        ``offset`` is the opaque cursor returned in ``ScrollResult.next_offset``
        from the previous call.  It is the OpenSearch ``search_after`` value
        serialised as a JSON string.  Pass ``None`` for the first page.
        """
        await self._assert_connected()
        bool_query = OpenSearchUtils.filter_expression_to_bool_query(scroll_filter)
        body: Dict[str, Any] = {
            "query": bool_query,
            "size": min(limit, 10000),
            "_source": {"exclude": ["dense_embedding"]},
            "sort": [{"_id": "asc"}],
        }

        if offset is not None:
            import json as _json
            try:
                body["search_after"] = _json.loads(offset)
            except (ValueError, TypeError):
                body["search_after"] = [offset]

        result = await self.client.search(index=collection_name, body=body)  # type: ignore
        hits = result.get("hits", {}).get("hits", [])
        if len(hits) > limit:
            hits = hits[:limit]
        points = [
            VectorPoint(
                id=hit["_id"],
                payload={
                    "metadata": hit.get("_source", {}).get("metadata", {}),
                    "page_content": hit.get("_source", {}).get("page_content", ""),
                },
            )
            for hit in hits
        ]
        # Return a cursor for the next page when the result set is full
        next_offset = None
        if len(hits) == limit and hits:
            import json as _json
            last_sort = hits[-1].get("sort")
            if last_sort:
                next_offset = _json.dumps(last_sort)

        return ScrollResult(points=points, next_offset=next_offset)

    async def query_nearest_points(
        self,
        collection_name: str,
        requests: List[HybridSearchRequest],
    ) -> List[List[SearchResult]]:
        await self._assert_connected()
        pipeline_name = f"{collection_name}-rrf-pipeline"

        has_hybrid = any(
            r.dense_query is not None and r.text_query is not None
            for r in requests
        )
        if has_hybrid:
            await self._ensure_rrf_pipeline(collection_name)

        async def _one_search(req: HybridSearchRequest) -> List[SearchResult]:
            body = OpenSearchUtils.build_hybrid_query(req)
            # Only attach the RRF pipeline when the request is genuinely hybrid
            # (has both a dense leg and a text/BM25 leg).  Single-leg queries
            # must not use it — the score-ranker-processor produces nonsensical
            # scores when only one sub-query is present.
            is_hybrid = (
                req.dense_query is not None and req.text_query is not None
            )
            search_kwargs: Dict[str, Any] = {
                "index": collection_name,
                "body": body,
            }
            if is_hybrid:
                search_kwargs["params"] = {"search_pipeline": pipeline_name}

            result = await self.client.search(**search_kwargs)  # type: ignore
            hits = result.get("hits", {}).get("hits", [])
            return [OpenSearchUtils.hit_to_search_result(h) for h in hits]

        return list(await asyncio.gather(*[_one_search(r) for r in requests]))

    async def upsert_points(
        self,
        collection_name: str,
        points: List[VectorPoint],
        batch_size: int = 500,
    ) -> None:
        await self._assert_connected()
        start = time.perf_counter()
        logger.info(
            f"Upserting {len(points)} points into OpenSearch index '{collection_name}'"
        )

        actions = [
            {
                "_index": collection_name,
                "_id": p.id,
                "_source": OpenSearchUtils.vector_point_to_document(p),
            }
            for p in points
        ]

        await os_helpers.async_bulk(
            self.client,
            actions,
            chunk_size=batch_size,
            raise_on_error=True,
        )
        elapsed = time.perf_counter() - start
        logger.info(
            f"Upsert complete: {len(points)} points in {elapsed:.2f}s "
            f"({len(points)/elapsed:.0f} pts/s)"
        )

    async def delete_points(
        self,
        collection_name: str,
        filter: FilterExpression,
    ) -> None:
        if filter.is_empty():
            raise ValueError(
                "delete_points called with an empty filter — this would wipe the entire "
                "index. Populate at least one filter condition (e.g. virtualRecordId)."
            )
        await self._assert_connected()
        bool_query = OpenSearchUtils.filter_expression_to_bool_query(filter)
        await self.client.delete_by_query(  # type: ignore
            index=collection_name,
            body={"query": bool_query},
            conflicts="proceed",
            slices="auto",
            wait_for_completion=True,
        )
        logger.info(f"Deleted points from OpenSearch index '{collection_name}'")

    async def overwrite_payload(
        self,
        collection_name: str,
        payload: dict,
        points: FilterExpression,
    ) -> None:
        """Update fields in matched documents via a Painless script.

        Dotted keys like ``"metadata.status"`` are correctly expanded to nested
        field access (``ctx._source.metadata.status``), not treated as literal
        top-level key names.
        """
        await self._assert_connected()
        bool_query = OpenSearchUtils.filter_expression_to_bool_query(points)
        script_parts = []
        params: Dict[str, Any] = {}
        for key, value in payload.items():
            # Sanitise param name: replace non-alphanumeric chars with underscores
            import re as _re
            param_name = "p_" + _re.sub(r"[^a-zA-Z0-9]", "_", key)
            params[param_name] = value
            if "." in key:
                # e.g. "metadata.status" → ctx._source.metadata.status = params.p_metadata_status
                parts = key.split(".")
                source_path = ".".join(parts)
                script_parts.append(
                    f"ctx._source.{source_path} = params.{param_name}"
                )
            else:
                script_parts.append(f"ctx._source['{key}'] = params.{param_name}")

        await self.client.update_by_query(  # type: ignore
            index=collection_name,
            body={
                "query": bool_query,
                "script": {
                    "lang": "painless",
                    "source": "; ".join(script_parts),
                    "params": params,
                },
            },
        )

    # ------------------------------------------------------------------
    # Internal guards
    # ------------------------------------------------------------------

    async def _assert_connected(self) -> None:
        """Ensure the client is ready, creating it lazily if needed."""
        await self._ensure_client()


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _is_not_found_error(exc: Exception) -> bool:
    """Return True when *exc* represents a 404-style index-not-found condition."""
    msg = str(exc).lower()
    return (
        "not found" in msg
        or "index_not_found" in msg
        or "no such index" in msg
        or "404" in msg
        or getattr(exc, "status_code", None) == 404
        or getattr(exc, "error", "") == "index_not_found_exception"
    )

_MIN_RRF_MAJOR = 2
_MIN_RRF_MINOR = 19


def _check_version_supports_rrf(version_str: str) -> tuple:
    """Return (ok, message) checking OpenSearch >= 2.19 for RRF support."""
    import re
    try:
        m = re.match(r"(\d+)\.(\d+)", version_str)
        if not m:
            return True, ""
        major, minor = int(m.group(1)), int(m.group(2))
        if (major, minor) < (_MIN_RRF_MAJOR, _MIN_RRF_MINOR):
            return (
                False,
                f"OpenSearch {version_str} does not support the score-ranker-processor "
                f"(RRF); requires >= {_MIN_RRF_MAJOR}.{_MIN_RRF_MINOR}",
            )
        return True, ""
    except Exception:
        return True, ""
