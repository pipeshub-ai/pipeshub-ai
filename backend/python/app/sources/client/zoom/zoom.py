from dataclasses import asdict, dataclass
from typing import Any

from app.config.configuration_service import ConfigurationService
from app.sources.client.http.http_client import HTTPClient
from app.sources.client.http.http_request import HTTPRequest
from app.sources.client.http.http_response import HTTPResponse
from app.sources.client.iclient import IClient


class ZoomRESTClientViaToken(HTTPClient):
    """Zoom REST client via OAuth Token"""

    def __init__(self, base_url: str = "https://api.zoom.us/v2", token: str = "", token_type: str = "Bearer") -> None:
        super().__init__(token, token_type)
        self.base_url = base_url

    def get_base_url(self) -> str:
        """Return the base URL"""
        return self.base_url

    async def request(self, method: str, endpoint: str, body: Any = None) -> dict:
        """Perform an HTTP request asynchronously"""
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
        except Exception:
            return {"status": response.status, "raw": response.content}


@dataclass
class ZoomTokenConfig:
    """Configuration for Zoom REST client via token"""
    base_url: str = "https://api.zoom.us/v2"
    token: str = ""
    ssl: bool = True

    def create_client(self) -> ZoomRESTClientViaToken:
        return ZoomRESTClientViaToken(self.base_url, self.token)

    def to_dict(self) -> dict:
        return asdict(self)


class ZoomClient(IClient):
    """Builder class for Zoom clients (wrapper around REST client)."""

    def __init__(self, client: ZoomRESTClientViaToken) -> None:
        self.client = client

    def get_client(self) -> ZoomRESTClientViaToken:
        return self.client

    @classmethod
    def build_with_config(cls, config: ZoomTokenConfig) -> "ZoomClient":
        """Build ZoomClient using configuration"""
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
        """Build ZoomClient using config service (placeholder)"""
        return cls(client=None)  # type: ignore
