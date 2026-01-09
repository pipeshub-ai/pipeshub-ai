"""Knowledge Hub Unified Browse API Router"""

from typing import Any, Dict, List, Optional, Set, Union

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from app.connectors.sources.localKB.api.knowledge_hub_models import (
    ConnectorFilter,
    IncludeOption,
    IndexingStatusFilter,
    KnowledgeHubErrorResponse,
    KnowledgeHubNodesResponse,
    NodeType,
    RecordTypeFilter,
    SortField,
    SortOrder,
    SourceType,
    ViewMode,
)
from app.connectors.sources.localKB.handlers.knowledge_hub_service import (
    KnowledgeHubService,
)
from app.containers.connector import ConnectorAppContainer

knowledge_hub_router = APIRouter(
    prefix="/api/v2/knowledge-hub",
    tags=["Knowledge Hub"]
)


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
    return [item.strip() for item in value.split(',') if item.strip()]


def _parse_date_range(value: Optional[str]) -> Optional[Dict[str, Optional[int]]]:
    """Parse date range from query parameter"""
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
                    result[key] = int(val)
                except ValueError:
                    pass
    return result if result else None


def _parse_size_range(value: Optional[str]) -> Optional[Dict[str, Optional[int]]]:
    """Parse size range from query parameter"""
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
                    result[key] = int(val)
                except ValueError:
                    pass
    return result if result else None


@knowledge_hub_router.get(
    "/nodes",
    response_model=KnowledgeHubNodesResponse,
    responses={
        400: {"model": KnowledgeHubErrorResponse},
        500: {"model": KnowledgeHubErrorResponse},
    },
)
async def get_knowledge_hub_nodes(
    request: Request,
    parent_id: Optional[str] = Query(None, description="Parent node ID (null for root level)"),
    view: Optional[str] = Query(None, description="View mode: 'children' or 'search'"),
    only_containers: bool = Query(False, description="Only return nodes with children (for sidebar)"),
    page: int = Query(1, ge=1, description="Page number (1-indexed)"),
    limit: int = Query(50, ge=1, le=200, description="Items per page"),
    sort_by: str = Query("name", description="Sort field: name, createdAt, updatedAt, size, type"),
    sort_order: str = Query("asc", description="Sort order: asc or desc"),
    q: Optional[str] = Query(None, description="Full-text search query"),
    node_types: Optional[str] = Query(None, description="Comma-separated node types"),
    record_types: Optional[str] = Query(None, description="Comma-separated record types"),
    sources: Optional[str] = Query(None, description="Comma-separated sources: KB, CONNECTOR"),
    connectors: Optional[str] = Query(None, description="Comma-separated connector names"),
    indexing_status: Optional[str] = Query(None, description="Comma-separated indexing statuses"),
    created_at: Optional[str] = Query(None, description="Created date range: gte:timestamp,lte:timestamp"),
    updated_at: Optional[str] = Query(None, description="Updated date range: gte:timestamp,lte:timestamp"),
    size: Optional[str] = Query(None, description="Size range: gte:bytes,lte:bytes"),
    include: Optional[str] = Query(None, description="Comma-separated includes: breadcrumbs, counts, availableFilters, permissions"),
    knowledge_hub_service: KnowledgeHubService = Depends(get_knowledge_hub_service),
) -> Union[KnowledgeHubNodesResponse, Dict[str, Any]]:
    """
    Get nodes for the Knowledge Hub unified browse API.

    Supports:
    - Root level browsing (KBs and Apps)
    - KB/folder/app/record group/record children browsing
    - Global search with filters
    - Sidebar expansion (onlyContainers=true)
    """
    try:
        user_id = request.state.user.get("userId")
        org_id = request.state.user.get("orgId")

        if not user_id or not org_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="userId and orgId are required"
            )

        # Parse comma-separated parameters
        parsed_node_types = _parse_comma_separated_str(node_types)
        parsed_record_types = _parse_comma_separated_str(record_types)
        parsed_sources = _parse_comma_separated_str(sources)
        parsed_connectors = _parse_comma_separated_str(connectors)
        parsed_indexing_status = _parse_comma_separated_str(indexing_status)
        parsed_include = _parse_comma_separated_str(include)

        # Validate enum-based filters (lenient - invalid values are filtered out)
        parsed_node_types = _validate_enum_values(
            parsed_node_types, _get_enum_values(NodeType), "node_types"
        )
        parsed_record_types = _validate_enum_values(
            parsed_record_types, _get_enum_values(RecordTypeFilter), "record_types"
        )
        parsed_sources = _validate_enum_values(
            parsed_sources, _get_enum_values(SourceType), "sources"
        )
        parsed_connectors = _validate_enum_values(
            parsed_connectors, _get_enum_values(ConnectorFilter), "connectors"
        )
        parsed_indexing_status = _validate_enum_values(
            parsed_indexing_status, _get_enum_values(IndexingStatusFilter), "indexing_status"
        )
        parsed_include = _validate_enum_values(
            parsed_include, _get_enum_values(IncludeOption), "include"
        )

        # Validate view mode
        if view and view not in _get_enum_values(ViewMode):
            view = None  # Reset to default if invalid

        # Validate sort_by
        if sort_by not in _get_enum_values(SortField):
            sort_by = SortField.NAME.value

        # Validate sort_order
        if sort_order.lower() not in _get_enum_values(SortOrder):
            sort_order = SortOrder.ASC.value

        # Parse date and size ranges
        parsed_created_at = _parse_date_range(created_at)
        parsed_updated_at = _parse_date_range(updated_at)
        parsed_size = _parse_size_range(size)

        # Call service
        result = await knowledge_hub_service.get_nodes(
            user_id=user_id,
            org_id=org_id,
            parent_id=parent_id,
            view=view,
            only_containers=only_containers,
            page=page,
            limit=limit,
            sort_by=sort_by,
            sort_order=sort_order,
            q=q,
            node_types=parsed_node_types,
            record_types=parsed_record_types,
            sources=parsed_sources,
            connectors=parsed_connectors,
            indexing_status=parsed_indexing_status,
            created_at=parsed_created_at,
            updated_at=parsed_updated_at,
            size=parsed_size,
            include=parsed_include,
        )

        if not result.success:
            error_detail = result.error if result.error else "Failed to retrieve nodes"
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=error_detail
            )

        return result

    except HTTPException as he:
        raise he
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Unexpected error: {str(e)}"
        )

