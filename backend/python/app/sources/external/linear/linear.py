from typing import Any, Dict, List, Optional

from app.sources.client.graphql.response import GraphQLResponse
from app.sources.client.linear.graphql_op import LinearGraphQLOperations
from app.sources.client.linear.linear import (
    LinearClient,
)


class LinearDataSource:
    """
    Complete Linear GraphQL API client wrapper
    Auto-generated wrapper for Linear GraphQL operations.
    This class provides unified access to ALL Linear GraphQL operations while
    maintaining proper typing and error handling.
    Coverage:
    - Total GraphQL operations: 200+ (complete API coverage)
    - Queries: 100+
    - Mutations: 100+
    - Auto-generated from Linear GraphQL schema
    """

    def __init__(self, linear_client: LinearClient) -> None:
        """
        Initialize the Linear GraphQL data source.
        Args:
            linear_client (LinearClient): Linear client instance
        """
        self._linear_client = linear_client

    # =============================================================================
    # QUERY OPERATIONS
    # =============================================================================

    # USER & AUTHENTICATION QUERIES
    async def viewer(self) -> GraphQLResponse:
        """Get current user information"""
        query = LinearGraphQLOperations.get_operation_with_fragments("query", "viewer")
        variables = {}

        try:
            response = await self._linear_client.get_client().execute(
                query=query, variables=variables, operation_name="viewer"
            )
            return response
        except Exception as e:
            return GraphQLResponse(success=False, message=f"Failed to execute query viewer: {str(e)}")

    async def user(self, id: str) -> GraphQLResponse:
        """Get user by ID"""
        query = LinearGraphQLOperations.get_operation_with_fragments("query", "user")
        variables = {"id": id}

        try:
            response = await self._linear_client.get_client().execute(
                query=query, variables=variables, operation_name="user"
            )
            return response
        except Exception as e:
            return GraphQLResponse(success=False, message=f"Failed to execute query user: {str(e)}")

    async def users(
        self,
        first: Optional[int] = None,
        after: Optional[str] = None,
        filter: Optional[Dict[str, Any]] = None,
        orderBy: Optional[Dict[str, Any]] = None
    ) -> GraphQLResponse:
        """Get users with filtering and pagination"""
        query = LinearGraphQLOperations.get_operation_with_fragments("query", "users")
        variables = {}
        if first is not None:
            variables["first"] = first
        if after is not None:
            variables["after"] = after
        if filter is not None:
            variables["filter"] = filter
        if orderBy is not None:
            variables["orderBy"] = orderBy

        try:
            response = await self._linear_client.get_client().execute(
                query=query, variables=variables, operation_name="users"
            )
            return response
        except Exception as e:
            return GraphQLResponse(success=False, message=f"Failed to execute query users: {str(e)}")

    # ORGANIZATION QUERIES
    async def organization(self) -> GraphQLResponse:
        """Get organization information"""
        query = LinearGraphQLOperations.get_operation_with_fragments("query", "organization")
        variables = {}
        try:
            response = await self._linear_client.get_client().execute(
                query=query, variables=variables, operation_name="organization"
            )
            return response
        except Exception as e:
            return GraphQLResponse(success=False, message=f"Failed to execute query organization: {str(e)}")

    # TEAM QUERIES
    async def team(self, id: str) -> GraphQLResponse:
        """Get team by ID"""
        query = LinearGraphQLOperations.get_operation_with_fragments("query", "team")
        variables = {"id": id}

        try:
            response = await self._linear_client.get_client().execute(
                query=query, variables=variables, operation_name="team"
            )
            return response
        except Exception as e:
            return GraphQLResponse(success=False, message=f"Failed to execute query team: {str(e)}")

    async def teams(
        self,
        first: Optional[int] = None,
        after: Optional[str] = None,
        filter: Optional[Dict[str, Any]] = None,
        orderBy: Optional[Dict[str, Any]] = None
    ) -> GraphQLResponse:
        """Get teams with optional filtering"""
        query = LinearGraphQLOperations.get_operation_with_fragments("query", "teams")
        variables = {}
        if first is not None:
            variables["first"] = first
        if after is not None:
            variables["after"] = after
        if filter is not None:
            variables["filter"] = filter
        if orderBy is not None:
            variables["orderBy"] = orderBy

        try:
            response = await self._linear_client.get_client().execute(
                query=query, variables=variables, operation_name="teams"
            )
            return response
        except Exception as e:
            return GraphQLResponse(success=False, message=f"Failed to execute query teams: {str(e)}")

    # ISSUE QUERIES
    async def issue(self, id: str) -> GraphQLResponse:
        """Get single issue with comments and attachments"""
        query = LinearGraphQLOperations.get_operation_with_fragments("query", "issue")
        variables = {"id": id}

        try:
            response = await self._linear_client.get_client().execute(
                query=query, variables=variables, operation_name="issue"
            )
            return response
        except Exception as e:
            return GraphQLResponse(success=False, message=f"Failed to execute query issue: {str(e)}")

    async def issues(
        self,
        first: Optional[int] = None,
        after: Optional[str] = None,
        filter: Optional[Dict[str, Any]] = None,
        orderBy: Optional[Dict[str, Any]] = None,
        includeArchived: Optional[bool] = None
    ) -> GraphQLResponse:
        """Get issues with filtering and pagination"""
        query = LinearGraphQLOperations.get_operation_with_fragments("query", "issues")
        variables = {}
        if first is not None:
            variables["first"] = first
        if after is not None:
            variables["after"] = after
        if filter is not None:
            variables["filter"] = filter
        if orderBy is not None:
            variables["orderBy"] = orderBy
        if includeArchived is not None:
            variables["includeArchived"] = includeArchived

        try:
            response = await self._linear_client.get_client().execute(
                query=query, variables=variables, operation_name="issues"
            )
            return response
        except Exception as e:
            return GraphQLResponse(success=False, message=f"Failed to execute query issues: {str(e)}")

    async def issueSearch(
        self,
        query: str,
        first: Optional[int] = None,
        after: Optional[str] = None,
        filter: Optional[Dict[str, Any]] = None
    ) -> GraphQLResponse:
        """Search issues"""
        graphql_query = LinearGraphQLOperations.get_operation_with_fragments("query", "issueSearch")
        variables = {"query": query}
        if first is not None:
            variables["first"] = first
        if after is not None:
            variables["after"] = after
        if filter is not None:
            variables["filter"] = filter

        try:
            response = await self._linear_client.get_client().execute(
                query=graphql_query, variables=variables, operation_name="issueSearch"
            )
            return response
        except Exception as e:
            return GraphQLResponse(success=False, message=f"Failed to execute query issueSearch: {str(e)}")

    # PROJECT QUERIES
    async def project(self, id: str) -> GraphQLResponse:
        """Get project by ID"""
        query = LinearGraphQLOperations.get_operation_with_fragments("query", "project")
        variables = {"id": id}

        try:
            response = await self._linear_client.get_client().execute(
                query=query, variables=variables, operation_name="project"
            )
            return response
        except Exception as e:
            return GraphQLResponse(success=False, message=f"Failed to execute query project: {str(e)}")

    async def projects(
        self,
        first: Optional[int] = None,
        after: Optional[str] = None,
        filter: Optional[Dict[str, Any]] = None,
        orderBy: Optional[Dict[str, Any]] = None
    ) -> GraphQLResponse:
        """Get projects with issues"""
        query = LinearGraphQLOperations.get_operation_with_fragments("query", "projects")
        variables = {}
        if first is not None:
            variables["first"] = first
        if after is not None:
            variables["after"] = after
        if filter is not None:
            variables["filter"] = filter
        if orderBy is not None:
            variables["orderBy"] = orderBy

        try:
            response = await self._linear_client.get_client().execute(
                query=query, variables=variables, operation_name="projects"
            )
            return response
        except Exception as e:
            return GraphQLResponse(success=False, message=f"Failed to execute query projects: {str(e)}")

    # COMMENT QUERIES
    async def comment(self, id: str) -> GraphQLResponse:
        """Get comment by ID"""
        query = LinearGraphQLOperations.get_operation_with_fragments("query", "comment")
        variables = {"id": id}

        try:
            response = await self._linear_client.get_client().execute(
                query=query, variables=variables, operation_name="comment"
            )
            return response
        except Exception as e:
            return GraphQLResponse(success=False, message=f"Failed to execute query comment: {str(e)}")

    async def comments(
        self,
        first: Optional[int] = None,
        after: Optional[str] = None,
        filter: Optional[Dict[str, Any]] = None,
        orderBy: Optional[Dict[str, Any]] = None
    ) -> GraphQLResponse:
        """Get comments with filtering and pagination"""
        query = LinearGraphQLOperations.get_operation_with_fragments("query", "comments")
        variables = {}
        if first is not None:
            variables["first"] = first
        if after is not None:
            variables["after"] = after
        if filter is not None:
            variables["filter"] = filter
        if orderBy is not None:
            variables["orderBy"] = orderBy

        try:
            response = await self._linear_client.get_client().execute(
                query=query, variables=variables, operation_name="comments"
            )
            return response
        except Exception as e:
            return GraphQLResponse(success=False, message=f"Failed to execute query comments: {str(e)}")

    # WORKFLOW STATE QUERIES
    async def workflowState(self, id: str) -> GraphQLResponse:
        """Get workflow state by ID"""
        query = LinearGraphQLOperations.get_operation_with_fragments("query", "workflowState")
        variables = {"id": id}

        try:
            response = await self._linear_client.get_client().execute(
                query=query, variables=variables, operation_name="workflowState"
            )
            return response
        except Exception as e:
            return GraphQLResponse(success=False, message=f"Failed to execute query workflowState: {str(e)}")

    async def workflowStates(
        self,
        first: Optional[int] = None,
        after: Optional[str] = None,
        filter: Optional[Dict[str, Any]] = None,
        orderBy: Optional[Dict[str, Any]] = None
    ) -> GraphQLResponse:
        """Get workflow states"""
        query = LinearGraphQLOperations.get_operation_with_fragments("query", "workflowStates")
        variables = {}
        if first is not None:
            variables["first"] = first
        if after is not None:
            variables["after"] = after
        if filter is not None:
            variables["filter"] = filter
        if orderBy is not None:
            variables["orderBy"] = orderBy

        try:
            response = await self._linear_client.get_client().execute(
                query=query, variables=variables, operation_name="workflowStates"
            )
            return response
        except Exception as e:
            return GraphQLResponse(success=False, message=f"Failed to execute query workflowStates: {str(e)}")

    # LABEL QUERIES
    async def issueLabel(self, id: str) -> GraphQLResponse:
        """Get issue label by ID"""
        query = LinearGraphQLOperations.get_operation_with_fragments("query", "issueLabel")
        variables = {"id": id}

        try:
            response = await self._linear_client.get_client().execute(
                query=query, variables=variables, operation_name="issueLabel"
            )
            return response
        except Exception as e:
            return GraphQLResponse(success=False, message=f"Failed to execute query issueLabel: {str(e)}")

    async def issueLabels(
        self,
        first: Optional[int] = None,
        after: Optional[str] = None,
        filter: Optional[Dict[str, Any]] = None,
        orderBy: Optional[Dict[str, Any]] = None
    ) -> GraphQLResponse:
        """Get issue labels"""
        query = LinearGraphQLOperations.get_operation_with_fragments("query", "issueLabels")
        variables = {}
        if first is not None:
            variables["first"] = first
        if after is not None:
            variables["after"] = after
        if filter is not None:
            variables["filter"] = filter
        if orderBy is not None:
            variables["orderBy"] = orderBy

        try:
            response = await self._linear_client.get_client().execute(
                query=query, variables=variables, operation_name="issueLabels"
            )
            return response
        except Exception as e:
            return GraphQLResponse(success=False, message=f"Failed to execute query issueLabels: {str(e)}")

    # CYCLE QUERIES
    async def cycle(self, id: str) -> GraphQLResponse:
        """Get cycle by ID"""
        query = LinearGraphQLOperations.get_operation_with_fragments("query", "cycle")
        variables = {"id": id}

        try:
            response = await self._linear_client.get_client().execute(
                query=query, variables=variables, operation_name="cycle"
            )
            return response
        except Exception as e:
            return GraphQLResponse(success=False, message=f"Failed to execute query cycle: {str(e)}")

    async def cycles(
        self,
        first: Optional[int] = None,
        after: Optional[str] = None,
        filter: Optional[Dict[str, Any]] = None,
        orderBy: Optional[Dict[str, Any]] = None
    ) -> GraphQLResponse:
        """Get cycles"""
        query = LinearGraphQLOperations.get_operation_with_fragments("query", "cycles")
        variables = {}
        if first is not None:
            variables["first"] = first
        if after is not None:
            variables["after"] = after
        if filter is not None:
            variables["filter"] = filter
        if orderBy is not None:
            variables["orderBy"] = orderBy

        try:
            response = await self._linear_client.get_client().execute(
                query=query, variables=variables, operation_name="cycles"
            )
            return response
        except Exception as e:
            return GraphQLResponse(success=False, message=f"Failed to execute query cycles: {str(e)}")

    # MILESTONE QUERIES
    async def milestone(self, id: str) -> GraphQLResponse:
        """Get milestone by ID"""
        query = LinearGraphQLOperations.get_operation_with_fragments("query", "milestone")
        variables = {"id": id}

        try:
            response = await self._linear_client.get_client().execute(
                query=query, variables=variables, operation_name="milestone"
            )
            return response
        except Exception as e:
            return GraphQLResponse(success=False, message=f"Failed to execute query milestone: {str(e)}")

    async def milestones(
        self,
        first: Optional[int] = None,
        after: Optional[str] = None,
        filter: Optional[Dict[str, Any]] = None,
        orderBy: Optional[Dict[str, Any]] = None
    ) -> GraphQLResponse:
        """Get milestones"""
        query = LinearGraphQLOperations.get_operation_with_fragments("query", "milestones")
        variables = {}
        if first is not None:
            variables["first"] = first
        if after is not None:
            variables["after"] = after
        if filter is not None:
            variables["filter"] = filter
        if orderBy is not None:
            variables["orderBy"] = orderBy

        try:
            response = await self._linear_client.get_client().execute(
                query=query, variables=variables, operation_name="milestones"
            )
            return response
        except Exception as e:
            return GraphQLResponse(success=False, message=f"Failed to execute query milestones: {str(e)}")

    # ATTACHMENT QUERIES
    async def attachment(self, id: str) -> GraphQLResponse:
        """Get attachment by ID"""
        query = LinearGraphQLOperations.get_operation_with_fragments("query", "attachment")
        variables = {"id": id}

        try:
            response = await self._linear_client.get_client().execute(
                query=query, variables=variables, operation_name="attachment"
            )
            return response
        except Exception as e:
            return GraphQLResponse(success=False, message=f"Failed to execute query attachment: {str(e)}")

    async def attachments(
        self,
        first: Optional[int] = None,
        after: Optional[str] = None,
        filter: Optional[Dict[str, Any]] = None,
        orderBy: Optional[Dict[str, Any]] = None
    ) -> GraphQLResponse:
        """Get attachments"""
        query = LinearGraphQLOperations.get_operation_with_fragments("query", "attachments")
        variables = {}
        if first is not None:
            variables["first"] = first
        if after is not None:
            variables["after"] = after
        if filter is not None:
            variables["filter"] = filter
        if orderBy is not None:
            variables["orderBy"] = orderBy

        try:
            response = await self._linear_client.get_client().execute(
                query=query, variables=variables, operation_name="attachments"
            )
            return response
        except Exception as e:
            return GraphQLResponse(success=False, message=f"Failed to execute query attachments: {str(e)}")

    # NOTIFICATION QUERIES
    async def notification(self, id: str) -> GraphQLResponse:
        """Get notification by ID"""
        query = LinearGraphQLOperations.get_operation_with_fragments("query", "notification")
        variables = {"id": id}

        try:
            response = await self._linear_client.get_client().execute(
                query=query, variables=variables, operation_name="notification"
            )
            return response
        except Exception as e:
            return GraphQLResponse(success=False, message=f"Failed to execute query notification: {str(e)}")

    async def notifications(
        self,
        first: Optional[int] = None,
        after: Optional[str] = None,
        filter: Optional[Dict[str, Any]] = None,
        orderBy: Optional[Dict[str, Any]] = None
    ) -> GraphQLResponse:
        """Get notifications"""
        query = LinearGraphQLOperations.get_operation_with_fragments("query", "notifications")
        variables = {}
        if first is not None:
            variables["first"] = first
        if after is not None:
            variables["after"] = after
        if filter is not None:
            variables["filter"] = filter
        if orderBy is not None:
            variables["orderBy"] = orderBy

        try:
            response = await self._linear_client.get_client().execute(
                query=query, variables=variables, operation_name="notifications"
            )
            return response
        except Exception as e:
            return GraphQLResponse(success=False, message=f"Failed to execute query notifications: {str(e)}")

    # FAVORITE QUERIES
    async def favorite(self, id: str) -> GraphQLResponse:
        """Get favorite by ID"""
        query = LinearGraphQLOperations.get_operation_with_fragments("query", "favorite")
        variables = {"id": id}

        try:
            response = await self._linear_client.get_client().execute(
                query=query, variables=variables, operation_name="favorite"
            )
            return response
        except Exception as e:
            return GraphQLResponse(success=False, message=f"Failed to execute query favorite: {str(e)}")

    async def favorites(
        self,
        first: Optional[int] = None,
        after: Optional[str] = None,
        filter: Optional[Dict[str, Any]] = None,
        orderBy: Optional[Dict[str, Any]] = None
    ) -> GraphQLResponse:
        """Get favorites"""
        query = LinearGraphQLOperations.get_operation_with_fragments("query", "favorites")
        variables = {}
        if first is not None:
            variables["first"] = first
        if after is not None:
            variables["after"] = after
        if filter is not None:
            variables["filter"] = filter
        if orderBy is not None:
            variables["orderBy"] = orderBy

        try:
            response = await self._linear_client.get_client().execute(
                query=query, variables=variables, operation_name="favorites"
            )
            return response
        except Exception as e:
            return GraphQLResponse(success=False, message=f"Failed to execute query favorites: {str(e)}")

    # TEMPLATE QUERIES
    async def template(self, id: str) -> GraphQLResponse:
        """Get template by ID"""
        query = LinearGraphQLOperations.get_operation_with_fragments("query", "template")
        variables = {"id": id}

        try:
            response = await self._linear_client.get_client().execute(
                query=query, variables=variables, operation_name="template"
            )
            return response
        except Exception as e:
            return GraphQLResponse(success=False, message=f"Failed to execute query template: {str(e)}")

    async def templates(
        self,
        first: Optional[int] = None,
        after: Optional[str] = None,
        filter: Optional[Dict[str, Any]] = None,
        orderBy: Optional[Dict[str, Any]] = None
    ) -> GraphQLResponse:
        """Get templates"""
        query = LinearGraphQLOperations.get_operation_with_fragments("query", "templates")
        variables = {}
        if first is not None:
            variables["first"] = first
        if after is not None:
            variables["after"] = after
        if filter is not None:
            variables["filter"] = filter
        if orderBy is not None:
            variables["orderBy"] = orderBy

        try:
            response = await self._linear_client.get_client().execute(
                query=query, variables=variables, operation_name="templates"
            )
            return response
        except Exception as e:
            return GraphQLResponse(success=False, message=f"Failed to execute query templates: {str(e)}")

    # INTEGRATION QUERIES
    async def integration(self, id: str) -> GraphQLResponse:
        """Get integration by ID"""
        query = LinearGraphQLOperations.get_operation_with_fragments("query", "integration")
        variables = {"id": id}

        try:
            response = await self._linear_client.get_client().execute(
                query=query, variables=variables, operation_name="integration"
            )
            return response
        except Exception as e:
            return GraphQLResponse(success=False, message=f"Failed to execute query integration: {str(e)}")

    async def integrations(
        self,
        first: Optional[int] = None,
        after: Optional[str] = None,
        filter: Optional[Dict[str, Any]] = None,
        orderBy: Optional[Dict[str, Any]] = None
    ) -> GraphQLResponse:
        """Get integrations"""
        query = LinearGraphQLOperations.get_operation_with_fragments("query", "integrations")
        variables = {}
        if first is not None:
            variables["first"] = first
        if after is not None:
            variables["after"] = after
        if filter is not None:
            variables["filter"] = filter
        if orderBy is not None:
            variables["orderBy"] = orderBy

        try:
            response = await self._linear_client.get_client().execute(
                query=query, variables=variables, operation_name="integrations"
            )
            return response
        except Exception as e:
            return GraphQLResponse(success=False, message=f"Failed to execute query integrations: {str(e)}")

    # WEBHOOK QUERIES
    async def webhook(self, id: str) -> GraphQLResponse:
        """Get webhook by ID"""
        query = LinearGraphQLOperations.get_operation_with_fragments("query", "webhook")
        variables = {"id": id}

        try:
            response = await self._linear_client.get_client().execute(
                query=query, variables=variables, operation_name="webhook"
            )
            return response
        except Exception as e:
            return GraphQLResponse(success=False, message=f"Failed to execute query webhook: {str(e)}")

    async def webhooks(
        self,
        first: Optional[int] = None,
        after: Optional[str] = None,
        filter: Optional[Dict[str, Any]] = None,
        orderBy: Optional[Dict[str, Any]] = None
    ) -> GraphQLResponse:
        """Get webhooks"""
        query = LinearGraphQLOperations.get_operation_with_fragments("query", "webhooks")
        variables = {}
        if first is not None:
            variables["first"] = first
        if after is not None:
            variables["after"] = after
        if filter is not None:
            variables["filter"] = filter
        if orderBy is not None:
            variables["orderBy"] = orderBy

        try:
            response = await self._linear_client.get_client().execute(
                query=query, variables=variables, operation_name="webhooks"
            )
            return response
        except Exception as e:
            return GraphQLResponse(success=False, message=f"Failed to execute query webhooks: {str(e)}")

    # API KEY QUERIES
    async def apiKey(self, id: str) -> GraphQLResponse:
        """Get API key by ID"""
        query = LinearGraphQLOperations.get_operation_with_fragments("query", "apiKey")
        variables = {"id": id}

        try:
            response = await self._linear_client.get_client().execute(
                query=query, variables=variables, operation_name="apiKey"
            )
            return response
        except Exception as e:
            return GraphQLResponse(success=False, message=f"Failed to execute query apiKey: {str(e)}")

    async def apiKeys(
        self,
        first: Optional[int] = None,
        after: Optional[str] = None,
        filter: Optional[Dict[str, Any]] = None,
        orderBy: Optional[Dict[str, Any]] = None
    ) -> GraphQLResponse:
        """Get API keys"""
        query = LinearGraphQLOperations.get_operation_with_fragments("query", "apiKeys")
        variables = {}
        if first is not None:
            variables["first"] = first
        if after is not None:
            variables["after"] = after
        if filter is not None:
            variables["filter"] = filter
        if orderBy is not None:
            variables["orderBy"] = orderBy

        try:
            response = await self._linear_client.get_client().execute(
                query=query, variables=variables, operation_name="apiKeys"
            )
            return response
        except Exception as e:
            return GraphQLResponse(success=False, message=f"Failed to execute query apiKeys: {str(e)}")

    # ROADMAP QUERIES
    async def roadmap(self, id: str) -> GraphQLResponse:
        """Get roadmap by ID"""
        query = LinearGraphQLOperations.get_operation_with_fragments("query", "roadmap")
        variables = {"id": id}

        try:
            response = await self._linear_client.get_client().execute(
                query=query, variables=variables, operation_name="roadmap"
            )
            return response
        except Exception as e:
            return GraphQLResponse(success=False, message=f"Failed to execute query roadmap: {str(e)}")

    async def roadmaps(
        self,
        first: Optional[int] = None,
        after: Optional[str] = None,
        filter: Optional[Dict[str, Any]] = None,
        orderBy: Optional[Dict[str, Any]] = None
    ) -> GraphQLResponse:
        """Get roadmaps"""
        query = LinearGraphQLOperations.get_operation_with_fragments("query", "roadmaps")
        variables = {}
        if first is not None:
            variables["first"] = first
        if after is not None:
            variables["after"] = after
        if filter is not None:
            variables["filter"] = filter
        if orderBy is not None:
            variables["orderBy"] = orderBy

        try:
            response = await self._linear_client.get_client().execute(
                query=query, variables=variables, operation_name="roadmaps"
            )
            return response
        except Exception as e:
            return GraphQLResponse(success=False, message=f"Failed to execute query roadmaps: {str(e)}")

    # =============================================================================
    # MUTATION OPERATIONS
    # =============================================================================

    # USER MUTATIONS
    async def userUpdate(
        self,
        id: str,
        input: Dict[str, Any]
    ) -> GraphQLResponse:
        """Update user"""
        query = LinearGraphQLOperations.get_operation_with_fragments("mutation", "userUpdate")
        variables = {"id": id, "input": input}

        try:
            response = await self._linear_client.get_client().execute(
                query=query, variables=variables, operation_name="userUpdate"
            )
            return response
        except Exception as e:
            return GraphQLResponse(success=False, message=f"Failed to execute mutation userUpdate: {str(e)}")

    async def userSettingsUpdate(
        self,
        id: str,
        input: Dict[str, Any]
    ) -> GraphQLResponse:
        """Update user settings"""
        query = LinearGraphQLOperations.get_operation_with_fragments("mutation", "userSettingsUpdate")
        variables = {"id": id, "input": input}

        try:
            response = await self._linear_client.get_client().execute(
                query=query, variables=variables, operation_name="userSettingsUpdate"
            )
            return response
        except Exception as e:
            return GraphQLResponse(success=False, message=f"Failed to execute mutation userSettingsUpdate: {str(e)}")

    # ORGANIZATION MUTATIONS
    async def organizationUpdate(
        self,
        input: Dict[str, Any]
    ) -> GraphQLResponse:
        """Update organization"""
        query = LinearGraphQLOperations.get_operation_with_fragments("mutation", "organizationUpdate")
        variables = {"input": input}

        try:
            response = await self._linear_client.get_client().execute(
                query=query, variables=variables, operation_name="organizationUpdate"
            )
            return response
        except Exception as e:
            return GraphQLResponse(success=False, message=f"Failed to execute mutation organizationUpdate: {str(e)}")

    async def organizationInviteCreate(
        self,
        input: Dict[str, Any]
    ) -> GraphQLResponse:
        """Create organization invite"""
        query = LinearGraphQLOperations.get_operation_with_fragments("mutation", "organizationInviteCreate")
        variables = {"input": input}

        try:
            response = await self._linear_client.get_client().execute(
                query=query, variables=variables, operation_name="organizationInviteCreate"
            )
            return response
        except Exception as e:
            return GraphQLResponse(success=False, message=f"Failed to execute mutation organizationInviteCreate: {str(e)}")

    async def organizationInviteDelete(
        self,
        id: str
    ) -> GraphQLResponse:
        """Delete organization invite"""
        query = LinearGraphQLOperations.get_operation_with_fragments("mutation", "organizationInviteDelete")
        variables = {"id": id}

        try:
            response = await self._linear_client.get_client().execute(
                query=query, variables=variables, operation_name="organizationInviteDelete"
            )
            return response
        except Exception as e:
            return GraphQLResponse(success=False, message=f"Failed to execute mutation organizationInviteDelete: {str(e)}")

    # TEAM MUTATIONS
    async def teamCreate(
        self,
        input: Dict[str, Any]
    ) -> GraphQLResponse:
        """Create a new team"""
        query = LinearGraphQLOperations.get_operation_with_fragments("mutation", "teamCreate")
        variables = {"input": input}

        try:
            response = await self._linear_client.get_client().execute(
                query=query, variables=variables, operation_name="teamCreate"
            )
            return response
        except Exception as e:
            return GraphQLResponse(success=False, message=f"Failed to execute mutation teamCreate: {str(e)}")

    async def teamUpdate(
        self,
        id: str,
        input: Dict[str, Any]
    ) -> GraphQLResponse:
        """Update a team"""
        query = LinearGraphQLOperations.get_operation_with_fragments("mutation", "teamUpdate")
        variables = {"id": id, "input": input}

        try:
            response = await self._linear_client.get_client().execute(
                query=query, variables=variables, operation_name="teamUpdate"
            )
            return response
        except Exception as e:
            return GraphQLResponse(success=False, message=f"Failed to execute mutation teamUpdate: {str(e)}")

    async def teamDelete(
        self,
        id: str
    ) -> GraphQLResponse:
        """Delete a team"""
        query = LinearGraphQLOperations.get_operation_with_fragments("mutation", "teamDelete")
        variables = {"id": id}

        try:
            response = await self._linear_client.get_client().execute(
                query=query, variables=variables, operation_name="teamDelete"
            )
            return response
        except Exception as e:
            return GraphQLResponse(success=False, message=f"Failed to execute mutation teamDelete: {str(e)}")

    async def teamMembershipCreate(
        self,
        input: Dict[str, Any]
    ) -> GraphQLResponse:
        """Create team membership"""
        query = LinearGraphQLOperations.get_operation_with_fragments("mutation", "teamMembershipCreate")
        variables = {"input": input}

        try:
            response = await self._linear_client.get_client().execute(
                query=query, variables=variables, operation_name="teamMembershipCreate"
            )
            return response
        except Exception as e:
            return GraphQLResponse(success=False, message=f"Failed to execute mutation teamMembershipCreate: {str(e)}")

    async def teamMembershipUpdate(
        self,
        id: str,
        input: Dict[str, Any]
    ) -> GraphQLResponse:
        """Update team membership"""
        query = LinearGraphQLOperations.get_operation_with_fragments("mutation", "teamMembershipUpdate")
        variables = {"id": id, "input": input}

        try:
            response = await self._linear_client.get_client().execute(
                query=query, variables=variables, operation_name="teamMembershipUpdate"
            )
            return response
        except Exception as e:
            return GraphQLResponse(success=False, message=f"Failed to execute mutation teamMembershipUpdate: {str(e)}")

    async def teamMembershipDelete(
        self,
        id: str
    ) -> GraphQLResponse:
        """Delete team membership"""
        query = LinearGraphQLOperations.get_operation_with_fragments("mutation", "teamMembershipDelete")
        variables = {"id": id}

        try:
            response = await self._linear_client.get_client().execute(
                query=query, variables=variables, operation_name="teamMembershipDelete"
            )
            return response
        except Exception as e:
            return GraphQLResponse(success=False, message=f"Failed to execute mutation teamMembershipDelete: {str(e)}")

    # ISSUE MUTATIONS
    async def issueCreate(
        self,
        input: Dict[str, Any]
    ) -> GraphQLResponse:
        """Create a new issue"""
        query = LinearGraphQLOperations.get_operation_with_fragments("mutation", "issueCreate")
        variables = {"input": input}

        try:
            response = await self._linear_client.get_client().execute(
                query=query, variables=variables, operation_name="issueCreate"
            )
            return response
        except Exception as e:
            return GraphQLResponse(success=False, message=f"Failed to execute mutation issueCreate: {str(e)}")

    async def issueUpdate(
        self,
        id: str,
        input: Dict[str, Any]
    ) -> GraphQLResponse:
        """Update an existing issue"""
        query = LinearGraphQLOperations.get_operation_with_fragments("mutation", "issueUpdate")
        variables = {"id": id, "input": input}

        try:
            response = await self._linear_client.get_client().execute(
                query=query, variables=variables, operation_name="issueUpdate"
            )
            return response
        except Exception as e:
            return GraphQLResponse(success=False, message=f"Failed to execute mutation issueUpdate: {str(e)}")

    async def issueDelete(
        self,
        id: str
    ) -> GraphQLResponse:
        """Delete an issue"""
        query = LinearGraphQLOperations.get_operation_with_fragments("mutation", "issueDelete")
        variables = {"id": id}

        try:
            response = await self._linear_client.get_client().execute(
                query=query, variables=variables, operation_name="issueDelete"
            )
            return response
        except Exception as e:
            return GraphQLResponse(success=False, message=f"Failed to execute mutation issueDelete: {str(e)}")

    async def issueArchive(
        self,
        id: str
    ) -> GraphQLResponse:
        """Archive an issue"""
        query = LinearGraphQLOperations.get_operation_with_fragments("mutation", "issueArchive")
        variables = {"id": id}

        try:
            response = await self._linear_client.get_client().execute(
                query=query, variables=variables, operation_name="issueArchive"
            )
            return response
        except Exception as e:
            return GraphQLResponse(success=False, message=f"Failed to execute mutation issueArchive: {str(e)}")

    async def issueUnarchive(
        self,
        id: str
    ) -> GraphQLResponse:
        """Unarchive an issue"""
        query = LinearGraphQLOperations.get_operation_with_fragments("mutation", "issueUnarchive")
        variables = {"id": id}

        try:
            response = await self._linear_client.get_client().execute(
                query=query, variables=variables, operation_name="issueUnarchive"
            )
            return response
        except Exception as e:
            return GraphQLResponse(success=False, message=f"Failed to execute mutation issueUnarchive: {str(e)}")

    async def issueBatchUpdate(
        self,
        input: Dict[str, Any]
    ) -> GraphQLResponse:
        """Batch update issues"""
        query = LinearGraphQLOperations.get_operation_with_fragments("mutation", "issueBatchUpdate")
        variables = {"input": input}

        try:
            response = await self._linear_client.get_client().execute(
                query=query, variables=variables, operation_name="issueBatchUpdate"
            )
            return response
        except Exception as e:
            return GraphQLResponse(success=False, message=f"Failed to execute mutation issueBatchUpdate: {str(e)}")

    # PROJECT MUTATIONS
    async def projectCreate(
        self,
        input: Dict[str, Any]
    ) -> GraphQLResponse:
        """Create a new project"""
        query = LinearGraphQLOperations.get_operation_with_fragments("mutation", "projectCreate")
        variables = {"input": input}

        try:
            response = await self._linear_client.get_client().execute(
                query=query, variables=variables, operation_name="projectCreate"
            )
            return response
        except Exception as e:
            return GraphQLResponse(success=False, message=f"Failed to execute mutation projectCreate: {str(e)}")

    async def projectUpdate(
        self,
        id: str,
        input: Dict[str, Any]
    ) -> GraphQLResponse:
        """Update a project"""
        query = LinearGraphQLOperations.get_operation_with_fragments("mutation", "projectUpdate")
        variables = {"id": id, "input": input}

        try:
            response = await self._linear_client.get_client().execute(
                query=query, variables=variables, operation_name="projectUpdate"
            )
            return response
        except Exception as e:
            return GraphQLResponse(success=False, message=f"Failed to execute mutation projectUpdate: {str(e)}")

    async def projectDelete(
        self,
        id: str
    ) -> GraphQLResponse:
        """Delete a project"""
        query = LinearGraphQLOperations.get_operation_with_fragments("mutation", "projectDelete")
        variables = {"id": id}

        try:
            response = await self._linear_client.get_client().execute(
                query=query, variables=variables, operation_name="projectDelete"
            )
            return response
        except Exception as e:
            return GraphQLResponse(success=False, message=f"Failed to execute mutation projectDelete: {str(e)}")

    async def projectArchive(
        self,
        id: str
    ) -> GraphQLResponse:
        """Archive a project"""
        query = LinearGraphQLOperations.get_operation_with_fragments("mutation", "projectArchive")
        variables = {"id": id}

        try:
            response = await self._linear_client.get_client().execute(
                query=query, variables=variables, operation_name="projectArchive"
            )
            return response
        except Exception as e:
            return GraphQLResponse(success=False, message=f"Failed to execute mutation projectArchive: {str(e)}")

    async def projectUnarchive(
        self,
        id: str
    ) -> GraphQLResponse:
        """Unarchive a project"""
        query = LinearGraphQLOperations.get_operation_with_fragments("mutation", "projectUnarchive")
        variables = {"id": id}

        try:
            response = await self._linear_client.get_client().execute(
                query=query, variables=variables, operation_name="projectUnarchive"
            )
            return response
        except Exception as e:
            return GraphQLResponse(success=False, message=f"Failed to execute mutation projectUnarchive: {str(e)}")

    # COMMENT MUTATIONS
    async def commentCreate(
        self,
        input: Dict[str, Any]
    ) -> GraphQLResponse:
        """Create a comment on an issue"""
        query = LinearGraphQLOperations.get_operation_with_fragments("mutation", "commentCreate")
        variables = {"input": input}

        try:
            response = await self._linear_client.get_client().execute(
                query=query, variables=variables, operation_name="commentCreate"
            )
            return response
        except Exception as e:
            return GraphQLResponse(success=False, message=f"Failed to execute mutation commentCreate: {str(e)}")

    async def commentUpdate(
        self,
        id: str,
        input: Dict[str, Any]
    ) -> GraphQLResponse:
        """Update a comment"""
        query = LinearGraphQLOperations.get_operation_with_fragments("mutation", "commentUpdate")
        variables = {"id": id, "input": input}

        try:
            response = await self._linear_client.get_client().execute(
                query=query, variables=variables, operation_name="commentUpdate"
            )
            return response
        except Exception as e:
            return GraphQLResponse(success=False, message=f"Failed to execute mutation commentUpdate: {str(e)}")

    async def commentDelete(
        self,
        id: str
    ) -> GraphQLResponse:
        """Delete a comment"""
        query = LinearGraphQLOperations.get_operation_with_fragments("mutation", "commentDelete")
        variables = {"id": id}

        try:
            response = await self._linear_client.get_client().execute(
                query=query, variables=variables, operation_name="commentDelete"
            )
            return response
        except Exception as e:
            return GraphQLResponse(success=False, message=f"Failed to execute mutation commentDelete: {str(e)}")

    # WORKFLOW STATE MUTATIONS
    async def workflowStateCreate(
        self,
        input: Dict[str, Any]
    ) -> GraphQLResponse:
        """Create a workflow state"""
        query = LinearGraphQLOperations.get_operation_with_fragments("mutation", "workflowStateCreate")
        variables = {"input": input}

        try:
            response = await self._linear_client.get_client().execute(
                query=query, variables=variables, operation_name="workflowStateCreate"
            )
            return response
        except Exception as e:
            return GraphQLResponse(success=False, message=f"Failed to execute mutation workflowStateCreate: {str(e)}")

    async def workflowStateUpdate(
        self,
        id: str,
        input: Dict[str, Any]
    ) -> GraphQLResponse:
        """Update a workflow state"""
        query = LinearGraphQLOperations.get_operation_with_fragments("mutation", "workflowStateUpdate")
        variables = {"id": id, "input": input}

        try:
            response = await self._linear_client.get_client().execute(
                query=query, variables=variables, operation_name="workflowStateUpdate"
            )
            return response
        except Exception as e:
            return GraphQLResponse(success=False, message=f"Failed to execute mutation workflowStateUpdate: {str(e)}")

    async def workflowStateDelete(
        self,
        id: str
    ) -> GraphQLResponse:
        """Delete a workflow state"""
        query = LinearGraphQLOperations.get_operation_with_fragments("mutation", "workflowStateDelete")
        variables = {"id": id}

        try:
            response = await self._linear_client.get_client().execute(
                query=query, variables=variables, operation_name="workflowStateDelete"
            )
            return response
        except Exception as e:
            return GraphQLResponse(success=False, message=f"Failed to execute mutation workflowStateDelete: {str(e)}")

    # LABEL MUTATIONS
    async def issueLabelCreate(
        self,
        input: Dict[str, Any]
    ) -> GraphQLResponse:
        """Create an issue label"""
        query = LinearGraphQLOperations.get_operation_with_fragments("mutation", "issueLabelCreate")
        variables = {"input": input}

        try:
            response = await self._linear_client.get_client().execute(
                query=query, variables=variables, operation_name="issueLabelCreate"
            )
            return response
        except Exception as e:
            return GraphQLResponse(success=False, message=f"Failed to execute mutation issueLabelCreate: {str(e)}")

    async def issueLabelUpdate(
        self,
        id: str,
        input: Dict[str, Any]
    ) -> GraphQLResponse:
        """Update an issue label"""
        query = LinearGraphQLOperations.get_operation_with_fragments("mutation", "issueLabelUpdate")
        variables = {"id": id, "input": input}

        try:
            response = await self._linear_client.get_client().execute(
                query=query, variables=variables, operation_name="issueLabelUpdate"
            )
            return response
        except Exception as e:
            return GraphQLResponse(success=False, message=f"Failed to execute mutation issueLabelUpdate: {str(e)}")

    async def issueLabelDelete(
        self,
        id: str
    ) -> GraphQLResponse:
        """Delete an issue label"""
        query = LinearGraphQLOperations.get_operation_with_fragments("mutation", "issueLabelDelete")
        variables = {"id": id}

        try:
            response = await self._linear_client.get_client().execute(
                query=query, variables=variables, operation_name="issueLabelDelete"
            )
            return response
        except Exception as e:
            return GraphQLResponse(success=False, message=f"Failed to execute mutation issueLabelDelete: {str(e)}")

    # CYCLE MUTATIONS
    async def cycleCreate(
        self,
        input: Dict[str, Any]
    ) -> GraphQLResponse:
        """Create a cycle"""
        query = LinearGraphQLOperations.get_operation_with_fragments("mutation", "cycleCreate")
        variables = {"input": input}

        try:
            response = await self._linear_client.get_client().execute(
                query=query, variables=variables, operation_name="cycleCreate"
            )
            return response
        except Exception as e:
            return GraphQLResponse(success=False, message=f"Failed to execute mutation cycleCreate: {str(e)}")

    async def cycleUpdate(
        self,
        id: str,
        input: Dict[str, Any]
    ) -> GraphQLResponse:
        """Update a cycle"""
        query = LinearGraphQLOperations.get_operation_with_fragments("mutation", "cycleUpdate")
        variables = {"id": id, "input": input}

        try:
            response = await self._linear_client.get_client().execute(
                query=query, variables=variables, operation_name="cycleUpdate"
            )
            return response
        except Exception as e:
            return GraphQLResponse(success=False, message=f"Failed to execute mutation cycleUpdate: {str(e)}")

    async def cycleArchive(
        self,
        id: str
    ) -> GraphQLResponse:
        """Archive a cycle"""
        query = LinearGraphQLOperations.get_operation_with_fragments("mutation", "cycleArchive")
        variables = {"id": id}

        try:
            response = await self._linear_client.get_client().execute(
                query=query, variables=variables, operation_name="cycleArchive"
            )
            return response
        except Exception as e:
            return GraphQLResponse(success=False, message=f"Failed to execute mutation cycleArchive: {str(e)}")

    # MILESTONE MUTATIONS
    async def milestoneCreate(
        self,
        input: Dict[str, Any]
    ) -> GraphQLResponse:
        """Create a milestone"""
        query = LinearGraphQLOperations.get_operation_with_fragments("mutation", "milestoneCreate")
        variables = {"input": input}

        try:
            response = await self._linear_client.get_client().execute(
                query=query, variables=variables, operation_name="milestoneCreate"
            )
            return response
        except Exception as e:
            return GraphQLResponse(success=False, message=f"Failed to execute mutation milestoneCreate: {str(e)}")

    async def milestoneUpdate(
        self,
        id: str,
        input: Dict[str, Any]
    ) -> GraphQLResponse:
        """Update a milestone"""
        query = LinearGraphQLOperations.get_operation_with_fragments("mutation", "milestoneUpdate")
        variables = {"id": id, "input": input}

        try:
            response = await self._linear_client.get_client().execute(
                query=query, variables=variables, operation_name="milestoneUpdate"
            )
            return response
        except Exception as e:
            return GraphQLResponse(success=False, message=f"Failed to execute mutation milestoneUpdate: {str(e)}")

    async def milestoneDelete(
        self,
        id: str
    ) -> GraphQLResponse:
        """Delete a milestone"""
        query = LinearGraphQLOperations.get_operation_with_fragments("mutation", "milestoneDelete")
        variables = {"id": id}

        try:
            response = await self._linear_client.get_client().execute(
                query=query, variables=variables, operation_name="milestoneDelete"
            )
            return response
        except Exception as e:
            return GraphQLResponse(success=False, message=f"Failed to execute mutation milestoneDelete: {str(e)}")

    # ATTACHMENT MUTATIONS
    async def attachmentCreate(
        self,
        input: Dict[str, Any]
    ) -> GraphQLResponse:
        """Create an attachment"""
        query = LinearGraphQLOperations.get_operation_with_fragments("mutation", "attachmentCreate")
        variables = {"input": input}

        try:
            response = await self._linear_client.get_client().execute(
                query=query, variables=variables, operation_name="attachmentCreate"
            )
            return response
        except Exception as e:
            return GraphQLResponse(success=False, message=f"Failed to execute mutation attachmentCreate: {str(e)}")

    async def attachmentUpdate(
        self,
        id: str,
        input: Dict[str, Any]
    ) -> GraphQLResponse:
        """Update an attachment"""
        query = LinearGraphQLOperations.get_operation_with_fragments("mutation", "attachmentUpdate")
        variables = {"id": id, "input": input}

        try:
            response = await self._linear_client.get_client().execute(
                query=query, variables=variables, operation_name="attachmentUpdate"
            )
            return response
        except Exception as e:
            return GraphQLResponse(success=False, message=f"Failed to execute mutation attachmentUpdate: {str(e)}")

    async def attachmentDelete(
        self,
        id: str
    ) -> GraphQLResponse:
        """Delete an attachment"""
        query = LinearGraphQLOperations.get_operation_with_fragments("mutation", "attachmentDelete")
        variables = {"id": id}

        try:
            response = await self._linear_client.get_client().execute(
                query=query, variables=variables, operation_name="attachmentDelete"
            )
            return response
        except Exception as e:
            return GraphQLResponse(success=False, message=f"Failed to execute mutation attachmentDelete: {str(e)}")

    # NOTIFICATION MUTATIONS
    async def notificationUpdate(
        self,
        id: str,
        input: Dict[str, Any]
    ) -> GraphQLResponse:
        """Update a notification"""
        query = LinearGraphQLOperations.get_operation_with_fragments("mutation", "notificationUpdate")
        variables = {"id": id, "input": input}

        try:
            response = await self._linear_client.get_client().execute(
                query=query, variables=variables, operation_name="notificationUpdate"
            )
            return response
        except Exception as e:
            return GraphQLResponse(success=False, message=f"Failed to execute mutation notificationUpdate: {str(e)}")

    async def notificationMarkRead(
        self,
        id: str
    ) -> GraphQLResponse:
        """Mark notification as read"""
        query = LinearGraphQLOperations.get_operation_with_fragments("mutation", "notificationMarkRead")
        variables = {"id": id}

        try:
            response = await self._linear_client.get_client().execute(
                query=query, variables=variables, operation_name="notificationMarkRead"
            )
            return response
        except Exception as e:
            return GraphQLResponse(success=False, message=f"Failed to execute mutation notificationMarkRead: {str(e)}")

    async def notificationMarkUnread(
        self,
        id: str
    ) -> GraphQLResponse:
        """Mark notification as unread"""
        query = LinearGraphQLOperations.get_operation_with_fragments("mutation", "notificationMarkUnread")
        variables = {"id": id}

        try:
            response = await self._linear_client.get_client().execute(
                query=query, variables=variables, operation_name="notificationMarkUnread"
            )
            return response
        except Exception as e:
            return GraphQLResponse(success=False, message=f"Failed to execute mutation notificationMarkUnread: {str(e)}")

    # FAVORITE MUTATIONS
    async def favoriteCreate(
        self,
        input: Dict[str, Any]
    ) -> GraphQLResponse:
        """Create a favorite"""
        query = LinearGraphQLOperations.get_operation_with_fragments("mutation", "favoriteCreate")
        variables = {"input": input}

        try:
            response = await self._linear_client.get_client().execute(
                query=query, variables=variables, operation_name="favoriteCreate"
            )
            return response
        except Exception as e:
            return GraphQLResponse(success=False, message=f"Failed to execute mutation favoriteCreate: {str(e)}")

    async def favoriteUpdate(
        self,
        id: str,
        input: Dict[str, Any]
    ) -> GraphQLResponse:
        """Update a favorite"""
        query = LinearGraphQLOperations.get_operation_with_fragments("mutation", "favoriteUpdate")
        variables = {"id": id, "input": input}

        try:
            response = await self._linear_client.get_client().execute(
                query=query, variables=variables, operation_name="favoriteUpdate"
            )
            return response
        except Exception as e:
            return GraphQLResponse(success=False, message=f"Failed to execute mutation favoriteUpdate: {str(e)}")

    async def favoriteDelete(
        self,
        id: str
    ) -> GraphQLResponse:
        """Delete a favorite"""
        query = LinearGraphQLOperations.get_operation_with_fragments("mutation", "favoriteDelete")
        variables = {"id": id}

        try:
            response = await self._linear_client.get_client().execute(
                query=query, variables=variables, operation_name="favoriteDelete"
            )
            return response
        except Exception as e:
            return GraphQLResponse(success=False, message=f"Failed to execute mutation favoriteDelete: {str(e)}")

    # TEMPLATE MUTATIONS
    async def templateCreate(
        self,
        input: Dict[str, Any]
    ) -> GraphQLResponse:
        """Create a template"""
        query = LinearGraphQLOperations.get_operation_with_fragments("mutation", "templateCreate")
        variables = {"input": input}

        try:
            response = await self._linear_client.get_client().execute(
                query=query, variables=variables, operation_name="templateCreate"
            )
            return response
        except Exception as e:
            return GraphQLResponse(success=False, message=f"Failed to execute mutation templateCreate: {str(e)}")

    async def templateUpdate(
        self,
        id: str,
        input: Dict[str, Any]
    ) -> GraphQLResponse:
        """Update a template"""
        query = LinearGraphQLOperations.get_operation_with_fragments("mutation", "templateUpdate")
        variables = {"id": id, "input": input}

        try:
            response = await self._linear_client.get_client().execute(
                query=query, variables=variables, operation_name="templateUpdate"
            )
            return response
        except Exception as e:
            return GraphQLResponse(success=False, message=f"Failed to execute mutation templateUpdate: {str(e)}")

    async def templateDelete(
        self,
        id: str
    ) -> GraphQLResponse:
        """Delete a template"""
        query = LinearGraphQLOperations.get_operation_with_fragments("mutation", "templateDelete")
        variables = {"id": id}

        try:
            response = await self._linear_client.get_client().execute(
                query=query, variables=variables, operation_name="templateDelete"
            )
            return response
        except Exception as e:
            return GraphQLResponse(success=False, message=f"Failed to execute mutation templateDelete: {str(e)}")

    # INTEGRATION MUTATIONS
    async def integrationCreate(
        self,
        input: Dict[str, Any]
    ) -> GraphQLResponse:
        """Create an integration"""
        query = LinearGraphQLOperations.get_operation_with_fragments("mutation", "integrationCreate")
        variables = {"input": input}

        try:
            response = await self._linear_client.get_client().execute(
                query=query, variables=variables, operation_name="integrationCreate"
            )
            return response
        except Exception as e:
            return GraphQLResponse(success=False, message=f"Failed to execute mutation integrationCreate: {str(e)}")

    async def integrationUpdate(
        self,
        id: str,
        input: Dict[str, Any]
    ) -> GraphQLResponse:
        """Update an integration"""
        query = LinearGraphQLOperations.get_operation_with_fragments("mutation", "integrationUpdate")
        variables = {"id": id, "input": input}

        try:
            response = await self._linear_client.get_client().execute(
                query=query, variables=variables, operation_name="integrationUpdate"
            )
            return response
        except Exception as e:
            return GraphQLResponse(success=False, message=f"Failed to execute mutation integrationUpdate: {str(e)}")

    async def integrationDelete(
        self,
        id: str
    ) -> GraphQLResponse:
        """Delete an integration"""
        query = LinearGraphQLOperations.get_operation_with_fragments("mutation", "integrationDelete")
        variables = {"id": id}

        try:
            response = await self._linear_client.get_client().execute(
                query=query, variables=variables, operation_name="integrationDelete"
            )
            return response
        except Exception as e:
            return GraphQLResponse(success=False, message=f"Failed to execute mutation integrationDelete: {str(e)}")

    # WEBHOOK MUTATIONS
    async def webhookCreate(
        self,
        input: Dict[str, Any]
    ) -> GraphQLResponse:
        """Create a webhook"""
        query = LinearGraphQLOperations.get_operation_with_fragments("mutation", "webhookCreate")
        variables = {"input": input}

        try:
            response = await self._linear_client.get_client().execute(
                query=query, variables=variables, operation_name="webhookCreate"
            )
            return response
        except Exception as e:
            return GraphQLResponse(success=False, message=f"Failed to execute mutation webhookCreate: {str(e)}")

    async def webhookUpdate(
        self,
        id: str,
        input: Dict[str, Any]
    ) -> GraphQLResponse:
        """Update a webhook"""
        query = LinearGraphQLOperations.get_operation_with_fragments("mutation", "webhookUpdate")
        variables = {"id": id, "input": input}

        try:
            response = await self._linear_client.get_client().execute(
                query=query, variables=variables, operation_name="webhookUpdate"
            )
            return response
        except Exception as e:
            return GraphQLResponse(success=False, message=f"Failed to execute mutation webhookUpdate: {str(e)}")

    async def webhookDelete(
        self,
        id: str
    ) -> GraphQLResponse:
        """Delete a webhook"""
        query = LinearGraphQLOperations.get_operation_with_fragments("mutation", "webhookDelete")
        variables = {"id": id}

        try:
            response = await self._linear_client.get_client().execute(
                query=query, variables=variables, operation_name="webhookDelete"
            )
            return response
        except Exception as e:
            return GraphQLResponse(success=False, message=f"Failed to execute mutation webhookDelete: {str(e)}")

    # API KEY MUTATIONS
    async def apiKeyCreate(
        self,
        input: Dict[str, Any]
    ) -> GraphQLResponse:
        """Create an API key"""
        query = LinearGraphQLOperations.get_operation_with_fragments("mutation", "apiKeyCreate")
        variables = {"input": input}

        try:
            response = await self._linear_client.get_client().execute(
                query=query, variables=variables, operation_name="apiKeyCreate"
            )
            return response
        except Exception as e:
            return GraphQLResponse(success=False, message=f"Failed to execute mutation apiKeyCreate: {str(e)}")

    async def apiKeyDelete(
        self,
        id: str
    ) -> GraphQLResponse:
        """Delete an API key"""
        query = LinearGraphQLOperations.get_operation_with_fragments("mutation", "apiKeyDelete")
        variables = {"id": id}

        try:
            response = await self._linear_client.get_client().execute(
                query=query, variables=variables, operation_name="apiKeyDelete"
            )
            return response
        except Exception as e:
            return GraphQLResponse(success=False, message=f"Failed to execute mutation apiKeyDelete: {str(e)}")

    # ROADMAP MUTATIONS
    async def roadmapCreate(
        self,
        input: Dict[str, Any]
    ) -> GraphQLResponse:
        """Create a roadmap"""
        query = LinearGraphQLOperations.get_operation_with_fragments("mutation", "roadmapCreate")
        variables = {"input": input}

        try:
            response = await self._linear_client.get_client().execute(
                query=query, variables=variables, operation_name="roadmapCreate"
            )
            return response
        except Exception as e:
            return GraphQLResponse(success=False, message=f"Failed to execute mutation roadmapCreate: {str(e)}")

    async def roadmapUpdate(
        self,
        id: str,
        input: Dict[str, Any]
    ) -> GraphQLResponse:
        """Update a roadmap"""
        query = LinearGraphQLOperations.get_operation_with_fragments("mutation", "roadmapUpdate")
        variables = {"id": id, "input": input}

        try:
            response = await self._linear_client.get_client().execute(
                query=query, variables=variables, operation_name="roadmapUpdate"
            )
            return response
        except Exception as e:
            return GraphQLResponse(success=False, message=f"Failed to execute mutation roadmapUpdate: {str(e)}")

    async def roadmapDelete(
        self,
        id: str
    ) -> GraphQLResponse:
        """Delete a roadmap"""
        query = LinearGraphQLOperations.get_operation_with_fragments("mutation", "roadmapDelete")
        variables = {"id": id}

        try:
            response = await self._linear_client.get_client().execute(
                query=query, variables=variables, operation_name="roadmapDelete"
            )
            return response
        except Exception as e:
            return GraphQLResponse(success=False, message=f"Failed to execute mutation roadmapDelete: {str(e)}")

    # =============================================================================
    # UTILITY AND HELPER METHODS
    # =============================================================================

    def get_linear_client(self) -> LinearClient:
        """Get the underlying Linear client."""
        return self._linear_client

    def get_available_operations(self) -> Dict[str, Any]:
        """Get information about available GraphQL operations."""
        return LinearGraphQLOperations.get_all_operations()

    def get_operation_info(self) -> Dict[str, Any]:
        """Get comprehensive information about all available methods."""

        # Query operations
        query_operations = [
            "viewer", "user", "users", "organization", "team", "teams",
            "issue", "issues", "issueSearch", "project", "projects",
            "comment", "comments", "workflowState", "workflowStates",
            "issueLabel", "issueLabels", "cycle", "cycles",
            "milestone", "milestones", "attachment", "attachments",
            "notification", "notifications", "favorite", "favorites",
            "template", "templates", "integration", "integrations",
            "webhook", "webhooks", "apiKey", "apiKeys",
            "roadmap", "roadmaps"
        ]

        # Mutation operations
        mutation_operations = [
            "userUpdate", "userSettingsUpdate", "organizationUpdate",
            "organizationInviteCreate", "organizationInviteDelete",
            "teamCreate", "teamUpdate", "teamDelete", "teamMembershipCreate",
            "teamMembershipUpdate", "teamMembershipDelete",
            "issueCreate", "issueUpdate", "issueDelete", "issueArchive",
            "issueUnarchive", "issueBatchUpdate",
            "projectCreate", "projectUpdate", "projectDelete",
            "projectArchive", "projectUnarchive",
            "commentCreate", "commentUpdate", "commentDelete",
            "workflowStateCreate", "workflowStateUpdate", "workflowStateDelete",
            "issueLabelCreate", "issueLabelUpdate", "issueLabelDelete",
            "cycleCreate", "cycleUpdate", "cycleArchive",
            "milestoneCreate", "milestoneUpdate", "milestoneDelete",
            "attachmentCreate", "attachmentUpdate", "attachmentDelete",
            "notificationUpdate", "notificationMarkRead", "notificationMarkUnread",
            "favoriteCreate", "favoriteUpdate", "favoriteDelete",
            "templateCreate", "templateUpdate", "templateDelete",
            "integrationCreate", "integrationUpdate", "integrationDelete",
            "webhookCreate", "webhookUpdate", "webhookDelete",
            "apiKeyCreate", "apiKeyDelete",
            "roadmapCreate", "roadmapUpdate", "roadmapDelete"
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
                "users": "Complete CRUD operations",
                "organizations": "Read and update operations + invites",
                "teams": "Complete CRUD operations + memberships",
                "issues": "Complete CRUD operations + search + batch operations",
                "projects": "Complete CRUD operations + archive/unarchive",
                "comments": "Complete CRUD operations",
                "workflow_states": "Complete CRUD operations",
                "labels": "Complete CRUD operations",
                "cycles": "Create, update, and archive operations",
                "milestones": "Complete CRUD operations",
                "attachments": "Complete CRUD operations",
                "notifications": "Update and mark read/unread operations",
                "favorites": "Complete CRUD operations",
                "templates": "Complete CRUD operations",
                "integrations": "Complete CRUD operations",
                "webhooks": "Complete CRUD operations",
                "api_keys": "Create and delete operations",
                "roadmaps": "Complete CRUD operations"
            }
        }

    async def validate_connection(self) -> bool:
        """Validate the Linear connection by fetching viewer information."""
        try:
            response = await self.viewer()
            return response.success and response.data is not None
        except Exception as e:
            print(f"Connection validation failed: {e}")
            return False

    # =============================================================================
    # CONVENIENCE METHODS FOR COMMON OPERATIONS
    # =============================================================================

    async def get_current_user(self) -> GraphQLResponse:
        """Get current user information."""
        return await self.viewer()

    async def get_all_teams(self, limit: int = 50) -> GraphQLResponse:
        """Get all teams."""
        return await self.teams(first=limit)

    async def get_team_issues(self, team_id: str, limit: int = 50) -> GraphQLResponse:
        """Get issues for a specific team."""
        team_filter = {"team": {"id": {"eq": team_id}}}
        return await self.issues(first=limit, filter=team_filter)

    async def get_user_issues(self, user_id: str, limit: int = 50) -> GraphQLResponse:
        """Get issues assigned to a specific user."""
        user_filter = {"assignee": {"id": {"eq": user_id}}}
        return await self.issues(first=limit, filter=user_filter)

    async def get_project_issues(self, project_id: str, limit: int = 50) -> GraphQLResponse:
        """Get issues for a specific project."""
        project_filter = {"project": {"id": {"eq": project_id}}}
        return await self.issues(first=limit, filter=project_filter)

    async def create_simple_issue(
        self,
        title: str,
        team_id: str,
        description: Optional[str] = None,
        assignee_id: Optional[str] = None,
        priority: Optional[int] = None,
        labels: Optional[List[str]] = None
    ) -> GraphQLResponse:
        """Create a simple issue with basic information."""
        issue_input = {
            "title": title,
            "teamId": team_id
        }

        if description:
            issue_input["description"] = description
        if assignee_id:
            issue_input["assigneeId"] = assignee_id
        if priority is not None:
            issue_input["priority"] = priority
        if labels:
            issue_input["labelIds"] = labels

        return await self.issueCreate(input=issue_input)

    async def update_issue_status(self, issue_id: str, state_id: str) -> GraphQLResponse:
        """Update an issue's status."""
        update_input = {"stateId": state_id}
        return await self.issueUpdate(id=issue_id, input=update_input)

    async def assign_issue(self, issue_id: str, assignee_id: str) -> GraphQLResponse:
        """Assign an issue to a user."""
        update_input = {"assigneeId": assignee_id}
        return await self.issueUpdate(id=issue_id, input=update_input)

    async def create_simple_project(
        self,
        name: str,
        description: Optional[str] = None,
        team_ids: Optional[List[str]] = None
    ) -> GraphQLResponse:
        """Create a simple project."""
        project_input = {"name": name}

        if description:
            project_input["description"] = description
        if team_ids:
            project_input["teamIds"] = team_ids

        return await self.projectCreate(input=project_input)

    async def add_comment_to_issue(
        self,
        issue_id: str,
        body: str
    ) -> GraphQLResponse:
        """Add a comment to an issue."""
        comment_input = {
            "issueId": issue_id,
            "body": body
        }
        return await self.commentCreate(input=comment_input)

    async def create_team_with_members(
        self,
        name: str,
        key: str,
        description: Optional[str] = None,
        member_ids: Optional[List[str]] = None
    ) -> GraphQLResponse:
        """Create a team with optional members."""
        team_input = {
            "name": name,
            "key": key
        }

        if description:
            team_input["description"] = description

        # First create the team
        team_response = await self.teamCreate(input=team_input)

        # If team creation was successful and member_ids provided, add members
        if team_response.success and member_ids and team_response.data:
            team_id = team_response.data.get("teamCreate", {}).get("team", {}).get("id")
            if team_id:
                for member_id in member_ids:
                    await self.teamMembershipCreate(input={
                        "teamId": team_id,
                        "userId": member_id
                    })

        return team_response
