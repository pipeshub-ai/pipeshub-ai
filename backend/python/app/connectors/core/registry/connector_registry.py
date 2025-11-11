"""
connector registry service.
"""

import hashlib
from enum import Enum
from inspect import isclass
from typing import Any, Callable, Dict, List, Optional, Type

from app.config.constants.arangodb import CollectionNames
from app.connectors.services.base_arango_service import (
    BaseArangoService as ArangoService,
)
from app.containers.connector import ConnectorAppContainer
from app.models.entities import RecordType
from app.utils.time_conversion import get_epoch_timestamp_in_ms


class Origin(str, Enum):
    UPLOAD = "UPLOAD"
    CONNECTOR = "CONNECTOR"

class Permissions(str, Enum):
    READER = "READER"
    WRITER = "WRITER"
    OWNER = "OWNER"
    COMMENTER = "COMMENTER"

class IndexingStatus(str, Enum):
    NOT_STARTED = "NOT_STARTED"
    IN_PROGRESS = "IN_PROGRESS"
    PAUSED = "PAUSED"
    COMPLETED = "COMPLETED"
    FILE_TYPE_NOT_SUPPORTED = "FILE_TYPE_NOT_SUPPORTED"
    MANUAL_SYNC = "MANUAL_SYNC"
    AUTO_INDEX_OFF = "AUTO_INDEX_OFF"
    FAILED = "FAILED"

def Connector(
    name: str,
    app_group: str,
    auth_type: str,
    app_description: str = "",
    app_categories: List[str] = [],
    config: Optional[Dict[str, Any]] = None
) -> Callable[[Type], Type]:
    """
    Decorator to register a connector with metadata and full config schema.

    Args:
        name: Name of the application (e.g., "Google Drive", "Gmail")
        app_group: Group the app belongs to (e.g., "Google Workspace")
        auth_type: Authentication type (e.g., "oauth", "api_token")
        config: Complete configuration schema for the connector
    """
    def decorator(cls) -> Type:
        # Store metadata in the class
        cls._connector_metadata = {
            "name": name,
            "appGroup": app_group,
            "authType": auth_type,
            "appDescription": app_description,
            "appCategories": app_categories,
            "config": config or {}
        }

        # Mark class as a connector
        cls._is_connector = True

        return cls
    return decorator


class ConnectorRegistry:
    """
    Registry for managing connector metadata and database synchronization.
    Responsibilities:
    1. Register connector classes from code
    2. Sync with database (create missing apps, deactivate orphaned apps)
    3. Provide connector information with current DB status
    """

    def __init__(self, container: ConnectorAppContainer) -> None:
        self.container = container
        self.logger = container.logger()
        self._arango_service = None
        self.collection_name = CollectionNames.APPS.value

        # Store discovered connectors metadata
        self._connectors: Dict[str, Dict[str, Any]] = {}

    async def _get_arango_service(self) -> ArangoService:
        """Get the arango service, initializing it if needed"""
        if self._arango_service is None:
            self._arango_service = await self.container.arango_service()
        return self._arango_service

    def register_connector(self, connector_class: Type) -> bool:
        """
        Register a connector class with the registry.

        Args:
            connector_class: The connector class to register

        Returns:
            True if registered successfully
        """
        try:
            if not hasattr(connector_class, '_connector_metadata'):
                self.logger.warning(f"Class {connector_class.__name__} is not decorated with @Connector")
                return False

            metadata = connector_class._connector_metadata
            app_name = metadata['name']

            # Store in memory (only metadata, no DB status here)
            self._connectors[app_name] = metadata.copy()

            self.logger.info(f"Registered connector: {app_name}")
            return True

        except Exception as e:
            self.logger.error(f"Error registering connector {connector_class.__name__}: {e}")
            return False

    def discover_connectors(self, modules: List[str]) -> None:
        """
        Discover and register all connector classes from specified modules.

        Args:
            modules: List of module names to search for connectors
        """
        try:
            for module_name in modules:
                try:
                    module = __import__(module_name, fromlist=['*'])

                    for attr_name in dir(module):
                        attr = getattr(module, attr_name)

                        if (isclass(attr) and
                            hasattr(attr, '_connector_metadata') and
                            hasattr(attr, '_is_connector')):

                            self.register_connector(attr)

                except ImportError as e:
                    self.logger.warning(f"Could not import module {module_name}: {e}")
                    continue

            self.logger.info(f"Discovered {len(self._connectors)} connectors")

        except Exception as e:
            self.logger.error(f"Error discovering connectors: {e}")

    def _normalize_app_name(self, name: str) -> str:
        """
        Normalize app name for matching (case-insensitive, ignore spaces).
        Matches the logic used in get_app_by_name query.
        """
        return name.replace(' ', '').lower()

    async def _get_all_db_statuses(self) -> Dict[str, Dict[str, Any]]:
        """
        Batch fetch all app statuses from database in a single query.
        Returns a dictionary mapping normalized app names to their status.

        Returns:
            Dictionary mapping normalized app name to status dict
        """
        try:
            arango_service = await self._get_arango_service()
            # Fetch all apps in one query
            db_docs = await arango_service.get_all_documents(self.collection_name)

            # Create lookup map by normalized name
            status_map = {}
            for doc in db_docs:
                app_name = doc.get('name')
                if app_name:
                    normalized_name = self._normalize_app_name(app_name)
                    status_map[normalized_name] = {
                        'isActive': doc.get('isActive', False),
                        'isConfigured': doc.get('isConfigured', False),
                        'isAuthenticated': doc.get('isAuthenticated', False),
                        'appGroupId': doc.get('appGroupId'),
                        'createdAtTimestamp': doc.get('createdAtTimestamp'),
                        'updatedAtTimestamp': doc.get('updatedAtTimestamp'),
                    }

            return status_map
        except Exception as e:
            self.logger.debug(f"Could not batch fetch DB statuses: {e}")
            return {}

    async def _get_db_status(self, app_name: str) -> Dict[str, Any]:
        """
        Get connector status from database.

        Returns:
            Dictionary with status information or default values
        """
        try:
            arango_service = await self._get_arango_service()
            doc = await arango_service.get_app_by_name(app_name)

            if doc:
                return {
                    'isActive': doc.get('isActive', False),
                    'isConfigured': doc.get('isConfigured', False),
                    'isAuthenticated': doc.get('isAuthenticated', False),
                    'appGroupId': doc.get('appGroupId'),
                    'createdAtTimestamp': doc.get('createdAtTimestamp'),
                    'updatedAtTimestamp': doc.get('updatedAtTimestamp'),
                }

        except Exception as e:
            self.logger.debug(f"Could not get DB status for {app_name}: {e}")

        # Return default status
        return {
            'isActive': False,
            'isConfigured': False,
            'isAuthenticated': False,
            # Metadata (description, categories, icon, capabilities) are sourced from registry only
            'createdAtTimestamp': None,
            'updatedAtTimestamp': None,
            'config': {}
        }

    async def _create_app_in_db(self, app_name: str, metadata: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Create a new app entry in the database.

        Args:
            app_name: Name of the application
            metadata: Connector metadata from decorator

        Returns:
            App document if successful
        """
        try:
            arango_service = await self._get_arango_service()
            orgs = await arango_service.get_all_documents(CollectionNames.ORGS.value)

            if not orgs or not isinstance(orgs, list):
                self.logger.warning(
                    f"No organizations found in DB; skipping app creation for {app_name}"
                )
                return None

            org_id = orgs[0].get("_key")
            if not org_id:
                self.logger.warning(
                    f"First organization document missing _key; skipping app creation for {app_name}"
                )
                return None

            # for having same app group id for same app group
            app_group_id = hashlib.sha256(metadata['appGroup'].encode()).hexdigest()

            doc = {
                '_key': f"{org_id}_{app_name.replace(' ', '_').upper()}",
                'name': app_name,
                'type': metadata.get('type', app_name.upper().replace(' ', '_')),
                'appGroup': metadata['appGroup'],
                'appGroupId': app_group_id,
                'authType': metadata['authType'],
                'isActive': False,  # Always start as inactive
                'isConfigured': False,
                'createdAtTimestamp': get_epoch_timestamp_in_ms(),
                'updatedAtTimestamp': get_epoch_timestamp_in_ms()
            }

            app_doc = await arango_service.batch_upsert_nodes([doc], self.collection_name)
            if not app_doc:
                raise Exception(f"Failed to create app {app_name} in database")

            edge_data = {
                "_from": f"{CollectionNames.ORGS.value}/{org_id}",
                "_to": f"{CollectionNames.APPS.value}/{doc['_key']}",
                "createdAtTimestamp": get_epoch_timestamp_in_ms(),
            }

            edge_doc = await arango_service.batch_create_edges(
                [edge_data],
                CollectionNames.ORG_APP_RELATION.value,
            )
            if not edge_doc:
                raise Exception(f"Failed to create edge for {app_name} in database")

            self.logger.info(f"Created database entry for {app_name}")
            return app_doc

        except Exception as e:
            self.logger.error(f"Error creating app {app_name} in database: {e}")
            return None

    async def _deactivate_app_in_db(self, app_name: str) -> bool:
        """
        Deactivate an app in the database (set isActive = false).

        Args:
            app_name: Name of the application to deactivate

        Returns:
            True if successful
        """
        try:
            arango_service = await self._get_arango_service()

            existing_doc = await arango_service.get_app_by_name(app_name)
            if existing_doc:
                updated_doc = {
                    **existing_doc,
                    'isActive': False,
                    'updatedAtTimestamp': get_epoch_timestamp_in_ms()
                }

                query = """
                FOR node IN @@collection
                    FILTER node.name == @name
                    UPDATE node WITH @node_updates IN @@collection
                    RETURN NEW
                """
                db = arango_service.db
                cursor = db.aql.execute(query, bind_vars={
                    "name": app_name,
                    "node_updates": updated_doc,
                    "@collection": self.collection_name
                })
                if not list(cursor):
                    self.logger.warning(f"Failed to deactivate app {app_name}: app not found.")
                    return False

                self.logger.info(f"Deactivated app {app_name} (not in registry)")
                return True
            else:
                self.logger.warning(f"Cannot deactivate app {app_name} - not found in DB")
                return False

        except Exception as e:
            self.logger.error(f"Error deactivating app {app_name}: {e}")
            return False

    async def sync_with_database(self) -> bool:
        """
        Sync registry with database:
        1. Only deactivate apps in DB that are not in registry
        2. Do NOT create apps during startup - they will be created when configured

        Returns:
            True if successful
        """
        try:
            arango_service = await self._get_arango_service()

            # Get all apps from database
            db_docs = await arango_service.get_all_documents(self.collection_name)
            db_apps = {doc['name']: doc for doc in db_docs}

            deactivated_apps = []

            # Only deactivate apps in DB that are not in registry
            for app_name, doc in db_apps.items():
                if app_name not in self._connectors and doc.get('isActive', False):
                    if await self._deactivate_app_in_db(app_name):
                        deactivated_apps.append(app_name)

            # Log summary
            if deactivated_apps:
                self.logger.info(f"Deactivated {len(deactivated_apps)} apps not in registry: {deactivated_apps}")

            if not deactivated_apps:
                self.logger.info("Registry and database are already in sync")

            return True

        except Exception as e:
            self.logger.error(f"Error syncing registry with database: {e}")
            return False

    # Query methods - these fetch current DB status when needed

    async def get_all_connectors(self) -> List[Dict[str, Any]]:
        """
        Get all registered connectors with their current status from DB.

        Returns:
            List of connector metadata with current DB status
        """
        # Batch fetch all app statuses in one query
        db_statuses = await self._get_all_db_statuses()

        connectors = []
        for app_name, metadata in self._connectors.items():
            # Use registry metadata as the primary source
            connector_info = {
                'name': app_name,
                'appGroup': metadata['appGroup'],
                'authType': metadata['authType'],
                'appDescription': metadata.get('appDescription', ''),
                'appCategories': metadata.get('appCategories', []),
                'iconPath': metadata.get('config', {}).get('iconPath', '/assets/icons/connectors/default.svg'),
                'supportsRealtime': metadata.get('config', {}).get('supportsRealtime', False),
                'supportsSync': metadata.get('config', {}).get('supportsSync', False),
                'config': metadata.get('config', {}),
                # Default values for DB-specific fields
                'isActive': False,
                'isConfigured': False,
                'isAuthenticated': False,
                'createdAtTimestamp': None,
                'updatedAtTimestamp': None
            }

            # Look up DB status from batch-fetched map
            normalized_name = self._normalize_app_name(app_name)
            db_status = db_statuses.get(normalized_name)
            if db_status and db_status.get('createdAtTimestamp'):  # If app exists in DB
                connector_info.update({
                    'isActive': db_status.get('isActive', False),
                    'isConfigured': db_status.get('isConfigured', False),
                    'isAuthenticated': db_status.get('isAuthenticated', False),
                    'createdAtTimestamp': db_status.get('createdAtTimestamp'),
                    'updatedAtTimestamp': db_status.get('updatedAtTimestamp')
                })
                # Do not override metadata from DB; registry is source of truth

            connectors.append(connector_info)

        # Runtime filter based on feature flag
        try:
            feature_flag_service = await self.container.feature_flag_service()
            # Ensure we have the latest values
            try:
                await feature_flag_service.refresh()
            except Exception as e:
                self.logger.debug(f"Feature flag refresh failed: {e}")
            from app.services.featureflag.config.config import CONFIG
            beta_enabled = feature_flag_service.is_feature_enabled(CONFIG.ENABLE_BETA_CONNECTORS)
            if not beta_enabled:
                # Lazy import to avoid circular dependency
                from app.connectors.core.factory.connector_factory import (
                    ConnectorFactory,
                )
                beta_connectors = ConnectorFactory.list_beta_connectors()
                connectors = [
                    c for c in connectors
                    if c.get('name').replace(' ', '').lower() not in beta_connectors
                ]
            self.logger.debug(f"Connectors names: {[c.get('name').replace(' ', '').lower() for c in connectors]}")
        except Exception as e:
            # On any failure, return unfiltered list (fail-open)
            self.logger.debug(f"Feature flag filtering skipped due to error: {e}")

        return connectors

    async def get_active_connector(self) -> List[Dict[str, Any]]:
        """Get all enabled connectors (isActive = true)."""
        all_connectors = await self.get_all_connectors()
        return [connector for connector in all_connectors if connector.get('isActive', False)]

    async def get_inactive_connector(self) -> List[Dict[str, Any]]:
        """Get all disabled connectors (isActive = false)."""
        all_connectors = await self.get_all_connectors()
        return [connector for connector in all_connectors if not connector.get('isActive', False)]

    async def get_connector_by_name(self, app_name: str) -> Dict[str, Any]:
        """
        Get connector by app name with current status.

        Args:
            app_name: Name of the application

        Returns:
            Connector metadata with status or None if not found
        """
        if app_name in self._connectors:
            metadata = self._connectors[app_name]
            connector_info = {
                'name': app_name,
                'appGroup': metadata['appGroup'],
                'authType': metadata['authType'],
                'appDescription': metadata.get('appDescription', ''),
                'appCategories': metadata.get('appCategories', []),
                'iconPath': metadata.get('config', {}).get('iconPath', '/assets/icons/connectors/default.svg'),
                'supportsRealtime': metadata.get('config', {}).get('supportsRealtime', False),
                'supportsSync': metadata.get('config', {}).get('supportsSync', False),
                'config': metadata.get('config', {}),
                'isActive': False,
                'isConfigured': False,
                'isAuthenticated': False,
                'createdAtTimestamp': None,
                'updatedAtTimestamp': None
            }

            # Only override with DB status if the app exists in database
            try:
                db_status = await self._get_db_status(app_name)
                if db_status.get('createdAtTimestamp'):  # If app exists in DB
                    connector_info.update({
                        'isActive': db_status.get('isActive', False),
                        'isConfigured': db_status.get('isConfigured', False),
                        'isAuthenticated': db_status.get('isAuthenticated', False),
                        'createdAtTimestamp': db_status.get('createdAtTimestamp'),
                        'updatedAtTimestamp': db_status.get('updatedAtTimestamp'),
                        'appGroupId': db_status.get('appGroupId'),
                    })
                    # Do not override metadata, authType or config from DB
            except Exception as e:
                self.logger.debug(f"Could not get DB status for {app_name}: {e}")

            return connector_info

        return None

    async def get_connectors_by_group(self, app_group: str) -> List[Dict[str, Any]]:
        """
        Get all connectors in a specific group with their status.

        Args:
            app_group: Group name

        Returns:
            List of connectors in the group with status
        """
        return [
            connector for connector in await self.get_all_connectors()
            if connector['appGroup'] == app_group
        ]

    async def get_filter_options(self) -> Dict[str, List[str]]:
        """
        Get filter options based on registered connectors and system constants.
        Returns:
            Dictionary of filter options
        """
        app_groups = list(set(
            metadata['appGroup']
            for metadata in self._connectors.values()
        ))

        auth_types = list(set(
            metadata['authType']
            for metadata in self._connectors.values()
        ))

        app_names = list(self._connectors.keys())

        return {
            'appGroups': sorted(app_groups),
            'authTypes': sorted(auth_types),
            'appNames': sorted(app_names),
            'indexingStatus': IndexingStatus.values(),
            'recordType': RecordType.values(),
            'origin': Origin.values(),
            'permissions': Permissions.values()
        }

    async def create_app_when_configured(self, app_name: str) -> Optional[Dict[str, Any]]:
        """
        Create an app in the database when it's actually configured.
        This method should be called when a connector is being configured for the first time.
        If the app already exists, it will skip creation.

        Args:
            app_name: Name of the application to create

        Returns:
            App document if successful or if app already exists
        """
        if app_name not in self._connectors:
            self.logger.error(f"App {app_name} not found in registry")
            return None

        # Check if app already exists in database
        try:
            arango_service = await self._get_arango_service()
            existing_app = await arango_service.get_app_by_name(app_name)
            if existing_app:
                self.logger.info(f"App {app_name} already exists in database, skipping creation")
                return existing_app
        except Exception as e:
            self.logger.debug(f"Could not check if app {app_name} exists: {e}")

        # Create the app if it doesn't exist
        metadata = self._connectors[app_name]
        return await self._create_app_in_db(app_name, metadata)

    async def update_connector(self, app_name: str, updates: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Update connector in database.

        Args:
            app_name: Name of the application
            updates:  Updates to apply

        Returns:
            App document if successful
        """
        try:
            arango_service = await self._get_arango_service()

            existing_doc = await arango_service.get_app_by_name(app_name)
            if not existing_doc:
                self.logger.error(f"App {app_name} not found in database. Please configure the connector first.")
                return None

            updated_doc = {**existing_doc, **updates}

            query = """
            FOR node IN @@collection
                FILTER node.name == @name
                UPDATE node WITH @node_updates IN @@collection
                RETURN NEW
            """
            db = arango_service.db
            cursor = db.aql.execute(query, bind_vars={
                "name": app_name,
                "node_updates": updated_doc,
                "@collection": self.collection_name
            })
            if not list(cursor):
                self.logger.warning(f"Failed to update connector for app {app_name}: app not found.")
                return None

            self.logger.info(f"Updated connector for app {app_name}")
            return updated_doc

        except Exception as e:
            self.logger.error(f"Error updating connector for app {app_name}: {e}")
            return None
