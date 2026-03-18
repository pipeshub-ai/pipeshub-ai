# ruff: noqa
"""
JumpCloud REST API DataSource - Auto-generated API wrapper

Generated from JumpCloud API v2 documentation.
Uses HTTP client for direct REST API interactions.
All methods have explicit parameter signatures.
"""

from __future__ import annotations

from typing import Any

from app.sources.client.jumpcloud.jumpcloud import JumpCloudClient, JumpCloudResponse
from app.sources.client.http.http_request import HTTPRequest

# HTTP status code constant
HTTP_ERROR_THRESHOLD = 400


class JumpCloudDataSource:
    """JumpCloud REST API DataSource

    Provides async wrapper methods for JumpCloud REST API operations:
    - User management
    - User group management
    - System and system group management
    - Application management
    - Directory management
    - Policy management
    - Organization and RADIUS server management

    The base URL is https://console.jumpcloud.com/api/v2

    All methods return JumpCloudResponse objects.
    """

    def __init__(self, client: JumpCloudClient) -> None:
        """Initialize with JumpCloudClient.

        Args:
            client: JumpCloudClient instance with configured authentication
        """
        self._client = client
        self.http = client.get_client()
        try:
            self.base_url = self.http.get_base_url().rstrip('/')
        except AttributeError as exc:
            raise ValueError('HTTP client does not have get_base_url method') from exc

    def get_data_source(self) -> 'JumpCloudDataSource':
        """Return the data source instance."""
        return self

    def get_client(self) -> JumpCloudClient:
        """Return the underlying JumpCloudClient."""
        return self._client

    # -----------------------------------------------------------------------
    # Users
    # -----------------------------------------------------------------------

    async def list_users(
        self,
        *,
        limit: int | None = None,
        skip: int | None = None,
        sort: str | None = None,
        filter: str | None = None,
    ) -> JumpCloudResponse:
        """List users (GET /users)

        Args:
            limit: Maximum number of results
            skip: Number of results to skip
            sort: Field to sort by
            filter: Filter expression

        Returns:
            JumpCloudResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if limit is not None:
            query_params['limit'] = str(limit)
        if skip is not None:
            query_params['skip'] = str(skip)
        if sort is not None:
            query_params['sort'] = sort
        if filter is not None:
            query_params['filter'] = filter

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
            return JumpCloudResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_users" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return JumpCloudResponse(success=False, error=str(e), message="Failed to execute list_users")

    async def get_user(
        self,
        user_id: str,
    ) -> JumpCloudResponse:
        """Get a specific user (GET /users/{id})

        Args:
            user_id: The user ID

        Returns:
            JumpCloudResponse with operation result
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
            return JumpCloudResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_user" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return JumpCloudResponse(success=False, error=str(e), message="Failed to execute get_user")

    # -----------------------------------------------------------------------
    # User Groups
    # -----------------------------------------------------------------------

    async def list_user_groups(
        self,
        *,
        limit: int | None = None,
        skip: int | None = None,
        sort: str | None = None,
        filter: str | None = None,
    ) -> JumpCloudResponse:
        """List user groups (GET /usergroups)

        Args:
            limit: Maximum number of results
            skip: Number of results to skip
            sort: Field to sort by
            filter: Filter expression

        Returns:
            JumpCloudResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if limit is not None:
            query_params['limit'] = str(limit)
        if skip is not None:
            query_params['skip'] = str(skip)
        if sort is not None:
            query_params['sort'] = sort
        if filter is not None:
            query_params['filter'] = filter

        url = self.base_url + "/usergroups"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return JumpCloudResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_user_groups" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return JumpCloudResponse(success=False, error=str(e), message="Failed to execute list_user_groups")

    async def get_user_group(
        self,
        group_id: str,
    ) -> JumpCloudResponse:
        """Get a specific user group (GET /usergroups/{id})

        Args:
            group_id: The user group ID

        Returns:
            JumpCloudResponse with operation result
        """
        url = self.base_url + "/usergroups/{group_id}".format(group_id=group_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return JumpCloudResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_user_group" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return JumpCloudResponse(success=False, error=str(e), message="Failed to execute get_user_group")

    # -----------------------------------------------------------------------
    # System Groups
    # -----------------------------------------------------------------------

    async def list_system_groups(
        self,
        *,
        limit: int | None = None,
        skip: int | None = None,
        sort: str | None = None,
        filter: str | None = None,
    ) -> JumpCloudResponse:
        """List system groups (GET /systemgroups)

        Args:
            limit: Maximum number of results
            skip: Number of results to skip
            sort: Field to sort by
            filter: Filter expression

        Returns:
            JumpCloudResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if limit is not None:
            query_params['limit'] = str(limit)
        if skip is not None:
            query_params['skip'] = str(skip)
        if sort is not None:
            query_params['sort'] = sort
        if filter is not None:
            query_params['filter'] = filter

        url = self.base_url + "/systemgroups"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return JumpCloudResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_system_groups" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return JumpCloudResponse(success=False, error=str(e), message="Failed to execute list_system_groups")

    async def get_system_group(
        self,
        group_id: str,
    ) -> JumpCloudResponse:
        """Get a specific system group (GET /systemgroups/{id})

        Args:
            group_id: The system group ID

        Returns:
            JumpCloudResponse with operation result
        """
        url = self.base_url + "/systemgroups/{group_id}".format(group_id=group_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return JumpCloudResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_system_group" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return JumpCloudResponse(success=False, error=str(e), message="Failed to execute get_system_group")

    # -----------------------------------------------------------------------
    # Systems
    # -----------------------------------------------------------------------

    async def list_systems(
        self,
        *,
        limit: int | None = None,
        skip: int | None = None,
        sort: str | None = None,
        filter: str | None = None,
    ) -> JumpCloudResponse:
        """List systems (GET /systems)

        Args:
            limit: Maximum number of results
            skip: Number of results to skip
            sort: Field to sort by
            filter: Filter expression

        Returns:
            JumpCloudResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if limit is not None:
            query_params['limit'] = str(limit)
        if skip is not None:
            query_params['skip'] = str(skip)
        if sort is not None:
            query_params['sort'] = sort
        if filter is not None:
            query_params['filter'] = filter

        url = self.base_url + "/systems"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return JumpCloudResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_systems" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return JumpCloudResponse(success=False, error=str(e), message="Failed to execute list_systems")

    async def get_system(
        self,
        system_id: str,
    ) -> JumpCloudResponse:
        """Get a specific system (GET /systems/{id})

        Args:
            system_id: The system ID

        Returns:
            JumpCloudResponse with operation result
        """
        url = self.base_url + "/systems/{system_id}".format(system_id=system_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return JumpCloudResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_system" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return JumpCloudResponse(success=False, error=str(e), message="Failed to execute get_system")

    # -----------------------------------------------------------------------
    # Applications
    # -----------------------------------------------------------------------

    async def list_applications(
        self,
        *,
        limit: int | None = None,
        skip: int | None = None,
        sort: str | None = None,
        filter: str | None = None,
    ) -> JumpCloudResponse:
        """List applications (GET /applications)

        Args:
            limit: Maximum number of results
            skip: Number of results to skip
            sort: Field to sort by
            filter: Filter expression

        Returns:
            JumpCloudResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if limit is not None:
            query_params['limit'] = str(limit)
        if skip is not None:
            query_params['skip'] = str(skip)
        if sort is not None:
            query_params['sort'] = sort
        if filter is not None:
            query_params['filter'] = filter

        url = self.base_url + "/applications"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return JumpCloudResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_applications" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return JumpCloudResponse(success=False, error=str(e), message="Failed to execute list_applications")

    async def get_application(
        self,
        app_id: str,
    ) -> JumpCloudResponse:
        """Get a specific application (GET /applications/{id})

        Args:
            app_id: The application ID

        Returns:
            JumpCloudResponse with operation result
        """
        url = self.base_url + "/applications/{app_id}".format(app_id=app_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return JumpCloudResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_application" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return JumpCloudResponse(success=False, error=str(e), message="Failed to execute get_application")

    # -----------------------------------------------------------------------
    # Directories
    # -----------------------------------------------------------------------

    async def list_directories(
        self,
        *,
        limit: int | None = None,
        skip: int | None = None,
        sort: str | None = None,
    ) -> JumpCloudResponse:
        """List directories (GET /directories)

        Args:
            limit: Maximum number of results
            skip: Number of results to skip
            sort: Field to sort by

        Returns:
            JumpCloudResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if limit is not None:
            query_params['limit'] = str(limit)
        if skip is not None:
            query_params['skip'] = str(skip)
        if sort is not None:
            query_params['sort'] = sort

        url = self.base_url + "/directories"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return JumpCloudResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_directories" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return JumpCloudResponse(success=False, error=str(e), message="Failed to execute list_directories")

    # -----------------------------------------------------------------------
    # Policies
    # -----------------------------------------------------------------------

    async def list_policies(
        self,
        *,
        limit: int | None = None,
        skip: int | None = None,
        sort: str | None = None,
        filter: str | None = None,
    ) -> JumpCloudResponse:
        """List policies (GET /policies)

        Args:
            limit: Maximum number of results
            skip: Number of results to skip
            sort: Field to sort by
            filter: Filter expression

        Returns:
            JumpCloudResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if limit is not None:
            query_params['limit'] = str(limit)
        if skip is not None:
            query_params['skip'] = str(skip)
        if sort is not None:
            query_params['sort'] = sort
        if filter is not None:
            query_params['filter'] = filter

        url = self.base_url + "/policies"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return JumpCloudResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_policies" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return JumpCloudResponse(success=False, error=str(e), message="Failed to execute list_policies")

    async def get_policy(
        self,
        policy_id: str,
    ) -> JumpCloudResponse:
        """Get a specific policy (GET /policies/{id})

        Args:
            policy_id: The policy ID

        Returns:
            JumpCloudResponse with operation result
        """
        url = self.base_url + "/policies/{policy_id}".format(policy_id=policy_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return JumpCloudResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_policy" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return JumpCloudResponse(success=False, error=str(e), message="Failed to execute get_policy")

    # -----------------------------------------------------------------------
    # Organizations & RADIUS Servers
    # -----------------------------------------------------------------------

    async def list_organizations(
        self,
        *,
        limit: int | None = None,
        skip: int | None = None,
    ) -> JumpCloudResponse:
        """List organizations (GET /organizations)

        Args:
            limit: Maximum number of results
            skip: Number of results to skip

        Returns:
            JumpCloudResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if limit is not None:
            query_params['limit'] = str(limit)
        if skip is not None:
            query_params['skip'] = str(skip)

        url = self.base_url + "/organizations"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return JumpCloudResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_organizations" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return JumpCloudResponse(success=False, error=str(e), message="Failed to execute list_organizations")

    async def list_radius_servers(
        self,
        *,
        limit: int | None = None,
        skip: int | None = None,
        sort: str | None = None,
        filter: str | None = None,
    ) -> JumpCloudResponse:
        """List RADIUS servers (GET /radiusservers)

        Args:
            limit: Maximum number of results
            skip: Number of results to skip
            sort: Field to sort by
            filter: Filter expression

        Returns:
            JumpCloudResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if limit is not None:
            query_params['limit'] = str(limit)
        if skip is not None:
            query_params['skip'] = str(skip)
        if sort is not None:
            query_params['sort'] = sort
        if filter is not None:
            query_params['filter'] = filter

        url = self.base_url + "/radiusservers"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return JumpCloudResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_radius_servers" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return JumpCloudResponse(success=False, error=str(e), message="Failed to execute list_radius_servers")
