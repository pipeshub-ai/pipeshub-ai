"""Read-only queries against Drupal Wiki's internal GraphQL API.

Operation names and field selections mirror the wiki's own web UI, so a wiki that
accepts the token answers these exactly as it answers its own front end. Everything
here is unavailable through the public REST API:

* space members                  -> who may read a private space
* space content structure        -> the page tree

The API is undocumented, so every method returns plain Python structures and
raises a ``DrupalWikiGraphQLError`` on failure; callers decide whether to fail
closed (permissions) or carry on without the data (tree). Its subclasses tell a
wiki that refuses the operation outright from a token it rejects and from a
failure that may pass.
"""

from typing import Any

from app.config.constants.http_status_code import HttpStatusCode
from app.sources.client.drupal_wiki.drupal_wiki import describe_exception
from app.sources.client.drupal_wiki.graphql import DrupalWikiGraphQLClient
from app.sources.client.graphql.response import GraphQLResponse

# Only the fields the connector reads are selected: an unknown field fails the whole
# operation, so asking for anything spare is a live failure trigger buying nothing.
SPACE_MEMBERS_QUERY = """
query RoleMemberManagerData($spaceId: ID!) {
  spaceRoleUserMembers(spaceId: $spaceId) {
    id
    email
  }
  spaceRoleGroupMembers(spaceId: $spaceId) {
    id
  }
}
"""

SPACE_TREE_QUERY = """
query SpaceContentStructure($spaceId: ID!) {
  spaceContentStructureFlatTree(spaceId: $spaceId) {
    id
    pageId
    parentId
    title
    position
  }
}
"""


# Drupal Wiki 8.44 accepts only the persisted queries its web UI ships (trusted
# documents). It answers any inline query with HTTP 400 ``{"errors": [{"message":
# "QueryNotAllowed", "extensions": {"code": "QUERY_NOT_ALLOWED"}}]}``, and a hash it does
# not know with ``PERSISTED_QUERY_NOT_FOUND``. Older wikis put the inline verdict in
# ``extensions.classification``.
QUERY_NOT_ALLOWED_CODE = "QUERY_NOT_ALLOWED"
PERSISTED_QUERY_NOT_FOUND_CODE = "PERSISTED_QUERY_NOT_FOUND"
PERSISTED_QUERY_NOT_SUPPORTED_CODE = "PERSISTED_QUERY_NOT_SUPPORTED"
REFUSAL_CODES = frozenset({
    QUERY_NOT_ALLOWED_CODE, PERSISTED_QUERY_NOT_FOUND_CODE, PERSISTED_QUERY_NOT_SUPPORTED_CODE,
})
QUERY_NOT_ALLOWED_CLASSIFICATION = "QueryNotAllowed"
AUTH_STATUSES = frozenset({HttpStatusCode.UNAUTHORIZED.value, HttpStatusCode.FORBIDDEN.value})


class DrupalWikiGraphQLError(RuntimeError):
    """A GraphQL call failed or returned errors."""


class DrupalWikiGraphQLRefusedError(DrupalWikiGraphQLError):
    """The wiki does not allow this operation for the token. Asking again will not help.

    ``kind`` is the wiki's code (one of ``REFUSAL_CODES``): an inline query refused, or a
    persisted query it does not know or does not support. They need different fixes.
    """

    def __init__(self, message: str, kind: str = QUERY_NOT_ALLOWED_CODE) -> None:
        super().__init__(message)
        self.kind = kind


class DrupalWikiGraphQLAuthError(DrupalWikiGraphQLError):
    """The wiki rejected the token on ``/graphql`` (HTTP 401 or 403)."""


class DrupalWikiGraphQLTransientError(DrupalWikiGraphQLError):
    """A failure that may pass: a 5xx or 429 left after the retries, a timeout, a dropped connection."""


def _refusal_kind(response: GraphQLResponse) -> str | None:
    for error in response.errors or []:
        extensions = error.extensions or {}
        if extensions.get("code") in REFUSAL_CODES:
            return str(extensions["code"])
        if extensions.get("classification") == QUERY_NOT_ALLOWED_CLASSIFICATION:
            return QUERY_NOT_ALLOWED_CODE
    return None


def _response_error(response: GraphQLResponse, message: str) -> DrupalWikiGraphQLError:
    """The refusal wins over the status: the wiki sends it with a 400 today, but may not tomorrow."""
    kind = _refusal_kind(response)
    if kind is not None:
        return DrupalWikiGraphQLRefusedError(message, kind)
    status = response.status_code
    if status in AUTH_STATUSES:
        return DrupalWikiGraphQLAuthError(message)
    if status is not None and (
        status >= HttpStatusCode.INTERNAL_SERVER_ERROR.value
        or status == HttpStatusCode.TOO_MANY_REQUESTS.value
    ):
        return DrupalWikiGraphQLTransientError(message)
    return DrupalWikiGraphQLError(message)


def _exception_type(error: BaseException) -> type[DrupalWikiGraphQLError]:
    status = getattr(getattr(error, "response", None), "status_code", None)
    return DrupalWikiGraphQLAuthError if status in AUTH_STATUSES else DrupalWikiGraphQLTransientError


class DrupalWikiGraphQLDataSource:
    """Typed wrappers around the internal GraphQL operations the connector needs."""

    def __init__(self, client: DrupalWikiGraphQLClient) -> None:
        self._client = client.get_client()

    async def _query(
        self,
        query: str,
        operation_name: str,
        variables: dict[str, Any],
    ) -> dict[str, Any]:
        try:
            response = await self._client.execute(query, variables=variables, operation_name=operation_name)
        except Exception as e:
            # A timeout or dropped connection is still a failure of this one query; the
            # caller decides per space.
            raise _exception_type(e)(f"{operation_name} failed: {describe_exception(e)}") from e
        if not response.success or response.data is None:
            reason = response.message or f"the reply carried no data (HTTP {response.status_code})"
            raise _response_error(response, f"{operation_name} failed: {reason}")
        return response.data

    async def get_space_members(self, space_id: int) -> dict[str, Any]:
        """Members of one space: ``{"users": [{id, email}], "groups": [{id}]}``.

        A space's access status comes from the REST listing, not from here.
        """
        data = await self._query(
            SPACE_MEMBERS_QUERY, "RoleMemberManagerData", {"spaceId": str(space_id)}
        )
        return {
            "users": data.get("spaceRoleUserMembers") or [],
            "groups": data.get("spaceRoleGroupMembers") or [],
        }

    async def get_space_tree(self, space_id: int) -> list[dict[str, Any]]:
        """Flat page tree of one space: ``pageId``, ``parentId``, ``position``, ``title``."""
        data = await self._query(
            SPACE_TREE_QUERY, "SpaceContentStructure", {"spaceId": str(space_id)}
        )
        return data.get("spaceContentStructureFlatTree") or []

