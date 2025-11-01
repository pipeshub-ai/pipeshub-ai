import uuid
from dataclasses import dataclass
from datetime import datetime
from logging import Logger
from typing import Any, Dict, List, Optional

import aiohttp
from fastapi.responses import StreamingResponse

from app.config.configuration_service import ConfigurationService
from app.config.constants.arangodb import (
    Connectors,
    MimeTypes,
    OriginTypes,
    RecordTypes,
)
from app.connectors.core.base.connector.connector_service import BaseConnector
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.connectors.core.base.data_store.data_store import DataStoreProvider
from app.connectors.core.registry.connector_builder import (
    AuthField,
    ConnectorBuilder,
    DocumentationLink,
)
from app.connectors.sources.atlassian.core.apps import ConfluenceApp
from app.connectors.sources.atlassian.core.oauth import (
    OAUTH_CONFLUENCE_CONFIG_PATH,
    AtlassianScope,
)
from app.models.entities import (
    AppUser,
    Record,
    RecordGroupType,
    RecordType,
    WebpageRecord,
)
from app.models.permission import EntityType, Permission, PermissionType

RESOURCE_URL = "https://api.atlassian.com/oauth/token/accessible-resources"
BASE_URL = "https://api.atlassian.com/ex/confluence"
AUTHORIZE_URL = "https://auth.atlassian.com/authorize"
TOKEN_URL = "https://auth.atlassian.com/oauth/token"

@dataclass
class AtlassianCloudResource:
    """Represents an Atlassian Cloud resource (site)"""
    id: str
    name: str
    url: str
    scopes: List[str]
    avatar_url: Optional[str] = None

class ConfluenceClient:
    def __init__(self, logger: Logger, config_service: ConfigurationService) -> None:
        self.logger = logger
        self.config_service = config_service
        self.base_url = "https://api.atlassian.com/ex/confluence"
        self.session = aiohttp.ClientSession()
        self.accessible_resources = None
        self.cloud_id = None

    async def _ensure_session(self) -> aiohttp.ClientSession:
        """Ensure session is created and available"""
        if self.session is None:
            self.session = aiohttp.ClientSession()
        return self.session

    async def close(self) -> None:
        """Close the session"""
        if self.session:
            await self.session.close()
            self.session = None

    async def __aenter__(self) -> "ConfluenceClient":
        """Async context manager entry"""
        await self._ensure_session()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Async context manager exit"""
        await self.close()

    async def initialize(self) -> None:
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
        config = await self.config_service.get_config(f"{OAUTH_CONFLUENCE_CONFIG_PATH}")
        token = None
        if not config:
            self.logger.error("❌ Confluence credentials not found")
            raise ValueError("Confluence credentials not found")
        credentials_config = config.get("credentials", {})

        if not credentials_config:
            self.logger.error("❌ Confluence credentials not found")
            raise ValueError("Confluence credentials not found")

        token = {
            "token_type": credentials_config.get("token_type"),
            "access_token": credentials_config.get("access_token")
        }

        headers = kwargs.pop("headers", {})
        headers["Authorization"] = f"{token['token_type']} {token['access_token']}"

        session = await self._ensure_session()
        async with session.request(method, url, headers=headers, **kwargs) as response:
            response.raise_for_status()
            return await response.json()

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
            permissions = permissions + permissions_batch.get("results", [])
            next_url = permissions_batch.get("_links", {}).get("next", None)
            if not next_url:
                break
            url = f"{base_url}/{next_url}"

        return permissions

    async def fetch_page_content(
        self,
        page_id: str,
    ) -> Dict[str, Any]:
        base_url = f"{BASE_URL}/{self.cloud_id}"
        url = f"{base_url}/wiki/api/v2/pages/{page_id}"
        page_details = await self.make_authenticated_json_request("GET", url, params={"body-format": "storage"})
        html_content = page_details.get("body", {}).get("storage", {}).get("value", "")
        title = page_details.get("title", "")

        html = f"""
            <!DOCTYPE html>
            <html>
            <head>
                <title>{title}</title>
                <meta charset="UTF-8">
            </head>
            <body>
                {html_content}
            </body>
            </html>
        """

        return html

    async def _fetch_page_details(
        self,
        page_id: str,
    ) -> Dict[str, Any]:
        base_url = f"{BASE_URL}/{self.cloud_id}"
        url = f"{base_url}/wiki/api/v2/pages/{page_id}"
        return await self.make_authenticated_json_request("GET", url)

    async def fetch_pages_with_permissions(
        self,
        space_id: str,
        users: List[AppUser],
    ) -> List[WebpageRecord]:
        base_url = f"{BASE_URL}/{self.cloud_id}"
        limit = 25
        pages_url = f"{base_url}/wiki/api/v2/spaces/{space_id}/pages"
        records = []

        permissions = []

        for user in users:
            permissions.append(Permission(
                email=user.email,
                entity_type=EntityType.USER,
                type=PermissionType.READ
            ))
        while True:
            pages_batch = await self.make_authenticated_json_request("GET", pages_url, params={"limit": limit})
            for page in pages_batch.get("results", []):
                # page_permissions = await self._fetch_page_permission(page["id"])
                # page["permissions"] = page_permissions

                page_details = await self._fetch_page_details(page["id"])

                created_at = datetime.strptime(page["createdAt"], "%Y-%m-%dT%H:%M:%S.%fZ")
                modified_at = datetime.strptime(page_details["version"]["createdAt"], "%Y-%m-%dT%H:%M:%S.%fZ")
                page_link = page_details.get("_links")
                web_url = ""
                if page_link:
                    web_url = page_link.get("base", "") + page_link.get("webui", "")

                record_id=str(uuid.uuid4())
                record = WebpageRecord(
                    id=record_id,
                    external_record_id=page["id"],
                    external_revision_id=str(page_details["version"]["number"]),
                    version=0,
                    record_name=page["title"],
                    record_type=RecordTypes.WEBPAGE,
                    origin=OriginTypes.CONNECTOR,
                    connector_name=Connectors.CONFLUENCE,
                    record_group_type=RecordGroupType.CONFLUENCE_SPACES,
                    external_record_group_id=space_id,
                    parent_record_type=RecordType.WEBPAGE,
                    parent_external_record_id=page.get('parentId'),
                    weburl=web_url,
                    mime_type=MimeTypes.HTML.value,
                    source_created_at=int(created_at.timestamp() * 1000),
                    source_modified_at=int(modified_at.timestamp() * 1000),
                )
                records.append((record, permissions))
            next_url = pages_batch.get("_links", {}).get("next", None)
            if not next_url:
                break
            pages_url = f"{base_url}/{next_url}"

        return records

    async def fetch_users(self) -> List[AppUser]:
        url = f"{BASE_URL}/{self.cloud_id}/rest/api/3/users/search"
        users = []
        base_url = f"{BASE_URL}/{self.cloud_id}"
        while True:
            users_batch = await self.make_authenticated_json_request("GET", url)
            users = users + users_batch.get("results", [])
            next_url = users_batch.get("_links", {}).get("next", None)
            if not next_url:
                break
            url = f"{base_url}/{next_url}"
        return [AppUser(self.connector_name, email=user["emailAddress"], org_id=self.org_id) for user in users]


@ConnectorBuilder("Confluence")\
    .in_group("Atlassian")\
    .with_auth_type("OAUTH")\
    .with_description("Sync pages, spaces from Confluence Cloud")\
    .with_categories(["Storage"])\
    .configure(lambda builder: builder
        .with_icon("/assets/icons/connectors/confluence.svg")
        .add_documentation_link(DocumentationLink(
            "Confluence Cloud API Setup",
            "https://developer.atlassian.com/cloud/confluence/rest/",
            "setup"
        ))
        .add_documentation_link(DocumentationLink(
            'Pipeshub Documentation',
            'https://docs.pipeshub.com/connectors/confluence/confluence',
            'pipeshub'
        ))
        .with_redirect_uri("connectors/oauth/callback/Confluence", False)
        .add_auth_field(AuthField(
            name="clientId",
            display_name="Application (Client) ID",
            placeholder="Enter your Atlassian Cloud Application ID",
            description="The Application (Client) ID from Azure AD App Registration"
        ))
        .add_auth_field(AuthField(
            name="clientSecret",
            display_name="Client Secret",
            placeholder="Enter your Atlassian Cloud Client Secret",
            description="The Client Secret from Azure AD App Registration",
            field_type="PASSWORD",
            is_secret=True
        ))
        .add_auth_field(AuthField(
            name="domain",
            display_name="Atlassian Domain",
            description="https://your-domain.atlassian.net"
        ))
        .with_sync_strategies(["SCHEDULED", "MANUAL"])
        .with_scheduled_config(True, 60)
        .with_oauth_urls(AUTHORIZE_URL, TOKEN_URL, AtlassianScope.get_full_access())

    )\
    .build_decorator()
class ConfluenceConnector(BaseConnector):
    def __init__(self, logger: Logger, data_entities_processor: DataSourceEntitiesProcessor,
                 data_store_provider: DataStoreProvider, config_service: ConfigurationService) -> None:
        super().__init__(ConfluenceApp(), logger, data_entities_processor, data_store_provider, config_service)

    async def init(self) -> None:
        await self.data_entities_processor.initialize()

        self.confluence_client = await self.get_confluence_client()

        return True

    async def get_signed_url(self, record: Record) -> str:
        return ""

    async def stream_record(self, record: Record) -> StreamingResponse:
        content = await self.confluence_client.fetch_page_content(record.external_record_id)
        return StreamingResponse(
            content,
            media_type=record.mime_type if record.mime_type else "application/octet-stream",
            headers={
                "Content-Disposition": f"attachment; filename={record.record_name}"
            }
        )

    async def test_connection_and_access(self) -> bool:
        return True

    async def run_incremental_sync(self) -> None:
        pass

    async def cleanup(self) -> None:
        pass

    async def handle_webhook_notification(self, notification: Dict) -> None:
        pass

    async def run_sync(self) -> None:
        users = await self.data_entities_processor.get_all_active_users()
        if not users:
            self.logger.info("No users found")
            return
        confluence_client = await self.get_confluence_client()
        user = users[0]
        try:
            spaces = await confluence_client.fetch_spaces_with_permissions()
            for space in spaces:
                page_records = await confluence_client.fetch_pages_with_permissions(space["id"], users)
                await self.data_entities_processor.on_new_records(page_records)
        except Exception as e:
            self.logger.error(f"Error processing user {user.email}: {e}")

    @classmethod
    async def create_connector(cls, logger: Logger,
                               data_store_provider: DataStoreProvider, config_service: ConfigurationService) -> BaseConnector:
        data_entities_processor = DataSourceEntitiesProcessor(logger, data_store_provider, config_service)
        await data_entities_processor.initialize()

        return ConfluenceConnector(logger, data_entities_processor, data_store_provider, config_service)

    async def get_confluence_client(self) -> ConfluenceClient:
        confluence_client = ConfluenceClient(self.logger, self.config_service)
        await confluence_client.initialize()

        return confluence_client
