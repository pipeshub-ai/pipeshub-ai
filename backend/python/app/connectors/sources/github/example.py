"""
Test file for YourConnector.
Run this to test your connector implementation before integrating with the full system.
"""

import asyncio
import os
import sys
from pathlib import Path
from typing import Optional, Tuple

from arango import ArangoClient

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent.parent))

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import CollectionNames
from app.config.providers.in_memory_store import InMemoryKeyValueStore
from app.connectors.core.base.data_store.arango_data_store import ArangoDataStore
from app.connectors.services.base_arango_service import BaseArangoService
from app.connectors.sources.github.connector import GithubConnector
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

async def test_run()->None:
    """
    Initializes and runs the Github Individual connector sync process for testing.
    This is for individual/personal Github accounts, not team accounts.
    """
    org_id = (os.getenv("GITHUB_TEST_ORG_ID") or "68d28814cdabcc98a3e02605").strip()

    async def create_test_users(arango_service: BaseArangoService) -> None:
        """
        Set up test organization and user in ArangoDB.
        For individual accounts, we create a single user record.
        """
        org = {
            "_key": org_id,
            "accountType": "individual",
            "name": "Test Individual Github Account",
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
        logger = create_logger("github_individual_connector")
        base_dir = os.path.dirname(os.path.abspath(__file__))
        config_path = os.path.join(base_dir, "../../../config/default_config.json")

        key_value_store = InMemoryKeyValueStore(logger, config_path)
        config_service = ConfigurationService(logger, key_value_store)
        kafka_service = KafkaConsumerManager(logger, config_service, None, None)

        arango_client = ArangoClient()
        arango_service = BaseArangoService(logger, arango_client, config_service, kafka_service)
        await arango_service.connect()

        data_store_provider = ArangoDataStore(logger, arango_service)

        logger.info("Services initialized successfully for Github Individual connector test run.")
        return logger, config_service, data_store_provider, arango_service

    async def configure_github_credentials(key_value_store: InMemoryKeyValueStore, logger) -> bool:
        """
        Set up Github credentials for individual account in the config store.
        Returns:
            bool: True if credentials are configured successfully
        """
        access_token = _get_env(logger, "GITHUB_PERSONAL_ACCESS_TOKEN", "GITHUB_ACCESS_TOKEN")
        app_key = _get_env(logger, "GITHUB_PERSONAL_APP_KEY", "GITHUB_APP_KEY")
        app_secret = _get_env(logger, "GITHUB_PERSONAL_APP_SECRET", "GITHUB_APP_SECRET")

        missing_values = []
        if not access_token:
            missing_values.append("GITHUB_PERSONAL_ACCESS_TOKEN")
        if not app_key:
            missing_values.append("GITHUB_PERSONAL_APP_KEY")
        if not app_secret:
            missing_values.append("GITHUB_PERSONAL_APP_SECRET")

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

        config_key = "/services/connectors/github/config"
        await key_value_store.create_key(config_key, config)
        logger.info("Stored Github Individual credentials at %s", config_key)

        stored_config = await key_value_store.get_key(config_key)
        if stored_config:
            logger.debug("Verified stored config keys: %s", list(stored_config.keys()))

        return True

    async def run_connector(logger, data_store_provider, config_service) -> None:
        """
        Create and run the Github Individual connector.
        Handles initialization and sync execution.
        """
        github_connector = await GithubConnector.create_connector(
            logger,
            data_store_provider,
            config_service,
            "github"
        )

        # Temporary workaround: connector expects url_identifier for config path
        if not hasattr(github_connector.app, "url_identifier"):
            github_connector.app.url_identifier = "github"

        try:
            if await github_connector.init():
                logger.info("Github Individual connector initialized successfully.")
                await github_connector.run_sync()
                logger.info("Github Individual connector sync completed.")
            else:
                logger.error("Github Individual connector initialization failed.")
        finally:
            await github_connector.cleanup()
    # Main execution flow
    try:
        # 1. Initialize services
        logger, config_service, data_store_provider, arango_service = await setup_services()

        # 2. Create test data in database
        await create_test_users(arango_service)

        # 3. Configure Github credentials
        key_value_store = getattr(config_service, "store", None)
        if not key_value_store:
            logger.error("ConfigurationService does not expose an underlying key-value store.")
            return
        credentials_configured = await configure_github_credentials(key_value_store, logger)

        if not credentials_configured:
            logger.error("Failed to configure Github credentials. Exiting.")
            return

        # 4. Run the connector
        await run_connector(logger, data_store_provider, config_service)

    except Exception as e:
        logger.error(f"Fatal error in test run: {e}", exc_info=True)


if __name__ == "__main__":
    # Environment variables required:
    # GITHUB_PERSONAL_TOKEN - OAuth token for individual Github account
    # GITHUB_PERSONAL_APP_KEY - App key from Github App Console
    # GITHUB_PERSONAL_APP_SECRET - App secret from Github App Console
    # Example:
    # export GITHUB_PERSONAL_TOKEN='your-individual-token-here'
    # export GITHUB_PERSONAL_APP_KEY='your-app-key'
    # export GITHUB_PERSONAL_APP_SECRET='your-app-secret'
    asyncio.run(test_run())
