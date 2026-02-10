"""Knowledge Hub Unified Browse Service"""

import re
import traceback
from collections import Counter
from typing import Any, Dict, List, Optional, Tuple

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
from app.models.entities import IndexingStatus, RecordType
from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

FOLDER_MIME_TYPES = [
    'application/vnd.folder',
    'application/vnd.google-apps.folder',
    'text/directory'
]

def _get_node_type_value(node_type) -> str:
    """Safely extract the string value from a NodeType enum or string."""
    if hasattr(node_type, 'value'):
        return node_type.value
    return str(node_type)

class KnowledgeHubService:
    """Service for unified Knowledge Hub browse API"""

    def __init__(
        self,
        logger,
        graph_provider: IGraphDBProvider,
    ) -> None:
        self.logger = logger
        self.graph_provider = graph_provider

    def _has_search_filters(self, q: Optional[str], node_types: Optional[List[str]],
                             record_types: Optional[List[str]], origins: Optional[List[str]],
                             connector_ids: Optional[List[str]], kb_ids: Optional[List[str]],
                             indexing_status: Optional[List[str]],
                             created_at: Optional[Dict], updated_at: Optional[Dict],
                             size: Optional[Dict]) -> bool:
        """Check if any search/filter parameters are provided."""
        return any([q, node_types, record_types, origins, connector_ids, kb_ids,
                    indexing_status, created_at, updated_at, size])

    def _has_flattening_filters(self, q: Optional[str], node_types: Optional[List[str]],
                                 record_types: Optional[List[str]], origins: Optional[List[str]],
                                 connector_ids: Optional[List[str]], kb_ids: Optional[List[str]],
                                 indexing_status: Optional[List[str]],
                                 created_at: Optional[Dict], updated_at: Optional[Dict],
                                 size: Optional[Dict]) -> bool:
        """Check if any filters that should trigger flattened/recursive search are provided.

        These filters should return flattened results (all nested children):
        - q, nodeTypes, recordTypes, origins, connectorIds, kbIds,
          createdAt, updatedAt, size, indexingStatus
        Note: sortBy and sortOrder are NOT included as they don't trigger flattening.
        """
        return any([q, node_types, record_types, origins, connector_ids, kb_ids,
                    indexing_status, created_at, updated_at, size])

    async def get_nodes(
        self,
        user_id: str,
        org_id: str,
        parent_id: Optional[str] = None,
        parent_type: Optional[str] = None,
        only_containers: bool = False,
        page: int = 1,
        limit: int = 50,
        sort_by: str = "updatedAt",
        sort_order: str = "desc",
        q: Optional[str] = None,
        node_types: Optional[List[str]] = None,
        record_types: Optional[List[str]] = None,
        origins: Optional[List[str]] = None,
        connector_ids: Optional[List[str]] = None,
        kb_ids: Optional[List[str]] = None,
        indexing_status: Optional[List[str]] = None,
        created_at: Optional[Dict[str, Optional[int]]] = None,
        updated_at: Optional[Dict[str, Optional[int]]] = None,
        size: Optional[Dict[str, Optional[int]]] = None,
        flattened: bool = False,
        include: Optional[List[str]] = None,
    ) -> KnowledgeHubNodesResponse:
        """
        Get nodes for the Knowledge Hub unified browse API
        """
        try:
            # Determine if this is a search request
            is_search = self._has_search_filters(
                q, node_types, record_types, origins, connector_ids, kb_ids,
                indexing_status, created_at, updated_at, size
            )

            # Validate pagination
            page = max(1, page)
            limit = min(max(1, limit), 200)  # Max 200
            skip = (page - 1) * limit

            # Get user key
            user = await self.graph_provider.get_user_by_user_id(user_id=user_id)
            if not user:
                return KnowledgeHubNodesResponse(
                    success=False,
                    error="User not found",
                    id=parent_id,
                    items=[],
                    pagination=PaginationInfo(
                        page=page, limit=limit, totalItems=0, totalPages=0,
                        hasNext=False, hasPrev=False
                    ),
                    filters=FiltersInfo(applied=AppliedFilters()),
                )
            user_key = user.get('_key')

            # Get nodes based on request type
            # If parent_id is provided with flattening filters or flattened=true, do recursive search
            # If parent_id is provided without filters, browse direct children only
            # If no parent_id with search filters, do global search

            # Check if flattening filters are applied (these should return flattened results)
            has_flattening_filters = self._has_flattening_filters(
                q, node_types, record_types, origins, connector_ids, kb_ids,
                indexing_status, created_at, updated_at, size
            )

            # Initialize available_filters
            available_filters = None

            if (parent_id and (has_flattening_filters or flattened)) or (is_search and parent_id is None):
                # Search mode: Global search (no parent) or scoped search (within parent and descendants)
                items, total_count, available_filters = await self._search_nodes(
                    user_key=user_key,
                    org_id=org_id,
                    skip=skip,
                    limit=limit,
                    sort_by=sort_by,
                    sort_order=sort_order,
                    q=q,
                    node_types=node_types,
                    record_types=record_types,
                    origins=origins,
                    connector_ids=connector_ids,
                    kb_ids=kb_ids,
                    indexing_status=indexing_status,
                    created_at=created_at,
                    updated_at=updated_at,
                    size=size,
                    only_containers=only_containers,
                    parent_id=parent_id,  # None for global search, set for scoped search
                    parent_type=parent_type,
                    include_filters=(parent_id is None) or (include and 'availableFilters' in include),
                )
            else:
                # Browse mode - get direct children of parent only
                items, total_count, _ = await self._get_children_nodes(
                    user_key=user_key,
                    org_id=org_id,
                    parent_id=parent_id,
                    parent_type=parent_type,
                    skip=skip,
                    limit=limit,
                    sort_by=sort_by,
                    sort_order=sort_order,
                    q=None,  # No search query for browse mode
                    node_types=node_types,
                    record_types=record_types,
                    origins=origins,
                    connector_ids=connector_ids,
                    kb_ids=kb_ids,
                    indexing_status=indexing_status,
                    created_at=created_at,
                    updated_at=updated_at,
                    size=size,
                    only_containers=only_containers,
                )
                # In browse mode, fetch available filters only if requested
                if include and 'availableFilters' in include:
                    available_filters = await self._get_available_filters(user_key, org_id)

            # Permissions are now included directly from queries (userRole field)
            # No need for separate batch permission fetch

            # Calculate pagination
            total_pages = (total_count + limit - 1) // limit if total_count > 0 else 0

            # Build current node info if parent_id is provided
            current_node = None
            parent_node = None
            if parent_id:
                current_node = await self._get_current_node_info(parent_id)
                # Get parent node info using provider's parent lookup
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
                kbIds=kb_ids,
                indexingStatus=indexing_status,
                createdAt=created_at,
                updatedAt=updated_at,
                size=size,
                sortBy=sort_by,
                sortOrder=sort_order,
            )

            # Build filters info (without available filters initially)
            filters_info = FiltersInfo(applied=applied_filters)

            # Build response
            response = KnowledgeHubNodesResponse(
                success=True,
                id=parent_id,
                currentNode=current_node,
                parentNode=parent_node,
                items=items,
                pagination=PaginationInfo(
                    page=page,
                    limit=limit,
                    totalItems=total_count,
                    totalPages=total_pages,
                    hasNext=page < total_pages,
                    hasPrev=page > 1,
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
                        'kb': 'knowledge bases',
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
                        total=total_count,  # Use actual total count, not paginated length
                    )

                if 'permissions' in include:
                    response.permissions = await self._get_permissions(user_key, org_id, parent_id)

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
                    page=page, limit=limit, totalItems=0, totalPages=0,
                    hasNext=False, hasPrev=False
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
                    page=page, limit=limit, totalItems=0, totalPages=0,
                    hasNext=False, hasPrev=False
                ),
                filters=FiltersInfo(applied=AppliedFilters()),
            )

    async def _get_children_nodes(
        self,
        user_key: str,
        org_id: str,
        parent_id: Optional[str],
        parent_type: Optional[str],  # Now passed directly from router
        skip: int,
        limit: int,
        sort_by: str,
        sort_order: str,
        q: Optional[str],  # Search query to filter within children
        node_types: Optional[List[str]],
        record_types: Optional[List[str]],
        origins: Optional[List[str]],
        connector_ids: Optional[List[str]],
        kb_ids: Optional[List[str]],
        indexing_status: Optional[List[str]],
        created_at: Optional[Dict[str, Optional[int]]],
        updated_at: Optional[Dict[str, Optional[int]]],
        size: Optional[Dict[str, Optional[int]]],
        only_containers: bool,
    ) -> Tuple[List[NodeItem], int, Optional[AvailableFilters]]:
        """Get children nodes for a given parent using unified provider method."""
        if parent_id is None:
            # Root level: return KBs and Apps
            return await self._get_root_level_nodes(
                user_key, org_id, skip, limit, sort_by, sort_order,
                node_types, origins, connector_ids, kb_ids, only_containers
            )

        # Validate that the node exists and type matches
        await self._validate_node_existence_and_type(parent_id, parent_type, user_key, org_id)

        # Type is now known from the URL path - no DB lookup needed!

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

        # Use provider method for simple tree navigation (no filters)
        # For filtered results, the API should use the search endpoint instead
        result = await self.graph_provider.get_knowledge_hub_children(
            parent_id=parent_id,
            parent_type=parent_type,
            org_id=org_id,
            user_key=user_key,
            skip=skip,
            limit=limit,
            sort_field=sort_field,
            sort_dir=sort_dir,
            only_containers=only_containers,
        )

        nodes_data = result.get('nodes', [])
        total_count = result.get('total', 0)

        # Convert to NodeItem objects
        items = [self._doc_to_node_item(node_doc) for node_doc in nodes_data]

        # Available filters are always None for browse mode (children)
        # They're only returned in search mode or can be fetched separately
        return items, total_count, None

    async def _get_root_level_nodes(
        self,
        user_key: str,
        org_id: str,
        skip: int,
        limit: int,
        sort_by: str,
        sort_order: str,
        node_types: Optional[List[str]],
        origins: Optional[List[str]],
        connector_ids: Optional[List[str]],
        kb_ids: Optional[List[str]],
        only_containers: bool,
    ) -> Tuple[List[NodeItem], int, Optional[AvailableFilters]]:
        """Get root level nodes (KBs and Apps)"""
        try:
            # Determine if we should include KBs and Apps
            include_kbs = True
            include_apps = True

            # Handle connector_ids and kb_ids filters:
            # - If only connector_ids provided: exclude KBs (show filtered apps only)
            # - If only kb_ids provided: exclude apps (show filtered KBs only)
            # - If both provided: include both, filter each appropriately
            if connector_ids and not kb_ids:
                include_kbs = False  # Only show apps matching connector_ids
            elif kb_ids and not connector_ids:
                include_apps = False  # Only show KBs matching kb_ids

            if node_types:
                if 'kb' not in node_types and 'recordGroup' not in node_types:
                    # Note: recordGroup can be in KB too.
                    # But root nodes are just KB and APP.
                    include_kbs = False
                if 'app' not in node_types:
                    include_apps = False

            if origins:
                if 'KB' not in origins:
                    include_kbs = False
                if 'CONNECTOR' not in origins:
                    include_apps = False

            # Get user's accessible apps
            user_apps_ids = await self.graph_provider.get_user_app_ids(user_key)

            # Filter apps by connector_ids if provided
            if connector_ids:
                user_apps_ids = [app_id for app_id in user_apps_ids if app_id in connector_ids]

            # Build sort clause
            sort_field_map = {
                "name": "name",
                "createdAt": "createdAt",
                "updatedAt": "updatedAt",
            }
            sort_field = sort_field_map.get(sort_by, "name")
            sort_dir = "ASC" if sort_order.lower() == "asc" else "DESC"

            # Use the provider method
            result = await self.graph_provider.get_knowledge_hub_root_nodes(
                user_key=user_key,
                org_id=org_id,
                user_app_ids=user_apps_ids,
                skip=skip,
                limit=limit,
                sort_field=sort_field,
                sort_dir=sort_dir,
                include_kbs=include_kbs,
                include_apps=include_apps,
                only_containers=only_containers,
            )

            nodes_data = result.get('nodes', [])
            total_count = result.get('total', 0)

            # Filter KBs by kb_ids if provided (keeps apps as-is, filters KBs to match kb_ids)
            if kb_ids:
                nodes_data = [
                    n for n in nodes_data
                    if n.get('nodeType') != 'kb' or n.get('id') in kb_ids
                ]
                total_count = len(nodes_data)

            # Convert to NodeItem objects
            items = [self._doc_to_node_item(node_doc) for node_doc in nodes_data]

            return items, total_count, None

        except Exception as e:
            self.logger.error(f"❌ Failed to get root level nodes: {str(e)}")
            raise

    async def _get_available_filters(self, user_key: str, org_id: str) -> AvailableFilters:
        """Get filter options (dynamic KBs/Apps + static others)"""
        try:
            options = await self.graph_provider.get_knowledge_hub_filter_options(user_key, org_id)
            kbs_data = options.get('kbs', [])
            apps_data = options.get('apps', [])
            # KB options with icon
            kb_options = [
                FilterOption(
                    id=k['id'],
                    label=k['name']
                )
                for k in kbs_data
            ]

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
                NodeType.KB: "Knowledge Base",
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
                        label="Knowledge Base" if ot == OriginType.KB else "External Connector"
                    )
                    for ot in OriginType
                ],
                connectors=app_options,
                kbs=kb_options,
                indexingStatus=[
                    FilterOption(
                        id=status.value,
                        label=self._format_enum_label(status.value, {"AUTO_INDEX_OFF": "Manual Indexing"})
                    )
                    for status in IndexingStatus
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
        skip: int,
        limit: int,
        sort_by: str,
        sort_order: str,
        q: Optional[str],
        node_types: Optional[List[str]],
        record_types: Optional[List[str]],
        origins: Optional[List[str]],
        connector_ids: Optional[List[str]],
        kb_ids: Optional[List[str]],
        indexing_status: Optional[List[str]],
        created_at: Optional[Dict[str, Optional[int]]],
        updated_at: Optional[Dict[str, Optional[int]]],
        size: Optional[Dict[str, Optional[int]]],
        only_containers: bool,
        parent_id: Optional[str] = None,
        parent_type: Optional[str] = None,
        include_filters: bool = False,
    ) -> Tuple[List[NodeItem], int, Optional[AvailableFilters]]:
        """
        Search for nodes (global or scoped within parent).

        This unified method handles both:
        - Global search: When parent_id is None, searches across all nodes
        - Scoped search: When parent_id is provided, searches within parent and descendants

        Args:
            user_key: User's key for permission filtering
            org_id: Organization ID
            skip: Number of items to skip for pagination
            limit: Maximum number of items to return
            sort_by: Sort field
            sort_order: Sort order (asc/desc)
            q: Optional search query
            node_types: Optional list of node types to filter by
            record_types: Optional list of record types to filter by
            origins: Optional list of origins to filter by
            connector_ids: Optional list of connector IDs to filter by
            kb_ids: Optional list of KB IDs to filter by
            indexing_status: Optional list of indexing statuses to filter by
            created_at: Optional date range filter for creation date
            updated_at: Optional date range filter for update date
            size: Optional size range filter
            only_containers: If True, only return nodes that can have children
            parent_id: Optional parent to scope search within (None for global)
            parent_type: Type of parent (required if parent_id provided)
            include_filters: Whether to fetch available filters

        Returns:
            Tuple of (items, total_count, available_filters)
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

            # Call unified provider method
            result = await self.graph_provider.get_knowledge_hub_search(
                org_id=org_id,
                user_key=user_key,
                skip=skip,
                limit=limit,
                sort_field=sort_field,
                sort_dir=sort_dir,
                search_query=q,
                node_types=node_types,
                record_types=record_types,
                origins=origins,
                connector_ids=connector_ids,
                kb_ids=kb_ids,
                indexing_status=indexing_status,
                created_at=created_at,
                updated_at=updated_at,
                size=size,
                only_containers=only_containers,
                parent_id=parent_id,  # Can be None for global search
                parent_type=parent_type,
            )

            nodes_data = result.get('nodes', [])
            total_count = result.get('total', 0)

            # Convert to NodeItem objects
            items = [self._doc_to_node_item(node_doc) for node_doc in nodes_data]

            # Get available filters if requested
            available_filters = None
            if include_filters:
                available_filters = await self._get_available_filters(user_key, org_id)

            return items, total_count, available_filters

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

    async def _get_current_node_info(self, node_id: str) -> Optional[CurrentNode]:
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

    async def _get_breadcrumbs(self, node_id: str) -> List[BreadcrumbItem]:
        """
        Get breadcrumb trail for a node using the optimized provider method.
        """
        try:
            # Use the provider's optimized AQL query
            breadcrumbs_data = await self.graph_provider.get_knowledge_hub_breadcrumbs(node_id=node_id)

            # Convert to BreadcrumbItem objects
            breadcrumbs = [
                BreadcrumbItem(
                    id=item['id'],
                    name=item['name'],
                    nodeType=item['nodeType'],
                    subType=item.get('subType')
                )
                for item in breadcrumbs_data
            ]

            return breadcrumbs

        except Exception as e:
            self.logger.error(f"❌ Failed to get breadcrumbs: {str(e)}")
            self.logger.error(traceback.format_exc())
            # Fallback: return empty list or just current node if possible
            return []

    async def _get_permissions(
        self, user_key: str, org_id: str, parent_id: Optional[str]
    ) -> Optional[PermissionsInfo]:
        """Get user permissions for the current context. Returns None if user has no permission."""
        try:
            perm_data = await self.graph_provider.get_knowledge_hub_context_permissions(
                user_key=user_key,
                org_id=org_id,
                parent_id=parent_id,
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

    def _doc_to_node_item(self, doc: Dict[str, Any]) -> NodeItem:
        """Convert a database document to a NodeItem"""
        # Extract ID - prefer 'id' field, fallback to '_key' or parse from '_id'
        doc_id = doc.get('id')
        if not doc_id or (isinstance(doc_id, str) and not doc_id.strip()):
            if '_key' in doc and doc['_key']:
                doc_id = doc['_key']
            elif '_id' in doc and doc['_id']:
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
        origin_str = doc.get('origin', 'KB')
        origin = OriginType.KB if origin_str == 'KB' else OriginType.CONNECTOR

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
        item = NodeItem(
            id=doc_id,
            name=doc.get('name', ''),
            nodeType=node_type,
            parentId=doc.get('parentId'),
            origin=origin,
            connector=doc.get('connector'),
            recordType=doc.get('recordType'),
            recordGroupType=doc.get('recordGroupType'),
            indexingStatus=doc.get('indexingStatus'),
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
        )

        return item

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

    def _format_enum_label(self, value: str, special_cases: Optional[Dict[str, str]] = None) -> str:
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
