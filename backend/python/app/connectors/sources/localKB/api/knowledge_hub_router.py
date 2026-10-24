"""Knowledge Hub Unified Browse API Router"""

import logging
import re
from typing import Any, Dict, List, Optional, Set, Union

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status

from app.api.connection_limit import (
    connection_apps,
    ids_outside_connection,
    named_inside_connection,
    refuse_outside_connection,
)
from app.api.middlewares.auth import connection_limit, is_request_admin, require_scopes
from app.config.constants.arangodb import ProgressStatus
from app.config.constants.service import OAuthScopes
from app.connectors.sources.localKB.api.knowledge_hub_models import (
    CountItem,
    CountsInfo,
    IncludeOption,
    KnowledgeHubErrorResponse,
    KnowledgeHubNodesResponse,
    NodeItem,
    NodeType,
    OriginType,
    PaginationInfo,
    SortField,
    SortOrder,
)
from app.connectors.sources.localKB.handlers.kh_bounded import (
    BOUNDED_BATCH,
    BoundedCursor,
    BoundedRead,
    Position,
    bound_key,
    decode_bounded_cursor,
    encode_bounded_cursor,
    is_bounded_cursor,
    read_inside,
)
from app.connectors.sources.localKB.handlers.knowledge_hub_service import (
    KnowledgeHubService,
)
from app.containers.connector import ConnectorAppContainer
from app.models.entities import RecordType
from app.modules.retrieval.selection_scope import names_below_apps
from app.utils.user_messages import action_failed

# Handlers bind their own ``logger`` inside the request, so the module logger
# needs a name a half-run handler cannot shadow.
_log = logging.getLogger(__name__)

knowledge_hub_router = APIRouter(
    prefix="/api/v1/knowledge-hub",
    tags=["Knowledge Hub"]
)

# Validation constants
MAX_TIMESTAMP_MS = 9999999999999  # Year 2286 in milliseconds
MAX_FILE_SIZE_BYTES = 1099511627776  # 1 TB in bytes
MAX_SEARCH_QUERY_LENGTH = 500
HTTP_CLIENT_ERROR_MIN = 400
HTTP_SERVER_ERROR_MIN = 500
MIN_SEARCH_QUERY_LENGTH = 2
MAX_COMMA_SEPARATED_ITEMS = 100


async def get_knowledge_hub_service(request: Request) -> KnowledgeHubService:
    """
    Helper to resolve KnowledgeHubService with its dependencies.

    Uses graph_provider from app.state which is initialized once at startup,
    following the same pattern as arango_service and config_service.
    """
    container: ConnectorAppContainer = request.app.container
    logger = container.logger()
    graph_provider = request.app.state.graph_provider
    # The cursor-signing key comes from the config service; without it the
    # service hands out no cursors.
    return KnowledgeHubService(
        logger=logger,
        graph_provider=graph_provider,
        config_service=container.config_service(),
    )

def _get_enum_values(enum_class) -> Set[str]:
    """Get all valid values from an enum class."""
    return {e.value for e in enum_class}

def _validate_enum_values(
    values: Optional[List[str]],
    valid_values: Set[str],
    field_name: str
) -> Optional[List[str]]:
    """Validate every value, or answer 400.

    Dropping an unrecognised value silently leaves unapplied a filter the caller
    believes is applied: a *wider* result set that looks like a correct answer.
    """
    if not values:
        return None
    invalid = [value for value in values if value not in valid_values]
    if invalid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Invalid {field_name} value(s): {', '.join(invalid)}. "
                f"Allowed: {', '.join(sorted(valid_values))}"
            ),
        )
    return values

def _parse_comma_separated_str(value: Optional[str]) -> Optional[List[str]]:
    """Parses a comma-separated string into a list of strings, filtering out empty items."""
    if not value:
        return None
    items = [item.strip() for item in value.split(',') if item.strip()]
    if len(items) > MAX_COMMA_SEPARATED_ITEMS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Too many items in comma-separated list (max: {MAX_COMMA_SEPARATED_ITEMS}, got: {len(items)})"
        )
    return items if items else None

def _validate_uuid_format(value: Optional[str], field_name: str) -> None:
    """Validate UUID format for IDs."""
    if not value:
        return

    uuid_pattern = re.compile(r'^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$', re.IGNORECASE)
    if not uuid_pattern.match(value):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid UUID format for {field_name}: {value}"
        )

def _parse_date_range(value: Optional[str]) -> Optional[Dict[str, Optional[int]]]:
    """Parse and validate date range from query parameter"""
    if not value:
        return None
    # Format: "gte:1234567890,lte:1234567890" or just a single timestamp
    parts = value.split(',')
    result = {}
    for part in parts:
        if ':' in part:
            key, val = part.split(':', 1)
            if key in ['gte', 'lte']:
                try:
                    timestamp = int(val)
                    # Validate reasonable timestamp range (0 to year 2286)
                    if timestamp < 0 or timestamp > MAX_TIMESTAMP_MS:
                        raise HTTPException(
                            status_code=status.HTTP_400_BAD_REQUEST,
                            detail=f"Timestamp out of valid range: {timestamp}"
                        )
                    result[key] = timestamp
                except ValueError:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"Invalid timestamp value: {val}"
                    ) from None

    # Validate gte <= lte
    if 'gte' in result and 'lte' in result and result['gte'] > result['lte']:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Date range invalid: gte must be <= lte"
        )

    return result if result else None

def _parse_size_range(value: Optional[str]) -> Optional[Dict[str, Optional[int]]]:
    """Parse and validate size range from query parameter"""
    if not value:
        return None
    # Format: "gte:1024,lte:1048576"
    parts = value.split(',')
    result = {}
    for part in parts:
        if ':' in part:
            key, val = part.split(':', 1)
            if key in ['gte', 'lte']:
                try:
                    size = int(val)
                    # Validate non-negative
                    if size < 0:
                        raise HTTPException(
                            status_code=status.HTTP_400_BAD_REQUEST,
                            detail=f"Size must be non-negative, got: {size}"
                        )
                    # Validate reasonable max (1TB)
                    if size > MAX_FILE_SIZE_BYTES:
                        raise HTTPException(
                            status_code=status.HTTP_400_BAD_REQUEST,
                            detail=f"Size exceeds maximum (1TB): {size}"
                        )
                    result[key] = size
                except ValueError:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"Invalid size value: {val}"
                    ) from None

    # Validate gte <= lte
    if 'gte' in result and 'lte' in result and result['gte'] > result['lte']:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Size range invalid: gte must be <= lte"
        )

    return result if result else None


def _serialized(result: object) -> object:
    """The page as JSON, serialized once. Handed back as the model, FastAPI dumps
    it, validates the dump against the model again, serializes that and encodes
    the result: about six times the CPU for the same body, spent on the event
    loop every request of this service shares."""
    if isinstance(result, KnowledgeHubNodesResponse):
        return Response(content=result.model_dump_json(by_alias=True), media_type="application/json")
    return result


@knowledge_hub_router.get(
    "/nodes",
    response_model=KnowledgeHubNodesResponse,
    responses={
        400: {"model": KnowledgeHubErrorResponse},
        500: {"model": KnowledgeHubErrorResponse},
    },
    dependencies=[Depends(require_scopes(OAuthScopes.KB_READ))],
)
async def get_knowledge_hub_root_nodes(
    request: Request,
    only_containers: bool = Query(False, description="Only return nodes with children (for sidebar)"),
    page: int = Query(1, ge=1, description="Page number (1-indexed)"),
    limit: int = Query(50, ge=1, le=200, description="Items per page"),
    sort_by: str = Query("updatedAt", description="Sort field: name, createdAt, updatedAt, size, type"),
    sort_order: str = Query("desc", description="Sort order: asc or desc"),
    q: Optional[str] = Query(None, description="Full-text search query"),
    node_types: Optional[str] = Query(None, description="Comma-separated node types"),
    record_types: Optional[str] = Query(None, description="Comma-separated record types"),
    origins: Optional[str] = Query(None, description="Comma-separated origins: COLLECTION, CONNECTOR"),
    connector_ids: Optional[str] = Query(None, description="Comma-separated connector instance IDs"),
    indexing_status: Optional[str] = Query(None, description="Comma-separated indexing statuses"),
    created_at: Optional[str] = Query(None, description="Created date range: gte:timestamp,lte:timestamp"),
    updated_at: Optional[str] = Query(None, description="Updated date range: gte:timestamp,lte:timestamp"),
    size: Optional[str] = Query(None, description="Size range: gte:bytes,lte:bytes"),
    flattened: Optional[bool] = Query(None, description="Force flattened/recursive search (true) or direct listing (false). When omitted, computed from which filters are present."),
    include: Optional[str] = Query(None, description="Comma-separated includes: breadcrumbs, counts, availableFilters, permissions"),
    cursor: Optional[str] = Query(None, description="Opaque page cursor from a previous response. When set it decides listing mode, sort, filters and parent; only include comes from this request. A parent in the URL that disagrees is refused."),
    knowledge_hub_service: KnowledgeHubService = Depends(get_knowledge_hub_service),
) -> Union[KnowledgeHubNodesResponse, Dict[str, Any]]:
    """
    Get root level nodes (Apps) or search across all nodes.

    For browsing children of a specific node, use:
    GET /nodes/{parent_type}/{parent_id}
    """
    return _serialized(await _handle_get_nodes(
        request=request,
        knowledge_hub_service=knowledge_hub_service,
        parent_id=None,
        parent_type=None,
        only_containers=only_containers,
        page=page,
        limit=limit,
        sort_by=sort_by,
        sort_order=sort_order,
        q=q,
        node_types=node_types,
        record_types=record_types,
        origins=origins,
        connector_ids=connector_ids,
        indexing_status=indexing_status,
        created_at=created_at,
        updated_at=updated_at,
        size=size,
        flattened=flattened,
        include=include,
        cursor=cursor,
    ))


@knowledge_hub_router.get(
    "/nodes/{parent_type}/{parent_id}",
    response_model=KnowledgeHubNodesResponse,
    responses={
        400: {"model": KnowledgeHubErrorResponse},
        500: {"model": KnowledgeHubErrorResponse},
    },
    dependencies=[Depends(require_scopes(OAuthScopes.KB_READ))],
)
async def get_knowledge_hub_children_nodes(
    request: Request,
    parent_type: str,
    parent_id: str,
    only_containers: bool = Query(False, description="Only return nodes with children (for sidebar)"),
    page: int = Query(1, ge=1, description="Page number (1-indexed)"),
    limit: int = Query(50, ge=1, le=200, description="Items per page"),
    sort_by: str = Query("updatedAt", description="Sort field: name, createdAt, updatedAt, size, type"),
    sort_order: str = Query("desc", description="Sort order: asc or desc"),
    q: Optional[str] = Query(None, description="Full-text search query"),
    node_types: Optional[str] = Query(None, description="Comma-separated node types"),
    record_types: Optional[str] = Query(None, description="Comma-separated record types"),
    origins: Optional[str] = Query(None, description="Comma-separated origins: COLLECTION, CONNECTOR"),
    connector_ids: Optional[str] = Query(None, description="Comma-separated connector instance IDs"),
    indexing_status: Optional[str] = Query(None, description="Comma-separated indexing statuses"),
    created_at: Optional[str] = Query(None, description="Created date range: gte:timestamp,lte:timestamp"),
    updated_at: Optional[str] = Query(None, description="Updated date range: gte:timestamp,lte:timestamp"),
    size: Optional[str] = Query(None, description="Size range: gte:bytes,lte:bytes"),
    flattened: Optional[bool] = Query(None, description="Force flattened/recursive search (true) or direct listing (false). When omitted, computed from which filters are present."),
    include: Optional[str] = Query(None, description="Comma-separated includes: breadcrumbs, counts, availableFilters, permissions"),
    cursor: Optional[str] = Query(None, description="Opaque page cursor from a previous response. When set it decides listing mode, sort, filters and parent; only include comes from this request. A parent in the URL that disagrees is refused."),
    knowledge_hub_service: KnowledgeHubService = Depends(get_knowledge_hub_service),
) -> Union[KnowledgeHubNodesResponse, Dict[str, Any]]:
    """
    Get children of a specific node.

    parent_type must be one of: app, recordGroup, folder, record
    """
    valid_types = {"app", "recordGroup", "folder", "record"}
    if parent_type not in valid_types:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid parent_type. Must be one of: {', '.join(valid_types)}"
        )
    return _serialized(await _handle_get_nodes(
        request=request,
        knowledge_hub_service=knowledge_hub_service,
        parent_id=parent_id,
        parent_type=parent_type,
        only_containers=only_containers,
        page=page,
        limit=limit,
        sort_by=sort_by,
        sort_order=sort_order,
        q=q,
        node_types=node_types,
        record_types=record_types,
        origins=origins,
        connector_ids=connector_ids,
        indexing_status=indexing_status,
        created_at=created_at,
        updated_at=updated_at,
        size=size,
        flattened=flattened,
        include=include,
        cursor=cursor,
    ))


async def _handle_get_nodes(
    request: Request,
    knowledge_hub_service: KnowledgeHubService,
    parent_id: Optional[str],
    parent_type: Optional[str],
    only_containers: bool,
    page: int,
    limit: int,
    sort_by: str,
    sort_order: str,
    q: Optional[str],
    node_types: Optional[str],
    record_types: Optional[str],
    origins: Optional[str],
    connector_ids: Optional[str],
    indexing_status: Optional[str],
    created_at: Optional[str],
    updated_at: Optional[str],
    size: Optional[str],
    flattened: Optional[bool],
    include: Optional[str],
    cursor: Optional[str] = None,
) -> Union[KnowledgeHubNodesResponse, Dict[str, Any]]:
    """Shared handler for both root and children node retrieval."""
    try:
        user_id = request.state.user.get("userId")
        org_id = request.state.user.get("orgId")

        if not user_id or not org_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="userId and orgId are required"
            )

        # Validate parent_id format if provided
        if parent_id:
            _validate_uuid_format(parent_id, "parent_id")
            await refuse_outside_connection(
                request, request.app.state.graph_provider, org_id, user_id, parent_id,
            )

        # A limited root's own cursor decides the listing as the service's
        # cursors do; what it carries is checked below like any request.
        bounded: BoundedCursor | None = None
        if parent_id is None and is_bounded_cursor(cursor):
            try:
                bounded = decode_bounded_cursor(cursor)
            except ValueError:
                raise _bad_cursor() from None
            if bounded.bound != bound_key(connection_limit(request.state.user)):
                # Its offset and position count rows of another limit's listing.
                raise _bad_cursor()
            carried = bounded.params
            q, node_types, record_types = carried.get("q"), carried.get("node_types"), carried.get("record_types")
            origins, connector_ids = carried.get("origins"), carried.get("connector_ids")
            indexing_status, size = carried.get("indexing_status"), carried.get("size")
            created_at, updated_at = carried.get("created_at"), carried.get("updated_at")
            sort_by, sort_order = carried.get("sort_by", sort_by), carried.get("sort_order", sort_order)
            only_containers, flattened = carried.get("only_containers", False), carried.get("flattened")

        # Validate and sanitize search query
        if q:
            q = q.strip()
            if len(q) < MIN_SEARCH_QUERY_LENGTH:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Search query must be at least {MIN_SEARCH_QUERY_LENGTH} characters"
                )
            if len(q) > MAX_SEARCH_QUERY_LENGTH:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Search query too long (max: {MAX_SEARCH_QUERY_LENGTH} characters)"
                )

        # Parse comma-separated parameters
        parsed_node_types = _parse_comma_separated_str(node_types)
        parsed_record_types = _parse_comma_separated_str(record_types)
        parsed_origins = _parse_comma_separated_str(origins)
        parsed_connector_ids = _parse_comma_separated_str(connector_ids)
        parsed_indexing_status = _parse_comma_separated_str(indexing_status)
        parsed_include = _parse_comma_separated_str(include)

        # Validate enum-based filters (lenient - invalid values are filtered out)
        parsed_node_types = _validate_enum_values(
            parsed_node_types, _get_enum_values(NodeType), "node_types"
        )
        parsed_record_types = _validate_enum_values(
            parsed_record_types, _get_enum_values(RecordType), "record_types"
        )
        parsed_origins = _validate_enum_values(
            parsed_origins, _get_enum_values(OriginType), "origins"
        )
        # connector_ids is dynamic, no enum validation needed
        parsed_indexing_status = _validate_enum_values(
            parsed_indexing_status, _get_enum_values(ProgressStatus), "indexing_status"
        )
        parsed_include = _validate_enum_values(
            parsed_include, _get_enum_values(IncludeOption), "include"
        )

        # A silent fallback here sorts by something the caller did not ask for,
        # and paging then resumes against that other order.
        if sort_by not in _get_enum_values(SortField):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Invalid sort_by: {sort_by}. "
                    f"Allowed: {', '.join(sorted(_get_enum_values(SortField)))}"
                ),
            )
        if sort_order.lower() not in _get_enum_values(SortOrder):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Invalid sort_order: {sort_order}. "
                    f"Allowed: {', '.join(sorted(_get_enum_values(SortOrder)))}"
                ),
            )
        sort_order = sort_order.lower()

        # Parse date and size ranges
        parsed_created_at = _parse_date_range(created_at)
        parsed_updated_at = _parse_date_range(updated_at)
        parsed_size = _parse_size_range(size)
        listing_params = {
            "q": q, "sort_by": sort_by, "sort_order": sort_order, "node_types": node_types,
            "record_types": record_types, "origins": origins, "connector_ids": connector_ids,
            "indexing_status": indexing_status, "created_at": created_at, "updated_at": updated_at,
            "size": size, "only_containers": only_containers, "flattened": flattened,
        }

        query: dict[str, Any] = {
            "user_id": user_id,
            "org_id": org_id,
            "parent_id": parent_id,
            "parent_type": parent_type,
            "only_containers": only_containers,
            "page": page,
            "limit": limit,
            "sort_by": sort_by,
            "sort_order": sort_order,
            "q": q,
            "node_types": parsed_node_types,
            "record_types": parsed_record_types,
            "origins": parsed_origins,
            "connector_ids": parsed_connector_ids,
            "indexing_status": parsed_indexing_status,
            "created_at": parsed_created_at,
            "updated_at": parsed_updated_at,
            "size": parsed_size,
            "flattened": flattened,
            "include": parsed_include,
            "cursor": cursor,
            "is_org_admin": is_request_admin(request),
        }
        if parent_id is None and connection_limit(request.state.user) is not None:
            result = await _root_inside_connection(
                request, knowledge_hub_service, query, bounded=bounded, params=listing_params,
            )
        else:
            result = await knowledge_hub_service.get_nodes(**query)

        if not result.success:
            # The service flags what it wrote for a person with a 4xx code (a node
            # that is gone, a link that asks for the wrong kind of thing). Anything
            # else is ours to explain, so its text never reaches the toast — and
            # the words themselves decide nothing, since an exception's text can
            # read like anything.
            said = result.error or ""
            code = result.errorCode or 0
            if said and HTTP_CLIENT_ERROR_MIN <= code < HTTP_SERVER_ERROR_MIN:
                status_code = code
                error_detail = said
            elif said and code == status.HTTP_503_SERVICE_UNAVAILABLE:
                # Busy, not broken: the caller is told when to come back.
                raise HTTPException(status_code=code, detail=said, headers={"Retry-After": "5"})
            else:
                status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
                error_detail = action_failed("open this collection")

            raise HTTPException(
                status_code=status_code,
                detail=error_detail
            )

        # Every listing above keeps to the limit before it is paged; this only
        # guards against a row that should not have reached it.
        outside = await ids_outside_connection(
            request, request.app.state.graph_provider, org_id, user_id, [item.id for item in result.items],
        )
        if outside:
            result.items = [item for item in result.items if item.id not in outside]
            if result.typed_records:
                result.typed_records = {k: v for k, v in result.typed_records.items() if k not in outside}

        return result

    except HTTPException as he:
        raise he
    except Exception as e:
        _log.error("_handle_get_nodes failed: %s", e, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=action_failed("open this collection")
        ) from e




# The service refuses a page number past this; a later page is reached by cursor.
_MAX_PAGE_WALK = 50
# A search inside a limit below app level reads every page before the one asked
# for, up to BOUNDED_MAX_BATCHES listing reads each, so a page number is walked
# only this far; past it the caller pages by cursor, one page per request.
_MAX_BOUNDED_SEARCH_PAGE = 5
# The service's largest page.
_APP_BATCH = 200
_INVALID_LIMIT_CURSOR = "Invalid cursor: issued for a different listing"
_COUNT_LABELS = {"app": "apps", "folder": "folders", "recordGroup": "groups", "record": "records"}
_SORT_KEYS = {
    "name": lambda item: (item.name or "").casefold(),
    "createdAt": lambda item: item.createdAt or 0,
    "updatedAt": lambda item: item.updatedAt or 0,
    "size": lambda item: item.sizeInBytes if item.sizeInBytes is not None else -1,
    "type": lambda item: str(item.nodeType),
}


def _bad_cursor() -> HTTPException:
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=_INVALID_LIMIT_CURSOR)


def _page_walk_refused(most: int = _MAX_PAGE_WALK) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_400_BAD_REQUEST,
        detail=f"Invalid page: page must not exceed {most}. Use the cursor from a previous response.",
    )


async def _root_inside_connection(
    request: Request, service: KnowledgeHubService, query: dict[str, Any],
    *, bounded: BoundedCursor | None, params: dict[str, Any],
) -> KnowledgeHubNodesResponse:
    """The root listing or global search of a connected app with a limit, paged
    over what the limit admits only.

    A limit of whole apps narrows the listing query itself. A limit that also
    names folders or files cannot be put in the query: a browse lists what the
    limit names, and a search reads the query's pages and keeps what it admits.
    """
    limit_sources = connection_limit(request.state.user) or {}
    whole, partial = await connection_apps(
        request, request.app.state.graph_provider, query["org_id"], query["user_id"],
    )
    cursor = query["cursor"]
    # The service's own cursor checks it was issued to a listing narrowed the same way.
    if not names_below_apps(limit_sources) or (cursor and bounded is None):
        return await service.get_nodes(**query, within_app_ids=whole)

    if bounded is not None:
        mode, seen, position = bounded.mode, bounded.seen, bounded.position
    else:
        if query["page"] > _MAX_PAGE_WALK:
            raise _page_walk_refused()
        flattened = query["flattened"]
        searching = flattened if flattened is not None else service._has_flattening_filters(
            query["q"], query["node_types"], query["record_types"], query["origins"],
            query["connector_ids"], query["indexing_status"], query["created_at"],
            query["updated_at"], query["size"],
        )
        mode, seen, position = ("search" if searching else "browse"), 0, Position()
    if mode == "search" and not cursor and query["page"] > _MAX_BOUNDED_SEARCH_PAGE:
        raise _page_walk_refused(_MAX_BOUNDED_SEARCH_PAGE)
    if mode == "search":
        return await _search_inside_connection(
            request, service, query, whole | partial, seen=seen, position=position, by_cursor=bool(cursor),
            params=params,
        )
    offset = seen if cursor else (query["page"] - 1) * query["limit"]
    return await _named_root(request, service, query, whole | partial, offset=offset, params=params)


def _admits(item: NodeItem, query: dict[str, Any]) -> bool:
    """Whether ``item`` passes the request's filters, as the listing query applies them."""
    q = query["q"]
    if q and q.casefold() not in (item.name or "").casefold():
        return False
    for allowed, value in (
        (query["node_types"], item.nodeType), (query["record_types"], item.recordType),
        (query["origins"], item.origin), (query["indexing_status"], item.indexingStatus),
    ):
        if allowed and value not in allowed:
            return False
    connector_ids = query["connector_ids"]
    if connector_ids and item.connectorId not in connector_ids and item.id not in connector_ids:
        return False
    if query["only_containers"] and not (item.hasChildren or item.nodeType in ("app", "recordGroup", "folder")):
        return False
    for bounds, value in (
        (query["created_at"], item.createdAt), (query["updated_at"], item.updatedAt), (query["size"], item.sizeInBytes),
    ):
        if not bounds:
            continue
        if value is None:
            return False
        if bounds.get("gte") is not None and value < bounds["gte"]:
            return False
        if bounds.get("lte") is not None and value > bounds["lte"]:
            return False
    return True


def _counts(items: list[NodeItem]) -> CountsInfo:
    by_type: dict[str, int] = {}
    for item in items:
        by_type[str(item.nodeType)] = by_type.get(str(item.nodeType), 0) + 1
    return CountsInfo(
        items=[CountItem(label=_COUNT_LABELS.get(t, t), count=n) for t, n in sorted(by_type.items())],
        total=len(items),
    )


async def _named_root(
    request: Request, service: KnowledgeHubService, query: dict[str, Any], apps: frozenset,
    *, offset: int, params: dict[str, Any],
) -> KnowledgeHubNodesResponse:
    """The root of a connection limited below app level: each app, record
    group, folder and file its limit names that the user can read, as one
    listing with exact totals."""
    include = query["include"] or []
    bound = bound_key(connection_limit(request.state.user))
    named = await named_inside_connection(
        request, request.app.state.graph_provider, query["org_id"], query["user_id"],
    )
    named_apps = {node.id for node in named if node.node_type == "app"}
    # Read over every app the limit touches, so the filter options name them all;
    # an app reached only through some of its folders is not itself listed.
    listing = await service.get_nodes(
        **{**query, "page": 1, "limit": _APP_BATCH, "cursor": None, "flattened": False,
           "include": [i for i in include if i != "counts"] or None},
        within_app_ids=apps,
    )
    if not listing.success:
        return listing
    rows = [item for item in listing.items if item.id in named_apps]
    batch, number = listing, 1
    while named_apps - {item.id for item in rows} and batch.pagination and batch.pagination.hasNext:
        number += 1
        following = batch.pagination.nextCursor
        batch = await service.get_nodes(
            **{**query, "page": 1 if following else number, "limit": _APP_BATCH, "cursor": following,
               "flattened": False, "include": None},
            within_app_ids=apps,
        )
        if not batch.success:
            return batch
        rows += [item for item in batch.items if item.id in named_apps]
    rows += [
        item for item in await service.named_node_items(query["user_id"], query["org_id"], named)
        if _admits(item, query)
    ]
    sort_key = _SORT_KEYS.get(query["sort_by"], _SORT_KEYS["name"])
    rows.sort(key=lambda item: (sort_key(item), item.id), reverse=query["sort_order"] == "desc")
    limit = query["limit"]
    page = rows[offset:offset + limit]
    end = offset + len(page)
    return KnowledgeHubNodesResponse(
        success=True,
        items=page,
        pagination=PaginationInfo(
            limit=limit,
            totalItems=len(rows),
            hasNext=end < len(rows),
            hasPrev=offset > 0,
            startIndex=offset + 1 if page else 0,
            endIndex=end,
            currentPageItems=len(page),
            nextCursor=encode_bounded_cursor("browse", end, params=params, bound=bound) if end < len(rows) else None,
            prevCursor=(
                encode_bounded_cursor("browse", max(0, offset - limit), params=params, bound=bound)
                if offset > 0 else None
            ),
            page=query["page"] if query["cursor"] is None else None,
            totalPages=(len(rows) + limit - 1) // limit if query["cursor"] is None else None,
        ),
        filters=listing.filters,
        permissions=listing.permissions,
        breadcrumbs=[] if "breadcrumbs" in include else None,
        counts=_counts(rows) if "counts" in include else None,
    )


def _resume(read: BoundedRead, limit: int) -> Position | None:
    """Where the page after a read's first ``limit`` rows starts; None when none follows."""
    if len(read.kept) > limit:
        return read.after[limit - 1]
    return read.stop


async def _search_inside_connection(
    request: Request, service: KnowledgeHubService, query: dict[str, Any], apps: frozenset,
    *, seen: int, position: Position, by_cursor: bool, params: dict[str, Any],
) -> KnowledgeHubNodesResponse:
    """A global search of a connection limited below app level: the search's
    own pages, narrowed to the apps the limit touches, refilled with what the
    limit admits. The total counts what has been found so far, and is final
    once no page follows."""
    graph_provider = request.app.state.graph_provider
    org_id, user_id, limit = query["org_id"], query["user_id"], query["limit"]
    include = query["include"] or []
    extras = [i for i in include if i != "counts"] or None
    final = False

    async def fetch(at: Position, *, first: bool) -> KnowledgeHubNodesResponse:
        return await service.get_nodes(
            **{**query, "page": 1 if at.cursor else at.page, "limit": BOUNDED_BATCH, "cursor": at.cursor,
               "flattened": True, "include": extras if first and final else None},
            within_app_ids=apps,
        )

    async def inside(ids: list[str]) -> set[str]:
        return set(ids) - await ids_outside_connection(request, graph_provider, org_id, user_id, ids)

    pages = 1 if by_cursor else query["page"]
    for number in range(1, pages + 1):
        final = number == pages
        read = await read_inside(fetch, inside, start=position, want=limit + 1)
        if read.failed:
            return read.first
        if final:
            break
        following = _resume(read, limit)
        seen += min(len(read.kept), limit)
        if following is None:
            # Asked for a page past the last one.
            read.kept, read.after, read.stop, read.exhausted = [], [], None, True
            break
        position = following
    items = read.kept[:limit]
    following = _resume(read, limit)
    return KnowledgeHubNodesResponse(
        success=True,
        items=items,
        pagination=PaginationInfo(
            limit=limit,
            totalItems=seen + len(read.kept),
            hasNext=following is not None,
            hasPrev=seen > 0,
            startIndex=seen + 1 if items else 0,
            endIndex=seen + len(items),
            currentPageItems=len(items),
            nextCursor=(
                encode_bounded_cursor(
                    "search", seen + len(items), following, params,
                    bound=bound_key(connection_limit(request.state.user)),
                )
                if following is not None else None
            ),
            page=None if by_cursor else query["page"],
        ),
        filters=read.first.filters,
        permissions=read.first.permissions,
        breadcrumbs=[] if "breadcrumbs" in include else None,
        # Counted only when this one read covered the whole answer.
        counts=_counts(read.kept) if "counts" in include and seen == 0 and read.exhausted else None,
    )
