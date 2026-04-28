"""Knowledge Hub Unified Browse Service"""

import logging
import re
import traceback
from collections import Counter
from typing import Any

from app.config.constants.arangodb import ProgressStatus
from app.utils.cursor import (
    build_partition_cursors,
    compute_query_offsets_for_prev,
    decode_cursor,
    extract_filters_from_cursor,
    extract_partition_offsets,
)
from app.connectors.sources.localKB.api.knowledge_hub_models import (
    AppliedFilters,
    AvailableFilters,
    BreadcrumbItem,
    CountItem,
    CountsInfo,
    CurrentNode,
    FilterOption,
    FiltersInfo,
    ItemPermission,
    KnowledgeHubNodesResponse,
    NodeItem,
    NodeType,
    OriginType,
    PaginationInfo,
    PermissionsInfo,
    SortField,
    SortOrder,
)
from app.models.entities import RecordType
from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

FOLDER_MIME_TYPES = [
    'application/vnd.folder',
    'application/vnd.google-apps.folder',
    'text/directory'
]

def _get_node_type_value(node_type: NodeType | str) -> str:
    """Safely extract the string value from a NodeType enum or string."""
    if hasattr(node_type, 'value'):
        return node_type.value
    return str(node_type)

class KnowledgeHubService:
    """Service for unified Knowledge Hub browse API"""

    def __init__(
        self,
        logger: logging.Logger,
        graph_provider: IGraphDBProvider,
    ) -> None:
        self.logger = logger
        self.graph_provider = graph_provider

    def _has_flattening_filters(self, q: str | None, node_types: list[str] | None,
                                 record_types: list[str] | None, origins: list[str] | None,
                                 connector_ids: list[str] | None,
                                 indexing_status: list[str] | None,
                                 created_at: dict[str, int | None] | None, updated_at: dict[str, int | None] | None,
                                 size: dict[str, int | None] | None) -> bool:
        """Check if any filters that should trigger flattened/recursive search are provided.

        These filters should return flattened results (all nested children):
        - q, nodeTypes, recordTypes, origins, connectorIds,
          createdAt, updatedAt, size, indexingStatus
        Note: sortBy and sortOrder are NOT included as they don't trigger flattening.
        """
        return any([q, node_types, record_types, origins, connector_ids,
                    indexing_status, created_at, updated_at, size])

    async def get_nodes(
        self,
        user_id: str,
        org_id: str,
        parent_id: str | None = None,
        parent_type: str | None = None,
        only_containers: bool = False,
        cursor: str | None = None,
        limit: int = 50,
        sort_by: str = "updatedAt",
        sort_order: str = "desc",
        q: str | None = None,
        node_types: list[str] | None = None,
        record_types: list[str] | None = None,
        origins: list[str] | None = None,
        connector_ids: list[str] | None = None,
        indexing_status: list[str] | None = None,
        created_at: dict[str, int | None] | None = None,
        updated_at: dict[str, int | None] | None = None,
        size: dict[str, int | None] | None = None,
        flattened: bool = False,
        include: list[str] | None = None,
        record_group_ids: list[str] | None = None,
    ) -> KnowledgeHubNodesResponse:
        """
        Get nodes for the Knowledge Hub unified browse API.
        
        Uses cursor-based pagination:
        - First page: no cursor, use query params for filters
        - Subsequent pages: cursor contains complete state (filters, sort, parent context)
        """
        try:
            limit = min(max(1, limit), 200)

            # Get user key
            user = await self.graph_provider.get_user_by_user_id(user_id=user_id)
            if not user:
                return KnowledgeHubNodesResponse(
                    success=False,
                    error="User not found",
                    id=parent_id,
                    items=[],
                    pagination=PaginationInfo(
                        limit=limit, totalItems=0, currentPageItems=0,
                        startIndex=0, endIndex=0,
                        hasNext=False, hasPrev=False
                    ),
                    filters=FiltersInfo(applied=AppliedFilters()),
                )
            
            user_key = user.get('_key')
            
            # When filters are applied we want flattened results; otherwise use request's flattened flag
            has_flattening_filters = self._has_flattening_filters(
                q, node_types, record_types, origins, connector_ids,
                indexing_status, created_at, updated_at, size
            )
            flattened_result = has_flattening_filters or flattened

            items_seen_so_far = 0
            if cursor:
                cursor_data = decode_cursor(cursor, self.logger)
                if cursor_data:
                    cursor_filters = extract_filters_from_cursor(cursor_data)
                    items_seen_so_far = cursor_filters.get("items_seen", 0)
                    if parent_id is None and cursor_filters.get("parent_id"):
                        parent_id = cursor_filters["parent_id"]
                    if parent_type is None and cursor_filters.get("parent_type"):
                        parent_type = cursor_filters["parent_type"]
            
            available_filters = None
            items, total_count, cursors, available_filters, actual_limit = await self._search_nodes(
                user_key=user_key,
                org_id=org_id,
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
                only_containers=only_containers,
                parent_id=parent_id,
                parent_type=parent_type,
                flattened=flattened_result,
                include_filters=include and 'availableFilters' in include,
                items_seen_so_far=items_seen_so_far,
            )
            if include and 'availableFilters' in include and available_filters is None:
                available_filters = await self._get_available_filters(user_key, org_id)

            current_node = None
            parent_node = None
            effective_parent_id = parent_id
            if parent_id:
                current_node = await self._get_current_node_info(parent_id)
                parent_info = await self.graph_provider.get_knowledge_hub_parent_node(
                    node_id=parent_id,
                    folder_mime_types=FOLDER_MIME_TYPES,
                )
                if parent_info and parent_info.get('id') and parent_info.get('name'):
                    parent_node = CurrentNode(
                        id=parent_info['id'],
                        name=parent_info['name'],
                        nodeType=parent_info['nodeType'],
                        subType=parent_info.get('subType'),
                    )

            # Build applied filters
            applied_filters = AppliedFilters(
                q=q,
                nodeTypes=node_types,
                recordTypes=record_types,
                origins=origins,
                connectorIds=connector_ids,
                indexingStatus=indexing_status,
                createdAt=created_at,
                updatedAt=updated_at,
                size=size,
                sortBy=sort_by,
                sortOrder=sort_order,
            )

            filters_info = FiltersInfo(applied=applied_filters)

            start_index = items_seen_so_far + 1 if len(items) > 0 else 0
            end_index = items_seen_so_far + len(items)

            response = KnowledgeHubNodesResponse(
                success=True,
                id=effective_parent_id,
                currentNode=current_node,
                parentNode=parent_node,
                items=items,
                pagination=PaginationInfo(
                    limit=actual_limit,  # Use actual limit from provider
                    totalItems=total_count,
                    currentPageItems=len(items),  # Actual items in current page
                    startIndex=start_index,  # 1-based start index for display
                    endIndex=end_index,  # 1-based end index for display
                    hasNext=cursors.get('next') is not None,
                    hasPrev=cursors.get('prev') is not None,
                    nextCursor=cursors.get('next'),
                    prevCursor=cursors.get('prev'),
                ),
                filters=filters_info,
            )

            # Add optional expansions
            if include:
                if 'availableFilters' in include:
                    # Add available filters only when requested
                    response.filters.available = available_filters

                if 'breadcrumbs' in include and parent_id:
                    response.breadcrumbs = await self._get_breadcrumbs(parent_id)

                if 'counts' in include:
                    # TODO(Counts): Per-type breakdown only reflects current page items, not all
                    # filtered results. The 'total' is correct, but 'items' breakdown is inaccurate
                    # for paginated results. To fix properly, add a separate aggregation query
                    # that counts by nodeType across the entire filtered result set.
                    type_counts = Counter(_get_node_type_value(item.nodeType) for item in items)

                    # Map nodeType to display label
                    label_map = {
                        'app': 'apps',
                        'folder': 'folders',
                        'recordGroup': 'groups',
                        'record': 'records',
                    }

                    count_items = [
                        CountItem(
                            label=label_map.get(node_type, node_type),
                            count=count
                        )
                        for node_type, count in sorted(type_counts.items())
                    ]

                    response.counts = CountsInfo(
                        items=count_items,
                        total=total_count,
                    )

                if 'permissions' in include:
                    response.permissions = await self._get_permissions(
                        user_key, org_id, parent_id, parent_type
                    )

            return response

        except ValueError as ve:
            # Validation errors (404 - not found, 400 - type mismatch)
            self.logger.warning(f"⚠️ Validation error: {str(ve)}")
            return KnowledgeHubNodesResponse(
                success=False,
                error=str(ve),
                id=parent_id,
                items=[],
                pagination=PaginationInfo(
                    limit=limit, totalItems=0,
                    hasNext=False, hasPrev=False,
                    nextCursor=None, prevCursor=None
                ),
                filters=FiltersInfo(applied=AppliedFilters()),
            )
        except Exception as e:
            self.logger.error(f"❌ Failed to get nodes: {str(e)}")
            self.logger.error(traceback.format_exc())
            return KnowledgeHubNodesResponse(
                success=False,
                error=f"Failed to retrieve nodes: {str(e)}",
                id=parent_id,
                items=[],
                pagination=PaginationInfo(
                    limit=limit, totalItems=0,
                    hasNext=False, hasPrev=False,
                    nextCursor=None, prevCursor=None
                ),
                filters=FiltersInfo(applied=AppliedFilters()),
            )

    async def _get_available_filters(self, user_key: str, org_id: str) -> AvailableFilters:
        """Get filter options (dynamic Apps + static others)"""
        try:
            options = await self.graph_provider.get_knowledge_hub_filter_options(user_key, org_id)
            apps_data = options.get('apps', [])

            # App/Connector options with connectorType
            app_options = [
                FilterOption(
                    id=a['id'],
                    label=a['name'],
                    connectorType=a.get('type', a.get('name'))
                )
                for a in apps_data
            ]

            # Node type labels mapping
            node_type_labels = {
                NodeType.FOLDER: "Folder",
                NodeType.RECORD: "File",
                NodeType.RECORD_GROUP: "Drive/Root",
                NodeType.APP: "Connector",
            }

            return AvailableFilters(
                nodeTypes=[
                    FilterOption(
                        id=nt.value,
                        label=node_type_labels.get(nt, nt.value)
                    )
                    for nt in NodeType
                ],
                recordTypes=[
                    FilterOption(
                        id=rt.value,
                        label=self._format_enum_label(rt.value)
                    )
                    for rt in RecordType
                ],
                origins=[
                    FilterOption(
                        id=ot.value,
                        label="Collection" if ot == OriginType.COLLECTION else "External Connector"
                    )
                    for ot in OriginType
                ],
                connectors=app_options,
                indexingStatus=[
                    FilterOption(
                        id=status.value,
                        label=self._format_enum_label(status.value, {"AUTO_INDEX_OFF": "Manual Indexing"})
                    )
                    for status in ProgressStatus
                ],
                sortBy=[
                    FilterOption(
                        id=sf.value,
                        label=self._format_enum_label(sf.value, {"createdAt": "Created Date", "updatedAt": "Modified Date"})
                    )
                    for sf in SortField
                ],
                sortOrder=[
                    FilterOption(
                        id=so.value,
                        label="Ascending" if so == SortOrder.ASC else "Descending"
                    )
                    for so in SortOrder
                ]
            )
        except Exception as e:
            self.logger.error(f"Failed to get available filters: {e}")
            return AvailableFilters()

    async def _search_nodes(
        self,
        user_key: str,
        org_id: str,
        cursor: str | None,
        limit: int,
        sort_by: str,
        sort_order: str,
        q: str | None,
        node_types: list[str] | None,
        record_types: list[str] | None,
        origins: list[str] | None,
        connector_ids: list[str] | None,
        indexing_status: list[str] | None,
        created_at: dict[str, int | None] | None,
        updated_at: dict[str, int | None] | None,
        size: dict[str, int | None] | None,
        only_containers: bool,
        parent_id: str | None = None,
        parent_type: str | None = None,
        flattened: bool = False,
        include_filters: bool = False,
        record_group_ids: list[str] | None = None,
        strict_permission: bool = False,
        items_seen_so_far: int = 0,
    ) -> tuple[list[NodeItem], int, AvailableFilters | None, int]:
        """
        Search for nodes (global or scoped within parent) using v2 cursor-based pagination.

        This unified method handles both:
        - Global search: When parent_id is None, searches across all nodes
        - Scoped search: When parent_id is provided, searches within parent and descendants
        - When parent_id set: flattened=False returns only direct children; flattened=True returns all descendants

        Args:
            user_key: User's key for permission filtering
            org_id: Organization ID
            cursor: Pagination cursor (contains complete state including filters)
            limit: Maximum number of items to return
            sort_by: Sort field (used for first page, cursor has its own for subsequent)
            sort_order: Sort order (asc/desc)
            q: Optional search query
            node_types: Optional list of node types to filter by
            record_types: Optional list of record types to filter by
            origins: Optional list of origins to filter by
            connector_ids: Optional list of connector IDs to filter by
            indexing_status: Optional list of indexing statuses to filter by
            created_at: Optional date range filter for creation date
            updated_at: Optional date range filter for update date
            size: Optional size range filter
            only_containers: If True, only return nodes that can have children
            parent_id: Optional parent to scope search within (None for global)
            parent_type: Type of parent (required if parent_id provided)
            flattened: If True and parent_id set, return all descendants; if False, only direct children
            include_filters: Whether to fetch available filters

        Returns:
            Tuple of (items, total_count, cursors, available_filters)
        """
        try:
            # Build sort clause
            sort_field_map = {
                "name": "name",
                "createdAt": "createdAt",
                "updatedAt": "updatedAt",
                "size": "sizeInBytes",
                "type": "nodeType",
            }
            sort_field = sort_field_map.get(sort_by, "name")
            sort_dir = "ASC" if sort_order.lower() == "asc" else "DESC"

            cursor_data = decode_cursor(cursor, self.logger) if cursor else None

            partition_offsets: dict[str, int] = {}
            cached_total = None
            is_prev_page = False
            
            if cursor_data:
                cursor_filters = extract_filters_from_cursor(cursor_data)
                if cursor_filters.get("search_query") is not None:
                    q = cursor_filters["search_query"]
                if cursor_filters.get("node_types") is not None:
                    node_types = cursor_filters["node_types"]
                if cursor_filters.get("record_types") is not None:
                    record_types = cursor_filters["record_types"]
                if cursor_filters.get("origins") is not None:
                    origins = cursor_filters["origins"]
                if cursor_filters.get("connector_ids") is not None:
                    connector_ids = cursor_filters["connector_ids"]
                if cursor_filters.get("indexing_status") is not None:
                    indexing_status = cursor_filters["indexing_status"]
                if cursor_filters.get("created_at") is not None:
                    created_at = cursor_filters["created_at"]
                if cursor_filters.get("updated_at") is not None:
                    updated_at = cursor_filters["updated_at"]
                if cursor_filters.get("size") is not None:
                    size = cursor_filters["size"]
                if cursor_filters.get("only_containers") is not None:
                    only_containers = cursor_filters["only_containers"]
                if cursor_filters.get("flattened") is not None:
                    flattened = cursor_filters["flattened"]
                if cursor_filters.get("parent_id") is not None:
                    parent_id = cursor_filters["parent_id"]
                if cursor_filters.get("parent_type") is not None:
                    parent_type = cursor_filters["parent_type"]
                if cursor_filters.get("sort_field") is not None:
                    sort_field = cursor_filters["sort_field"]
                if cursor_filters.get("sort_dir") is not None:
                    sort_dir = cursor_filters["sort_dir"]
                if cursor_filters.get("limit") is not None:
                    limit = cursor_filters["limit"]
                
                # Extract total from cursor (for performance optimization)
                if cursor_filters.get("total_items") is not None:
                    cached_total = cursor_filters["total_items"]
                
                cursor_offsets = extract_partition_offsets(cursor_data)
                is_prev_page = cursor_data.get("dir") == "prev"
                if is_prev_page:
                    partition_offsets = compute_query_offsets_for_prev(cursor_offsets, limit)
                else:
                    partition_offsets = {cid: idx + 1 for cid, idx in cursor_offsets.items() if idx >= 0}

            result = await self.graph_provider.get_knowledge_hub_search_v3(
                org_id=org_id,
                user_key=user_key,
                limit=limit,
                sort_field=sort_field,
                sort_dir=sort_dir,
                partition_offsets=partition_offsets if partition_offsets else None,
                is_prev_page=is_prev_page,
                cached_total=cached_total,
                search_query=q,
                node_types=node_types,
                record_types=record_types,
                origins=origins,
                connector_ids=connector_ids,
                indexing_status=indexing_status,
                created_at=created_at,
                updated_at=updated_at,
                size=size,
                only_containers=only_containers,
                parent_id=parent_id,
                parent_type=parent_type,
                flattened=flattened,
                strict_permission=strict_permission,
            )

            nodes_data = result.get('nodes', [])
            total_count = result.get('total', 0)
            partition_ranges = result.get('partition_ranges', {})
            is_first_page = cursor_data is None
            has_more_items = result.get('has_more_items', False)

            items = [self._doc_to_node_item(node_doc) for node_doc in nodes_data]
            
            cursors = build_partition_cursors(
                partition_ranges=partition_ranges,
                is_first_page=is_first_page,
                has_more_items=has_more_items,
                sort_field=sort_field,
                sort_dir=sort_dir,
                limit=limit,
                search_query=q,
                node_types=node_types,
                record_types=record_types,
                origins=origins,
                connector_ids=connector_ids,
                indexing_status=indexing_status,
                created_at=created_at,
                updated_at=updated_at,
                size=size,
                only_containers=only_containers,
                parent_id=parent_id,
                parent_type=parent_type,
                flattened=flattened,
                total_items=total_count,
                base_offsets=partition_offsets,
                items_seen_after_page=items_seen_so_far + len(items),
                items_seen_before_prev_page=max(0, items_seen_so_far - limit),
            )

            available_filters = None
            if include_filters:
                available_filters = await self._get_available_filters(user_key, org_id)

            return items, total_count, cursors, available_filters, limit

        except Exception as e:
            scope = f"within parent {parent_id}" if parent_id else "globally"
            self.logger.error(f"❌ Failed to search nodes {scope}: {str(e)}")
            self.logger.error(traceback.format_exc())
            raise

    async def _validate_node_existence_and_type(
        self,
        node_id: str,
        expected_type: str,
        user_key: str,
        org_id: str
    ) -> None:
        """
        Validate that a node exists and matches the expected type.

        Raises:
            KnowledgeHubNodesResponse with error if validation fails
        """
        # Get node info
        node_info = await self.graph_provider.get_knowledge_hub_node_info(
            node_id=node_id,
            folder_mime_types=FOLDER_MIME_TYPES,
        )

        if not node_info:
            raise ValueError(f"Node with ID '{node_id}' not found")

        actual_type = node_info.get('nodeType')

        # Validate type matches
        if actual_type != expected_type:
            raise ValueError(
                f"Node type mismatch: node '{node_id}' is not '{expected_type}', it is '{actual_type}'. Use /nodes/{actual_type}/{node_id} instead."
            )

        # Validate user has access (check permissions)
        # For now, the queries already filter by user permissions, but we could add explicit check here
        # TODO: Add explicit permission check if needed

    async def _get_current_node_info(self, node_id: str) -> CurrentNode | None:
        """Get current node information (the node being browsed)"""
        node_info = await self.graph_provider.get_knowledge_hub_node_info(
            node_id=node_id,
            folder_mime_types=FOLDER_MIME_TYPES,
        )
        if node_info and node_info.get('id') and node_info.get('name'):
            return CurrentNode(
                id=node_info['id'],
                name=node_info['name'],
                nodeType=node_info['nodeType'],
                subType=node_info.get('subType'),
            )
        return None

    async def _get_breadcrumbs(self, node_id: str) -> list[BreadcrumbItem]:
        """
        Get breadcrumb trail for a node using the optimized provider method.
        """
        try:
            # Use the provider's optimized AQL query
            breadcrumbs_data = await self.graph_provider.get_knowledge_hub_breadcrumbs(node_id=node_id)

            # Convert to BreadcrumbItem objects
            return [
                BreadcrumbItem(
                    id=item['id'],
                    name=item['name'],
                    nodeType=item['nodeType'],
                    subType=item.get('subType')
                )
                for item in breadcrumbs_data
            ]


        except Exception as e:
            self.logger.error(f"❌ Failed to get breadcrumbs: {str(e)}")
            self.logger.error(traceback.format_exc())
            # Fallback: return empty list or just current node if possible
            return []

    async def _get_permissions(
        self,
        user_key: str,
        org_id: str,
        parent_id: str | None,
        parent_type: str | None = None,
    ) -> PermissionsInfo | None:
        """Get user permissions for the current context. Returns None if user has no permission."""
        try:
            perm_data = await self.graph_provider.get_knowledge_hub_context_permissions(
                user_key=user_key,
                org_id=org_id,
                parent_id=parent_id,
                parent_type=parent_type,
            )

            # If role is None, user has no permission - return None
            role = perm_data.get('role')
            if role is None:
                return None

            return PermissionsInfo(
                role=role,
                canUpload=perm_data.get('canUpload', False),
                canCreateFolders=perm_data.get('canCreateFolders', False),
                canEdit=perm_data.get('canEdit', False),
                canDelete=perm_data.get('canDelete', False),
                canManagePermissions=perm_data.get('canManagePermissions', False),
            )

        except Exception as e:
            self.logger.error(f"❌ Failed to get permissions: {str(e)}")
            self.logger.error(traceback.format_exc())
            # Return None on error (no permission granted)
            return None

    def _doc_to_node_item(self, doc: dict[str, Any]) -> NodeItem:
        """Convert a database document to a NodeItem"""
        # Extract ID - prefer 'id' field, fallback to '_key' or parse from '_id'
        doc_id = doc.get('id')
        if not isinstance(doc_id, str) or not doc_id.strip():
            if doc.get('_key'):
                doc_id = doc['_key']
            elif doc.get('_id'):
                _id_value = doc['_id']
                if isinstance(_id_value, str) and '/' in _id_value:
                    doc_id = _id_value.split('/', 1)[1]
                else:
                    doc_id = _id_value
            else:
                doc_id = ''

        node_type_str = doc.get('nodeType', 'record')
        try:
            node_type = NodeType(node_type_str)
        except ValueError:
            node_type = NodeType.RECORD

        # Get origin
        origin_str = doc.get('origin', 'COLLECTION')
        origin = OriginType.COLLECTION if origin_str == 'COLLECTION' else OriginType.CONNECTOR

        # Convert userRole to ItemPermission if present
        permission = None
        user_role = doc.get('userRole')
        if user_role:
            # Handle case where userRole might be a list (defensive safeguard)
            if isinstance(user_role, list):
                user_role = user_role[0] if user_role else None
            if user_role:
                permission = self._role_to_permission(user_role)

        # Build NodeItem
        return NodeItem(
            id=doc_id,
            name=(doc.get('name') or ''),
            nodeType=node_type,
            parentId=doc.get('parentId'),
            origin=origin,
            connector=doc.get('connector'),
            recordType=doc.get('recordType'),
            recordGroupType=doc.get('recordGroupType'),
            indexingStatus=doc.get('indexingStatus'),
            reason=doc.get('reason'),
            createdAt=doc.get('createdAt', 0),
            updatedAt=doc.get('updatedAt', 0),
            sizeInBytes=doc.get('sizeInBytes'),
            mimeType=doc.get('mimeType'),
            extension=doc.get('extension'),
            webUrl=doc.get('webUrl'),
            hasChildren=doc.get('hasChildren', False),
            previewRenderable=doc.get('previewRenderable'),
            permission=permission,
            sharingStatus=doc.get('sharingStatus'),
            isInternal=bool(doc.get('isInternal', False)),
        )


    def _role_to_permission(self, role: str) -> ItemPermission:
        """
        Convert a user role string to ItemPermission object with computed flags.

        Permission hierarchy:
        - OWNER, ADMIN: Full control (edit + delete)
        - EDITOR, WRITER: Can edit but not delete
        - COMMENTER, READER: Read-only (no edit, no delete)
        """
        role_upper = role.upper() if role else ''

        # Determine edit and delete permissions based on role
        can_edit = role_upper in ['OWNER', 'ADMIN', 'EDITOR', 'WRITER']
        can_delete = role_upper in ['OWNER', 'ADMIN']

        return ItemPermission(
            role=role,
            canEdit=can_edit,
            canDelete=can_delete,
        )

    def _format_enum_label(self, value: str, special_cases: dict[str, str] | None = None) -> str:
        """
        Convert enum value to human-readable label.

        Handles both UPPER_SNAKE_CASE and camelCase:
        - "FILE_NAME" → "File Name"
        - "createdAt" → "Created At"
        - "autoIndexOff" → "Auto Index Off"

        Args:
            value: The enum value to format
            special_cases: Optional dict of special case mappings that differ from generic formatting

        Returns:
            Human-readable label
        """
        if special_cases and value in special_cases:
            return special_cases[value]

        # Handle camelCase by inserting space before uppercase letters
        # Insert space before uppercase letters that follow lowercase letters
        spaced = re.sub(r'([a-z])([A-Z])', r'\1 \2', value)
        # Replace underscores with spaces
        spaced = spaced.replace("_", " ")
        # Title case each word
        return spaced.title()
