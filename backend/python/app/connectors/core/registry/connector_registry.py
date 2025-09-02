"""
connector registry service.
"""

import uuid
from inspect import isclass
from typing import Any, Callable, Dict, List, Optional, Type

from app.config.constants.arangodb import CollectionNames
from app.connectors.sources.google.common.arango_service import ArangoService
from app.containers.connector import ConnectorAppContainer
from app.utils.time_conversion import get_epoch_timestamp_in_ms


def Connector(
    name: str,
    app_group: str,
    auth_type: str,
    supports_realtime: bool = False,
    config: Optional[Dict[str, Any]] = None
) -> Callable[[Type], Type]:
    """
    Decorator to register a connector with metadata and full config schema.

    Args:
        name: Name of the application (e.g., "Google Drive", "Gmail")
        app_group: Group the app belongs to (e.g., "Google Workspace")
        auth_type: Authentication type (e.g., "oauth", "api_token")
        supports_realtime: Whether connector supports real-time updates
        config: Complete configuration schema for the connector
    """
    def decorator(cls) -> Type:
        # Store metadata in the class
        cls._connector_metadata = {
            "name": name,
            "appGroup": app_group,
            "authType": auth_type,
            "supportsRealtime": supports_realtime,
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
                    'createdAtTimestamp': doc.get('createdAtTimestamp'),
                    'updatedAtTimestamp': doc.get('updatedAtTimestamp'),
                    'config': doc.get('config', {})
                }

        except Exception as e:
            self.logger.debug(f"Could not get DB status for {app_name}: {e}")

        # Return default status
        return {
            'isActive': False,
            'isConfigured': False,
            'createdAtTimestamp': None,
            'updatedAtTimestamp': None,
            'config': {}
        }

    async def _create_app_in_db(self, app_name: str, metadata: Dict[str, Any]) -> bool:
        """
        Create a new app entry in the database.

        Args:
            app_name: Name of the application
            metadata: Connector metadata from decorator

        Returns:
            True if successful
        """
        try:
            arango_service = await self._get_arango_service()
            app_group_id = str(uuid.uuid4())

            doc = {
                '_key': f"{app_group_id}_{app_name.replace(' ', '_').lower()}",
                'name': app_name,
                'type': metadata.get('type', app_name.upper().replace(' ', '_')),
                'appGroup': metadata['appGroup'],
                'appGroupId': app_group_id,
                'authType': metadata['authType'],
                'config': metadata.get('config', {}),
                'supportsRealtime': metadata['supportsRealtime'],
                'isActive': False,  # Always start as inactive
                'isConfigured': False,
                'createdAtTimestamp': get_epoch_timestamp_in_ms(),
                'updatedAtTimestamp': get_epoch_timestamp_in_ms()
            }

            await arango_service.batch_upsert_nodes([doc], self.collection_name)
            self.logger.info(f"Created database entry for {app_name}")
            return True

        except Exception as e:
            self.logger.error(f"Error creating app {app_name} in database: {e}")
            return False

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
                db.aql.execute(query, bind_vars={
                    "name": app_name,
                    "node_updates": updated_doc,
                    "@collection": self.collection_name
                })

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
        1. Create missing apps from registry (as inactive)
        2. Deactivate apps in DB that are not in registry

        Returns:
            True if successful
        """
        try:
            arango_service = await self._get_arango_service()

            # Get all apps from database
            db_docs = await arango_service.get_all_documents(self.collection_name)
            db_apps = {doc['name']: doc for doc in db_docs}

            created_apps = []
            deactivated_apps = []

            # 1. Create missing apps from registry
            for app_name, metadata in self._connectors.items():
                if app_name not in db_apps:
                    if await self._create_app_in_db(app_name, metadata):
                        created_apps.append(app_name)

            # 2. Deactivate apps in DB that are not in registry
            for app_name, doc in db_apps.items():
                if app_name not in self._connectors and doc.get('isActive', False):
                    if await self._deactivate_app_in_db(app_name):
                        deactivated_apps.append(app_name)

            # Log summary
            if created_apps:
                self.logger.info(f"Created {len(created_apps)} new apps: {created_apps}")
            if deactivated_apps:
                self.logger.info(f"Deactivated {len(deactivated_apps)} apps not in registry: {deactivated_apps}")

            if not created_apps and not deactivated_apps:
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
        connectors = []
        for app_name, metadata in self._connectors.items():
            db_status = await self._get_db_status(app_name)
            connector_info = {
                'name': app_name,
                **metadata,
                **db_status
            }
            connectors.append(connector_info)
        return connectors

    async def get_enabled_connectors(self) -> List[Dict[str, Any]]:
        """Get all enabled connectors (isActive = true)."""
        all_connectors = await self.get_all_connectors()
        return [connector for connector in all_connectors if connector.get('isActive', False)]

    async def get_disabled_connectors(self) -> List[Dict[str, Any]]:
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
            db_status = await self._get_db_status(app_name)
            return {
                'name': app_name,
                **metadata,
                **db_status
            }
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
            'indexingStatus': ['NOT_STARTED', 'IN_PROGRESS', 'COMPLETED', 'FAILED'],
            'recordType': ['FILE', 'MAIL', 'MESSAGE', 'WEBPAGE', 'TICKET'],
            'origin': ['UPLOAD', 'CONNECTOR'],
            'permissions': ['READER', 'WRITER', 'OWNER', 'COMMENTER']
        }

    async def update_connector_config(self, app_name: str, config_update: Dict[str, Any]) -> bool:
        """
        Update connector configuration in database.

        Args:
            app_name: Name of the application
            config_update: Configuration updates to apply

        Returns:
            True if successful
        """
        try:
            arango_service = await self._get_arango_service()

            existing_doc = await arango_service.get_app_by_name(app_name)
            if not existing_doc:
                self.logger.error(f"App {app_name} not found in database")
                return False

            # Merge config updates
            current_config = existing_doc.get('config', {})
            updated_config = {**current_config, **config_update}

            updated_doc = {
                **existing_doc,
                'config': updated_config,
                'updatedAtTimestamp': get_epoch_timestamp_in_ms()
            }

            query = """
            FOR node IN @@collection
                FILTER node.name == @name
                UPDATE node WITH @node_updates IN @@collection
                RETURN NEW
            """
            db = arango_service.db
            db.aql.execute(query, bind_vars={
                "name": app_name,
                "node_updates": updated_doc,
                "@collection": self.collection_name
            })

            self.logger.info(f"Updated config for app {app_name}")
            return True

        except Exception as e:
            self.logger.error(f"Error updating config for app {app_name}: {e}")
            return False
