"""
src/api/setup.py
"""

import os

import aiohttp
from arango import ArangoClient
from confluent_kafka import Consumer, KafkaError
from dependency_injector import containers, providers
from qdrant_client import QdrantClient
from redis import asyncio as aioredis
from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.config.configuration_service import (
    ConfigurationService,
    RedisConfig,
    config_node_constants,
)
from app.connectors.core.kafka_service import KafkaService
from app.connectors.core.sync_kafka_consumer import SyncKafkaRouteConsumer
from app.connectors.google.admin.admin_webhook_handler import AdminWebhookHandler
from app.connectors.google.admin.google_admin_service import GoogleAdminService
from app.connectors.google.core.arango_service import ArangoService
from app.connectors.google.core.sync_tasks import SyncTasks
from app.connectors.google.gmail.core.gmail_sync_service import (
    GmailSyncEnterpriseService,
    GmailSyncIndividualService,
)
from app.connectors.google.gmail.core.gmail_user_service import GmailUserService
from app.connectors.google.gmail.handlers.gmail_change_handler import GmailChangeHandler
from app.connectors.google.gmail.handlers.gmail_webhook_handler import (
    EnterpriseGmailWebhookHandler,
    IndividualGmailWebhookHandler,
)
from app.connectors.google.google_drive.core.drive_sync_service import (
    DriveSyncEnterpriseService,
    DriveSyncIndividualService,
)
from app.connectors.google.google_drive.core.drive_user_service import DriveUserService
from app.connectors.google.google_drive.handlers.drive_change_handler import (
    DriveChangeHandler,
)
from app.connectors.google.google_drive.handlers.drive_webhook_handler import (
    EnterpriseDriveWebhookHandler,
    IndividualDriveWebhookHandler,
)
from app.connectors.google.helpers.google_token_handler import GoogleTokenHandler
from app.connectors.utils.rate_limiter import GoogleAPIRateLimiter
from app.core.celery_app import CeleryApp
from app.core.signed_url import SignedUrlConfig, SignedUrlHandler
from app.modules.parsers.google_files.google_docs_parser import GoogleDocsParser
from app.modules.parsers.google_files.google_sheets_parser import GoogleSheetsParser
from app.modules.parsers.google_files.google_slides_parser import GoogleSlidesParser
from app.modules.parsers.google_files.parser_user_service import ParserUserService
from app.utils.logger import create_logger


async def initialize_individual_account_services_fn(org_id, container):
    """Initialize services for an individual account type."""
    try:
        logger = container.logger()
        # Initialize base services
        container.drive_service.override(
            providers.Singleton(
                DriveUserService,
                logger=logger,
                config=container.config_service,
                rate_limiter=container.rate_limiter,
                google_token_handler=await container.google_token_handler(),
            )
        )
        drive_service = container.drive_service()
        assert isinstance(drive_service, DriveUserService)

        container.gmail_service.override(
            providers.Singleton(
                GmailUserService,
                logger=logger,
                config=container.config_service,
                rate_limiter=container.rate_limiter,
                google_token_handler=await container.google_token_handler(),
            )
        )
        gmail_service = container.gmail_service()
        assert isinstance(gmail_service, GmailUserService)

        # Initialize webhook handlers
        container.drive_webhook_handler.override(
            providers.Singleton(
                IndividualDriveWebhookHandler,
                logger=logger,
                config=container.config_service,
                drive_user_service=container.drive_service(),
                arango_service=await container.arango_service(),
                change_handler=await container.drive_change_handler(),
            )
        )
        drive_webhook_handler = container.drive_webhook_handler()
        assert isinstance(drive_webhook_handler, IndividualDriveWebhookHandler)

        container.gmail_webhook_handler.override(
            providers.Singleton(
                IndividualGmailWebhookHandler,
                logger=logger,
                config=container.config_service,
                gmail_user_service=container.gmail_service(),
                arango_service=await container.arango_service(),
                change_handler=await container.gmail_change_handler(),
            )
        )
        gmail_webhook_handler = container.gmail_webhook_handler()
        assert isinstance(gmail_webhook_handler, IndividualGmailWebhookHandler)

        # Initialize sync services
        container.drive_sync_service.override(
            providers.Singleton(
                DriveSyncIndividualService,
                logger=logger,
                config=container.config_service,
                drive_user_service=container.drive_service(),
                arango_service=await container.arango_service(),
                change_handler=await container.drive_change_handler(),
                kafka_service=container.kafka_service,
                celery_app=container.celery_app,
            )
        )
        drive_sync_service = container.drive_sync_service()
        assert isinstance(drive_sync_service, DriveSyncIndividualService)

        container.gmail_sync_service.override(
            providers.Singleton(
                GmailSyncIndividualService,
                logger=logger,
                config=container.config_service,
                gmail_user_service=container.gmail_service(),
                arango_service=await container.arango_service(),
                change_handler=await container.gmail_change_handler(),
                kafka_service=container.kafka_service,
                celery_app=container.celery_app,
            )
        )
        gmail_sync_service = container.gmail_sync_service()
        assert isinstance(gmail_sync_service, GmailSyncIndividualService)

        container.sync_tasks.override(
            providers.Singleton(
                SyncTasks,
                logger=logger,
                celery_app=container.celery_app,
                drive_sync_service=container.drive_sync_service(),
                gmail_sync_service=container.gmail_sync_service(),
                arango_service=await container.arango_service(),
            )
        )
        sync_tasks = container.sync_tasks()
        assert isinstance(sync_tasks, SyncTasks)

        container.parser_user_service.override(
            providers.Singleton(
                ParserUserService,
                logger=logger,
                config=container.config_service,
                rate_limiter=container.rate_limiter,
                google_token_handler=await container.google_token_handler(),
            )
        )
        parser_user_service = container.parser_user_service()
        assert isinstance(parser_user_service, ParserUserService)

        container.google_docs_parser.override(
            providers.Singleton(
                GoogleDocsParser,
                logger=logger,
                user_service=container.parser_user_service(),
            )
        )
        google_docs_parser = container.google_docs_parser()
        assert isinstance(google_docs_parser, GoogleDocsParser)

        container.google_sheets_parser.override(
            providers.Singleton(
                GoogleSheetsParser,
                logger=logger,
                user_service=container.parser_user_service(),
            )
        )
        google_sheets_parser = container.google_sheets_parser()
        assert isinstance(google_sheets_parser, GoogleSheetsParser)

        container.google_slides_parser.override(
            providers.Singleton(
                GoogleSlidesParser,
                logger=logger,
                user_service=container.parser_user_service(),
            )
        )
        google_slides_parser = container.google_slides_parser()
        assert isinstance(google_slides_parser, GoogleSlidesParser)

        container.sync_kafka_consumer.override(
            providers.Singleton(
                SyncKafkaRouteConsumer,
                logger=logger,
                config_service=container.config_service,
                arango_service=await container.arango_service(),
                sync_tasks=container.sync_tasks(),
            )
        )
        sync_kafka_consumer = container.sync_kafka_consumer()
        assert isinstance(sync_kafka_consumer, SyncKafkaRouteConsumer)

        # Start the sync Kafka consumer
        await sync_kafka_consumer.start()
        logger.info("✅ Sync Kafka consumer initialized")

    except Exception as e:
        logger.error(
            f"❌ Failed to initialize services for individual account: {str(e)}"
        )
        raise

    container.wire(
        modules=[
            "app.core.celery_app",
            "app.connectors.google.core.sync_tasks",
            "app.connectors.api.router",
            "app.connectors.api.middleware",
            "app.core.signed_url",
        ]
    )

    logger.info("✅ Successfully initialized services for individual account")


async def initialize_enterprise_account_services_fn(org_id, container):
    """Initialize services for an enterprise account type."""

    try:
        logger = container.logger()
        # Initialize base services
        container.drive_service.override(
            providers.Singleton(
                GoogleAdminService,
                logger=logger,
                config=container.config_service,
                rate_limiter=container.rate_limiter,
                google_token_handler=await container.google_token_handler(),
                arango_service=await container.arango_service(),
            )
        )
        container.gmail_service.override(
            providers.Singleton(
                GoogleAdminService,
                logger=logger,
                config=container.config_service,
                rate_limiter=container.rate_limiter,
                google_token_handler=await container.google_token_handler(),
                arango_service=await container.arango_service(),
            )
        )

        # Initialize webhook handlers
        container.drive_webhook_handler.override(
            providers.Singleton(
                EnterpriseDriveWebhookHandler,
                logger=logger,
                config=container.config_service,
                drive_admin_service=container.drive_service(),
                arango_service=await container.arango_service(),
                change_handler=await container.drive_change_handler(),
            )
        )
        drive_webhook_handler = container.drive_webhook_handler()
        assert isinstance(drive_webhook_handler, EnterpriseDriveWebhookHandler)

        container.gmail_webhook_handler.override(
            providers.Singleton(
                EnterpriseGmailWebhookHandler,
                logger=logger,
                config=container.config_service,
                gmail_admin_service=container.gmail_service(),
                arango_service=await container.arango_service(),
                change_handler=await container.gmail_change_handler(),
            )
        )
        gmail_webhook_handler = container.gmail_webhook_handler()
        assert isinstance(gmail_webhook_handler, EnterpriseGmailWebhookHandler)

        # Initialize sync services
        container.drive_sync_service.override(
            providers.Singleton(
                DriveSyncEnterpriseService,
                logger=logger,
                config=container.config_service,
                drive_admin_service=container.drive_service(),
                arango_service=await container.arango_service(),
                change_handler=await container.drive_change_handler(),
                kafka_service=container.kafka_service,
                celery_app=container.celery_app,
            )
        )
        drive_sync_service = container.drive_sync_service()
        assert isinstance(drive_sync_service, DriveSyncEnterpriseService)

        container.gmail_sync_service.override(
            providers.Singleton(
                GmailSyncEnterpriseService,
                logger=logger,
                config=container.config_service,
                gmail_admin_service=container.gmail_service(),
                arango_service=await container.arango_service(),
                change_handler=await container.gmail_change_handler(),
                kafka_service=container.kafka_service,
                celery_app=container.celery_app,
            )
        )
        gmail_sync_service = container.gmail_sync_service()
        assert isinstance(gmail_sync_service, GmailSyncEnterpriseService)

        container.sync_tasks.override(
            providers.Singleton(
                SyncTasks,
                logger=logger,
                celery_app=container.celery_app,
                drive_sync_service=container.drive_sync_service(),
                gmail_sync_service=container.gmail_sync_service(),
                arango_service=await container.arango_service(),
            )
        )
        sync_tasks = container.sync_tasks()
        assert isinstance(sync_tasks, SyncTasks)

        container.google_admin_service.override(
            providers.Singleton(
                GoogleAdminService,
                logger=logger,
                config=container.config_service,
                rate_limiter=container.rate_limiter,
                google_token_handler=await container.google_token_handler(),
                arango_service=await container.arango_service(),
            )
        )
        google_admin_service = container.google_admin_service()
        assert isinstance(google_admin_service, GoogleAdminService)

        await google_admin_service.connect_admin(org_id)
        await google_admin_service.create_admin_watch(org_id)

        container.admin_webhook_handler.override(
            providers.Singleton(
                AdminWebhookHandler, logger=logger, admin_service=google_admin_service
            )
        )
        admin_webhook_handler = container.admin_webhook_handler()
        assert isinstance(admin_webhook_handler, AdminWebhookHandler)

        container.google_docs_parser.override(
            providers.Singleton(
                GoogleDocsParser,
                logger=logger,
                admin_service=container.google_admin_service(),
            )
        )
        google_docs_parser = container.google_docs_parser()
        assert isinstance(google_docs_parser, GoogleDocsParser)

        container.google_sheets_parser.override(
            providers.Singleton(
                GoogleSheetsParser,
                logger=logger,
                admin_service=container.google_admin_service(),
            )
        )
        google_sheets_parser = container.google_sheets_parser()
        assert isinstance(google_sheets_parser, GoogleSheetsParser)

        container.google_slides_parser.override(
            providers.Singleton(
                GoogleSlidesParser,
                logger=logger,
                admin_service=container.google_admin_service(),
            )
        )
        google_slides_parser = container.google_slides_parser()
        assert isinstance(google_slides_parser, GoogleSlidesParser)

        container.sync_kafka_consumer.override(
            providers.Singleton(
                SyncKafkaRouteConsumer,
                logger=logger,
                config_service=container.config_service,
                arango_service=await container.arango_service(),
                sync_tasks=container.sync_tasks(),
            )
        )
        sync_kafka_consumer = container.sync_kafka_consumer()
        assert isinstance(sync_kafka_consumer, SyncKafkaRouteConsumer)

        # Start the sync Kafka consumer
        await sync_kafka_consumer.start()
        logger.info("✅ Sync Kafka consumer initialized")

    except Exception as e:
        logger.error(
            f"❌ Failed to initialize services for enterprise account: {str(e)}"
        )
        raise

    container.wire(
        modules=[
            "app.core.celery_app",
            "app.connectors.api.router",
            "app.connectors.google.core.sync_tasks",
            "app.connectors.api.middleware",
            "app.core.signed_url",
        ]
    )

    logger.info("✅ Successfully initialized services for enterprise account")


class AppContainer(containers.DeclarativeContainer):
    """Dependency injection container for the application."""

    # Initialize logger correctly as a singleton provider
    logger = providers.Singleton(create_logger, "connector_service")

    # Log when container is initialized
    logger().info("🚀 Initializing AppContainer")
    logger().info("🔧 Environment: dev")

    # Core services that don't depend on account type
    config_service = providers.Singleton(ConfigurationService, logger=logger)

    async def _create_arango_client(config_service):
        """Async method to initialize ArangoClient."""
        arangodb_config = await config_service.get_config(
            config_node_constants.ARANGODB.value
        )
        hosts = arangodb_config["url"]
        return ArangoClient(hosts=hosts)

    async def _create_redis_client(config_service):
        """Async method to initialize RedisClient."""
        redis_config = await config_service.get_config(
            config_node_constants.REDIS.value
        )
        url = f"redis://{redis_config['host']}:{redis_config['port']}/{RedisConfig.REDIS_DB.value}"
        return await aioredis.from_url(url, encoding="utf-8", decode_responses=True)

    # Core Resources
    arango_client = providers.Resource(
        _create_arango_client, config_service=config_service
    )
    redis_client = providers.Resource(
        _create_redis_client, config_service=config_service
    )

    # Core Services
    rate_limiter = providers.Singleton(GoogleAPIRateLimiter)
    kafka_service = providers.Singleton(
        KafkaService, logger=logger, config=config_service
    )

    arango_service = providers.Singleton(
        ArangoService,
        logger=logger,
        arango_client=arango_client,
        kafka_service=kafka_service,
        config=config_service,
    )

    google_token_handler = providers.Singleton(
        GoogleTokenHandler,
        logger=logger,
        config_service=config_service,
        arango_service=arango_service,
    )

    # Change Handlers
    drive_change_handler = providers.Singleton(
        DriveChangeHandler,
        logger=logger,
        config_service=config_service,
        arango_service=arango_service,
    )

    gmail_change_handler = providers.Singleton(
        GmailChangeHandler,
        logger=logger,
        config_service=config_service,
        arango_service=arango_service,
    )

    # Celery and Tasks
    celery_app = providers.Singleton(
        CeleryApp, logger=logger, config_service=config_service
    )

    # Signed URL Handler
    signed_url_config = providers.Resource(
        SignedUrlConfig.create, configuration_service=config_service
    )
    signed_url_handler = providers.Singleton(
        SignedUrlHandler,
        logger=logger,
        config=signed_url_config,
        configuration_service=config_service,
    )

    # Services that will be initialized based on account type
    # Define lazy dependencies for account-based services:
    drive_service = providers.Dependency()
    gmail_service = providers.Dependency()
    drive_sync_service = providers.Dependency()
    gmail_sync_service = providers.Dependency()
    drive_webhook_handler = providers.Dependency()
    gmail_webhook_handler = providers.Dependency()
    sync_tasks = providers.Dependency()
    google_admin_service = providers.Dependency()
    admin_webhook_handler = providers.Dependency()

    google_docs_parser = providers.Dependency()
    google_sheets_parser = providers.Dependency()
    google_slides_parser = providers.Dependency()
    parser_user_service = providers.Dependency()
    sync_kafka_consumer = providers.Dependency()
    # Wire everything up
    wiring_config = containers.WiringConfiguration(
        modules=[
            "app.core.celery_app",
            "app.connectors.api.router",
            "app.connectors.google.core.sync_tasks",
            "app.connectors.api.middleware",
            "app.core.signed_url",
        ]
    )


async def health_check_etcd(container):
    """Check the health of etcd via HTTP request."""
    logger = container.logger()
    logger.info("🔍 Starting etcd health check...")
    try:
        etcd_url = os.getenv("ETCD_URL")
        if not etcd_url:
            error_msg = "ETCD_URL environment variable is not set"
            logger.error(f"❌ {error_msg}")
            raise Exception(error_msg)

        logger.debug(f"Checking etcd health at endpoint: {etcd_url}/health")

        async with aiohttp.ClientSession() as session:
            async with session.get(f"{etcd_url}/health") as response:
                if response.status == 200:
                    response_text = await response.text()
                    logger.info("✅ etcd health check passed")
                    logger.debug(f"etcd health response: {response_text}")
                else:
                    error_msg = (
                        f"etcd health check failed with status {response.status}"
                    )
                    logger.error(f"❌ {error_msg}")
                    raise Exception(error_msg)
    except aiohttp.ClientError as e:
        error_msg = f"Connection error during etcd health check: {str(e)}"
        logger.error(f"❌ {error_msg}")
        raise Exception(error_msg)
    except Exception as e:
        error_msg = f"etcd health check failed: {str(e)}"
        logger.error(f"❌ {error_msg}")
        raise


async def health_check_arango(container):
    """Check the health of ArangoDB using ArangoClient."""
    logger = container.logger()
    logger.info("🔍 Starting ArangoDB health check...")
    try:
        # Get the config_service instance first, then call get_config
        config_service = container.config_service()
        arangodb_config = await config_service.get_config(
            config_node_constants.ARANGODB.value
        )
        username = arangodb_config["username"]
        password = arangodb_config["password"]

        logger.debug("Checking ArangoDB connection using ArangoClient")

        # Get the ArangoClient from the container
        client = await container.arango_client()

        # Connect to system database
        sys_db = client.db("_system", username=username, password=password)

        # Check server version to verify connection
        server_version = sys_db.version()
        logger.info("✅ ArangoDB health check passed")
        logger.debug(f"ArangoDB server version: {server_version}")

    except Exception as e:
        error_msg = f"ArangoDB health check failed: {str(e)}"
        logger.error(f"❌ {error_msg}")
        raise Exception(error_msg)


async def health_check_kafka(container):
    """Check the health of Kafka by attempting to create a connection."""
    logger = container.logger()
    logger.info("🔍 Starting Kafka health check...")
    try:
        kafka_config = await container.config_service().get_config(
            config_node_constants.KAFKA.value
        )
        brokers = kafka_config["brokers"]
        logger.debug(f"Checking Kafka connection at: {brokers}")

        # Try to create a consumer with a short timeout
        try:
            config = {
                "bootstrap.servers": ",".join(brokers),
                "group.id": "test",
                "auto.offset.reset": "earliest",
                "enable.auto.commit": True,  # Disable auto-commit for exactly-once semantics
                "isolation.level": "read_committed",  # Ensure we only read committed messages
                "enable.partition.eof": False,
            }
            consumer = Consumer(config)
            # Try to list topics to verify connection
            topics = consumer.list_topics()
            consumer.close()

            logger.info("✅ Kafka health check passed")
            logger.debug(f"Available Kafka topics: {topics}")

        except KafkaError as ke:
            error_msg = f"Failed to connect to Kafka: {str(ke)}"
            logger.error(f"❌ {error_msg}")
            raise Exception(error_msg)

    except Exception as e:
        error_msg = f"Kafka health check failed: {str(e)}"
        logger.error(f"❌ {error_msg}")
        raise


async def health_check_redis(container):
    """Check the health of Redis by attempting to connect and ping."""
    logger = container.logger()
    logger.info("🔍 Starting Redis health check...")
    try:
        config_service = container.config_service()
        redis_config = await config_service.get_config(
            config_node_constants.REDIS.value
        )
        redis_url = f"redis://{redis_config['host']}:{redis_config['port']}/{RedisConfig.REDIS_DB.value}"
        logger.debug(f"Checking Redis connection at: {redis_url}")
        # Create Redis client and attempt to ping
        redis_client = Redis.from_url(redis_url, socket_timeout=5.0)
        try:
            await redis_client.ping()
            logger.info("✅ Redis health check passed")
        except RedisError as re:
            error_msg = f"Failed to connect to Redis: {str(re)}"
            logger.error(f"❌ {error_msg}")
            raise Exception(error_msg)
        finally:
            await redis_client.close()

    except Exception as e:
        error_msg = f"Redis health check failed: {str(e)}"
        logger.error(f"❌ {error_msg}")
        raise


async def health_check_qdrant(container):
    """Check the health of Qdrant via HTTP request."""
    logger = container.logger()
    logger.info("🔍 Starting Qdrant health check...")
    try:
        qdrant_config = await container.config_service().get_config(
            config_node_constants.QDRANT.value
        )
        host = qdrant_config["host"]
        port = qdrant_config["port"]
        api_key = qdrant_config["apiKey"]

        client = QdrantClient(host=host, port=port, api_key=api_key, https=False)
        logger.debug(f"Checking Qdrant health at endpoint: {host}:{port}")
        try:
            # Fetch collections to check connectivity
            client.get_collections()
            logger.info("Qdrant is healthy!")
        except Exception as e:
            error_msg = f"Qdrant health check failed: {str(e)}"
            logger.error(f"❌ {error_msg}")
            raise
    except Exception as e:
        error_msg = f"Qdrant health check failed: {str(e)}"
        logger.error(f"❌ {error_msg}")
        raise


async def health_check(container):
    """Run health checks sequentially using HTTP requests."""
    logger = container.logger()
    logger.info("🏥 Starting health checks for all services...")
    try:
        # Run health checks sequentially
        await health_check_etcd(container)
        logger.info("✅ etcd health check completed")

        await health_check_arango(container)
        logger.info("✅ ArangoDB health check completed")

        await health_check_kafka(container)
        logger.info("✅ Kafka health check completed")

        await health_check_redis(container)
        logger.info("✅ Redis health check completed")

        await health_check_qdrant(container)
        logger.info("✅ Qdrant health check completed")

        logger.info("✅ All health checks completed successfully")
    except Exception as e:
        logger.error(f"❌ One or more health checks failed: {str(e)}")
        raise


async def initialize_container(container) -> bool:
    """Initialize container resources with health checks."""

    logger = container.logger()

    logger.info("🚀 Initializing application resources")
    try:
        logger.info("Running health checks for all services...")
        await health_check(container)

        logger.info("Connecting to ArangoDB")
        arango_service = await container.arango_service()
        if arango_service:
            arango_connected = await arango_service.connect()
            if not arango_connected:
                raise Exception("Failed to connect to ArangoDB")
            logger.info("✅ Connected to ArangoDB")
        else:
            raise Exception("Failed to initialize ArangoDB service")

        logger.info("✅ Container initialization completed successfully")
        return True

    except Exception as e:
        logger.error(f"❌ Container initialization failed: {str(e)}")
        raise
