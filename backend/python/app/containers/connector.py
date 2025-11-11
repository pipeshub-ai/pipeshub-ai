import asyncio
from datetime import datetime, timedelta, timezone

import google.oauth2.credentials
from dependency_injector import containers, providers
from google.oauth2 import service_account

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import AppGroups
from app.config.providers.etcd.etcd3_encrypted_store import Etcd3EncryptedKeyValueStore
from app.connectors.services.base_arango_service import BaseArangoService
from app.connectors.services.kafka_service import KafkaService
from app.connectors.sources.google.admin.admin_webhook_handler import (
    AdminWebhookHandler,
)
from app.connectors.sources.google.admin.google_admin_service import GoogleAdminService
from app.connectors.sources.google.common.google_token_handler import GoogleTokenHandler
from app.connectors.sources.google.common.scopes import (
    GOOGLE_CONNECTOR_ENTERPRISE_SCOPES,
)
from app.connectors.sources.google.gmail.gmail_change_handler import GmailChangeHandler
from app.connectors.sources.google.gmail.gmail_sync_service import (
    GmailSyncEnterpriseService,
    GmailSyncIndividualService,
)
from app.connectors.sources.google.gmail.gmail_user_service import GmailUserService
from app.connectors.sources.google.gmail.gmail_webhook_handler import (
    EnterpriseGmailWebhookHandler,
    IndividualGmailWebhookHandler,
)
from app.connectors.sources.google.gmail.services.sync_service.sync_tasks import (
    GmailSyncTasks,
)
from app.connectors.sources.google.google_drive.drive_change_handler import (
    DriveChangeHandler,
)
from app.connectors.sources.google.google_drive.drive_sync_service import (
    DriveSyncEnterpriseService,
    DriveSyncIndividualService,
)
from app.connectors.sources.google.google_drive.drive_user_service import (
    DriveUserService,
)
from app.connectors.sources.google.google_drive.drive_webhook_handler import (
    EnterpriseDriveWebhookHandler,
    IndividualDriveWebhookHandler,
)
from app.connectors.sources.google.google_drive.services.sync_service.sync_tasks import (
    DriveSyncTasks,
)
from app.connectors.sources.localKB.handlers.kb_service import KnowledgeBaseService
from app.connectors.sources.localKB.handlers.migration_service import run_kb_migration
from app.connectors.utils.rate_limiter import GoogleAPIRateLimiter
from app.containers.container import BaseAppContainer
from app.containers.utils.utils import ContainerUtils
from app.core.celery_app import CeleryApp
from app.core.signed_url import SignedUrlConfig, SignedUrlHandler
from app.health.health import Health
from app.modules.parsers.google_files.google_docs_parser import GoogleDocsParser
from app.modules.parsers.google_files.google_sheets_parser import GoogleSheetsParser
from app.modules.parsers.google_files.google_slides_parser import GoogleSlidesParser
from app.modules.parsers.google_files.parser_user_service import ParserUserService
from app.utils.logger import create_logger


async def initialize_individual_google_account_services_fn(org_id, container, app_names: list[str]) -> None:
    """Initialize services for an individual account type."""
    try:
        logger = container.logger()
        arango_service = await container.arango_service()

        if "drive" in app_names:
            await initialize_individual_drive_account_services_fn(org_id, container)

        if "gmail" in app_names:
            await initialize_individual_gmail_account_services_fn(org_id, container)

        container.parser_user_service.override(
            providers.Singleton(
                ParserUserService,
                logger=logger,
                config_service=container.config_service,
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

        # Pre-fetch service account credentials for this org
        org_apps = await arango_service.get_org_apps(org_id)
        for app in org_apps:
            if app["appGroup"].replace(" ", "").lower() == AppGroups.GOOGLE_WORKSPACE.value.replace(" ", "").lower():
                logger.info("Refreshing Google Workspace user credentials")
                if "drive" == app["name"].lower() and "drive" in app_names:
                    asyncio.create_task(refresh_google_workspace_user_credentials(org_id, arango_service,logger, container,app_name="drive"))
                elif "gmail" == app["name"].lower() and "gmail" in app_names:
                    asyncio.create_task(refresh_google_workspace_user_credentials(org_id, arango_service,logger, container,app_name="gmail"))
                break

    except Exception as e:
        logger.error(
            f"❌ Failed to initialize services for individual account: {str(e)}"
        )
        raise

    container.wire(
        modules=[
            "app.core.celery_app",
            "app.connectors.sources.google.common.sync_tasks",
            "app.connectors.api.router",
            "app.connectors.api.middleware",
            "app.core.signed_url",
        ]
    )

    logger.info("✅ Successfully initialized services for individual account")

async def initialize_individual_drive_account_services_fn(org_id, container) -> None:
    """Initialize services for an drive individual account type."""
    try:
        logger = container.logger()
        arango_service = await container.arango_service()

        # Initialize base services
        container.drive_service.override(
            providers.Singleton(
                DriveUserService,
                logger=logger,
                config_service=container.config_service,
                rate_limiter=container.rate_limiter,
                google_token_handler=await container.google_token_handler(),
            )
        )
        drive_service = container.drive_service()
        assert isinstance(drive_service, DriveUserService)

        # Initialize webhook handlers
        container.drive_webhook_handler.override(
            providers.Singleton(
                IndividualDriveWebhookHandler,
                logger=logger,
                config_service=container.config_service,
                drive_user_service=container.drive_service(),
                arango_service=await container.arango_service(),
                change_handler=await container.drive_change_handler(),
            )
        )
        drive_webhook_handler = container.drive_webhook_handler()
        assert isinstance(drive_webhook_handler, IndividualDriveWebhookHandler)

        # Initialize sync services
        container.drive_sync_service.override(
            providers.Singleton(
                DriveSyncIndividualService,
                logger=logger,
                config_service=container.config_service,
                drive_user_service=container.drive_service(),
                arango_service=await container.arango_service(),
                change_handler=await container.drive_change_handler(),
                kafka_service=container.kafka_service,
                celery_app=container.celery_app,
            )
        )
        drive_sync_service = container.drive_sync_service()
        assert isinstance(drive_sync_service, DriveSyncIndividualService)

        drive_sync_task = DriveSyncTasks(
            logger=logger,
            celery_app=container.celery_app,
            arango_service=await container.arango_service(),
        )
        drive_sync_task.register_drive_sync_service(drive_sync_service)

        # Store sync tasks in container for later access
        if not hasattr(container, 'sync_tasks_registry'):
            container.sync_tasks_registry = {}

        container.sync_tasks_registry['drive'] = drive_sync_task

        # Pre-fetch service account credentials for this org
        org_apps = await arango_service.get_org_apps(org_id)
        for app in org_apps:
            if app["appGroup"].replace(" ", "").lower() == AppGroups.GOOGLE_WORKSPACE.value.replace(" ", "").lower():
                logger.info("Refreshing Google Workspace user credentials")
                asyncio.create_task(refresh_google_workspace_user_credentials(org_id, arango_service,logger, container,app_name="drive"))
                break

    except Exception as e:
        logger.error(
            f"❌ Failed to initialize services for drive individual account: {str(e)}"
        )
        raise

    container.wire(
        modules=[
            "app.core.celery_app",
            "app.connectors.sources.google.common.sync_tasks",
            "app.connectors.api.router",
            "app.connectors.api.middleware",
            "app.core.signed_url",
        ]
    )

    logger.info("✅ Successfully initialized services for drive individual account")

async def initialize_individual_gmail_account_services_fn(org_id, container) -> None:
    """Initialize services for an gmail individual account type."""
    try:
        logger = container.logger()
        arango_service = await container.arango_service()

        container.gmail_service.override(
            providers.Singleton(
                GmailUserService,
                logger=logger,
                config_service=container.config_service,
                rate_limiter=container.rate_limiter,
                google_token_handler=await container.google_token_handler(),
            )
        )
        gmail_service = container.gmail_service()
        assert isinstance(gmail_service, GmailUserService)

        container.gmail_webhook_handler.override(
            providers.Singleton(
                IndividualGmailWebhookHandler,
                logger=logger,
                config_service=container.config_service,
                gmail_user_service=container.gmail_service(),
                arango_service=await container.arango_service(),
                change_handler=await container.gmail_change_handler(),
            )
        )
        gmail_webhook_handler = container.gmail_webhook_handler()
        assert isinstance(gmail_webhook_handler, IndividualGmailWebhookHandler)


        container.gmail_sync_service.override(
            providers.Singleton(
                GmailSyncIndividualService,
                logger=logger,
                config_service=container.config_service,
                gmail_user_service=container.gmail_service(),
                arango_service=await container.arango_service(),
                change_handler=await container.gmail_change_handler(),
                kafka_service=container.kafka_service,
                celery_app=container.celery_app,
            )
        )
        gmail_sync_service = container.gmail_sync_service()
        assert isinstance(gmail_sync_service, GmailSyncIndividualService)

        gmail_sync_task = GmailSyncTasks(
            logger=logger,
            celery_app=container.celery_app,
            arango_service=await container.arango_service(),
        )
        gmail_sync_task.register_gmail_sync_service(gmail_sync_service)


        # Store sync tasks in container for later access
        if not hasattr(container, 'sync_tasks_registry'):
            container.sync_tasks_registry = {}

        container.sync_tasks_registry['gmail'] = gmail_sync_task

        # Pre-fetch service account credentials for this org
        org_apps = await arango_service.get_org_apps(org_id)
        for app in org_apps:
            if app["appGroup"].replace(" ", "").lower() == AppGroups.GOOGLE_WORKSPACE.value.replace(" ", "").lower():
                logger.info("Refreshing Google Workspace gmail user credentials")
                asyncio.create_task(refresh_google_workspace_user_credentials(org_id, arango_service,logger, container,app_name="gmail"))
                break

    except Exception as e:
        logger.error(
            f"❌ Failed to initialize services for gmail individual account: {str(e)}"
        )
        raise

    container.wire(
        modules=[
            "app.core.celery_app",
            "app.connectors.sources.google.common.sync_tasks",
            "app.connectors.api.router",
            "app.connectors.api.middleware",
            "app.core.signed_url",
        ]
    )

    logger.info("✅ Successfully initialized services for gmail individual account")


async def initialize_enterprise_google_account_services_fn(org_id, container, app_names: list[str] = ["drive", "gmail"]) -> None:
    """Initialize services for an enterprise account type."""

    try:
        logger = container.logger()
        arango_service = await container.arango_service()

        if "drive" in app_names:
            await initialize_enterprise_drive_account_services_fn(org_id, container)

        if "gmail" in app_names:
            await initialize_enterprise_gmail_account_services_fn(org_id, container)

        container.google_admin_service.override(
            providers.Singleton(
                GoogleAdminService,
                logger=logger,
                config_service=container.config_service,
                rate_limiter=container.rate_limiter,
                google_token_handler=await container.google_token_handler(),
                arango_service=await container.arango_service(),
            )
        )
        google_admin_service = container.google_admin_service()
        assert isinstance(google_admin_service, GoogleAdminService)

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

        # Initialize service credentials cache if not exists
        if not hasattr(container, 'service_creds_cache'):
            container.service_creds_cache = {}
            logger.info("Created service credentials cache")

        # Pre-fetch service account credentials for this org
        org_apps = await arango_service.get_org_apps(org_id)
        for app in org_apps:
            if app["appGroup"].replace(" ", "").lower() == AppGroups.GOOGLE_WORKSPACE.value.replace(" ", "").lower():
                logger.info("Caching Google Workspace service credentials")
                if "drive" == app["name"].lower() and "drive" in app_names:
                    await cache_google_workspace_service_credentials(org_id, arango_service, logger, container,app_name="drive")
                    await google_admin_service.connect_admin(org_id, app_name=app['name'])
                    await google_admin_service.create_admin_watch(org_id, app_name=app['name'])
                elif "gmail" == app["name"].lower() and "gmail" in app_names:
                    await cache_google_workspace_service_credentials(org_id, arango_service, logger, container,app_name="gmail")
                    await google_admin_service.connect_admin(org_id, app_name=app['name'])
                    await google_admin_service.create_admin_watch(org_id, app_name=app['name'])

                logger.info("✅ Google Workspace service credentials cached")
                # Initialize admin service with a generic app context (Drive by default)
                break

    except Exception as e:
        logger.error(
            f"❌ Failed to initialize services for enterprise google account: {str(e)}"
        )
        raise

    container.wire(
        modules=[
            "app.core.celery_app",
            "app.connectors.api.router",
            "app.connectors.sources.google.common.sync_tasks",
            "app.connectors.api.middleware",
            "app.core.signed_url",
        ]
    )

    logger.info("✅ Successfully initialized services for enterprise google account")

async def initialize_enterprise_drive_account_services_fn(org_id, container) -> None:
    """Initialize services for an enterprise drive account type."""

    try:
        logger = container.logger()

        # Initialize base services
        container.drive_service.override(
            providers.Singleton(
                GoogleAdminService,
                logger=logger,
                config_service=container.config_service,
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
                config_service=container.config_service,
                drive_admin_service=container.drive_service(),
                arango_service=await container.arango_service(),
                change_handler=await container.drive_change_handler(),
            )
        )
        drive_webhook_handler = container.drive_webhook_handler()
        assert isinstance(drive_webhook_handler, EnterpriseDriveWebhookHandler)



        # Initialize sync services
        container.drive_sync_service.override(
            providers.Singleton(
                DriveSyncEnterpriseService,
                logger=logger,
                config_service=container.config_service,
                drive_admin_service=container.drive_service(),
                arango_service=await container.arango_service(),
                change_handler=await container.drive_change_handler(),
                kafka_service=container.kafka_service,
                celery_app=container.celery_app,
            )
        )
        drive_sync_service = container.drive_sync_service()
        assert isinstance(drive_sync_service, DriveSyncEnterpriseService)

        drive_sync_task = DriveSyncTasks(
            logger=logger,
            celery_app=container.celery_app,
            arango_service=await container.arango_service(),
        )
        drive_sync_task.register_drive_sync_service(drive_sync_service)

        # Store sync tasks in container for later access
        if not hasattr(container, 'sync_tasks_registry'):
            container.sync_tasks_registry = {}

        container.sync_tasks_registry['drive'] = drive_sync_task

    except Exception as e:
        logger.error(
            f"❌ Failed to initialize services for enterprise drive account: {str(e)}"
        )
        raise

    container.wire(
        modules=[
            "app.core.celery_app",
            "app.connectors.api.router",
            "app.connectors.sources.google.common.sync_tasks",
            "app.connectors.api.middleware",
            "app.core.signed_url",
        ]
    )

    logger.info("✅ Successfully initialized services for enterprise drive account")

async def initialize_enterprise_gmail_account_services_fn(org_id, container) -> None:
    """Initialize services for an enterprise gmail account type."""

    try:
        logger = container.logger()

        container.gmail_service.override(
            providers.Singleton(
                GoogleAdminService,
                logger=logger,
                config_service=container.config_service,
                rate_limiter=container.rate_limiter,
                google_token_handler=await container.google_token_handler(),
                arango_service=await container.arango_service(),
            )
        )


        container.gmail_webhook_handler.override(
            providers.Singleton(
                EnterpriseGmailWebhookHandler,
                logger=logger,
                config_service=container.config_service,
                gmail_admin_service=container.gmail_service(),
                arango_service=await container.arango_service(),
                change_handler=await container.gmail_change_handler(),
            )
        )
        gmail_webhook_handler = container.gmail_webhook_handler()
        assert isinstance(gmail_webhook_handler, EnterpriseGmailWebhookHandler)

        container.gmail_sync_service.override(
            providers.Singleton(
                GmailSyncEnterpriseService,
                logger=logger,
                config_service=container.config_service,
                gmail_admin_service=container.gmail_service(),
                arango_service=await container.arango_service(),
                change_handler=await container.gmail_change_handler(),
                kafka_service=container.kafka_service,
                celery_app=container.celery_app,
            )
        )
        gmail_sync_service = container.gmail_sync_service()
        assert isinstance(gmail_sync_service, GmailSyncEnterpriseService)

        gmail_sync_task = GmailSyncTasks(
            logger=logger,
            celery_app=container.celery_app,
            arango_service=await container.arango_service(),
        )

        gmail_sync_task.register_gmail_sync_service(gmail_sync_service)


        # Store sync tasks in container for later access
        if not hasattr(container, 'sync_tasks_registry'):
            container.sync_tasks_registry = {}

        container.sync_tasks_registry['gmail'] = gmail_sync_task


    except Exception as e:
        logger.error(
            f"❌ Failed to initialize services for enterprise gmail account: {str(e)}"
        )
        raise

    container.wire(
        modules=[
            "app.core.celery_app",
            "app.connectors.api.router",
            "app.connectors.sources.google.common.sync_tasks",
            "app.connectors.api.middleware",
            "app.core.signed_url",
        ]
    )

    logger.info("✅ Successfully initialized services for enterprise gmail account")

async def cache_google_workspace_service_credentials(org_id, arango_service, logger, container,app_name: str) -> None:
    """Get Google Workspace service credentials for an organization."""
    try:
        google_token_handler = await container.google_token_handler()
        users = await arango_service.get_users(org_id)
        service_creds_lock = container.service_creds_lock()

        for user in users:
            user_id = user["userId"]
            try:
                cache_key = f"{org_id}_{user_id}"

                async with service_creds_lock:
                    # Check if credentials are already cached
                    if not hasattr(container, 'service_creds_cache'):
                        container.service_creds_cache = {}

                    if cache_key in container.service_creds_cache:
                        logger.info(f"Service account cache hit: {cache_key}. Skipping cache")
                        continue

                    # Fetch and cache credentials
                    SCOPES = GOOGLE_CONNECTOR_ENTERPRISE_SCOPES
                    credentials_json = await google_token_handler.get_enterprise_token(org_id,app_name=app_name)
                    credentials = service_account.Credentials.from_service_account_info(
                        credentials_json, scopes=SCOPES
                    )
                    credentials = credentials.with_subject(user["email"])

                    container.service_creds_cache[cache_key] = credentials
                    logger.info(f"Cached service credentials for {cache_key}")

            except Exception as e:
                logger.error(f"Failed to cache credentials for user {user_id} in org {org_id}: {str(e)}")

        logger.info("✅ Service credentials cache initialized for org")

    except Exception as e:
        logger.error(f"Error initializing service credentials cache: {str(e)}")
        raise

async def refresh_google_workspace_user_credentials(org_id, arango_service, logger, container,app_name: str) -> None:
    """Background task to refresh user credentials before they expire"""
    logger.debug("🔄 Checking refresh status of credentials for user")
    user_creds_lock = container.user_creds_lock()

    while True:
        try:
            async with user_creds_lock:
                if not hasattr(container, 'user_creds_cache'):
                    container.user_creds_cache = {}
                    logger.info("Created user credentials cache")

            users = await arango_service.get_users(org_id)
            user = users[0]
            user_id = user["userId"]
            cache_key = f"{org_id}_{user_id}"
            logger.info(f"User credentials cache key: {cache_key}")

            needs_refresh = True
            async with user_creds_lock:
                if cache_key in container.user_creds_cache:
                    creds = container.user_creds_cache[cache_key]
                    logger.info(f"Expiry time: {creds.expiry}")
                    expiry = creds.expiry

                    try:
                        now = datetime.now(timezone.utc).replace(tzinfo=None)
                        # Add 5 minute buffer before expiry to ensure we refresh early
                        buffer_time = timedelta(minutes=10)

                        if expiry and (expiry - buffer_time) > now:
                            logger.info(f"User credentials cache hit: {cache_key}")
                            needs_refresh = False
                        else:
                            logger.info(f"User credentials expired or expiring soon for {cache_key}")
                            # Remove expired credentials from cache
                            container.user_creds_cache.pop(cache_key, None)
                    except Exception as e:
                        logger.error(f"Failed to check credentials for {cache_key}: {str(e)}")
                        container.user_creds_cache.pop(cache_key, None)

            if needs_refresh:
                logger.info(f"User credentials cache miss: {cache_key}. Creating new credentials.")
                google_token_handler = await container.google_token_handler()
                SCOPES = await google_token_handler.get_account_scopes(app_name=app_name)

                # Refresh token
                # Refresh Gmail tokens (primary for individual flows)
                await google_token_handler.refresh_token(org_id, user_id, app_name=app_name)
                creds_data = await google_token_handler.get_individual_token(org_id, user_id,app_name=app_name)

                if not creds_data.get("access_token"):
                    raise Exception("Invalid credentials. Access token not found")

                new_creds = google.oauth2.credentials.Credentials(
                    token=creds_data.get("access_token"),
                    refresh_token=creds_data.get("refresh_token"),
                    token_uri="https://oauth2.googleapis.com/token",
                    client_id=creds_data.get("clientId"),
                    client_secret=creds_data.get("clientSecret"),
                    scopes=SCOPES,
                )
                # Update token expiry time
                new_creds.expiry = datetime.fromtimestamp(
                    creds_data.get("access_token_expiry_time", 0) / 1000, timezone.utc
                ).replace(tzinfo=None)  # Convert to naive UTC for Google client compatibility

                async with user_creds_lock:
                    container.user_creds_cache[cache_key] = new_creds
                    logger.info(f"Refreshed credentials for {cache_key}")

        except Exception as e:
            logger.error(f"Error in credential refresh task: {str(e)}")

        # Run every 5 minutes
        await asyncio.sleep(300)
        logger.debug("🔄 Checking refresh status of credentials for user")


class ConnectorAppContainer(BaseAppContainer):
    """Dependency injection container for the connector application."""

    # Override logger with service-specific name
    logger = providers.Singleton(create_logger, "connector_service")
    container_utils = ContainerUtils()
    key_value_store = providers.Singleton(Etcd3EncryptedKeyValueStore, logger=logger)

    # Override config_service to use the service-specific logger
    config_service = providers.Singleton(ConfigurationService, logger=logger, key_value_store=key_value_store)

    # Override arango_client and redis_client to use the service-specific config_service
    arango_client = providers.Resource(
        BaseAppContainer._create_arango_client, config_service=config_service
    )
    redis_client = providers.Resource(
        BaseAppContainer._create_redis_client, config_service=config_service
    )

    # Core Services
    rate_limiter = providers.Singleton(GoogleAPIRateLimiter)
    kafka_service = providers.Singleton(
        KafkaService, logger=logger, config_service=config_service
    )

    # First create an async factory for the connected BaseArangoService
    @staticmethod
    async def _create_arango_service(logger, arango_client, kafka_service, config_service) -> BaseArangoService:
        """Async factory to create and connect BaseArangoService"""
        service = BaseArangoService(logger, arango_client, config_service, kafka_service)
        await service.connect()
        return service


    arango_service = providers.Resource(
        _create_arango_service,
        logger=logger,
        arango_client=arango_client,
        kafka_service=kafka_service,
        config_service=config_service,
    )

    # First create an async factory for the connected BaseArangoService
    @staticmethod
    async def _create_kb_arango_service(logger, arango_client, kafka_service, config_service) -> BaseArangoService:
        """Async factory to create and connect BaseArangoService"""
        service = BaseArangoService(logger, arango_client, config_service, kafka_service)
        await service.connect()
        return service

    kb_arango_service = providers.Resource(
        _create_kb_arango_service,
        logger=logger,
        arango_client=arango_client,
        kafka_service=kafka_service,
        config_service=config_service,
    )

    kb_service = providers.Singleton(
        KnowledgeBaseService,
        logger= logger,
        arango_service= kb_arango_service,
        kafka_service= kafka_service
    )

    google_token_handler = providers.Singleton(
        GoogleTokenHandler,
        logger=logger,
        config_service=config_service,
        arango_service=arango_service,
        key_value_store=key_value_store
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
        SignedUrlConfig.create, config_service=config_service
    )
    signed_url_handler = providers.Singleton(
        SignedUrlHandler,
        logger=logger,
        config=signed_url_config,
        config_service=config_service,
    )

    feature_flag_service = providers.Singleton(container_utils.create_feature_flag_service, config_service=config_service)

    # Services that will be initialized based on account type
    # Define lazy dependencies for account-based services:
    drive_service = providers.Dependency()
    gmail_service = providers.Dependency()
    drive_sync_service = providers.Dependency()
    gmail_sync_service = providers.Dependency()
    drive_webhook_handler = providers.Dependency()
    gmail_webhook_handler = providers.Dependency()
    google_admin_service = providers.Dependency()
    admin_webhook_handler = providers.Dependency()

    google_docs_parser = providers.Dependency()
    google_sheets_parser = providers.Dependency()
    google_slides_parser = providers.Dependency()
    parser_user_service = providers.Dependency()

    # Connector-specific wiring configuration
    wiring_config = containers.WiringConfiguration(
        modules=[
            "app.core.celery_app",
            "app.connectors.api.router",
            "app.connectors.sources.localKB.api.kb_router",
            "app.connectors.api.middleware",
            "app.core.signed_url",
        ]
    )


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

    logger.info("🚀 Initializing application resources")
    try:
        await Health.system_health_check(container)

        logger.info("Connecting to ArangoDB")
        arango_service = await container.arango_service()
        if arango_service:
            arango_connected = await arango_service.connect()
            if not arango_connected:
                raise Exception("Failed to connect to ArangoDB")
            logger.info("✅ Connected to ArangoDB")
        else:
            raise Exception("Failed to initialize ArangoDB service")

        logger.info("Connecting to ArangoDB (KnowledgeBase)")
        kb_arango_service = await container.kb_arango_service()
        if kb_arango_service:
            kb_arango_connected = await kb_arango_service.connect()
            if not kb_arango_connected:
                raise Exception("Failed to connect to ArangoDB (KnowledgeBase)")
            logger.info("✅ Connected to ArangoDB (KnowledgeBase)")
        else:
            raise Exception("Failed to initialize ArangoDB service (KnowledgeBase)")

        logger.info("✅ Container initialization completed successfully")

        logger.info("🔄 Running Knowledge Base migration...")
        migration_success = await run_knowledge_base_migration(container)
        if not migration_success:
            logger.warning("⚠️ Knowledge Base migration had issues but continuing initialization")

        return True

    except Exception as e:
        logger.error(f"❌ Container initialization failed: {str(e)}")
        raise

