import asyncio
import os

from arango import ArangoClient

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import CollectionNames
from app.config.providers.in_memory_store import InMemoryKeyValueStore
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.connectors.services.base_arango_service import BaseArangoService
from app.connectors.sources.microsoft.common.apps import OneDriveApp
from app.connectors.sources.microsoft.onedrive.onedrive import (
    OneDriveConnector,
    OneDriveCredentials,
)
from app.services.kafka_consumer import KafkaConsumerManager
from app.utils.logger import create_logger


def is_valid_email(email: str) -> bool:
    return email is not None and email != "" and "@" in email

async def test_run() -> None:
    user_email = os.getenv("TEST_USER_EMAIL")

    async def create_test_users(user_email: str, arango_service: BaseArangoService) -> None:
        org_id = "org_1"
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


    logger = create_logger("onedrive_connector")
    key_value_store = InMemoryKeyValueStore(logger, "app/config/default_config.json")
    config_service = ConfigurationService(logger, key_value_store)
    kafka_service = KafkaConsumerManager(logger, config_service, None, None)
    arango_client = ArangoClient()
    arango_service = BaseArangoService(logger, arango_client, config_service, kafka_service)
    await arango_service.connect()

    if user_email:
        await create_test_users(user_email, arango_service)

    data_entities_processor = DataSourceEntitiesProcessor(logger, OneDriveApp(), arango_service, config_service)
    await data_entities_processor.initialize()
    credentials = OneDriveCredentials(
        tenant_id=os.getenv("AZURE_TENANT_ID"),
        client_id=os.getenv("AZURE_CLIENT_ID"),
        client_secret=os.getenv("AZURE_CLIENT_SECRET"),
    )
    onedrive_connector = OneDriveConnector(logger, data_entities_processor, arango_service, credentials)
    await onedrive_connector.run()

if __name__ == "__main__":
    asyncio.run(test_run())
