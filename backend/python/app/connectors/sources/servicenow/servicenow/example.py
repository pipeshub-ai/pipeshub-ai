import asyncio
import os

from arango import ArangoClient

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import CollectionNames
from app.config.providers.in_memory_store import InMemoryKeyValueStore
from app.connectors.core.base.connector.connector_service import BaseConnector
from app.connectors.core.base.data_store.arango_data_store import ArangoDataStore
from app.connectors.services.base_arango_service import BaseArangoService
from app.connectors.sources.servicenow.servicenow.connector import (
    ServiceNowConnector,
)
from app.services.kafka_consumer import KafkaConsumerManager
from app.utils.logger import create_logger


async def test_run() -> None:
    user_email = os.getenv("TEST_USER_EMAIL")
    org_id = "org_id"

    async def create_test_users(
        user_email: str, arango_service: BaseArangoService
    ) -> None:
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
        await arango_service.batch_create_edges(
            [
                {
                    "_from": f"{CollectionNames.USERS.value}/{user['_key']}",
                    "_to": f"{CollectionNames.ORGS.value}/{org_id}",
                    "entityType": "ORGANIZATION",
                    "createdAtTimestamp": 1718745600,
                    "updatedAtTimestamp": 1718745600,
                }
            ],
            CollectionNames.BELONGS_TO.value,
        )

    logger = create_logger("servicenow_connector")
    key_value_store = InMemoryKeyValueStore(logger, "app/config/default_config.json")
    config_service = ConfigurationService(logger, key_value_store)
    kafka_service = KafkaConsumerManager(logger, config_service, None, None)
    arango_client = ArangoClient()
    arango_service = BaseArangoService(
        logger, arango_client, config_service, kafka_service
    )
    await arango_service.connect()
    data_store_provider = ArangoDataStore(logger, arango_service)
    if user_email:
        await create_test_users(user_email, arango_service)

    config = {
        "auth": {
            "instanceUrl": os.getenv("SERVICENOW_INSTANCE_URL"),
            "authorizeUrl": os.getenv("SERVICENOW_AUTHORIZE_URL"),
            "tokenUrl": os.getenv("SERVICENOW_TOKEN_URL"),
            "clientId": os.getenv("SERVICENOW_CLIENT_ID"),
            "clientSecret": os.getenv("SERVICENOW_CLIENT_SECRET"),
            "redirectUri": "http://localhost/connectors/oauth/callback/ServiceNow",
        },
        "credentials": {
            "access_token": os.getenv("SERVICENOW_ACCESS_TOKEN"),
            "refresh_token": os.getenv("SERVICENOW_REFRESH_TOKEN"),
        }
    }
    await key_value_store.create_key("/services/connectors/servicenow/config", config)
    connector: BaseConnector = await ServiceNowConnector.create_connector(
        logger, data_store_provider, config_service
    )
    await connector.init()
    await connector.run_sync()


if __name__ == "__main__":
    asyncio.run(test_run())
