from enum import Enum
from inspect import isclass
from typing import Any, Callable, Dict, List, Optional, Type, Union
from uuid import uuid4

from app.config.constants.arangodb import CollectionNames, ProgressStatus
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
    QUEUED = "QUEUED"
    CONNECTOR_DISABLED = "CONNECTOR_DISABLED"


def Connector(
    name: str,
    app_group: str,
    supported_auth_types: Union[str, List[str]],  # Supported auth types (user selects one during creation)
    app_description: str = "",
    app_categories: Optional[List[str]] = None,
    config: Optional[Dict[str, Any]] = None,
    connector_scopes: Optional[List[ConnectorScope]] = None,
    connector_info: Optional[str] = None
) -> Callable[[Type], Type]:
    """
    Decorator to register a connector with metadata and configuration schema.

    Args:
        name: Name of the application (e.g., "Google Drive", "Gmail")
        app_group: Group the app belongs to (e.g., "Google Workspace")
        supported_auth_types: Supported authentication types (e.g., ["OAUTH", "API_TOKEN"])
                             User will select one during connector creation
        app_description: Description of the application
        app_categories: List of categories the app belongs to
        config: Complete configuration schema for the connector
        connector_scopes: List of scopes the connector supports ("personal", "team")
        connector_info: Optional info text to display on the frontend connector page
    Returns:
        Decorator function that marks a class as a connector

    Example:
        @Connector(
            name="Gmail",
            app_group="Google Workspace",
            supported_auth_types=["OAUTH"],
            app_description="Email client",
            app_categories=["email", "productivity"],
            connector_scopes=["personal", "team"],
            connector_info="This connector syncs emails from your Gmail account."
        )
        class GmailConnector:
            pass
    """
    def decorator(cls: Type) -> Type:
        # Normalize supported auth types
        if isinstance(supported_auth_types, str):
            supported_auth_types_list = [supported_auth_types]
        elif isinstance(supported_auth_types, list):
            if not supported_auth_types:
                raise ValueError("supported_auth_types list cannot be empty")
            supported_auth_types_list = supported_auth_types
        else:
            raise ValueError(f"supported_auth_types must be str or List[str], got {type(supported_auth_types)}")

        # Store metadata in the class (no authType - it comes from etcd/database when connector is created)
        cls._connector_metadata = {
            "name": name,
            "appGroup": app_group,
            "supportedAuthTypes": supported_auth_types_list,  # Supported types (user selects one during creation)
            "appDescription": app_description,
            "appCategories": app_categories or [],
            "config": config or {},
            "connectorScopes": connector_scopes or [ConnectorScope.PERSONAL],  # Default to personal only
            "connectorInfo": connector_info
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

    def _normalize_connector_name(self, name: str) -> str:
        """
        Normalize connector name for matching (case-insensitive, ignore spaces).

        This matches the logic used in ConnectorFactory for beta connector identification.

        Args:
            name: Connector name to normalize

        Returns:
            Normalized connector name
        """
        return name.replace(' ', '').lower()

    def _get_beta_connector_names(self) -> List[str]:
        """
        Get list of normalized beta connector names from ConnectorFactory.

        Returns:
            List of normalized beta connector names
        """
        try:
            from app.connectors.core.factory.connector_factory import (
                ConnectorFactory,
            )
            beta_connectors = ConnectorFactory.list_beta_connectors()
            # Extract connector names from metadata and normalize them
            beta_names = []
            for connector_class in beta_connectors.values():
                if hasattr(connector_class, '_connector_metadata'):
                    metadata = connector_class._connector_metadata
                    connector_name = metadata.get('name', '')
                    if connector_name:
                        beta_names.append(self._normalize_connector_name(connector_name))
            return beta_names
        except Exception as e:
            self.logger.debug(f"Could not get beta connector names: {e}")
            return []

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

    async def _check_name_uniqueness(
        self,
        instance_name: str,
        scope: str,
        org_id: str,
        user_id: str,
    ) -> bool:
        """
        Check if connector instance name is unique based on scope.

        Args:
            instance_name: Name to check
            scope: Connector scope (personal/team)
            org_id: Organization ID
            user_id: User ID (for personal scope)

        Returns:
            True if name is unique, False if already exists
        """
        try:
            arango_service = await self._get_arango_service()

            # Normalize name for comparison (case-insensitive, trim whitespace)
            normalized_name = instance_name.strip().lower()

            if scope == ConnectorScope.PERSONAL.value:
                # For personal scope: check uniqueness within user's personal connectors
                query = """
                FOR doc IN @@collection
                    FILTER doc.scope == @scope
                    FILTER doc.createdBy == @user_id
                    FILTER LOWER(TRIM(doc.name)) == @normalized_name
                    RETURN doc._key
                """
                bind_vars = {
                    "@collection": self._collection_name,
                    "scope": ConnectorScope.PERSONAL.value,
                    "user_id": user_id,
                    "normalized_name": normalized_name,
                }
            else:  # TEAM scope
                # For team scope: check uniqueness within organization's team connectors
                # We need to check via org relationship
                query = """
                FOR edge IN @@edge_collection
                    FILTER edge._from == @org_id
                    FOR doc IN @@collection
                        FILTER doc._id == edge._to
                        FILTER doc.scope == @scope
                        FILTER LOWER(TRIM(doc.name)) == @normalized_name
                        RETURN doc._key
                """
                bind_vars = {
                    "@collection": self._collection_name,
                    "@edge_collection": CollectionNames.ORG_APP_RELATION.value,
                    "org_id": f"{CollectionNames.ORGS.value}/{org_id}",
                    "scope": ConnectorScope.TEAM.value,
                    "normalized_name": normalized_name,
                }

            cursor = arango_service.db.aql.execute(query, bind_vars=bind_vars)
            existing = list(cursor)

            # If any results found, name is not unique
            return len(existing) == 0

        except Exception as e:
            self.logger.error(f"Error checking name uniqueness: {e}")
            # On error, allow the operation (fail-open to avoid blocking)
            return True

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
        org_id: str,
        selected_auth_type: Optional[str] = None
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

            # Check name uniqueness before creating
            is_unique = await self._check_name_uniqueness(
                instance_name=instance_name,
                scope=scope,
                org_id=org_id,
                user_id=created_by
            )

            if not is_unique:
                self.logger.warning(
                    f"Connector instance name '{instance_name}' already exists for scope {scope}"
                )
                raise ValueError(
                    f"Connector instance name '{instance_name}' already exists. "
                    f"Please choose a different name."
                )

            # Create connector instance document
            instance_key = str(uuid4())
            current_timestamp = get_epoch_timestamp_in_ms()

            # Require selected auth type - it must be provided by the user during connector creation
            # selected_auth_type is the auth type chosen by the user and stored in the database
            # This cannot be changed after creation
            if not selected_auth_type:
                raise ValueError(
                    f"selected_auth_type is required when creating connector '{connector_type}'. "
                    f"User must select one of the supported auth types: {metadata.get('supportedAuthTypes', [])}"
                )

            # Validate that selected auth type is supported by the connector
            supported_auth_types = metadata.get('supportedAuthTypes', [])
            if supported_auth_types and selected_auth_type not in supported_auth_types:
                raise ValueError(
                    f"Selected auth type '{selected_auth_type}' is not supported for connector '{connector_type}'. "
                    f"Supported types: {supported_auth_types}"
                )

            auth_type_to_store = selected_auth_type

            instance_document = {
                '_key': instance_key,
                'name': instance_name,
                'type': connector_type,
                'appGroup': metadata['appGroup'],
                'authType': auth_type_to_store,  # Store selected auth type (user's choice, not metadata)
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

        except ValueError:
            # Re-raise ValueError (name uniqueness) as-is
            raise
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

            # Collect keys of instances that need to be deactivated
            keys_to_deactivate = []
            for document in all_documents:
                connector_type = document['type']
                is_active = document.get('isActive', False)

                if connector_type not in self._connectors and is_active:
                    keys_to_deactivate.append(document['_key'])

            # Batch deactivate all instances in a single query
            if keys_to_deactivate:
                current_timestamp = get_epoch_timestamp_in_ms()
                query = """
                FOR doc IN @@collection
                    FILTER doc._key IN @keys
                    UPDATE doc WITH {
                        isActive: false,
                        isAgentActive: false,
                        updatedAtTimestamp: @timestamp
                    } IN @@collection
                    RETURN NEW
                """
                bind_vars = {
                    "@collection": self._collection_name,
                    "keys": keys_to_deactivate,
                    "timestamp": current_timestamp
                }
                arango_service.db.aql.execute(query, bind_vars=bind_vars)
                self.logger.info(f"Batch deactivated {len(keys_to_deactivate)} connector instances")

            self.logger.info("Successfully synced registry with database")
            return True

        except Exception as e:
            self.logger.error(f"Error syncing registry with database: {e}")
            return False

    def _build_connector_info(
        self,
        connector_type: str,
        metadata: Dict[str, Any],
        instance_data: Optional[Dict[str, Any]] = None,
        scope: Optional[str] = None
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
            'supportedAuthTypes': metadata.get('supportedAuthTypes', []),  # Supported types (user selects one)
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
            'scope': scope if scope else metadata.get('connectorScopes', [ConnectorScope.PERSONAL.value]),
            'connectorInfo': metadata.get('connectorInfo')
        }

        # Add instance-specific data if provided
        if instance_data:
            # authType comes from instance_data (stored in database when connector was created)
            connector_info.update({
                'authType': instance_data.get('authType'),  # Selected auth type (from database)
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
        Get all connector instances from the database.

        Returns:
            - All personal connectors created by the user
            - All team connectors in the organization (regardless of creator)
        """
        connectors = []
        try:
            arango_service = await self._get_arango_service()
            # Get connectors that are either:
            # 1. Team scope connectors (shared across org)
            # 2. Personal scope connectors created by this user
            query = """
            FOR doc IN @@collection
                FILTER doc._id != null
                FILTER (
                    doc.scope == @team_scope OR
                    (doc.scope == @personal_scope AND doc.createdBy == @user_id)
                )
                RETURN doc
            """
            bind_vars = {
                "@collection": self._collection_name,
                "team_scope": ConnectorScope.TEAM.value,
                "personal_scope": ConnectorScope.PERSONAL.value,
                "user_id": user_id,
            }
            cursor = arango_service.db.aql.execute(query, bind_vars=bind_vars)
            documents = list[Dict[str, Any]](cursor)
            for document in documents:
                if document['type'] in self._connectors:
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
        search: Optional[str] = None,
        account_type: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Get all registered connectors from the registry (without instance status).

        This returns the connector types available for configuration, optionally filtered by scope.
        Beta connectors are filtered based on the ENABLE_BETA_CONNECTORS feature flag.
        Beta connectors are excluded for enterprise accounts to prevent instability.

        Args:
            scope: Optional scope filter (personal/team)
            is_admin: Whether the user is an admin
            page: Page number (1-indexed)
            limit: Number of items per page
            search: Optional search query
            account_type: Account type ('individual' or 'enterprise'/'business') - used to filter beta connectors
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
            # Search in supported auth types
            for auth_type in info.get('supportedAuthTypes', []) or []:
                haystacks.append(str(auth_type).lower())
            for cat in info.get('appCategories', []) or []:
                haystacks.append(str(cat).lower())
            combined = ' '.join(haystacks)
            return all(tok in combined for tok in tokens)

        # Check feature flag once and get beta connector names
        # This is done once before the loop for efficiency
        # We always need beta_names to filter them for enterprise accounts
        try:
            feature_flag_service = await self.container.feature_flag_service()
            try:
                await feature_flag_service.refresh()
            except Exception as e:
                self.logger.debug(f"Feature flag refresh failed: {e}")

            from app.services.featureflag.config.config import CONFIG
            beta_enabled = feature_flag_service.is_feature_enabled(CONFIG.ENABLE_BETA_CONNECTORS)
            # Always get beta names - needed for enterprise account filtering even when feature flag is enabled
            beta_names = self._get_beta_connector_names()
        except Exception as e:
            # On any failure, include all connectors (fail-open)
            self.logger.debug(f"Feature flag check failed, including all connectors: {e}")
            beta_enabled = True
            beta_names = []

        for connector_type, metadata in self._connectors.items():
            # Filter by scope if specified
            connector_scopes = metadata.get('connectorScopes', [])
            if scope and scope not in connector_scopes:
                continue

            # Check if this is a beta connector
            normalized_name = self._normalize_connector_name(connector_type)
            is_beta_connector = normalized_name in beta_names

            # Filter by feature flag (beta connectors)
            if not beta_enabled and is_beta_connector:
                continue

            # Filter out beta connectors for enterprise accounts ONLY when scope is "team"
            # Beta connectors are allowed for:
            # - personal scope in enterprise accounts
            # - team scope in individual accounts
            # Beta connectors are NOT allowed for:
            # - team scope in enterprise accounts (to prevent instability)
            if is_beta_connector and account_type and account_type.lower() in ['enterprise', 'business'] and scope == ConnectorScope.TEAM.value:
                continue

            connector_info = self._build_connector_info(connector_type, metadata, scope=scope)
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

        # Calculate registry counts by scope (all connectors after beta filtering, before search filtering)
        # This represents the total available connector types per scope
        registry_counts_by_scope = {"personal": 0, "team": 0}

        for connector_type, metadata in self._connectors.items():
            # Filter by feature flag (beta connectors)
            if not beta_enabled:
                normalized_name = self._normalize_connector_name(connector_type)
                if normalized_name in beta_names:
                    continue

            # Count by scope
            connector_scopes = metadata.get('connectorScopes', [])
            if ConnectorScope.PERSONAL.value in connector_scopes:
                registry_counts_by_scope["personal"] += 1
            if ConnectorScope.TEAM.value in connector_scopes:
                registry_counts_by_scope["team"] += 1

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
            },
            "registryCountsByScope": registry_counts_by_scope
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

            # Get total count for current query (with filters)
            count_query = query + " COLLECT WITH COUNT INTO total RETURN total"
            count_cursor = arango_service.db.aql.execute(count_query, bind_vars=bind_vars)
            total_count = next(count_cursor, 0)

            # Calculate scope counts (total configured connectors per scope, without search/pagination filters)
            # These counts represent the total configured connectors for each scope
            scope_counts = {"personal": 0, "team": 0}

            try:
                # Count personal connectors (configured = isConfigured = True)
                personal_count_query = """
                FOR doc IN @@collection
                    FILTER doc._id != null
                    FILTER doc.scope == @personal_scope
                    FILTER doc.createdBy == @user_id
                    FILTER doc.isConfigured == true
                    COLLECT WITH COUNT INTO total
                    RETURN total
                """
                personal_bind_vars = {
                    "@collection": self._collection_name,
                    "personal_scope": ConnectorScope.PERSONAL.value,
                    "user_id": user_id,
                }
                personal_cursor = arango_service.db.aql.execute(personal_count_query, bind_vars=personal_bind_vars)
                scope_counts["personal"] = next(personal_cursor, 0)

                # Count team connectors (only for admins)
                if is_admin:
                    team_count_query = """
                    FOR doc IN @@collection
                        FILTER doc._id != null
                        FILTER doc.scope == @team_scope
                        FILTER doc.isConfigured == true
                        COLLECT WITH COUNT INTO total
                        RETURN total
                    """
                    team_bind_vars = {
                        "@collection": self._collection_name,
                        "team_scope": ConnectorScope.TEAM.value,
                    }
                    team_cursor = arango_service.db.aql.execute(team_count_query, bind_vars=team_bind_vars)
                    scope_counts["team"] = next(team_cursor, 0)
            except Exception as e:
                self.logger.warning(f"Error calculating scope counts: {e}")
                # Continue with zero counts if there's an error

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
                },
                "scopeCounts": scope_counts
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
            },
            "scopeCounts": result.get("scopeCounts", {"personal": 0, "team": 0})
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

        # Collect all supported auth types from all connectors
        all_auth_types = set()
        for metadata in self._connectors.values():
            supported_types = metadata.get('supportedAuthTypes', [])
            all_auth_types.update(supported_types)
        auth_types = sorted(list(all_auth_types))

        connector_names = list(self._connectors.keys())

        return {
            'appGroups': sorted(app_groups),
            'authTypes': sorted(auth_types),
            'connectorNames': sorted(connector_names),
            'indexingStatus': ProgressStatus.values(),
            'recordType': RecordType.values(),
            'origin': Origin.values(),
            'permissions': Permissions.values(),
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
        selected_auth_type: Optional[str] = None
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
            selected_auth_type: Auth type selected by the user (cannot be changed after creation)
        Returns:
            Created connector instance document or None if failed
        Raises:
            ValueError: If instance name is not unique
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
            org_id,
            selected_auth_type=selected_auth_type
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
        Raises:
            ValueError: If instance name is not unique
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

            # If name is being updated, check uniqueness
            if 'name' in updates:
                new_name = updates['name']
                scope = existing_document.get('scope', ConnectorScope.PERSONAL.value)
                created_by = existing_document.get('createdBy', user_id)

                # Check if name is unique (excluding current connector)
                is_unique = await self._check_name_uniqueness(
                    instance_name=new_name,
                    scope=scope,
                    org_id=org_id,
                    user_id=created_by,
                )

                if not is_unique:
                    self.logger.warning(
                        f"Connector instance name '{new_name}' already exists for scope {scope}"
                    )
                    raise ValueError(
                        f"Connector instance name '{new_name}' already exists. "
                        f"Please choose a different name."
                    )

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

        except ValueError:
            # Re-raise ValueError (name uniqueness) as-is
            raise
        except Exception as e:
            self.logger.error(
                f"Error updating connector instance {connector_id}: {e}"
            )
            return None
