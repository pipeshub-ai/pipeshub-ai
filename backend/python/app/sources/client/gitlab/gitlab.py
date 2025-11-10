import logging
from typing import Any

import gitlab
from gitlab import Gitlab, GitlabAuthenticationError
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
        kwargs: dict[str, Any] = {
            "url": self.url,
            "private_token": self.token,
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
        try:
            self._sdk.auth()  # validate the credentials early
        except GitlabAuthenticationError as e:
            raise RuntimeError("GitLab authentication failed") from e
        except Exception as e:
            raise RuntimeError("Error initializing GitLab client") from e

        return self._sdk

    def get_sdk(self) -> Gitlab:
        if self._sdk is None:
            # lazy init if not yet created
            return self.create_client()
        return self._sdk

    def get_base_url(self) -> str:
        return self.url


class GitLabConfig(BaseModel):
    token: str = Field(..., description="GitLab private token")
    url: str | None = Field(
        default="https://gitlab.com",
        description="GitLab instance URL",
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
    ) -> "GitLabClient":
        """Build GitLabClient using configuration service
        Args:
            logger: Logger instance
            config_service: Configuration service instance
        Returns:
            GitLabClient instance
        """
        config = await cls._get_connector_config(logger, config_service)
        if not config:
            raise ValueError("Failed to get GitLab connector configuration")
        auth_type = config.get("authType", "API_TOKEN")  # API_TOKEN or OAUTH
        auth_config = config.get("auth", {})
        if auth_type == "API_TOKEN":
            token = auth_config.get("token", "")
            timeout = auth_config.get("timeout", 30)
            url = auth_config.get("url", "https://gitlab.com")
            if not token:
                raise ValueError("Token required for token auth type")
            client = GitLabClientViaToken(token, url, timeout).create_client()
        else:
            raise ValueError(f"Invalid auth type: {auth_type}")
        return cls(client)

    @staticmethod
    async def _get_connector_config(
        logger: logging.Logger, config_service: ConfigurationService
    ) -> dict[str, Any]:
        """Fetch connector config from etcd for GitLab."""
        try:
            config = await config_service.get_config(
                "/services/connectors/gitlab/config"
            )
            return config or {}
        except Exception as e:
            logger.error(f"Failed to get GitLab connector config: {e}")
            return {}
