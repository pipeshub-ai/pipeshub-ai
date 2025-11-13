"""
Connector registry service for managing application connectors.

This module provides functionality to:
- Register connector classes with scope support (personal/team)
- Manage connector instances with scope-based filtering
- Implement pagination for large connector lists
- Control access based on user roles and connector scopes
"""

from enum import Enum
from inspect import isclass
from typing import Any, Callable, Dict, List, Optional, Type
from uuid import uuid4

from app.config.constants.arangodb import CollectionNames
from app.connectors.core.registry.connector_builder import ConnectorScope
from app.connectors.services.base_arango_service import (
    BaseArangoService as ArangoService,
)
from app.containers.connector import ConnectorAppContainer
from app.models.entities import RecordType
from app.utils.time_conversion import get_epoch_timestamp_in_ms


class Origin(str, Enum):
    """Data origin types."""
    UPLOAD = "UPLOAD"
    CONNECTOR = "CONNECTOR"


class Permissions(str, Enum):
    """Permission levels for resources."""
    READER = "READER"
    WRITER = "WRITER"
    OWNER = "OWNER"
    COMMENTER = "COMMENTER"


class IndexingStatus(str, Enum):
    """Status of indexing operations."""
    NOT_STARTED = "NOT_STARTED"
    IN_PROGRESS = "IN_PROGRESS"
    PAUSED = "PAUSED"
    COMPLETED = "COMPLETED"
    FILE_TYPE_NOT_SUPPORTED = "FILE_TYPE_NOT_SUPPORTED"
    MANUAL_SYNC = "MANUAL_SYNC"
    AUTO_INDEX_OFF = "AUTO_INDEX_OFF"
    FAILED = "FAILED"
    ENABLE_MULTIMODAL_MODELS = "ENABLE_MULTIMODAL_MODELS"


def Connector(
    name: str,
    app_group: str,
    auth_type: str,
    app_description: str = "",
    app_categories: Optional[List[str]] = None,
    config: Optional[Dict[str, Any]] = None,
    connector_scopes: Optional[List[ConnectorScope]] = None
) -> Callable[[Type], Type]:
    """
    Decorator to register a connector with metadata and configuration schema.

    Args:
        name: Name of the application (e.g., "Google Drive", "Gmail")
        app_group: Group the app belongs to (e.g., "Google Workspace")
        auth_type: Authentication type (e.g., "oauth", "api_token")
        app_description: Description of the application
        app_categories: List of categories the app belongs to
        config: Complete configuration schema for the connector
        connector_scopes: List of scopes the connector supports ("personal", "team")
    Returns:
        Decorator function that marks a class as a connector

    Example:
        @Connector(
            name="Gmail",
            app_group="Google Workspace",
            auth_type="oauth",
            app_description="Email client",
            app_categories=["email", "productivity"],
            connector_scopes=["personal", "team"]
        )
        class GmailConnector:
            pass
    """
    def decorator(cls: Type) -> Type:
        # Store metadata in the class
        cls._connector_metadata = {
            "name": name,
            "appGroup": app_group,
            "authType": auth_type,
            "appDescription": app_description,
            "appCategories": app_categories or [],
            "config": config or {},
            "connectorScopes": connector_scopes or [ConnectorScope.PERSONAL]  # Default to personal only
        }

        # Mark class as a connector
        cls._is_connector = True

        return cls
    return decorator


class ConnectorRegistry:
    """
    Registry for managing connector metadata and database synchronization with scope support.

    This class handles:
    - Registration of connector classes from code
    - Synchronization with database (deactivating orphaned apps)
    - Providing connector information with current database status
    - Creating and updating connector instances with scope-based access control
    - Pagination for large connector lists
    """

    def __init__(self, container: ConnectorAppContainer) -> None:
        """
        Initialize the connector registry.

        Args:
            container: Dependency injection container
        """
        self.container = container
        self.logger = container.logger()
        self._arango_service: Optional[ArangoService] = None
        self._collection_name = CollectionNames.APPS.value

        # In-memory storage for connector metadata
        self._connectors: Dict[str, Dict[str, Any]] = {}

    async def _get_arango_service(self) -> ArangoService:
        """
        Get the ArangoDB service, initializing it lazily if needed.

        Returns:
            Initialized ArangoDB service instance
        """
        if self._arango_service is None:
            self._arango_service = await self.container.arango_service()
        return self._arango_service

    def register_connector(self, connector_class: Type) -> bool:
        """
        Register a connector class with the registry.

        Args:
            connector_class: The connector class to register (must be decorated with @Connector)

        Returns:
            True if registered successfully, False otherwise
        """
        try:
            if not hasattr(connector_class, '_connector_metadata'):
                self.logger.warning(
                    f"Class {connector_class.__name__} is not decorated with @Connector"
                )
                return False

            metadata = connector_class._connector_metadata
            connector_name = metadata['name']

            # Store in memory
            self._connectors[connector_name] = metadata.copy()

            self.logger.info(f"Registered connector: {connector_name}")
            return True

        except Exception as e:
            self.logger.error(
                f"Error registering connector {connector_class.__name__}: {e}"
            )
            return False

    def discover_connectors(self, module_paths: List[str]) -> None:
        """
        Discover and register all connector classes from specified modules.

        This method scans the provided modules for classes decorated with @Connector
        and automatically registers them.

        Args:
            module_paths: List of module names to search for connectors
        """
        try:
            for module_path in module_paths:
                try:
                    module = __import__(module_path, fromlist=['*'])

                    for attribute_name in dir(module):
                        attribute = getattr(module, attribute_name)

                        if (isclass(attribute) and
                            hasattr(attribute, '_connector_metadata') and
                            hasattr(attribute, '_is_connector')):

                            self.register_connector(attribute)

                except ImportError as e:
                    self.logger.warning(
                        f"Could not import module {module_path}: {e}"
                    )
                    continue

            self.logger.info(f"Discovered {len(self._connectors)} connectors")

        except Exception as e:
            self.logger.error(f"Error discovering connectors: {e}")


    async def _can_access_connector(
        self,
        connector_instance: Dict[str, Any],
        user_id: str,
        is_admin: bool
    ) -> bool:
        """
        Check if user can access a connector instance.

        Args:
            connector_instance: Connector instance document
            user_id: User ID
            is_admin: Whether the user is an admin

        Returns:
            True if user can access the connector
        """
        try:
            connector_scope = connector_instance.get("scope", ConnectorScope.PERSONAL.value)
            created_by = connector_instance.get("createdBy")

            # Team scope: accessible by admins and the creator
            if connector_scope == ConnectorScope.TEAM.value:
                return is_admin or created_by == user_id

            # Personal scope: only accessible by creator
            if connector_scope == ConnectorScope.PERSONAL.value:
                return created_by == user_id

            return False

        except Exception as e:
            self.logger.error(f"Error checking connector access: {e}")
            return False

    async def _get_connector_instance_from_db(
        self,
        connector_id: str
    ) -> Optional[Dict[str, Any]]:
        """
        Get connector instance document from database.

        Args:
            connector_id: Unique key of the connector instance

        Returns:
            Connector instance document or None if not found
        """
        try:
            arango_service = await self._get_arango_service()
            document = await arango_service.get_document(
                connector_id,
                self._collection_name
            )
            return document

        except Exception as e:
            self.logger.debug(
                f"Could not get connector instance {connector_id} from database: {e}"
            )
            return None

    async def _create_connector_instance(
        self,
        connector_type: str,
        instance_name: str,
        metadata: Dict[str, Any],
        scope: str,
        created_by: str,
        org_id: str
    ) -> Optional[Dict[str, Any]]:
        """
        Create a new connector instance in the database.

        Args:
            connector_type: Type of the connector (from registry)
            instance_name: Name for this specific instance
            metadata: Connector metadata from decorator
            scope: Connector scope (personal/team)
            created_by: User ID who created the connector
            org_id: Organization ID

        Returns:
            Created connector instance document or None if failed
        """
        try:
            arango_service = await self._get_arango_service()

            # Verify organization exists
            organization = await arango_service.get_document(
                org_id,
                CollectionNames.ORGS.value
            )

            if not organization:
                self.logger.warning(
                    f"Organization {org_id} not found; skipping instance creation"
                )
                return None

            # Create connector instance document
            instance_key = str(uuid4())
            current_timestamp = get_epoch_timestamp_in_ms()

            instance_document = {
                '_key': instance_key,
                'name': instance_name,
                'type': connector_type,
                'appGroup': metadata['appGroup'],
                'authType': metadata['authType'],
                'scope': scope,
                'isActive': False,
                'isAgentActive': False,
                'isConfigured': True,
                'isAuthenticated': False,
                'createdBy': created_by,
                'updatedBy': created_by,
                'createdAtTimestamp': current_timestamp,
                'updatedAtTimestamp': current_timestamp
            }

            # Create instance in database
            created_instance = await arango_service.batch_upsert_nodes(
                [instance_document],
                self._collection_name
            )

            if not created_instance:
                raise Exception(
                    f"Failed to create connector instance for {connector_type}"
                )

            # Create relationship edge between organization and instance
            edge_document = {
                "_from": f"{CollectionNames.ORGS.value}/{org_id}",
                "_to": f"{CollectionNames.APPS.value}/{instance_key}",
                "createdAtTimestamp": current_timestamp,
            }

            created_edge = await arango_service.batch_create_edges(
                [edge_document],
                CollectionNames.ORG_APP_RELATION.value,
            )

            if not created_edge:
                raise Exception(
                    f"Failed to create organization relationship for {connector_type}"
                )

            self.logger.info(
                f"Created connector instance '{instance_name}' of type {connector_type} "
                f"with scope {scope} for user {created_by}"
            )
            return instance_document

        except Exception as e:
            self.logger.error(
                f"Error creating connector instance for {connector_type}: {e}"
            )
            return None

    async def _deactivate_connector_instance(self, connector_id: str) -> bool:
        """
        Deactivate a connector instance in the database.

        Args:
            connector_id: Unique key of the connector instance

        Returns:
            True if successful, False otherwise
        """
        try:
            arango_service = await self._get_arango_service()

            existing_document = await arango_service.get_document(
                connector_id,
                self._collection_name
            )

            if not existing_document:
                self.logger.warning(
                    f"Connector instance {connector_id} not found in database"
                )
                return False

            updated_document = {
                **existing_document,
                'isActive': False,
                'isAgentActive': False,
                'updatedAtTimestamp': get_epoch_timestamp_in_ms()
            }

            await arango_service.update_node(
                connector_id,
                updated_document,
                self._collection_name
            )

            self.logger.info(f"Deactivated connector instance {connector_id}")
            return True

        except Exception as e:
            self.logger.error(
                f"Error deactivating connector instance {connector_id}: {e}"
            )
            return False

    async def sync_with_database(self) -> bool:
        """
        Synchronize registry with database.

        This method deactivates connector instances in the database that are no longer
        registered in the code. It does NOT create new instances during startup.

        Returns:
            True if successful, False otherwise
        """
        try:
            arango_service = await self._get_arango_service()

            # Get all connector instances from database
            all_documents = await arango_service.get_all_documents(
                self._collection_name
            )

            # Deactivate instances of connectors no longer in registry
            for document in all_documents:
                connector_type = document['type']
                is_active = document.get('isActive', False)

                if connector_type not in self._connectors and is_active:
                    await self._deactivate_connector_instance(document['_key'])

            self.logger.info("Successfully synced registry with database")
            return True

        except Exception as e:
            self.logger.error(f"Error syncing registry with database: {e}")
            return False

    def _build_connector_info(
        self,
        connector_type: str,
        metadata: Dict[str, Any],
        instance_data: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """
        Build connector information dictionary from metadata and instance data.

        Args:
            connector_type: Type of the connector
            metadata: Connector metadata from registry
            instance_data: Optional instance-specific data from database

        Returns:
            Complete connector information dictionary
        """
        connector_config = metadata.get('config', {})

        connector_info = {
            'name': connector_type,
            'type': connector_type,
            'appGroup': metadata['appGroup'],
            'authType': metadata['authType'],
            'appDescription': metadata.get('appDescription', ''),
            'appCategories': metadata.get('appCategories', []),
            'iconPath': connector_config.get(
                'iconPath',
                '/assets/icons/connectors/default.svg'
            ),
            'supportsRealtime': connector_config.get('supportsRealtime', False),
            'supportsSync': connector_config.get('supportsSync', False),
            'supportsAgent': connector_config.get('supportsAgent', False),
            'config': connector_config,
            'connectorScopes': metadata.get('connectorScopes', ['personal'])
        }

        # Add instance-specific data if provided
        if instance_data:
            connector_info.update({
                'isActive': instance_data.get('isActive', False),
                'isAgentActive': instance_data.get('isAgentActive', False),
                'isConfigured': instance_data.get('isConfigured', False),
                'isAuthenticated': instance_data.get('isAuthenticated', False),
                'createdAtTimestamp': instance_data.get('createdAtTimestamp'),
                'updatedAtTimestamp': instance_data.get('updatedAtTimestamp'),
                '_key': instance_data.get('_key'),
                'name': instance_data.get('name'),
                'scope': instance_data.get('scope', ConnectorScope.PERSONAL.value),
                'createdBy': instance_data.get('createdBy'),
                'updatedBy': instance_data.get('updatedBy'),
            })

        return connector_info

    async def _get_all_connector_instances(self, user_id: str, org_id: str) -> List[Dict[str, Any]]:
        """
        Get all connector instances from the database. (Team and personal connectors)
        """
        connectors = []
        try:
            arango_service = await self._get_arango_service()
            # get all connectors which are of team type or have user_id as createdBy
            query = """
            FOR doc IN @@collection
                FILTER doc._id != null
                FILTER (doc.scope == @scope OR doc.createdBy == @user_id)
                RETURN doc
            """
            bind_vars = {
                "@collection": self._collection_name,
                "scope": ConnectorScope.TEAM.value,
                "user_id": user_id,
            }
            cursor = arango_service.db.aql.execute(query, bind_vars=bind_vars)
            documents = list[Dict[str, Any]](cursor)
            for document in documents:
                connectors.append(self._build_connector_info(document['type'], self._connectors[document['type']], document))
            return connectors
        except Exception as e:
            self.logger.error(f"Error getting all connector instances: {e}")
            return []

    async def get_all_registered_connectors(
        self,
        is_admin: bool,
        scope: Optional[str] = None,
        page: int = 1,
        limit: int = 20,
        search: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Get all registered connectors from the registry (without instance status).

        This returns the connector types available for configuration, optionally filtered by scope.

        Args:
            scope: Optional scope filter (personal/team)
            is_admin: Whether the user is an admin
            page: Page number (1-indexed)
            limit: Number of items per page
            search: Optional search query
        Returns:
            Dictionary with connectors list and pagination info
        """
        connectors = []

        # Prepare search tokens (case-insensitive, AND across tokens)
        tokens: List[str] = []
        if search:
            tokens = [t.strip().lower() for t in str(search).split() if t.strip()]

        def matches_search(info: Dict[str, Any]) -> bool:
            if not tokens:
                return True
            haystacks: List[str] = []
            haystacks.append(str(info.get('name', '')).lower())
            haystacks.append(str(info.get('type', '')).lower())
            haystacks.append(str(info.get('appGroup', '')).lower())
            haystacks.append(str(info.get('appDescription', '')).lower())
            haystacks.append(str(info.get('authType', '')).lower())
            for cat in info.get('appCategories', []) or []:
                haystacks.append(str(cat).lower())
            combined = ' '.join(haystacks)
            return all(tok in combined for tok in tokens)

        for connector_type, metadata in self._connectors.items():
            # Filter by scope if specified
            connector_scopes = metadata.get('connectorScopes', [])
            if scope and scope not in connector_scopes:
                continue

            connector_info = self._build_connector_info(connector_type, metadata)
            if matches_search(connector_info):
                connectors.append(connector_info)

        # Calculate pagination
        total_count = len(connectors)
        total_pages = (total_count + limit - 1) // limit
        start_idx = (page - 1) * limit
        end_idx = start_idx + limit
        has_prev = page > 1
        has_next = end_idx < total_count

        paginated_connectors = connectors[start_idx:end_idx]

        return {
            "connectors": paginated_connectors,
            "pagination": {
                "page": page,
                "limit": limit,
                "search": search,
                "totalCount": total_count,
                "totalPages": total_pages,
                "hasPrev": has_prev,
                "hasNext": has_next,
                "prevPage": page - 1,
                "nextPage": page + 1
            }
        }

    async def get_all_connector_instances(
        self,
        user_id: str,
        org_id: str,
        is_admin: bool,
        scope: Optional[str] = None,
        page: int = 1,
        limit: int = 20,
        search: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Get all configured connector instances with scope-based filtering.

        Args:
            user_id: User ID requesting the instances
            org_id: Organization ID
            is_admin: Whether the user is an admin
            scope: Optional scope filter (personal/team)
            page: Page number (1-indexed)
            limit: Number of items per page
            search: Optional search query
        Returns:
            Dictionary with connector instances and pagination info
        """
        try:
            arango_service = await self._get_arango_service()

            # Build AQL query with scope filtering
            query = """
            FOR doc IN @@collection
                FILTER doc._id != null
            """

            bind_vars = {
                "@collection": self._collection_name
            }

            # Add scope filter if specified
            if scope:
                query += " FILTER doc.scope == @scope"
                bind_vars["scope"] = scope

            # Add access control
            if not is_admin:
                # Non-admins can only see their own connectors or team connectors they created
                query += """
                FILTER (doc.createdBy == @user_id)
                """
                bind_vars["user_id"] = user_id
            else:
                # Admins can see all team connectors + their personal connectors
                query += """
                FILTER (doc.scope == @team_scope) OR (doc.createdBy == @user_id)
                """
                bind_vars["team_scope"] = ConnectorScope.TEAM.value
                bind_vars["user_id"] = user_id

            # Add search filter if specified
            if search:
                query += " FILTER (LOWER(doc.name) LIKE @search) OR (LOWER(doc.type) LIKE @search) OR (LOWER(doc.appGroup) LIKE @search)"
                bind_vars["search"] = f"%{search.lower()}%"

            # Get total count
            count_query = query + " COLLECT WITH COUNT INTO total RETURN total"
            count_cursor = arango_service.db.aql.execute(count_query, bind_vars=bind_vars)
            total_count = next(count_cursor, 0)

            # Add pagination
            query += """
                SORT doc.createdAtTimestamp DESC
                LIMIT @offset, @limit
                RETURN doc
            """
            bind_vars["offset"] = (page - 1) * limit
            bind_vars["limit"] = limit

            cursor = arango_service.db.aql.execute(query, bind_vars=bind_vars)
            documents = list(cursor)

            connector_instances = []

            for document in documents:
                connector_type = document['type']

                if connector_type not in self._connectors:
                    self.logger.warning(
                        f"Connector type {connector_type} not found in registry"
                    )
                    continue

                metadata = self._connectors[connector_type]
                connector_info = self._build_connector_info(
                    connector_type,
                    metadata,
                    document
                )
                connector_instances.append(connector_info)

            total_pages = (total_count + limit - 1) // limit
            has_prev = page > 1
            start_idx = (page - 1) * limit
            end_idx = start_idx + limit
            has_next = end_idx < total_count
            return {
                "connectors": connector_instances,
                "pagination": {
                    "page": page,
                    "limit": limit,
                    "search": search,
                    "totalCount": total_count,
                    "totalPages": total_pages,
                    "hasPrev": has_prev,
                    "hasNext": has_next,
                    "prevPage": page - 1,
                    "nextPage": page + 1
                }
            }

        except Exception as e:
            self.logger.error(f"Error getting all connector instances: {e}")
            return {
                "connectors": [],
                "pagination": {
                    "page": page,
                    "limit": limit,
                    "search": search,
                    "totalCount": 0,
                    "totalPages": 0,
                    "hasPrev": False,
                    "hasNext": False,
                    "prevPage": None,
                    "nextPage": None
                }
            }

    async def get_active_connector_instances(
        self,
        user_id: str,
        org_id: str,
    ) -> List[Dict[str, Any]]:
        """
        Get all active connector instances (isActive = true) with scope-based filtering.

        Args:
            user_id: User ID requesting the instances
            org_id: Organization ID
        Returns:
            List of active connector instances
        """
        result = await self._get_all_connector_instances(user_id, org_id)

        active_instances = [
            instance for instance in result
            if instance.get('isActive', False)
        ]
        for instance in active_instances:
            instance.pop('config', None)
        return active_instances

    async def get_active_agent_connector_instances(
        self,
        user_id: str,
        org_id: str,
        is_admin: bool,
        scope: Optional[str] = None,
        page: int = 1,
        limit: int = 20,
        search: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Get all active agent connector instances (isAgentActive = true) with scope-based filtering.

        Args:
            user_id: User ID requesting the instances
            org_id: Organization ID
            is_admin: Whether the user is an admin
            scope: Optional scope filter (personal/team)
            page: Page number (1-indexed)
            limit: Number of items per page
            search: Optional search query
        Returns:
            Dictionary with active agent connector instances and pagination info
        """
        result = await self.get_all_connector_instances(user_id, org_id, is_admin, scope, page, limit * 2, search)

        active_agent_connector_instances = [
            instance for instance in result["connectors"]
            if instance.get('isAgentActive', False) and instance.get('isConfigured', False)
        ]

        # Re-paginate the filtered results
        total_count = len(active_agent_connector_instances)
        total_pages = (total_count + limit - 1) // limit
        start_idx = (page - 1) * limit
        end_idx = start_idx + limit
        has_prev = page > 1
        has_next = end_idx < total_count
        return {
            "connectors": active_agent_connector_instances[start_idx:end_idx],
            "pagination": {
                "page": page,
                "limit": limit,
                "search": search,
                "totalCount": total_count,
                "totalPages": total_pages,
                "hasPrev": has_prev,
                "hasNext": has_next,
                "prevPage": page - 1,
                "nextPage": page + 1
            }
        }


    async def get_inactive_connector_instances(
        self,
        user_id: str,
        org_id: str,
    ) -> List[Dict[str, Any]]:
        """
        Get all inactive connector instances (isActive = false) with scope-based filtering.

        Args:
            user_id: User ID requesting the instances
            org_id: Organization ID
        Returns:
            List of inactive connector instances
        """
        result = await self._get_all_connector_instances(user_id, org_id)

        inactive_instances = [
            instance for instance in result
            if not instance.get('isActive', False)
        ]
        for instance in inactive_instances:
            instance.pop('config', None)

        return inactive_instances

    async def get_configured_connector_instances(
        self,
        user_id: str,
        org_id: str,
        is_admin: bool,
        scope: Optional[str] = None,
        page: int = 1,
        limit: int = 20,
        search: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Get all configured connector instances (isConfigured = true) with scope-based filtering.

        Args:
            user_id: User ID requesting the instances
            org_id: Organization ID
            is_admin: Whether the user is an admin
            scope: Optional scope filter (personal/team)
            page: Page number (1-indexed)
            limit: Number of items per page
            search: Optional search query
        Returns:
            Dictionary with configured connector instances and pagination info
        """
        result = await self.get_all_connector_instances(user_id, org_id, is_admin, scope, page, limit * 2, search)

        configured_instances = [
            instance for instance in result["connectors"]
            if instance.get('isConfigured', False)
        ]

        # Re-paginate the filtered results
        total_count = len(configured_instances)
        total_pages = (total_count + limit - 1) // limit
        start_idx = (page - 1) * limit
        end_idx = start_idx + limit
        has_prev = page > 1
        has_next = end_idx < total_count
        return {
            "connectors": configured_instances[start_idx:end_idx],
            "pagination": {
                "page": page,
                "limit": limit,
                "search": search,
                "totalCount": total_count,
                "totalPages": total_pages,
                "hasPrev": has_prev,
                "hasNext": has_next,
                "prevPage": page - 1,
                "nextPage": page + 1
            }
        }

    async def get_connector_metadata(self, connector_type: str, instance_data: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
        """
        Get connector metadata by type from the registry.

        Args:
            connector_type: Type of the connector

        Returns:
            Connector metadata or None if not found
        """
        if connector_type not in self._connectors:
            return None

        metadata = self._connectors[connector_type]
        return self._build_connector_info(connector_type, metadata, instance_data)

    async def get_connector_instance(
        self,
        connector_id: str,
        user_id: str,
        org_id: str,
        is_admin: bool,
    ) -> Optional[Dict[str, Any]]:
        """
        Get a specific connector instance by its key with access control.

        Args:
            connector_id: Unique key of the connector instance
            user_id: User ID requesting the instance
            org_id: Organization ID
            is_admin: Whether the user is an admin
        Returns:
            Connector instance with full metadata and status or None if not found/no access
        """
        try:
            document = await self._get_connector_instance_from_db(connector_id)

            if not document:
                self.logger.error(
                    f"Connector instance {connector_id} not found in database"
                )
                return None

            # Check access
            has_access = await self._can_access_connector(document, user_id, is_admin)

            if not has_access:
                self.logger.warning(
                    f"User {user_id} does not have access to connector {connector_id}"
                )
                return None

            connector_type = document['type']

            if connector_type not in self._connectors:
                self.logger.error(
                    f"Connector type {connector_type} not found in registry"
                )
                return None

            metadata = self._connectors[connector_type]
            return self._build_connector_info(connector_type, metadata, document)

        except Exception as e:
            self.logger.error(
                f"Error getting connector instance {connector_id}: {e}"
            )
            return None

    async def get_connector_instances_by_group(
        self,
        app_group: str,
        user_id: str,
        org_id: str,
        scope: Optional[str] = None,
        page: int = 1,
        limit: int = 20
    ) -> Dict[str, Any]:
        """
        Get all connector instances in a specific group with scope-based filtering.

        Args:
            app_group: Group name to filter by
            user_id: User ID requesting the instances
            org_id: Organization ID
            scope: Optional scope filter (personal/team)
            page: Page number (1-indexed)
            limit: Number of items per page

        Returns:
            Dictionary with connector instances and pagination info
        """
        result = await self.get_all_connector_instances(user_id, org_id, scope, page, limit * 2)

        group_instances = [
            instance for instance in result["connectors"]
            if instance['appGroup'] == app_group
        ]

        # Re-paginate the filtered results
        total_count = len(group_instances)
        total_pages = (total_count + limit - 1) // limit
        start_idx = (page - 1) * limit
        end_idx = start_idx + limit

        return {
            "connectors": group_instances[start_idx:end_idx],
            "pagination": {
                "page": page,
                "limit": limit,
                "totalCount": total_count,
                "totalPages": total_pages
            }
        }

    async def get_filter_options(self) -> Dict[str, List[str]]:
        """
        Get available filter options for connectors.

        Returns:
            Dictionary containing lists of available filter values
        """
        app_groups = list(set(
            metadata['appGroup']
            for metadata in self._connectors.values()
        ))

        auth_types = list(set(
            metadata['authType']
            for metadata in self._connectors.values()
        ))

        connector_names = list(self._connectors.keys())

        return {
            'appGroups': sorted(app_groups),
            'authTypes': sorted(auth_types),
            'appNames': sorted(connector_names),
            'indexingStatus': [status.value for status in IndexingStatus],
            'recordType': [record_type.value for record_type in RecordType],
            'origin': [origin.value for origin in Origin],
            'permissions': [permission.value for permission in Permissions],
            'scopes': [scope.value for scope in ConnectorScope]
        }

    async def create_connector_instance_on_configuration(
        self,
        connector_type: str,
        instance_name: str,
        scope: str,
        created_by: str,
        org_id: str,
        is_admin: bool,
    ) -> Optional[Dict[str, Any]]:
        """
        Create a connector instance when it's being configured.

        This method should be called during the configuration process.

        Args:
            connector_type: Type of the connector (from registry)
            instance_name: Name for this specific instance
            scope: Connector scope (personal/team)
            created_by: User ID creating the connector
            org_id: Organization ID
            is_admin: Whether the user is an admin
        Returns:
            Created connector instance document or None if failed
        """
        if connector_type not in self._connectors:
            self.logger.error(
                f"Connector type {connector_type} not found in registry"
            )
            return None
        return await self._create_connector_instance(
            connector_type,
            instance_name,
            self._connectors[connector_type],
            scope,
            created_by,
            org_id
        )

    async def update_connector_instance(
        self,
        connector_id: str,
        updates: Dict[str, Any],
        user_id: str,
        org_id: str,
        is_admin: bool,
    ) -> Optional[Dict[str, Any]]:
        """
        Update a connector instance in the database with access control.

        Args:
            connector_id: Unique key of the connector instance
            updates: Dictionary of fields to update
            user_id: User ID performing the update
            org_id: Organization ID
            is_admin: Whether the user is an admin
        Returns:
            Updated connector instance document or None if failed
        """
        try:
            arango_service = await self._get_arango_service()

            existing_document = await arango_service.get_document(
                connector_id,
                self._collection_name
            )

            if not existing_document:
                self.logger.error(
                    f"Connector instance {connector_id} not found. "
                    "Please configure the connector first."
                )
                return None

            # Check access
            has_access = await self._can_access_connector(
                existing_document,
                user_id,
                is_admin
            )

            if not has_access:
                self.logger.error(
                    f"User {user_id} does not have permission to update connector {connector_id}"
                )
                return None

            # Merge updates with existing document
            updated_document = {
                **existing_document,
                **updates,
                'updatedAtTimestamp': get_epoch_timestamp_in_ms()
            }

            # Execute update query
            query = """
            FOR node IN @@collection
                FILTER node._key == @key
                UPDATE node WITH @node_updates IN @@collection
                RETURN NEW
            """

            db = arango_service.db
            cursor = db.aql.execute(
                query,
                bind_vars={
                    "key": connector_id,
                    "node_updates": updated_document,
                    "@collection": self._collection_name
                }
            )

            result = list(cursor)
            if not result:
                self.logger.warning(
                    f"Failed to update connector instance {connector_id}: not found"
                )
                return None

            self.logger.info(f"Updated connector instance {connector_id}")
            return updated_document

        except Exception as e:
            self.logger.error(
                f"Error updating connector instance {connector_id}: {e}"
            )
            return None
