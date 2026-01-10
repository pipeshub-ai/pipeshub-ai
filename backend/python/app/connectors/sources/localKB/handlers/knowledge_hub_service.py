"""Knowledge Hub Unified Browse Service"""

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
)
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

    async def get_nodes(
        self,
        user_id: str,
        org_id: str,
        parent_id: Optional[str] = None,
        parent_type: Optional[str] = None,
        only_containers: bool = False,
        page: int = 1,
        limit: int = 50,
        sort_by: str = "name",
        sort_order: str = "asc",
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
            if is_search:
                # Global search across all nodes
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
                    origins=origins,
                    connector_ids=connector_ids,
                    kb_ids=kb_ids,
                    indexing_status=indexing_status,
                    created_at=created_at,
                    updated_at=updated_at,
                    size=size,
                    only_containers=only_containers,
                )
            else:
                # Browse mode - get children of parent
                items, total_count, _ = await self._get_children_nodes(
                    user_key=user_key,
                    org_id=org_id,
                    parent_id=parent_id,
                    parent_type=parent_type,  # Now passed directly!
                    skip=skip,
                    limit=limit,
                    sort_by=sort_by,
                    sort_order=sort_order,
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
                available_filters = None
                if include and 'availableFilters' in include:
                    available_filters = await self._get_available_filters(user_key, org_id)

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

        if origins:
            bind_vars["origins"] = origins
            filter_conditions.append("node.source IN @origins")

        if connector_ids:
            bind_vars["connector_ids"] = connector_ids
            filter_conditions.append("node.appId IN @connector_ids")

        if kb_ids:
            bind_vars["kb_ids"] = kb_ids
            filter_conditions.append("node.kbId IN @kb_ids")

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

            # Filter by kb_ids if provided
            if kb_ids and len(kb_ids) > 0:
                # If specifically asking for KBs, we should include them
                include_kbs = True
                # But if searching for explicit Apps (connector_ids), we might exclude KBs unless origins say otherwise
                if connector_ids and not origins:
                    pass # Keep both included and let ID intersection handle it?
                         # Actually KBs don't have connector_ids, Apps don't have kb_ids.

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

            # Filter KBs if kb_ids provided
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

            kb_options = [FilterOption(id=k['id'], label=k['name']) for k in kbs_data]
            app_options = [FilterOption(id=a['id'], label=a['name']) for a in apps_data]

            return AvailableFilters(
                nodeTypes=[
                    FilterOption(id="folder", label="Folder"),
                    FilterOption(id="record", label="File"),
                    FilterOption(id="recordGroup", label="Drive/Root"),
                    FilterOption(id="app", label="Connector"),
                    FilterOption(id="kb", label="Knowledge Base"),
                ],
                recordTypes=[
                    FilterOption(id="FILE", label="File"),
                    FilterOption(id="WEBPAGE", label="Webpage"),
                    FilterOption(id="MESSAGE", label="Message"),
                    FilterOption(id="EMAIL", label="Email"),
                    FilterOption(id="TICKET", label="Ticket"),
                ],
                origins=[
                    FilterOption(id="KB", label="Knowledge Base"),
                    FilterOption(id="CONNECTOR", label="External Connector"),
                ],
                connectors=app_options,
                kbs=kb_options,
                indexingStatus=[
                    FilterOption(id="COMPLETED", label="Completed"),
                    FilterOption(id="IN_PROGRESS", label="In Progress"),
                    FilterOption(id="FAILED", label="Failed"),
                ]
            )
        except Exception as e:
            self.logger.error(f"Failed to get available filters: {e}")
            return AvailableFilters()


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
        origins: Optional[List[str]],
        connector_ids: Optional[List[str]],
        kb_ids: Optional[List[str]],
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
                origins=origins,
                connector_ids=connector_ids,
                kb_ids=kb_ids,
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

            # Get available filters
            available_filters = await self._get_available_filters(user_key, org_id)

            return items, total_count, available_filters

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
                f"Node type mismatch: expected '{expected_type}' but found '{actual_type}' for node ID '{node_id}'"
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

        # Get origin
        origin_str = doc.get('source', 'KB')
        origin = OriginType.KB if origin_str == 'KB' else OriginType.CONNECTOR

        # Build NodeItem
        item = NodeItem(
            id=doc.get('id', ''),
            name=doc.get('name', ''),
            nodeType=node_type,
            parentId=doc.get('parentId'),
            origin=origin,
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
