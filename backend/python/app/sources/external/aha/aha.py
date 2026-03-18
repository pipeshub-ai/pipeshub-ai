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
    - Idea management
    - Release management
    - Goal operations
    - Epic management
    - Integration listing

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
