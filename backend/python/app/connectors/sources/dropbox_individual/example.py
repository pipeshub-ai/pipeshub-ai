import asyncio
import os
from typing import Optional, Tuple

from arango import ArangoClient

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import CollectionNames
from app.config.providers.in_memory_store import InMemoryKeyValueStore
from app.connectors.core.base.data_store.arango_data_store import ArangoDataStore
from app.connectors.services.base_arango_service import BaseArangoService
from app.connectors.sources.dropbox_individual.connector import (
    DropboxIndividualConnector,
)
from app.services.kafka_consumer import KafkaConsumerManager
from app.utils.logger import create_logger


def _get_env(logger, primary: str, *fallbacks: str, allow_fallback: bool = True) -> Optional[str]:
    """Fetch env var from primary name, otherwise try fallbacks."""
    possible_names = (primary, *(fallbacks if allow_fallback else []))
    for name in possible_names:
        value = os.getenv(name)
        if value:
            value = value.strip()
        if value:
            if name != primary:
                logger.warning(
                    "Using fallback env var %s for %s. "
                    "Consider defining %s to keep environments explicit.",
                    name, primary, primary
                )
            return value
    return None


async def test_run() -> None:
    """
    Initializes and runs the Dropbox Individual connector sync process for testing.
    This is for individual/personal Dropbox accounts, not team accounts.
    """
    org_id = "68d28814cdabcc98a3e02605"

    async def create_test_users(arango_service: BaseArangoService) -> None:
        """
        Set up test organization and user in ArangoDB.
        For individual accounts, we create a single user record.
        """
        org = {
            "_key": org_id,
            "accountType": "individual",
            "name": "Test Individual Dropbox Account",
            "isActive": True,
            "createdAtTimestamp": 1718745600,
            "updatedAtTimestamp": 1718745600,
        }
        await arango_service.batch_upsert_nodes([org], CollectionNames.ORGS.value)

        user_email = "test_user@example.com" # Dummy user for DB record
        user = {
            "_key": user_email,
            "email": user_email,
            "userId": user_email,
            "orgId": org_id,
            "isActive": True,
            "createdAtTimestamp": 1718745600,
            "updatedAtTimestamp": 1718745600,
        }
        await arango_service.batch_upsert_nodes([user], CollectionNames.USERS.value)

        await arango_service.batch_create_edges([{
            "_from": f"{CollectionNames.USERS.value}/{user['_key']}",
            "_to": f"{CollectionNames.ORGS.value}/{org_id}",
        }], CollectionNames.BELONGS_TO.value)

        arango_service.logger.info(f"Test data initialized: Org {org_id} and User {user_email} created.")

    async def setup_services() -> Tuple:
        """
        Initialize all required services (logger, config, database, etc.).
        Returns:
            Tuple of (logger, config_service, data_store_provider, arango_service)
        """
        logger = create_logger("dropbox_individual_connector")
        base_dir = os.path.dirname(os.path.abspath(__file__))
        config_path = os.path.join(base_dir, "../../../config/default_config.json")

        key_value_store = InMemoryKeyValueStore(logger, config_path)
        config_service = ConfigurationService(logger, key_value_store)
        kafka_service = KafkaConsumerManager(logger, config_service, None, None)

        arango_client = ArangoClient()
        arango_service = BaseArangoService(logger, arango_client, config_service, kafka_service)
        await arango_service.connect()

        data_store_provider = ArangoDataStore(logger, arango_service)

        logger.info("Services initialized successfully for Dropbox Individual connector test run.")
        return logger, config_service, data_store_provider, arango_service

    async def configure_dropbox_credentials(key_value_store: InMemoryKeyValueStore, logger) -> bool:
        """
        Set up Dropbox credentials for individual account in the config store.
        Returns:
            bool: True if credentials are configured successfully
        """
        access_token = _get_env(logger, "DROPBOX_PERSONAL_TOKEN", "DROPBOX_TOKEN")
        app_key = _get_env(logger, "DROPBOX_PERSONAL_APP_KEY", "DROPBOX_APP_KEY")
        app_secret = _get_env(logger, "DROPBOX_PERSONAL_APP_SECRET", "DROPBOX_APP_SECRET")
        refresh_token = _get_env(
            logger,
            "DROPBOX_PERSONAL_REFRESH_TOKEN",
            allow_fallback=False
        )

        missing_values = []
        if not access_token:
            missing_values.append("DROPBOX_PERSONAL_TOKEN")
        if not app_key:
            missing_values.append("DROPBOX_PERSONAL_APP_KEY")
        if not app_secret:
            missing_values.append("DROPBOX_PERSONAL_APP_SECRET")

        if missing_values:
            logger.error(
                "Missing required environment variables: %s",
                ", ".join(missing_values),
            )
            return False

        config = {
            "credentials": {
                "access_token": access_token,
            },
            "auth": {
                "clientId": app_key,
                "clientSecret": app_secret,
            },
        }

        if refresh_token:
            config["credentials"]["refresh_token"] = refresh_token

        config_key = "/services/connectors/dropboxpersonal/config"
        await key_value_store.create_key(config_key, config)
        logger.info("Stored Dropbox Individual credentials at %s", config_key)

        stored_config = await key_value_store.get_key(config_key)
        if stored_config:
            logger.debug("Verified stored config keys: %s", list(stored_config.keys()))

        return True

    async def run_connector(logger, data_store_provider, config_service) -> None:
        """
        Create and run the Dropbox Individual connector.
        Handles initialization and sync execution.
        """
        dropbox_connector = await DropboxIndividualConnector.create_connector(
            logger,
            data_store_provider,
            config_service,
        )

        # Temporary workaround: connector expects url_identifier for config path
        if not hasattr(dropbox_connector.app, "url_identifier"):
            dropbox_connector.app.url_identifier = "dropboxpersonal"

        try:
            if await dropbox_connector.init():
                logger.info("Dropbox Individual connector initialized successfully.")
                await dropbox_connector.run_sync()
                logger.info("Dropbox Individual connector sync completed.")
            else:
                logger.error("Dropbox Individual connector initialization failed.")
        finally:
            await dropbox_connector.cleanup()

    # Main execution flow
    try:
        # 1. Initialize services
        logger, config_service, data_store_provider, arango_service = await setup_services()

        # 2. Create test data in database
        await create_test_users(arango_service)

        # 3. Configure Dropbox credentials
        key_value_store = getattr(config_service, "store", None)
        if not key_value_store:
            logger.error("ConfigurationService does not expose an underlying key-value store.")
            return
        credentials_configured = await configure_dropbox_credentials(key_value_store, logger)

        if not credentials_configured:
            logger.error("Failed to configure Dropbox credentials. Exiting.")
            return

        # 4. Run the connector
        await run_connector(logger, data_store_provider, config_service)

    except Exception as e:
        logger.error(f"Fatal error in test run: {e}", exc_info=True)


if __name__ == "__main__":
    # Environment variables required:
    # DROPBOX_PERSONAL_TOKEN - OAuth token for individual Dropbox account
    # DROPBOX_PERSONAL_REFRESH_TOKEN - Refresh token (optional)
    # DROPBOX_PERSONAL_APP_KEY - App key from Dropbox App Console
    # DROPBOX_PERSONAL_APP_SECRET - App secret from Dropbox App Console

    # Example:
    # export DROPBOX_PERSONAL_TOKEN='your-individual-token-here'
    # export DROPBOX_PERSONAL_REFRESH_TOKEN='your-refresh-token'
    # export DROPBOX_PERSONAL_APP_KEY='your-app-key'
    # export DROPBOX_PERSONAL_APP_SECRET='your-app-secret'
    asyncio.run(test_run())
