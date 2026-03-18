"""
InVision REST API DataSource - Auto-generated API wrapper

Generated from InVision REST API v2 documentation.
Uses HTTP client for direct REST API interactions.
All methods have explicit parameter signatures.
"""

from __future__ import annotations

from typing import Any

from app.sources.client.http.http_request import HTTPRequest
from app.sources.client.invision.invision import InVisionClient, InVisionResponse

# HTTP status code constant
HTTP_ERROR_THRESHOLD = 400


class InVisionDataSource:
    """InVision REST API DataSource

    Provides async wrapper methods for InVision REST API operations:
    - User profile
    - Project management (list, get, create)
    - Screen operations (list, get)
    - Comment management
    - Team and member management
    - Space operations

    The base URL is https://api.invisionapp.com/v2.

    All methods return InVisionResponse objects.
    """

    def __init__(self, client: InVisionClient) -> None:
        """Initialize with InVisionClient.

        Args:
            client: InVisionClient instance with configured authentication
        """
        self._client = client
        self.http = client.get_client()
        try:
            self.base_url = self.http.get_base_url().rstrip('/')
        except AttributeError as exc:
            raise ValueError('HTTP client does not have get_base_url method') from exc

    def get_data_source(self) -> 'InVisionDataSource':
        """Return the data source instance."""
        return self

    def get_client(self) -> InVisionClient:
        """Return the underlying InVisionClient."""
        return self._client

    async def get_current_user(
        self
    ) -> InVisionResponse:
        """Get the current authenticated user details (API v2)

        Returns:
            InVisionResponse with operation result
        """
        url = self.base_url + "/users/me"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return InVisionResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_current_user" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return InVisionResponse(success=False, error=str(e), message="Failed to execute get_current_user")

    async def list_projects(
        self,
        *,
        limit: int | None = None,
        offset: int | None = None,
        sortBy: str | None = None,
        archived: bool | None = None
    ) -> InVisionResponse:
        """List all projects accessible to the authenticated user (API v2)

        Args:
            limit: Maximum number of results to return
            offset: Number of results to skip for pagination
            sortBy: Field to sort results by
            archived: Filter by archived status

        Returns:
            InVisionResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if limit is not None:
            query_params['limit'] = str(limit)
        if offset is not None:
            query_params['offset'] = str(offset)
        if sortBy is not None:
            query_params['sortBy'] = sortBy
        if archived is not None:
            query_params['archived'] = str(archived).lower()

        url = self.base_url + "/projects"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return InVisionResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_projects" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return InVisionResponse(success=False, error=str(e), message="Failed to execute list_projects")

    async def get_project(
        self,
        projectId: str
    ) -> InVisionResponse:
        """Get a specific project by ID (API v2)

        Args:
            projectId: The project ID

        Returns:
            InVisionResponse with operation result
        """
        url = self.base_url + "/projects/{projectId}".format(projectId=projectId)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return InVisionResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_project" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return InVisionResponse(success=False, error=str(e), message="Failed to execute get_project")

    async def create_project(
        self,
        name: str,
        project_type: str | None = None,
        description: str | None = None
    ) -> InVisionResponse:
        """Create a new project (API v2)

        Args:
            name: The name of the project
            project_type: The type of the project
            description: The project description

        Returns:
            InVisionResponse with operation result
        """
        url = self.base_url + "/projects"

        body: dict[str, Any] = {}
        body['name'] = name
        if project_type is not None:
            body['project_type'] = project_type
        if description is not None:
            body['description'] = description

        try:
            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return InVisionResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed create_project" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return InVisionResponse(success=False, error=str(e), message="Failed to execute create_project")

    async def list_project_screens(
        self,
        projectId: str,
        limit: int | None = None,
        offset: int | None = None,
        sortBy: str | None = None
    ) -> InVisionResponse:
        """List all screens in a project (API v2)

        Args:
            projectId: The project ID
            limit: Maximum number of results to return
            offset: Number of results to skip for pagination
            sortBy: Field to sort results by

        Returns:
            InVisionResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if limit is not None:
            query_params['limit'] = str(limit)
        if offset is not None:
            query_params['offset'] = str(offset)
        if sortBy is not None:
            query_params['sortBy'] = sortBy

        url = self.base_url + "/projects/{projectId}/screens".format(projectId=projectId)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return InVisionResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_project_screens" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return InVisionResponse(success=False, error=str(e), message="Failed to execute list_project_screens")

    async def get_screen(
        self,
        screenId: str
    ) -> InVisionResponse:
        """Get a specific screen by ID (API v2)

        Args:
            screenId: The screen ID

        Returns:
            InVisionResponse with operation result
        """
        url = self.base_url + "/screens/{screenId}".format(screenId=screenId)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return InVisionResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_screen" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return InVisionResponse(success=False, error=str(e), message="Failed to execute get_screen")

    async def list_project_comments(
        self,
        projectId: str,
        limit: int | None = None,
        offset: int | None = None
    ) -> InVisionResponse:
        """List all comments in a project (API v2)

        Args:
            projectId: The project ID
            limit: Maximum number of results to return
            offset: Number of results to skip for pagination

        Returns:
            InVisionResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if limit is not None:
            query_params['limit'] = str(limit)
        if offset is not None:
            query_params['offset'] = str(offset)

        url = self.base_url + "/projects/{projectId}/comments".format(projectId=projectId)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return InVisionResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_project_comments" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return InVisionResponse(success=False, error=str(e), message="Failed to execute list_project_comments")

    async def list_teams(
        self
    ) -> InVisionResponse:
        """List all teams (API v2)

        Returns:
            InVisionResponse with operation result
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
            return InVisionResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_teams" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return InVisionResponse(success=False, error=str(e), message="Failed to execute list_teams")

    async def get_team(
        self,
        teamId: str
    ) -> InVisionResponse:
        """Get a specific team by ID (API v2)

        Args:
            teamId: The team ID

        Returns:
            InVisionResponse with operation result
        """
        url = self.base_url + "/teams/{teamId}".format(teamId=teamId)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return InVisionResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_team" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return InVisionResponse(success=False, error=str(e), message="Failed to execute get_team")

    async def list_team_members(
        self,
        teamId: str
    ) -> InVisionResponse:
        """List all members of a team (API v2)

        Args:
            teamId: The team ID

        Returns:
            InVisionResponse with operation result
        """
        url = self.base_url + "/teams/{teamId}/members".format(teamId=teamId)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return InVisionResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_team_members" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return InVisionResponse(success=False, error=str(e), message="Failed to execute list_team_members")

    async def list_spaces(
        self,
        limit: int | None = None,
        offset: int | None = None
    ) -> InVisionResponse:
        """List all spaces (API v2)

        Args:
            limit: Maximum number of results to return
            offset: Number of results to skip for pagination

        Returns:
            InVisionResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if limit is not None:
            query_params['limit'] = str(limit)
        if offset is not None:
            query_params['offset'] = str(offset)

        url = self.base_url + "/spaces"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return InVisionResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_spaces" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return InVisionResponse(success=False, error=str(e), message="Failed to execute list_spaces")

    async def get_space(
        self,
        spaceId: str
    ) -> InVisionResponse:
        """Get a specific space by ID (API v2)

        Args:
            spaceId: The space ID

        Returns:
            InVisionResponse with operation result
        """
        url = self.base_url + "/spaces/{spaceId}".format(spaceId=spaceId)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return InVisionResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_space" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return InVisionResponse(success=False, error=str(e), message="Failed to execute get_space")
