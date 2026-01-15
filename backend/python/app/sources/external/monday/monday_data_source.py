from typing import Any, Dict, List, Optional

from app.sources.client.graphql.response import GraphQLResponse
from app.sources.client.monday.graphql_op import MondayGraphQLOperations
from app.sources.client.monday.monday import MondayClient


class MondayDataSource:
    """
    Complete Monday.com GraphQL API client wrapper
    Auto-generated wrapper for Monday.com GraphQL operations.

    This class provides unified access to all Monday.com GraphQL operations while
    maintaining proper typing and error handling.

    Coverage:
    - Total GraphQL operations: 71
    - Queries: 20
    - Mutations: 51
    - Auto-generated from Monday.com GraphQL schema
    """

    def __init__(self, monday_client: MondayClient) -> None:
        """
        Initialize the Monday.com GraphQL data source.

        Args:
            monday_client (MondayClient): Monday.com client instance
        """
        self._monday_client = monday_client

    # =============================================================================
    # QUERY OPERATIONS
    # =============================================================================

    async def me(self) -> GraphQLResponse:
        """Get current user information

        GraphQL Operation: Query me

        Returns:
            GraphQLResponse: The GraphQL response containing the operation result

        Example:
            await monday_datasource.me()
        """
        # Get the complete GraphQL operation with fragments
        query = MondayGraphQLOperations.get_operation_with_fragments("query", "me")

        # Prepare variables
        variables = {}

        # Execute the GraphQL operation
        try:
            response = await self._monday_client.get_graphql_client().execute(
                query=query,
                variables=variables,
                operation_name="me"
            )
            return response
        except Exception as e:
            return GraphQLResponse(
                success=False,
                message=f"Failed to execute query me: {str(e)}"
            )

    async def users(
        self,
        limit: Optional[int] = None,
        page: Optional[int] = None,
        kind: Optional[str] = None,
        emails: Optional[List[str]] = None,
        ids: Optional[List[str]] = None,
        name: Optional[str] = None,
        newest_first: Optional[bool] = None
    ) -> GraphQLResponse:
        """Get all users

        GraphQL Operation: Query users

        Args:
            limit (int, optional): Parameter for limit
            page (int, optional): Parameter for page
            kind (str, optional): Parameter for kind
            emails (List[str], optional): Parameter for emails
            ids (List[str], optional): Parameter for ids
            name (str, optional): Parameter for name
            newest_first (bool, optional): Parameter for newest_first

        Returns:
            GraphQLResponse: The GraphQL response containing the operation result

        Example:
            await monday_datasource.users(limit=50)
        """
        # Get the complete GraphQL operation with fragments
        query = MondayGraphQLOperations.get_operation_with_fragments("query", "users")

        # Prepare variables
        variables = {}
        if limit is not None:
            variables["limit"] = limit
        if page is not None:
            variables["page"] = page
        if kind is not None:
            variables["kind"] = kind
        if emails is not None:
            variables["emails"] = emails
        if ids is not None:
            variables["ids"] = ids
        if name is not None:
            variables["name"] = name
        if newest_first is not None:
            variables["newest_first"] = newest_first

        # Execute the GraphQL operation
        try:
            response = await self._monday_client.get_graphql_client().execute(
                query=query,
                variables=variables,
                operation_name="users"
            )
            return response
        except Exception as e:
            return GraphQLResponse(
                success=False,
                message=f"Failed to execute query users: {str(e)}"
            )

    async def account(self) -> GraphQLResponse:
        """Get account information

        GraphQL Operation: Query account

        Returns:
            GraphQLResponse: The GraphQL response containing the operation result

        Example:
            await monday_datasource.account()
        """
        # Get the complete GraphQL operation with fragments
        query = MondayGraphQLOperations.get_operation_with_fragments("query", "account")

        # Prepare variables
        variables = {}

        # Execute the GraphQL operation
        try:
            response = await self._monday_client.get_graphql_client().execute(
                query=query,
                variables=variables,
                operation_name="account"
            )
            return response
        except Exception as e:
            return GraphQLResponse(
                success=False,
                message=f"Failed to execute query account: {str(e)}"
            )

    async def boards(
        self,
        limit: Optional[int] = None,
        page: Optional[int] = None,
        board_kind: Optional[str] = None,
        ids: Optional[List[str]] = None,
        order_by: Optional[str] = None,
        state: Optional[str] = None,
        workspace_ids: Optional[List[str]] = None
    ) -> GraphQLResponse:
        """Get all boards

        GraphQL Operation: Query boards

        Args:
            limit (int, optional): Parameter for limit
            page (int, optional): Parameter for page
            board_kind (str, optional): Parameter for board_kind
            ids (List[str], optional): Parameter for ids
            order_by (str, optional): Parameter for order_by
            state (str, optional): Parameter for state
            workspace_ids (List[str], optional): Parameter for workspace_ids

        Returns:
            GraphQLResponse: The GraphQL response containing the operation result

        Example:
            await monday_datasource.boards(limit=50)
        """
        # Get the complete GraphQL operation with fragments
        query = MondayGraphQLOperations.get_operation_with_fragments("query", "boards")

        # Prepare variables
        variables = {}
        if limit is not None:
            variables["limit"] = limit
        if page is not None:
            variables["page"] = page
        if board_kind is not None:
            variables["board_kind"] = board_kind
        if ids is not None:
            variables["ids"] = ids
        if order_by is not None:
            variables["order_by"] = order_by
        if state is not None:
            variables["state"] = state
        if workspace_ids is not None:
            variables["workspace_ids"] = workspace_ids

        # Execute the GraphQL operation
        try:
            response = await self._monday_client.get_graphql_client().execute(
                query=query,
                variables=variables,
                operation_name="boards"
            )
            return response
        except Exception as e:
            return GraphQLResponse(
                success=False,
                message=f"Failed to execute query boards: {str(e)}"
            )

    async def items(
        self,
        ids: List[str],
        limit: Optional[int] = None,
        page: Optional[int] = None,
        newest_first: Optional[bool] = None
    ) -> GraphQLResponse:
        """Get items by IDs

        GraphQL Operation: Query items

        Args:
            ids (List[str], required): Parameter for ids
            limit (int, optional): Parameter for limit
            page (int, optional): Parameter for page
            newest_first (bool, optional): Parameter for newest_first

        Returns:
            GraphQLResponse: The GraphQL response containing the operation result

        Example:
            await monday_datasource.items(ids=["123", "456"])
        """
        # Get the complete GraphQL operation with fragments
        query = MondayGraphQLOperations.get_operation_with_fragments("query", "items")

        # Prepare variables
        variables = {}
        if ids is not None:
            variables["ids"] = ids
        if limit is not None:
            variables["limit"] = limit
        if page is not None:
            variables["page"] = page
        if newest_first is not None:
            variables["newest_first"] = newest_first

        # Execute the GraphQL operation
        try:
            response = await self._monday_client.get_graphql_client().execute(
                query=query,
                variables=variables,
                operation_name="items"
            )
            return response
        except Exception as e:
            return GraphQLResponse(
                success=False,
                message=f"Failed to execute query items: {str(e)}"
            )

    async def items_page_by_column_values(
        self,
        board_id: str,
        columns: List[Dict[str, Any]],
        limit: Optional[int] = None,
        cursor: Optional[str] = None
    ) -> GraphQLResponse:
        """Get items by column values

        GraphQL Operation: Query items_page_by_column_values

        Args:
            board_id (str, required): Parameter for board_id
            columns (List[Dict[str, Any]], required): Parameter for columns
            limit (int, optional): Parameter for limit
            cursor (str, optional): Parameter for cursor

        Returns:
            GraphQLResponse: The GraphQL response containing the operation result

        Example:
            await monday_datasource.items_page_by_column_values(board_id="123", columns=[{"column_id": "status", "column_values": ["Done"]}])
        """
        # Get the complete GraphQL operation with fragments
        query = MondayGraphQLOperations.get_operation_with_fragments("query", "items_page_by_column_values")

        # Prepare variables
        variables = {}
        if board_id is not None:
            variables["board_id"] = board_id
        if columns is not None:
            variables["columns"] = columns
        if limit is not None:
            variables["limit"] = limit
        if cursor is not None:
            variables["cursor"] = cursor

        # Execute the GraphQL operation
        try:
            response = await self._monday_client.get_graphql_client().execute(
                query=query,
                variables=variables,
                operation_name="items_page_by_column_values"
            )
            return response
        except Exception as e:
            return GraphQLResponse(
                success=False,
                message=f"Failed to execute query items_page_by_column_values: {str(e)}"
            )

    async def next_items_page(
        self,
        cursor: str,
        limit: Optional[int] = None
    ) -> GraphQLResponse:
        """Get next page of items

        GraphQL Operation: Query next_items_page

        Args:
            cursor (str, required): Parameter for cursor
            limit (int, optional): Parameter for limit

        Returns:
            GraphQLResponse: The GraphQL response containing the operation result

        Example:
            await monday_datasource.next_items_page(cursor="abc123")
        """
        # Get the complete GraphQL operation with fragments
        query = MondayGraphQLOperations.get_operation_with_fragments("query", "next_items_page")

        # Prepare variables
        variables = {}
        if cursor is not None:
            variables["cursor"] = cursor
        if limit is not None:
            variables["limit"] = limit

        # Execute the GraphQL operation
        try:
            response = await self._monday_client.get_graphql_client().execute(
                query=query,
                variables=variables,
                operation_name="next_items_page"
            )
            return response
        except Exception as e:
            return GraphQLResponse(
                success=False,
                message=f"Failed to execute query next_items_page: {str(e)}"
            )

    async def workspaces(
        self,
        limit: Optional[int] = None,
        page: Optional[int] = None,
        ids: Optional[List[str]] = None,
        kind: Optional[str] = None,
        state: Optional[str] = None
    ) -> GraphQLResponse:
        """Get all workspaces

        GraphQL Operation: Query workspaces

        Args:
            limit (int, optional): Parameter for limit
            page (int, optional): Parameter for page
            ids (List[str], optional): Parameter for ids
            kind (str, optional): Parameter for kind
            state (str, optional): Parameter for state

        Returns:
            GraphQLResponse: The GraphQL response containing the operation result

        Example:
            await monday_datasource.workspaces(limit=50)
        """
        # Get the complete GraphQL operation with fragments
        query = MondayGraphQLOperations.get_operation_with_fragments("query", "workspaces")

        # Prepare variables
        variables = {}
        if limit is not None:
            variables["limit"] = limit
        if page is not None:
            variables["page"] = page
        if ids is not None:
            variables["ids"] = ids
        if kind is not None:
            variables["kind"] = kind
        if state is not None:
            variables["state"] = state

        # Execute the GraphQL operation
        try:
            response = await self._monday_client.get_graphql_client().execute(
                query=query,
                variables=variables,
                operation_name="workspaces"
            )
            return response
        except Exception as e:
            return GraphQLResponse(
                success=False,
                message=f"Failed to execute query workspaces: {str(e)}"
            )

    async def teams(
        self,
        ids: Optional[List[str]] = None
    ) -> GraphQLResponse:
        """Get all teams

        GraphQL Operation: Query teams

        Args:
            ids (List[str], optional): Parameter for ids

        Returns:
            GraphQLResponse: The GraphQL response containing the operation result

        Example:
            await monday_datasource.teams()
        """
        # Get the complete GraphQL operation with fragments
        query = MondayGraphQLOperations.get_operation_with_fragments("query", "teams")

        # Prepare variables
        variables = {}
        if ids is not None:
            variables["ids"] = ids

        # Execute the GraphQL operation
        try:
            response = await self._monday_client.get_graphql_client().execute(
                query=query,
                variables=variables,
                operation_name="teams"
            )
            return response
        except Exception as e:
            return GraphQLResponse(
                success=False,
                message=f"Failed to execute query teams: {str(e)}"
            )

    async def tags(
        self,
        ids: Optional[List[str]] = None
    ) -> GraphQLResponse:
        """Get all tags

        GraphQL Operation: Query tags

        Args:
            ids (List[str], optional): Parameter for ids

        Returns:
            GraphQLResponse: The GraphQL response containing the operation result

        Example:
            await monday_datasource.tags()
        """
        # Get the complete GraphQL operation with fragments
        query = MondayGraphQLOperations.get_operation_with_fragments("query", "tags")

        # Prepare variables
        variables = {}
        if ids is not None:
            variables["ids"] = ids

        # Execute the GraphQL operation
        try:
            response = await self._monday_client.get_graphql_client().execute(
                query=query,
                variables=variables,
                operation_name="tags"
            )
            return response
        except Exception as e:
            return GraphQLResponse(
                success=False,
                message=f"Failed to execute query tags: {str(e)}"
            )

    async def updates(
        self,
        limit: Optional[int] = None,
        page: Optional[int] = None
    ) -> GraphQLResponse:
        """Get updates

        GraphQL Operation: Query updates

        Args:
            limit (int, optional): Parameter for limit
            page (int, optional): Parameter for page

        Returns:
            GraphQLResponse: The GraphQL response containing the operation result

        Example:
            await monday_datasource.updates(limit=50)
        """
        # Get the complete GraphQL operation with fragments
        query = MondayGraphQLOperations.get_operation_with_fragments("query", "updates")

        # Prepare variables
        variables = {}
        if limit is not None:
            variables["limit"] = limit
        if page is not None:
            variables["page"] = page

        # Execute the GraphQL operation
        try:
            response = await self._monday_client.get_graphql_client().execute(
                query=query,
                variables=variables,
                operation_name="updates"
            )
            return response
        except Exception as e:
            return GraphQLResponse(
                success=False,
                message=f"Failed to execute query updates: {str(e)}"
            )

    async def docs(
        self,
        limit: Optional[int] = None,
        page: Optional[int] = None,
        object_ids: Optional[List[str]] = None,
        workspace_ids: Optional[List[str]] = None
    ) -> GraphQLResponse:
        """Get documents

        GraphQL Operation: Query docs

        Args:
            limit (int, optional): Parameter for limit
            page (int, optional): Parameter for page
            object_ids (List[str], optional): Parameter for object_ids
            workspace_ids (List[str], optional): Parameter for workspace_ids

        Returns:
            GraphQLResponse: The GraphQL response containing the operation result

        Example:
            await monday_datasource.docs(limit=50)
        """
        # Get the complete GraphQL operation with fragments
        query = MondayGraphQLOperations.get_operation_with_fragments("query", "docs")

        # Prepare variables
        variables = {}
        if limit is not None:
            variables["limit"] = limit
        if page is not None:
            variables["page"] = page
        if object_ids is not None:
            variables["object_ids"] = object_ids
        if workspace_ids is not None:
            variables["workspace_ids"] = workspace_ids

        # Execute the GraphQL operation
        try:
            response = await self._monday_client.get_graphql_client().execute(
                query=query,
                variables=variables,
                operation_name="docs"
            )
            return response
        except Exception as e:
            return GraphQLResponse(
                success=False,
                message=f"Failed to execute query docs: {str(e)}"
            )

    async def folders(
        self,
        limit: Optional[int] = None,
        page: Optional[int] = None,
        workspace_ids: Optional[List[str]] = None,
        ids: Optional[List[str]] = None
    ) -> GraphQLResponse:
        """Get folders

        GraphQL Operation: Query folders

        Args:
            limit (int, optional): Parameter for limit
            page (int, optional): Parameter for page
            workspace_ids (List[str], optional): Parameter for workspace_ids
            ids (List[str], optional): Parameter for ids

        Returns:
            GraphQLResponse: The GraphQL response containing the operation result

        Example:
            await monday_datasource.folders(limit=50)
        """
        # Get the complete GraphQL operation with fragments
        query = MondayGraphQLOperations.get_operation_with_fragments("query", "folders")

        # Prepare variables
        variables = {}
        if limit is not None:
            variables["limit"] = limit
        if page is not None:
            variables["page"] = page
        if workspace_ids is not None:
            variables["workspace_ids"] = workspace_ids
        if ids is not None:
            variables["ids"] = ids

        # Execute the GraphQL operation
        try:
            response = await self._monday_client.get_graphql_client().execute(
                query=query,
                variables=variables,
                operation_name="folders"
            )
            return response
        except Exception as e:
            return GraphQLResponse(
                success=False,
                message=f"Failed to execute query folders: {str(e)}"
            )

    async def app_subscription(self) -> GraphQLResponse:
        """Get app subscription information

        GraphQL Operation: Query app_subscription

        Returns:
            GraphQLResponse: The GraphQL response containing the operation result

        Example:
            await monday_datasource.app_subscription()
        """
        # Get the complete GraphQL operation with fragments
        query = MondayGraphQLOperations.get_operation_with_fragments("query", "app_subscription")

        # Prepare variables
        variables = {}

        # Execute the GraphQL operation
        try:
            response = await self._monday_client.get_graphql_client().execute(
                query=query,
                variables=variables,
                operation_name="app_subscription"
            )
            return response
        except Exception as e:
            return GraphQLResponse(
                success=False,
                message=f"Failed to execute query app_subscription: {str(e)}"
            )

    async def webhooks(
        self,
        board_id: str
    ) -> GraphQLResponse:
        """Get webhooks for a board

        GraphQL Operation: Query webhooks

        Args:
            board_id (str, required): Parameter for board_id

        Returns:
            GraphQLResponse: The GraphQL response containing the operation result

        Example:
            await monday_datasource.webhooks(board_id="123")
        """
        # Get the complete GraphQL operation with fragments
        query = MondayGraphQLOperations.get_operation_with_fragments("query", "webhooks")

        # Prepare variables
        variables = {}
        if board_id is not None:
            variables["board_id"] = board_id

        # Execute the GraphQL operation
        try:
            response = await self._monday_client.get_graphql_client().execute(
                query=query,
                variables=variables,
                operation_name="webhooks"
            )
            return response
        except Exception as e:
            return GraphQLResponse(
                success=False,
                message=f"Failed to execute query webhooks: {str(e)}"
            )

    async def boards_activity_logs(
        self,
        board_ids: List[str],
        limit: Optional[int] = None,
        page: Optional[int] = None,
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
        user_ids: Optional[List[str]] = None,
        column_ids: Optional[List[str]] = None,
        group_ids: Optional[List[str]] = None,
        item_ids: Optional[List[str]] = None
    ) -> GraphQLResponse:
        """Get activity logs for boards

        GraphQL Operation: Query boards_activity_logs

        Args:
            board_ids (List[str], required): Parameter for board_ids
            limit (int, optional): Parameter for limit
            page (int, optional): Parameter for page
            from_date (str, optional): Parameter for from_date
            to_date (str, optional): Parameter for to_date
            user_ids (List[str], optional): Parameter for user_ids
            column_ids (List[str], optional): Parameter for column_ids
            group_ids (List[str], optional): Parameter for group_ids
            item_ids (List[str], optional): Parameter for item_ids

        Returns:
            GraphQLResponse: The GraphQL response containing the operation result

        Example:
            await monday_datasource.boards_activity_logs(board_ids=["123"])
        """
        # Get the complete GraphQL operation with fragments
        query = MondayGraphQLOperations.get_operation_with_fragments("query", "boards_activity_logs")

        # Prepare variables
        variables = {}
        if board_ids is not None:
            variables["board_ids"] = board_ids
        if limit is not None:
            variables["limit"] = limit
        if page is not None:
            variables["page"] = page
        if from_date is not None:
            variables["from_date"] = from_date
        if to_date is not None:
            variables["to_date"] = to_date
        if user_ids is not None:
            variables["user_ids"] = user_ids
        if column_ids is not None:
            variables["column_ids"] = column_ids
        if group_ids is not None:
            variables["group_ids"] = group_ids
        if item_ids is not None:
            variables["item_ids"] = item_ids

        # Execute the GraphQL operation
        try:
            response = await self._monday_client.get_graphql_client().execute(
                query=query,
                variables=variables,
                operation_name="boards_activity_logs"
            )
            return response
        except Exception as e:
            return GraphQLResponse(
                success=False,
                message=f"Failed to execute query boards_activity_logs: {str(e)}"
            )

    async def version(self) -> GraphQLResponse:
        """Get API version information

        GraphQL Operation: Query version

        Returns:
            GraphQLResponse: The GraphQL response containing the operation result

        Example:
            await monday_datasource.version()
        """
        # Get the complete GraphQL operation with fragments
        query = MondayGraphQLOperations.get_operation_with_fragments("query", "version")

        # Prepare variables
        variables = {}

        # Execute the GraphQL operation
        try:
            response = await self._monday_client.get_graphql_client().execute(
                query=query,
                variables=variables,
                operation_name="version"
            )
            return response
        except Exception as e:
            return GraphQLResponse(
                success=False,
                message=f"Failed to execute query version: {str(e)}"
            )

    async def complexity(self) -> GraphQLResponse:
        """Get complexity information for current query

        GraphQL Operation: Query complexity

        Returns:
            GraphQLResponse: The GraphQL response containing the operation result

        Example:
            await monday_datasource.complexity()
        """
        # Get the complete GraphQL operation with fragments
        query = MondayGraphQLOperations.get_operation_with_fragments("query", "complexity")

        # Prepare variables
        variables = {}

        # Execute the GraphQL operation
        try:
            response = await self._monday_client.get_graphql_client().execute(
                query=query,
                variables=variables,
                operation_name="complexity"
            )
            return response
        except Exception as e:
            return GraphQLResponse(
                success=False,
                message=f"Failed to execute query complexity: {str(e)}"
            )

    async def rate_limit_status(self) -> GraphQLResponse:
        """Get rate limit status (via complexity)

        GraphQL Operation: Query rate_limit_status

        Returns:
            GraphQLResponse: The GraphQL response containing the operation result

        Example:
            await monday_datasource.rate_limit_status()
        """
        # Get the complete GraphQL operation with fragments
        query = MondayGraphQLOperations.get_operation_with_fragments("query", "rate_limit_status")

        # Prepare variables
        variables = {}

        # Execute the GraphQL operation
        try:
            response = await self._monday_client.get_graphql_client().execute(
                query=query,
                variables=variables,
                operation_name="rate_limit_status"
            )
            return response
        except Exception as e:
            return GraphQLResponse(
                success=False,
                message=f"Failed to execute query rate_limit_status: {str(e)}"
            )

    async def assets(
        self,
        ids: List[str]
    ) -> GraphQLResponse:
        """Get assets by IDs

        GraphQL Operation: Query assets

        Args:
            ids (List[str], required): Parameter for ids

        Returns:
            GraphQLResponse: The GraphQL response containing the operation result

        Example:
            await monday_datasource.assets(ids=["123", "456"])
        """
        # Get the complete GraphQL operation with fragments
        query = MondayGraphQLOperations.get_operation_with_fragments("query", "assets")

        # Prepare variables
        variables = {}
        if ids is not None:
            variables["ids"] = ids

        # Execute the GraphQL operation
        try:
            response = await self._monday_client.get_graphql_client().execute(
                query=query,
                variables=variables,
                operation_name="assets"
            )
            return response
        except Exception as e:
            return GraphQLResponse(
                success=False,
                message=f"Failed to execute query assets: {str(e)}"
            )

    # =============================================================================
    # MUTATION OPERATIONS
    # =============================================================================

    async def create_board(
        self,
        board_name: str,
        board_kind: str,
        workspace_id: Optional[str] = None,
        template_id: Optional[str] = None,
        folder_id: Optional[str] = None,
        board_owner_ids: Optional[List[str]] = None,
        board_subscriber_ids: Optional[List[str]] = None,
        description: Optional[str] = None,
        board_owner_team_ids: Optional[List[str]] = None,
        board_subscriber_team_ids: Optional[List[str]] = None
    ) -> GraphQLResponse:
        """Create a new board

        GraphQL Operation: Mutation create_board

        Args:
            board_name (str, required): Parameter for board_name
            board_kind (str, required): Parameter for board_kind
            workspace_id (str, optional): Parameter for workspace_id
            template_id (str, optional): Parameter for template_id
            folder_id (str, optional): Parameter for folder_id
            board_owner_ids (List[str], optional): Parameter for board_owner_ids
            board_subscriber_ids (List[str], optional): Parameter for board_subscriber_ids
            description (str, optional): Parameter for description
            board_owner_team_ids (List[str], optional): Parameter for board_owner_team_ids
            board_subscriber_team_ids (List[str], optional): Parameter for board_subscriber_team_ids

        Returns:
            GraphQLResponse: The GraphQL response containing the operation result

        Example:
            await monday_datasource.create_board(board_name="My Board", board_kind="public")
        """
        # Get the complete GraphQL operation with fragments
        query = MondayGraphQLOperations.get_operation_with_fragments("mutation", "create_board")

        # Prepare variables
        variables = {}
        if board_name is not None:
            variables["board_name"] = board_name
        if board_kind is not None:
            variables["board_kind"] = board_kind
        if workspace_id is not None:
            variables["workspace_id"] = workspace_id
        if template_id is not None:
            variables["template_id"] = template_id
        if folder_id is not None:
            variables["folder_id"] = folder_id
        if board_owner_ids is not None:
            variables["board_owner_ids"] = board_owner_ids
        if board_subscriber_ids is not None:
            variables["board_subscriber_ids"] = board_subscriber_ids
        if description is not None:
            variables["description"] = description
        if board_owner_team_ids is not None:
            variables["board_owner_team_ids"] = board_owner_team_ids
        if board_subscriber_team_ids is not None:
            variables["board_subscriber_team_ids"] = board_subscriber_team_ids

        # Execute the GraphQL operation
        try:
            response = await self._monday_client.get_graphql_client().execute(
                query=query,
                variables=variables,
                operation_name="create_board"
            )
            return response
        except Exception as e:
            return GraphQLResponse(
                success=False,
                message=f"Failed to execute mutation create_board: {str(e)}"
            )

    async def update_board(
        self,
        board_id: str,
        board_attribute: str,
        new_value: str
    ) -> GraphQLResponse:
        """Update a board

        GraphQL Operation: Mutation update_board

        Args:
            board_id (str, required): Parameter for board_id
            board_attribute (str, required): Parameter for board_attribute
            new_value (str, required): Parameter for new_value

        Returns:
            GraphQLResponse: The GraphQL response containing the operation result

        Example:
            await monday_datasource.update_board(board_id="123", board_attribute="name", new_value="New Name")
        """
        # Get the complete GraphQL operation with fragments
        query = MondayGraphQLOperations.get_operation_with_fragments("mutation", "update_board")

        # Prepare variables
        variables = {}
        if board_id is not None:
            variables["board_id"] = board_id
        if board_attribute is not None:
            variables["board_attribute"] = board_attribute
        if new_value is not None:
            variables["new_value"] = new_value

        # Execute the GraphQL operation
        try:
            response = await self._monday_client.get_graphql_client().execute(
                query=query,
                variables=variables,
                operation_name="update_board"
            )
            return response
        except Exception as e:
            return GraphQLResponse(
                success=False,
                message=f"Failed to execute mutation update_board: {str(e)}"
            )

    async def archive_board(
        self,
        board_id: str
    ) -> GraphQLResponse:
        """Archive a board

        GraphQL Operation: Mutation archive_board

        Args:
            board_id (str, required): Parameter for board_id

        Returns:
            GraphQLResponse: The GraphQL response containing the operation result

        Example:
            await monday_datasource.archive_board(board_id="123")
        """
        # Get the complete GraphQL operation with fragments
        query = MondayGraphQLOperations.get_operation_with_fragments("mutation", "archive_board")

        # Prepare variables
        variables = {}
        if board_id is not None:
            variables["board_id"] = board_id

        # Execute the GraphQL operation
        try:
            response = await self._monday_client.get_graphql_client().execute(
                query=query,
                variables=variables,
                operation_name="archive_board"
            )
            return response
        except Exception as e:
            return GraphQLResponse(
                success=False,
                message=f"Failed to execute mutation archive_board: {str(e)}"
            )

    async def delete_board(
        self,
        board_id: str
    ) -> GraphQLResponse:
        """Delete a board

        GraphQL Operation: Mutation delete_board

        Args:
            board_id (str, required): Parameter for board_id

        Returns:
            GraphQLResponse: The GraphQL response containing the operation result

        Example:
            await monday_datasource.delete_board(board_id="123")
        """
        # Get the complete GraphQL operation with fragments
        query = MondayGraphQLOperations.get_operation_with_fragments("mutation", "delete_board")

        # Prepare variables
        variables = {}
        if board_id is not None:
            variables["board_id"] = board_id

        # Execute the GraphQL operation
        try:
            response = await self._monday_client.get_graphql_client().execute(
                query=query,
                variables=variables,
                operation_name="delete_board"
            )
            return response
        except Exception as e:
            return GraphQLResponse(
                success=False,
                message=f"Failed to execute mutation delete_board: {str(e)}"
            )

    async def duplicate_board(
        self,
        board_id: str,
        duplicate_type: str,
        board_name: Optional[str] = None,
        workspace_id: Optional[str] = None,
        folder_id: Optional[str] = None,
        keep_subscribers: Optional[bool] = None
    ) -> GraphQLResponse:
        """Duplicate a board

        GraphQL Operation: Mutation duplicate_board

        Args:
            board_id (str, required): Parameter for board_id
            duplicate_type (str, required): Parameter for duplicate_type
            board_name (str, optional): Parameter for board_name
            workspace_id (str, optional): Parameter for workspace_id
            folder_id (str, optional): Parameter for folder_id
            keep_subscribers (bool, optional): Parameter for keep_subscribers

        Returns:
            GraphQLResponse: The GraphQL response containing the operation result

        Example:
            await monday_datasource.duplicate_board(board_id="123", duplicate_type="duplicate_board_with_structure")
        """
        # Get the complete GraphQL operation with fragments
        query = MondayGraphQLOperations.get_operation_with_fragments("mutation", "duplicate_board")

        # Prepare variables
        variables = {}
        if board_id is not None:
            variables["board_id"] = board_id
        if duplicate_type is not None:
            variables["duplicate_type"] = duplicate_type
        if board_name is not None:
            variables["board_name"] = board_name
        if workspace_id is not None:
            variables["workspace_id"] = workspace_id
        if folder_id is not None:
            variables["folder_id"] = folder_id
        if keep_subscribers is not None:
            variables["keep_subscribers"] = keep_subscribers

        # Execute the GraphQL operation
        try:
            response = await self._monday_client.get_graphql_client().execute(
                query=query,
                variables=variables,
                operation_name="duplicate_board"
            )
            return response
        except Exception as e:
            return GraphQLResponse(
                success=False,
                message=f"Failed to execute mutation duplicate_board: {str(e)}"
            )

    async def create_column(
        self,
        board_id: str,
        title: str,
        column_type: str,
        description: Optional[str] = None,
        defaults: Optional[str] = None,
        id: Optional[str] = None,
        after_column_id: Optional[str] = None
    ) -> GraphQLResponse:
        """Create a new column

        GraphQL Operation: Mutation create_column

        Args:
            board_id (str, required): Parameter for board_id
            title (str, required): Parameter for title
            column_type (str, required): Parameter for column_type
            description (str, optional): Parameter for description
            defaults (str, optional): Parameter for defaults
            id (str, optional): Parameter for id
            after_column_id (str, optional): Parameter for after_column_id

        Returns:
            GraphQLResponse: The GraphQL response containing the operation result

        Example:
            await monday_datasource.create_column(board_id="123", title="Status", column_type="status")
        """
        # Get the complete GraphQL operation with fragments
        query = MondayGraphQLOperations.get_operation_with_fragments("mutation", "create_column")

        # Prepare variables
        variables = {}
        if board_id is not None:
            variables["board_id"] = board_id
        if title is not None:
            variables["title"] = title
        if column_type is not None:
            variables["column_type"] = column_type
        if description is not None:
            variables["description"] = description
        if defaults is not None:
            variables["defaults"] = defaults
        if id is not None:
            variables["id"] = id
        if after_column_id is not None:
            variables["after_column_id"] = after_column_id

        # Execute the GraphQL operation
        try:
            response = await self._monday_client.get_graphql_client().execute(
                query=query,
                variables=variables,
                operation_name="create_column"
            )
            return response
        except Exception as e:
            return GraphQLResponse(
                success=False,
                message=f"Failed to execute mutation create_column: {str(e)}"
            )

    async def change_column_title(
        self,
        board_id: str,
        column_id: str,
        title: str
    ) -> GraphQLResponse:
        """Change column title

        GraphQL Operation: Mutation change_column_title

        Args:
            board_id (str, required): Parameter for board_id
            column_id (str, required): Parameter for column_id
            title (str, required): Parameter for title

        Returns:
            GraphQLResponse: The GraphQL response containing the operation result

        Example:
            await monday_datasource.change_column_title(board_id="123", column_id="status", title="New Status")
        """
        # Get the complete GraphQL operation with fragments
        query = MondayGraphQLOperations.get_operation_with_fragments("mutation", "change_column_title")

        # Prepare variables
        variables = {}
        if board_id is not None:
            variables["board_id"] = board_id
        if column_id is not None:
            variables["column_id"] = column_id
        if title is not None:
            variables["title"] = title

        # Execute the GraphQL operation
        try:
            response = await self._monday_client.get_graphql_client().execute(
                query=query,
                variables=variables,
                operation_name="change_column_title"
            )
            return response
        except Exception as e:
            return GraphQLResponse(
                success=False,
                message=f"Failed to execute mutation change_column_title: {str(e)}"
            )

    async def change_column_metadata(
        self,
        board_id: str,
        column_id: str,
        column_property: str,
        value: str
    ) -> GraphQLResponse:
        """Change column metadata

        GraphQL Operation: Mutation change_column_metadata

        Args:
            board_id (str, required): Parameter for board_id
            column_id (str, required): Parameter for column_id
            column_property (str, required): Parameter for column_property
            value (str, required): Parameter for value

        Returns:
            GraphQLResponse: The GraphQL response containing the operation result

        Example:
            await monday_datasource.change_column_metadata(board_id="123", column_id="status", column_property="description", value="New Description")
        """
        # Get the complete GraphQL operation with fragments
        query = MondayGraphQLOperations.get_operation_with_fragments("mutation", "change_column_metadata")

        # Prepare variables
        variables = {}
        if board_id is not None:
            variables["board_id"] = board_id
        if column_id is not None:
            variables["column_id"] = column_id
        if column_property is not None:
            variables["column_property"] = column_property
        if value is not None:
            variables["value"] = value

        # Execute the GraphQL operation
        try:
            response = await self._monday_client.get_graphql_client().execute(
                query=query,
                variables=variables,
                operation_name="change_column_metadata"
            )
            return response
        except Exception as e:
            return GraphQLResponse(
                success=False,
                message=f"Failed to execute mutation change_column_metadata: {str(e)}"
            )

    async def delete_column(
        self,
        board_id: str,
        column_id: str
    ) -> GraphQLResponse:
        """Delete a column

        GraphQL Operation: Mutation delete_column

        Args:
            board_id (str, required): Parameter for board_id
            column_id (str, required): Parameter for column_id

        Returns:
            GraphQLResponse: The GraphQL response containing the operation result

        Example:
            await monday_datasource.delete_column(board_id="123", column_id="status")
        """
        # Get the complete GraphQL operation with fragments
        query = MondayGraphQLOperations.get_operation_with_fragments("mutation", "delete_column")

        # Prepare variables
        variables = {}
        if board_id is not None:
            variables["board_id"] = board_id
        if column_id is not None:
            variables["column_id"] = column_id

        # Execute the GraphQL operation
        try:
            response = await self._monday_client.get_graphql_client().execute(
                query=query,
                variables=variables,
                operation_name="delete_column"
            )
            return response
        except Exception as e:
            return GraphQLResponse(
                success=False,
                message=f"Failed to execute mutation delete_column: {str(e)}"
            )

    async def change_column_value(
        self,
        board_id: str,
        item_id: str,
        column_id: str,
        value: str
    ) -> GraphQLResponse:
        """Change column value for an item

        GraphQL Operation: Mutation change_column_value

        Args:
            board_id (str, required): Parameter for board_id
            item_id (str, required): Parameter for item_id
            column_id (str, required): Parameter for column_id
            value (str, required): Parameter for value

        Returns:
            GraphQLResponse: The GraphQL response containing the operation result

        Example:
            await monday_datasource.change_column_value(board_id="123", item_id="456", column_id="status", value="{\"label\": \"Done\"}")
        """
        # Get the complete GraphQL operation with fragments
        query = MondayGraphQLOperations.get_operation_with_fragments("mutation", "change_column_value")

        # Prepare variables
        variables = {}
        if board_id is not None:
            variables["board_id"] = board_id
        if item_id is not None:
            variables["item_id"] = item_id
        if column_id is not None:
            variables["column_id"] = column_id
        if value is not None:
            variables["value"] = value

        # Execute the GraphQL operation
        try:
            response = await self._monday_client.get_graphql_client().execute(
                query=query,
                variables=variables,
                operation_name="change_column_value"
            )
            return response
        except Exception as e:
            return GraphQLResponse(
                success=False,
                message=f"Failed to execute mutation change_column_value: {str(e)}"
            )

    async def change_multiple_column_values(
        self,
        board_id: str,
        item_id: str,
        column_values: str,
        create_labels_if_missing: Optional[bool] = None
    ) -> GraphQLResponse:
        """Change multiple column values for an item

        GraphQL Operation: Mutation change_multiple_column_values

        Args:
            board_id (str, required): Parameter for board_id
            item_id (str, required): Parameter for item_id
            column_values (str, required): Parameter for column_values
            create_labels_if_missing (bool, optional): Parameter for create_labels_if_missing

        Returns:
            GraphQLResponse: The GraphQL response containing the operation result

        Example:
            await monday_datasource.change_multiple_column_values(board_id="123", item_id="456", column_values="{\"status\": {\"label\": \"Done\"}}")
        """
        # Get the complete GraphQL operation with fragments
        query = MondayGraphQLOperations.get_operation_with_fragments("mutation", "change_multiple_column_values")

        # Prepare variables
        variables = {}
        if board_id is not None:
            variables["board_id"] = board_id
        if item_id is not None:
            variables["item_id"] = item_id
        if column_values is not None:
            variables["column_values"] = column_values
        if create_labels_if_missing is not None:
            variables["create_labels_if_missing"] = create_labels_if_missing

        # Execute the GraphQL operation
        try:
            response = await self._monday_client.get_graphql_client().execute(
                query=query,
                variables=variables,
                operation_name="change_multiple_column_values"
            )
            return response
        except Exception as e:
            return GraphQLResponse(
                success=False,
                message=f"Failed to execute mutation change_multiple_column_values: {str(e)}"
            )

    async def change_simple_column_value(
        self,
        board_id: str,
        item_id: str,
        column_id: str,
        value: str,
        create_labels_if_missing: Optional[bool] = None
    ) -> GraphQLResponse:
        """Change simple column value for an item

        GraphQL Operation: Mutation change_simple_column_value

        Args:
            board_id (str, required): Parameter for board_id
            item_id (str, required): Parameter for item_id
            column_id (str, required): Parameter for column_id
            value (str, required): Parameter for value
            create_labels_if_missing (bool, optional): Parameter for create_labels_if_missing

        Returns:
            GraphQLResponse: The GraphQL response containing the operation result

        Example:
            await monday_datasource.change_simple_column_value(board_id="123", item_id="456", column_id="text", value="Hello World")
        """
        # Get the complete GraphQL operation with fragments
        query = MondayGraphQLOperations.get_operation_with_fragments("mutation", "change_simple_column_value")

        # Prepare variables
        variables = {}
        if board_id is not None:
            variables["board_id"] = board_id
        if item_id is not None:
            variables["item_id"] = item_id
        if column_id is not None:
            variables["column_id"] = column_id
        if value is not None:
            variables["value"] = value
        if create_labels_if_missing is not None:
            variables["create_labels_if_missing"] = create_labels_if_missing

        # Execute the GraphQL operation
        try:
            response = await self._monday_client.get_graphql_client().execute(
                query=query,
                variables=variables,
                operation_name="change_simple_column_value"
            )
            return response
        except Exception as e:
            return GraphQLResponse(
                success=False,
                message=f"Failed to execute mutation change_simple_column_value: {str(e)}"
            )

    async def create_group(
        self,
        board_id: str,
        group_name: str,
        group_color: Optional[str] = None,
        position: Optional[str] = None,
        relative_to: Optional[str] = None,
        position_relative_method: Optional[str] = None
    ) -> GraphQLResponse:
        """Create a new group

        GraphQL Operation: Mutation create_group

        Args:
            board_id (str, required): Parameter for board_id
            group_name (str, required): Parameter for group_name
            group_color (str, optional): Parameter for group_color
            position (str, optional): Parameter for position
            relative_to (str, optional): Parameter for relative_to
            position_relative_method (str, optional): Parameter for position_relative_method

        Returns:
            GraphQLResponse: The GraphQL response containing the operation result

        Example:
            await monday_datasource.create_group(board_id="123", group_name="New Group")
        """
        # Get the complete GraphQL operation with fragments
        query = MondayGraphQLOperations.get_operation_with_fragments("mutation", "create_group")

        # Prepare variables
        variables = {}
        if board_id is not None:
            variables["board_id"] = board_id
        if group_name is not None:
            variables["group_name"] = group_name
        if group_color is not None:
            variables["group_color"] = group_color
        if position is not None:
            variables["position"] = position
        if relative_to is not None:
            variables["relative_to"] = relative_to
        if position_relative_method is not None:
            variables["position_relative_method"] = position_relative_method

        # Execute the GraphQL operation
        try:
            response = await self._monday_client.get_graphql_client().execute(
                query=query,
                variables=variables,
                operation_name="create_group"
            )
            return response
        except Exception as e:
            return GraphQLResponse(
                success=False,
                message=f"Failed to execute mutation create_group: {str(e)}"
            )

    async def update_group(
        self,
        board_id: str,
        group_id: str,
        group_attribute: str,
        new_value: str
    ) -> GraphQLResponse:
        """Update a group

        GraphQL Operation: Mutation update_group

        Args:
            board_id (str, required): Parameter for board_id
            group_id (str, required): Parameter for group_id
            group_attribute (str, required): Parameter for group_attribute
            new_value (str, required): Parameter for new_value

        Returns:
            GraphQLResponse: The GraphQL response containing the operation result

        Example:
            await monday_datasource.update_group(board_id="123", group_id="group1", group_attribute="title", new_value="Updated Group")
        """
        # Get the complete GraphQL operation with fragments
        query = MondayGraphQLOperations.get_operation_with_fragments("mutation", "update_group")

        # Prepare variables
        variables = {}
        if board_id is not None:
            variables["board_id"] = board_id
        if group_id is not None:
            variables["group_id"] = group_id
        if group_attribute is not None:
            variables["group_attribute"] = group_attribute
        if new_value is not None:
            variables["new_value"] = new_value

        # Execute the GraphQL operation
        try:
            response = await self._monday_client.get_graphql_client().execute(
                query=query,
                variables=variables,
                operation_name="update_group"
            )
            return response
        except Exception as e:
            return GraphQLResponse(
                success=False,
                message=f"Failed to execute mutation update_group: {str(e)}"
            )

    async def duplicate_group(
        self,
        board_id: str,
        group_id: str,
        add_to_top: Optional[bool] = None,
        group_title: Optional[str] = None
    ) -> GraphQLResponse:
        """Duplicate a group

        GraphQL Operation: Mutation duplicate_group

        Args:
            board_id (str, required): Parameter for board_id
            group_id (str, required): Parameter for group_id
            add_to_top (bool, optional): Parameter for add_to_top
            group_title (str, optional): Parameter for group_title

        Returns:
            GraphQLResponse: The GraphQL response containing the operation result

        Example:
            await monday_datasource.duplicate_group(board_id="123", group_id="group1")
        """
        # Get the complete GraphQL operation with fragments
        query = MondayGraphQLOperations.get_operation_with_fragments("mutation", "duplicate_group")

        # Prepare variables
        variables = {}
        if board_id is not None:
            variables["board_id"] = board_id
        if group_id is not None:
            variables["group_id"] = group_id
        if add_to_top is not None:
            variables["add_to_top"] = add_to_top
        if group_title is not None:
            variables["group_title"] = group_title

        # Execute the GraphQL operation
        try:
            response = await self._monday_client.get_graphql_client().execute(
                query=query,
                variables=variables,
                operation_name="duplicate_group"
            )
            return response
        except Exception as e:
            return GraphQLResponse(
                success=False,
                message=f"Failed to execute mutation duplicate_group: {str(e)}"
            )

    async def archive_group(
        self,
        board_id: str,
        group_id: str
    ) -> GraphQLResponse:
        """Archive a group

        GraphQL Operation: Mutation archive_group

        Args:
            board_id (str, required): Parameter for board_id
            group_id (str, required): Parameter for group_id

        Returns:
            GraphQLResponse: The GraphQL response containing the operation result

        Example:
            await monday_datasource.archive_group(board_id="123", group_id="group1")
        """
        # Get the complete GraphQL operation with fragments
        query = MondayGraphQLOperations.get_operation_with_fragments("mutation", "archive_group")

        # Prepare variables
        variables = {}
        if board_id is not None:
            variables["board_id"] = board_id
        if group_id is not None:
            variables["group_id"] = group_id

        # Execute the GraphQL operation
        try:
            response = await self._monday_client.get_graphql_client().execute(
                query=query,
                variables=variables,
                operation_name="archive_group"
            )
            return response
        except Exception as e:
            return GraphQLResponse(
                success=False,
                message=f"Failed to execute mutation archive_group: {str(e)}"
            )

    async def delete_group(
        self,
        board_id: str,
        group_id: str
    ) -> GraphQLResponse:
        """Delete a group

        GraphQL Operation: Mutation delete_group

        Args:
            board_id (str, required): Parameter for board_id
            group_id (str, required): Parameter for group_id

        Returns:
            GraphQLResponse: The GraphQL response containing the operation result

        Example:
            await monday_datasource.delete_group(board_id="123", group_id="group1")
        """
        # Get the complete GraphQL operation with fragments
        query = MondayGraphQLOperations.get_operation_with_fragments("mutation", "delete_group")

        # Prepare variables
        variables = {}
        if board_id is not None:
            variables["board_id"] = board_id
        if group_id is not None:
            variables["group_id"] = group_id

        # Execute the GraphQL operation
        try:
            response = await self._monday_client.get_graphql_client().execute(
                query=query,
                variables=variables,
                operation_name="delete_group"
            )
            return response
        except Exception as e:
            return GraphQLResponse(
                success=False,
                message=f"Failed to execute mutation delete_group: {str(e)}"
            )

    async def move_item_to_group(
        self,
        item_id: str,
        group_id: str
    ) -> GraphQLResponse:
        """Move an item to a different group

        GraphQL Operation: Mutation move_item_to_group

        Args:
            item_id (str, required): Parameter for item_id
            group_id (str, required): Parameter for group_id

        Returns:
            GraphQLResponse: The GraphQL response containing the operation result

        Example:
            await monday_datasource.move_item_to_group(item_id="456", group_id="group2")
        """
        # Get the complete GraphQL operation with fragments
        query = MondayGraphQLOperations.get_operation_with_fragments("mutation", "move_item_to_group")

        # Prepare variables
        variables = {}
        if item_id is not None:
            variables["item_id"] = item_id
        if group_id is not None:
            variables["group_id"] = group_id

        # Execute the GraphQL operation
        try:
            response = await self._monday_client.get_graphql_client().execute(
                query=query,
                variables=variables,
                operation_name="move_item_to_group"
            )
            return response
        except Exception as e:
            return GraphQLResponse(
                success=False,
                message=f"Failed to execute mutation move_item_to_group: {str(e)}"
            )

    async def create_item(
        self,
        board_id: str,
        item_name: str,
        group_id: Optional[str] = None,
        column_values: Optional[str] = None,
        create_labels_if_missing: Optional[bool] = None,
        position_relative_method: Optional[str] = None,
        relative_to: Optional[str] = None
    ) -> GraphQLResponse:
        """Create a new item

        GraphQL Operation: Mutation create_item

        Args:
            board_id (str, required): Parameter for board_id
            item_name (str, required): Parameter for item_name
            group_id (str, optional): Parameter for group_id
            column_values (str, optional): Parameter for column_values
            create_labels_if_missing (bool, optional): Parameter for create_labels_if_missing
            position_relative_method (str, optional): Parameter for position_relative_method
            relative_to (str, optional): Parameter for relative_to

        Returns:
            GraphQLResponse: The GraphQL response containing the operation result

        Example:
            await monday_datasource.create_item(board_id="123", item_name="New Item")
        """
        # Get the complete GraphQL operation with fragments
        query = MondayGraphQLOperations.get_operation_with_fragments("mutation", "create_item")

        # Prepare variables
        variables = {}
        if board_id is not None:
            variables["board_id"] = board_id
        if item_name is not None:
            variables["item_name"] = item_name
        if group_id is not None:
            variables["group_id"] = group_id
        if column_values is not None:
            variables["column_values"] = column_values
        if create_labels_if_missing is not None:
            variables["create_labels_if_missing"] = create_labels_if_missing
        if position_relative_method is not None:
            variables["position_relative_method"] = position_relative_method
        if relative_to is not None:
            variables["relative_to"] = relative_to

        # Execute the GraphQL operation
        try:
            response = await self._monday_client.get_graphql_client().execute(
                query=query,
                variables=variables,
                operation_name="create_item"
            )
            return response
        except Exception as e:
            return GraphQLResponse(
                success=False,
                message=f"Failed to execute mutation create_item: {str(e)}"
            )

    async def duplicate_item(
        self,
        board_id: str,
        item_id: str,
        with_updates: Optional[bool] = None
    ) -> GraphQLResponse:
        """Duplicate an item

        GraphQL Operation: Mutation duplicate_item

        Args:
            board_id (str, required): Parameter for board_id
            item_id (str, required): Parameter for item_id
            with_updates (bool, optional): Parameter for with_updates

        Returns:
            GraphQLResponse: The GraphQL response containing the operation result

        Example:
            await monday_datasource.duplicate_item(board_id="123", item_id="456")
        """
        # Get the complete GraphQL operation with fragments
        query = MondayGraphQLOperations.get_operation_with_fragments("mutation", "duplicate_item")

        # Prepare variables
        variables = {}
        if board_id is not None:
            variables["board_id"] = board_id
        if item_id is not None:
            variables["item_id"] = item_id
        if with_updates is not None:
            variables["with_updates"] = with_updates

        # Execute the GraphQL operation
        try:
            response = await self._monday_client.get_graphql_client().execute(
                query=query,
                variables=variables,
                operation_name="duplicate_item"
            )
            return response
        except Exception as e:
            return GraphQLResponse(
                success=False,
                message=f"Failed to execute mutation duplicate_item: {str(e)}"
            )

    async def move_item_to_board(
        self,
        board_id: str,
        item_id: str,
        group_id: Optional[str] = None,
        columns_mapping: Optional[List[Dict[str, str]]] = None,
        subitems_columns_mapping: Optional[List[Dict[str, str]]] = None
    ) -> GraphQLResponse:
        """Move an item to a different board

        GraphQL Operation: Mutation move_item_to_board

        Args:
            board_id (str, required): Parameter for board_id
            item_id (str, required): Parameter for item_id
            group_id (str, optional): Parameter for group_id
            columns_mapping (List[Dict[str, str]], optional): Parameter for columns_mapping
            subitems_columns_mapping (List[Dict[str, str]], optional): Parameter for subitems_columns_mapping

        Returns:
            GraphQLResponse: The GraphQL response containing the operation result

        Example:
            await monday_datasource.move_item_to_board(board_id="456", item_id="123")
        """
        # Get the complete GraphQL operation with fragments
        query = MondayGraphQLOperations.get_operation_with_fragments("mutation", "move_item_to_board")

        # Prepare variables
        variables = {}
        if board_id is not None:
            variables["board_id"] = board_id
        if item_id is not None:
            variables["item_id"] = item_id
        if group_id is not None:
            variables["group_id"] = group_id
        if columns_mapping is not None:
            variables["columns_mapping"] = columns_mapping
        if subitems_columns_mapping is not None:
            variables["subitems_columns_mapping"] = subitems_columns_mapping

        # Execute the GraphQL operation
        try:
            response = await self._monday_client.get_graphql_client().execute(
                query=query,
                variables=variables,
                operation_name="move_item_to_board"
            )
            return response
        except Exception as e:
            return GraphQLResponse(
                success=False,
                message=f"Failed to execute mutation move_item_to_board: {str(e)}"
            )

    async def archive_item(
        self,
        item_id: str
    ) -> GraphQLResponse:
        """Archive an item

        GraphQL Operation: Mutation archive_item

        Args:
            item_id (str, required): Parameter for item_id

        Returns:
            GraphQLResponse: The GraphQL response containing the operation result

        Example:
            await monday_datasource.archive_item(item_id="123")
        """
        # Get the complete GraphQL operation with fragments
        query = MondayGraphQLOperations.get_operation_with_fragments("mutation", "archive_item")

        # Prepare variables
        variables = {}
        if item_id is not None:
            variables["item_id"] = item_id

        # Execute the GraphQL operation
        try:
            response = await self._monday_client.get_graphql_client().execute(
                query=query,
                variables=variables,
                operation_name="archive_item"
            )
            return response
        except Exception as e:
            return GraphQLResponse(
                success=False,
                message=f"Failed to execute mutation archive_item: {str(e)}"
            )

    async def delete_item(
        self,
        item_id: str
    ) -> GraphQLResponse:
        """Delete an item

        GraphQL Operation: Mutation delete_item

        Args:
            item_id (str, required): Parameter for item_id

        Returns:
            GraphQLResponse: The GraphQL response containing the operation result

        Example:
            await monday_datasource.delete_item(item_id="123")
        """
        # Get the complete GraphQL operation with fragments
        query = MondayGraphQLOperations.get_operation_with_fragments("mutation", "delete_item")

        # Prepare variables
        variables = {}
        if item_id is not None:
            variables["item_id"] = item_id

        # Execute the GraphQL operation
        try:
            response = await self._monday_client.get_graphql_client().execute(
                query=query,
                variables=variables,
                operation_name="delete_item"
            )
            return response
        except Exception as e:
            return GraphQLResponse(
                success=False,
                message=f"Failed to execute mutation delete_item: {str(e)}"
            )

    async def clear_item_updates(
        self,
        item_id: str
    ) -> GraphQLResponse:
        """Clear all updates from an item

        GraphQL Operation: Mutation clear_item_updates

        Args:
            item_id (str, required): Parameter for item_id

        Returns:
            GraphQLResponse: The GraphQL response containing the operation result

        Example:
            await monday_datasource.clear_item_updates(item_id="123")
        """
        # Get the complete GraphQL operation with fragments
        query = MondayGraphQLOperations.get_operation_with_fragments("mutation", "clear_item_updates")

        # Prepare variables
        variables = {}
        if item_id is not None:
            variables["item_id"] = item_id

        # Execute the GraphQL operation
        try:
            response = await self._monday_client.get_graphql_client().execute(
                query=query,
                variables=variables,
                operation_name="clear_item_updates"
            )
            return response
        except Exception as e:
            return GraphQLResponse(
                success=False,
                message=f"Failed to execute mutation clear_item_updates: {str(e)}"
            )

    async def create_subitem(
        self,
        parent_item_id: str,
        item_name: str,
        column_values: Optional[str] = None,
        create_labels_if_missing: Optional[bool] = None
    ) -> GraphQLResponse:
        """Create a subitem

        GraphQL Operation: Mutation create_subitem

        Args:
            parent_item_id (str, required): Parameter for parent_item_id
            item_name (str, required): Parameter for item_name
            column_values (str, optional): Parameter for column_values
            create_labels_if_missing (bool, optional): Parameter for create_labels_if_missing

        Returns:
            GraphQLResponse: The GraphQL response containing the operation result

        Example:
            await monday_datasource.create_subitem(parent_item_id="123", item_name="Subitem")
        """
        # Get the complete GraphQL operation with fragments
        query = MondayGraphQLOperations.get_operation_with_fragments("mutation", "create_subitem")

        # Prepare variables
        variables = {}
        if parent_item_id is not None:
            variables["parent_item_id"] = parent_item_id
        if item_name is not None:
            variables["item_name"] = item_name
        if column_values is not None:
            variables["column_values"] = column_values
        if create_labels_if_missing is not None:
            variables["create_labels_if_missing"] = create_labels_if_missing

        # Execute the GraphQL operation
        try:
            response = await self._monday_client.get_graphql_client().execute(
                query=query,
                variables=variables,
                operation_name="create_subitem"
            )
            return response
        except Exception as e:
            return GraphQLResponse(
                success=False,
                message=f"Failed to execute mutation create_subitem: {str(e)}"
            )

    async def create_update(
        self,
        item_id: str,
        body: str,
        parent_id: Optional[str] = None
    ) -> GraphQLResponse:
        """Create an update

        GraphQL Operation: Mutation create_update

        Args:
            item_id (str, required): Parameter for item_id
            body (str, required): Parameter for body
            parent_id (str, optional): Parameter for parent_id

        Returns:
            GraphQLResponse: The GraphQL response containing the operation result

        Example:
            await monday_datasource.create_update(item_id="123", body="This is an update")
        """
        # Get the complete GraphQL operation with fragments
        query = MondayGraphQLOperations.get_operation_with_fragments("mutation", "create_update")

        # Prepare variables
        variables = {}
        if item_id is not None:
            variables["item_id"] = item_id
        if body is not None:
            variables["body"] = body
        if parent_id is not None:
            variables["parent_id"] = parent_id

        # Execute the GraphQL operation
        try:
            response = await self._monday_client.get_graphql_client().execute(
                query=query,
                variables=variables,
                operation_name="create_update"
            )
            return response
        except Exception as e:
            return GraphQLResponse(
                success=False,
                message=f"Failed to execute mutation create_update: {str(e)}"
            )

    async def edit_update(
        self,
        id: str,
        body: str
    ) -> GraphQLResponse:
        """Edit an update

        GraphQL Operation: Mutation edit_update

        Args:
            id (str, required): Parameter for id
            body (str, required): Parameter for body

        Returns:
            GraphQLResponse: The GraphQL response containing the operation result

        Example:
            await monday_datasource.edit_update(id="123", body="Updated content")
        """
        # Get the complete GraphQL operation with fragments
        query = MondayGraphQLOperations.get_operation_with_fragments("mutation", "edit_update")

        # Prepare variables
        variables = {}
        if id is not None:
            variables["id"] = id
        if body is not None:
            variables["body"] = body

        # Execute the GraphQL operation
        try:
            response = await self._monday_client.get_graphql_client().execute(
                query=query,
                variables=variables,
                operation_name="edit_update"
            )
            return response
        except Exception as e:
            return GraphQLResponse(
                success=False,
                message=f"Failed to execute mutation edit_update: {str(e)}"
            )

    async def delete_update(
        self,
        id: str
    ) -> GraphQLResponse:
        """Delete an update

        GraphQL Operation: Mutation delete_update

        Args:
            id (str, required): Parameter for id

        Returns:
            GraphQLResponse: The GraphQL response containing the operation result

        Example:
            await monday_datasource.delete_update(id="123")
        """
        # Get the complete GraphQL operation with fragments
        query = MondayGraphQLOperations.get_operation_with_fragments("mutation", "delete_update")

        # Prepare variables
        variables = {}
        if id is not None:
            variables["id"] = id

        # Execute the GraphQL operation
        try:
            response = await self._monday_client.get_graphql_client().execute(
                query=query,
                variables=variables,
                operation_name="delete_update"
            )
            return response
        except Exception as e:
            return GraphQLResponse(
                success=False,
                message=f"Failed to execute mutation delete_update: {str(e)}"
            )

    async def like_update(
        self,
        update_id: str
    ) -> GraphQLResponse:
        """Like an update

        GraphQL Operation: Mutation like_update

        Args:
            update_id (str, required): Parameter for update_id

        Returns:
            GraphQLResponse: The GraphQL response containing the operation result

        Example:
            await monday_datasource.like_update(update_id="123")
        """
        # Get the complete GraphQL operation with fragments
        query = MondayGraphQLOperations.get_operation_with_fragments("mutation", "like_update")

        # Prepare variables
        variables = {}
        if update_id is not None:
            variables["update_id"] = update_id

        # Execute the GraphQL operation
        try:
            response = await self._monday_client.get_graphql_client().execute(
                query=query,
                variables=variables,
                operation_name="like_update"
            )
            return response
        except Exception as e:
            return GraphQLResponse(
                success=False,
                message=f"Failed to execute mutation like_update: {str(e)}"
            )

    async def create_notification(
        self,
        text: str,
        user_id: str,
        target_id: str,
        target_type: str
    ) -> GraphQLResponse:
        """Create a notification

        GraphQL Operation: Mutation create_notification

        Args:
            text (str, required): Parameter for text
            user_id (str, required): Parameter for user_id
            target_id (str, required): Parameter for target_id
            target_type (str, required): Parameter for target_type

        Returns:
            GraphQLResponse: The GraphQL response containing the operation result

        Example:
            await monday_datasource.create_notification(text="Hello", user_id="123", target_id="456", target_type="Project")
        """
        # Get the complete GraphQL operation with fragments
        query = MondayGraphQLOperations.get_operation_with_fragments("mutation", "create_notification")

        # Prepare variables
        variables = {}
        if text is not None:
            variables["text"] = text
        if user_id is not None:
            variables["user_id"] = user_id
        if target_id is not None:
            variables["target_id"] = target_id
        if target_type is not None:
            variables["target_type"] = target_type

        # Execute the GraphQL operation
        try:
            response = await self._monday_client.get_graphql_client().execute(
                query=query,
                variables=variables,
                operation_name="create_notification"
            )
            return response
        except Exception as e:
            return GraphQLResponse(
                success=False,
                message=f"Failed to execute mutation create_notification: {str(e)}"
            )

    async def create_or_get_tag(
        self,
        tag_name: str,
        board_id: Optional[str] = None
    ) -> GraphQLResponse:
        """Create or get a tag

        GraphQL Operation: Mutation create_or_get_tag

        Args:
            tag_name (str, required): Parameter for tag_name
            board_id (str, optional): Parameter for board_id

        Returns:
            GraphQLResponse: The GraphQL response containing the operation result

        Example:
            await monday_datasource.create_or_get_tag(tag_name="Important")
        """
        # Get the complete GraphQL operation with fragments
        query = MondayGraphQLOperations.get_operation_with_fragments("mutation", "create_or_get_tag")

        # Prepare variables
        variables = {}
        if tag_name is not None:
            variables["tag_name"] = tag_name
        if board_id is not None:
            variables["board_id"] = board_id

        # Execute the GraphQL operation
        try:
            response = await self._monday_client.get_graphql_client().execute(
                query=query,
                variables=variables,
                operation_name="create_or_get_tag"
            )
            return response
        except Exception as e:
            return GraphQLResponse(
                success=False,
                message=f"Failed to execute mutation create_or_get_tag: {str(e)}"
            )

    async def create_workspace(
        self,
        name: str,
        kind: str,
        description: Optional[str] = None
    ) -> GraphQLResponse:
        """Create a workspace

        GraphQL Operation: Mutation create_workspace

        Args:
            name (str, required): Parameter for name
            kind (str, required): Parameter for kind
            description (str, optional): Parameter for description

        Returns:
            GraphQLResponse: The GraphQL response containing the operation result

        Example:
            await monday_datasource.create_workspace(name="My Workspace", kind="open")
        """
        # Get the complete GraphQL operation with fragments
        query = MondayGraphQLOperations.get_operation_with_fragments("mutation", "create_workspace")

        # Prepare variables
        variables = {}
        if name is not None:
            variables["name"] = name
        if kind is not None:
            variables["kind"] = kind
        if description is not None:
            variables["description"] = description

        # Execute the GraphQL operation
        try:
            response = await self._monday_client.get_graphql_client().execute(
                query=query,
                variables=variables,
                operation_name="create_workspace"
            )
            return response
        except Exception as e:
            return GraphQLResponse(
                success=False,
                message=f"Failed to execute mutation create_workspace: {str(e)}"
            )

    async def update_workspace(
        self,
        id: str,
        attributes: Dict[str, Any]
    ) -> GraphQLResponse:
        """Update a workspace

        GraphQL Operation: Mutation update_workspace

        Args:
            id (str, required): Parameter for id
            attributes (Dict[str, Any], required): Parameter for attributes

        Returns:
            GraphQLResponse: The GraphQL response containing the operation result

        Example:
            await monday_datasource.update_workspace(id="123", attributes={"name": "New Name"})
        """
        # Get the complete GraphQL operation with fragments
        query = MondayGraphQLOperations.get_operation_with_fragments("mutation", "update_workspace")

        # Prepare variables
        variables = {}
        if id is not None:
            variables["id"] = id
        if attributes is not None:
            variables["attributes"] = attributes

        # Execute the GraphQL operation
        try:
            response = await self._monday_client.get_graphql_client().execute(
                query=query,
                variables=variables,
                operation_name="update_workspace"
            )
            return response
        except Exception as e:
            return GraphQLResponse(
                success=False,
                message=f"Failed to execute mutation update_workspace: {str(e)}"
            )

    async def delete_workspace(
        self,
        workspace_id: str
    ) -> GraphQLResponse:
        """Delete a workspace

        GraphQL Operation: Mutation delete_workspace

        Args:
            workspace_id (str, required): Parameter for workspace_id

        Returns:
            GraphQLResponse: The GraphQL response containing the operation result

        Example:
            await monday_datasource.delete_workspace(workspace_id="123")
        """
        # Get the complete GraphQL operation with fragments
        query = MondayGraphQLOperations.get_operation_with_fragments("mutation", "delete_workspace")

        # Prepare variables
        variables = {}
        if workspace_id is not None:
            variables["workspace_id"] = workspace_id

        # Execute the GraphQL operation
        try:
            response = await self._monday_client.get_graphql_client().execute(
                query=query,
                variables=variables,
                operation_name="delete_workspace"
            )
            return response
        except Exception as e:
            return GraphQLResponse(
                success=False,
                message=f"Failed to execute mutation delete_workspace: {str(e)}"
            )

    async def add_users_to_workspace(
        self,
        workspace_id: str,
        user_ids: List[str],
        kind: str
    ) -> GraphQLResponse:
        """Add users to a workspace

        GraphQL Operation: Mutation add_users_to_workspace

        Args:
            workspace_id (str, required): Parameter for workspace_id
            user_ids (List[str], required): Parameter for user_ids
            kind (str, required): Parameter for kind

        Returns:
            GraphQLResponse: The GraphQL response containing the operation result

        Example:
            await monday_datasource.add_users_to_workspace(workspace_id="123", user_ids=["456"], kind="subscriber")
        """
        # Get the complete GraphQL operation with fragments
        query = MondayGraphQLOperations.get_operation_with_fragments("mutation", "add_users_to_workspace")

        # Prepare variables
        variables = {}
        if workspace_id is not None:
            variables["workspace_id"] = workspace_id
        if user_ids is not None:
            variables["user_ids"] = user_ids
        if kind is not None:
            variables["kind"] = kind

        # Execute the GraphQL operation
        try:
            response = await self._monday_client.get_graphql_client().execute(
                query=query,
                variables=variables,
                operation_name="add_users_to_workspace"
            )
            return response
        except Exception as e:
            return GraphQLResponse(
                success=False,
                message=f"Failed to execute mutation add_users_to_workspace: {str(e)}"
            )

    async def delete_users_from_workspace(
        self,
        workspace_id: str,
        user_ids: List[str]
    ) -> GraphQLResponse:
        """Remove users from a workspace

        GraphQL Operation: Mutation delete_users_from_workspace

        Args:
            workspace_id (str, required): Parameter for workspace_id
            user_ids (List[str], required): Parameter for user_ids

        Returns:
            GraphQLResponse: The GraphQL response containing the operation result

        Example:
            await monday_datasource.delete_users_from_workspace(workspace_id="123", user_ids=["456"])
        """
        # Get the complete GraphQL operation with fragments
        query = MondayGraphQLOperations.get_operation_with_fragments("mutation", "delete_users_from_workspace")

        # Prepare variables
        variables = {}
        if workspace_id is not None:
            variables["workspace_id"] = workspace_id
        if user_ids is not None:
            variables["user_ids"] = user_ids

        # Execute the GraphQL operation
        try:
            response = await self._monday_client.get_graphql_client().execute(
                query=query,
                variables=variables,
                operation_name="delete_users_from_workspace"
            )
            return response
        except Exception as e:
            return GraphQLResponse(
                success=False,
                message=f"Failed to execute mutation delete_users_from_workspace: {str(e)}"
            )

    async def add_teams_to_workspace(
        self,
        workspace_id: str,
        team_ids: List[str],
        kind: str
    ) -> GraphQLResponse:
        """Add teams to a workspace

        GraphQL Operation: Mutation add_teams_to_workspace

        Args:
            workspace_id (str, required): Parameter for workspace_id
            team_ids (List[str], required): Parameter for team_ids
            kind (str, required): Parameter for kind

        Returns:
            GraphQLResponse: The GraphQL response containing the operation result

        Example:
            await monday_datasource.add_teams_to_workspace(workspace_id="123", team_ids=["456"], kind="subscriber")
        """
        # Get the complete GraphQL operation with fragments
        query = MondayGraphQLOperations.get_operation_with_fragments("mutation", "add_teams_to_workspace")

        # Prepare variables
        variables = {}
        if workspace_id is not None:
            variables["workspace_id"] = workspace_id
        if team_ids is not None:
            variables["team_ids"] = team_ids
        if kind is not None:
            variables["kind"] = kind

        # Execute the GraphQL operation
        try:
            response = await self._monday_client.get_graphql_client().execute(
                query=query,
                variables=variables,
                operation_name="add_teams_to_workspace"
            )
            return response
        except Exception as e:
            return GraphQLResponse(
                success=False,
                message=f"Failed to execute mutation add_teams_to_workspace: {str(e)}"
            )

    async def delete_teams_from_workspace(
        self,
        workspace_id: str,
        team_ids: List[str]
    ) -> GraphQLResponse:
        """Remove teams from a workspace

        GraphQL Operation: Mutation delete_teams_from_workspace

        Args:
            workspace_id (str, required): Parameter for workspace_id
            team_ids (List[str], required): Parameter for team_ids

        Returns:
            GraphQLResponse: The GraphQL response containing the operation result

        Example:
            await monday_datasource.delete_teams_from_workspace(workspace_id="123", team_ids=["456"])
        """
        # Get the complete GraphQL operation with fragments
        query = MondayGraphQLOperations.get_operation_with_fragments("mutation", "delete_teams_from_workspace")

        # Prepare variables
        variables = {}
        if workspace_id is not None:
            variables["workspace_id"] = workspace_id
        if team_ids is not None:
            variables["team_ids"] = team_ids

        # Execute the GraphQL operation
        try:
            response = await self._monday_client.get_graphql_client().execute(
                query=query,
                variables=variables,
                operation_name="delete_teams_from_workspace"
            )
            return response
        except Exception as e:
            return GraphQLResponse(
                success=False,
                message=f"Failed to execute mutation delete_teams_from_workspace: {str(e)}"
            )

    async def add_subscribers_to_board(
        self,
        board_id: str,
        user_ids: List[str],
        kind: Optional[str] = None
    ) -> GraphQLResponse:
        """Add subscribers to a board

        GraphQL Operation: Mutation add_subscribers_to_board

        Args:
            board_id (str, required): Parameter for board_id
            user_ids (List[str], required): Parameter for user_ids
            kind (str, optional): Parameter for kind

        Returns:
            GraphQLResponse: The GraphQL response containing the operation result

        Example:
            await monday_datasource.add_subscribers_to_board(board_id="123", user_ids=["456"])
        """
        # Get the complete GraphQL operation with fragments
        query = MondayGraphQLOperations.get_operation_with_fragments("mutation", "add_subscribers_to_board")

        # Prepare variables
        variables = {}
        if board_id is not None:
            variables["board_id"] = board_id
        if user_ids is not None:
            variables["user_ids"] = user_ids
        if kind is not None:
            variables["kind"] = kind

        # Execute the GraphQL operation
        try:
            response = await self._monday_client.get_graphql_client().execute(
                query=query,
                variables=variables,
                operation_name="add_subscribers_to_board"
            )
            return response
        except Exception as e:
            return GraphQLResponse(
                success=False,
                message=f"Failed to execute mutation add_subscribers_to_board: {str(e)}"
            )

    async def delete_subscribers_from_board(
        self,
        board_id: str,
        user_ids: List[str]
    ) -> GraphQLResponse:
        """Remove subscribers from a board

        GraphQL Operation: Mutation delete_subscribers_from_board

        Args:
            board_id (str, required): Parameter for board_id
            user_ids (List[str], required): Parameter for user_ids

        Returns:
            GraphQLResponse: The GraphQL response containing the operation result

        Example:
            await monday_datasource.delete_subscribers_from_board(board_id="123", user_ids=["456"])
        """
        # Get the complete GraphQL operation with fragments
        query = MondayGraphQLOperations.get_operation_with_fragments("mutation", "delete_subscribers_from_board")

        # Prepare variables
        variables = {}
        if board_id is not None:
            variables["board_id"] = board_id
        if user_ids is not None:
            variables["user_ids"] = user_ids

        # Execute the GraphQL operation
        try:
            response = await self._monday_client.get_graphql_client().execute(
                query=query,
                variables=variables,
                operation_name="delete_subscribers_from_board"
            )
            return response
        except Exception as e:
            return GraphQLResponse(
                success=False,
                message=f"Failed to execute mutation delete_subscribers_from_board: {str(e)}"
            )

    async def create_webhook(
        self,
        board_id: str,
        url: str,
        event: str,
        config: Optional[str] = None
    ) -> GraphQLResponse:
        """Create a webhook

        GraphQL Operation: Mutation create_webhook

        Args:
            board_id (str, required): Parameter for board_id
            url (str, required): Parameter for url
            event (str, required): Parameter for event
            config (str, optional): Parameter for config

        Returns:
            GraphQLResponse: The GraphQL response containing the operation result

        Example:
            await monday_datasource.create_webhook(board_id="123", url="https://example.com/webhook", event="create_item")
        """
        # Get the complete GraphQL operation with fragments
        query = MondayGraphQLOperations.get_operation_with_fragments("mutation", "create_webhook")

        # Prepare variables
        variables = {}
        if board_id is not None:
            variables["board_id"] = board_id
        if url is not None:
            variables["url"] = url
        if event is not None:
            variables["event"] = event
        if config is not None:
            variables["config"] = config

        # Execute the GraphQL operation
        try:
            response = await self._monday_client.get_graphql_client().execute(
                query=query,
                variables=variables,
                operation_name="create_webhook"
            )
            return response
        except Exception as e:
            return GraphQLResponse(
                success=False,
                message=f"Failed to execute mutation create_webhook: {str(e)}"
            )

    async def delete_webhook(
        self,
        id: str
    ) -> GraphQLResponse:
        """Delete a webhook

        GraphQL Operation: Mutation delete_webhook

        Args:
            id (str, required): Parameter for id

        Returns:
            GraphQLResponse: The GraphQL response containing the operation result

        Example:
            await monday_datasource.delete_webhook(id="123")
        """
        # Get the complete GraphQL operation with fragments
        query = MondayGraphQLOperations.get_operation_with_fragments("mutation", "delete_webhook")

        # Prepare variables
        variables = {}
        if id is not None:
            variables["id"] = id

        # Execute the GraphQL operation
        try:
            response = await self._monday_client.get_graphql_client().execute(
                query=query,
                variables=variables,
                operation_name="delete_webhook"
            )
            return response
        except Exception as e:
            return GraphQLResponse(
                success=False,
                message=f"Failed to execute mutation delete_webhook: {str(e)}"
            )

    async def create_doc(
        self,
        location: Dict[str, Any]
    ) -> GraphQLResponse:
        """Create a document

        GraphQL Operation: Mutation create_doc

        Args:
            location (Dict[str, Any], required): Parameter for location

        Returns:
            GraphQLResponse: The GraphQL response containing the operation result

        Example:
            await monday_datasource.create_doc(location={"workspace": {"workspace_id": 123}})
        """
        # Get the complete GraphQL operation with fragments
        query = MondayGraphQLOperations.get_operation_with_fragments("mutation", "create_doc")

        # Prepare variables
        variables = {}
        if location is not None:
            variables["location"] = location

        # Execute the GraphQL operation
        try:
            response = await self._monday_client.get_graphql_client().execute(
                query=query,
                variables=variables,
                operation_name="create_doc"
            )
            return response
        except Exception as e:
            return GraphQLResponse(
                success=False,
                message=f"Failed to execute mutation create_doc: {str(e)}"
            )

    async def create_doc_block(
        self,
        doc_id: str,
        type: str,
        content: str,
        after_block_id: Optional[str] = None,
        parent_block_id: Optional[str] = None
    ) -> GraphQLResponse:
        """Create a document block

        GraphQL Operation: Mutation create_doc_block

        Args:
            doc_id (str, required): Parameter for doc_id
            type (str, required): Parameter for type
            content (str, required): Parameter for content
            after_block_id (str, optional): Parameter for after_block_id
            parent_block_id (str, optional): Parameter for parent_block_id

        Returns:
            GraphQLResponse: The GraphQL response containing the operation result

        Example:
            await monday_datasource.create_doc_block(doc_id="123", type="normal_text", content="Hello World")
        """
        # Get the complete GraphQL operation with fragments
        query = MondayGraphQLOperations.get_operation_with_fragments("mutation", "create_doc_block")

        # Prepare variables
        variables = {}
        if doc_id is not None:
            variables["doc_id"] = doc_id
        if type is not None:
            variables["type"] = type
        if content is not None:
            variables["content"] = content
        if after_block_id is not None:
            variables["after_block_id"] = after_block_id
        if parent_block_id is not None:
            variables["parent_block_id"] = parent_block_id

        # Execute the GraphQL operation
        try:
            response = await self._monday_client.get_graphql_client().execute(
                query=query,
                variables=variables,
                operation_name="create_doc_block"
            )
            return response
        except Exception as e:
            return GraphQLResponse(
                success=False,
                message=f"Failed to execute mutation create_doc_block: {str(e)}"
            )

    async def create_folder(
        self,
        name: str,
        workspace_id: Optional[str] = None,
        parent_folder_id: Optional[str] = None,
        color: Optional[str] = None
    ) -> GraphQLResponse:
        """Create a folder

        GraphQL Operation: Mutation create_folder

        Args:
            name (str, required): Parameter for name
            workspace_id (str, optional): Parameter for workspace_id
            parent_folder_id (str, optional): Parameter for parent_folder_id
            color (str, optional): Parameter for color

        Returns:
            GraphQLResponse: The GraphQL response containing the operation result

        Example:
            await monday_datasource.create_folder(name="My Folder")
        """
        # Get the complete GraphQL operation with fragments
        query = MondayGraphQLOperations.get_operation_with_fragments("mutation", "create_folder")

        # Prepare variables
        variables = {}
        if name is not None:
            variables["name"] = name
        if workspace_id is not None:
            variables["workspace_id"] = workspace_id
        if parent_folder_id is not None:
            variables["parent_folder_id"] = parent_folder_id
        if color is not None:
            variables["color"] = color

        # Execute the GraphQL operation
        try:
            response = await self._monday_client.get_graphql_client().execute(
                query=query,
                variables=variables,
                operation_name="create_folder"
            )
            return response
        except Exception as e:
            return GraphQLResponse(
                success=False,
                message=f"Failed to execute mutation create_folder: {str(e)}"
            )

    async def update_folder(
        self,
        folder_id: str,
        name: Optional[str] = None,
        color: Optional[str] = None,
        parent_folder_id: Optional[str] = None
    ) -> GraphQLResponse:
        """Update a folder

        GraphQL Operation: Mutation update_folder

        Args:
            folder_id (str, required): Parameter for folder_id
            name (str, optional): Parameter for name
            color (str, optional): Parameter for color
            parent_folder_id (str, optional): Parameter for parent_folder_id

        Returns:
            GraphQLResponse: The GraphQL response containing the operation result

        Example:
            await monday_datasource.update_folder(folder_id="123", name="New Name")
        """
        # Get the complete GraphQL operation with fragments
        query = MondayGraphQLOperations.get_operation_with_fragments("mutation", "update_folder")

        # Prepare variables
        variables = {}
        if folder_id is not None:
            variables["folder_id"] = folder_id
        if name is not None:
            variables["name"] = name
        if color is not None:
            variables["color"] = color
        if parent_folder_id is not None:
            variables["parent_folder_id"] = parent_folder_id

        # Execute the GraphQL operation
        try:
            response = await self._monday_client.get_graphql_client().execute(
                query=query,
                variables=variables,
                operation_name="update_folder"
            )
            return response
        except Exception as e:
            return GraphQLResponse(
                success=False,
                message=f"Failed to execute mutation update_folder: {str(e)}"
            )

    async def delete_folder(
        self,
        folder_id: str
    ) -> GraphQLResponse:
        """Delete a folder

        GraphQL Operation: Mutation delete_folder

        Args:
            folder_id (str, required): Parameter for folder_id

        Returns:
            GraphQLResponse: The GraphQL response containing the operation result

        Example:
            await monday_datasource.delete_folder(folder_id="123")
        """
        # Get the complete GraphQL operation with fragments
        query = MondayGraphQLOperations.get_operation_with_fragments("mutation", "delete_folder")

        # Prepare variables
        variables = {}
        if folder_id is not None:
            variables["folder_id"] = folder_id

        # Execute the GraphQL operation
        try:
            response = await self._monday_client.get_graphql_client().execute(
                query=query,
                variables=variables,
                operation_name="delete_folder"
            )
            return response
        except Exception as e:
            return GraphQLResponse(
                success=False,
                message=f"Failed to execute mutation delete_folder: {str(e)}"
            )

    async def add_file_to_column(
        self,
        item_id: str,
        column_id: str,
        file: Any
    ) -> GraphQLResponse:
        """Add a file to a file column

        GraphQL Operation: Mutation add_file_to_column

        Args:
            item_id (str, required): Parameter for item_id
            column_id (str, required): Parameter for column_id
            file (Any, required): Parameter for file

        Returns:
            GraphQLResponse: The GraphQL response containing the operation result

        Example:
            await monday_datasource.add_file_to_column(item_id="123", column_id="files", file=file_object)
        """
        # Get the complete GraphQL operation with fragments
        query = MondayGraphQLOperations.get_operation_with_fragments("mutation", "add_file_to_column")

        # Prepare variables
        variables = {}
        if item_id is not None:
            variables["item_id"] = item_id
        if column_id is not None:
            variables["column_id"] = column_id
        if file is not None:
            variables["file"] = file

        # Execute the GraphQL operation
        try:
            response = await self._monday_client.get_graphql_client().execute(
                query=query,
                variables=variables,
                operation_name="add_file_to_column"
            )
            return response
        except Exception as e:
            return GraphQLResponse(
                success=False,
                message=f"Failed to execute mutation add_file_to_column: {str(e)}"
            )

    async def add_file_to_update(
        self,
        update_id: str,
        file: Any
    ) -> GraphQLResponse:
        """Add a file to an update

        GraphQL Operation: Mutation add_file_to_update

        Args:
            update_id (str, required): Parameter for update_id
            file (Any, required): Parameter for file

        Returns:
            GraphQLResponse: The GraphQL response containing the operation result

        Example:
            await monday_datasource.add_file_to_update(update_id="123", file=file_object)
        """
        # Get the complete GraphQL operation with fragments
        query = MondayGraphQLOperations.get_operation_with_fragments("mutation", "add_file_to_update")

        # Prepare variables
        variables = {}
        if update_id is not None:
            variables["update_id"] = update_id
        if file is not None:
            variables["file"] = file

        # Execute the GraphQL operation
        try:
            response = await self._monday_client.get_graphql_client().execute(
                query=query,
                variables=variables,
                operation_name="add_file_to_update"
            )
            return response
        except Exception as e:
            return GraphQLResponse(
                success=False,
                message=f"Failed to execute mutation add_file_to_update: {str(e)}"
            )

    async def add_users_to_team(
        self,
        team_id: str,
        user_ids: List[str]
    ) -> GraphQLResponse:
        """Add users to a team

        GraphQL Operation: Mutation add_users_to_team

        Args:
            team_id (str, required): Parameter for team_id
            user_ids (List[str], required): Parameter for user_ids

        Returns:
            GraphQLResponse: The GraphQL response containing the operation result

        Example:
            await monday_datasource.add_users_to_team(team_id="123", user_ids=["456"])
        """
        # Get the complete GraphQL operation with fragments
        query = MondayGraphQLOperations.get_operation_with_fragments("mutation", "add_users_to_team")

        # Prepare variables
        variables = {}
        if team_id is not None:
            variables["team_id"] = team_id
        if user_ids is not None:
            variables["user_ids"] = user_ids

        # Execute the GraphQL operation
        try:
            response = await self._monday_client.get_graphql_client().execute(
                query=query,
                variables=variables,
                operation_name="add_users_to_team"
            )
            return response
        except Exception as e:
            return GraphQLResponse(
                success=False,
                message=f"Failed to execute mutation add_users_to_team: {str(e)}"
            )

    async def remove_users_from_team(
        self,
        team_id: str,
        user_ids: List[str]
    ) -> GraphQLResponse:
        """Remove users from a team

        GraphQL Operation: Mutation remove_users_from_team

        Args:
            team_id (str, required): Parameter for team_id
            user_ids (List[str], required): Parameter for user_ids

        Returns:
            GraphQLResponse: The GraphQL response containing the operation result

        Example:
            await monday_datasource.remove_users_from_team(team_id="123", user_ids=["456"])
        """
        # Get the complete GraphQL operation with fragments
        query = MondayGraphQLOperations.get_operation_with_fragments("mutation", "remove_users_from_team")

        # Prepare variables
        variables = {}
        if team_id is not None:
            variables["team_id"] = team_id
        if user_ids is not None:
            variables["user_ids"] = user_ids

        # Execute the GraphQL operation
        try:
            response = await self._monday_client.get_graphql_client().execute(
                query=query,
                variables=variables,
                operation_name="remove_users_from_team"
            )
            return response
        except Exception as e:
            return GraphQLResponse(
                success=False,
                message=f"Failed to execute mutation remove_users_from_team: {str(e)}"
            )

    # =============================================================================
    # UTILITY AND HELPER METHODS
    # =============================================================================

    def get_monday_client(self) -> MondayClient:
        """Get the underlying Monday.com client."""
        return self._monday_client

    def get_available_operations(self) -> Dict[str, Any]:
        """Get information about available GraphQL operations."""
        return MondayGraphQLOperations.get_all_operations()

    def get_operation_info(self) -> Dict[str, Any]:
        """Get comprehensive information about all available methods."""

        # Query operations
        query_operations = [
            "me", "users", "account", "boards", "items",
            "items_page_by_column_values", "next_items_page",
            "workspaces", "teams", "tags", "updates", "docs",
            "folders", "app_subscription", "webhooks",
            "boards_activity_logs", "version", "complexity",
            "rate_limit_status", "assets"
        ]

        # Mutation operations
        mutation_operations = [
            "create_board", "update_board", "archive_board", "delete_board", "duplicate_board",
            "create_column", "change_column_title", "change_column_metadata", "delete_column",
            "change_column_value", "change_multiple_column_values", "change_simple_column_value",
            "create_group", "update_group", "duplicate_group", "archive_group", "delete_group",
            "move_item_to_group", "create_item", "duplicate_item", "move_item_to_board",
            "archive_item", "delete_item", "clear_item_updates", "create_subitem",
            "create_update", "edit_update", "delete_update", "like_update",
            "create_notification", "create_or_get_tag",
            "create_workspace", "update_workspace", "delete_workspace",
            "add_users_to_workspace", "delete_users_from_workspace",
            "add_teams_to_workspace", "delete_teams_from_workspace",
            "add_subscribers_to_board", "delete_subscribers_from_board",
            "create_webhook", "delete_webhook",
            "create_doc", "create_doc_block",
            "create_folder", "update_folder", "delete_folder",
            "add_file_to_column", "add_file_to_update",
            "add_users_to_team", "remove_users_from_team"
        ]

        return {
            "total_methods": len(query_operations) + len(mutation_operations),
            "queries": len(query_operations),
            "mutations": len(mutation_operations),
            "operations": {
                "queries": query_operations,
                "mutations": mutation_operations
            },
            "coverage": {
                "users": "Read operations + team membership",
                "account": "Read operations",
                "boards": "Complete CRUD operations + duplicate + subscribers",
                "items": "Complete CRUD operations + duplicate + move",
                "columns": "Complete CRUD operations + value changes",
                "groups": "Complete CRUD operations + duplicate",
                "subitems": "Create operations",
                "updates": "Complete CRUD operations + like",
                "workspaces": "Complete CRUD operations + user/team management",
                "teams": "User management operations",
                "tags": "Create/get operations",
                "docs": "Create operations + blocks",
                "folders": "Complete CRUD operations",
                "webhooks": "Create and delete operations",
                "notifications": "Create operations",
                "assets": "File upload operations"
            }
        }

    async def validate_connection(self) -> bool:
        """Validate the Monday.com connection by fetching current user information."""
        try:
            response = await self.me()
            return response.success and response.data is not None
        except Exception as e:
            print(f"Connection validation failed: {e}")
            return False

    # =============================================================================
    # CONVENIENCE METHODS FOR COMMON OPERATIONS
    # =============================================================================

    async def get_current_user(self) -> GraphQLResponse:
        """Get current user information."""
        return await self.me()

    async def get_all_boards(self, limit: int = 50) -> GraphQLResponse:
        """Get all boards."""
        return await self.boards(limit=limit)

    async def get_board_items(self, board_id: str, limit: int = 100) -> GraphQLResponse:
        """Get items from a specific board."""
        # First get the board to get its items
        response = await self.boards(ids=[board_id], limit=1)
        return response

    async def create_simple_item(
        self,
        board_id: str,
        item_name: str,
        group_id: Optional[str] = None,
        column_values: Optional[Dict[str, Any]] = None
    ) -> GraphQLResponse:
        """Create a simple item with basic information."""
        column_values_str = None
        if column_values:
            import json
            column_values_str = json.dumps(column_values)

        return await self.create_item(
            board_id=board_id,
            item_name=item_name,
            group_id=group_id,
            column_values=column_values_str
        )

    async def update_item_column(
        self,
        board_id: str,
        item_id: str,
        column_id: str,
        value: Any
    ) -> GraphQLResponse:
        """Update a single column value for an item."""
        import json
        value_str = json.dumps(value) if not isinstance(value, str) else value
        return await self.change_column_value(
            board_id=board_id,
            item_id=item_id,
            column_id=column_id,
            value=value_str
        )

    async def add_comment_to_item(
        self,
        item_id: str,
        body: str
    ) -> GraphQLResponse:
        """Add a comment/update to an item."""
        return await self.create_update(item_id=item_id, body=body)

    async def get_board_groups(self, board_id: str) -> GraphQLResponse:
        """Get groups from a specific board."""
        return await self.boards(ids=[board_id], limit=1)

    async def get_board_columns(self, board_id: str) -> GraphQLResponse:
        """Get columns from a specific board."""
        return await self.boards(ids=[board_id], limit=1)

    async def create_simple_board(
        self,
        name: str,
        kind: str = "public",
        workspace_id: Optional[str] = None,
        description: Optional[str] = None
    ) -> GraphQLResponse:
        """Create a simple board."""
        return await self.create_board(
            board_name=name,
            board_kind=kind,
            workspace_id=workspace_id,
            description=description
        )

    async def search_items_by_column(
        self,
        board_id: str,
        column_id: str,
        column_values: List[str],
        limit: int = 50
    ) -> GraphQLResponse:
        """Search for items by column values."""
        columns = [{"column_id": column_id, "column_values": column_values}]
        return await self.items_page_by_column_values(
            board_id=board_id,
            columns=columns,
            limit=limit
        )
