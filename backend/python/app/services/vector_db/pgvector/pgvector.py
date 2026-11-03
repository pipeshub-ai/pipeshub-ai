"""PostgreSQL + pgvector provider: lifecycle, catalog and collections.

Every statement runs in a unit of work of the shared database runtime (``app.core.db``): the org is set per transaction and row-level
security does the rest. This change carries the lifecycle (connect, health, capabilities), the collection catalog and the per-org
space questions; points are written, read and searched by the changes that follow, and until then those methods raise
``NotImplementedError``. Nothing selects the provider yet (no ``VECTOR_DB_TYPE`` entry).
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING

from app.config.constants.service import config_node_constants
from app.core.db import CapabilityError
from app.core.db.shared import acquire_db, release_db
from app.services.vector_db.filters import (
    build_filter_expression,
    build_generic_conditions,
)
from app.services.vector_db.interface.vector_db import IVectorDBService
from app.services.vector_db.models import (
    CollectionConfig,
    FilterExpression,
    FilterMode,
    FilterValue,
    FusionMethod,
    HealthStatus,
    HybridSearchRequest,
    ScoreSemantics,
    ScrollResult,
    SearchFailureKind,
    SearchResult,
    VectorCollectionInfo,
    VectorDBCapabilities,
    VectorDBHealth,
    VectorPoint,
)
from app.services.vector_db.pgvector import reads
from app.services.vector_db.pgvector.catalog import Catalog, CollectionRow
from app.services.vector_db.pgvector.config import PgVectorConfig
from app.services.vector_db.pgvector.errors import (
    CollectionAlreadyExistsError,
    PgVectorError,
    PgVectorUnsupportedError,
    classify_pg_search_error,
)
from app.services.vector_db.pgvector.placement import OwnLeafPolicy, PlacementPolicy
from app.services.vector_db.pgvector.probe import PgCapabilities, evaluate
from app.services.vector_db.pgvector.scope import ambient_org, org_scope, require_org
from app.utils.logger import create_logger

if TYPE_CHECKING:
    from app.config.configuration_service import ConfigurationService
    from app.core.db import KernelDB, Tx

logger = create_logger("pgvector_service")

_CAPABILITIES = VectorDBCapabilities(
    supports_sparse_vectors=True,
    supports_server_side_text_search=False,
    supported_fusion_methods=[FusionMethod.RRF],
    supports_multi_collection=True,
    score_semantics=ScoreSemantics.RANK_FUSED,
    stamps_in_catalog=True,
    spaces_per_org=True,
)
UNKNOWN_MODEL = "unknown"      # the model name of a space adopted from points that do not name theirs


def _pending(method: str) -> NotImplementedError:
    return NotImplementedError(f"pgvector: {method} is not implemented yet")


class PgVectorService(IVectorDBService):
    def __init__(self, config: ConfigurationService | PgVectorConfig) -> None:
        self.config_service = config
        self._cfg: PgVectorConfig | None = None
        self._db: KernelDB | None = None
        self._catalog: Catalog | None = None
        self.pg_capabilities: PgCapabilities | None = None
        self.placement: PlacementPolicy | None = None     # None: no policy (rows stay in the shared tree); see PgVectorConfig.placement
        self._own_placement = False                        # connect() made the policy (it holds this connection's database), so disconnect() drops it
        self._lifecycle = asyncio.Lock()                   # one connect or disconnect at a time: two connects must not both acquire the runtime

    @classmethod
    async def create(
        cls, config: ConfigurationService | PgVectorConfig
    ) -> PgVectorService:
        service = cls(config)
        await service.connect()
        return service

    # Lifecycle ---------------------------------------------------------

    async def _load_config(self) -> PgVectorConfig:
        if isinstance(self.config_service, PgVectorConfig):
            return self.config_service
        raw = await self.config_service.get_config(config_node_constants.PGVECTOR.value)
        return PgVectorConfig.from_dict(raw if isinstance(raw, dict) else None)

    async def connect(self) -> None:
        async with self._lifecycle:
            if self._db is not None:
                return
            cfg = await self._load_config()
            try:
                db = await acquire_db(cfg.to_db_config())
            except CapabilityError as e:
                raise PgVectorUnsupportedError(str(e)) from e
            try:
                version = db.capabilities.versions.get("vector") if db.capabilities else None
                if version is None:
                    raise PgVectorUnsupportedError(
                        "pgvector extension is not installed in this database "
                        "(run CREATE EXTENSION vector WITH SCHEMA ext as a privileged role)"
                    )
                self.pg_capabilities = evaluate(db.capabilities.pg_major, version, strict=cfg.strict)
            except BaseException:
                await release_db(cfg.to_db_config())
                raise
            self._cfg, self._db, self._catalog = cfg, db, Catalog(db, cfg.catalog_ttl)
            if self.placement is None and cfg.placement == "own_leaf":
                self.placement, self._own_placement = OwnLeafPolicy(db, wait=cfg.placement_wait), True
            logger.info(
                "pgvector provider ready (PostgreSQL %s, pgvector %s, safe_mode=%s)",
                self.pg_capabilities.pg_major,
                self.pg_capabilities.pgvector_version,
                self.pg_capabilities.hnsw_safe_mode,
            )

    async def disconnect(self) -> None:
        async with self._lifecycle:
            if self._db is None:
                return
            cfg, self._db, self._catalog = self._cfg, None, None
            if self._own_placement:
                self.placement, self._own_placement = None, False
            await release_db(cfg.to_db_config())

    def _assert_connected(self) -> tuple[KernelDB, Catalog]:
        if self._db is None or self._catalog is None:
            raise PgVectorError("pgvector provider is not connected")
        return self._db, self._catalog

    @property
    def _safe_mode(self) -> bool:
        return bool(self.pg_capabilities and self.pg_capabilities.hnsw_safe_mode)

    # Identity, capabilities, health -------------------------------------

    def get_service_name(self) -> str:
        return "pgvector"

    def get_service(self) -> PgVectorService:
        return self

    def get_service_client(self) -> object:
        return self._assert_connected()[0]

    def get_capabilities(self) -> VectorDBCapabilities:
        return _CAPABILITIES

    async def get_collection_stamp(self, collection_name: str) -> str | None:
        _, catalog = self._assert_connected()
        return await catalog.stamp(collection_name)

    async def set_collection_stamp(self, collection_name: str, stamp: str) -> str:
        _, catalog = self._assert_connected()
        return await catalog.set_stamp(collection_name, stamp)

    async def orgs_of_virtual_records(self, virtual_record_ids: list[str]) -> dict[str, set[str]] | None:
        _, catalog = self._assert_connected()
        return await catalog.orgs_of_vrids(virtual_record_ids)

    def transient_search_error(self, error: BaseException) -> SearchFailureKind | None:
        return classify_pg_search_error(error)

    async def health_check(self) -> VectorDBHealth:
        if self._db is None:
            return VectorDBHealth(
                status=HealthStatus.UNHEALTHY, message="Not connected"
            )
        start = time.monotonic()
        try:
            await self._catalog.collections()
        except Exception as e:
            logger.warning("pgvector health check failed: %s", e)       # the detail (host, port, driver text) stays in the log
            return VectorDBHealth(status=HealthStatus.UNHEALTHY, message=f"catalog unreachable ({type(e).__name__})")
        return VectorDBHealth(
            latency_ms=round((time.monotonic() - start) * 1000, 2),
            server_version=f"pgvector {self.pg_capabilities.pgvector_version}",
        )

    # Filters ------------------------------------------------------------

    async def filter_collection(
        self,
        filter_mode: str | FilterMode = FilterMode.MUST,
        must: dict[str, FilterValue] | None = None,
        should: dict[str, FilterValue] | None = None,
        must_not: dict[str, FilterValue] | None = None,
        min_should_match: int | None = None,
        max_values: dict[str, int] | None = None,
        **filters: FilterValue,
    ) -> FilterExpression:
        return build_filter_expression(
            filter_mode,
            must=must,
            should=should,
            must_not=must_not,
            min_should_match=min_should_match,
            max_values=max_values,
            extra_kwargs=filters or None,
            build_conditions=build_generic_conditions,
        )

    # Collections ---------------------------------------------------------

    async def create_collection(
        self, collection_name: str = "records", config: CollectionConfig | None = None
    ) -> None:
        """Adds the collection to the catalog (a jobs path). An org's embedding space is not created here: the first write of
        an org adopts the space of its vectors."""
        _, catalog = self._assert_connected()
        cfg = config or CollectionConfig()
        if await catalog.collection(collection_name) is not None:
            raise CollectionAlreadyExistsError(collection_name)
        await catalog.create_collection(
            collection_name, sparse_enabled=cfg.enable_sparse, sparse_idf=cfg.sparse_idf
        )

    async def get_collections(self) -> list[CollectionRow]:
        return await self._assert_connected()[1].collections()

    async def get_collection(self, collection_name: str) -> CollectionRow:
        return await self._assert_connected()[1].require_collection(collection_name)

    async def get_collection_info(self, collection_name: str) -> VectorCollectionInfo:
        """Dimension and point count are the calling org's (its active space); with no org in context only existence is known."""
        db, catalog = self._assert_connected()
        row = await catalog.collection(collection_name)
        if row is None:
            return VectorCollectionInfo(name=collection_name, exists=False)
        org = ambient_org()
        space = await catalog.active_space(org) if org else None
        if space is None:
            return VectorCollectionInfo(name=collection_name, exists=True)
        points = await db.run(lambda tx: reads.count(tx, org, row.collection_id, space.space_id), custom_plan=True)
        return VectorCollectionInfo(
            name=collection_name, exists=True, dense_dimension=space.dim, points_count=points
        )

    async def org_space_accepts(self, identity: str | None, dimension: int, current_identity: str | None = None) -> bool:
        """Whether changing to ``identity`` (``provider:model:dimension``) of ``dimension`` fits the org's active space. A named space
        takes its own model only. A nameless space (written before records were stamped) that holds points is read as named by the
        model configured now (``current_identity``): what the config says is what wrote them. Without it, it takes any model."""
        space = await self._assert_connected()[1].active_space(require_org(ambient_org()))
        if space is None or space.dim != dimension:
            return False
        if space.effective_model != UNKNOWN_MODEL:
            return space.effective_model == identity
        if not await self.org_holds_points():
            return True
        return identity is not None and identity == current_identity

    async def org_holds_points(self) -> bool:
        db, _ = self._assert_connected()
        org = require_org(ambient_org())

        async def read(tx: Tx) -> bool:
            return await tx.fetchval("/* pipeshub:pgvector.org_holds_points */ SELECT EXISTS (SELECT 1 FROM retrieval.points WHERE org_id = $1)", org)

        with org_scope(org):
            return bool(await db.run(read, custom_plan=True))

    async def collection_exists(self, collection_name: str) -> bool:
        return await self._assert_connected()[1].collection(collection_name) is not None

    async def delete_collection(self, collection_name: str) -> None:
        """Hides the collection at once (a jobs path); its rows are unreachable and the maintenance job removes them. A collection
        that is not there is not an error, as on the other providers."""
        catalog = self._assert_connected()[1]
        if not await catalog.mark_deleting(collection_name):
            logger.info("pgvector collection '%s' was not there to delete", collection_name)

    async def create_index(
        self, collection_name: str, field_name: str, field_schema: dict
    ) -> None:
        """Accepted and ignored: the indexes are fixed by the schema (promoted columns, the membership table)."""
        return

    # Points: written, read and searched by the changes that follow ----------------------------------------------

    async def scroll(self, collection_name: str, scroll_filter: FilterExpression, limit: int, offset: str | None = None,
                     with_payload: list[str] | None = None) -> ScrollResult:
        raise _pending("scroll")

    async def retrieve_points(self, collection_name: str, ids: list[str]) -> list[VectorPoint]:
        raise _pending("retrieve_points")

    async def upsert_points(self, collection_name: str, points: list[VectorPoint]) -> None:
        raise _pending("upsert_points")

    async def delete_points(self, collection_name: str, filter: FilterExpression, refresh: bool = False) -> None:  # noqa: A002, FBT001, FBT002
        raise _pending("delete_points")

    async def update_payload_by_ids(self, collection_name: str, point_ids: list[str], payload: dict) -> None:
        raise _pending("update_payload_by_ids")

    async def overwrite_payload(self, collection_name: str, payload: dict, points: FilterExpression) -> None:
        raise _pending("overwrite_payload")

    async def set_payload(self, collection_name: str, payload: dict, filter: FilterExpression, refresh: bool = False) -> None:  # noqa: A002, FBT001, FBT002
        raise _pending("set_payload")

    async def query_nearest_points(self, collection_name: str, requests: list[HybridSearchRequest]) -> list[list[SearchResult]]:
        raise _pending("query_nearest_points")
