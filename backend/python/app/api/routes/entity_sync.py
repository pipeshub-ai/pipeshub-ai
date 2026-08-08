"""
Admin endpoint for entity vector store synchronisation.

POST /api/v1/admin/entity-sync/trigger
    Full sync: pulls all entities for the org from the graph DB and upserts them
    into the entity vector store.  Safe to re-run (idempotent due to UUID5 point IDs).
    Pass ``?background=true`` to run it as a FastAPI background task and get an
    immediate 202 instead of blocking on however long the org's full sync takes.

GET  /api/v1/admin/entity-sync/status
    Returns the current entity vector store collection stats for the org, plus
    the persisted status of the most recent sync run (idle/running/completed/failed).
"""

import contextlib
import logging
from typing import TYPE_CHECKING, Any, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from app.api.middlewares.admin import require_admin
from app.api.middlewares.auth import require_scopes
from app.config.constants.arangodb import CollectionNames
from app.config.constants.service import OAuthScopes
from app.models.entities import EntityRecord, EntityType
from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
from app.utils.time_conversion import get_epoch_timestamp_in_ms

if TYPE_CHECKING:
    from app.modules.transformers.entity_vectorstore import EntityVectorStore

# Page size + hard page-count cap for the backfill read below: bounds a single
# request to at most PAGE_SIZE * MAX_PAGES entities per (type-group) query
# instead of an unbounded scroll over the whole org (CLAUDE.md scalability
# guidance — never loop unbounded over external/DB data on a request path).
_SYNC_PAGE_SIZE = 500
_SYNC_MAX_PAGES = 100

# Reuses the generic syncPoints collection/mechanism connectors already use
# for their own incremental-sync cursors (see get_sync_point/upsert_sync_point
# on IGraphDBProvider) rather than inventing a second status-tracking store —
# this is what makes "per-org status" survive a background-task response
# across process restarts and multiple query-service instances.
_SYNC_STATUS_COLLECTION = CollectionNames.SYNC_POINTS.value


def _sync_status_key(org_id: str) -> str:
    return f"entity_sync_status:{org_id}"


router = APIRouter(
    prefix="/api/v1/admin/entity-sync",
    tags=["Entity Sync Admin"],
    # Both endpoints are org-scoped admin operations that can trigger embedding
    # calls and expose per-org counts. Two independent checks, since neither
    # alone covers every caller: require_scopes only enforces anything for
    # OAuth-token requests (a no-op for a regular session JWT — see its own
    # docstring), while require_admin covers session/JWT callers by checking
    # the real admin role against the Node.js CM backend.
    dependencies=[
        Depends(require_scopes(OAuthScopes.CONNECTOR_SYNC, OAuthScopes.KG_GOVERNANCE)),
        Depends(require_admin),
    ],
)


async def _get_services(request: Request) -> dict[str, Any]:
    container = request.app.container
    logger = container.logger()

    graph_provider = None
    if hasattr(request.app.state, "graph_provider"):
        graph_provider = request.app.state.graph_provider
    if graph_provider is None and hasattr(container, "graph_provider"):
        graph_provider = await container.graph_provider()

    entity_vector_store = None
    if hasattr(container, "entity_vector_store"):
        try:
            entity_vector_store = await container.entity_vector_store()
        except Exception as exc:
            logger.warning("entity_vector_store not available: %s", exc)

    return {
        "logger": logger,
        "graph_provider": graph_provider,
        "entity_vector_store": entity_vector_store,
    }


async def _fetch_all_entities_for_sync(
    graph_provider: IGraphDBProvider,
    *,
    org_id: str,
    entity_types: list[str] | None,
    logger: logging.Logger,
) -> list[dict[str, Any]]:
    """Page through ``get_entities_for_sync`` instead of trusting a single
    unbounded call — a page short of ``_SYNC_PAGE_SIZE`` ends that type's
    pagination; the hard ``_SYNC_MAX_PAGES`` cap prevents a single admin
    request from scrolling an unbounded number of pages against the graph DB.
    """
    all_entities: list[dict[str, Any]] = []
    offset = 0
    for _ in range(_SYNC_MAX_PAGES):
        page = await graph_provider.get_entities_for_sync(
            org_id=org_id,
            entity_types=entity_types,
            limit=_SYNC_PAGE_SIZE,
            offset=offset,
        )
        if not page:
            break
        all_entities.extend(page)
        if len(page) < _SYNC_PAGE_SIZE:
            break
        offset += _SYNC_PAGE_SIZE
    else:
        logger.warning(
            "Entity sync hit the %d-page cap for org=%s — some entities may "
            "not have been synced this run.",
            _SYNC_MAX_PAGES, org_id,
        )
    return all_entities


async def _write_sync_status(
    graph_provider: IGraphDBProvider,
    org_id: str,
    logger: logging.Logger,
    *,
    status: Literal["running", "completed", "failed"],
    synced: int | None = None,
    error: str | None = None,
    started_at: int | None = None,
) -> None:
    """Best-effort — a failure to persist status must never fail (or mask
    the outcome of) the sync itself.
    """
    now = get_epoch_timestamp_in_ms()
    data = {
        "status": status,
        "orgId": org_id,
        "updatedAtTimestamp": now,
        "startedAtTimestamp": started_at if started_at is not None else now,
    }
    if status != "running":
        data["completedAtTimestamp"] = now
    if synced is not None:
        data["synced"] = synced
    if error is not None:
        data["error"] = error
    try:
        await graph_provider.upsert_sync_point(
            _sync_status_key(org_id), data, collection=_SYNC_STATUS_COLLECTION,
        )
    except Exception as exc:
        logger.warning("entity-sync status persist failed (non-fatal): %s", exc)


async def _execute_sync(
    graph_provider: IGraphDBProvider,
    entity_vector_store: "EntityVectorStore",
    org_id: str,
    requested_types: list[str] | None,
    logger: logging.Logger,
    *,
    started_at: int,
) -> dict[str, Any]:
    """Core sync logic shared by the foreground and background paths.
    Always writes a terminal ``completed``/``failed`` status on the way out.
    """
    valid_types = {e.value for e in EntityType}
    try:
        raw_entities = await _fetch_all_entities_for_sync(
            graph_provider, org_id=org_id, entity_types=requested_types, logger=logger
        )

        entity_records: list[EntityRecord] = []
        for raw in raw_entities:
            try:
                etype_val = (raw.get("entityType") or "").lower()
                if etype_val not in valid_types:
                    continue
                entity_records.append(
                    EntityRecord(
                        entity_id=str(raw["entityId"]),
                        entity_type=EntityType(etype_val),
                        name=str(raw.get("name") or ""),
                        org_id=org_id,
                        description=raw.get("description") or "",
                        aliases=raw.get("aliases") or [],
                        parent_entity_id=raw.get("parentEntityId"),
                        parent_entity_type=(
                            EntityType(raw["parentEntityType"].lower())
                            if raw.get("parentEntityType")
                            and raw["parentEntityType"].lower() in valid_types
                            else None
                        ),
                        connector_id=raw.get("connectorId"),
                    )
                )
            except Exception as exc:
                logger.warning("Skipping malformed entity %s: %s", raw, exc)

        if entity_records:
            await entity_vector_store.upsert_entities_batch(entity_records)

        result = {
            "status": "success",
            "message": f"Synced {len(entity_records)} entities to vector store.",
            "synced": len(entity_records),
        }
        await _write_sync_status(
            graph_provider, org_id, logger, status="completed",
            synced=len(entity_records), started_at=started_at,
        )
        logger.info("Entity sync complete | org=%s | synced=%d", org_id, len(entity_records))
        return result
    except Exception as exc:
        logger.error("Entity sync failed | org=%s: %s", org_id, exc, exc_info=True)
        await _write_sync_status(
            graph_provider, org_id, logger, status="failed", error=str(exc), started_at=started_at,
        )
        raise


@router.post(
    "/trigger",
    summary="Trigger full entity vector sync for the caller's organisation",
)
async def trigger_entity_sync(
    request: Request,
    background_tasks: BackgroundTasks,
    entity_types: list[str] | None = None,
    background: bool = False,  # noqa: FBT001, FBT002 -- FastAPI query param, never called positionally
) -> JSONResponse:
    """Pull all entities from the graph DB and upsert them into the entity
    vector store.  Idempotent — safe to re-run as a repair operation.

    Query params:
      entity_types: optional comma-separated list to restrict sync
                    (e.g. category,topic,department).  Defaults to all types.
      background: when true, runs as a FastAPI background task and returns
                    202 immediately instead of blocking on the full org sync —
                    poll GET /status for completion. Recommended for orgs with
                    a large entity graph.
    """
    services = await _get_services(request)
    logger = services["logger"]
    graph_provider = services["graph_provider"]
    entity_vector_store = services["entity_vector_store"]

    user = getattr(request.state, "user", {})
    org_id = user.get("orgId", "")
    if not org_id:
        raise HTTPException(status_code=401, detail="Authenticated org_id required")

    if entity_vector_store is None:
        raise HTTPException(
            status_code=503,
            detail="Entity vector store is not available on this service instance.",
        )
    if graph_provider is None:
        raise HTTPException(status_code=503, detail="Graph provider not available.")

    # Validate entity_types param
    valid_types = {e.value for e in EntityType}
    requested_types: list[str] | None = None
    if entity_types:
        requested_types = []
        for raw_type in entity_types:
            normalized_type = raw_type.strip().lower()
            if normalized_type not in valid_types:
                raise HTTPException(
                    status_code=400,
                    detail=f"Unknown entity_type '{normalized_type}'. Valid: {sorted(valid_types)}",
                )
            requested_types.append(normalized_type)

    logger.info(
        "Entity sync triggered | org=%s | entity_types=%s | background=%s",
        org_id, requested_types, background,
    )
    started_at = get_epoch_timestamp_in_ms()
    await _write_sync_status(graph_provider, org_id, logger, status="running", started_at=started_at)

    if background:
        async def _run_and_swallow() -> None:
            # _execute_sync already persists a terminal completed/failed
            # status before re-raising — nothing left to do with the
            # exception here, there's no request left to report it to.
            with contextlib.suppress(Exception):
                await _execute_sync(
                    graph_provider, entity_vector_store, org_id, requested_types, logger,
                    started_at=started_at,
                )

        background_tasks.add_task(_run_and_swallow)
        return JSONResponse(
            status_code=202,
            content={
                "status": "accepted",
                "message": "Entity sync scheduled in the background. Poll GET /status for progress.",
            },
        )

    try:
        result = await _execute_sync(
            graph_provider, entity_vector_store, org_id, requested_types, logger,
            started_at=started_at,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Entity sync failed: {exc}") from exc
    return JSONResponse(content=result)


@router.get(
    "/status",
    summary="Get entity vector store collection stats for the caller's organisation",
)
async def entity_sync_status(request: Request) -> JSONResponse:
    """Return collection info, per-org entity count, and the persisted
    status of the most recent sync run (idle if none has ever run)."""
    services = await _get_services(request)
    logger = services["logger"]
    graph_provider = services["graph_provider"]
    entity_vector_store = services["entity_vector_store"]

    user = getattr(request.state, "user", {})
    org_id = user.get("orgId", "")

    if entity_vector_store is None:
        return JSONResponse(
            status_code=503,
            content={
                "status": "unavailable",
                "message": "Entity vector store is not provisioned on this service.",
            },
        )

    if not org_id:
        raise HTTPException(status_code=401, detail="Authenticated org_id required")

    try:
        # Org-scoped only: never expose the collection-wide `points_count`
        # here, since that is a cross-tenant total in a shared collection.
        org_counts = await entity_vector_store.count_org_entities(org_id)
        info = {
            "collection": entity_vector_store.collection_name,
            "org_entity_count": org_counts["count"],
            "is_estimate": org_counts["is_estimate"],
        }
    except Exception as exc:
        logger.warning("Could not fetch org entity count: %s", exc)
        info = {"collection": entity_vector_store.collection_name, "error": str(exc)}

    last_sync: dict[str, Any] = {"status": "idle"}
    if graph_provider is not None:
        try:
            sync_point = await graph_provider.get_sync_point(
                _sync_status_key(org_id), _SYNC_STATUS_COLLECTION,
            )
            if sync_point:
                last_sync = {
                    "status": sync_point.get("status", "idle"),
                    "startedAt": sync_point.get("startedAtTimestamp"),
                    "completedAt": sync_point.get("completedAtTimestamp"),
                    "synced": sync_point.get("synced"),
                    "error": sync_point.get("error"),
                }
        except Exception as exc:
            logger.warning("Could not fetch last sync status: %s", exc)

    return JSONResponse(content={"status": "success", "data": info, "lastSync": last_sync})
