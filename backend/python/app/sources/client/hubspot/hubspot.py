# ruff: noqa: ALL

"""HubSpot Client Implementation - Matching Your Exact Project Structure

Based on your working Airtable client patterns and HTTP client structure.
"""

from typing import Any

from pydantic import BaseModel  # type: ignore

from app.sources.client.http.http_client import HTTPClient
from app.sources.client.iclient import IClient


class HubSpotResponse(BaseModel):
    """Standardized HubSpot API response wrapper - matches AirtableResponse pattern"""

    success: bool
    data: dict[str, Any] | None = None
    error: str | None = None
    message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for JSON serialization"""
        return self.model_dump()

    def to_json(self) -> str:
        """Convert to JSON string"""
        return self.model_dump_json()


class HubSpotRESTClientViaToken(HTTPClient):
    """HubSpot REST client via Private App Access Token - matches Airtable pattern"""

    def __init__(self, token: str, base_url: str = "https://api.hubapi.com") -> None:
        super().__init__(token, "Bearer")
        self.base_url = base_url.rstrip("/")

        # Add HubSpot-specific headers
        self.headers.update({
            "Content-Type": "application/json",
            "Accept": "application/json",
        })

    def get_base_url(self) -> str:
        """Get the base URL"""
        return self.base_url


class HubSpotTokenConfig(BaseModel):
    """Configuration for HubSpot REST client via Private App Access Token - matches AirtableTokenConfig"""

    token: str
    base_url: str = "https://api.hubapi.com"
    ssl: bool = True

    def create_client(self) -> HubSpotRESTClientViaToken:
        return HubSpotRESTClientViaToken(self.token, self.base_url)

    def to_dict(self) -> dict:
        """Convert the configuration to a dictionary"""
        return self.model_dump()


class HubSpotClient(IClient):
    """Builder class for HubSpot clients - matches AirtableClient pattern"""

    def __init__(self, client: HubSpotRESTClientViaToken) -> None:
        """Initialize with a HubSpot client object"""
        self.client = client

    def get_client(self) -> HubSpotRESTClientViaToken:
        """Return the HubSpot client object"""
        return self.client

    def get_base_url(self) -> str:
        """Get the base URL"""
        return self.client.get_base_url()

    @classmethod
    def build_with_config(cls, config: HubSpotTokenConfig) -> "HubSpotClient":
        """Build HubSpotClient with configuration - matches Airtable pattern"""
        return cls(config.create_client())
