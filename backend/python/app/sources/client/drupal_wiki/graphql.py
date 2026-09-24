"""Client for Drupal Wiki's internal GraphQL API.

The public REST API exposes no space members, page tree, sharing or trash, so the
connector reads those from ``/graphql`` — the same endpoint the wiki's own web UI
uses. The endpoint is undocumented, so a query that fails is reported per space
rather than failing the whole sync.
"""


from app.sources.client.drupal_wiki.drupal_wiki import (
    ACCEPT_HEADER,
    API_VERSION,
    DrupalWikiRESTClientViaToken,
)
from app.sources.client.graphql.client import GraphQLClient
from app.sources.client.iclient import IClient

GRAPHQL_PATH = "/graphql"


class DrupalWikiGraphQLClientViaToken(GraphQLClient):
    """Sends the same bearer token the REST client uses against ``/graphql``."""

    def __init__(self, base_url: str, token: str, timeout: int = 30) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        super().__init__(
            endpoint=f"{self.base_url}{GRAPHQL_PATH}",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "Accept": ACCEPT_HEADER,
                "X-API-Version": API_VERSION,
            },
            timeout=timeout,
        )

    def get_auth_header(self) -> str | None:
        return self.headers.get("Authorization")

    def get_base_url(self) -> str:
        return self.base_url

    def set_token(self, token: str) -> None:
        self.token = token
        self.headers["Authorization"] = f"Bearer {token}"


class DrupalWikiGraphQLClient(IClient):
    """Builder for the internal GraphQL client."""

    def __init__(self, client: DrupalWikiGraphQLClientViaToken) -> None:
        self.client = client

    def get_client(self) -> DrupalWikiGraphQLClientViaToken:
        return self.client

    @classmethod
    def build_from_rest_client(
        cls,
        rest_client: DrupalWikiRESTClientViaToken,
    ) -> "DrupalWikiGraphQLClient":
        """Reuse the REST client's wiki URL and already-normalized token."""
        return cls(
            DrupalWikiGraphQLClientViaToken(
                base_url=rest_client.get_base_url(),
                token=rest_client.get_token(),
            )
        )

    async def close(self) -> None:
        await self.client.close()
