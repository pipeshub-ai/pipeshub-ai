from typing import Dict, List, Optional, Union

from app.config.constants.arangodb import (
    CollectionNames,
    Connectors,
)
from app.connectors.services.kafka_service import KafkaService
from app.connectors.sources.custom_connector.handlers.custom_connector_service import (
    CustomConnectorService,
)
from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider


read_collections = [
    collection.value for collection in CollectionNames
]

write_collections = [
    collection.value for collection in CollectionNames
]


class KnowledgeBaseService:
    """Data handler for knowledge base operations.

    Delegates to CustomConnectorService with connector_type=KNOWLEDGE_BASE
    and is_restricted=True.
    """

    def __init__(
        self,
        logger,
        graph_provider: IGraphDBProvider,
        kafka_service: KafkaService,
    ) -> None:
        self.logger = logger
        self.graph_provider = graph_provider
        self.kafka_service = kafka_service
        self.custom_connector_service = CustomConnectorService(
            logger=logger,
            graph_provider=graph_provider,
            kafka_service=kafka_service,
        )
        self.connector_type = Connectors.KNOWLEDGE_BASE.value
        self.group_type = Connectors.KNOWLEDGE_BASE.value

    # ==================== Instance CRUD ====================

    async def create_knowledge_base(
        self,
        user_id: str,
        org_id: str,
        name: str,
    ) -> Optional[Dict]:
        """Create a new knowledge base."""
        return await self.custom_connector_service.create_record_group(
            user_id=user_id,
            org_id=org_id,
            name=name,
            group_type=self.group_type,
            connector_id=f"knowledgeBase_{org_id}",
            is_restricted=True,
            inherit_permissions=True,
        )

    async def get_knowledge_base(
        self,
        kb_id: str,
        user_id: str,
    ) -> Optional[Dict]:
        """Get Knowledge base details."""
        return await self.custom_connector_service.get_record_group(
            record_group_id=kb_id,
            user_id=user_id,
            group_type=self.group_type,
        )

    async def list_user_knowledge_bases(
        self,
        user_id: str,
        org_id: str,
        page: int = 1,
        limit: int = 20,
        search: Optional[str] = None,
        permissions: Optional[List[str]] = None,
        sort_by: str = "name",
        sort_order: str = "asc",
    ) -> Union[List[Dict], Dict]:
        """List knowledge bases with pagination and filtering."""
        result = await self.custom_connector_service.list_user_record_groups(
            user_id=user_id,
            org_id=org_id,
            group_type=self.group_type,
            page=page,
            limit=limit,
            search=search,
            permissions=permissions,
            sort_by=sort_by,
            sort_order=sort_order,
        )
        # Rename key for backward compatibility
        if isinstance(result, dict) and "recordGroups" in result:
            result["knowledgeBases"] = result.pop("recordGroups")
        return result

    async def update_knowledge_base(
        self,
        kb_id: str,
        user_id: str,
        updates: Dict,
    ) -> Optional[Dict]:
        """Update knowledge base details."""
        return await self.custom_connector_service.update_record_group(
            record_group_id=kb_id,
            user_id=user_id,
            updates=updates,
        )

    async def delete_knowledge_base(
        self,
        kb_id: str,
        user_id: str,
    ) -> Optional[Dict]:
        """Delete a knowledge base."""
        return await self.custom_connector_service.delete_record_group(
            record_group_id=kb_id,
            user_id=user_id,
        )

    # ==================== Folder Operations ====================

    async def create_folder_in_kb(
        self,
        kb_id: str,
        name: str,
        user_id: str,
        org_id: str,
    ) -> Optional[Dict]:
        """Create folder in KB root."""
        return await self.custom_connector_service.create_folder(
            instance_id=kb_id,
            name=name,
            user_id=user_id,
            org_id=org_id,
            is_restricted=True,
            inherit_permissions=True,
            parent_id=None,
        )

    async def create_nested_folder(
        self,
        kb_id: str,
        parent_folder_id: str,
        name: str,
        user_id: str,
        org_id: str,
    ) -> Optional[Dict]:
        """Create folder inside another folder."""
        return await self.custom_connector_service.create_folder(
            instance_id=kb_id,
            name=name,
            user_id=user_id,
            org_id=org_id,
            is_restricted=True,
            inherit_permissions=True,
            parent_id=parent_folder_id,
        )

    async def get_folder_contents(
        self,
        kb_id: str,
        folder_id: str,
        user_id: str,
    ) -> Dict:
        """Get contents of a folder."""
        return await self.custom_connector_service.get_folder_contents(
            instance_id=kb_id,
            folder_id=folder_id,
            user_id=user_id,
        )

    async def updateFolder(
        self,
        folder_id: str,
        kb_id: str,
        user_id: str,
        name: str,
    ) -> Dict:
        """Update folder name."""
        return await self.custom_connector_service.update_folder(
            instance_id=kb_id,
            folder_id=folder_id,
            user_id=user_id,
            name=name,
        )

    async def delete_folder(
        self,
        kb_id: str,
        folder_id: str,
        user_id: str,
    ) -> Dict:
        """Delete a folder."""
        return await self.custom_connector_service.delete_folder(
            instance_id=kb_id,
            folder_id=folder_id,
            user_id=user_id,
        )

    # ==================== Record Operations ====================

    async def update_record(
        self,
        user_id: str,
        record_id: str,
        updates: Dict,
        file_metadata: Optional[Dict] = None,
    ) -> Optional[Dict]:
        """Update a record."""
        # KB uses _get_kb_context_for_record to find the KB, then checks permission
        # on the KB. We replicate that here since CustomConnectorService.update_record
        # requires instance_id upfront.
        try:
            kb_context = await self.graph_provider.get_kb_context_for_record(record_id)
            if not kb_context:
                return {
                    "success": False,
                    "code": 404,
                    "reason": "Knowledge base context not found for record",
                }
            instance_id = kb_context.get("kb_id")
            return await self.custom_connector_service.update_record(
                record_id=record_id,
                user_id=user_id,
                instance_id=instance_id,
                updates=updates,
                file_metadata=file_metadata,
            )
        except Exception as e:
            self.logger.error(f"Failed to update KB record: {str(e)}")
            return {"success": False, "reason": str(e), "code": 500}

    async def delete_records_in_kb(
        self,
        kb_id: str,
        record_ids: List[str],
        user_id: str,
    ) -> Optional[Dict]:
        """Delete multiple records from KB root."""
        return await self.custom_connector_service.delete_records(
            record_ids=record_ids,
            instance_id=kb_id,
            user_id=user_id,
            parent_id=None,
        )

    async def delete_records_in_folder(
        self,
        kb_id: str,
        folder_id: str,
        record_ids: List[str],
        user_id: str,
    ) -> Optional[Dict]:
        """Delete multiple records from a specific folder."""
        return await self.custom_connector_service.delete_records(
            record_ids=record_ids,
            instance_id=kb_id,
            user_id=user_id,
            parent_id=folder_id,
        )

    # ==================== Permission Operations ====================

    async def create_kb_permissions(
        self,
        kb_id: str,
        requester_id: str,
        user_ids: List[str],
        team_ids: List[str],
        role: str,
    ) -> Optional[Dict]:
        """Create KB permissions for users and teams."""
        result = await self.custom_connector_service.add_permissions_to_node(
            node_id=kb_id,
            requester_id=requester_id,
            user_ids=user_ids,
            team_ids=team_ids,
            role=role,
        )
        if isinstance(result, dict) and "nodeId" in result:
            result["kbId"] = result.pop("nodeId")
        return result

    async def update_kb_permission(
        self,
        kb_id: str,
        requester_id: str,
        user_ids: List[str],
        team_ids: List[str],
        new_role: str,
    ) -> Optional[Dict]:
        """Update permissions for users and teams on a knowledge base."""
        result = await self.custom_connector_service.update_node_permissions(
            node_id=kb_id,
            requester_id=requester_id,
            user_ids=user_ids,
            team_ids=team_ids,
            new_role=new_role,
        )
        if isinstance(result, dict) and "nodeId" in result:
            result["kbId"] = result.pop("nodeId")
        return result

    async def remove_kb_permission(
        self,
        kb_id: str,
        requester_id: str,
        user_ids: List[str],
        team_ids: List[str],
    ) -> Optional[Dict]:
        """Remove permissions for users and teams from a knowledge base."""
        result = await self.custom_connector_service.remove_node_permissions(
            node_id=kb_id,
            requester_id=requester_id,
            user_ids=user_ids,
            team_ids=team_ids,
        )
        if isinstance(result, dict) and "nodeId" in result:
            result["kbId"] = result.pop("nodeId")
        return result

    async def list_kb_permissions(
        self,
        kb_id: str,
        requester_id: str,
    ) -> Optional[Dict]:
        """List all permissions for a knowledge base."""
        result = await self.custom_connector_service.list_node_permissions(
            node_id=kb_id,
            requester_id=requester_id,
        )
        # Remap nodeId -> kbId for backward compatibility
        if isinstance(result, dict) and "nodeId" in result:
            result["kbId"] = result.pop("nodeId")
        return result

    # ==================== Listing Operations ====================

    async def list_all_records(
        self,
        user_id: str,
        org_id: str,
        page: int = 1,
        limit: int = 20,
        search: Optional[str] = None,
        record_types: Optional[List[str]] = None,
        origins: Optional[List[str]] = None,
        connectors: Optional[List[str]] = None,
        indexing_status: Optional[List[str]] = None,
        permissions: Optional[List[str]] = None,
        date_from: Optional[int] = None,
        date_to: Optional[int] = None,
        sort_by: str = "createdAtTimestamp",
        sort_order: str = "desc",
        source: str = "all",
    ) -> Dict:
        """List all records the user can access."""
        return await self.custom_connector_service.list_all_records(
            user_id=user_id,
            org_id=org_id,
            page=page,
            limit=limit,
            search=search,
            record_types=record_types,
            origins=origins,
            connectors=connectors,
            indexing_status=indexing_status,
            permissions=permissions,
            date_from=date_from,
            date_to=date_to,
            sort_by=sort_by,
            sort_order=sort_order,
            source=source,
        )

    async def list_kb_records(
        self,
        kb_id: str,
        user_id: str,
        org_id: str,
        page: int = 1,
        limit: int = 20,
        search: Optional[str] = None,
        record_types: Optional[List[str]] = None,
        origins: Optional[List[str]] = None,
        connectors: Optional[List[str]] = None,
        indexing_status: Optional[List[str]] = None,
        date_from: Optional[int] = None,
        date_to: Optional[int] = None,
        sort_by: str = "createdAtTimestamp",
        sort_order: str = "desc",
    ) -> Dict:
        """List all records in a specific KB."""
        return await self.custom_connector_service.list_record_group_records(
            record_group_id=kb_id,
            user_id=user_id,
            org_id=org_id,
            page=page,
            limit=limit,
            search=search,
            record_types=record_types,
            origins=origins,
            connectors=connectors,
            indexing_status=indexing_status,
            date_from=date_from,
            date_to=date_to,
            sort_by=sort_by,
            sort_order=sort_order,
        )

    async def get_kb_children(
        self,
        kb_id: str,
        user_id: str,
        page: int = 1,
        limit: int = 20,
        level: int = 1,
        search: Optional[str] = None,
        record_types: Optional[List[str]] = None,
        origins: Optional[List[str]] = None,
        connectors: Optional[List[str]] = None,
        indexing_status: Optional[List[str]] = None,
        sort_by: str = "name",
        sort_order: str = "asc",
    ) -> Dict:
        """Get KB root contents with pagination and filters."""
        return await self.custom_connector_service.get_node_children(
            instance_id=kb_id,
            user_id=user_id,
            node_id=None,
            node_type="recordGroup",
            page=page,
            limit=limit,
            level=level,
            search=search,
            record_types=record_types,
            origins=origins,
            connectors=connectors,
            indexing_status=indexing_status,
            sort_by=sort_by,
            sort_order=sort_order,
        )

    async def get_folder_children(
        self,
        kb_id: str,
        folder_id: str,
        user_id: str,
        page: int = 1,
        limit: int = 20,
        level: int = 1,
        search: Optional[str] = None,
        record_types: Optional[List[str]] = None,
        origins: Optional[List[str]] = None,
        connectors: Optional[List[str]] = None,
        indexing_status: Optional[List[str]] = None,
        sort_by: str = "name",
        sort_order: str = "asc",
    ) -> Dict:
        """Get folder contents with pagination and filters."""
        return await self.custom_connector_service.get_node_children(
            instance_id=kb_id,
            user_id=user_id,
            node_id=folder_id,
            node_type="record",
            page=page,
            limit=limit,
            level=level,
            search=search,
            record_types=record_types,
            origins=origins,
            connectors=connectors,
            indexing_status=indexing_status,
            sort_by=sort_by,
            sort_order=sort_order,
        )

    # ==================== Upload & Move ====================

    async def upload_records_to_kb(
        self,
        kb_id: str,
        user_id: str,
        org_id: str,
        files: List[Dict],
    ) -> Dict:
        """Upload to KB root."""
        return await self.custom_connector_service.upload_files(
            instance_id=kb_id,
            files=files,
            user_id=user_id,
            org_id=org_id,
            is_restricted=True,
            parent_id=None,
        )

    async def upload_records_to_folder(
        self,
        kb_id: str,
        folder_id: str,
        user_id: str,
        org_id: str,
        files: List[Dict],
    ) -> Dict:
        """Upload to specific folder."""
        return await self.custom_connector_service.upload_files(
            instance_id=kb_id,
            files=files,
            user_id=user_id,
            org_id=org_id,
            is_restricted=True,
            parent_id=folder_id,
        )

    async def move_record(
        self,
        kb_id: str,
        record_id: str,
        new_parent_id: Optional[str],
        user_id: str,
    ) -> Dict:
        """Move a record to a new location within the same KB."""
        result = await self.custom_connector_service.move_record(
            record_id=record_id,
            instance_id=kb_id,
            new_parent_id=new_parent_id,
            user_id=user_id,
        )
        if isinstance(result, dict) and "instanceId" in result:
            result["kbId"] = result.pop("instanceId")
        return result

    def _error_response(self, code: int, reason: str) -> Dict:
        """Create consistent error response."""
        return {"success": False, "code": code, "reason": reason}
