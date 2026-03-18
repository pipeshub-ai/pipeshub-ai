"""
Aha! REST API DataSource - Auto-generated API wrapper

Generated from Aha! REST API v1 documentation.
Uses HTTP client for direct REST API interactions.
All methods have explicit parameter signatures.
"""

from __future__ import annotations

from typing import Any

from app.sources.client.aha.aha import AhaClient, AhaResponse
from app.sources.client.http.http_request import HTTPRequest

# HTTP status code constant
HTTP_ERROR_THRESHOLD = 400


class AhaDataSource:
    """Aha! REST API DataSource

    Provides async wrapper methods for Aha! REST API operations:
    - User profile and management
    - Product management
    - Feature CRUD operations
    - Idea management and portals
    - Release management
    - Goal and initiative operations
    - Epic management
    - Requirement management
    - Task management
    - Comment operations
    - Page/note management
    - Workflow management
    - Integration listing
    - Custom fields
    - Team management
    - Strategy operations
    - Audit and deletion tracking

    The base URL is https://{subdomain}.aha.io/api/v1.

    All methods return AhaResponse objects.
    """

    def __init__(self, client: AhaClient) -> None:
        """Initialize with AhaClient.

        Args:
            client: AhaClient instance with configured authentication and subdomain
        """
        self._client = client
        self.http = client.get_client()
        try:
            self.base_url = self.http.get_base_url().rstrip('/')
        except AttributeError as exc:
            raise ValueError('HTTP client does not have get_base_url method') from exc

    def get_data_source(self) -> 'AhaDataSource':
        """Return the data source instance."""
        return self

    def get_client(self) -> AhaClient:
        """Return the underlying AhaClient."""
        return self._client

    async def get_current_user(
        self
    ) -> AhaResponse:
        """Get the current authenticated user details (API v1)

        Returns:
            AhaResponse with operation result
        """
        url = self.base_url + "/me"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return AhaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_current_user" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return AhaResponse(success=False, error=str(e), message="Failed to execute get_current_user")

    async def list_users(
        self,
        page: int | None = None,
        per_page: int | None = None
    ) -> AhaResponse:
        """List all users in the account (API v1)

        Args:
            page: Page number for pagination
            per_page: Number of results per page

        Returns:
            AhaResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if page is not None:
            query_params['page'] = str(page)
        if per_page is not None:
            query_params['per_page'] = str(per_page)

        url = self.base_url + "/users"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return AhaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_users" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return AhaResponse(success=False, error=str(e), message="Failed to execute list_users")

    async def get_user(
        self,
        user_id: str
    ) -> AhaResponse:
        """Get a specific user by ID (API v1)

        Args:
            user_id: The user ID

        Returns:
            AhaResponse with operation result
        """
        url = self.base_url + "/users/{user_id}".format(user_id=user_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return AhaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_user" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return AhaResponse(success=False, error=str(e), message="Failed to execute get_user")

    async def list_products(
        self,
        page: int | None = None,
        per_page: int | None = None
    ) -> AhaResponse:
        """List all products in the account (API v1)

        Args:
            page: Page number for pagination
            per_page: Number of results per page

        Returns:
            AhaResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if page is not None:
            query_params['page'] = str(page)
        if per_page is not None:
            query_params['per_page'] = str(per_page)

        url = self.base_url + "/products"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return AhaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_products" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return AhaResponse(success=False, error=str(e), message="Failed to execute list_products")

    async def get_product(
        self,
        product_id: str
    ) -> AhaResponse:
        """Get a specific product by ID (API v1)

        Args:
            product_id: The product ID

        Returns:
            AhaResponse with operation result
        """
        url = self.base_url + "/products/{product_id}".format(product_id=product_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return AhaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_product" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return AhaResponse(success=False, error=str(e), message="Failed to execute get_product")

    async def list_product_users(
        self,
        product_id: str
    ) -> AhaResponse:
        """List all users for a product (API v1)

        Args:
            product_id: The product ID

        Returns:
            AhaResponse with operation result
        """
        url = self.base_url + "/products/{product_id}/users".format(product_id=product_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return AhaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_product_users" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return AhaResponse(success=False, error=str(e), message="Failed to execute list_product_users")

    async def list_product_workflows(
        self,
        product_id: str
    ) -> AhaResponse:
        """List all workflows for a product (API v1)

        Args:
            product_id: The product ID

        Returns:
            AhaResponse with operation result
        """
        url = self.base_url + "/products/{product_id}/workflows".format(product_id=product_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return AhaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_product_workflows" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return AhaResponse(success=False, error=str(e), message="Failed to execute list_product_workflows")

    async def list_product_teams(
        self,
        product_id: str
    ) -> AhaResponse:
        """List all teams for a product (API v1)

        Args:
            product_id: The product ID

        Returns:
            AhaResponse with operation result
        """
        url = self.base_url + "/products/{product_id}/teams".format(product_id=product_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return AhaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_product_teams" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return AhaResponse(success=False, error=str(e), message="Failed to execute list_product_teams")

    async def list_features(
        self,
        page: int | None = None,
        per_page: int | None = None,
        q: str | None = None,
        assigned_to_user: str | None = None
    ) -> AhaResponse:
        """List all features across all products (API v1)

        Args:
            page: Page number for pagination
            per_page: Number of results per page
            q: Search query string
            assigned_to_user: Filter by assigned user

        Returns:
            AhaResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if page is not None:
            query_params['page'] = str(page)
        if per_page is not None:
            query_params['per_page'] = str(per_page)
        if q is not None:
            query_params['q'] = q
        if assigned_to_user is not None:
            query_params['assigned_to_user'] = assigned_to_user

        url = self.base_url + "/features"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return AhaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_features" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return AhaResponse(success=False, error=str(e), message="Failed to execute list_features")

    async def list_product_features(
        self,
        product_id: str,
        page: int | None = None,
        per_page: int | None = None,
        q: str | None = None,
        assigned_to_user: str | None = None
    ) -> AhaResponse:
        """List all features for a product (API v1)

        Args:
            product_id: The product ID
            page: Page number for pagination
            per_page: Number of results per page
            q: Search query string
            assigned_to_user: Filter by assigned user

        Returns:
            AhaResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if page is not None:
            query_params['page'] = str(page)
        if per_page is not None:
            query_params['per_page'] = str(per_page)
        if q is not None:
            query_params['q'] = q
        if assigned_to_user is not None:
            query_params['assigned_to_user'] = assigned_to_user

        url = self.base_url + "/products/{product_id}/features".format(product_id=product_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return AhaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_product_features" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return AhaResponse(success=False, error=str(e), message="Failed to execute list_product_features")

    async def get_feature(
        self,
        feature_id: str
    ) -> AhaResponse:
        """Get a specific feature by ID (API v1)

        Args:
            feature_id: The feature ID

        Returns:
            AhaResponse with operation result
        """
        url = self.base_url + "/features/{feature_id}".format(feature_id=feature_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return AhaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_feature" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return AhaResponse(success=False, error=str(e), message="Failed to execute get_feature")

    async def create_feature(
        self,
        product_id: str,
        name: str,
        description: str | None = None,
        workflow_status: str | None = None,
        assigned_to_user: str | None = None,
        due_date: str | None = None,
        start_date: str | None = None,
        release: str | None = None,
        tags: str | None = None
    ) -> AhaResponse:
        """Create a new feature in a product (API v1)

        Args:
            product_id: The product ID
            name: The name of the feature
            description: The feature description
            workflow_status: The workflow status
            assigned_to_user: User to assign the feature to
            due_date: Due date in YYYY-MM-DD format
            start_date: Start date in YYYY-MM-DD format
            release: Release to associate the feature with
            tags: Comma-separated list of tags

        Returns:
            AhaResponse with operation result
        """
        url = self.base_url + "/products/{product_id}/features".format(product_id=product_id)

        body: dict[str, Any] = {}
        body['name'] = name
        if description is not None:
            body['description'] = description
        if workflow_status is not None:
            body['workflow_status'] = workflow_status
        if assigned_to_user is not None:
            body['assigned_to_user'] = assigned_to_user
        if due_date is not None:
            body['due_date'] = due_date
        if start_date is not None:
            body['start_date'] = start_date
        if release is not None:
            body['release'] = release
        if tags is not None:
            body['tags'] = tags

        try:
            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return AhaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed create_feature" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return AhaResponse(success=False, error=str(e), message="Failed to execute create_feature")

    async def update_feature(
        self,
        feature_id: str,
        name: str | None = None,
        description: str | None = None,
        workflow_status: str | None = None,
        assigned_to_user: str | None = None,
        due_date: str | None = None,
        start_date: str | None = None,
        release: str | None = None,
        tags: str | None = None
    ) -> AhaResponse:
        """Update an existing feature (API v1)

        Args:
            feature_id: The feature ID
            name: The name of the feature
            description: The feature description
            workflow_status: The workflow status
            assigned_to_user: User to assign the feature to
            due_date: Due date in YYYY-MM-DD format
            start_date: Start date in YYYY-MM-DD format
            release: Release to associate the feature with
            tags: Comma-separated list of tags

        Returns:
            AhaResponse with operation result
        """
        url = self.base_url + "/features/{feature_id}".format(feature_id=feature_id)

        body: dict[str, Any] = {}
        if name is not None:
            body['name'] = name
        if description is not None:
            body['description'] = description
        if workflow_status is not None:
            body['workflow_status'] = workflow_status
        if assigned_to_user is not None:
            body['assigned_to_user'] = assigned_to_user
        if due_date is not None:
            body['due_date'] = due_date
        if start_date is not None:
            body['start_date'] = start_date
        if release is not None:
            body['release'] = release
        if tags is not None:
            body['tags'] = tags

        try:
            request = HTTPRequest(
                method="PUT",
                url=url,
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return AhaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed update_feature" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return AhaResponse(success=False, error=str(e), message="Failed to execute update_feature")

    async def list_feature_comments(
        self,
        feature_id: str
    ) -> AhaResponse:
        """List comments on a feature (API v1)

        Args:
            feature_id: The feature ID

        Returns:
            AhaResponse with operation result
        """
        url = self.base_url + "/features/{feature_id}/comments".format(feature_id=feature_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return AhaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_feature_comments" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return AhaResponse(success=False, error=str(e), message="Failed to execute list_feature_comments")

    async def list_feature_tasks(
        self,
        feature_id: str
    ) -> AhaResponse:
        """List tasks for a feature (API v1)

        Args:
            feature_id: The feature ID

        Returns:
            AhaResponse with operation result
        """
        url = self.base_url + "/features/{feature_id}/tasks".format(feature_id=feature_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return AhaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_feature_tasks" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return AhaResponse(success=False, error=str(e), message="Failed to execute list_feature_tasks")

    async def list_feature_requirements(
        self,
        feature_id: str
    ) -> AhaResponse:
        """List requirements for a feature (API v1)

        Args:
            feature_id: The feature ID

        Returns:
            AhaResponse with operation result
        """
        url = self.base_url + "/features/{feature_id}/requirements".format(feature_id=feature_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return AhaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_feature_requirements" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return AhaResponse(success=False, error=str(e), message="Failed to execute list_feature_requirements")

    async def convert_feature_to_epic(
        self,
        feature_id: str
    ) -> AhaResponse:
        """Convert a feature to an epic (API v1)

        Args:
            feature_id: The feature ID to convert

        Returns:
            AhaResponse with operation result
        """
        url = self.base_url + "/features/{feature_id}/convert_to_epic".format(feature_id=feature_id)

        try:
            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return AhaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed convert_feature_to_epic" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return AhaResponse(success=False, error=str(e), message="Failed to execute convert_feature_to_epic")

    async def list_ideas(
        self,
        page: int | None = None,
        per_page: int | None = None
    ) -> AhaResponse:
        """List all ideas across all products (API v1)

        Args:
            page: Page number for pagination
            per_page: Number of results per page

        Returns:
            AhaResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if page is not None:
            query_params['page'] = str(page)
        if per_page is not None:
            query_params['per_page'] = str(per_page)

        url = self.base_url + "/ideas"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return AhaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_ideas" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return AhaResponse(success=False, error=str(e), message="Failed to execute list_ideas")

    async def list_product_ideas(
        self,
        product_id: str,
        page: int | None = None,
        per_page: int | None = None
    ) -> AhaResponse:
        """List all ideas for a product (API v1)

        Args:
            product_id: The product ID
            page: Page number for pagination
            per_page: Number of results per page

        Returns:
            AhaResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if page is not None:
            query_params['page'] = str(page)
        if per_page is not None:
            query_params['per_page'] = str(per_page)

        url = self.base_url + "/products/{product_id}/ideas".format(product_id=product_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return AhaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_product_ideas" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return AhaResponse(success=False, error=str(e), message="Failed to execute list_product_ideas")

    async def get_idea(
        self,
        idea_id: str
    ) -> AhaResponse:
        """Get a specific idea by ID (API v1)

        Args:
            idea_id: The idea ID

        Returns:
            AhaResponse with operation result
        """
        url = self.base_url + "/ideas/{idea_id}".format(idea_id=idea_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return AhaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_idea" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return AhaResponse(success=False, error=str(e), message="Failed to execute get_idea")

    async def list_idea_comments(
        self,
        idea_id: str
    ) -> AhaResponse:
        """List comments on an idea (API v1)

        Args:
            idea_id: The idea ID

        Returns:
            AhaResponse with operation result
        """
        url = self.base_url + "/ideas/{idea_id}/comments".format(idea_id=idea_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return AhaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_idea_comments" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return AhaResponse(success=False, error=str(e), message="Failed to execute list_idea_comments")

    async def list_idea_endorsements(
        self,
        idea_id: str
    ) -> AhaResponse:
        """List endorsements for an idea (API v1)

        Args:
            idea_id: The idea ID

        Returns:
            AhaResponse with operation result
        """
        url = self.base_url + "/ideas/{idea_id}/endorsements".format(idea_id=idea_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return AhaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_idea_endorsements" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return AhaResponse(success=False, error=str(e), message="Failed to execute list_idea_endorsements")

    async def list_idea_tasks(
        self,
        idea_id: str
    ) -> AhaResponse:
        """List tasks for an idea (API v1)

        Args:
            idea_id: The idea ID

        Returns:
            AhaResponse with operation result
        """
        url = self.base_url + "/ideas/{idea_id}/tasks".format(idea_id=idea_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return AhaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_idea_tasks" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return AhaResponse(success=False, error=str(e), message="Failed to execute list_idea_tasks")

    async def list_idea_portals(
        self
    ) -> AhaResponse:
        """List all idea portals (API v1)

        Returns:
            AhaResponse with operation result
        """
        url = self.base_url + "/idea_portals"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return AhaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_idea_portals" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return AhaResponse(success=False, error=str(e), message="Failed to execute list_idea_portals")

    async def list_product_idea_portals(
        self,
        product_id: str
    ) -> AhaResponse:
        """List idea portals for a product (API v1)

        Args:
            product_id: The product ID

        Returns:
            AhaResponse with operation result
        """
        url = self.base_url + "/products/{product_id}/idea_portals".format(product_id=product_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return AhaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_product_idea_portals" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return AhaResponse(success=False, error=str(e), message="Failed to execute list_product_idea_portals")

    async def list_idea_categories(
        self,
        product_id: str
    ) -> AhaResponse:
        """List idea categories for a product (API v1)

        Args:
            product_id: The product ID

        Returns:
            AhaResponse with operation result
        """
        url = self.base_url + "/products/{product_id}/idea_categories".format(product_id=product_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return AhaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_idea_categories" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return AhaResponse(success=False, error=str(e), message="Failed to execute list_idea_categories")

    async def list_product_releases(
        self,
        product_id: str,
        page: int | None = None,
        per_page: int | None = None
    ) -> AhaResponse:
        """List all releases for a product (API v1)

        Args:
            product_id: The product ID
            page: Page number for pagination
            per_page: Number of results per page

        Returns:
            AhaResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if page is not None:
            query_params['page'] = str(page)
        if per_page is not None:
            query_params['per_page'] = str(per_page)

        url = self.base_url + "/products/{product_id}/releases".format(product_id=product_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return AhaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_product_releases" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return AhaResponse(success=False, error=str(e), message="Failed to execute list_product_releases")

    async def get_release(
        self,
        release_id: str
    ) -> AhaResponse:
        """Get a specific release by ID (API v1)

        Args:
            release_id: The release ID

        Returns:
            AhaResponse with operation result
        """
        url = self.base_url + "/releases/{release_id}".format(release_id=release_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return AhaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_release" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return AhaResponse(success=False, error=str(e), message="Failed to execute get_release")

    async def list_release_features(
        self,
        release_id: str
    ) -> AhaResponse:
        """List features in a release (API v1)

        Args:
            release_id: The release ID

        Returns:
            AhaResponse with operation result
        """
        url = self.base_url + "/releases/{release_id}/features".format(release_id=release_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return AhaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_release_features" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return AhaResponse(success=False, error=str(e), message="Failed to execute list_release_features")

    async def list_release_epics(
        self,
        release_id: str
    ) -> AhaResponse:
        """List epics in a release (API v1)

        Args:
            release_id: The release ID

        Returns:
            AhaResponse with operation result
        """
        url = self.base_url + "/releases/{release_id}/epics".format(release_id=release_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return AhaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_release_epics" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return AhaResponse(success=False, error=str(e), message="Failed to execute list_release_epics")

    async def list_release_comments(
        self,
        release_id: str
    ) -> AhaResponse:
        """List comments on a release (API v1)

        Args:
            release_id: The release ID

        Returns:
            AhaResponse with operation result
        """
        url = self.base_url + "/releases/{release_id}/comments".format(release_id=release_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return AhaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_release_comments" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return AhaResponse(success=False, error=str(e), message="Failed to execute list_release_comments")

    async def list_release_tasks(
        self,
        release_id: str
    ) -> AhaResponse:
        """List tasks for a release (API v1)

        Args:
            release_id: The release ID

        Returns:
            AhaResponse with operation result
        """
        url = self.base_url + "/releases/{release_id}/tasks".format(release_id=release_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return AhaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_release_tasks" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return AhaResponse(success=False, error=str(e), message="Failed to execute list_release_tasks")

    async def list_release_phases(
        self
    ) -> AhaResponse:
        """List all release phases (API v1)

        Returns:
            AhaResponse with operation result
        """
        url = self.base_url + "/release_phases"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return AhaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_release_phases" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return AhaResponse(success=False, error=str(e), message="Failed to execute list_release_phases")

    async def get_release_phase(
        self,
        release_phase_id: str
    ) -> AhaResponse:
        """Get a specific release phase by ID (API v1)

        Args:
            release_phase_id: The release phase ID

        Returns:
            AhaResponse with operation result
        """
        url = self.base_url + "/release_phases/{release_phase_id}".format(release_phase_id=release_phase_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return AhaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_release_phase" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return AhaResponse(success=False, error=str(e), message="Failed to execute get_release_phase")

    async def list_goals(
        self,
        page: int | None = None,
        per_page: int | None = None
    ) -> AhaResponse:
        """List all goals across all products (API v1)

        Args:
            page: Page number for pagination
            per_page: Number of results per page

        Returns:
            AhaResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if page is not None:
            query_params['page'] = str(page)
        if per_page is not None:
            query_params['per_page'] = str(per_page)

        url = self.base_url + "/goals"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return AhaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_goals" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return AhaResponse(success=False, error=str(e), message="Failed to execute list_goals")

    async def list_product_goals(
        self,
        product_id: str
    ) -> AhaResponse:
        """List all goals for a product (API v1)

        Args:
            product_id: The product ID

        Returns:
            AhaResponse with operation result
        """
        url = self.base_url + "/products/{product_id}/goals".format(product_id=product_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return AhaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_product_goals" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return AhaResponse(success=False, error=str(e), message="Failed to execute list_product_goals")

    async def get_goal(
        self,
        goal_id: str
    ) -> AhaResponse:
        """Get a specific goal by ID (API v1)

        Args:
            goal_id: The goal ID

        Returns:
            AhaResponse with operation result
        """
        url = self.base_url + "/goals/{goal_id}".format(goal_id=goal_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return AhaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_goal" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return AhaResponse(success=False, error=str(e), message="Failed to execute get_goal")

    async def list_goal_features(
        self,
        goal_id: str
    ) -> AhaResponse:
        """List features linked to a goal (API v1)

        Args:
            goal_id: The goal ID

        Returns:
            AhaResponse with operation result
        """
        url = self.base_url + "/goals/{goal_id}/features".format(goal_id=goal_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return AhaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_goal_features" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return AhaResponse(success=False, error=str(e), message="Failed to execute list_goal_features")

    async def list_goal_epics(
        self,
        goal_id: str
    ) -> AhaResponse:
        """List epics linked to a goal (API v1)

        Args:
            goal_id: The goal ID

        Returns:
            AhaResponse with operation result
        """
        url = self.base_url + "/goals/{goal_id}/epics".format(goal_id=goal_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return AhaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_goal_epics" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return AhaResponse(success=False, error=str(e), message="Failed to execute list_goal_epics")

    async def list_goal_initiatives(
        self,
        goal_id: str
    ) -> AhaResponse:
        """List initiatives linked to a goal (API v1)

        Args:
            goal_id: The goal ID

        Returns:
            AhaResponse with operation result
        """
        url = self.base_url + "/goals/{goal_id}/initiatives".format(goal_id=goal_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return AhaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_goal_initiatives" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return AhaResponse(success=False, error=str(e), message="Failed to execute list_goal_initiatives")

    async def list_goal_releases(
        self,
        goal_id: str
    ) -> AhaResponse:
        """List releases linked to a goal (API v1)

        Args:
            goal_id: The goal ID

        Returns:
            AhaResponse with operation result
        """
        url = self.base_url + "/goals/{goal_id}/releases".format(goal_id=goal_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return AhaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_goal_releases" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return AhaResponse(success=False, error=str(e), message="Failed to execute list_goal_releases")

    async def list_goal_key_results(
        self,
        goal_id: str
    ) -> AhaResponse:
        """List key results for a goal (API v1)

        Args:
            goal_id: The goal ID

        Returns:
            AhaResponse with operation result
        """
        url = self.base_url + "/goals/{goal_id}/key_results".format(goal_id=goal_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return AhaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_goal_key_results" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return AhaResponse(success=False, error=str(e), message="Failed to execute list_goal_key_results")

    async def list_goal_comments(
        self,
        goal_id: str
    ) -> AhaResponse:
        """List comments on a goal (API v1)

        Args:
            goal_id: The goal ID

        Returns:
            AhaResponse with operation result
        """
        url = self.base_url + "/goals/{goal_id}/comments".format(goal_id=goal_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return AhaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_goal_comments" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return AhaResponse(success=False, error=str(e), message="Failed to execute list_goal_comments")

    async def list_initiatives(
        self,
        page: int | None = None,
        per_page: int | None = None
    ) -> AhaResponse:
        """List all initiatives across all products (API v1)

        Args:
            page: Page number for pagination
            per_page: Number of results per page

        Returns:
            AhaResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if page is not None:
            query_params['page'] = str(page)
        if per_page is not None:
            query_params['per_page'] = str(per_page)

        url = self.base_url + "/initiatives"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return AhaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_initiatives" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return AhaResponse(success=False, error=str(e), message="Failed to execute list_initiatives")

    async def list_product_initiatives(
        self,
        product_id: str
    ) -> AhaResponse:
        """List all initiatives for a product (API v1)

        Args:
            product_id: The product ID

        Returns:
            AhaResponse with operation result
        """
        url = self.base_url + "/products/{product_id}/initiatives".format(product_id=product_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return AhaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_product_initiatives" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return AhaResponse(success=False, error=str(e), message="Failed to execute list_product_initiatives")

    async def get_initiative(
        self,
        initiative_id: str
    ) -> AhaResponse:
        """Get a specific initiative by ID (API v1)

        Args:
            initiative_id: The initiative ID

        Returns:
            AhaResponse with operation result
        """
        url = self.base_url + "/initiatives/{initiative_id}".format(initiative_id=initiative_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return AhaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_initiative" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return AhaResponse(success=False, error=str(e), message="Failed to execute get_initiative")

    async def list_epics(
        self,
        page: int | None = None,
        per_page: int | None = None
    ) -> AhaResponse:
        """List all epics across all products (API v1)

        Args:
            page: Page number for pagination
            per_page: Number of results per page

        Returns:
            AhaResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if page is not None:
            query_params['page'] = str(page)
        if per_page is not None:
            query_params['per_page'] = str(per_page)

        url = self.base_url + "/epics"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return AhaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_epics" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return AhaResponse(success=False, error=str(e), message="Failed to execute list_epics")

    async def list_product_epics(
        self,
        product_id: str,
        page: int | None = None,
        per_page: int | None = None
    ) -> AhaResponse:
        """List all epics for a product (API v1)

        Args:
            product_id: The product ID
            page: Page number for pagination
            per_page: Number of results per page

        Returns:
            AhaResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if page is not None:
            query_params['page'] = str(page)
        if per_page is not None:
            query_params['per_page'] = str(per_page)

        url = self.base_url + "/products/{product_id}/epics".format(product_id=product_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return AhaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_product_epics" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return AhaResponse(success=False, error=str(e), message="Failed to execute list_product_epics")

    async def get_epic(
        self,
        epic_id: str
    ) -> AhaResponse:
        """Get a specific epic by ID (API v1)

        Args:
            epic_id: The epic ID

        Returns:
            AhaResponse with operation result
        """
        url = self.base_url + "/epics/{epic_id}".format(epic_id=epic_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return AhaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_epic" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return AhaResponse(success=False, error=str(e), message="Failed to execute get_epic")

    async def list_epic_features(
        self,
        epic_id: str
    ) -> AhaResponse:
        """List features in an epic (API v1)

        Args:
            epic_id: The epic ID

        Returns:
            AhaResponse with operation result
        """
        url = self.base_url + "/epics/{epic_id}/features".format(epic_id=epic_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return AhaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_epic_features" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return AhaResponse(success=False, error=str(e), message="Failed to execute list_epic_features")

    async def list_epic_comments(
        self,
        epic_id: str
    ) -> AhaResponse:
        """List comments on an epic (API v1)

        Args:
            epic_id: The epic ID

        Returns:
            AhaResponse with operation result
        """
        url = self.base_url + "/epics/{epic_id}/comments".format(epic_id=epic_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return AhaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_epic_comments" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return AhaResponse(success=False, error=str(e), message="Failed to execute list_epic_comments")

    async def list_epic_tasks(
        self,
        epic_id: str
    ) -> AhaResponse:
        """List tasks for an epic (API v1)

        Args:
            epic_id: The epic ID

        Returns:
            AhaResponse with operation result
        """
        url = self.base_url + "/epics/{epic_id}/tasks".format(epic_id=epic_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return AhaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_epic_tasks" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return AhaResponse(success=False, error=str(e), message="Failed to execute list_epic_tasks")

    async def get_requirement(
        self,
        requirement_id: str
    ) -> AhaResponse:
        """Get a specific requirement by ID (API v1)

        Args:
            requirement_id: The requirement ID

        Returns:
            AhaResponse with operation result
        """
        url = self.base_url + "/requirements/{requirement_id}".format(requirement_id=requirement_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return AhaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_requirement" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return AhaResponse(success=False, error=str(e), message="Failed to execute get_requirement")

    async def list_requirement_comments(
        self,
        requirement_id: str
    ) -> AhaResponse:
        """List comments on a requirement (API v1)

        Args:
            requirement_id: The requirement ID

        Returns:
            AhaResponse with operation result
        """
        url = self.base_url + "/requirements/{requirement_id}/comments".format(requirement_id=requirement_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return AhaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_requirement_comments" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return AhaResponse(success=False, error=str(e), message="Failed to execute list_requirement_comments")

    async def list_requirement_tasks(
        self,
        requirement_id: str
    ) -> AhaResponse:
        """List tasks for a requirement (API v1)

        Args:
            requirement_id: The requirement ID

        Returns:
            AhaResponse with operation result
        """
        url = self.base_url + "/requirements/{requirement_id}/tasks".format(requirement_id=requirement_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return AhaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_requirement_tasks" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return AhaResponse(success=False, error=str(e), message="Failed to execute list_requirement_tasks")

    async def list_tasks(
        self,
        page: int | None = None,
        per_page: int | None = None
    ) -> AhaResponse:
        """List all tasks (API v1)

        Args:
            page: Page number for pagination
            per_page: Number of results per page

        Returns:
            AhaResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if page is not None:
            query_params['page'] = str(page)
        if per_page is not None:
            query_params['per_page'] = str(per_page)

        url = self.base_url + "/tasks"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return AhaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_tasks" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return AhaResponse(success=False, error=str(e), message="Failed to execute list_tasks")

    async def get_task(
        self,
        task_id: str
    ) -> AhaResponse:
        """Get a specific task by ID (API v1)

        Args:
            task_id: The task ID

        Returns:
            AhaResponse with operation result
        """
        url = self.base_url + "/tasks/{task_id}".format(task_id=task_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return AhaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_task" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return AhaResponse(success=False, error=str(e), message="Failed to execute get_task")

    async def list_product_tasks(
        self,
        product_id: str
    ) -> AhaResponse:
        """List all tasks for a product (API v1)

        Args:
            product_id: The product ID

        Returns:
            AhaResponse with operation result
        """
        url = self.base_url + "/products/{product_id}/tasks".format(product_id=product_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return AhaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_product_tasks" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return AhaResponse(success=False, error=str(e), message="Failed to execute list_product_tasks")

    async def list_user_tasks(
        self,
        user_id: str
    ) -> AhaResponse:
        """List tasks assigned to a user (API v1)

        Args:
            user_id: The user ID

        Returns:
            AhaResponse with operation result
        """
        url = self.base_url + "/users/{user_id}/tasks".format(user_id=user_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return AhaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_user_tasks" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return AhaResponse(success=False, error=str(e), message="Failed to execute list_user_tasks")

    async def get_comment(
        self,
        comment_id: str
    ) -> AhaResponse:
        """Get a specific comment by ID (API v1)

        Args:
            comment_id: The comment ID

        Returns:
            AhaResponse with operation result
        """
        url = self.base_url + "/comments/{comment_id}".format(comment_id=comment_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return AhaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_comment" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return AhaResponse(success=False, error=str(e), message="Failed to execute get_comment")

    async def list_product_comments(
        self,
        project_id: str
    ) -> AhaResponse:
        """List comments for a product (API v1)

        Args:
            project_id: The product/project ID

        Returns:
            AhaResponse with operation result
        """
        url = self.base_url + "/products/{project_id}/comments".format(project_id=project_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return AhaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_product_comments" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return AhaResponse(success=False, error=str(e), message="Failed to execute list_product_comments")

    async def list_product_pages(
        self,
        product_id: str
    ) -> AhaResponse:
        """List all pages/notes for a product (API v1)

        Args:
            product_id: The product ID

        Returns:
            AhaResponse with operation result
        """
        url = self.base_url + "/products/{product_id}/pages".format(product_id=product_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return AhaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_product_pages" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return AhaResponse(success=False, error=str(e), message="Failed to execute list_product_pages")

    async def list_product_integrations(
        self,
        product_id: str
    ) -> AhaResponse:
        """List all integrations for a product (API v1)

        Args:
            product_id: The product ID

        Returns:
            AhaResponse with operation result
        """
        url = self.base_url + "/products/{product_id}/integrations".format(product_id=product_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return AhaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_product_integrations" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return AhaResponse(success=False, error=str(e), message="Failed to execute list_product_integrations")

    async def list_custom_field_definitions(
        self
    ) -> AhaResponse:
        """List all custom field definitions (API v1)

        Returns:
            AhaResponse with operation result
        """
        url = self.base_url + "/custom_field_definitions"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return AhaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_custom_field_definitions" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return AhaResponse(success=False, error=str(e), message="Failed to execute list_custom_field_definitions")

    async def list_teams(
        self
    ) -> AhaResponse:
        """List all teams (API v1)

        Returns:
            AhaResponse with operation result
        """
        url = self.base_url + "/teams"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return AhaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_teams" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return AhaResponse(success=False, error=str(e), message="Failed to execute list_teams")

    async def get_team(
        self,
        team_id: str
    ) -> AhaResponse:
        """Get a specific team by ID (API v1)

        Args:
            team_id: The team ID

        Returns:
            AhaResponse with operation result
        """
        url = self.base_url + "/teams/{team_id}".format(team_id=team_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return AhaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_team" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return AhaResponse(success=False, error=str(e), message="Failed to execute get_team")

    async def list_team_members(
        self
    ) -> AhaResponse:
        """List all team members (API v1)

        Returns:
            AhaResponse with operation result
        """
        url = self.base_url + "/team_members"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return AhaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_team_members" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return AhaResponse(success=False, error=str(e), message="Failed to execute list_team_members")

    async def get_workflow(
        self,
        workflow_id: str
    ) -> AhaResponse:
        """Get a specific workflow by ID (API v1)

        Args:
            workflow_id: The workflow ID

        Returns:
            AhaResponse with operation result
        """
        url = self.base_url + "/workflows/{workflow_id}".format(workflow_id=workflow_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return AhaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_workflow" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return AhaResponse(success=False, error=str(e), message="Failed to execute get_workflow")

    async def list_audits(
        self,
        page: int | None = None,
        per_page: int | None = None
    ) -> AhaResponse:
        """List audit events (API v1)

        Args:
            page: Page number for pagination
            per_page: Number of results per page

        Returns:
            AhaResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if page is not None:
            query_params['page'] = str(page)
        if per_page is not None:
            query_params['per_page'] = str(per_page)

        url = self.base_url + "/audits"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return AhaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_audits" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return AhaResponse(success=False, error=str(e), message="Failed to execute list_audits")

    async def list_deletions(
        self,
        page: int | None = None,
        per_page: int | None = None
    ) -> AhaResponse:
        """List recently deleted records (API v1)

        Args:
            page: Page number for pagination
            per_page: Number of results per page

        Returns:
            AhaResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if page is not None:
            query_params['page'] = str(page)
        if per_page is not None:
            query_params['per_page'] = str(per_page)

        url = self.base_url + "/deletions"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return AhaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_deletions" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return AhaResponse(success=False, error=str(e), message="Failed to execute list_deletions")

    async def list_strategy_models(
        self
    ) -> AhaResponse:
        """List all strategy models (API v1)

        Returns:
            AhaResponse with operation result
        """
        url = self.base_url + "/strategy_models"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return AhaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_strategy_models" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return AhaResponse(success=False, error=str(e), message="Failed to execute list_strategy_models")

    async def get_strategy_model(
        self,
        strategy_model_id: str
    ) -> AhaResponse:
        """Get a specific strategy model by ID (API v1)

        Args:
            strategy_model_id: The strategy model ID

        Returns:
            AhaResponse with operation result
        """
        url = self.base_url + "/strategy_models/{strategy_model_id}".format(strategy_model_id=strategy_model_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return AhaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_strategy_model" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return AhaResponse(success=False, error=str(e), message="Failed to execute get_strategy_model")

    async def list_strategy_visions(
        self
    ) -> AhaResponse:
        """List all strategy visions (API v1)

        Returns:
            AhaResponse with operation result
        """
        url = self.base_url + "/strategy_visions"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return AhaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_strategy_visions" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return AhaResponse(success=False, error=str(e), message="Failed to execute list_strategy_visions")

    async def get_strategy_vision(
        self,
        strategy_vision_id: str
    ) -> AhaResponse:
        """Get a specific strategy vision by ID (API v1)

        Args:
            strategy_vision_id: The strategy vision ID

        Returns:
            AhaResponse with operation result
        """
        url = self.base_url + "/strategy_visions/{strategy_vision_id}".format(strategy_vision_id=strategy_vision_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return AhaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_strategy_vision" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return AhaResponse(success=False, error=str(e), message="Failed to execute get_strategy_vision")

    async def list_strategy_positions(
        self
    ) -> AhaResponse:
        """List all strategy positions (API v1)

        Returns:
            AhaResponse with operation result
        """
        url = self.base_url + "/strategy_positions"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return AhaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_strategy_positions" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return AhaResponse(success=False, error=str(e), message="Failed to execute list_strategy_positions")

    async def get_strategy_position(
        self,
        strategy_position_id: str
    ) -> AhaResponse:
        """Get a specific strategy position by ID (API v1)

        Args:
            strategy_position_id: The strategy position ID

        Returns:
            AhaResponse with operation result
        """
        url = self.base_url + "/strategy_positions/{strategy_position_id}".format(strategy_position_id=strategy_position_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return AhaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_strategy_position" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return AhaResponse(success=False, error=str(e), message="Failed to execute get_strategy_position")

    async def list_product_creative_briefs(
        self,
        product_id: str
    ) -> AhaResponse:
        """List creative briefs for a product (API v1)

        Args:
            product_id: The product ID

        Returns:
            AhaResponse with operation result
        """
        url = self.base_url + "/products/{product_id}/creative_briefs".format(product_id=product_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return AhaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_product_creative_briefs" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return AhaResponse(success=False, error=str(e), message="Failed to execute list_product_creative_briefs")

    async def get_creative_brief(
        self,
        creative_brief_id: str
    ) -> AhaResponse:
        """Get a specific creative brief by ID (API v1)

        Args:
            creative_brief_id: The creative brief ID

        Returns:
            AhaResponse with operation result
        """
        url = self.base_url + "/creative_briefs/{creative_brief_id}".format(creative_brief_id=creative_brief_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return AhaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_creative_brief" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return AhaResponse(success=False, error=str(e), message="Failed to execute get_creative_brief")

    async def list_product_personas(
        self,
        product_id: str
    ) -> AhaResponse:
        """List personas for a product (API v1)

        Args:
            product_id: The product ID

        Returns:
            AhaResponse with operation result
        """
        url = self.base_url + "/products/{product_id}/personas".format(product_id=product_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return AhaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_product_personas" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return AhaResponse(success=False, error=str(e), message="Failed to execute list_product_personas")

    async def get_competitor(
        self,
        competitor_id: str
    ) -> AhaResponse:
        """Get a specific competitor by ID (API v1)

        Args:
            competitor_id: The competitor ID

        Returns:
            AhaResponse with operation result
        """
        url = self.base_url + "/competitors/{competitor_id}".format(competitor_id=competitor_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return AhaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_competitor" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return AhaResponse(success=False, error=str(e), message="Failed to execute get_competitor")

    async def list_schedules(
        self
    ) -> AhaResponse:
        """List all schedules (API v1)

        Returns:
            AhaResponse with operation result
        """
        url = self.base_url + "/schedules"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return AhaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_schedules" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return AhaResponse(success=False, error=str(e), message="Failed to execute list_schedules")

    async def list_screen_definitions(
        self
    ) -> AhaResponse:
        """List all screen definitions (API v1)

        Returns:
            AhaResponse with operation result
        """
        url = self.base_url + "/screen_definitions"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return AhaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_screen_definitions" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return AhaResponse(success=False, error=str(e), message="Failed to execute list_screen_definitions")

    async def get_screen_definition(
        self,
        screen_definition_id: str
    ) -> AhaResponse:
        """Get a specific screen definition by ID (API v1)

        Args:
            screen_definition_id: The screen definition ID

        Returns:
            AhaResponse with operation result
        """
        url = self.base_url + "/screen_definitions/{screen_definition_id}".format(screen_definition_id=screen_definition_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return AhaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_screen_definition" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return AhaResponse(success=False, error=str(e), message="Failed to execute get_screen_definition")
