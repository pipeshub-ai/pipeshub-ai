"""OpenSearch vector database provider.

Fully async — uses AsyncOpenSearch everywhere.
Hybrid search: BM25 ``multi_match`` over exact and English-stemmed
``page_content`` (text_query) + k-NN dense, fused via
OpenSearch RRF ``score-ranker-processor`` pipeline (requires OpenSearch >= 2.19).

Key design decisions
--------------------
- Filters are embedded **inside each hybrid sub-query** (not post_filter) to
  preserve k-NN recall.
- The RRF pipeline is created idempotently at collection creation.
- Dense embedding stored as ``knn_vector`` field ``dense_embedding``.
- Metadata stored as explicit keyword-mapped fields under ``metadata.*``.

Performance optimisations
------------------------
- HNSW ``m`` reduced from 48 → 16 (configurable).  Each node previously
  maintained 48 bidirectional links; 16 cuts graph-build and traversal cost
  by ~3× with negligible recall loss for datasets < 10 M vectors.
- ``ef_construction`` lowered from 256 → 128 (configurable).  128 is the
  OpenSearch/Lucene recommended default; 256 doubled the beam-search cost
  per insert with negligible recall gain.
- Lucene 7-bit scalar quantization (``sq`` encoder) enabled by default.
  Reduces memory footprint ~4× (comparable to Qdrant's INT8 default) with
  < 5% recall loss.
- ``index.knn.algo_param.ef_search`` set in index settings so the Lucene
  engine uses a proper beam width instead of defaulting to k.
- ``index.refresh_interval`` set to ``30s`` so ingest builds fewer, larger
  Lucene segments instead of one segment per second.  New documents become
  searchable within ~30 s.
- ``index.translog.flush_threshold_size`` set to ``1gb`` to reduce expensive
  Lucene flushes during bulk ingest.
- ``metadata.*`` string fields mapped to ``keyword`` via a dynamic_template so
  term/terms filters on any metadata subfield (blockId, etc.) work correctly.
- ``upsert_points`` issues the bulk with ``refresh=False`` (no per-call refresh).
- ``index.merge.policy.segments_per_tier`` lowered to 4 so TieredMergePolicy
  keeps segment count (and therefore search cost) low continuously.
- ``force_merge`` exposed as an explicit operator action for indices that are
  no longer written to; it must not be run on a schedule against a live index.
- ``warmup`` method calls the OpenSearch k-NN warmup API to load native
  library indexes into the OS page cache before the first search.
- RRF ``rank_constant`` is now configurable via ``OpenSearchConfig``.
- JVM heap + container memory limits raised in docker-compose (2 GB JVM,
  4 GB container) to give the Lucene mmap engine a larger OS page cache.
"""

import asyncio
import json
import re
import time
from dataclasses import replace
from typing import Any, Dict, List, Optional, Union

from opensearchpy import AsyncOpenSearch  # type: ignore
from opensearchpy import helpers as os_helpers
from opensearchpy.exceptions import NotFoundError

from app.config.configuration_service import ConfigurationService
from app.config.constants.service import config_node_constants
from app.services.permissions.mode import scope_fields_enabled
from app.services.permissions.scope_fields import SCOPE_KEYS
from app.services.vector_db.const.const import (
    CONNECTOR_IDS_FIELD,
    RECORD_GROUP_IDS_FIELD,
    ROOT_RECORD_GROUP_IDS_FIELD,
    SCOPE_KEYWORD_INDEXES,
)
from app.services.vector_db.errors import OrgRequired
from app.services.vector_db.filters import (
    forbid_org_in_payload,
    is_missing,
    keep_org_in_overwrite,
    reject_scope_filter,
    require_filter_org,
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
    SearchFailureKind,
    SearchResult,
    VectorCollectionInfo,
    VectorDBCapabilities,
    VectorDBHealth,
    VectorPoint,
)
from app.services.vector_db.opensearch.config import OpenSearchConfig
from app.services.vector_db.opensearch.errors import classify_opensearch_search_error
from app.services.vector_db.opensearch.utils import (
    PAGE_CONTENT_FIELD,
    PAGE_CONTENT_MAPPING,
    STEMMED_PAGE_CONTENT_FIELD,
    STEMMED_SUBFIELD,
    OpenSearchUtils,
)
from app.utils.logger import create_logger
from app.utils.loop_local import LoopLocal, running_loop

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
    supports_multi_collection=True,
    max_recommended_collections=200,
)

# Defaults used when _cfg is not yet loaded (e.g. connected_service fixture in tests)
_DEFAULT_M = 16
_DEFAULT_EF_CONSTRUCTION = 128
_DEFAULT_EF_SEARCH = 100
_DEFAULT_QUANTIZATION_BITS = 7

# Fallbacks for when _cfg is absent (unit tests inject a raw client).
_DEFAULT_SEGMENTS_PER_TIER = 4
_DEFAULT_MAX_CONCURRENT_SEARCHES = 8
_DEFAULT_CONFIDENCE_INTERVAL = 0.99
_DEFAULT_RRF_RANK_CONSTANT = 60
# A partial update re-reads the document when another write lands between its
# read and write; without retries it fails the item with a 409.
_MERGE_IF_ORG_SCRIPT = (
    "void merge(Map t, Map s) { for (e in s.entrySet()) { def v = e.getValue(); def c = t.get(e.getKey()); "
    "if (v instanceof Map && c instanceof Map) { merge(c, v) } else { t.put(e.getKey(), v) } } } "
    "if (ctx._source.metadata == null || ctx._source.metadata.orgId != params.org) { ctx.op = 'noop' } "
    "else { merge(ctx._source, params.doc) }"
)
# Bounded: a conflict means a concurrent writer; a few refreshed passes outlast it, more would hide a bug.
_DELETE_CONFLICT_RETRIES = 5
_ANY = "*"  # a written point that names no virtual record
_UNREFRESHED_MAX = 10_000
_DEFAULT_VISIBLE_AFTER_SECONDS = 35.0  # the 30 s refresh_interval the provider creates indexes with, and a margin
_VISIBLE_MARGIN_SECONDS = 5.0
_INTERVAL_REREAD_SECONDS = 60.0
_INTERVAL_UNITS = {"nanos": 1e-9, "micros": 1e-6, "ms": 1e-3, "s": 1.0, "m": 60.0, "h": 3600.0, "d": 86400.0}


def _interval_seconds(raw: object) -> float | None:
    """A refresh_interval setting ("30s", "1m", "500ms") in seconds; None for -1 (never) or anything unreadable."""
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*(nanos|micros|ms|s|m|h|d)\s*", str(raw or ""))
    if match is None:
        return None
    seconds = float(match.group(1)) * _INTERVAL_UNITS[match.group(2)]
    return seconds if seconds > 0 else None
_UPDATE_RETRY_ON_CONFLICT = 3

# Progress of the stemmed-field backfill, kept in the index's own ``_meta`` so
# every replica reads the same state: {"task": <id>} while it runs, {"done":
# true} once it succeeded.
_STEMMED_BACKFILL_META_KEY = "pipeshub_stemmed_backfill"
# The backfill rewrites every older document; throttling keeps it from
# competing with live indexing and search for I/O.
_STEMMED_BACKFILL_DOCS_PER_SECOND = 500
_MISSING_STEMMED_QUERY: Dict[str, Any] = {
    "bool": {"must_not": {"exists": {"field": STEMMED_PAGE_CONTENT_FIELD}}}
}


def _has_stemmed_page_content(mappings: Dict[str, Any]) -> bool:
    page_content = (mappings.get("properties") or {}).get(PAGE_CONTENT_FIELD) or {}
    return STEMMED_SUBFIELD in (page_content.get("fields") or {})


class _LoopClient:
    """One event loop's client, and whether it has been checked against the server yet."""

    def __init__(self, client: AsyncOpenSearch) -> None:
        super().__init__()
        self.client = client
        self.checked = False
        # One caller probes the server; the others on this loop wait for it.
        self.checking = asyncio.Lock()


class OpenSearchService(IVectorDBService):
    """Fully-async OpenSearch provider implementing IVectorDBService."""

    def __init__(
        self,
        config_service: ConfigurationService | OpenSearchConfig,
    ) -> None:
        self.config_service = config_service
        self._cfg: Optional[OpenSearchConfig] = None
        # The aiohttp session inside AsyncOpenSearch binds to the loop that first uses it, and
        # this service is shared by the server loop and every consumer's worker loop.
        self._clients: LoopLocal[_LoopClient] = LoopLocal(self._new_loop_client)
        # Assigned through `client` (tests); served to every loop as-is.
        self._client_override: AsyncOpenSearch | None = None
        # Per index, what this process wrote since the index last refreshed: virtual record id -> when
        # (``_ANY`` for a point that names none). Only a delete that could match such a point refreshes first.
        # shortcut: writes by other processes are not seen here and wait for the index's own 30 s refresh;
        # upgrade to a refresh before every delete if cross-process delete-after-write must be exact.
        self._unrefreshed: dict[str, dict[str, float]] = {}
        # Per index: (seconds after which a write is visible, or None when the index does not refresh on
        # its own or the setting is unknown; read at). Re-read now and then, so an operator change is seen.
        self._visible_after: dict[str, tuple[float | None, float]] = {}

    @property
    def client(self) -> AsyncOpenSearch | None:
        """The running loop's client; ``_ensure_client`` is what checks it against the server."""
        if self._client_override is not None:
            return self._client_override
        if self._cfg is None:
            return None
        return self._clients.get().client

    @client.setter
    def client(self, value: AsyncOpenSearch | None) -> None:
        self._client_override = value

    def _new_loop_client(self) -> _LoopClient:
        if self._cfg is None:
            raise RuntimeError("OpenSearch config not loaded. Call connect() first.")
        return _LoopClient(self._build_client(self._cfg))

    async def _guard_scope_mapping(self, collection_name: str, keys: Any) -> bool:  # noqa: ANN401
        """Before the first write that carries scope fields to an index: the id lists must be mapped as keyword. An index
        created before they existed maps an unseen string field dynamically as text (split on hyphens, and impossible to change
        afterwards), where a `terms` filter on a path id would never match. Map the fields when they are absent. When one is
        already mapped as anything else, return False: the caller writes the point **without** the scope fields (it stays
        unstamped, which is the legacy filter), so indexing and membership go on; this is logged once per index and listed in
        ``scope_blocked``, and such an index must never be switched to the scope filter."""
        mapped: set[str] = self.__dict__.setdefault("_scope_mapped", set())
        blocked: set[str] = self.__dict__.setdefault("scope_blocked", set())
        if collection_name in blocked:
            return False
        if collection_name in mapped or not set(keys) & set(SCOPE_KEYS):
            return True
        await self._assert_connected()
        try:
            got = await self.client.indices.get_mapping(index=collection_name)  # type: ignore
        except NotFoundError:
            return True  # no index to guard: the write itself reports a missing index as it always did; nothing is remembered
        # A name may be an alias over several indices: every one behind it must map the fields as keyword.
        for index, body in got.items():
            props = body.get("mappings", {}).get("properties", {})
            missing = {}
            for field, _ in SCOPE_KEYWORD_INDEXES:
                kind = (props.get(field) or {}).get("type")
                if kind is None:
                    missing[field] = {"type": "keyword"}
                elif kind != "keyword":
                    blocked.add(collection_name)
                    logger.error(
                        "index '%s' maps '%s' as %s, not keyword: scope fields are not written to it and it must not be switched to "
                        "the scope filter (re-create the index, or reindex it with the keyword mapping)", index, field, kind)
                    return False
            if missing:
                await self.client.indices.put_mapping(index=index, body={"properties": missing})  # type: ignore
        mapped.add(collection_name)
        return True

    def _forget_scope_mapping(self, collection_name: str) -> None:
        """An index that is created or deleted is a different one next time: what was learned about its mapping no longer holds."""
        self.__dict__.setdefault("_scope_mapped", set()).discard(collection_name)
        self.__dict__.setdefault("scope_blocked", set()).discard(collection_name)

    @staticmethod
    def _without_scope(payload: dict) -> dict:
        return {k: v for k, v in payload.items() if k not in SCOPE_KEYS}

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
        """Return the running loop's client, checking it against the server on first use.

        Each loop keeps its own client, so a loop never swaps out a client another loop
        has requests in flight on.
        """
        if self._client_override is not None:
            return self._client_override
        if self._cfg is None:
            raise RuntimeError(
                "OpenSearch config not loaded. Call connect() first."
            )

        while True:
            entry = self._clients.get()
            if entry.checked:
                return entry.client
            async with entry.checking:
                if entry.checked:
                    return entry.client
                if entry is not self._clients.get():
                    # A failed probe replaced it while this caller waited: check the new one.
                    continue
                try:
                    info = await entry.client.info()
                except Exception:
                    # Nobody was served this client: close it; the next call builds a fresh one.
                    self._clients.replace_current()
                    try:
                        await entry.client.close()
                    except Exception:
                        pass
                    raise
                version = info.get("version", {}).get("number", "unknown")
                logger.info(
                    f"Connected to OpenSearch {version} at "
                    f"{self._cfg.host}:{self._cfg.port} "
                    f"(loop id={id(running_loop())})"
                )
                entry.checked = True
                return entry.client

    async def disconnect(self) -> None:
        """Close every loop's client, each on the loop that owns it."""
        override, self._client_override = self._client_override, None
        had_clients = override is not None or len(self._clients) > 0
        errors = await self._clients.aclose_all(lambda entry: entry.client.close())
        if override is not None:
            try:
                await override.close()
            except Exception as e:
                errors.append(e)
        for error in errors:
            logger.warning(f"Error during OpenSearch disconnect: {error}")
        if had_clients:
            logger.info("Disconnected from OpenSearch")

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

    def transient_search_error(self, error: BaseException) -> SearchFailureKind | None:
        return classify_opensearch_search_error(error)

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
        self._forget_scope_mapping(collection_name)
        if config is None:
            config = CollectionConfig()

        space_type = _SPACE_TYPE_MAP.get(config.distance_metric, "cosinesimil")

        # Read tuning params from service config; fall back to defaults when
        # _cfg is None (e.g. unit tests that inject a raw client directly).
        m = self._cfg.m if self._cfg else _DEFAULT_M
        ef_construction = self._cfg.ef_construction if self._cfg else _DEFAULT_EF_CONSTRUCTION
        ef_search = self._cfg.ef_search if self._cfg else _DEFAULT_EF_SEARCH
        quantization_bits = self._cfg.quantization_bits if self._cfg else _DEFAULT_QUANTIZATION_BITS
        confidence_interval = self._cfg.confidence_interval if self._cfg else _DEFAULT_CONFIDENCE_INTERVAL
        segments_per_tier = self._cfg.segments_per_tier if self._cfg else _DEFAULT_SEGMENTS_PER_TIER

        # Build HNSW parameters block
        hnsw_params: Dict[str, Any] = {
            "ef_construction": ef_construction,
            "m": m,
        }
        # QuantizationType.NONE is provider-agnostic and must actually disable
        # quantization here too; without this the caller asks for full-precision
        # vectors and silently keeps getting quantized ones.
        if config.quantization == QuantizationType.NONE:
            quantization_bits = 0

        # Lucene scalar quantization at the configured bit depth (7 by default:
        # ~4× memory reduction, equivalent to Qdrant's INT8, < 5% recall loss).
        # Lucene keeps these memory-mapped, so there is no always-RAM pin to
        # disable the way Qdrant needs — CollectionConfig.quantization_always_ram
        # does not apply to this provider.
        if quantization_bits > 0:
            hnsw_params["encoder"] = {
                "name": "sq",
                "parameters": {
                    "bits": quantization_bits,
                    "confidence_interval": confidence_interval,
                },
            }

        # Only create if not already present
        exists = await self.client.indices.exists(index=collection_name)  # type: ignore
        if not exists:
            index_body = {
                "settings": {
                    "index.knn": True,
                    # ef_search sets the Lucene HNSW beam width at search time.
                    # Without this, OpenSearch 3.x defaults ef_search to k which
                    # reduces recall when k is small.
                    "index.knn.algo_param.ef_search": ef_search,
                    "number_of_shards": 1,
                    "number_of_replicas": 0,
                    # Batch refreshes: build fewer, larger Lucene segments during
                    # ingest instead of one tiny segment per second.  New documents
                    # become searchable within ~30 s (acceptable per design decision).
                    "index.refresh_interval": "30s",
                    # Allow the translog to accumulate up to 1 GiB before flushing so
                    # we don't pay a Lucene flush + segment build on every small batch.
                    "index.translog.flush_threshold_size": "1gb",
                    # Search cost scales with segment count, and the way to keep
                    # that low on a *live* index is the merge policy, not periodic
                    # force-merges: TieredMergePolicy merges continuously, is
                    # throttled, and keeps every segment under
                    # max_merged_segment so it stays eligible for future merges.
                    # Lower than the default 10 to trade a little more background
                    # merge I/O for fewer segments at search time.
                    "index.merge.policy.segments_per_tier": segments_per_tier,
                },
                "mappings": {
                    # Map every string field under metadata.* to keyword so that
                    # term/terms filters (orgId, virtualRecordId, blockId, etc.) work
                    # correctly.  Without this, dynamically-created metadata sub-fields
                    # get the default "text" type and term queries on UUID-style values
                    # fail silently (the standard analyser tokenises on hyphens).
                    "dynamic_templates": [
                        {
                            "metadata_strings_as_keyword": {
                                "path_match": "metadata.*",
                                "match_mapping_type": "string",
                                "mapping": {"type": "keyword"},
                            }
                        }
                    ],
                    "properties": {
                        "dense_embedding": {
                            "type": "knn_vector",
                            "dimension": config.embedding_size,
                            "method": {
                                "engine": "lucene",
                                "space_type": space_type,
                                "name": "hnsw",
                                "parameters": hnsw_params,
                            },
                        },
                        PAGE_CONTENT_FIELD: PAGE_CONTENT_MAPPING,
                        # Sortable stand-in for _id so scroll can page with
                        # search_after; _id itself requires fielddata.
                        "point_id": {"type": "keyword"},
                        "connectorIds": {"type": "keyword"},
                        "recordGroupIds": {"type": "keyword"},
                        "rootRecordGroupIds": {"type": "keyword"},
                        # scope fields (permission model): keyword mappings up front, so a dynamic mapping never types them as text
                        **({field: {"type": "keyword"} for field, _ in SCOPE_KEYWORD_INDEXES} if scope_fields_enabled() else {}),
                        # Keep explicit keyword declarations for the two most-used
                        # filter fields so the mapping is readable without introspection.
                        "metadata": {
                            "type": "object",
                            "properties": {
                                "orgId": {"type": "keyword"},
                                "virtualRecordId": {"type": "keyword"},
                                "blockId": {"type": "keyword"},
                            },
                        },
                    },
                },
            }
            await self.client.indices.create(index=collection_name, body=index_body)  # type: ignore
            logger.info(
                f"Created OpenSearch index '{collection_name}' "
                f"(m={m}, ef_construction={ef_construction}, ef_search={ef_search}, "
                f"quantization_bits={quantization_bits}, refresh_interval=30s)"
            )

        # Create / update the RRF search pipeline idempotently
        await self._ensure_rrf_pipeline(collection_name)

    async def _ensure_rrf_pipeline(self, collection_name: str) -> None:
        """Create the RRF score-ranker pipeline only when it doesn't already exist.

        Idempotent: skips the PUT if the pipeline is already present.
        OpenSearch >= 2.19 is required for the ``score-ranker-processor``.
        """
        pipeline_name = f"{collection_name}-rrf-pipeline"
        rank_constant = self._cfg.rrf_rank_constant if self._cfg else _DEFAULT_RRF_RANK_CONSTANT

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
                            "rank_constant": rank_constant,
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
        logger.info(f"Created RRF pipeline '{pipeline_name}' (rank_constant={rank_constant})")

    async def reconcile_lexical_scoring(
        self,
        collection_name: str = "records",
        config: Optional[CollectionConfig] = None,
    ) -> Optional[str]:
        """Add the English-stemmed ``page_content`` sub-field to an older index.

        A new sub-field is a mapping change only: documents written from now on
        carry it and nothing stored is touched. Documents indexed before it are
        filled in by ``reconcile_storage_layout``, which rewrites them and so
        stays behind the operator's opt-in.
        """
        await self._assert_connected()
        if _has_stemmed_page_content(await self._index_mappings(collection_name)):
            return None
        await self.client.indices.put_mapping(  # type: ignore
            index=collection_name,
            body={"properties": {PAGE_CONTENT_FIELD: PAGE_CONTENT_MAPPING}},
        )
        logger.info(f"Added {STEMMED_PAGE_CONTENT_FIELD} to OpenSearch index '{collection_name}'")
        return STEMMED_PAGE_CONTENT_FIELD

    async def reconcile_storage_layout(
        self,
        collection_name: str = "records",
        config: Optional[CollectionConfig] = None,
    ) -> Optional[str]:
        """Backfill the stemmed sub-field on documents indexed before it existed.

        Runs as one throttled background ``_update_by_query`` per index. Each
        call advances it one step: start the task, wait while it runs, then
        record success, or forget a failed or lost task so the next call
        retries it. Only documents still missing the field are rewritten.
        """
        await self._assert_connected()
        mappings = await self._index_mappings(collection_name)
        if not _has_stemmed_page_content(mappings):
            return None

        meta = dict(mappings.get("_meta") or {})
        state = meta.get(_STEMMED_BACKFILL_META_KEY) or {}
        if state.get("done"):
            return None

        task_id = state.get("task")
        if task_id:
            try:
                task = await self.client.tasks.get(task_id=task_id)  # type: ignore
            except NotFoundError:
                task = {"completed": True, "error": "task not found"}
            if not task.get("completed"):
                return None
            failed = task.get("error") or (task.get("response") or {}).get("failures")
            if failed:
                logger.warning(
                    f"Stemmed-field backfill on '{collection_name}' did not finish "
                    f"({failed}); it will be retried"
                )
            await self._set_stemmed_backfill_state(
                collection_name, meta, {} if failed else {"done": True}
            )
            return None

        response = await self.client.update_by_query(  # type: ignore
            index=collection_name,
            body={"query": _MISSING_STEMMED_QUERY},
            params={
                "conflicts": "proceed",
                "wait_for_completion": "false",
                "requests_per_second": _STEMMED_BACKFILL_DOCS_PER_SECOND,
                "slices": "auto",
            },
        )
        await self._set_stemmed_backfill_state(
            collection_name, meta, {"task": response["task"]}
        )
        logger.info(
            f"Started {STEMMED_PAGE_CONTENT_FIELD} backfill on '{collection_name}' "
            f"(task {response['task']})"
        )
        return f"{STEMMED_PAGE_CONTENT_FIELD}.backfill"

    async def _index_mappings(self, collection_name: str) -> Dict[str, Any]:
        response = await self.client.indices.get_mapping(index=collection_name)  # type: ignore
        index_body = response.get(collection_name) or next(iter(response.values()), {})
        return index_body.get("mappings") or {}

    async def _set_stemmed_backfill_state(
        self, collection_name: str, meta: Dict[str, Any], state: Dict[str, Any]
    ) -> None:
        # ``_meta`` is replaced wholesale on update, so keep whatever else is in it.
        await self.client.indices.put_mapping(  # type: ignore
            index=collection_name,
            body={"_meta": {**meta, _STEMMED_BACKFILL_META_KEY: state}},
        )

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
        self._forget_scope_mapping(collection_name)
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
            os_type = {"keyword": "keyword", "integer": "long"}.get(field_schema.get("type"), "text")
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
        max_values: Optional[Dict[str, int]] = None,
        **kwargs: FilterValue,
    ) -> FilterExpression:
        from app.services.vector_db.filters import build_filter_expression

        return build_filter_expression(
            filter_mode,
            must=must,
            should=should,
            must_not=must_not,
            min_should_match=min_should_match,
            max_values=max_values,
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
        with_payload: Optional[List[str]] = None,
    ) -> ScrollResult:
        """Scroll a page of points.

        ``offset`` is the opaque cursor returned in ``ScrollResult.next_offset``
        from the previous call.  It is the OpenSearch ``search_after`` value
        serialised as a JSON string.  Pass ``None`` for the first page.

        ``with_payload`` maps to ``_source`` includes, so a caller that needs
        two fields does not transfer every chunk's text.
        """
        await self._assert_connected()
        bool_query = OpenSearchUtils.filter_expression_to_bool_query(scroll_filter)
        source: Dict[str, Any] = (
            {"includes": list(with_payload)}
            if with_payload
            else {"exclude": ["dense_embedding"]}
        )
        body: Dict[str, Any] = {
            "query": bool_query,
            "size": min(limit, 10000),
            "_source": source,
            "sort": [{"point_id": "asc"}],
        }

        if offset is not None:
            try:
                body["search_after"] = json.loads(offset)
            except (ValueError, TypeError):
                body["search_after"] = [offset]

        result = await self.client.search(index=collection_name, body=body)  # type: ignore
        hits = result.get("hits", {}).get("hits", [])
        if len(hits) > limit:
            hits = hits[:limit]
        points = [self._hit_to_point(hit) for hit in hits]
        # Return a cursor for the next page when the result set is full
        next_offset = None
        if len(hits) == limit and hits:
            last_sort = hits[-1].get("sort")
            if not last_sort:
                # No sort value on a full page means the documents predate the
                # point_id field, so there is nothing to anchor search_after to.
                # Returning None here would look like "last page" and silently
                # drop the remainder.
                raise RuntimeError(
                    f"Cannot page '{collection_name}': documents have no "
                    f"'point_id' to sort on. This index was written before "
                    f"point_id existed — reindex it to enable scrolling."
                )
            next_offset = json.dumps(last_sort)

        return ScrollResult(points=points, next_offset=next_offset)

    @staticmethod
    def _hit_to_point(hit: Dict[str, Any]) -> VectorPoint:
        source = hit.get("_source", {})
        return VectorPoint(
            id=hit["_id"],
            payload={
                "metadata": source.get("metadata", {}),
                "page_content": source.get("page_content", ""),
                CONNECTOR_IDS_FIELD: list(source.get(CONNECTOR_IDS_FIELD) or []),
                RECORD_GROUP_IDS_FIELD: list(source.get(RECORD_GROUP_IDS_FIELD) or []),
                ROOT_RECORD_GROUP_IDS_FIELD: list(source.get(ROOT_RECORD_GROUP_IDS_FIELD) or []),
                **{k: source[k] for k in SCOPE_KEYS if k in source},
            },
        )

    async def retrieve_points(
        self,
        collection_name: str,
        ids: List[str],
    ) -> List[VectorPoint]:
        """``mget`` is realtime: it sees documents indexed since the last refresh."""
        await self._assert_connected()
        if not ids:
            return []
        result = await self.client.mget(  # type: ignore
            index=collection_name,
            body={"ids": list(ids)},
            _source_excludes=["dense_embedding"],
        )
        return [self._hit_to_point(doc) for doc in result.get("docs", []) if doc.get("found")]

    async def _clause_limit(self) -> int:
        """The cluster's ``indices.query.bool.max_clause_count`` (1024 unless set), read once. If it cannot be read the default is assumed: a
        smaller assumed limit only refuses a filter, it never sends a search that fails."""
        if self.__dict__.get("_max_clause_count") is None:
            limit = 1024
            try:
                got = await self.client.cluster.get_settings(include_defaults=True, flat_settings=True)  # type: ignore
                key = "indices.query.bool.max_clause_count"
                limit = int(next((got[k][key] for k in ("transient", "persistent", "defaults") if key in (got.get(k) or {})), 1024))
            except Exception as exc:
                logger.warning(f"OpenSearch max_clause_count not read, assuming 1024: {exc}")
            self.__dict__["_max_clause_count"] = limit
        return self.__dict__["_max_clause_count"]

    async def query_nearest_points(
        self,
        collection_name: str,
        requests: List[HybridSearchRequest],
    ) -> List[List[SearchResult]]:
        await self._assert_connected()
        reject_scope_filter(requests, "OpenSearch")
        pipeline_name = f"{collection_name}-rrf-pipeline"

        has_hybrid = any(
            r.dense_query is not None and r.text_query is not None
            for r in requests
        )
        if has_hybrid:
            await self._ensure_rrf_pipeline(collection_name)

        async def _one_search(req: HybridSearchRequest) -> List[SearchResult]:
            body = OpenSearchUtils.build_hybrid_query(req, await self._clause_limit() if req.scope_filter is not None else 1024)
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

        # Agent retrieval issues one request per source, so this fan-out is
        # caller-controlled and unbounded. Cap it so a wide query cannot open an
        # arbitrary number of concurrent searches against the cluster.
        max_concurrent = (
            self._cfg.max_concurrent_searches
            if self._cfg
            else _DEFAULT_MAX_CONCURRENT_SEARCHES
        )
        semaphore = asyncio.Semaphore(max_concurrent)

        async def _bounded(req: HybridSearchRequest) -> List[SearchResult]:
            async with semaphore:
                return await _one_search(req)

        return list(await asyncio.gather(*[_bounded(r) for r in requests]))

    async def upsert_points(
        self,
        collection_name: str,
        points: List[VectorPoint],
        batch_size: int = 500,
        refresh: bool = False,
    ) -> None:
        """Bulk-upsert vector points into an OpenSearch index.

        Performance notes
        -----------------
        This method is intentionally lean.  The indexing pipeline calls it many
        times concurrently with small batches (see ``_process_document_chunks``
        in ``vectorstore.py``), so any per-call index-wide work is both racy and
        costly:

        - It does **not** toggle ``index.refresh_interval``.  Doing that per
          call races across concurrent upserts (one call re-enables refresh
          while another is still bulk-loading) and adds two cluster-state
          round-trips per batch.
        - It does **not** force an ``indices.refresh()`` per call.  A forced
          refresh creates a new Lucene segment every batch — the exact segment
          proliferation we want to avoid.  Searchability is instead handled by
          the index's ``refresh_interval`` (set to ``30s`` at collection creation),
          which naturally batches documents across concurrent upserts.
        - ``refresh`` defaults to ``False``.  Pass ``refresh=True`` only when the
          caller needs the documents to be immediately searchable on return
          (e.g. a read-after-write test).  Post-bulk consolidation is not needed:
          the merge policy keeps the segment count low continuously.
        """
        await self._assert_connected()
        if not await self._guard_scope_mapping(collection_name, {k for p in points for k in p.payload}):
            points = [replace(p, payload=self._without_scope(p.payload)) for p in points]
        start = time.perf_counter()
        logger.debug(
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

        started = time.monotonic()
        try:
            await os_helpers.async_bulk(
                self.client,
                actions,
                chunk_size=batch_size,
                raise_on_error=True,
                refresh=refresh,
            )
        except BaseException:
            # Earlier chunks may be indexed and unrefreshed; a later delete must still see them.
            self._note_unrefreshed(collection_name, points)
            raise
        if refresh and actions:  # an empty batch sends no bulk request, so no refresh ran
            self._forget_refreshed(collection_name, started)
        else:
            self._note_unrefreshed(collection_name, points)

        elapsed = time.perf_counter() - start
        logger.info(
            f"Upsert complete: {len(points)} points in {elapsed:.2f}s "
            f"({len(points)/elapsed:.0f} pts/s)"
        )

    def _forget_refreshed(self, collection_name: str, refreshed_at: float) -> None:
        """Drop the pending writes a refresh that began at ``refreshed_at`` covered; a write noted after it
        (by a concurrent caller) may not be in that refresh and stays."""
        pending = self._unrefreshed.get(collection_name)
        if pending is None:
            return
        for key in [k for k, t in pending.items() if t <= refreshed_at]:
            pending.pop(key, None)
        if not pending:
            self._unrefreshed.pop(collection_name, None)

    def _note_unrefreshed(self, collection_name: str, points: list[VectorPoint]) -> None:
        now = time.monotonic()
        pending = self._unrefreshed.setdefault(collection_name, {})
        for p in points:
            meta = (p.payload or {}).get("metadata") or {}
            pending[meta.get("virtualRecordId") or _ANY] = now
        window = self._visible_after.get(collection_name, (_DEFAULT_VISIBLE_AFTER_SECONDS, 0.0))[0]
        if len(pending) > _UNREFRESHED_MAX and window is not None:  # old entries are visible by now
            for key in [k for k, t in pending.items() if now - t > window]:
                pending.pop(key, None)

    async def _visible_after_seconds(self, collection_name: str) -> float | None:
        """How long after a write the index shows it without a refresh from us: its ``refresh_interval`` plus a
        margin; None when the index never refreshes by itself (-1) or the setting cannot be read."""
        now = time.monotonic()
        cached = self._visible_after.get(collection_name)
        if cached is not None and now - cached[1] < _INTERVAL_REREAD_SECONDS:
            return cached[0]
        try:
            reply = await self.client.indices.get_settings(  # type: ignore
                index=collection_name, name="index.refresh_interval", include_defaults=True,
            )
            body = next(iter(reply.values()))
            raw = ((body.get("settings") or {}).get("index") or {}).get("refresh_interval") or (
                (body.get("defaults") or {}).get("index") or {}
            ).get("refresh_interval")
            seconds = _interval_seconds(raw)
            window = None if seconds is None else seconds + _VISIBLE_MARGIN_SECONDS
        except Exception:
            window = None
        self._visible_after[collection_name] = (window, now)
        return window

    async def _needs_refresh_before_delete(self, collection_name: str, filter: FilterExpression) -> bool:  # noqa: A002
        """Whether a point the index has not refreshed yet could match the filter."""
        window = await self._visible_after_seconds(collection_name)
        if window is None:
            return True  # no automatic refresh to rely on: every delete refreshes
        pending = self._unrefreshed.get(collection_name)
        if not pending:
            return False
        now = time.monotonic()
        live = {k for k, t in pending.items() if now - t <= window}
        if not live:
            self._unrefreshed.pop(collection_name, None)
            return False
        pinned: set = set()
        for cond in filter.must:
            if cond.key == "metadata.virtualRecordId":
                # Only real ids pin: a length-bound condition (values_count_lte) carries none.
                if cond.values is not None:
                    pinned.update(cond.values)
                elif cond.value is not None:
                    pinned.add(cond.value)
        # A filter that names no virtual record (entities, a connector) could match any pending point.
        return not pinned or _ANY in live or bool(pinned & live)

    async def delete_points(
        self,
        collection_name: str,
        filter: FilterExpression,
        refresh: bool = False,
    ) -> None:
        if filter.is_empty():
            raise ValueError(
                "delete_points called with an empty filter — this would wipe the entire "
                "index. Populate at least one filter condition (e.g. virtualRecordId)."
            )
        if not filter.has_positive_match():
            raise ValueError(
                "delete_points called with only array-length conditions — a point "
                "whose field is absent satisfies those too, so this would delete "
                "most of the index. Pair it with a value match (e.g. connectorIds)."
            )
        require_filter_org(filter)
        await self._assert_connected()
        bool_query = OpenSearchUtils.filter_expression_to_bool_query(filter)
        # ``refresh`` (after the delete) is opt-in: the connector cleanup asks for it because it re-reads
        # the matched set to decide when it is done; per-record deletes must not.
        # The query runs on the last refreshed view (the index refreshes every 30 s on purpose). A
        # document indexed since then is invisible to it and would stay searchable after the delete,
        # so the index is refreshed first; deletes are rare next to writes, so the cost is accepted.
        # A document rewritten after that refresh is a version conflict that ``conflicts="proceed"``
        # would skip: a conflict refreshes and goes again, and one that does not clear raises, never
        # a silent partial delete.
        if await self._needs_refresh_before_delete(collection_name, filter):
            refreshed_at = time.monotonic()
            await self.client.indices.refresh(index=collection_name)  # type: ignore
            self._forget_refreshed(collection_name, refreshed_at)
        for attempt in range(_DELETE_CONFLICT_RETRIES + 1):
            response = await self.client.delete_by_query(  # type: ignore
                index=collection_name,
                body={"query": bool_query},
                conflicts="proceed",
                slices="auto",
                wait_for_completion=True,
                refresh=refresh,
            )
            conflicts = (response or {}).get("version_conflicts") or 0
            if not conflicts:
                break
            if attempt == _DELETE_CONFLICT_RETRIES:
                raise RuntimeError(
                    f"delete_points on '{collection_name}' left {conflicts} document(s) behind: "
                    f"version conflicts did not clear after {_DELETE_CONFLICT_RETRIES} retries"
                )
            await self.client.indices.refresh(index=collection_name)  # type: ignore
        logger.info(f"Deleted points from OpenSearch index '{collection_name}'")

    async def overwrite_payload(
        self,
        collection_name: str,
        payload: dict,
        points: FilterExpression,
        refresh: bool = False,
    ) -> None:
        """Update fields in matched documents via a Painless script.

        Dotted keys like ``"metadata.status"`` are correctly expanded to nested
        field access (``ctx._source.metadata.status``), not treated as literal
        top-level key names.
        """
        if points.is_empty():
            raise ValueError(
                "overwrite_payload called with an empty filter — this would rewrite "
                "every document in the index. Populate at least one filter condition "
                "(e.g. virtualRecordId)."
            )
        require_filter_org(points)
        keep_org_in_overwrite(payload, points)
        await self._assert_connected()
        if not await self._guard_scope_mapping(collection_name, payload):
            payload = self._without_scope(payload)
            if not payload:
                return
        bool_query = OpenSearchUtils.filter_expression_to_bool_query(points)
        script_parts = []
        params: Dict[str, Any] = {}
        for key, value in payload.items():
            # Sanitise param name: replace non-alphanumeric chars with underscores
            param_name = "p_" + re.sub(r"[^a-zA-Z0-9]", "_", key)
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
            # A version conflict means the document was rewritten underneath us —
            # for membership that is a concurrent reindex, which already wrote the
            # current arrays. Default "abort" would fail the whole update for the
            # rest of the matched documents over a doc that is already correct.
            conflicts="proceed",
            # Opt-in, same reason as delete_points.
            refresh=refresh,
            body={
                "query": bool_query,
                "script": {
                    "lang": "painless",
                    "source": "; ".join(script_parts),
                    "params": params,
                },
            },
        )

    async def set_payload(
        self,
        collection_name: str,
        payload: dict,
        filter: FilterExpression,
        refresh: bool = False,
    ) -> None:
        if filter.is_empty():
            raise ValueError(
                "set_payload called with an empty filter — this would update the entire "
                "index. Populate at least one filter condition (e.g. virtualRecordId)."
            )
        require_filter_org(filter)
        forbid_org_in_payload(payload)
        await self.overwrite_payload(collection_name, payload, filter, refresh=refresh)

    async def update_payload_by_ids(
        self,
        collection_name: str,
        point_ids: List[str],
        payload: dict,
        *,
        org_id: str,
    ) -> None:
        """A bulk partial update by ``_id`` reaches documents the index has
        not refreshed yet, which ``update_by_query`` cannot. Only the points of
        ``org_id`` are touched."""
        await self._assert_connected()
        if is_missing(org_id):
            raise OrgRequired("update_payload_by_ids needs the org of the points")
        forbid_org_in_payload(payload)
        if not point_ids:
            return
        if not await self._guard_scope_mapping(collection_name, payload):
            payload = self._without_scope(payload)
            if not payload:
                return
        doc = OpenSearchUtils.nest_dotted_keys(payload)
        # A scripted update checks the org on the document itself (a bulk update by _id sees unrefreshed
        # writes) and is a noop for another org's id; it merges objects recursively like a partial doc.
        actions = [
            {
                "_op_type": "update", "_index": collection_name, "_id": point_id,
                "script": {"lang": "painless", "source": _MERGE_IF_ORG_SCRIPT, "params": {"org": org_id, "doc": doc}},
                "retry_on_conflict": _UPDATE_RETRY_ON_CONFLICT,
            }
            for point_id in point_ids
        ]
        _, errors = await os_helpers.async_bulk(
            self.client, actions, raise_on_error=False, refresh=False,
        )
        # A missing id is ignored by contract; any other item error is a lost write.
        failed = [e for e in errors if (e.get("update") or {}).get("status") != 404]
        if failed:
            raise RuntimeError(
                f"update_payload_by_ids on '{collection_name}' failed for "
                f"{len(failed)} of {len(point_ids)} point(s): {failed[0]}"
            )

    # ------------------------------------------------------------------
    # Performance utilities
    # ------------------------------------------------------------------

    async def force_merge(
        self,
        collection_name: str,
        max_segments: int = 1,
    ) -> None:
        """Merge Lucene segments to improve search performance.

        Lucene (and by extension OpenSearch's k-NN engine) searches each
        segment independently and merges the per-segment results.  Reducing
        to a single segment eliminates this overhead and typically yields
        ~2× QPS improvement.

        Explicit operator action only — never call this on a schedule.

        Merging to one segment produces a segment larger than
        ``index.merge.policy.max_merged_segment``, which TieredMergePolicy then
        excludes from ordinary merging: deleted documents inside it are no longer
        reclaimed by normal merges. That is an acceptable trade for an index that
        will not be written again, and a bad one for a live index — which is what
        ``segments_per_tier`` below is tuned for instead.

        Blocking; ``request_timeout=600`` allows up to 10 minutes. The merge
        continues server-side even if that HTTP wait times out.
        """
        await self._assert_connected()
        await self.client.indices.forcemerge(  # type: ignore
            index=collection_name,
            max_num_segments=max_segments,
            request_timeout=600,
        )
        # Searches keep reading the pre-merge segments until the next refresh,
        # up to the index's 30s refresh_interval, so publish the merge now.
        await self.client.indices.refresh(index=collection_name)  # type: ignore
        logger.info(
            f"Force-merged '{collection_name}' to {max_segments} segment(s)"
        )

    async def warmup(self, collection_name: str) -> None:
        """Load k-NN native library indexes into the OS page cache.

        The Lucene HNSW engine uses memory-mapped files.  The first query
        after a restart or after a force-merge triggers I/O to load the graph
        from disk into the OS page cache, causing multi-second latency.  This
        call pre-loads the graphs so subsequent queries are CPU-bound.

        Requires the OpenSearch k-NN plugin (standard in all OpenSearch
        distributions).
        """
        await self._assert_connected()
        await self.client.transport.perform_request(  # type: ignore
            "GET", f"/_plugins/_knn/warmup/{collection_name}"
        )
        logger.info(f"k-NN warmup complete for '{collection_name}'")

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


