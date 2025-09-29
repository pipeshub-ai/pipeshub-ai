import asyncio
import os
from typing import Optional

import uvicorn
from arango import ArangoClient
from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.responses import RedirectResponse, Response

from app.config.configuration_service import ConfigurationService
from app.config.providers.in_memory_store import InMemoryKeyValueStore
from app.connectors.core.base.connector.connector_service import BaseConnector
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.connectors.services.base_arango_service import BaseArangoService
from app.connectors.sources.atlassian.confluence_cloud.connector import (
    ConfluenceConnector,
)
from app.connectors.sources.atlassian.core.oauth import OAUTH_CONFIG_PATH
from app.services.kafka_consumer import KafkaConsumerManager
from app.utils.logger import create_logger

app = FastAPI()

async def test_run() -> None:
    logger = create_logger("confluence_connector")

    key_value_store = InMemoryKeyValueStore(logger, "app/config/default_config.json")

    config_service = ConfigurationService(logger, key_value_store)
    kafka_service = KafkaConsumerManager(logger, config_service, None, None)

    arango_service = BaseArangoService(logger, ArangoClient(), config_service, kafka_service)
    await arango_service.connect()
    data_entities_processor = DataSourceEntitiesProcessor(logger, arango_service, config_service)
    await data_entities_processor.initialize()
    await key_value_store.create_key(f"{OAUTH_CONFIG_PATH}/{data_entities_processor.org_id}", {
        "client_id":os.getenv("ATLASSIAN_CLIENT_ID"),
        "client_secret": os.getenv("ATLASSIAN_CLIENT_SECRET"),
        "redirect_uri": os.getenv("ATLASSIAN_REDIRECT_URI")
    })
    connector: BaseConnector = ConfluenceConnector(logger, data_entities_processor, config_service)
    await connector.initialize()


    app.connector = connector

router = APIRouter()

@router.get("/oauth/atlassian/start")
async def oauth_start(return_to: Optional[str] = None) -> RedirectResponse:
    url = await app.connector.provider.start_authorization(return_to=return_to, use_pkce=True)
    return RedirectResponse(url)

@router.get("/oauth/atlassian/callback")
async def oauth_callback(request: Request) -> RedirectResponse:
    error = request.query_params.get("error")
    if error:
        raise HTTPException(400, detail=request.query_params.get("error_description", error))
    code = request.query_params.get("code")
    state = request.query_params.get("state")
    if not code or not state:
        raise HTTPException(400, detail="Missing code/state")
    await app.connector.provider.handle_callback(code, state)
    await app.connector.run_sync()

    # Optionally pull saved return_to from state store before deletion,
    # or stash it in a short-lived cookie at /start.
    return RedirectResponse(url="http://localhost:3001")

@router.get("/api/v1/org/{org_id}/page/{page_id}/fetch")
async def get_page(org_id: str, page_id: str) -> Response:
    confluence_client = await app.connector.get_confluence_client(org_id)
    page_content = await confluence_client.fetch_page_content(page_id)
    return Response(content=page_content, media_type="text/html")

app.include_router(router)

@app.on_event("startup")
async def startup_event() -> None:
    asyncio.create_task(test_run())


if __name__ == "__main__":
    # asyncio.run(test_run())
    uvicorn.run(app, host="0.0.0.0", port=8088)

