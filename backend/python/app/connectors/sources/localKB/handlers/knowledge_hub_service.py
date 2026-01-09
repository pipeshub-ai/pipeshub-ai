"""Knowledge Hub Unified Browse Service"""

import traceback
from collections import Counter
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from app.config.constants.arangodb import CollectionNames, Connectors
from app.connectors.sources.localKB.api.knowledge_hub_models import (
    AppliedFilters,
    AvailableFilters,
    BreadcrumbItem,
    CountItem,
    CountsInfo,
    CurrentNode,
    FiltersInfo,
    ItemPermission,
    KnowledgeHubNodesResponse,
    NodeItem,
    NodeType,
    PaginationInfo,
    PermissionsInfo,
    SourceType,
    ViewMode,
)
from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

FOLDER_MIME_TYPES = [
    'application/vnd.folder',
    'application/vnd.google-apps.folder',
    'text/directory'
]


@dataclass
class FilterBuilder:
    """Helper class to build AQL filter conditions and bind variables."""
    conditions: List[str] = field(default_factory=list)
    bind_vars: Dict[str, Any] = field(default_factory=dict)

    def add_node_type_filter(
        self,
        node_types: Optional[List[str]],
        is_folder_var: str = "is_folder"
    ) -> None:
        """Add node type filter for folder vs record."""
        if not node_types:
            return
        if "folder" in node_types and "record" not in node_types:
            self.conditions.append(f"{is_folder_var} == true")
        elif "record" in node_types and "folder" not in node_types:
            self.conditions.append(f"{is_folder_var} == false")

    def add_record_type_filter(
        self,
        record_types: Optional[List[str]],
        record_var: str = "record"
    ) -> None:
        """Add record type filter."""
        if record_types:
            self.bind_vars["record_types"] = record_types
            self.conditions.append(f"{record_var}.recordType IN @record_types")

    def add_indexing_status_filter(
        self,
        indexing_status: Optional[List[str]],
        record_var: str = "record"
    ) -> None:
        """Add indexing status filter."""
        if indexing_status:
            self.bind_vars["indexing_status"] = indexing_status
            self.conditions.append(f"{record_var}.indexingStatus IN @indexing_status")

    def add_date_range_filter(
        self,
        date_range: Optional[Dict[str, Optional[int]]],
        field_name: str,
        bind_prefix: str,
        record_var: str = "record"
    ) -> None:
        """Add date range filter (gte/lte)."""
        if not date_range:
            return
        if date_range.get("gte"):
            self.bind_vars[f"{bind_prefix}_gte"] = date_range["gte"]
            self.conditions.append(f"{record_var}.{field_name} >= @{bind_prefix}_gte")
        if date_range.get("lte"):
            self.bind_vars[f"{bind_prefix}_lte"] = date_range["lte"]
            self.conditions.append(f"{record_var}.{field_name} <= @{bind_prefix}_lte")

    def add_size_filter(
        self,
        size: Optional[Dict[str, Optional[int]]],
        file_info_var: str = "file_info"
    ) -> None:
        """Add size range filter."""
        if not size:
            return
        if size.get("gte"):
            self.bind_vars["size_gte"] = size["gte"]
            self.conditions.append(f"{file_info_var}.fileSizeInBytes >= @size_gte")
        if size.get("lte"):
            self.bind_vars["size_lte"] = size["lte"]
            self.conditions.append(f"{file_info_var}.fileSizeInBytes <= @size_lte")

    def add_source_filter(
        self,
        sources: Optional[List[str]],
        record_var: str = "record"
    ) -> None:
        """Add source filter (KB vs CONNECTOR)."""
        if sources:
            self.bind_vars["sources"] = sources
            self.conditions.append(
                f'({record_var}.connectorName == "KB" AND "KB" IN @sources) OR '
                f'({record_var}.connectorName != "KB" AND "CONNECTOR" IN @sources)'
            )

    def add_connector_filter(
        self,
        connectors: Optional[List[str]],
        record_var: str = "record"
    ) -> None:
        """Add connector name filter."""
        if connectors:
            self.bind_vars["connectors"] = connectors
            self.conditions.append(f"{record_var}.connectorName IN @connectors")

    def build_clause(self) -> str:
        """Build the final filter clause string."""
        return " AND ".join(self.conditions) if self.conditions else "true"

    def build_record_filters(
        self,
        node_types: Optional[List[str]] = None,
        record_types: Optional[List[str]] = None,
        indexing_status: Optional[List[str]] = None,
        created_at: Optional[Dict[str, Optional[int]]] = None,
        updated_at: Optional[Dict[str, Optional[int]]] = None,
        size: Optional[Dict[str, Optional[int]]] = None,
        sources: Optional[List[str]] = None,
        connectors: Optional[List[str]] = None,
        is_folder_var: str = "is_folder",
        record_var: str = "record",
        file_info_var: str = "file_info",
    ) -> None:
        """Build all common record filters at once."""
        self.add_node_type_filter(node_types, is_folder_var)
        self.add_record_type_filter(record_types, record_var)
        self.add_indexing_status_filter(indexing_status, record_var)
        self.add_date_range_filter(created_at, "createdAtTimestamp", "created_at", record_var)
        self.add_date_range_filter(updated_at, "updatedAtTimestamp", "updated_at", record_var)
        self.add_size_filter(size, file_info_var)
        self.add_source_filter(sources, record_var)
        self.add_connector_filter(connectors, record_var)


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

    def _determine_view_mode(self, request_params: Dict[str, Any]) -> ViewMode:
        """Auto-switch to search mode when filters are applied"""
        has_filters = any([
            request_params.get('q'),
            request_params.get('nodeTypes'),
            request_params.get('recordTypes'),
            request_params.get('sources'),
            request_params.get('connectors'),
            request_params.get('indexingStatus'),
            request_params.get('createdAt'),
            request_params.get('updatedAt'),
            request_params.get('size'),
        ])

        explicit_view = request_params.get('view')
        if explicit_view == 'search':
            return ViewMode.SEARCH
        elif has_filters:
            return ViewMode.SEARCH  # Auto-switch
        elif explicit_view == 'children':
            return ViewMode.CHILDREN
        else:
            return ViewMode.CHILDREN  # Default

    async def get_nodes(
        self,
        user_id: str,
        org_id: str,
        parent_id: Optional[str] = None,
        view: Optional[str] = None,
        only_containers: bool = False,
        page: int = 1,
        limit: int = 50,
        sort_by: str = "name",
        sort_order: str = "asc",
        q: Optional[str] = None,
        node_types: Optional[List[str]] = None,
        record_types: Optional[List[str]] = None,
        sources: Optional[List[str]] = None,
        connectors: Optional[List[str]] = None,
        indexing_status: Optional[List[str]] = None,
        created_at: Optional[Dict[str, Optional[int]]] = None,
        updated_at: Optional[Dict[str, Optional[int]]] = None,
        size: Optional[Dict[str, Optional[int]]] = None,
        include: Optional[List[str]] = None,
    ) -> KnowledgeHubNodesResponse:
        """
        Get nodes for the Knowledge Hub unified browse API
        """
        try:
            # Build request params dict for view mode determination
            request_params = {
                'view': view,
                'q': q,
                'nodeTypes': node_types,
                'recordTypes': record_types,
                'sources': sources,
                'connectors': connectors,
                'indexingStatus': indexing_status,
                'createdAt': created_at,
                'updatedAt': updated_at,
                'size': size,
            }

            # Determine view mode (auto-switch to search if filters applied)
            view_mode = self._determine_view_mode(request_params)

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

            # Get nodes based on parent_id and view mode
            if view_mode == ViewMode.SEARCH:
                items, total_count, available_filters = await self._get_search_nodes(
                    user_key=user_key,
                    org_id=org_id,
                    skip=skip,
                    limit=limit,
                    sort_by=sort_by,
                    sort_order=sort_order,
                    q=q,
                    node_types=node_types,
                    record_types=record_types,
                    sources=sources,
                    connectors=connectors,
                    indexing_status=indexing_status,
                    created_at=created_at,
                    updated_at=updated_at,
                    size=size,
                    only_containers=only_containers,
                )
            else:  # ViewMode.CHILDREN
                items, total_count, available_filters = await self._get_children_nodes(
                    user_key=user_key,
                    org_id=org_id,
                    parent_id=parent_id,
                    skip=skip,
                    limit=limit,
                    sort_by=sort_by,
                    sort_order=sort_order,
                    node_types=node_types,
                    record_types=record_types,
                    sources=sources,
                    connectors=connectors,
                    indexing_status=indexing_status,
                    created_at=created_at,
                    updated_at=updated_at,
                    size=size,
                    only_containers=only_containers,
                )

            # Fetch permissions for all items in batch and assign to each item
            permissions_map = await self._get_batch_permissions(user_key, items)
            for item in items:
                if item.id in permissions_map:
                    item.permission = permissions_map[item.id]

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
                if parent_info:
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
                sources=sources,
                connectors=connectors,
                indexingStatus=indexing_status,
                createdAt=created_at,
                updatedAt=updated_at,
                size=size,
                sortBy=sort_by,
                sortOrder=sort_order,
            )

            # Build filters info
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
                if 'breadcrumbs' in include and parent_id:
                    response.breadcrumbs = await self._get_breadcrumbs(parent_id)

                if 'counts' in include:
                    # Count items by nodeType (from current page items for type breakdown)
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
        skip: int,
        limit: int,
        sort_by: str,
        sort_order: str,
        node_types: Optional[List[str]],
        record_types: Optional[List[str]],
        sources: Optional[List[str]],
        connectors: Optional[List[str]],
        indexing_status: Optional[List[str]],
        created_at: Optional[Dict[str, Optional[int]]],
        updated_at: Optional[Dict[str, Optional[int]]],
        size: Optional[Dict[str, Optional[int]]],
        only_containers: bool,
    ) -> Tuple[List[NodeItem], int, Optional[AvailableFilters]]:
        """Get children nodes for a given parent using unified provider method."""
        if parent_id is None:
            # Root level: return KBs and Apps (special handling, not a "parent")
            return await self._get_root_level_nodes(
                user_key, org_id, skip, limit, sort_by, sort_order,
                node_types, sources, connectors, only_containers
            )

        # Determine parent type
        parent_type = await self._determine_parent_type(parent_id)
        if not parent_type:
            return [], 0, None

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

        # Build filter conditions manually for unified query (uses 'node' variable)
        filter_conditions = []
        bind_vars = {}

        # Node type filter (folder vs record)
        if node_types:
            type_conditions = []
            for nt in node_types:
                if nt == "folder":
                    type_conditions.append('node.nodeType == "folder"')
                elif nt == "record":
                    type_conditions.append('node.nodeType == "record"')
                elif nt == "recordGroup":
                    type_conditions.append('node.nodeType == "recordGroup"')
            if type_conditions:
                filter_conditions.append(f"({' OR '.join(type_conditions)})")

        if record_types:
            bind_vars["record_types"] = record_types
            filter_conditions.append("(node.recordType == null OR node.recordType IN @record_types)")

        if indexing_status:
            bind_vars["indexing_status"] = indexing_status
            filter_conditions.append("(node.indexingStatus == null OR node.indexingStatus IN @indexing_status)")

        if created_at:
            if created_at.get("gte"):
                bind_vars["created_at_gte"] = created_at["gte"]
                filter_conditions.append("node.createdAt >= @created_at_gte")
            if created_at.get("lte"):
                bind_vars["created_at_lte"] = created_at["lte"]
                filter_conditions.append("node.createdAt <= @created_at_lte")

        if updated_at:
            if updated_at.get("gte"):
                bind_vars["updated_at_gte"] = updated_at["gte"]
                filter_conditions.append("node.updatedAt >= @updated_at_gte")
            if updated_at.get("lte"):
                bind_vars["updated_at_lte"] = updated_at["lte"]
                filter_conditions.append("node.updatedAt <= @updated_at_lte")

        if size:
            if size.get("gte"):
                bind_vars["size_gte"] = size["gte"]
                filter_conditions.append("(node.sizeInBytes == null OR node.sizeInBytes >= @size_gte)")
            if size.get("lte"):
                bind_vars["size_lte"] = size["lte"]
                filter_conditions.append("(node.sizeInBytes == null OR node.sizeInBytes <= @size_lte)")

        if sources:
            bind_vars["sources"] = sources
            filter_conditions.append("node.source IN @sources")

        if connectors:
            bind_vars["connectors"] = connectors
            filter_conditions.append("node.connector IN @connectors")

        filter_clause = " AND ".join(filter_conditions) if filter_conditions else "true"

        # Use unified provider method
        result = await self.graph_provider.get_knowledge_hub_children(
            parent_id=parent_id,
            parent_type=parent_type,
            org_id=org_id,
            skip=skip,
            limit=limit,
            sort_field=sort_field,
            sort_dir=sort_dir,
            filter_clause=filter_clause,
            bind_vars=bind_vars,
            only_containers=only_containers,
        )

        nodes_data = result.get('nodes', [])
        total_count = result.get('total', 0)

        # Convert to NodeItem objects
        items = [self._doc_to_node_item(node_doc) for node_doc in nodes_data]

        return items, total_count, None

    async def _determine_parent_type(self, parent_id: str) -> Optional[str]:
        """Determine the type of a parent node for routing."""
        parent_doc = await self.graph_provider.get_document(
            parent_id, CollectionNames.RECORDS.value
        )
        if parent_doc:
            # Check for folder mimeType
            if parent_doc.get('mimeType') in FOLDER_MIME_TYPES:
                return "folder"
            # Check if it's a folder via file type
            if await self._is_folder(parent_id):
                return "folder"
            return "record"

        parent_doc = await self.graph_provider.get_document(
            parent_id, CollectionNames.APPS.value
        )
        if parent_doc:
            return "app"

        parent_doc = await self.graph_provider.get_document(
            parent_id, CollectionNames.RECORD_GROUPS.value
        )
        if parent_doc:
            if parent_doc.get('connectorName') == Connectors.KNOWLEDGE_BASE.value:
                return "kb"
            return "recordGroup"

        return None

    async def _get_root_level_nodes(
        self,
        user_key: str,
        org_id: str,
        skip: int,
        limit: int,
        sort_by: str,
        sort_order: str,
        node_types: Optional[List[str]],
        sources: Optional[List[str]],
        connectors: Optional[List[str]],
        only_containers: bool,
    ) -> Tuple[List[NodeItem], int, Optional[AvailableFilters]]:
        """Get root level nodes (KBs and Apps)"""
        try:
            # Determine if we should include KBs and Apps
            include_kbs = True
            include_apps = True

            if node_types:
                if 'kb' not in node_types and 'recordGroup' not in node_types:
                    include_kbs = False
                if 'app' not in node_types:
                    include_apps = False

            if sources:
                if 'KB' not in sources:
                    include_kbs = False
                if 'CONNECTOR' not in sources:
                    include_apps = False

            # Get user's accessible apps
            user_apps_ids = await self.graph_provider.get_user_app_ids(user_key)

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

            # Convert to NodeItem objects
            items = [self._doc_to_node_item(node_doc) for node_doc in nodes_data]

            return items, total_count, None

        except Exception as e:
            self.logger.error(f"❌ Failed to get root level nodes: {str(e)}")
            raise




    async def _get_search_nodes(
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
        sources: Optional[List[str]],
        connectors: Optional[List[str]],
        indexing_status: Optional[List[str]],
        created_at: Optional[Dict[str, Optional[int]]],
        updated_at: Optional[Dict[str, Optional[int]]],
        size: Optional[Dict[str, Optional[int]]],
        only_containers: bool,
    ) -> Tuple[List[NodeItem], int, Optional[AvailableFilters]]:
        """Get search results (global search across all nodes)"""
        try:
            # Get user's accessible apps
            user_apps_ids = await self.graph_provider.get_user_app_ids(user_key)

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

            # Use the provider method
            result = await self.graph_provider.get_knowledge_hub_search_nodes(
                user_key=user_key,
                org_id=org_id,
                user_app_ids=user_apps_ids,
                skip=skip,
                limit=limit,
                sort_field=sort_field,
                sort_dir=sort_dir,
                search_query=q,
                node_types=node_types,
                record_types=record_types,
                sources=sources,
                connectors_filter=connectors,
                indexing_status=indexing_status,
                created_at=created_at,
                updated_at=updated_at,
                size=size,
                only_containers=only_containers,
            )

            nodes_data = result.get('nodes', [])
            total_count = result.get('total', 0)

            # Convert to NodeItem objects
            items = [self._doc_to_node_item(node_doc) for node_doc in nodes_data]

            return items, total_count, None

        except Exception as e:
            self.logger.error(f"❌ Failed to get search nodes: {str(e)}")
            self.logger.error(traceback.format_exc())
            raise

    async def _is_folder(self, record_id: str) -> bool:
        """Check if a record is a folder (isFile=false or mimeType is folder)"""
        return await self.graph_provider.is_knowledge_hub_folder(
            record_id=record_id,
            folder_mime_types=FOLDER_MIME_TYPES,
        )

    async def _get_current_node_info(self, node_id: str) -> Optional[CurrentNode]:
        """Get current node information (the node being browsed)"""
        node_info = await self.graph_provider.get_knowledge_hub_node_info(
            node_id=node_id,
            folder_mime_types=FOLDER_MIME_TYPES,
        )
        if node_info:
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
    ) -> PermissionsInfo:
        """Get user permissions for the current context"""
        try:
            perm_data = await self.graph_provider.get_knowledge_hub_context_permissions(
                user_key=user_key,
                org_id=org_id,
                parent_id=parent_id,
            )

            return PermissionsInfo(
                role=perm_data.get('role', 'READER'),
                canUpload=perm_data.get('canUpload', False),
                canCreateFolders=perm_data.get('canCreateFolders', False),
                canEdit=perm_data.get('canEdit', False),
                canDelete=perm_data.get('canDelete', False),
                canManagePermissions=perm_data.get('canManagePermissions', False),
            )

        except Exception as e:
            self.logger.error(f"❌ Failed to get permissions: {str(e)}")
            self.logger.error(traceback.format_exc())
            # Return default safe permissions
            return PermissionsInfo(
                role="READER",
                canUpload=False,
                canCreateFolders=False,
                canEdit=False,
                canDelete=False,
                canManagePermissions=False,
            )


    def _doc_to_node_item(self, doc: Dict[str, Any]) -> NodeItem:
        """Convert a database document to a NodeItem"""
        node_type_str = doc.get('nodeType', 'record')
        try:
            node_type = NodeType(node_type_str)
        except ValueError:
            node_type = NodeType.RECORD

        # Determine if container
        is_container = node_type in [NodeType.KB, NodeType.FOLDER, NodeType.APP, NodeType.RECORD_GROUP]

        # Get source
        source_str = doc.get('source', 'KB')
        source = SourceType.KB if source_str == 'KB' else SourceType.CONNECTOR

        # Build NodeItem
        item = NodeItem(
            id=doc.get('id', ''),
            name=doc.get('name', ''),
            nodeType=node_type,
            isContainer=is_container,
            parentId=doc.get('parentId'),
            source=source,
            connector=doc.get('connector'),
            recordType=doc.get('recordType'),
            indexingStatus=doc.get('indexingStatus'),
            createdAt=doc.get('createdAt', 0),
            updatedAt=doc.get('updatedAt', 0),
            sizeInBytes=doc.get('sizeInBytes'),
            mimeType=doc.get('mimeType'),
            extension=doc.get('extension'),
            webUrl=doc.get('webUrl'),
            hasChildren=doc.get('hasChildren', False),
            extra=doc.get('extra'),
        )

        return item

    async def _get_batch_permissions(
        self,
        user_key: str,
        items: List[NodeItem]
    ) -> Dict[str, ItemPermission]:
        """
        Get permissions for multiple items in a single batch query.
        Returns a dict mapping item_id -> ItemPermission
        """
        if not items:
            return {}

        try:
            # Collect node IDs and types
            node_ids = []
            node_types = []

            for item in items:
                node_ids.append(item.id)
                node_types.append(_get_node_type_value(item.nodeType))

            # Use the provider method
            perm_map = await self.graph_provider.get_knowledge_hub_node_permissions(
                user_key=user_key,
                node_ids=node_ids,
                node_types=node_types,
            )

            # Convert to ItemPermission objects
            permissions = {}
            for node_id, perm_data in perm_map.items():
                permissions[node_id] = ItemPermission(
                    role=perm_data.get('role', 'READER'),
                    canEdit=perm_data.get('canEdit', False),
                    canDelete=perm_data.get('canDelete', False),
                )

            return permissions

        except Exception as e:
            self.logger.error(f"❌ Failed to get batch permissions: {str(e)}")
            self.logger.error(traceback.format_exc())
            # Return empty dict - items will have no permission field
            return {}
