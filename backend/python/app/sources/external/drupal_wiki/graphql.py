"""Read-only queries against Drupal Wiki's internal GraphQL API.

Operation names and field selections mirror the wiki's own web UI, so a wiki that
accepts the token answers these exactly as it answers its own front end. Everything
here is unavailable through the public REST API:

* space members                  -> who may read a private space
* space content structure        -> the page tree

The API is undocumented, so every method returns plain Python structures and
raises ``DrupalWikiGraphQLError`` on failure; callers decide whether to fail
closed (permissions) or carry on without the data (tree).
"""

from typing import Any

from app.sources.client.drupal_wiki.graphql import DrupalWikiGraphQLClient

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


class DrupalWikiGraphQLError(RuntimeError):
    """A GraphQL call failed or returned errors."""


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
        response = await self._client.execute(query, variables=variables, operation_name=operation_name)
        if not response.success or response.data is None:
            raise DrupalWikiGraphQLError(
                f"{operation_name} failed (status {response.status_code}): {response.message}"
            )
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

