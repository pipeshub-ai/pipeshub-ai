import json
from dataclasses import asdict, dataclass
from typing import Any, Dict, Optional

import aiohttp

from app.config.configuration_service import ConfigurationService
from app.sources.client.iclient import IClient


@dataclass
class LinkedInResponse:
    """Standardized LinkedIn API response wrapper"""
    success: bool
    data: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    message: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization"""
        return asdict(self)

    def to_json(self) -> str:
        """Convert to JSON string"""
        return json.dumps(self.to_dict())


class LinkedInRESTClientViaOAuth2:
    """LinkedIn REST client via OAuth 2.0 access token

    Args:
        access_token: OAuth 2.0 access token for authentication
        api_version: LinkedIn API version (default: v2)
    """
    def __init__(self, access_token: str, api_version: str = "v2") -> None:
        self.access_token = access_token
        self.api_version = api_version
        self.base_url = f"https://api.linkedin.com/{api_version}"
        self.session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create aiohttp session"""
        if self.session is None or self.session.closed:
            # Create connector with SSL verification disabled for development/testing
            import ssl
            ssl_context = ssl.create_default_context()
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE

            connector = aiohttp.TCPConnector(ssl=ssl_context)
            self.session = aiohttp.ClientSession(connector=connector)
        return self.session

    async def _execute_request(
        self,
        method: str,
        path: str,
        **kwargs: Any
    ) -> Dict[str, Any]:
        """Execute an HTTP request to LinkedIn API

        Args:
            method: HTTP method (GET, POST, PUT, DELETE, etc.)
            path: API endpoint path
            **kwargs: Additional parameters (params, json, headers, etc.)

        Returns:
            Response data as dictionary
        """
        session = await self._get_session()

        # Build URL
        url = f"{self.base_url}{path}"

        # Prepare headers
        headers = kwargs.pop("headers", {})
        headers.update({
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
            "X-Restli-Protocol-Version": "2.0.0"
        })

        # Separate params and body
        params = kwargs.pop("params", {})
        body = kwargs.pop("json", kwargs.pop("data", None))

        # All remaining kwargs are query parameters
        params.update(kwargs)

        # Execute request
        async with session.request(
            method=method.upper(),
            url=url,
            headers=headers,
            params=params,
            json=body
        ) as response:
            # Handle response
            try:
                data = await response.json()
            except Exception:
                data = {"text": await response.text()}

            # Add status to response
            if "status" not in data:
                data["status"] = response.status

            # Check for errors
            if response.status >= 400:
                error_msg = data.get("message") or data.get("error") or f"HTTP {response.status}"
                data["error"] = error_msg

            return data

    async def close(self) -> None:
        """Close the aiohttp session"""
        if self.session and not self.session.closed:
            await self.session.close()

    def __del__(self) -> None:
        """Cleanup on deletion"""
        if self.session and not self.session.closed:
            import asyncio
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    loop.create_task(self.close())
                else:
                    loop.run_until_complete(self.close())
            except Exception:
                pass


@dataclass
class LinkedInOAuth2Config:
    """Configuration for LinkedIn REST client via OAuth 2.0

    Args:
        access_token: OAuth 2.0 access token
        api_version: API version (default: v2)
    """
    access_token: str
    api_version: str = "v2"

    def create_client(self) -> LinkedInRESTClientViaOAuth2:
        return LinkedInRESTClientViaOAuth2(
            access_token=self.access_token,
            api_version=self.api_version
        )

    def to_dict(self) -> dict:
        """Convert the configuration to a dictionary"""
        return asdict(self)


class LinkedInClient(IClient):
    """Builder class for LinkedIn clients with OAuth 2.0 authentication"""

    def __init__(self, client: LinkedInRESTClientViaOAuth2) -> None:
        """Initialize with a LinkedIn client object"""
        self.client = client

    def get_client(self) -> LinkedInRESTClientViaOAuth2:
        """Return the LinkedIn client object"""
        return self.client

    async def _execute_request(
        self,
        method: str,
        path: str,
        **kwargs: Any
    ) -> Dict[str, Any]:
        """Execute a request through the client"""
        return await self.client._execute_request(method, path, **kwargs)

    @classmethod
    def build_with_config(cls, config: LinkedInOAuth2Config) -> 'LinkedInClient':
        """Build LinkedInClient with configuration

        Args:
            config: LinkedInOAuth2Config instance

        Returns:
            LinkedInClient instance
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
    ) -> 'LinkedInClient':
        """Build LinkedInClient using configuration service and arango service

        Args:
            logger: Logger instance
            config_service: Configuration service instance
            arango_service: ArangoDB service instance
            org_id: Organization ID
            user_id: User ID

        Returns:
            LinkedInClient instance
        """
        # TODO: Implement service-based client construction
        # This would retrieve credentials from the configuration service
        raise NotImplementedError("Service-based client construction not yet implemented")

    async def close(self) -> None:
        """Close the client session"""
        await self.client.close()
