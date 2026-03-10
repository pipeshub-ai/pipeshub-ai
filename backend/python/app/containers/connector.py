import os

from dependency_injector import containers, providers

from app.config.configuration_service import ConfigurationService
from app.config.constants.service import config_node_constants
from app.config.providers.encrypted_store import EncryptedKeyValueStore
from app.connectors.core.base.data_store.graph_data_store import GraphDataStore
from app.connectors.services.base_arango_service import BaseArangoService
from app.connectors.services.kafka_service import KafkaService
from app.connectors.sources.localKB.handlers.migration_service import run_kb_migration
from app.containers.container import BaseAppContainer
from app.containers.utils.utils import ContainerUtils
from app.core.celery_app import CeleryApp
from app.core.signed_url import SignedUrlConfig, SignedUrlHandler
from app.health.health import Health
from app.migrations.connector_migration_service import ConnectorMigrationService
from app.migrations.delete_old_agents_templates_migration import (
    run_delete_old_agents_templates_migration,
)
from app.migrations.drive_to_drive_workspace_migration import (
    run_drive_to_drive_workspace_migration,
)
from app.migrations.files_to_records_migration import run_files_to_records_migration
from app.migrations.folder_hierarchy_migration import run_folder_hierarchy_migration
from app.migrations.knowledgebase_to_connector_migration import (
    run_kb_to_connector_migration,
)
from app.migrations.permission_edge_migration import (
    run_permissions_edge_migration,
    run_permissions_to_kb_migration,
)
from app.migrations.record_group_app_edge_migration import (
    run_record_group_app_edge_migration,
)
from app.migrations.toolset_instance_migration import run_toolset_instance_migration
from app.services.graph_db.graph_db_provider_factory import GraphDBProviderFactory
from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
from app.utils.logger import create_logger


class ConnectorAppContainer(BaseAppContainer):
    """Dependency injection container for the connector application."""

    # Override logger with service-specific name
    logger = providers.Singleton(create_logger, "connector_service")
    container_utils = ContainerUtils()
    key_value_store = providers.Singleton(EncryptedKeyValueStore, logger=logger)

    # Override config_service to use the service-specific logger
    config_service = providers.Singleton(ConfigurationService, logger=logger, key_value_store=key_value_store)

    # Override arango_client to use the service-specific config_service
    arango_client = providers.Resource(
        BaseAppContainer._create_arango_client, config_service=config_service
    )

    # Core Services
    kafka_service = providers.Singleton(
        KafkaService, logger=logger, config_service=config_service
    )

    # First create an async factory for the connected BaseArangoService
    @staticmethod
    async def _create_arango_service(logger, arango_client, kafka_service, config_service) -> BaseArangoService | None:
        """Async factory to create and connect BaseArangoService (with schema init allowed)"""
        # Skip ArangoDB service creation if using a different graph database
        data_store = os.getenv("DATA_STORE", "arangodb").lower()
        if data_store != "arangodb":
            logger.info(f"⏭️ Skipping ArangoDB service creation (DATA_STORE={data_store})")
            return None

        service = BaseArangoService(
            logger,
            arango_client,
            config_service,
            kafka_service,
            enable_schema_init=True,
        )
        await service.connect()
        return service


    arango_service = providers.Resource(
        _create_arango_service,
        logger=logger,
        arango_client=arango_client,
        kafka_service=kafka_service,
        config_service=config_service,
    )

    # Graph Database Provider via Factory (HTTP mode - fully async)
    @staticmethod
    async def _create_graphDB_provider(logger, config_service) -> IGraphDBProvider:
        """Async factory to create graph database provider"""
        return await GraphDBProviderFactory.create_provider(
            logger=logger,
            config_service=config_service,
        )

    graph_provider = providers.Resource(
        _create_graphDB_provider,
        logger=logger,
        config_service=config_service,
    )

    # Graph Data Store - Transaction-based data access layer
    @staticmethod
    async def _create_data_store(logger, graph_provider) -> GraphDataStore:
        """Async factory to create GraphDataStore with resolved graph_provider"""
        return GraphDataStore(logger, graph_provider)

    data_store = providers.Resource(
        _create_data_store,
        logger=logger,
        graph_provider=graph_provider,
    )

    # Note: KnowledgeBaseService is created in the router's get_kb_service() using
    # request.app.state.graph_provider and container.kafka_service (async Resource
    # does not inject well into Singleton). graph_provider no longer depends on kafka_service.
    # Note: KnowledgeHubService is created manually in the router's get_knowledge_hub_service()
    # helper function because it depends on async graph_provider which doesn't work well
    # with dependency_injector's Factory/Resource providers.

    # Celery and Tasks
    celery_app = providers.Singleton(
        CeleryApp, logger=logger, config_service=config_service
    )

    # Signed URL Handler
    signed_url_config = providers.Resource(
        SignedUrlConfig.create, config_service=config_service
    )
    signed_url_handler = providers.Singleton(
        SignedUrlHandler,
        logger=logger,
        config=signed_url_config,
        config_service=config_service,
    )

    feature_flag_service = providers.Singleton(container_utils.create_feature_flag_service, config_service=config_service)

    # Connector-specific wiring configuration
    wiring_config = containers.WiringConfiguration(
        modules=[
            "app.core.celery_app",
            "app.connectors.api.router",
            "app.connectors.sources.localKB.api.kb_router",
            "app.connectors.sources.localKB.api.knowledge_hub_router",
            "app.connectors.api.middleware",
            "app.core.signed_url",
        ]
    )

async def run_connector_migration(container) -> bool:
    """
    Run connector migration from name-based to UUID-based system.
    This should be called once during system initialization.

    Returns:
        bool: True if migration completed successfully or was not needed, False on error
    """
    logger = container.logger()

    try:
        logger.info("🔍 Checking if Connector UUID migration is needed...")

        # Get required services
        graph_provider = await container.graph_provider()
        config_service = container.config_service()

        # Create migration service instance
        migration_service = ConnectorMigrationService(
            graph_provider=graph_provider,
            config_service=config_service,
            logger=logger
        )

        # Run the migration
        await migration_service.migrate_all()

        logger.info("✅ Connector UUID migration completed successfully")
        return True

    except Exception as e:
        logger.error(f"❌ Connector UUID migration error: {str(e)}")
        # Don't fail startup - log error and continue
        # Migration is idempotent and can be retried
        return False

async def run_files_to_records_migration_wrapper(container) -> bool:
    """
    Run files to records MD5/Size migration.
    This should be called once during system initialization.

    Returns:
        bool: True if migration completed successfully or was not needed, False on error
    """
    logger = container.logger()

    try:
        logger.info("🔍 Checking if Files to Records MD5/Size migration is needed...")

        # Get required services
        graph_provider = await container.graph_provider()
        config_service = container.config_service()

        # Run the migration
        migration_result = await run_files_to_records_migration(
            graph_provider=graph_provider,
            config_service=config_service,
            logger=logger
        )

        if migration_result.get("success"):
            records_updated = migration_result.get("records_updated", 0)
            if records_updated > 0:
                logger.info(
                    f"✅ Files to Records MD5/Size migration completed: "
                    f"{records_updated} record(s) updated, "
                    f"{migration_result.get('md5_copied', 0)} MD5 checksum(s) copied, "
                    f"{migration_result.get('size_copied', 0)} size value(s) copied"
                )
            else:
                logger.info("✅ No Files to Records MD5/Size migration needed")
            return True
        else:
            logger.error(
                f"❌ Files to Records MD5/Size migration failed: "
                f"{migration_result.get('error', 'Unknown error')}"
            )
            return False

    except Exception as e:
        logger.error(f"❌ Files to Records MD5/Size migration error: {str(e)}")
        # Don't fail startup - log error and continue
        # Migration is idempotent and can be retried
        return False

async def run_drive_to_drive_workspace_migration_wrapper(container) -> bool:
    """
    Run drive to drive workspace migration.
    This should be called once during system initialization.

    Returns:
        bool: True if migration completed successfully or was not needed, False on error
    """
    logger = container.logger()

    try:
        logger.info("🔍 Checking if Drive to Drive Workspace migration is needed...")

        # Get required services
        graph_provider = await container.graph_provider()
        config_service = container.config_service()

        # Run the migration
        migration_result = await run_drive_to_drive_workspace_migration(
            graph_provider=graph_provider,
            config_service=config_service,
            logger=logger
        )

        if migration_result.get("success"):
            connectors_updated = migration_result.get("connectors_updated", 0)
            records_updated = migration_result.get("records_updated", 0)
            if connectors_updated > 0 or records_updated > 0:
                logger.info(
                    f"✅ Drive to Drive Workspace migration completed: "
                    f"{connectors_updated} connector(s) updated, "
                    f"{records_updated} record(s) updated"
                )
            else:
                logger.info("✅ No Drive to Drive Workspace migration needed")
            return True
        else:
            logger.error(
                f"❌ Drive to Drive Workspace migration failed: "
                f"{migration_result.get('error', 'Unknown error')}"
            )
            return False

    except Exception as e:
        logger.error(f"❌ Drive to Drive Workspace migration error: {str(e)}")
        # Don't fail startup - log error and continue
        # Migration is idempotent and can be retried
        return False

async def run_knowledge_base_migration(container) -> bool:
    """
    Run knowledge base migration from old system to new system
    This should be called once during system initialization
    """
    logger = container.logger()

    try:
        logger.info("🔍 Checking if Knowledge Base migration is needed...")

        # Run the migration
        migration_result = await run_kb_migration(container)

        if migration_result['success']:
            migrated_count = migration_result['migrated_count']
            if migrated_count > 0:
                logger.info(f"✅ Knowledge Base migration completed: {migrated_count} KBs migrated")
            else:
                logger.info("✅ No Knowledge Base migration needed")
            return True
        else:
            logger.error(f"❌ Knowledge Base migration failed: {migration_result['message']}")
            return False

    except Exception as e:
        logger.error(f"❌ Knowledge Base migration error: {str(e)}")
        return False

async def initialize_container(container) -> bool:
    """Initialize container resources with health checks."""

    logger = container.logger()
    config_service = container.config_service()
    migrations_key = config_node_constants.MIGRATIONS.value

    async def get_migration_state() -> dict:
        state = await config_service.get_config(migrations_key, default={})
        return state or {}

    def migration_completed(state: dict, name: str) -> bool:
        return bool(state.get(name))

    async def mark_migration_completed(name: str, result: dict) -> None:
        state = await get_migration_state()
        state[name] = True
        await config_service.set_config(migrations_key, state)

    logger.info("🚀 Initializing application resources")
    try:
        await Health.system_health_check(container)

        # Conditionally initialize ArangoDB service based on DATA_STORE
        data_store = os.getenv("DATA_STORE", "arangodb").lower()
        if data_store == "arangodb":
            logger.info("Ensuring ArangoDB service is initialized")
            # Arango_service is needed for migrations
            arango_service = await container.arango_service()
            if not arango_service:
                raise Exception("Failed to initialize ArangoDB service")
            logger.info("✅ ArangoDB service initialized")
        else:
            logger.info(f"⏭️ Skipping ArangoDB service init (DATA_STORE={data_store})")
            arango_service = None

        logger.info("Ensuring graph database provider is initialized")
        data_store = await container.data_store()
        if not data_store:
            raise Exception("Failed to initialize data store")
        logger.info("✅ Data store initialized")

        # Schema init: collections, graph, departments seed
        await data_store.graph_provider.ensure_schema()
        logger.info("✅ Schema ensured")

        logger.info("✅ Container initialization completed successfully")

        migration_state = await get_migration_state()


        if migration_completed(migration_state, "toolsetInstancePaths"):
            logger.info("⏭️ Toolset Instance Path migration already completed, skipping.")
        else:
            logger.info("🔄 Running Toolset Instance Path migration...")
            # Use graph_provider from data_store which is already resolved
            # This avoids potential coroutine reuse issues with container.graph_provider()
            result_toolset_migration = await run_toolset_instance_migration(
                graph_provider=data_store.graph_provider,
                config_service=config_service,
                logger=logger
            )
            if result_toolset_migration.get("success"):
                agents_deleted = result_toolset_migration.get("agents_deleted", 0)
                toolsets_deleted = result_toolset_migration.get("toolsets_deleted", 0)
                tools_deleted = result_toolset_migration.get("tools_deleted", 0)
                edges_deleted = result_toolset_migration.get("edges_deleted", 0)
                instances_created = result_toolset_migration.get("instances_created", 0)
                oauth_configs_created = result_toolset_migration.get("oauth_configs_created", 0)
                users_migrated = result_toolset_migration.get("users_migrated", 0)
                users_deleted = result_toolset_migration.get("users_deleted", 0)
                logger.info(
                    f"✅ Toolset Instance migration completed:\n"
                    f"   - {agents_deleted} agents hard deleted\n"
                    f"   - {toolsets_deleted} toolset nodes deleted\n"
                    f"   - {tools_deleted} tool nodes deleted\n"
                    f"   - {edges_deleted} edges/relationships deleted\n"
                    f"   - {instances_created} instances created\n"
                    f"   - {oauth_configs_created} OAuth configs created\n"
                    f"   - {users_migrated} admin users migrated (NOT authenticated - require re-auth)\n"
                    f"   - {users_deleted} non-admin users deleted"
                )
                await mark_migration_completed("toolsetInstancePaths", result_toolset_migration)
            else:
                error_msg = result_toolset_migration.get("message", "Unknown error")
                logger.error(f"❌ Toolset Instance Path migration failed: {error_msg}")

        # Skip all migrations if Neo4j is configured (migrations are ArangoDB-specific)
        if data_store != "arangodb":
            logger.info(f"⏭️ Skipping all migrations (DATA_STORE={data_store}, migrations are ArangoDB-specific)")
            return True


        if migration_completed(migration_state, "knowledgeBase"):
            logger.info("⏭️ Knowledge Base migration already completed, skipping.")
        else:
            logger.info("🔄 Running Knowledge Base migration...")
            migration_success = await run_knowledge_base_migration(container)
            if migration_success:
                await mark_migration_completed("knowledgeBase", {})
            else:
                logger.warning("⚠️ Knowledge Base migration had issues but continuing initialization")
        logger.info("🔄 Running Connector UUID migration...")
        connector_migration_success = await run_connector_migration(container)
        if not connector_migration_success:
            logger.warning("⚠️ Connector UUID migration had issues but continuing initialization")

        logger.info("🔄 Running Files to Records MD5/Size migration...")
        files_to_records_migration_success = await run_files_to_records_migration_wrapper(container)
        if not files_to_records_migration_success:
            logger.warning("⚠️ Files to Records MD5/Size migration had issues but continuing initialization")

        migration_state = await get_migration_state()

        if migration_completed(migration_state, "driveToDriveWorkspace"):
            logger.info("⏭️ Drive to Drive Workspace migration already completed, skipping.")
        else:
            logger.info("🔄 Running Drive to Drive Workspace migration...")
            drive_to_drive_workspace_migration_success = await run_drive_to_drive_workspace_migration_wrapper(container)
            if drive_to_drive_workspace_migration_success:
                await mark_migration_completed("driveToDriveWorkspace", {})
            else:
                logger.warning("⚠️ Drive to Drive Workspace migration had issues but continuing initialization")

        logger.info("🔄 Running Knowledge Base migration...")
        migration_success = await run_knowledge_base_migration(container)
        if not migration_success:
            logger.warning("⚠️ Knowledge Base migration had issues but continuing initialization")

        # Run KB to Connector migration (new migration with ETCD flag)
        logger.info("🔄 Running Knowledge Base to Connector migration...")
        kb_connector_migration_result = await run_kb_to_connector_migration(container)
        if kb_connector_migration_result.get("success"):
            if kb_connector_migration_result.get("skipped"):
                logger.info("⏭️ KB to Connector migration already completed, skipped.")
            else:
                logger.info(
                    f"✅ KB to Connector migration completed: "
                    f"{kb_connector_migration_result.get('orgs_processed', 0)} orgs processed, "
                    f"{kb_connector_migration_result.get('apps_created', 0)} apps created, "
                    f"{kb_connector_migration_result.get('records_updated', 0)} records updated"
                )
        else:
            logger.warning("⚠️ KB to Connector migration had issues but continuing initialization")

        migration_state = await get_migration_state()

        if migration_completed(migration_state, "permissionsEdge"):
            logger.info("⏭️ Permissions Edge migration already completed, skipping.")
        elif arango_service is None:
            logger.info("⏭️ Skipping Permissions Edge migration (ArangoDB service not initialized)")
        else:
            logger.info("🔄 Running Permissions Edge migration...")
            result_permissions_migration = await run_permissions_edge_migration(
                arango_service, logger, dry_run=False, batch_size=1000
            )
            if result_permissions_migration.get("success"):
                logger.info(f"Migrated: {result_permissions_migration.get('migrated_edges')} edges")
                logger.info(f"Deleted: {result_permissions_migration.get('deleted_edges')} edges")
                await mark_migration_completed("permissionsEdge", result_permissions_migration)
            else:
                logger.error(f"Failed: {result_permissions_migration.get('message')}")

        migration_state = await get_migration_state()

        if migration_completed(migration_state, "permissionsToKb"):
            logger.info("⏭️ Permissions To KB migration already completed, skipping.")
        elif arango_service is None:
            logger.info("⏭️ Skipping Permissions To KB migration (ArangoDB service not initialized)")
        else:
            logger.info("🔄 Running Permissions To KB migration...")
            result_permissions_to_kb_migration = await run_permissions_to_kb_migration(
                arango_service, logger, dry_run=False, batch_size=1000
            )
            if result_permissions_to_kb_migration.get("success"):
                logger.info(f"Migrated: {result_permissions_to_kb_migration.get('migrated_edges')} edges")
                logger.info(f"Deleted: {result_permissions_to_kb_migration.get('deleted_edges')} edges")
                await mark_migration_completed("permissionsToKb", result_permissions_to_kb_migration)
            else:
                logger.error(f"Failed: {result_permissions_to_kb_migration.get('message')}")

        migration_state = await get_migration_state()

        if migration_completed(migration_state, "folderHierarchy"):
            logger.info("⏭️ Folder Hierarchy migration already completed, skipping.")
        elif arango_service is None:
            logger.info("⏭️ Skipping Folder Hierarchy migration (ArangoDB service not initialized)")
        else:
            logger.info("🔄 Running Folder Hierarchy migration...")
            result_folder_hierarchy_migration = await run_folder_hierarchy_migration(
                arango_service, config_service, logger, dry_run=False
            )
            if result_folder_hierarchy_migration.get("success"):
                folders_migrated = result_folder_hierarchy_migration.get('folders_migrated', 0)
                edges_created = result_folder_hierarchy_migration.get('edges_created', 0)
                edges_updated = result_folder_hierarchy_migration.get('edges_updated', 0)

                if result_folder_hierarchy_migration.get('skipped'):
                    logger.info("⏭️ Folder Hierarchy migration already completed (checked by service)")
                else:
                    logger.info(f"✅ Migrated: {folders_migrated} folders")
                    logger.info(f"✅ Edges created: {edges_created}")
                    logger.info(f"✅ Edges updated: {edges_updated}")

                await mark_migration_completed("folderHierarchy", result_folder_hierarchy_migration)
            else:
                error_msg = result_folder_hierarchy_migration.get('error') or result_folder_hierarchy_migration.get('message', 'Unknown error')
                logger.error(f"❌ Folder Hierarchy migration failed: {error_msg}")

        migration_state = await get_migration_state()

        if migration_completed(migration_state, "recordGroupAppEdge"):
            logger.info("⏭️ Record Group -> App edge migration already completed, skipping.")
        else:
            logger.info("🔄 Running Record Group -> App edge migration...")
            result_rg_app_edge = await run_record_group_app_edge_migration(
                arango_service, config_service, logger
            )
            if result_rg_app_edge.get("success"):
                if result_rg_app_edge.get("skipped"):
                    logger.info("⏭️ Record Group -> App edge migration already completed (checked by service)")
                else:
                    edges_created = result_rg_app_edge.get("edges_created", 0)
                    logger.info(f"✅ Record Group -> App edge migration: {edges_created} edges created")
                await mark_migration_completed("recordGroupAppEdge", result_rg_app_edge)
            else:
                error_msg = result_rg_app_edge.get("message", "Unknown error")
                logger.error(f"❌ Record Group -> App edge migration failed: {error_msg}")

        if migration_completed(migration_state, "deleteOldAgentsTemplates"):
            logger.info("⏭️ Delete Old Agents and Templates migration already completed, skipping.")
        else:
            logger.info("🔄 Running Delete Old Agents and Templates migration...")
            result_delete_agents_templates = await run_delete_old_agents_templates_migration(container)
            if result_delete_agents_templates.get("success"):
                agents_deleted = result_delete_agents_templates.get("agents_deleted", 0)
                templates_deleted = result_delete_agents_templates.get("templates_deleted", 0)
                total_edges_deleted = result_delete_agents_templates.get("total_edges_deleted", 0)
                logger.info(
                    f"✅ Delete Old Agents and Templates migration completed: "
                    f"{agents_deleted} agents, {templates_deleted} templates, "
                    f"{total_edges_deleted} edges deleted"
                )
                await mark_migration_completed("deleteOldAgentsTemplates", result_delete_agents_templates)
            else:
                error_msg = result_delete_agents_templates.get("message", "Unknown error")
                logger.error(f"❌ Delete Old Agents and Templates migration failed: {error_msg}")

        return True

    except Exception as e:
        logger.error(f"❌ Container initialization failed: {str(e)}")
        raise
