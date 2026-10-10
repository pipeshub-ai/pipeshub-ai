import asyncio
import logging
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
from app.edition_services import bootstrap_guard
from app.health.health import Health
from app.migrations.all_team_migration import run_all_team_migration
from app.migrations.app_org_id_migration import run_app_org_id_migration
from app.migrations.duplicate_user_groups_migration import run_duplicate_user_groups_migration
from app.migrations.folder_mime_type_migration import run_folder_mime_type_migration
from app.migrations.hierarchy_backfill_migration import (
    run_hierarchy_backfill_migration,
    run_hierarchy_nested_group_roots_migration,
)
from app.migrations.kb_apps_migration import run_kb_apps_migration
from app.migrations.kh_listing_state_migration import run_kh_listing_state_migration
from app.migrations.mailbox_record_grants_migration import run_mailbox_record_grants_migration
from app.migrations.node_relation_migration import run_node_relation_migration
from app.migrations.record_link_migration import run_record_link_migration
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

# Background tasks started at initialization, held so they are not garbage-collected while running.
_kh_scope_tasks: set = set()
# How often the scope keeper looks for connectors to stamp: after Labs turns the listing on, a write outside a
# sync, or a stamp that was cut short.
KH_SCOPE_KEEPER_SECONDS = 60


# A background migration that failed (a deadlock with a running sync, the graph database restarting) is tried
# again after this long, doubling up to the maximum.
KH_MIGRATION_RETRY_SECONDS = 60
KH_MIGRATION_RETRY_MAX_SECONDS = 3600


async def _migrated(name: str, run, logger: logging.Logger, **kwargs) -> bool:
    try:
        result = await run(logger=logger, **kwargs)
    except Exception as e:
        logger.error(f"❌ {name} migration error: {e}")
        return False
    if not result.get("success"):
        logger.error(f"❌ {name} migration failed: {result.get('error')}")
        return False
    if not result.get("skipped"):
        logger.info(f"✅ {name} migration completed: {result}")
    return True


async def _kh_graph_migrations(
    graph_provider: "IGraphDBProvider", config_service: ConfigurationService, logger: logging.Logger,
    kb_apps_done: bool,
) -> bool:
    """The graph migrations that can outlast the process monitor's start limit (120 s, after which it restarts
    the service in a loop), in order, each retried until it succeeds. Until they finish an old graph is listed
    short, never wide. False when the hierarchy steps wait for the next start."""
    # (name, migration, the steps that must have finished first)
    steps = [
        ("Record link", run_record_link_migration, ()),
        ("Folder mimeType", run_folder_mime_type_migration, ()),
        ("Mailbox record grants", run_mailbox_record_grants_migration, ()),
        ("Duplicate user groups", run_duplicate_user_groups_migration, ()),
    ]
    if kb_apps_done:
        steps += [
            # After the grants removal, so an attachment of a mail that carried grants inherits from that mail.
            ("Hierarchy backfill", run_hierarchy_backfill_migration, ("Record link", "Mailbox record grants")),
            # Its own flag: stacks that ran the backfill before this shape existed get it too.
            ("Hierarchy nested group roots", run_hierarchy_nested_group_roots_migration,
             ("Mailbox record grants", "Hierarchy backfill")),
            ("Knowledge hub listing state", run_kh_listing_state_migration, ("Hierarchy backfill",)),
        ]
    else:
        # The backfill would hang collection roots from the old KB groups the KB apps migration removes.
        logger.warning("⚠️ Hierarchy backfill deferred until the KB apps migration succeeds")
    done: set = set()
    delay = KH_MIGRATION_RETRY_SECONDS
    while True:
        for name, run, after in steps:
            if name not in done and done.issuperset(after) and await _migrated(
                name, run, logger, graph_provider=graph_provider, config_service=config_service,
            ):
                done.add(name)
        if len(done) == len(steps):
            return kb_apps_done
        await asyncio.sleep(delay)
        delay = min(delay * 2, KH_MIGRATION_RETRY_MAX_SECONDS)


async def _kh_listing_background(
    graph_provider: "IGraphDBProvider", config_service: ConfigurationService, logger: logging.Logger,
    kb_apps_done: bool,
) -> None:
    if not await _kh_graph_migrations(graph_provider, config_service, logger, kb_apps_done):
        return
    # While the scope listing is enabled, keep every connector stamped. While it is off this only reads the flag.
    while True:
        try:
            results = await graph_provider.kh_scope_restamp_stale()
            stamped = [r for r in results if r.get("stamped")]
            if stamped:
                logger.info(f"Knowledge hub scopes stamped: {stamped}")
        except Exception as e:
            logger.error(f"❌ Knowledge hub scope keeper failed: {e}")
        await asyncio.sleep(KH_SCOPE_KEEPER_SECONDS)


async def _kh_plan_keeper(
    graph_provider: "IGraphDBProvider", config_service: ConfigurationService, logger: logging.Logger,
) -> None:
    """Keep the knowledge hub's browse statements planned (see ``warm_browse_plans``):
    once at start, then every KH_PLAN_KEEPER_SECONDS (0 turns it off)."""
    from app.connectors.sources.localKB.handlers.knowledge_hub_service import KnowledgeHubService
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider as _Provider

    try:
        every = float(os.environ.get("KH_PLAN_KEEPER_SECONDS", "600") or 0)
    except ValueError:
        logger.warning("KH_PLAN_KEEPER_SECONDS is not a number; the knowledge hub plan keeper is off")
        return
    # Nothing to keep for a backend that offers no sample (the interface's default).
    if every <= 0 or type(graph_provider).get_knowledge_hub_warm_sample is _Provider.get_knowledge_hub_warm_sample:
        return
    service = KnowledgeHubService(logger=logger, graph_provider=graph_provider, config_service=config_service)
    while True:
        try:
            await service.warm_browse_plans()
        except Exception as e:
            logger.warning(f"Knowledge hub plan keeper failed: {e}")
        await asyncio.sleep(every)


async def initialize_container(container, *, bootstrap: bool = True) -> bool:
    """Initialize container resources with health checks.

    ``bootstrap=False`` skips every step that mutates shared state, leaving only
    the health check and the data store. Secondary processes must pass False:
    the skipped steps are not safe to run concurrently.

    - The deployment KV write is a read-modify-write of one document.
    - ``ensure_schema`` is check-then-act over collections and the graph, and on
      Arango includes a read-modify-write of the edge definitions.
    - The All-team migration guards itself with a KV flag it reads and then
      writes, so two processes booting together both see "not done" and both run
      the full cross-org backfill.

    On Docker the ordering also protects us — the supervisor blocks on the API's
    /health before starting anything else — but that guarantee does not hold for
    multi-replica Kubernetes, so the flag is the real protection.
    """

    logger = container.logger()
    config_service = container.config_service()

    logger.info("🚀 Initializing application resources")
    try:
        await Health.system_health_check(container)

        if not bootstrap:
            data_store = await container.data_store()
            if not data_store:
                raise Exception("Failed to initialize data store")
            logger.info("✅ Container initialized (secondary process, bootstrap skipped)")
            return True

        # Only one process does the one-time work. Without this every uvicorn
        # worker races ensure_schema and the All-team migration, which is slow
        # enough to blow the supervisor's health gate and makes the migration's
        # read-then-write "done" flag useless.
        async with bootstrap_guard(logger, config_service) as should_bootstrap:
            if not should_bootstrap:
                data_store = await container.data_store()
                if not data_store:
                    raise Exception("Failed to initialize data store")
                logger.info("✅ Container initialized (bootstrap done by another process)")
                return True

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

            # Rename the hierarchy edge before schema init, not after it like the
            # migrations below: schema init would otherwise create an empty
            # nodeRelations collection on a deployment whose edges still live under
            # the old name, and the app would read the empty one.
            try:
                logger.info("🔄 Running node relation migration...")

                node_relation_result = await run_node_relation_migration(
                    graph_provider=data_store.graph_provider,
                    config_service=config_service,
                    logger=logger
                )

                if node_relation_result.get("success"):
                    if node_relation_result.get("skipped"):
                        logger.info("✅ Node relation migration already completed")
                    else:
                        logger.info(
                            f"✅ Node relation migration completed: "
                            f"{node_relation_result.get('migrated', 0)} edge(s) migrated"
                        )
                else:
                    raise Exception(
                        node_relation_result.get("error", "Unknown error")
                    )
            except Exception as e:
                # Fatal, unlike the migrations below: continuing into ensure_schema()
                # would serve a graph with no hierarchy. Failing here leaves the
                # data intact for the next attempt.
                logger.error(f"❌ Node relation migration error: {e}")
                raise

            # Schema init: collections, graph, departments seed
            await data_store.graph_provider.ensure_schema()
            logger.info("✅ Schema ensured")

            # Apps from before connector instances carried an orgId get one, or the
            # org filters on Apps drop them. Not fatal: the next startup retries.
            try:
                org_result = await run_app_org_id_migration(
                    graph_provider=data_store.graph_provider,
                    config_service=config_service,
                    logger=logger,
                )
                if not org_result.get("success"):
                    logger.error(f"❌ App orgId migration failed: {org_result.get('error')}")
                elif not org_result.get("skipped"):
                    logger.info(f"✅ App orgId migration stamped {org_result.get('backfilled', 0)} App(s)")
            except Exception as e:
                logger.error(f"❌ App orgId migration error: {e}")

            logger.info("✅ Container initialization completed successfully")


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
                kb_apps_done = bool(kb_migration_result.get("success"))
            except Exception as e:
                logger.error(f"❌ KB apps migration error: {e}")
                kb_apps_done = False

            # No sync of this process has started yet, so a knowledge hub scope still
            # marked as syncing was left by a crash. Cleared here, before syncs resume:
            # cleared later, it would clear the marks of syncs already running.
            try:
                await data_store.graph_provider.kh_scope_reset_syncing()
            except Exception as e:
                logger.error(f"❌ Knowledge hub scope reset failed: {e}")

            _kh_scope_tasks.add(asyncio.get_running_loop().create_task(
                _kh_listing_background(data_store.graph_provider, config_service, logger, kb_apps_done)))
            _kh_scope_tasks.add(asyncio.get_running_loop().create_task(
                _kh_plan_keeper(data_store.graph_provider, config_service, logger)))

            return True

    except Exception as e:
        logger.error(f"❌ Container initialization failed: {str(e)}")
        raise
