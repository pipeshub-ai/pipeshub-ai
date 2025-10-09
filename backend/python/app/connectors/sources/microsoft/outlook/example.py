import asyncio

import httpx
import uvicorn
from arango import ArangoClient
from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.responses import Response

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import CollectionNames
from app.config.constants.http_status_code import HttpStatusCode
from app.config.providers.in_memory_store import InMemoryKeyValueStore
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.connectors.core.base.data_store.arango_data_store import ArangoDataStore
from app.connectors.services.base_arango_service import BaseArangoService
from app.connectors.sources.microsoft.outlook.connector import (
    OutlookConnector,
)
from app.services.kafka_consumer import KafkaConsumerManager
from app.utils.logger import create_logger

app = FastAPI()
router = APIRouter()


def is_valid_email(email: str) -> bool:
    return email is not None and email != "" and "@" in email

async def test_run() -> None:
    user_email = "TEST_USER_EMAIL"

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


    logger = create_logger("outlook_connector")
    key_value_store = InMemoryKeyValueStore(logger, "app/config/default_config.json")
    config_service = ConfigurationService(logger, key_value_store)
    kafka_service = KafkaConsumerManager(logger, config_service, None, None)
    arango_client = ArangoClient()
    arango_service = BaseArangoService(logger, arango_client, config_service, kafka_service)
    try:
        await arango_service.connect()
        print("✅ Connected to ArangoDB")

        # Debug: Check if database connection is properly established
        if arango_service.db is None:
            print("❌ ERROR: Database connection is None after connect()")
            return
        else:
            print(f"✅ Database connection established: {arango_service.db}")
    except Exception as e:
        print(f"❌ ERROR connecting to ArangoDB: {e}")
        return

    data_store_provider = ArangoDataStore(logger, arango_service)
    if user_email:
        await create_test_users(user_email, arango_service)

    config = {
        "auth" : {
            "tenantId": "AZURE_TENANT_ID",
            "clientId": "AZURE_CLIENT_ID",
            "clientSecret": "AZURE_CLIENT_SECRET",
            "hasAdminConsent": True,
        },
    }
    await key_value_store.create_key("/services/connectors/outlook/config", config)

    # Create data processor for session-based access
    data_entities_processor = DataSourceEntitiesProcessor(logger, data_store_provider, config_service)
    await data_entities_processor.initialize()

    outlook_connector = await OutlookConnector.create_connector(logger, data_store_provider, config_service)
    app.connector = outlook_connector
    await outlook_connector.init()
    await outlook_connector.run_sync()

# Session-based endpoints
@router.get("/api/v1/org/{org_id}/outlook/record/{record_id}")
async def get_outlook_record(org_id: str, record_id: str) -> Response:
    """Get Outlook record content directly"""
    try:
        # Use connector's arango_service method
        arango_service = app.connector.data_store_provider.arango_service
        record = await arango_service.get_record_by_id(record_id)

        if not record:
            raise HTTPException(404, detail="Record not found")
        return await app.connector.stream_record(record)

    except Exception as e:
        raise HTTPException(500, detail=f"Failed to stream record: {str(e)}")

# Test endpoint to verify session-based access
@router.get("/test/outlook/session/{org_id}/{record_id}")
async def test_session_access(org_id: str, record_id: str) -> dict:
    """Test session-based access to Outlook records"""
    try:
        # Test the session-based endpoint
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"http://localhost:8089/api/v1/org/{org_id}/outlook/record/{record_id}"
            )

            return {
                "status": response.status_code,
                "content_type": response.headers.get('content-type'),
                "content_size": len(response.content),
                "success": response.status_code == HttpStatusCode.SUCCESS.value,
                "test_url": f"http://localhost:8089/api/v1/org/{org_id}/outlook/record/{record_id}"
            }

    except Exception as e:
        print(f"Error in test_session_access: {str(e)}")
        return {
            "error": "Failed to test session access",
            "success": False
        }

app.include_router(router)

@app.on_event("startup")
async def startup_event() -> None:
    asyncio.create_task(test_run())

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8089)
