import logging
from typing import Any

from github import Auth, Github
from pydantic import BaseModel, Field  # type: ignore
from urllib3.util.retry import Retry

# PyGithub's default retry (GithubRetry) silently sleeps INSIDE the SDK until
# the rate-limit window resets — observed parking worker threads for 30+ min.
# Rate limits are handled explicitly by the connector (probe + pause + backfill
# floor), so the SDK should only retry transient server errors, briefly, and
# let 403s surface immediately as failures.
_TRANSIENT_RETRY = Retry(
    total=3,
    backoff_factor=1.0,
    status_forcelist=(500, 502, 503, 504),
)

from app.config.configuration_service import ConfigurationService
from app.sources.client.iclient import IClient


# Standardized Github API response wrapper
class GitHubResponse(BaseModel):
    success: bool
    data: Any | None = None
    error: str | None = None
    message: str | None = None
    # HTTP status and exception class are preserved separately because callers
    # need to tell 401 (re-auth) from 403 (rate limit) from 5xx (retry), and
    # stringifying the exception into `error` loses that distinction.
    status_code: int | None = None
    exception_type: str | None = None

    def to_dict(self) -> dict[str, Any]:  # type: ignore
        return self.model_dump()


# Auth holder clients
class GitHubClientViaToken:
    def __init__(
        self,
        token: str,
        base_url: str | None = None,
        timeout: float | None = None,
        per_page: int | None = None,
    ) -> None:
        self.token = token
        self.base_url = base_url
        self._sdk = None  # PyGithub instance
        self.timeout = timeout
        self.per_page = per_page

    def create_client(self) -> None:
        # Build kwargs dynamically to exclude None values
        kwargs = {"auth": Auth.Token(self.token), "retry": _TRANSIENT_RETRY}

        if self.base_url is not None:
            kwargs["base_url"] = self.base_url
        if self.timeout is not None:
            kwargs["timeout"] = self.timeout
        if self.per_page is not None:
            kwargs["per_page"] = self.per_page

        self._sdk = Github(**kwargs)


    def get_sdk(self) -> Github:
        if self._sdk is None:
            raise RuntimeError("Client not initialized. Call create_client() first.")
        return self._sdk

    def get_base_url(self) -> str | None:
        return self.base_url

    def get_token(self) ->str:
        return self.token

    def set_token(self, token: str) -> None:
        """Rotate the access token and rebuild the underlying PyGithub SDK instance.

        PyGithub binds the ``Auth.Token`` at construction time, so a refreshed
        OAuth access token requires a new ``Github`` instance rather than
        mutating the existing one in place.
        """
        self.token = token
        self.create_client()


class GitHubConfig(BaseModel):
    token: str
    base_url: str | None = Field(
        default=None,
        description='e.g. "https://ghe.example.com/api/v3" for GH Enterprise',
    )
    timeout: float | None = None
    per_page: int | None = None

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

    def get_token(self) -> str:
        return self.client.get_token()

    @classmethod
    def build_with_config(
        cls,
        config: GitHubConfig,
    ) -> "GitHubClient":
        client = config.create_client()
        client.create_client()
        return cls(client)

    @classmethod
    async def build_from_toolset(
        cls,
        toolset_config: dict[str, Any],
        logger: logging.Logger,
    ) -> "GitHubClient":
        """Build GitHubClient from toolset configuration (new architecture).

        Toolset configs are stored per-user at:
        /services/toolsets/{user_id}/{toolset_type}

        Args:
            toolset_config: Toolset configuration dictionary from etcd
            logger: Logger instance

        Returns:
            GitHubClient instance
        """
        try:
            if not toolset_config:
                raise ValueError("Toolset config is required for GitHub client")

            credentials_config = toolset_config.get("credentials", {}) or {}
            token = credentials_config.get("access_token", "")
            if not token:
                raise ValueError("Access token required for GitHub client (OAuth)")

            client = GitHubClientViaToken(token=token, per_page=90)
            client.create_client()
            logger.info("Built GitHub client from toolset config")
            return cls(client)

        except Exception as e:
            logger.error(f"Failed to build GitHub client from toolset config: {str(e)}")
            raise

    @classmethod
    async def build_from_services(
        cls,
        logger: logging.Logger,
        config_service: ConfigurationService,
        connector_instance_id: str | None = None,
    ) -> "GitHubClient":
        """Build GitHubClient using configuration service
        Args:
            logger: Logger instance
            config_service: Configuration service instance
        Returns:
            GitHubClient instance
        """
        try:
            config = await cls._get_connector_config(logger, config_service, connector_instance_id)
            if not config:
                raise ValueError("Failed to get GitHub connector configuration")
            auth_config = config.get("auth", {})
            if not auth_config:
                raise ValueError ("Auth configuration not found in Github connector configuration")

            credentials_config  = config.get("credentials",{})
            if not  credentials_config:
                raise ValueError("Credentials configuration not found in Github connector configuration")
            # Extract configuration values
            auth_type = auth_config.get("authType", "API_TOKEN")  # API_TOKEN or OAUTH

            if auth_type == "API_TOKEN":
                token = auth_config.get("token", "")
                if not token:
                    raise ValueError("Token required for token auth type")

                client_via_token = GitHubClientViaToken(token=token,per_page=90)
                client_via_token.create_client()
                client =client_via_token
            elif auth_type == "OAUTH":
                access_token =credentials_config.get("access_token","")
                if not access_token:
                    raise ValueError("Access token required for OAuth auth type")

                client_via_token = GitHubClientViaToken(token=access_token,per_page=90)
                client_via_token.create_client()
                client =client_via_token
            else:
                raise ValueError(f"Invalid auth type: {auth_type}")
            return cls(client)
        except Exception as e:
            logger.error(f"Failed to build Github client from services: {str(e)}")
            raise

    @staticmethod
    async def _get_connector_config(logger: logging.Logger, config_service: ConfigurationService, connector_instance_id: str | None = None) -> dict[str, Any]:
        """Fetch connector config from etcd for GitHub."""
        try:
            config = await config_service.get_config(f"/services/connectors/{connector_instance_id}/config")
            if not config:
                raise ValueError(f"Failed to get GitHub connector configuration for instance {connector_instance_id}")
            return config
        except Exception as e:
            logger.error(f"Failed to get GitHub connector config: {e}")
            raise ValueError(f"Failed to get GitHub connector configuration for instance {connector_instance_id}") from e
