
import asyncio
import json
import os
from dataclasses import dataclass
from logging import Logger
from typing import Any, Dict, List, Optional

import aiohttp
import uvicorn
from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.responses import RedirectResponse

from app.config.configuration_service import ConfigurationService
from app.config.providers.in_memory_store import InMemoryKeyValueStore
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.connectors.core.base.token_service.oauth_service import OAuthToken
from app.connectors.services.base_arango_service import BaseArangoService
from app.connectors.sources.atlassian.core.apps import ConfluenceApp
from app.connectors.sources.atlassian.core.oauth import (
    AtlassianOAuthProvider,
    AtlassianScope,
)
from app.models.entities import User
from app.services.kafka_consumer import KafkaConsumerManager
from app.utils.logger import create_logger

app = FastAPI()

RESOURCE_URL = "https://api.atlassian.com/oauth/token/accessible-resources"
BASE_URL = "https://api.atlassian.com/ex/confluence"

@dataclass
class AtlassianCloudResource:
    """Represents an Atlassian Cloud resource (site)"""
    id: str
    name: str
    url: str
    scopes: List[str]
    avatar_url: Optional[str] = None

class ConfluenceClient:
    def __init__(self, logger: Logger, user: User, token: OAuthToken):
        self.logger = logger
        self.user = user
        self.token = token
        self.base_url = "https://api.atlassian.com/ex/confluence"
        self.headers = {
            "Authorization": f"Bearer {self.token.access_token}",
        }
        self.session = aiohttp.ClientSession()
        self.accessible_resources = None
        self.cloud_id = None

    async def _ensure_session(self):
        """Ensure session is created and available"""
        if self.session is None:
            self.session = aiohttp.ClientSession()
        return self.session

    async def close(self):
        """Close the session"""
        if self.session:
            await self.session.close()
            self.session = None

    async def __aenter__(self):
        """Async context manager entry"""
        await self._ensure_session()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit"""
        await self.close()

    async def initialize(self):
        await self._ensure_session()
        self.accessible_resources = await self.get_accessible_resources()
        if self.accessible_resources:
            self.cloud_id = self.accessible_resources[0].id
        else:
            raise Exception("No accessible resources found")

    async def make_authenticated_json_request(
        self,
        method: str,
        url: str,
        **kwargs
    ) -> Dict[str, Any]:
        """Make authenticated API request and return JSON response"""
        token = self.token

        headers = kwargs.pop("headers", {})
        headers["Authorization"] = f"{token.token_type} {token.access_token}"

        session = await self._ensure_session()
        async with session.request(method, url, headers=headers, **kwargs) as response:
            response.raise_for_status()
            return await response.json()

    async def make_authenticated_request(
        self,
        method: str,
        url: str,
        **kwargs
    ) -> aiohttp.ClientResponse:
        """Make authenticated API request"""
        token = self.token

        headers = kwargs.pop("headers", {})
        headers["Authorization"] = f"{token.token_type} {token.access_token}"

        session = await self._ensure_session()

        # For streaming responses, return the response object
        # Caller is responsible for reading the response
        return await session.request(method, url, headers=headers, **kwargs)

    async def get_accessible_resources(self) -> List[AtlassianCloudResource]:
        """
        Get list of Atlassian sites (Confluence/Jira instances) accessible to the user
        Args:
            None
        Returns:
            List of accessible Atlassian Cloud resources
        """

        response = await self.make_authenticated_json_request(
            "GET",
            RESOURCE_URL
        )

        return [
            AtlassianCloudResource(
                id=resource["id"],
                name=resource.get("name", ""),
                url=resource["url"],
                scopes=resource.get("scopes", []),
                avatar_url=resource.get("avatarUrl")
            )
            for resource in response
        ]


    async def fetch_spaces_with_permissions(
        self,
    ) -> Dict[str, Any]:
        """
        Get all Confluence spaces
        Args:
            None
        Returns:
            List of Confluence spaces with permissions
        """
        base_url = f"{BASE_URL}/{self.cloud_id}"
        spaces_url = f"{base_url}/wiki/api/v2/spaces"
        spaces = []
        while True:
            spaces_batch = await self.make_authenticated_json_request("GET", spaces_url)
            spaces = spaces + spaces_batch.get("results", [])
            next_url = spaces_batch.get("_links", {}).get("next", None)
            if not next_url:
                break
            spaces_url = f"{base_url}{next_url}"



        for space in spaces:
            space_permissions = await self._fetch_space_permission(space["id"])
            space["permissions"] = space_permissions

        print(json.dumps(spaces, indent=4), "spaces")

        return spaces

    async def _fetch_space_permission(
        self,
        space_id: str,
    ) -> Dict[str, Any]:
        permissions = []
        base_url = f"{BASE_URL}/{self.cloud_id}"
        url = f"{base_url}/wiki/api/v2/spaces/{space_id}/permissions"
        while True:
            permissions_batch = await self.make_authenticated_json_request("GET", url)
            print(json.dumps(permissions_batch, indent=4), "permissions_batch")
            permissions = permissions + permissions_batch.get("results", [])
            next_url = permissions_batch.get("_links", {}).get("next", None)
            if not next_url:
                break
            url = f"{base_url}/{next_url}"

        print(json.dumps(permissions, indent=4), "permissions")
        return permissions


    async def fetch_pages_with_permissions(
        self,
        space_id: str,
    ) -> Dict[str, Any]:
        base_url = f"{BASE_URL}/{self.cloud_id}"
        limit = 25
        pages_url = f"{base_url}/wiki/api/v2/spaces/{space_id}/pages"
        pages = []
        while True:
            pages_batch = await self.make_authenticated_json_request("GET", pages_url, params={"limit": limit})
            pages = pages + pages_batch.get("results", [])
            next_url = pages_batch.get("_links", {}).get("next", None)
            if not next_url:
                break
            pages_url = f"{base_url}/{next_url}"

        print(json.dumps(pages, indent=4), "pages")
        return pages


class ConfluenceConnector:
    def __init__(self, logger: Logger, data_entities_processor: DataSourceEntitiesProcessor, config_service: ConfigurationService):
        self.logger = logger
        self.data_entities_processor = data_entities_processor
        self.config_service = config_service


    async def initialize(self):
        await self.data_entities_processor.initialize()

    async def run(self):
        config = await self.config_service.get_config("atlassian_oauth_provider")
        self.provider = AtlassianOAuthProvider(
            client_id=config["client_id"],
            client_secret=config["client_secret"],
            redirect_uri=config["redirect_uri"],
            scopes=AtlassianScope.get_full_access(),
            key_value_store=self.config_service.store
        )


        users = await self.data_entities_processor.get_all_active_users()
        # users = await self.data_entities_processor.get_all_active_users_by_app(ConfluenceApp())

        for user in users:
            print(user, "user")
            async with await self.get_confluence_client(user) as confluence_client:
                try:
                    spaces = await confluence_client.fetch_spaces_with_permissions()
                    for space in spaces:
                        pages = await confluence_client.fetch_pages_with_permissions(space["id"])
                        print(json.dumps(pages, indent=4), "pages")
                    print(json.dumps(spaces, indent=4), "spaces")
                except Exception as e:
                    self.logger.error(f"Error processing user {user.email}: {e}")


    async def get_confluence_client(self, user: User) -> ConfluenceClient:
        token = await self.provider.get_token(user.email)
        if not token:
            raise Exception(f"Token for user {user.email} not found")
        confluence_client = ConfluenceClient(self.logger, user, token)
        await confluence_client.initialize()

        return confluence_client




async def test_run() -> None:
    logger = create_logger("confluence_connector")
    key_value_store = InMemoryKeyValueStore(logger, "app/config/default_config.json")
    config_service = ConfigurationService(logger, key_value_store)
    from arango import ArangoClient
    kafka_service = KafkaConsumerManager(logger, config_service, None, None)

    arango_service = BaseArangoService(logger, ArangoClient(), config_service, kafka_service)
    await arango_service.connect()
    data_entities_processor = DataSourceEntitiesProcessor(logger, ConfluenceApp(), arango_service, config_service)
    await data_entities_processor.initialize()
    confluence_connector = ConfluenceConnector(logger, data_entities_processor, config_service)

    logger = create_logger("atlassian_oauth_provider")
    await key_value_store.create_key("atlassian_oauth_provider", {
        "client_id":os.getenv("ATLASSIAN_CLIENT_ID"),
        "client_secret": os.getenv("ATLASSIAN_CLIENT_SECRET"),
        "redirect_uri": os.getenv("ATLASSIAN_REDIRECT_URI")
    })

    config = await config_service.get_config("atlassian_oauth_provider")
    provider = AtlassianOAuthProvider(
        client_id=config["client_id"],
        client_secret=config["client_secret"],
        redirect_uri=config["redirect_uri"],
        scopes=AtlassianScope.get_full_access(),
        key_value_store=key_value_store
    )
    app.provider = provider
    app.connector = confluence_connector

router = APIRouter(prefix="/oauth")

@router.get("/atlassian/start")
async def oauth_start(return_to: Optional[str] = None):
    url = await app.provider.start_authorization(return_to=return_to, use_pkce=True)
    return RedirectResponse(url)

@router.get("/atlassian/callback")
async def oauth_callback(request: Request):
    error = request.query_params.get("error")
    if error:
        raise HTTPException(400, detail=request.query_params.get("error_description", error))
    code = request.query_params.get("code")
    state = request.query_params.get("state")
    if not code or not state:
        raise HTTPException(400, detail="Missing code/state")
    token = await app.provider.handle_callback(code, state)
    await app.connector.run()

    print(token, "token")
    # Optionally pull saved return_to from state store before deletion,
    # or stash it in a short-lived cookie at /start.
    return RedirectResponse(url="http://localhost:3001")

app.include_router(router)

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(test_run())


if __name__ == "__main__":
    # asyncio.run(test_run())
    uvicorn.run(app, host="0.0.0.0", port=8088)


