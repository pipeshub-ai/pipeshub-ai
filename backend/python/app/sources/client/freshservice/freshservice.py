from __future__ import annotations

import base64
from typing import Optional, Union

from pydantic import BaseModel  # type: ignore

from app.config.configuration_service import ConfigurationService
from app.sources.client.http.http_client import HTTPClient
from app.sources.client.http.http_request import HTTPRequest
from app.sources.client.iclient import IClient


class FreshServiceRESTClientViaToken(HTTPClient):
    """Freshservice REST client via API Key (Basic Auth)

    Args:
        domain: Your Freshservice domain prefix (e.g. "acme" for acme.freshservice.com)
        api_key: Your Freshservice API key
    """

    def __init__(self, domain: str, api_key: str) -> None:
        # Initialize with empty token, we'll override headers for Basic auth
        super().__init__(token="", token_type="")
        self.domain = domain
        self.base_url = f"https://{domain}.freshservice.com/api/v2"

        # Freshservice uses API key as username and 'X' as password for Basic auth
        credentials = f"{api_key}:X".encode()
        encoded_credentials = base64.b64encode(credentials).decode()
        self.headers = {
            "Authorization": f"Basic {encoded_credentials}",
            "Content-Type": "application/json",
        }

    def get_base_url(self) -> str:
        return self.base_url

    def get_domain(self) -> str:
        return self.domain


class FreshServiceTokenConfig(BaseModel):
    """Configuration for Freshservice REST client via API Key.

    Args:
        domain: Your Freshservice domain prefix (e.g. "acme" for acme.freshservice.com)
        api_key: Your Freshservice API key
        ssl: Whether to use SSL (always True for Freshservice)
    """

    domain: str
    api_key: str
    ssl: bool = True

    def create_client(self) -> FreshServiceRESTClientViaToken:
        return FreshServiceRESTClientViaToken(self.domain, self.api_key)

    def to_dict(self) -> dict:
        return self.model_dump()


class FreshServiceClient(IClient):
    """Builder class for Freshservice clients with different construction methods"""

    def __init__(self, client: FreshServiceRESTClientViaToken) -> None:
        self.client = client

    def get_client(self) -> FreshServiceRESTClientViaToken:
        return self.client

    def get_base_url(self) -> str:
        return self.client.get_base_url()

    def get_domain(self) -> str:
        return self.client.get_domain()

    @classmethod
    def build_with_config(cls, config: FreshServiceTokenConfig) -> "FreshServiceClient":
        return cls(config.create_client())

    @classmethod
    async def build_from_services(  # pragma: no cover - integration dependent
        cls,
        logger,
        config_service: ConfigurationService,
        arango_service,
        org_id: str,
        user_id: str,
    ) -> "FreshServiceClient":
        """Build FreshServiceClient using configuration services.

        This is intentionally left unimplemented until configuration storage
        schema is finalized for Freshservice.
        """
        raise NotImplementedError("build_from_services is not yet implemented")
