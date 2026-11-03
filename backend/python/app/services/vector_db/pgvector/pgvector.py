"""PostgreSQL + pgvector provider: lifecycle, catalog, collections, writes, reads and dense search.

Every statement runs in a unit of work of the shared database runtime (``app.core.db``): the org is set per transaction and row-level
security does the rest. Carried so far: the lifecycle (connect, health, capabilities), the collection catalog, the per-org space
questions, the writes (upsert, delete, payload and membership), the reads (scroll, retrieve) and the dense leg of a search. A request with a
lexical query follows, and until then raises ``NotImplementedError``. Nothing selects the provider yet (no ``VECTOR_DB_TYPE`` entry).
"""

from __future__ import annotations

import asyncio
import dataclasses
import time
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import asyncpg

from app.config.constants.service import config_node_constants
from app.core import org_context
from app.core.db import CapabilityError, DbError
from app.core.db.shared import acquire_db, release_db
from app.modules.retrieval import degraded_notice
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
from app.services.vector_db.pgvector import dense, fusion, reads, validate, writes
from app.services.vector_db.pgvector.catalog import Catalog, CollectionRow, OrgSpace
from app.services.vector_db.pgvector.config import PgVectorConfig
from app.services.vector_db.pgvector.errors import (
    CollectionAlreadyExistsError,
    OrgMismatch,
    OrgRequired,
    PgVectorError,
    PgVectorUnsupportedError,
    SpaceMismatch,
    classify_pg_search_error,
)
from app.services.vector_db.pgvector.placement import OwnLeafPolicy, PlacementPolicy
from app.services.vector_db.pgvector.probe import PgCapabilities, evaluate
from app.services.vector_db.pgvector.sampler import Sample, Sampler
from app.services.vector_db.pgvector.scope import (
    ambient_or_required,
    ambient_org,
    org_scope,
    require_org,
)
from app.services.vector_db.pgvector.sql_filters import compile_filter, single_org
from app.services.vector_db.pgvector.visibility import scope_of
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
_MAX_LEG = 1000       # rows one leg of a search returns at most
MAX_SCROLL_LIMIT = 10_000   # rows one scroll page returns at most: the ceiling the other backends have (OpenSearch caps a page at 10,000)
UNKNOWN_MODEL = "unknown"      # the model name of a space adopted from points that do not name theirs


def _pending(method: str) -> NotImplementedError:
    return NotImplementedError(f"pgvector: {method} is not implemented yet")


def _ids(point_ids: list[str]) -> list[uuid.UUID]:
    out = []
    for i in point_ids:
        try:
            out.append(uuid.UUID(str(i)))
        except ValueError:
            continue          # an id that is no UUID names no point
    return out


def _match(spaces: list[OrgSpace], dim: int, model: str | None) -> OrgSpace | None:
    """The space vectors of ``dim`` (from ``model``, when named) belong in: the model's own space, else one adopted without a name
    (a nameless space takes any model of its dimension until a stamped write names it, see ``_claim_name``); with no model, the active
    space."""
    fits = [s for s in spaces if s.role in ("active", "shadow") and s.dim == dim]
    if model:
        return next((s for s in fits if s.effective_model == model), None) or next((s for s in fits if s.effective_model == UNKNOWN_MODEL), None)
    return next((s for s in fits if s.role == "active"), None)


def _filter_org(expr: FilterExpression) -> str:
    return require_org(single_org(expr))


class PgVectorService(IVectorDBService):
    def __init__(self, config: ConfigurationService | PgVectorConfig) -> None:
        self.config_service = config
        self._cfg: PgVectorConfig | None = None
        self._db: KernelDB | None = None
        self._catalog: Catalog | None = None
        self.pg_capabilities: PgCapabilities | None = None
        self.placement: PlacementPolicy | None = None     # None: no policy (rows stay in the shared tree); see PgVectorConfig.placement
        self._sizer: dense.Sizer | None = None
        self._sampler: Sampler | None = None
        self._bq: dict[tuple[str, int], bool] = {}        # (org, space): does bit HNSW + rerank pass its recall check? Absent: not checked yet
        self._bq_at: dict[tuple[str, int], float] = {}    # when this process last read (or started reading) the stored verdict
        self._checks: set[asyncio.Task] = set()
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
            self._sizer, self._sampler = dense.Sizer(db, cfg.search), Sampler(db, cfg.search)
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
            for task in self._checks:
                task.cancel()
            if self._sampler is not None:
                await self._sampler.close()
            self._sizer = self._sampler = None
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

    # Spaces --------------------------------------------------------------

    async def _space_for_write(self, org: str, dim: int, models: set[str]) -> OrgSpace:
        """The org's space for vectors of ``dim`` (and the named model): an active or shadow space that matches, else, when the
        org has no active space yet, the space the vectors themselves define. Anything else is a SpaceMismatch."""
        catalog = self._assert_connected()[1]
        if len(models) > 1:
            raise SpaceMismatch("a batch of points must come from one embedding model")
        model = next(iter(models), None)
        spaces = await catalog.org_spaces(org)
        found = _match(spaces, dim, model)
        if found:
            return await self._claim_name(catalog, org, found, model)
        if any(s.role == "active" for s in spaces):
            # The org's space is another model's or dimension: while the org holds no point it takes the space this write defines,
            # named as a first write names it (the database decides; a point that exists or lands meanwhile makes it refuse).
            await catalog.replace_empty_space(org, model or UNKNOWN_MODEL, dim)
            # Whatever the answer, read the spaces again: a concurrent writer of this very model may have won the replacement.
            catalog.forget(org=org)
            found = _match(await catalog.org_spaces(org), dim, model)
            if found:
                return await self._claim_name(catalog, org, found, model)
            raise SpaceMismatch(
                f"the organisation has no embedding space for {model or 'unnamed'}-model vectors of dimension {dim}"
            )
        # No active space in our (possibly stale) view: adopt one. A concurrent first write may have adopted another, so the
        # space that comes back is matched again, exactly like a cached one.
        space = await catalog.activate_space(org, model or UNKNOWN_MODEL, dim)
        found = _match([space], dim, model)
        if found is None:
            raise SpaceMismatch(
                f"the organisation's embedding space is {space.effective_model} with dimension {space.dim}, not {model or 'unnamed'}-model vectors of dimension {dim}"
            )
        return found

    @staticmethod
    async def _claim_name(catalog: Catalog, org: str, space: OrgSpace, model: str | None) -> OrgSpace:
        """A nameless space (adopted by a write that carried no model: points of releases before records were stamped) takes any
        model of its dimension until the first stamped write names it; from then on it takes that model only."""
        if not model or space.model != UNKNOWN_MODEL or space.named_by:
            return space
        if (named := await catalog.name_space(org, model)) != model:
            raise SpaceMismatch(f"the organisation's embedding space is {named}, not {model}")
        return dataclasses.replace(space, named_by=model)

    async def _active_space(self, org: str) -> OrgSpace | None:
        return await self._assert_connected()[1].active_space(org)

    # Reads and search: the changes that follow ---------------------------------------------------------------------

    # Reads ---------------------------------------------------------------------------------------------------------

    async def scroll(
        self,
        collection_name: str,
        scroll_filter: FilterExpression,
        limit: int,
        offset: str | None = None,
        with_payload: list[str] | None = None,
    ) -> ScrollResult:
        """A page of the org's points that match the filter, ordered by insertion; the filter must pin ``metadata.orgId``. The offset is
        opaque and belongs to the org that got it. A missing collection is an error; an org with no embedding space yet has no points."""
        db, catalog = self._assert_connected()
        org = _filter_org(scroll_filter)
        if not 1 <= limit <= MAX_SCROLL_LIMIT:
            raise ValueError(f"scroll limit must be between 1 and {MAX_SCROLL_LIMIT}")
        coll = await catalog.require_collection(collection_name)
        with org_scope(org):
            space = await catalog.active_space(org)
            if space is None:
                return ScrollResult(points=[], next_offset=None)
            compiled = compile_filter(scroll_filter, first_param=5)
            points, nxt = await db.run(
                lambda tx: reads.scroll(tx, org, coll.collection_id, space.space_id, compiled, limit, offset, with_payload or None),
                custom_plan=True,
            )
        return ScrollResult(points=points, next_offset=nxt)

    async def retrieve_points(self, collection_name: str, ids: list[str]) -> list[VectorPoint]:
        """The points with these ids in the request's org, in the order asked; ids that name no point (or are no UUID) are left out.
        The interface carries no org: it is the request's."""
        db, catalog = self._assert_connected()
        org = ambient_or_required()
        coll = await catalog.require_collection(collection_name)
        space = await catalog.active_space(org)
        wanted = _ids(ids)
        if space is None or not wanted:
            return []
        found = await db.run(lambda tx: reads.retrieve(tx, org, coll.collection_id, space.space_id, wanted), custom_plan=True)
        position = {str(i): n for n, i in enumerate(wanted)}
        return sorted(found, key=lambda p: position.get(str(p.id), len(position)))

    # Search ----------------------------------------------------------------------------------------------------------

    async def query_nearest_points(
        self, collection_name: str, requests: list[HybridSearchRequest]
    ) -> list[list[SearchResult]]:
        """The dense leg of each request, ranked as the other providers rank a one-leg answer (reciprocal-rank score ``1 / (2 + index)``,
        ties by id, ``limit`` results). Every request names its org in its filter and only that org's points in its embedding space are
        searched; an empty accessible list in the filter finds nothing. The requests of a call run a few at a time. A leg that cannot run
        in its tier is answered by the bounded exact path, counted, logged and flagged on every result (``SearchResult.degraded``).
        A request with a lexical (sparse) query is not served yet and raises ``NotImplementedError``."""
        coll = await self._assert_connected()[1].require_collection(collection_name)
        gate = asyncio.Semaphore(self._cfg.search.request_concurrency)

        async def one(req: HybridSearchRequest) -> list[SearchResult]:
            async with gate:
                return await self._search_one(coll, req)

        return list(await asyncio.gather(*(one(r) for r in requests)))

    async def _search_one(self, coll: CollectionRow, req: HybridSearchRequest) -> list[SearchResult]:
        db, catalog = self._assert_connected()
        if req.filter is None:
            raise OrgRequired("a search needs a filter that names the organisation")
        org = _filter_org(req.filter)
        if req.limit < 1:
            raise ValueError("a search limit must be at least 1")
        if req.sparse_query is not None and req.sparse_query.indices and coll.sparse_enabled:
            raise _pending("the lexical leg of a hybrid search")
        if req.dense_query is None:
            return []
        scope = scope_of(req.filter)
        cfg = self._cfg.search
        with org_scope(org):
            if scope.apps is not None and cfg.eliminate_all_apps:
                scope = scope.narrowed_to(await self._sizer.connectors(org, coll.collection_id))
            space = await catalog.active_space(org)
            if space is None:
                return []
            if len(req.dense_query) != space.dim:
                raise SpaceMismatch(f"the query has {len(req.dense_query)} dimensions; the organisation's embedding space has {space.dim}")
            q = dense.Query(org, coll.collection_id, space.space_id, space.dim, space.metric, writes.halfvec_text(req.dense_query), scope,
                            min(max(2 * req.limit, 1), _MAX_LEG))
            bq_ok = self._bq.get((org, space.space_id), True) and self.pg_capabilities.bit_hnsw
            found, sizing = await dense.search(db, self._sizer, q, cfg, bq_ok=bq_ok)
            self._after_dense(q, found, sizing, space.space_id)
            if found.degraded:
                degraded_notice.note(found.degraded)          # the provider contract: an empty answer is flagged too
            ranked = fusion.rrf([[pid for _, pid in found.rows]], req.limit)
            if not req.with_payload:
                return [SearchResult(id=pid, score=score, degraded=found.degraded) for pid, score in ranked]
            points = {p.id: p for p in await db.run(
                lambda tx: reads.retrieve(tx, org, coll.collection_id, space.space_id, _ids([pid for pid, _ in ranked])), custom_plan=True)}
            return [SearchResult(id=pid, score=score, payload=points[pid].payload, degraded=found.degraded) for pid, score in ranked if pid in points]

    def _after_dense(self, q: dense.Query, found: dense.Leg, sizing: dense.Sizing, space_id: int) -> None:
        """Work after a dense leg: offer the recall sample and, the first time this process serves an org's space from the vector
        index, make sure the space has a current quantisation verdict (stored; a scan only when none is due)."""
        key = (q.org, space_id)
        if (found.plan.tier in (dense.Tier.H, dense.Tier.H_DEEP, dense.Tier.E0X) and self._cfg.search.validate_quantization
                and time.monotonic() - self._bq_at.get(key, float("-inf")) > self._cfg.search.verdict_ttl_s):
            # The first time, and then every verdict_ttl_s: read the stored verdict (another process may have stored one, or the last one
            # may be due again) and check when none is current. The index serves until a verdict says otherwise.
            self._bq.setdefault(key, True)
            self._bq_at[key] = time.monotonic()
            task = org_context.spawn_detached(self._check_space(q, space_id, max(sizing.leaf_rows, sizing.n_hat)), name="pgvector-bit-recall-check")
            self._checks.add(task)
            task.add_done_callback(self._checks.discard)
        if found.degraded not in ("overload", "quantization"):
            self._sampler.offer(Sample(q, frozenset(sk for sk, _ in found.rows), "degraded" if found.degraded else found.plan.tier.value,
                                       found.plan.share_band, sizing.n_hat), rolled=False)

    async def _check_space(self, q: dense.Query, space_id: int, rows: int) -> None:
        t = self._cfg.search
        key = (q.org, space_id)
        try:
            with org_scope(q.org):
                stored = await validate.load(self._db, t, q.org, space_id)
                if stored.recall is not None:
                    self._bq[key] = stored.recall >= t.validate_floor
                if not stored.due:
                    return
                try:
                    samples = validate.samples_for(t, rows)
                    mean = await validate.bit_recall(self._db, t, q.org, q.collection_id, space_id, q.dim, q.metric, samples=samples)
                except (dense.Rejected, DbError):
                    await validate.failed(self._db, t, q.org, space_id)       # no slot or no time: not again before the back-off
                    return
                if mean is None:
                    return
                await validate.save(self._db, q.org, space_id, mean, samples)
        except Exception:
            logger.exception("bit-quantisation check failed; the stored verdict, or the vector index, keeps serving this space")
            return
        self._bq[key] = mean >= t.validate_floor
        if mean < t.validate_floor:
            logger.warning("bit HNSW + rerank finds %.3f of the exact neighbours in this embedding space (floor %.2f): "
                           "searches above the exact-scan size are served by a bounded path and flagged", mean, t.validate_floor)

    async def validate_space(self, collection_name: str, org_id: str) -> float | None:
        """Runs the quantisation check for the org's active space now (None: no points), stores the verdict and applies it: below
        the floor the space is served by the bounded path, above it by the index."""
        db, catalog = self._assert_connected()
        coll = await catalog.require_collection(collection_name)
        t = self._cfg.search
        with org_scope(require_org(org_id)) as org:
            space = await catalog.active_space(org)
            if space is None:
                return None
            mean = await validate.bit_recall(db, t, org, coll.collection_id, space.space_id, space.dim, space.metric)
            if mean is not None:
                await validate.save(db, org, space.space_id, mean, t.validate_sample)
                self._bq[(org, space.space_id)] = mean >= t.validate_floor
        return mean


    # Writes --------------------------------------------------------------

    async def upsert_points(
        self, collection_name: str, points: list[VectorPoint], *, computed_at: datetime | None = None
    ) -> None:
        """``computed_at``: when the membership arrays the points carry were read from the graph (default: now). A group's arrays are
        replaced only by a writer with a later time, so an index run that read its arrays before the membership sync wrote newer ones
        passes the time it read them and loses to the sync; without it the call time is used, which is right for a run that reads the
        graph and writes at once and makes the last writer win otherwise."""
        if not points:
            return
        db, catalog = self._assert_connected()
        orgs = {require_org(((p.payload or {}).get("metadata") or {}).get("orgId")) for p in points}
        if len(orgs) != 1:
            raise OrgMismatch("a batch of points must belong to one organisation")
        coll = await catalog.require_collection(collection_name)
        computed_at = computed_at or datetime.now(UTC)
        for start in range(0, len(points), writes.UPSERT_BATCH):
            batch = writes.prepare(points[start:start + writes.UPSERT_BATCH])
            if len(set(batch.dims)) != 1:
                raise SpaceMismatch("a batch of points must carry vectors of one dimension")
            with org_scope(batch.org):
                if self.placement is not None:
                    await self.placement.before_first_write(batch.org)      # the org's leaf exists before its first row
                space = await self._space_for_write(batch.org, batch.dims[0], {m for m in batch.models if m})
                try:
                    await db.run(
                        lambda tx, batch=batch, space=space: writes.upsert(
                            tx, coll.collection_id, space.space_id, batch, computed_at, safe_mode=self._safe_mode),
                        write=True, idempotent=True, custom_plan=True,
                    )
                except asyncpg.ForeignKeyViolationError as e:       # the database's own guard: our view of the spaces was stale
                    catalog.forget(org=batch.org)
                    raise SpaceMismatch("the vectors do not fit the organisation's embedding space") from e

    async def delete_points(
        self,
        collection_name: str,
        filter: FilterExpression,  # noqa: A002 - IVectorDBService's parameter name
        refresh: bool = False,  # noqa: FBT001, FBT002 - IVectorDBService's signature
    ) -> None:
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
        db, catalog = self._assert_connected()
        org = _filter_org(filter)
        coll = await catalog.require_collection(collection_name)
        compiled = compile_filter(filter, first_param=3)
        with org_scope(org):
            while True:       # one transaction per batch, keyed on (org_id, point_sk)
                done = await db.run(
                    lambda tx: writes.delete_batch(tx, org, coll.collection_id, compiled, safe_mode=self._safe_mode),
                    write=True, idempotent=True, custom_plan=True,
                )
                if done < writes.DELETE_BATCH:
                    return

    async def _write_payload(
        self, collection_name: str, payload: dict, org: str, *, ids: list[uuid.UUID] | None, expr: FilterExpression | None,
        computed_at: datetime | None, replace: bool,
    ) -> None:
        """Membership arrays go to the group rows behind the compare-and-set fence; every other key is merged into the stored
        payload. One transaction."""
        db, catalog = self._assert_connected()
        coll = await catalog.require_collection(collection_name)
        t = computed_at or datetime.now(UTC)
        membership = writes.membership_of(payload)
        group_filter = compile_filter(expr, first_param=3) if expr is not None else None
        merge_filter = compile_filter(expr, first_param=5) if expr is not None else None

        async def run(tx: Tx) -> None:
            if membership:
                keys = (await writes.group_keys_of_ids(tx, org, coll.collection_id, ids)) if ids is not None \
                    else await writes.group_keys_matching(tx, org, coll.collection_id, group_filter)
                if keys:
                    await writes.write_groups(tx, org, coll.collection_id, dict.fromkeys(keys, membership), t, allow_empty=True)
            await writes.merge_payload(tx, org, coll.collection_id, payload, merge_filter, ids, replace=replace)

        with org_scope(org):
            await db.run(run, write=True, idempotent=True, custom_plan=True)

    async def update_payload_by_ids(
        self, collection_name: str, point_ids: list[str], payload: dict, *, org_id: str | None = None,
        computed_at: datetime | None = None,
    ) -> None:
        """Merge ``payload`` into the points of ``org_id`` with these ids; ids of another org are left alone. Ids alone do not name an
        org: without ``org_id`` the request's org is used, and none at all is ``OrgRequired`` (a blank ``org_id`` too)."""
        org = ambient_or_required() if org_id is None else require_org(org_id)
        ids = _ids(point_ids)
        if ids:
            await self._write_payload(collection_name, payload, org, ids=ids, expr=None, computed_at=computed_at, replace=False)

    async def overwrite_payload(
        self, collection_name: str, payload: dict, points: FilterExpression
    ) -> None:
        if points.is_empty():
            raise ValueError(
                "overwrite_payload called with an empty filter — it would replace the payload of "
                "every point in the collection. Populate at least one filter condition (e.g. virtualRecordId)."
            )
        await self._write_payload(collection_name, payload, _filter_org(points), ids=None, expr=points, computed_at=None, replace=True)

    async def set_payload(
        self,
        collection_name: str,
        payload: dict,
        filter: FilterExpression,  # noqa: A002 - IVectorDBService's parameter name
        refresh: bool = False,  # noqa: FBT001, FBT002 - IVectorDBService's signature
        *,
        computed_at: datetime | None = None,
    ) -> None:
        """``computed_at``: when the writer read the graph the arrays come from (default: now). A row is replaced only by a
        later one."""
        if filter.is_empty():
            raise ValueError(
                "set_payload called with an empty filter — this would update the entire "
                "collection. Populate at least one filter condition (e.g. virtualRecordId)."
            )
        await self._write_payload(collection_name, payload, _filter_org(filter), ids=None, expr=filter, computed_at=computed_at, replace=False)

