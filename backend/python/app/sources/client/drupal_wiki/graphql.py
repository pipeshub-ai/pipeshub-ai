"""Client for Drupal Wiki's internal GraphQL API.

The public REST API exposes no space members, page tree, sharing or trash, so the
connector reads those from ``/graphql`` — the same endpoint the wiki's own web UI
uses. The endpoint is undocumented, so a query that fails is reported per space
rather than failing the whole sync.

Operations are posted through the REST client rather than a client of their own, so
they carry the same token and cookie policy and count against the same rate limit,
retries and ``Retry-After`` pauses as every REST call to the wiki.
"""

from typing import Any

from app.sources.client.drupal_wiki.drupal_wiki import (
    DrupalWikiRESTClientViaToken,
    describe_response,
    problem_reason,
)
from app.sources.client.graphql.response import GraphQLResponse
from app.sources.client.http.http_request import HTTPRequest
from app.sources.client.iclient import IClient

GRAPHQL_PATH = "/graphql"


class DrupalWikiGraphQLClientViaToken:
    """Posts GraphQL operations to ``/graphql`` through the REST client."""

    def __init__(self, rest_client: DrupalWikiRESTClientViaToken) -> None:
        self._rest = rest_client
        self.endpoint = f"{rest_client.get_base_url().rstrip('/')}{GRAPHQL_PATH}"

    @property
    def headers(self) -> dict[str, str]:
        return self._rest.headers

    def get_auth_header(self) -> str | None:
        return self._rest.headers.get("Authorization")

    def get_base_url(self) -> str:
        return self._rest.get_base_url()

    async def execute(
        self,
        query: str,
        variables: dict[str, Any] | None = None,
        operation_name: str | None = None,
    ) -> GraphQLResponse:
        """Run one operation. A transport failure left after the retries raises."""
        payload: dict[str, Any] = {"query": query, "variables": variables or {}}
        if operation_name:
            payload["operationName"] = operation_name
        response = await self._rest.execute(HTTPRequest(
            method="POST",
            url=self.endpoint,
            headers={"Content-Type": "application/json"},
            body=payload,
        ))
        try:
            body: object = response.json()
        except ValueError:
            # A proxy or login page answering in HTML, or a cut-off body.
            return GraphQLResponse(
                success=False,
                message=f"the wiki did not answer with JSON: {describe_response(response)}",
                status_code=response.status,
            )
        if not isinstance(body, dict):
            return GraphQLResponse(
                success=False,
                message=f"unexpected reply shape (HTTP {response.status})",
                status_code=response.status,
            )
        result = GraphQLResponse.from_response(body, status_code=response.status)
        if not result.success:
            # from_response keeps only the first error's message, and nothing at all for
            # a REST-style ProblemResponse (a 401 or 429 from the gateway); carry the
            # wiki's code, reason and status instead.
            reason = problem_reason(body)
            result.message = (
                f"{reason} (HTTP {response.status})" if reason else describe_response(response)
            )
        return result

    async def close(self) -> None:
        """Nothing of its own to close: the connection belongs to the REST client."""


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
        """Share the REST client's wiki URL, token, connection and resilience policy."""
        return cls(DrupalWikiGraphQLClientViaToken(rest_client))

    async def close(self) -> None:
        await self.client.close()
