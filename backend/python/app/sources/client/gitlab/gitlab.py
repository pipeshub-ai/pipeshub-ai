import logging
from typing import Any

import gitlab
from gitlab import Gitlab
from pydantic import BaseModel, Field  # type: ignore

from app.config.configuration_service import ConfigurationService
from app.sources.client.iclient import IClient


class GitLabResponse(BaseModel):
    success: bool
    data: Any | None = None
    error: str | None = None
    message: str | None = None

    def to_dict(self) -> dict[str, Any]:  # type: ignore
        return self.model_dump()


class GitLabClientViaToken:
    def __init__(
        self,
        token: str,
        url: str | None = None,
        timeout: float | None = None,
        api_version: str | None = "4",
        retry_transient_errors: bool | None = None,
        max_retries: int | None = None,
        obey_rate_limit: bool | None = None,
    ) -> None:
        self.token = token
        self.url = url or "https://gitlab.com"
        self.timeout = timeout
        self.api_version = api_version
        self.retry_transient_errors = retry_transient_errors
        self.max_retries = max_retries
        self.obey_rate_limit = obey_rate_limit

        self._sdk: Gitlab | None = None

    def create_client(self) -> Gitlab:
        # NOTE: this handles only authorization via OAuth token
        # if used will need to change token request param if API_TOKEN
        kwargs: dict[str, Any] = {
            "url": self.url,
            "oauth_token": self.token,
        }
        if self.timeout is not None:
            kwargs["timeout"] = self.timeout
        if self.api_version is not None:
            kwargs["api_version"] = self.api_version
        if self.retry_transient_errors is not None:
            kwargs["retry_transient_errors"] = self.retry_transient_errors
        if self.max_retries is not None:
            kwargs["max_retries"] = self.max_retries
        if self.obey_rate_limit is not None:
            kwargs["obey_rate_limit"] = self.obey_rate_limit

        self._sdk = gitlab.Gitlab(**kwargs)
        return self._sdk

    def get_sdk(self) -> Gitlab:
        if self._sdk is None:
            # lazy init if not yet created
            return self.create_client()
        return self._sdk

    def get_base_url(self) -> str:
        return self.url

    def get_token(self) -> str:
        return self.token

class GitLabConfig(BaseModel):
    token: str = Field(..., description="GitLab private token")
    url: str | None = Field(
        default="https://gitlab.com", description="GitLab instance URL"
    )
    timeout: float | None = None
    api_version: str | None = Field(default="4", description="GitLab API version")
    retry_transient_errors: bool | None = None
    max_retries: int | None = None
    obey_rate_limit: bool | None = None

    def create_client(self) -> GitLabClientViaToken:
        return GitLabClientViaToken(
            token=self.token,
            url=self.url,
            timeout=self.timeout,
            api_version=self.api_version,
            retry_transient_errors=self.retry_transient_errors,
            max_retries=self.max_retries,
            obey_rate_limit=self.obey_rate_limit,
        )


class GitLabClient(IClient):
    def __init__(self, client: GitLabClientViaToken) -> None:
        self.client = client

    def get_client(self) -> GitLabClientViaToken:
        return self.client

    def get_sdk(self) -> Gitlab:
        return self.client.get_sdk()

    def get_token(self) -> str:
        return self.client.get_token()

    @classmethod
    def build_with_config(
        cls,
        config: GitLabConfig,
    ) -> "GitLabClient":
        client = config.create_client()
        client.get_sdk()
        return cls(client)

    @classmethod
    async def build_from_services(
        cls,
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: str | None = None,
    ) -> "GitLabClient":
        """Build GitLabClient using configuration service
        Args:
            logger: Logger instance
            config_service: Configuration service instance
        Returns:
            GitLabClient instance
        """
        config = await cls._get_connector_config(logger, config_service, connector_instance_id)
        if not config:
            raise ValueError("Failed to get GitLab connector configuration")
        auth_config = config.get("auth", {})
        if not auth_config:
            raise ValueError("Auth configuration missing for GitLab connector")
        credentials_config  = config.get("credentials",{})
        if not  credentials_config:
            raise ValueError("Credentials configuration not found in Gitlab connector configuration")
        auth_type = auth_config.get("authType", "API_TOKEN")  # API_TOKEN or OAUTH default is API_TOKEN

        if auth_type == "API_TOKEN":
            # NOTE: if used will need to change token request param if API_TOKEN is used
            token = auth_config.get("token", "")
            timeout = auth_config.get("timeout", 30)
            url = auth_config.get("url", "https://gitlab.com")
            if not token:
                raise ValueError("Token required for token auth type")
            client = GitLabClientViaToken(token, url, timeout)
            client.create_client()
        elif auth_type == "OAUTH":
            access_token =credentials_config.get("access_token","")
            timeout = auth_config.get("timeout", 30)
            url = auth_config.get("url", "https://gitlab.com")
            if not access_token:
                raise ValueError("Access token required for OAuth auth type")
            client = GitLabClientViaToken(access_token,url, timeout)
            client.create_client()
        else:
            raise ValueError(f"Invalid auth type: {auth_type}")
        return cls(client)

    @staticmethod
    async def _get_connector_config(logger: logging.Logger, config_service: ConfigurationService, connector_instance_id: str | None = None) -> dict[str, Any]:
        """Fetch connector config from etcd for GitLab."""
        try:
            config = await config_service.get_config(f"/services/connectors/{connector_instance_id}/config")
            if not config:
                raise ValueError(f"Failed to get GitLab connector configuration for instance {connector_instance_id}")
            return config
        except Exception as e:
            raise ValueError(f"Failed to get GitLab connector configuration for instance {connector_instance_id}") from e
