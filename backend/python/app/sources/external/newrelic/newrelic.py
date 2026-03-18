"""
NewRelic NerdGraph DataSource - GraphQL API wrapper.

Provides typed wrapper methods for common NewRelic NerdGraph operations
including accounts, entities, NRQL queries, dashboards, alert policies,
synthetics monitors, and APM applications.

All methods return GraphQLResponse objects.
"""

from typing import Any

from app.sources.client.graphql.response import GraphQLResponse
from app.sources.client.newrelic.graphql_op import NewRelicGraphQLOperations
from app.sources.client.newrelic.newrelic import NewRelicClient


class NewRelicDataSource:
    """NewRelic NerdGraph DataSource

    Async wrapper for NewRelic NerdGraph (GraphQL) operations.

    Coverage:
    - Accounts: list, get
    - NRQL queries
    - Entities: list (search), get
    - Dashboards: list
    - Alert policies: list
    - Synthetics monitors: list
    - APM applications: get
    """

    def __init__(self, newrelic_client: NewRelicClient) -> None:
        """Initialize the NewRelic NerdGraph data source.

        Args:
            newrelic_client: NewRelicClient instance
        """
        self._client = newrelic_client

    def get_data_source(self) -> "NewRelicDataSource":
        """Return the data source instance."""
        return self

    def get_client(self) -> NewRelicClient:
        """Return the underlying NewRelicClient."""
        return self._client

    # =========================================================================
    # ACCOUNT OPERATIONS
    # =========================================================================

    async def list_accounts(self) -> GraphQLResponse:
        """List all accessible accounts.

        Returns:
            GraphQLResponse with account data under
            actor.accounts
        """
        query = NewRelicGraphQLOperations.get_operation_with_fragments(
            "query", "list_accounts"
        )
        variables: dict[str, Any] = {}

        try:
            return await self._client.get_client().execute(
                query=query,
                variables=variables,
                operation_name="listAccounts",
            )
        except Exception as e:
            return GraphQLResponse(
                success=False,
                message=f"Failed to list accounts: {str(e)}",
            )

    async def get_account(self, account_id: int) -> GraphQLResponse:
        """Get a specific account by ID.

        Args:
            account_id: NewRelic account ID

        Returns:
            GraphQLResponse with account data under
            actor.account
        """
        query = NewRelicGraphQLOperations.get_operation_with_fragments(
            "query", "get_account"
        )
        variables: dict[str, Any] = {"accountId": account_id}

        try:
            return await self._client.get_client().execute(
                query=query,
                variables=variables,
                operation_name="getAccount",
            )
        except Exception as e:
            return GraphQLResponse(
                success=False,
                message=f"Failed to get account: {str(e)}",
            )

    # =========================================================================
    # NRQL QUERY OPERATIONS
    # =========================================================================

    async def nrql_query(
        self, account_id: int, nrql_query: str
    ) -> GraphQLResponse:
        """Execute a NRQL query against an account.

        Args:
            account_id: NewRelic account ID
            nrql_query: NRQL query string (e.g., "SELECT count(*) FROM Transaction")

        Returns:
            GraphQLResponse with query results under
            actor.account.nrql.results
        """
        query = NewRelicGraphQLOperations.get_operation_with_fragments(
            "query", "nrql_query"
        )
        variables: dict[str, Any] = {
            "accountId": account_id,
            "nrqlQuery": nrql_query,
        }

        try:
            return await self._client.get_client().execute(
                query=query,
                variables=variables,
                operation_name="nrqlQuery",
            )
        except Exception as e:
            return GraphQLResponse(
                success=False,
                message=f"Failed to execute NRQL query: {str(e)}",
            )

    # =========================================================================
    # ENTITY OPERATIONS
    # =========================================================================

    async def list_entities(
        self,
        query_string: str | None = None,
        entity_types: list[str] | None = None,
    ) -> GraphQLResponse:
        """Search for entities with optional filters.

        Args:
            query_string: Search query string for entity names
            entity_types: Filter by entity types
                (e.g., ['APPLICATION', 'HOST', 'DASHBOARD'])

        Returns:
            GraphQLResponse with entity search results under
            actor.entitySearch.results.entities
        """
        query = NewRelicGraphQLOperations.get_operation_with_fragments(
            "query", "list_entities"
        )
        variables: dict[str, Any] = {}
        if query_string:
            variables["queryString"] = query_string
        if entity_types:
            variables["entityTypes"] = entity_types

        try:
            return await self._client.get_client().execute(
                query=query,
                variables=variables,
                operation_name="listEntities",
            )
        except Exception as e:
            return GraphQLResponse(
                success=False,
                message=f"Failed to list entities: {str(e)}",
            )

    async def get_entity(self, guid: str) -> GraphQLResponse:
        """Get a specific entity by GUID.

        Args:
            guid: Entity GUID

        Returns:
            GraphQLResponse with entity data under
            actor.entity
        """
        query = NewRelicGraphQLOperations.get_operation_with_fragments(
            "query", "get_entity"
        )
        variables: dict[str, Any] = {"guid": guid}

        try:
            return await self._client.get_client().execute(
                query=query,
                variables=variables,
                operation_name="getEntity",
            )
        except Exception as e:
            return GraphQLResponse(
                success=False,
                message=f"Failed to get entity: {str(e)}",
            )

    # =========================================================================
    # DASHBOARD OPERATIONS
    # =========================================================================

    async def list_dashboards(self) -> GraphQLResponse:
        """List all dashboards.

        Returns:
            GraphQLResponse with dashboard entities under
            actor.entitySearch.results.entities
        """
        query = NewRelicGraphQLOperations.get_operation_with_fragments(
            "query", "list_dashboards"
        )
        variables: dict[str, Any] = {}

        try:
            return await self._client.get_client().execute(
                query=query,
                variables=variables,
                operation_name="listDashboards",
            )
        except Exception as e:
            return GraphQLResponse(
                success=False,
                message=f"Failed to list dashboards: {str(e)}",
            )

    # =========================================================================
    # ALERT POLICY OPERATIONS
    # =========================================================================

    async def list_alert_policies(
        self,
        account_id: int,
        cursor: str | None = None,
    ) -> GraphQLResponse:
        """List alert policies for an account.

        Args:
            account_id: NewRelic account ID
            cursor: Pagination cursor for next page

        Returns:
            GraphQLResponse with alert policies under
            actor.account.alerts.policiesSearch.policies
        """
        query = NewRelicGraphQLOperations.get_operation_with_fragments(
            "query", "list_alert_policies"
        )
        variables: dict[str, Any] = {"accountId": account_id}
        if cursor:
            variables["cursor"] = cursor

        try:
            return await self._client.get_client().execute(
                query=query,
                variables=variables,
                operation_name="listAlertPolicies",
            )
        except Exception as e:
            return GraphQLResponse(
                success=False,
                message=f"Failed to list alert policies: {str(e)}",
            )

    # =========================================================================
    # SYNTHETICS MONITOR OPERATIONS
    # =========================================================================

    async def list_synthetics_monitors(self) -> GraphQLResponse:
        """List synthetics monitors.

        Returns:
            GraphQLResponse with synthetic monitor entities under
            actor.entitySearch.results.entities
        """
        query = NewRelicGraphQLOperations.get_operation_with_fragments(
            "query", "list_synthetics_monitors"
        )
        variables: dict[str, Any] = {}

        try:
            return await self._client.get_client().execute(
                query=query,
                variables=variables,
                operation_name="listSyntheticsMonitors",
            )
        except Exception as e:
            return GraphQLResponse(
                success=False,
                message=f"Failed to list synthetics monitors: {str(e)}",
            )

    # =========================================================================
    # APM APPLICATION OPERATIONS
    # =========================================================================

    async def get_application(self, guid: str) -> GraphQLResponse:
        """Get APM application details by GUID.

        Args:
            guid: Entity GUID for the APM application

        Returns:
            GraphQLResponse with APM application data under
            actor.entity
        """
        query = NewRelicGraphQLOperations.get_operation_with_fragments(
            "query", "get_application"
        )
        variables: dict[str, Any] = {"guid": guid}

        try:
            return await self._client.get_client().execute(
                query=query,
                variables=variables,
                operation_name="getApplication",
            )
        except Exception as e:
            return GraphQLResponse(
                success=False,
                message=f"Failed to get application: {str(e)}",
            )
