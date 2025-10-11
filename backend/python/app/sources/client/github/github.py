import logging
from typing import Any, Optional

from github import Auth, Github
from pydantic import BaseModel, Field  # type: ignore

from app.config.configuration_service import ConfigurationService
from app.services.graph_db.interface.graph_db import IGraphService
from app.sources.client.iclient import IClient


# Standardized Github API response wrapper
class GitHubResponse(BaseModel):
    success: bool
    data: Optional[Any] = None
    error: Optional[str] = None
    message: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:  # type: ignore
        return self.model_dump()


# Auth holder clients
class GitHubClientViaToken:
    def __init__(
        self,
        token: str,
        base_url: Optional[str] = None,
        timeout: Optional[float] = None,
        per_page: Optional[int] = None,
    ) -> None:
        self.token = token
        self.base_url = base_url
        self._sdk = None  # PyGithub instance
        self.timeout = timeout
        self.per_page = per_page

    def create_client(self) -> Github:
        # Build kwargs dynamically to exclude None values
        kwargs = {"auth": Auth.Token(self.token)}

        if self.base_url is not None:
            kwargs["base_url"] = self.base_url
        if self.timeout is not None:
            kwargs["timeout"] = self.timeout
        if self.per_page is not None:
            kwargs["per_page"] = self.per_page

        self._sdk = Github(**kwargs)
        return self._sdk

    def get_sdk(self) -> Github:
        if self._sdk is None:
            raise RuntimeError("Client not initialized. Call create_client() first.")
        return self._sdk

    def get_base_url(self) -> Optional[str]:
        return self.base_url


class GitHubConfig(BaseModel):
    token: str
    base_url: Optional[str] = Field(
        default=None,
        description='e.g. "https://ghe.example.com/api/v3" for GH Enterprise',
    )
    timeout: Optional[float] = None
    per_page: Optional[int] = None

    def create_client(self) -> GitHubClientViaToken:
        return GitHubClientViaToken(
            token=self.token,
            base_url=self.base_url,
            timeout=self.timeout,
            per_page=self.per_page,
        )


class GitHubClient(IClient):
    def __init__(self, client: GitHubClientViaToken) -> None:
        self.client = client

    def get_client(self) -> GitHubClientViaToken:
        return self.client

    def get_sdk(self) -> Github:
        return self.client.get_sdk()

    @classmethod
    def build_with_config(
        cls,
        config: GitHubConfig,
    ) -> "GitHubClient":
        client = config.create_client()
        client.create_client()
        return cls(client)

    @classmethod
    async def build_from_services(
        cls,
        logger: logging.Logger,
        config_service: ConfigurationService,
        graph_db_service: IGraphService,
    ) -> "GitHubClient":
        """Build GitHubClient using configuration service and graph database service
        Args:
            logger: Logger instance
            config_service: Configuration service instance
            graph_db_service: Graph database service instance
        Returns:
            GitHubClient instance
        """
        # TODO: Implement - fetch config from services
        # This would typically:
        # 1. Query graph_db_service for stored GitHub credentials
        # 2. Use config_service to get environment-specific settings
        # 3. Return appropriate client based on available credentials (token)

        return cls(client=None)  # type: ignore
