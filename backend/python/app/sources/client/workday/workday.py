# backend/python/app/sources/client/workday/workday.py
"""
Workday client implementation for PipesHub AI.
Uses REST (HTTP) client pattern — modeled after Jira/Confluence clients.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass
from typing import Any, Dict, Optional

import requests  # type: ignore

from app.config.configuration_service import ConfigurationService
from app.sources.client.iclient import IClient


@dataclass
class WorkdayTokenConfig:
    """Configuration for Workday REST client using a pre-generated access token."""

    base_url: str
    access_token: str
    tenant: Optional[str] = None

    def create_client(self) -> "WorkdayRESTClientViaToken":
        return WorkdayRESTClientViaToken(
            base_url=self.base_url,
            access_token=self.access_token,
            tenant=self.tenant,
        )

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class WorkdayOAuthConfig:
    """Configuration for Workday REST client using OAuth2 client credentials."""

    base_url: str
    client_id: str
    client_secret: str
    refresh_token: str
    tenant: Optional[str] = None

    def create_client(self) -> "WorkdayRESTClientViaOAuth":
        return WorkdayRESTClientViaOAuth(
            base_url=self.base_url,
            client_id=self.client_id,
            client_secret=self.client_secret,
            refresh_token=self.refresh_token,
            tenant=self.tenant,
        )

    def to_dict(self) -> dict:
        return asdict(self)


class WorkdayRESTClientViaToken:
    """HTTP client for Workday REST API via static access token."""

    def __init__(self, base_url: str, access_token: str, tenant: Optional[str] = None) -> None:
        if not base_url or not access_token:
            raise ValueError("Workday base_url and access_token are required")
        self.base_url = base_url.rstrip("/")
        self.access_token = access_token
        self.tenant = tenant
        self.logger = logging.getLogger(__name__)

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }

    def get(self, path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        url = f"{self.base_url}/{path.lstrip('/')}"
        self.logger.debug(f"GET {url} params={params}")
        response = requests.get(url, headers=self._headers(), params=params, timeout=30)
        response.raise_for_status()
        return response.json()

    def post(self, path: str, body: Dict[str, Any]) -> Dict[str, Any]:
        url = f"{self.base_url}/{path.lstrip('/')}"
        self.logger.debug(f"POST {url} body={body}")
        response = requests.post(url, headers=self._headers(), json=body, timeout=30)
        response.raise_for_status()
        return response.json()


class WorkdayRESTClientViaOAuth(WorkdayRESTClientViaToken):
    """HTTP client for Workday REST API via OAuth2 (auto-refresh)."""

    def __init__(
        self,
        base_url: str,
        client_id: str,
        client_secret: str,
        refresh_token: str,
        tenant: Optional[str] = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.client_id = client_id
        self.client_secret = client_secret
        self.refresh_token = refresh_token
        self.tenant = tenant
        self.access_token: Optional[str] = None
        self.logger = logging.getLogger(__name__)
        self._refresh_token()

    def _refresh_token(self) -> None:
        """Fetch a new access token from Workday OAuth endpoint."""
        token_url = f"{self.base_url}/oauth2/{self.tenant}/token" if self.tenant else f"{self.base_url}/token"
        payload = {
            "grant_type": "refresh_token",
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "refresh_token": self.refresh_token,
        }
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        self.logger.info(f"Refreshing Workday OAuth token at {token_url}")
        response = requests.post(token_url, data=payload, headers=headers, timeout=15)
        response.raise_for_status()
        data = response.json()
        self.access_token = data.get("access_token")
        if not self.access_token:
            raise ValueError("Failed to obtain access_token from Workday OAuth response")
        self.logger.debug("Successfully refreshed Workday access_token.")


class WorkdayClient(IClient):
    """Builder and high-level wrapper for Workday clients."""

    def __init__(self, client_obj: WorkdayRESTClientViaToken | WorkdayRESTClientViaOAuth) -> None:
        self.client = client_obj
        self.logger = logging.getLogger(__name__)

    def get_client(self) -> WorkdayRESTClientViaToken | WorkdayRESTClientViaOAuth:
        return self.client

    def request(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            if method.lower() == "get":
                return self.client.get(path, params=kwargs.get("params"))
            if method.lower() == "post":
                return self.client.post(path, body=kwargs.get("body"))
            raise ValueError(f"Unsupported method: {method}")
        except Exception as exc:
            self.logger.error(f"Workday API {method.upper()} {path} failed: {exc}")
            raise

    # ----------------------------------------------------------------------
    # Builder helpers
    # ----------------------------------------------------------------------
    @classmethod
    def build_with_config(cls, config: WorkdayTokenConfig | WorkdayOAuthConfig) -> "WorkdayClient":
        return cls(config.create_client())

    @classmethod
    async def build_from_services(cls, logger: logging.Logger, config_service: ConfigurationService) -> "WorkdayClient":
        """Load configuration from etcd or env vars (similar to SplunkClient)."""
        try:
            config = await cls._get_connector_config(logger, config_service)
            if not config:
                logger.info("No Workday config from service, using environment variables")
                config = cls._get_config_from_env()
            if not config:
                raise ValueError("Missing Workday connector configuration")

            auth_type = config.get("authType", "TOKEN")
            base_url = config.get("base_url", os.getenv("WORKDAY_BASE_URL"))
            tenant = config.get("tenant", os.getenv("WORKDAY_TENANT"))

            if auth_type == "TOKEN":
                token = config.get("token", os.getenv("WORKDAY_TOKEN"))
                if not token:
                    raise ValueError("Missing Workday token for TOKEN authType")
                client_obj = WorkdayRESTClientViaToken(base_url=base_url, access_token=token, tenant=tenant)
            elif auth_type == "OAUTH":
                client_id = config.get("client_id", os.getenv("WORKDAY_CLIENT_ID"))
                client_secret = config.get("client_secret", os.getenv("WORKDAY_CLIENT_SECRET"))
                refresh_token = config.get("refresh_token", os.getenv("WORKDAY_REFRESH_TOKEN"))
                if not all([client_id, client_secret, refresh_token]):
                    raise ValueError("Missing OAuth credentials for Workday client")
                client_obj = WorkdayRESTClientViaOAuth(
                    base_url=base_url,
                    client_id=client_id,
                    client_secret=client_secret,
                    refresh_token=refresh_token,
                    tenant=tenant,
                )
            else:
                raise ValueError(f"Unsupported Workday authType: {auth_type}")

            return cls(client_obj)
        except Exception as exc:
            logger.error(f"Failed to build Workday client: {exc}")
            raise

    @staticmethod
    async def _get_connector_config(logger: logging.Logger, config_service: ConfigurationService) -> dict[str, Any]:
        """Fetch connector config from etcd path."""
        try:
            config = await config_service.get_config("/services/connectors/workday/config")
            if not config or not isinstance(config, dict):
                return {}
            return config.get("auth", {}) or {}
        except Exception as exc:
            logger.error(f"Error fetching Workday config from etcd: {exc}")
            return {}

    @staticmethod
    def _get_config_from_env() -> dict[str, Any]:
        """Fallback to environment variables."""
        config: dict[str, Any] = {}
        if os.getenv("WORKDAY_TOKEN"):
            config["authType"] = "TOKEN"
            config["token"] = os.getenv("WORKDAY_TOKEN")
        elif os.getenv("WORKDAY_CLIENT_ID") and os.getenv("WORKDAY_CLIENT_SECRET") and os.getenv("WORKDAY_REFRESH_TOKEN"):
            config["authType"] = "OAUTH"
            config["client_id"] = os.getenv("WORKDAY_CLIENT_ID")
            config["client_secret"] = os.getenv("WORKDAY_CLIENT_SECRET")
            config["refresh_token"] = os.getenv("WORKDAY_REFRESH_TOKEN")
        config["base_url"] = os.getenv("WORKDAY_BASE_URL", "")
        config["tenant"] = os.getenv("WORKDAY_TENANT", "")
        return config
