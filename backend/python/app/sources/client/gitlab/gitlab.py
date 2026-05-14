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
        auth_type: str = "OAUTH",
    ) -> None:
        self.token = token
        self.url = url or "https://gitlab.com"
        self.timeout = timeout
        self.api_version = api_version
        self.retry_transient_errors = retry_transient_errors
        self.max_retries = max_retries
        self.obey_rate_limit = obey_rate_limit
        self.auth_type = auth_type  # "OAUTH" or "API_TOKEN"

        self._sdk: Gitlab | None = None

    def create_client(self) -> Gitlab:
        kwargs: dict[str, Any] = {"url": self.url}

        # Use private_token for PAT-based auth, oauth_token for OAuth flows
        if self.auth_type == "API_TOKEN":
            kwargs["private_token"] = self.token
        else:
            kwargs["oauth_token"] = self.token

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

    def set_token(self, token: str) -> None:
        """Update the active token in-place so long-lived instances pick up refreshed credentials.

        Mutates both the local ``token`` field and the underlying SDK instance's
        token attribute so API calls issued after this call use the new token
        without requiring a full client rebuild.
        """
        self.token = token
        if self._sdk is not None:
            if self.auth_type == "API_TOKEN":
                self._sdk.private_token = token
                # python-gitlab stores private token in http session headers
                self._sdk._set_auth_info()
            else:
                self._sdk.oauth_token = token
                self._sdk._set_auth_info()


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
        config = await cls._get_connector_config(
            logger, config_service, connector_instance_id
        )
        if not config:
            raise ValueError("Failed to get GitLab connector configuration")
        auth_config = config.get("auth", {})
        if not auth_config:
            raise ValueError("Auth configuration missing for GitLab connector")
        credentials_config = config.get("credentials", {})
        if not credentials_config:
            raise ValueError(
                "Credentials configuration not found in Gitlab connector configuration"
            )
        auth_type = auth_config.get(
            "authType", "OAUTH"
        )  # "OAUTH" or "API_TOKEN"; default is OAUTH

        # instanceUrl supports self-managed GitLab EE; falls back to gitlab.com
        instance_url = auth_config.get("instanceUrl", "https://gitlab.com").rstrip("/")
        timeout = auth_config.get("timeout", 30)

        if auth_type == "API_TOKEN":
            token = auth_config.get("token", "")
            if not token:
                raise ValueError("Token required for API_TOKEN auth type")
            client = GitLabClientViaToken(
                token, instance_url, timeout, auth_type="API_TOKEN"
            )
            client.create_client()
        elif auth_type == "OAUTH":
            access_token = credentials_config.get("access_token", "")
            if not access_token:
                raise ValueError("Access token required for OAuth auth type")
            client = GitLabClientViaToken(
                access_token, instance_url, timeout, auth_type="OAUTH"
            )
            client.create_client()
        else:
            raise ValueError(f"Invalid auth type: {auth_type}")
        return cls(client)

    @staticmethod
    async def _get_connector_config(
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: str | None = None,
    ) -> dict[str, Any]:
        """Fetch connector config from etcd for GitLab."""
        try:
            config = await config_service.get_config(
                f"/services/connectors/{connector_instance_id}/config"
            )
            if not config:
                raise ValueError(
                    f"Failed to get GitLab connector configuration for instance {connector_instance_id}"
                )
            return config
        except Exception as e:
            raise ValueError(
                f"Failed to get GitLab connector configuration for instance {connector_instance_id}"
            ) from e
