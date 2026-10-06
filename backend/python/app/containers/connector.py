import os

from dependency_injector import containers, providers

from app.config.configuration_service import ConfigurationService
from app.config.constants.service import config_node_constants
from app.config.providers.encrypted_store import EncryptedKeyValueStore
from app.connectors.core.base.data_store.graph_data_store import GraphDataStore
from app.services.notification.notification_service import (
    NotificationService,
)
from app.connectors.services.kafka_service import KafkaService
from app.containers.container import BaseAppContainer
from app.containers.utils.utils import ContainerUtils
from app.core.celery_app import CeleryApp
from app.core.signed_url import SignedUrlConfig, SignedUrlHandler
from app.health.health import Health
from app.migrations.agent_handles_migration import run_agent_handles_migration
from app.migrations.all_team_migration import run_all_team_migration
from app.migrations.chat_grant_edges_cleanup_migration import (
    run_chat_grant_edges_cleanup_migration,
)
from app.modules.authz.node_pdp_client import (
    AiohttpPdpHttp,
    NodePdpClient,
    set_node_pdp_client,
)
from app.migrations.team_org_id_migration import run_team_org_id_migration
from app.migrations.kb_apps_migration import run_kb_apps_migration
from app.migrations.kb_team_edge_role_migration import (
    run_kb_team_edge_role_migration,
)
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

    connector_notification_service = providers.Singleton(
        NotificationService,
        kafka_service=kafka_service,
        logger=logger,
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

    # Chat-content PDP client (Node `authz/internal/check`); installed as the
    # process accessor in `initialize_container`.
    node_pdp_client = providers.Singleton(
        NodePdpClient, config_service=config_service, http=providers.Singleton(AiohttpPdpHttp),
    )

    # For the startup health check (Health.health_check_vector_db). Entity
    # cleanup on connector and Collection deletion runs in the indexing
    # service (deleteConnectorEntities).
    vector_db_service = providers.Resource(
        container_utils.get_vector_db_service,
        config_service=config_service,
    )

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

async def initialize_container(container) -> bool:
    """Initialize container resources with health checks."""

    logger = container.logger()
    config_service = container.config_service()

    logger.info("🚀 Initializing application resources")
    try:
        set_node_pdp_client(container.node_pdp_client())
        await Health.system_health_check(container)

        # Write deployment config to KV store so Node.js can read it
        data_store_type = os.getenv("DATA_STORE", "arangodb").lower()
        try:
            existing_deployment = await config_service.get_config(
                config_node_constants.DEPLOYMENT.value, default={}
            ) or {}
            existing_deployment["dataStoreType"] = data_store_type
            existing_deployment["vectorDbType"] = os.getenv("VECTOR_DB_TYPE", "qdrant").lower().strip()
            await config_service.set_config(
                config_node_constants.DEPLOYMENT.value, existing_deployment
            )
            logger.info(f"✅ Deployment config written to KV store (dataStoreType={data_store_type})")
        except Exception as e:
            logger.warning(f"⚠️ Failed to write deployment config to KV store: {e}")

        logger.info("Ensuring graph database provider is initialized")
        data_store = await container.data_store()
        if not data_store:
            raise Exception("Failed to initialize data store")
        logger.info("✅ Data store initialized")

        # Schema init: collections, graph, departments seed
        await data_store.graph_provider.ensure_schema()
        logger.info("✅ Schema ensured")

        logger.info("✅ Container initialization completed successfully")


        # Must precede the All team migration: team lookups are org-scoped.
        try:
            team_org_result = await run_team_org_id_migration(
                graph_provider=data_store.graph_provider,
                config_service=config_service,
                logger=logger,
            )
            if not team_org_result.get("success"):
                logger.error(f"❌ Team orgId migration failed: {team_org_result.get('error', 'Unknown error')}")
        except Exception as e:
            logger.error(f"❌ Team orgId migration error: {e}")

        # Run All Team migration (DB-agnostic, runs for both ArangoDB and Neo4j)
        try:
            logger.info("🔄 Running All team migration...")
            
            migration_result = await run_all_team_migration(
                graph_provider=data_store.graph_provider,
                config_service=config_service,
                logger=logger
            )
            
            if migration_result.get("success"):
                if migration_result.get("skipped"):
                    logger.info("✅ All team migration already completed")
                else:
                    orgs_processed = migration_result.get("orgs_processed", 0)
                    teams_created = migration_result.get("teams_created", 0)
                    logger.info(
                        f"✅ All team migration completed: "
                        f"{orgs_processed} orgs processed, {teams_created} All teams ensured"
                    )
            else:
                error_msg = migration_result.get("error", "Unknown error")
                logger.error(f"❌ All team migration failed: {error_msg}")
        except Exception as e:
            logger.error(f"❌ All team migration error: {e}")

        # Ships in the same release as the PDP read path (PR-7.1-7.3); without the PDP
        # live, deleting these edges would briefly cut recipients off from shared chat files.
        try:
            chat_cleanup_result = await run_chat_grant_edges_cleanup_migration(
                graph_provider=data_store.graph_provider,
                config_service=config_service,
                logger=logger,
            )
            if chat_cleanup_result.get("success"):
                if chat_cleanup_result.get("skipped"):
                    logger.info("✅ Chat grant edges cleanup already completed")
                else:
                    logger.info(
                        f"✅ Chat grant edges cleanup completed: "
                        f"{chat_cleanup_result.get('deleted', 0)} edge(s) deleted"
                    )
            else:
                logger.error(
                    f"❌ Chat grant edges cleanup failed: {chat_cleanup_result.get('error', 'Unknown error')}"
                )
        except Exception as e:
            logger.error(f"❌ Chat grant edges cleanup error: {e}")

        # Run KB apps migration (legacy recordGroup-based KBs -> per-KB app
        # instances). Must run after the graph provider/schema are ready;
        # must complete before connectors_main.py's resume_sync_services()
        # so newly-migrated KB apps get a KnowledgeBaseConnector instance
        # registered on the same boot that migrates them.
        try:
            logger.info("🔄 Running KB apps migration...")

            kb_migration_result = await run_kb_apps_migration(
                graph_provider=data_store.graph_provider,
                config_service=config_service,
                logger=logger
            )

            if kb_migration_result.get("success"):
                if kb_migration_result.get("skipped"):
                    logger.info("✅ KB apps migration already completed")
                else:
                    orgs_processed = kb_migration_result.get("orgs_processed", 0)
                    kbs_migrated = kb_migration_result.get("kbs_migrated", 0)
                    logger.info(
                        f"✅ KB apps migration completed: "
                        f"{orgs_processed} orgs processed, {kbs_migrated} KB(s) migrated"
                    )
            else:
                error_msg = kb_migration_result.get("error", "Unknown error")
                logger.error(f"❌ KB apps migration failed: {error_msg}")
        except Exception as e:
            logger.error(f"❌ KB apps migration error: {e}")

        try:
            handles_result = await run_agent_handles_migration(
                graph_provider=data_store.graph_provider,
                config_service=config_service,
                logger=logger,
            )
            if handles_result.get("success"):
                if handles_result.get("skipped"):
                    logger.info("✅ Agent handles migration already completed")
                else:
                    logger.info(
                        f"✅ Agent handles migration completed: {handles_result.get('agents_updated', 0)} agent(s) updated"
                    )
            else:
                logger.error(f"❌ Agent handles migration failed: {handles_result.get('error', 'Unknown error')}")
        except Exception as e:
            logger.error(f"❌ Agent handles migration error: {e}")

        try:
            role_migration_result = await run_kb_team_edge_role_migration(
                graph_provider=data_store.graph_provider,
                config_service=config_service,
                logger=logger,
            )

            if role_migration_result.get("success"):
                if role_migration_result.get("skipped"):
                    logger.info("✅ KB team-edge role migration already completed")
                else:
                    logger.info(
                        f"✅ KB team-edge role migration completed: "
                        f"{role_migration_result.get('stamped', 0)} edge(s) stamped, "
                        f"{role_migration_result.get('remaining_role_less', 0)} role-less"
                    )
            elif role_migration_result.get("skipped"):
                logger.warning(
                    f"⚠️ KB team-edge role migration deferred: {role_migration_result.get('reason')}"
                )
            else:
                logger.error(
                    f"❌ KB team-edge role migration failed: {role_migration_result.get('error', 'Unknown error')}"
                )
        except Exception as e:
            logger.error(f"❌ KB team-edge role migration error: {e}")

        return True

    except Exception as e:
        logger.error(f"❌ Container initialization failed: {str(e)}")
        raise
