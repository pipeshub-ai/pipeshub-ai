import asyncio
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional

from app.agents.client.http import HTTPClient
from app.agents.client.iclient import IClient
from app.config.configuration_service import ConfigurationService

RESOURCE_URL = "https://api.atlassian.com/oauth/token/accessible-resources"
BASE_URL = "https://api.atlassian.com/ex/confluence"

@dataclass
class AtlassianCloudResource:
    """Represents an Atlassian Cloud resource (site)
    Args:
        id: The ID of the resource
        name: The name of the resource
        url: The URL of the resource
        scopes: The scopes of the resource
        avatar_url: The avatar URL of the resource
    """
    id: str
    name: str
    url: str
    scopes: List[str]
    avatar_url: Optional[str] = None

class ConfluenceRESTClientViaUsernamePassword(HTTPClient):
    """Confluence REST client via username and password
    Args:
        username: The username to use for authentication
        password: The password to use for authentication
        token_type: The type of token to use for authentication
    """
    def __init__(self, username: str, password: str, token_type: str = "Basic") -> None:
        #TODO: Implement
        pass

class ConfluenceRESTClientViaApiKey(HTTPClient):
    """Confluence REST client via API key
    Args:
        email: The email to use for authentication
        api_key: The API key to use for authentication
    """
    def __init__(self, email: str, api_key: str) -> None:
        #TODO: Implement
        pass


class ConfluenceRESTClientViaToken(HTTPClient):
    def __init__(self, token: str, token_type: str = "Bearer") -> None:
        super().__init__(token, token_type)
        self.accessible_resources = None
        self.cloud_id = None

    async def initialize(self) -> None:
        if self.accessible_resources is None:
            self.accessible_resources = await self._get_accessible_resources()
            if self.accessible_resources:
                self.cloud_id = self.accessible_resources[0].id
            else:
                raise Exception("No accessible resources found")


    async def _get_accessible_resources(self) -> List[AtlassianCloudResource]:
        """
        Get list of Atlassian sites (Confluence/Jira instances) accessible to the user
        Args:
            None
        Returns:
            List of accessible Atlassian Cloud resources
        """
        response = await self.get(RESOURCE_URL)
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

    async def get_spaces_with_permissions(
        self,
    ) -> Dict[str, Any]:
        """
        Get all Confluence spaces
        Args:
            None
        Returns:
            List of Confluence spaces with permissions
        """
        await self.initialize()
        base_url = f"{BASE_URL}/{self.cloud_id}"
        spaces_url = f"{base_url}/wiki/api/v2/spaces"
        spaces = []
        while True:
            spaces_batch = await self.get(spaces_url)
            spaces = spaces + spaces_batch.get("results", []) # TODO: use list extend instead
            next_url = spaces_batch.get("_links", {}).get("next", None)
            if not next_url:
                break
            spaces_url = f"{base_url}{next_url}"

        tasks = [self._get_space_permission(space["id"]) for space in spaces]
        all_permissions = await asyncio.gather(*tasks)
        for space, permissions in zip(spaces, all_permissions):
            space["permissions"] = permissions

        return spaces

    async def _get_space_permission(
        self,
        space_id: str,
    ) -> Dict[str, Any]:
        """Get permissions for a space
        Args:
            space_id: The ID of the space to get permissions for
        Returns:
            A list of permissions for the space
        """
        await self.initialize()
        permissions = []
        base_url = f"{BASE_URL}/{self.cloud_id}"
        url = f"{base_url}/wiki/api/v2/spaces/{space_id}/permissions"
        while True:
            permissions_batch = await self.get(url)
            # TODO: use list extend instead
            permissions = permissions + permissions_batch.get("results", [])
            next_url = permissions_batch.get("_links", {}).get("next", None)
            if not next_url:
                break
            url = f"{base_url}/{next_url}"

        return permissions

    async def get_page_content(
        self,
        page_id: str,
    ) -> str:
        """Get the content of a page
        Args:
            page_id: The ID of the page to get the content of
        Returns:
            The content of the page
        """
        await self.initialize()
        base_url = f"{BASE_URL}/{self.cloud_id}"
        url = f"{base_url}/wiki/api/v2/pages/{page_id}"
        page_details = await self.get(url, params={"body-format": "storage"})
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

    async def get_page(
        self,
        page_id: str,
    ) -> Dict[str, Any]:
        """Get the details of a page
        Args:
            page_id: The ID of the page to get the details of
        Returns:
            The details of the page
        """
        await self.initialize()
        base_url = f"{BASE_URL}/{self.cloud_id}"
        url = f"{base_url}/wiki/api/v2/pages/{page_id}"
        return await self.get(url)

    async def create_page(
        self,
        title: str,
        space_id: str,
        parent_id: Optional[str] = None,
        html_body: str = "",
        subtype: str = "live"
    ) -> None:
        """Create a page in Confluence
        Args:
            title: The title of the page
            space_id: The ID of the space to create the page in
            parent_id: The ID of the parent page
            html_body: The HTML body of the page
            subtype: The subtype of the page
        """
        await self.initialize()
        base_url = f"{BASE_URL}/{self.cloud_id}"
        url = f"{base_url}/wiki/api/v2/pages"

        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json"
        }

        payload = {
        "spaceId": space_id,
        "status": "current",
        "title": title,
        "body": {
            "representation": "storage",
            "value": html_body
        },
        "subtype": subtype
        }

        if parent_id:
            payload["parentId"] = parent_id

        await self.post(url, payload, headers=headers)

    async def get_pages(
        self,
        limit: int = 25,
    ) -> List[Dict[str, Any]]:
        """Get pages from Confluence
        Args:
            limit: The number of pages to get
            space_id: The ID of the space to get the pages from
        Returns:
            A list of pages
        """
        await self.initialize()
        base_url = f"{BASE_URL}/{self.cloud_id}"
        url = f"{base_url}/wiki/api/v2/pages"
        pages = []
        while True:
            pages_batch = await self.get(url, params={"limit": limit})
            pages = pages + pages_batch.get("results", []) # TODO: use list extend instead
            next_url = pages_batch.get("_links", {}).get("next", None)
            if not next_url:
                break
            url = f"{base_url}/{next_url}"
        return pages

    async def invite_email(
        self,
        email: str
    ) -> None:
        """Invite an email to Confluence
        Args:
            email: The email to invite
        """
        await self.initialize()
        base_url = f"{BASE_URL}/{self.cloud_id}"
        url = f"{base_url}/wiki/api/v2/user/access/invite-by-email"
        payload = {
            "email": [email]
        }
        await self.post(url, payload)

@dataclass
class ConfluenceUsernamePasswordConfig():
    """Configuration for Confluence REST client via username and password
    Args:
        base_url: The base URL of the Confluence instance
        username: The username to use for authentication
        password: The password to use for authentication
        ssl: Whether to use SSL
    """
    base_url: str
    username: str
    password: str
    ssl: bool = False

    def create_client(self) -> ConfluenceRESTClientViaUsernamePassword:
        return ConfluenceRESTClientViaUsernamePassword(self.username, self.password, "Basic")

    def to_dict(self) -> dict:
        """Convert the configuration to a dictionary"""
        return asdict(self)

@dataclass
class ConfluenceTokenConfig():
    """Configuration for Confluence REST client via token
    Args:
        base_url: The base URL of the Confluence instance
        token: The token to use for authentication
        ssl: Whether to use SSL
    """
    base_url: str
    token: str
    ssl: bool = False

    def create_client(self) -> ConfluenceRESTClientViaToken:
        return ConfluenceRESTClientViaToken(self.token)

    def to_dict(self) -> dict:
        """Convert the configuration to a dictionary"""
        return asdict(self)

@dataclass
class ConfluenceApiKeyConfig():
    """Configuration for Confluence REST client via API key
    Args:
        base_url: The base URL of the Confluence instance
        email: The email to use for authentication
        api_key: The API key to use for authentication
        ssl: Whether to use SSL
    """
    base_url: str
    email: str
    api_key: str
    ssl: bool = False

    def create_client(self) -> ConfluenceRESTClientViaApiKey:
        return ConfluenceRESTClientViaApiKey(self.email, self.api_key)

    def to_dict(self) -> dict:
        """Convert the configuration to a dictionary"""
        return asdict(self)


class ConfluenceClient(IClient):
    """Builder class for Confluence clients with different construction methods"""

    def __init__(self, client: object) -> None:
        """Initialize with a Confluence client object"""
        self.client = client

    def get_client(self) -> object:
        """Return the Confluence client object"""
        return self.client

    @classmethod
    def build_with_client(cls, client: object) -> 'ConfluenceClient':
        """
        Build ConfluenceClient with an already authenticated client
        Args:
            client: Authenticated Confluence client object
        Returns:
            ConfluenceClient instance
        """
        return cls(client)

    @classmethod
    def build_with_config(cls, config: ConfluenceUsernamePasswordConfig | ConfluenceTokenConfig | ConfluenceApiKeyConfig) -> 'ConfluenceClient':
        """
        Build ConfluenceClient with configuration (placeholder for future OAuth2/enterprise support)
        Args:
            config: ConfluenceConfigBase instance
        Returns:
            ConfluenceClient instance with placeholder implementation
        """
        return cls(config.create_client())

    @classmethod
    async def build_from_services(
        cls,
        logger,
        config_service: ConfigurationService,
        arango_service,
        org_id: str,
        user_id: str,
    ) -> 'ConfluenceClient':
        """
        Build ConfluenceClient using configuration service and arango service
        Args:
            logger: Logger instance
            config_service: Configuration service instance
            arango_service: ArangoDB service instance
            org_id: Organization ID
            user_id: User ID
        Returns:
            ConfluenceClient instance
        """
        return cls(client=None)

