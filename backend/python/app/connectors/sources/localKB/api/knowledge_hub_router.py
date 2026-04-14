"""Knowledge Hub Unified Browse API Router"""

import re
from typing import Any, Dict, List, Optional, Set, Union

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from app.api.middlewares.auth import require_scopes
from app.config.constants.arangodb import ProgressStatus
from app.config.constants.service import OAuthScopes
from app.connectors.sources.localKB.api.knowledge_hub_models import (
    IncludeOption,
    KnowledgeHubErrorResponse,
    KnowledgeHubNodesResponse,
    NodeType,
    OriginType,
    SortField,
    SortOrder,
)
from app.connectors.sources.localKB.handlers.knowledge_hub_service import (
    KnowledgeHubService,
)
from app.containers.connector import ConnectorAppContainer
from app.models.entities import RecordType

knowledge_hub_router = APIRouter(
    prefix="/api/v1/knowledge-hub",
    tags=["Knowledge Hub"]
)

# Validation constants
MAX_TIMESTAMP_MS = 9999999999999  # Year 2286 in milliseconds
MAX_FILE_SIZE_BYTES = 1099511627776  # 1 TB in bytes
MAX_SEARCH_QUERY_LENGTH = 500
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
    return KnowledgeHubService(logger=logger, graph_provider=graph_provider)

def _get_enum_values(enum_class) -> Set[str]:
    """Get all valid values from an enum class."""
    return {e.value for e in enum_class}

def _validate_enum_values(
    values: Optional[List[str]],
    valid_values: Set[str],
    field_name: str
) -> Optional[List[str]]:
    """
    Validate that all values are valid enum values.
    Returns the filtered list with only valid values, or None if input is None.
    Invalid values are silently filtered out to be lenient.
    """
    if not values:
        return None
    # Filter to only valid values (lenient approach - invalid values are ignored)
    valid = [v for v in values if v in valid_values]
    return valid if valid else None

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

# Collection app ID format: knowledgeBase_<orgId> (allowed for parent_id when parent_type is app)
_KNOWLEDGE_BASE_APP_ID_PATTERN = re.compile(r'^knowledgeBase_[a-zA-Z0-9_-]+$')


def _validate_uuid_format(value: Optional[str], field_name: str) -> None:
    """Validate UUID format for IDs, or knowledgeBase_<orgId> for Collection app."""
    if not value:
        return

    if _KNOWLEDGE_BASE_APP_ID_PATTERN.match(value):
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
    cursor: Optional[str] = Query(None, description="Pagination cursor (null for first page, contains complete state)"),
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
    flattened: bool = Query(False, description="Return flattened view with all nested children (even without filters)"),
    include: Optional[str] = Query(None, description="Comma-separated includes: breadcrumbs, counts, availableFilters, permissions"),
    knowledge_hub_service: KnowledgeHubService = Depends(get_knowledge_hub_service),
) -> Union[KnowledgeHubNodesResponse, Dict[str, Any]]:
    """
    Get root level nodes (Apps) or search across all nodes.

    For browsing children of a specific node, use:
    GET /nodes/{parent_type}/{parent_id}

    Pagination uses cursor-based approach:
    - First page: no cursor, use query params for filters
    - Subsequent pages: pass cursor from response (contains complete state)
    """
    return await _handle_get_nodes(
        request=request,
        knowledge_hub_service=knowledge_hub_service,
        parent_id=None,
        parent_type=None,
        only_containers=only_containers,
        cursor=cursor,
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
    )


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
    cursor: Optional[str] = Query(None, description="Pagination cursor (null for first page, contains complete state)"),
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
    flattened: bool = Query(False, description="Return flattened view with all nested children (even without filters)"),
    include: Optional[str] = Query(None, description="Comma-separated includes: breadcrumbs, counts, availableFilters, permissions"),
    knowledge_hub_service: KnowledgeHubService = Depends(get_knowledge_hub_service),
) -> Union[KnowledgeHubNodesResponse, Dict[str, Any]]:
    """
    Get children of a specific node.

    parent_type must be one of: app, recordGroup, folder, record

    Pagination uses cursor-based approach:
    - First page: no cursor, use query params for filters
    - Subsequent pages: pass cursor from response (contains complete state including parent context)
    """
    valid_types = {"app", "recordGroup", "folder", "record"}
    if parent_type not in valid_types:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid parent_type. Must be one of: {', '.join(valid_types)}"
        )
    return await _handle_get_nodes(
        request=request,
        knowledge_hub_service=knowledge_hub_service,
        parent_id=parent_id,
        parent_type=parent_type,
        only_containers=only_containers,
        cursor=cursor,
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
    )


async def _handle_get_nodes(
    request: Request,
    knowledge_hub_service: KnowledgeHubService,
    parent_id: Optional[str],
    parent_type: Optional[str],
    only_containers: bool,
    cursor: Optional[str],
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
    flattened: bool,
    include: Optional[str],
) -> Union[KnowledgeHubNodesResponse, Dict[str, Any]]:
    """
    Shared handler for both root and children node retrieval.

    When cursor is provided, it contains complete state (filters, sort, parent context).
    Query params are only used for first page requests (no cursor).
    """
    try:
        user_id = request.state.user.get("userId")
        org_id = request.state.user.get("orgId")

        if not user_id or not org_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="userId and orgId are required"
            )

        # Validate parent_id format if provided (only for first page, cursor has its own)
        if parent_id and not cursor:
            _validate_uuid_format(parent_id, "parent_id")

        # Validate and sanitize search query (only for first page)
        if q and not cursor:
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

        # Parse comma-separated parameters (only used for first page)
        parsed_node_types = _parse_comma_separated_str(node_types) if not cursor else None
        parsed_record_types = _parse_comma_separated_str(record_types) if not cursor else None
        parsed_origins = _parse_comma_separated_str(origins) if not cursor else None
        parsed_connector_ids = _parse_comma_separated_str(connector_ids) if not cursor else None
        parsed_indexing_status = _parse_comma_separated_str(indexing_status) if not cursor else None
        parsed_include = _parse_comma_separated_str(include)  # include is always from query params

        # Validate enum-based filters (lenient - invalid values are filtered out)
        if not cursor:
            parsed_node_types = _validate_enum_values(parsed_node_types, _get_enum_values(NodeType), "node_types")
            parsed_record_types = _validate_enum_values(parsed_record_types, _get_enum_values(RecordType), "record_types")
            parsed_origins = _validate_enum_values(parsed_origins, _get_enum_values(OriginType), "origins")
            # connector_ids is dynamic, no enum validation needed
            parsed_indexing_status = _validate_enum_values(parsed_indexing_status, _get_enum_values(ProgressStatus), "indexing_status")
        
        parsed_include = _validate_enum_values(
            parsed_include, _get_enum_values(IncludeOption), "include"
        )

        # Validate sort_by (only for first page)
        if not cursor and sort_by not in _get_enum_values(SortField):
            sort_by = SortField.NAME.value

        # Validate sort_order (only for first page)
        if not cursor and sort_order.lower() not in _get_enum_values(SortOrder):
            sort_order = SortOrder.ASC.value

        # Parse date and size ranges (only for first page)
        parsed_created_at = _parse_date_range(created_at) if not cursor else None
        parsed_updated_at = _parse_date_range(updated_at) if not cursor else None
        parsed_size = _parse_size_range(size) if not cursor else None

        result = await knowledge_hub_service.get_nodes(
            user_id=user_id,
            org_id=org_id,
            parent_id=parent_id,
            parent_type=parent_type,
            only_containers=only_containers if not cursor else False,
            cursor=cursor,
            limit=limit if not cursor else 50,
            sort_by=sort_by if not cursor else "updatedAt",
            sort_order=sort_order if not cursor else "desc",
            q=q if not cursor else None,
            node_types=parsed_node_types,
            record_types=parsed_record_types,
            origins=parsed_origins,
            connector_ids=parsed_connector_ids,
            indexing_status=parsed_indexing_status,
            created_at=parsed_created_at,
            updated_at=parsed_updated_at,
            size=parsed_size,
            flattened=flattened if not cursor else False,  # cursor has flattened
            include=parsed_include,
        )

        if not result.success:
            error_detail = result.error if result.error else "Failed to retrieve nodes"

            # Determine status code based on error message
            status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
            if error_detail:
                if "not found" in error_detail.lower():
                    status_code = status.HTTP_404_NOT_FOUND
                elif "type mismatch" in error_detail.lower() or "invalid" in error_detail.lower():
                    status_code = status.HTTP_400_BAD_REQUEST

            raise HTTPException(
                status_code=status_code,
                detail=error_detail
            )

        return result

    except HTTPException as he:
        raise he
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Unexpected error: {str(e)}"
        ) from e

