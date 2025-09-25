from dataclasses import asdict, dataclass
from typing import Any

from github import Auth, Github

from app.sources.client.iclient import IClient


# Standardized Github API response wrapper
@dataclass
class GitHubResponse:
    success: bool
    data: dict[str, Any] | None = None
    error: str | None = None
    message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


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

    def get_base_url(self) -> str | None:
        return self.base_url


@dataclass
class GitHubConfig:
    token: str
    base_url: str | None = (
        None  # e.g. "https://ghe.example.com/api/v3" for GH Enterprise
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

    @classmethod
    def build_with_config(
        cls,
        config: GitHubConfig,
    ) -> "GitHubClient":
        client = config.create_client()
        client.create_client()
        return cls(client)
