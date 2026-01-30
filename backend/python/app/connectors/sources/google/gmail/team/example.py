"""
Google Gmail Connector Enterprise - Test Example

This script demonstrates how to initialize and test the Google Gmail Enterprise connector.

Required Environment Variables:
    TEST_USER_EMAIL: Email address for test user (e.g., test@example.com)
    GOOGLE_SERVICE_ACCOUNT_FILE: Path to service account JSON file
    GOOGLE_SERVICE_ACCOUNT_JSON: (Alternative) Service account JSON as string
    GOOGLE_ADMIN_EMAIL: Admin email for domain-wide delegation (e.g., admin@example.com)

Setup Instructions:
    1. Create a Google Cloud Project and enable Gmail API and Admin SDK API
    2. Create a service account and download the JSON key file
    3. Enable domain-wide delegation for the service account
    4. Grant required scopes to the service account in Google Workspace Admin Console
    5. Set the required environment variables
    6. Run: python -m app.connectors.sources.google.gmail.team.example

Note: The service account JSON file must include an 'adminEmail' field for domain-wide delegation.
      You can add it manually to the JSON or it will be read from GOOGLE_ADMIN_EMAIL environment variable.
"""

import asyncio
import json
import os
import time

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import CollectionNames
from app.config.providers.in_memory_store import InMemoryKeyValueStore
from app.connectors.core.base.connector.connector_service import BaseConnector
from app.connectors.core.base.data_store.graph_data_store import GraphDataStore
from app.connectors.sources.google.gmail.team.connector import (
    GoogleGmailTeamConnector,
)
from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
from app.utils.logger import create_logger


def is_valid_email(email: str) -> bool:
    return email is not None and email != "" and "@" in email


def load_service_account_credentials() -> dict:
    """
    Load service account credentials from file or environment variable.
    Adds adminEmail field if not present.

    Returns:
        dict: Service account credentials JSON with adminEmail field
    """
    service_account_info = None

    # Try to load from file path
    service_account_file = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE")
    if service_account_file:
        with open(service_account_file, 'r') as f:
            service_account_info = json.load(f)

    # Try to load from JSON string environment variable
    if not service_account_info:
        service_account_json = os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON")
        if service_account_json:
            service_account_info = json.loads(service_account_json)

    if not service_account_info:
        raise ValueError(
            "GOOGLE_SERVICE_ACCOUNT_FILE or GOOGLE_SERVICE_ACCOUNT_JSON environment variable must be set. "
            "Example: export GOOGLE_SERVICE_ACCOUNT_FILE='/path/to/service-account.json'"
        )

    # Add adminEmail if not present (read from environment variable)
    admin_email = os.getenv("GOOGLE_ADMIN_EMAIL")
    if not admin_email:
        raise ValueError(
            "GOOGLE_ADMIN_EMAIL environment variable must be set. "
            "Example: export GOOGLE_ADMIN_EMAIL='admin@example.com'"
        )

    if "adminEmail" not in service_account_info:
        service_account_info["adminEmail"] = admin_email

    return service_account_info


async def test_run() -> None:
    user_email = os.getenv("TEST_USER_EMAIL")

    # Validate that TEST_USER_EMAIL is set
    if not user_email or not is_valid_email(user_email):
        raise ValueError(
            "TEST_USER_EMAIL environment variable must be set to a valid email address. "
            "Example: export TEST_USER_EMAIL='test@example.com'"
        )

    org_id = "org_1"

    async def create_test_users(user_email: str, graph_provider: ArangoHTTPProvider) -> None:
        org = {
            "id": org_id,
            "accountType": "enterprise",
            "name": "Test Org",
            "isActive": True,
            "createdAtTimestamp": 1718745600,
            "updatedAtTimestamp": 1718745600,
        }

        await graph_provider.batch_upsert_nodes([org], CollectionNames.ORGS.value)
        user = {
            "id": user_email,
            "email": user_email,
            "userId": user_email,
            "orgId": org_id,
            "isActive": True,
            "createdAtTimestamp": 1718745600,
            "updatedAtTimestamp": 1718745600,
        }

        await graph_provider.batch_upsert_nodes([user], CollectionNames.USERS.value)
        await graph_provider.batch_create_edges([{
            "from_id": user_email,
            "from_collection": CollectionNames.USERS.value,
            "to_id": org_id,
            "to_collection": CollectionNames.ORGS.value,
            "entityType": "ORGANIZATION",
            "createdAtTimestamp": 1718745600,
            "updatedAtTimestamp": 1718745600,
        }], CollectionNames.BELONGS_TO.value)

    logger = create_logger("google_gmail_enterprise_connector")
    key_value_store = InMemoryKeyValueStore(logger, "app/config/default_config.json")
    config_service = ConfigurationService(logger, key_value_store)

    # Initialize ArangoHTTPProvider
    graph_provider = ArangoHTTPProvider(logger, config_service)
    await graph_provider.connect()

    # NOTE: The GraphDataStore transaction system includes ALL collections from CollectionNames
    # (including ticketRelations, even though Gmail doesn't use tickets). This is a design
    # limitation - transactions are overly broad. In production, all collections should be
    # initialized during database setup. For this example, we create missing collections.

    # Get the HTTP client from the provider
    http_client = graph_provider.http_client
    if not http_client:
        raise Exception("Failed to get HTTP client from graph provider")

    # Create ticketRelations collection if it doesn't exist
    # The create_collection method handles the case where it already exists
    try:
        logger.info(f"Ensuring collection exists: {CollectionNames.TICKET_RELATIONS.value}")
        created = await http_client.create_collection(
            CollectionNames.TICKET_RELATIONS.value,
            edge=True
        )
        if created:
            logger.info(f"✅ Created collection: {CollectionNames.TICKET_RELATIONS.value}")
        else:
            logger.info(f"✅ Collection already exists: {CollectionNames.TICKET_RELATIONS.value}")
    except Exception as e:
        logger.warning(f"⚠️ Error creating ticketRelations collection: {e}")
        # Continue anyway - the collection might exist or the error might be non-critical

    data_store_provider = GraphDataStore(logger, graph_provider)

    # Create test users BEFORE initializing the connector
    # This ensures the organization exists when DataSourceEntitiesProcessor.initialize() runs
    await create_test_users(user_email, graph_provider)

    # Create app document in database if it doesn't exist
    # Note: In production, this is created by ConnectorRegistry when creating connector instances via API.
    # Since we're bypassing the API flow in this example, we need to create it manually.
    # This is only required because run_sync() calls _sync_users() which calls batch_upsert_app_users()
    # that requires the app document to exist.
    connector_id = "google_gmail_enterprise"
    existing_app = await graph_provider.get_document(connector_id, CollectionNames.APPS.value)

    if not existing_app:
        current_timestamp = int(time.time() * 1000)
        app_document = {
            "id": connector_id,
            "name": "Gmail Workspace",
            "type": "Gmail Workspace",
            "appGroup": "Google Workspace",
            "authType": "OAUTH_ADMIN_CONSENT",
            "scope": "team",
            "isActive": True,
            "isAgentActive": False,
            "isConfigured": False,
            "isAuthenticated": False,
            "createdBy": None,
            "updatedBy": None,
            "createdAtTimestamp": current_timestamp,
            "updatedAtTimestamp": current_timestamp,
        }
        await graph_provider.batch_upsert_nodes([app_document], CollectionNames.APPS.value)

        # Verify the app document was created
        verify_app = await graph_provider.get_document(connector_id, CollectionNames.APPS.value)
        if not verify_app:
            raise Exception(f"Failed to verify app document creation for {connector_id}")
        logger.info(f"✅ Verified app document exists: {verify_app.get('id')}")

        # Create edge between organization and app
        # Use generic format (from_id, from_collection, to_id, to_collection)
        # Note: basic_edge_schema only allows _from, _to, and createdAtTimestamp (no additionalProperties)
        await graph_provider.batch_create_edges([{
            "from_id": org_id,
            "from_collection": CollectionNames.ORGS.value,
            "to_id": connector_id,
            "to_collection": CollectionNames.APPS.value,
            "createdAtTimestamp": current_timestamp,
        }], CollectionNames.ORG_APP_RELATION.value)

        logger.info(f"✅ Created app document and org-app relation for {connector_id}")
    else:
        logger.info(f"✅ App document for {connector_id} already exists")

    # Load service account credentials
    try:
        service_account_credentials = load_service_account_credentials()
        logger.info(f"✅ Loaded service account credentials for admin: {service_account_credentials.get('adminEmail')}")
    except Exception as e:
        logger.error(f"❌ Failed to load service account credentials: {e}")
        raise

    # Google Enterprise config structure
    # Note: For testing, you need to provide:
    # - GOOGLE_SERVICE_ACCOUNT_FILE: Path to service account JSON file
    # - GOOGLE_ADMIN_EMAIL: Admin email for domain-wide delegation
    #
    # Important: GoogleClient.build_from_services expects service account credentials
    # to be stored under the 'auth' field, not 'credentials'
    config = {
        "auth": service_account_credentials
    }
    await key_value_store.create_key(f"/services/connectors/{connector_id}/config", config)

    connector: BaseConnector = await GoogleGmailTeamConnector.create_connector(
        logger, data_store_provider, config_service, connector_id
    )

    try:
        if await connector.init():
            logger.info("✅ Google Gmail Enterprise connector initialized successfully")

            # Run sync
            logger.info("🚀 Starting sync...")
            await connector.run_sync()
            logger.info("✅ Sync completed successfully")
        else:
            logger.error("❌ Google Gmail Enterprise connector initialization failed")
    except Exception as e:
        logger.error(f"❌ Error during connector operation: {e}", exc_info=True)
        raise
    finally:
        # Cleanup
        await connector.cleanup()
        await graph_provider.disconnect()
        logger.info("✅ Cleanup completed")


if __name__ == "__main__":
    asyncio.run(test_run())
