"""
Google Drive Connector Individual - Test Example

This script demonstrates how to initialize and test the Google Drive Individual connector.

Required Environment Variables:
    TEST_USER_EMAIL: Email address for test user (e.g., test@example.com)
    GOOGLE_CLIENT_ID: Your Google OAuth client ID from Google Cloud Console
    GOOGLE_CLIENT_SECRET: Your Google OAuth client secret from Google Cloud Console
    GOOGLE_ACCESS_TOKEN: (Optional) OAuth access token if you have one
    GOOGLE_REFRESH_TOKEN: (Optional) OAuth refresh token if you have one

Setup Instructions:
    1. Create a Google Cloud Project and enable Google Drive API
    2. Create OAuth 2.0 credentials (Web application)
    3. Set the required environment variables
    4. Run: python -m app.connectors.sources.google.drive.individual.example

Note: If you don't have access_token and refresh_token yet, you'll need to complete
      the OAuth flow first to obtain them.
"""

import asyncio
import os

from arango import ArangoClient

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import CollectionNames
from app.config.providers.in_memory_store import InMemoryKeyValueStore
from app.connectors.core.base.connector.connector_service import BaseConnector
from app.connectors.core.base.data_store.arango_data_store import ArangoDataStore
from app.connectors.services.base_arango_service import BaseArangoService
from app.connectors.sources.google.drive.individual.connector import (
    GoogleDriveConnectorIndividual,
)
from app.services.kafka_consumer import KafkaConsumerManager
from app.utils.logger import create_logger


def is_valid_email(email: str) -> bool:
    return email is not None and email != "" and "@" in email


async def test_run() -> None:
    user_email = os.getenv("TEST_USER_EMAIL")
    
    # Validate that TEST_USER_EMAIL is set
    if not user_email or not is_valid_email(user_email):
        raise ValueError(
            "TEST_USER_EMAIL environment variable must be set to a valid email address. "
            "Example: export TEST_USER_EMAIL='test@example.com'"
        )
    
    org_id = "org_1"

    async def create_test_users(user_email: str, arango_service: BaseArangoService) -> None:
        org = {
            "_key": org_id,
            "accountType": "enterprise",
            "name": "Test Org",
            "isActive": True,
            "createdAtTimestamp": 1718745600,
            "updatedAtTimestamp": 1718745600,
        }

        await arango_service.batch_upsert_nodes([org], CollectionNames.ORGS.value)
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
            "entityType": "ORGANIZATION",
            "createdAtTimestamp": 1718745600,
            "updatedAtTimestamp": 1718745600,
        }], CollectionNames.BELONGS_TO.value)

    logger = create_logger("google_drive_connector")
    key_value_store = InMemoryKeyValueStore(logger, "app/config/default_config.json")
    config_service = ConfigurationService(logger, key_value_store)
    kafka_service = KafkaConsumerManager(logger, config_service, None, None)
    arango_client = ArangoClient()
    arango_service = BaseArangoService(logger, arango_client, config_service, kafka_service)
    await arango_service.connect()
    data_store_provider = ArangoDataStore(logger, arango_service)
    
    # Create test users BEFORE initializing the connector
    # This ensures the organization exists when DataSourceEntitiesProcessor.initialize() runs
    await create_test_users(user_email, arango_service)

    # Google OAuth config structure
    # Note: For testing, you need to provide:
    # - GOOGLE_CLIENT_ID: Your Google OAuth client ID
    # - GOOGLE_CLIENT_SECRET: Your Google OAuth client secret
    # - GOOGLE_ACCESS_TOKEN: (Optional) OAuth access token if you have one
    # - GOOGLE_REFRESH_TOKEN: (Optional) OAuth refresh token if you have one
    config = {
        "auth": {
            "clientId": os.getenv("GOOGLE_CLIENT_ID"),
            "clientSecret": os.getenv("GOOGLE_CLIENT_SECRET"),
            "authorizeUrl": "https://accounts.google.com/o/oauth2/v2/auth",
            "tokenUrl": "https://oauth2.googleapis.com/token",
            "redirectUri": "connectors/oauth/callback/Google Drive",
            "scopes": [
                "https://www.googleapis.com/auth/drive.readonly",
                # "https://www.googleapis.com/auth/drive.metadata.readonly",
                # "https://www.googleapis.com/auth/drive.metadata",
                # "https://www.googleapis.com/auth/documents.readonly",
                # "https://www.googleapis.com/auth/spreadsheets.readonly",
                # "https://www.googleapis.com/auth/presentations.readonly",
                # "https://www.googleapis.com/auth/drive.file",
                # "https://www.googleapis.com/auth/drive",
            ],
        },
        "credentials": {
            "access_token": os.getenv("GOOGLE_ACCESS_TOKEN"),
            "refresh_token": os.getenv("GOOGLE_REFRESH_TOKEN"),
            "token_type": "Bearer",
            "expires_in": 3600,
        }
    }

    await key_value_store.create_key("/services/connectors/google_drive/config", config)
    connector: BaseConnector = await GoogleDriveConnectorIndividual.create_connector(
        logger, data_store_provider, config_service, "google_drive"
    )
    await connector.init()
    
    # Note: run_sync() is not implemented yet, so this will raise NotImplementedError
    # This is expected behavior as per the implementation requirements
    try:
        await connector.run_sync()
    except NotImplementedError as e:
        logger.info(f"run_sync() not implemented yet: {e}")
        logger.info("✅ Connector initialization completed successfully. run_sync() implementation pending.")


if __name__ == "__main__":
    asyncio.run(test_run())