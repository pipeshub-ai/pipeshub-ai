import json
from dataclasses import dataclass
from typing import Any

from app.config.configuration_service import ConfigurationService
from app.sources.client.http.http_client import HTTPClient
from app.sources.client.http.http_request import HTTPRequest
from app.sources.client.http.http_response import HTTPResponse
from app.sources.client.iclient import IClient


class ZoomRESTClientViaToken(HTTPClient):
    """Zoom REST client using Bearer token for authentication."""

    def __init__(self, base_url: str, token: str, token_type: str = "Bearer") -> None:
        super().__init__(token, token_type)
        self.base_url = base_url

    def get_base_url(self) -> str:
        """Return the base URL."""
        return self.base_url

    async def request(self, method: str, endpoint: str, body: Any = None) -> dict:
        """Perform an HTTP request asynchronously."""
        url = f"{self.base_url}{endpoint}"
        req = HTTPRequest(
            method=method,
            url=url,
            body=body or {},
            headers={"Content-Type": "application/json"},
        )
        response: HTTPResponse = await self.execute(req)
        try:
            return response.json()
        except json.JSONDecodeError:
            return {"status": response.status, "raw": response.bytes()}


@dataclass
class ZoomTokenConfig:
    """Configuration for Zoom REST client via token."""
    base_url: str = "https://api.zoom.us/v2"
    token: str = ""

    def create_client(self) -> ZoomRESTClientViaToken:
        return ZoomRESTClientViaToken(self.base_url, self.token)


class ZoomClient(IClient):
    """Builder class for Zoom REST clients."""

    def __init__(self, client: ZoomRESTClientViaToken) -> None:
        self.client = client

    def get_client(self) -> ZoomRESTClientViaToken:
        """Return the Zoom client instance."""
        return self.client

    @classmethod
    def build_with_config(cls, config: ZoomTokenConfig) -> "ZoomClient":
        """Build ZoomClient using configuration."""
        return cls(config.create_client())

    @classmethod
    async def build_from_services(
        cls,
        logger,
        config_service: ConfigurationService,
        arango_service,
        org_id: str,
        user_id: str,
    ) -> "ZoomClient":
        """Build ZoomClient using configuration service (placeholder)."""
        raise NotImplementedError("build_from_services is not yet implemented.")
