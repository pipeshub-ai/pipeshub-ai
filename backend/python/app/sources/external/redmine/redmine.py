"""
Redmine REST API DataSource - Auto-generated API wrapper

Generated from Redmine REST API documentation.
Uses HTTP client for direct REST API interactions.
All methods have explicit parameter signatures.
All endpoints use .json suffix for JSON responses.
"""

from __future__ import annotations

from typing import Any

from app.sources.client.http.http_request import HTTPRequest
from app.sources.client.redmine.redmine import RedmineClient, RedmineResponse

# HTTP status code constant
HTTP_ERROR_THRESHOLD = 400


class RedmineDataSource:
    """Redmine REST API DataSource

    Provides async wrapper methods for Redmine REST API operations:
    - Project management
    - Issue tracking
    - User management
    - Time entries
    - News
    - Wiki pages
    - Issue statuses, trackers, roles
    - Project memberships

    The base URL is the instance URL configured in the client.
    All endpoints append .json for JSON responses.
    All methods return RedmineResponse objects.
    """

    def __init__(self, client: RedmineClient) -> None:
        """Initialize with RedmineClient.

        Args:
            client: RedmineClient instance with configured authentication
        """
        self._client = client
        self.http = client.get_client()
        try:
            self.base_url = self.http.get_base_url().rstrip("/")
        except AttributeError as exc:
            raise ValueError(
                "HTTP client does not have get_base_url method"
            ) from exc

    def get_data_source(self) -> "RedmineDataSource":
        """Return the data source instance."""
        return self

    def get_client(self) -> RedmineClient:
        """Return the underlying RedmineClient."""
        return self._client

    # -----------------------------------------------------------------------
    # Projects
    # -----------------------------------------------------------------------

    async def get_projects(
        self,
        *,
        limit: int | None = None,
        offset: int | None = None,
    ) -> RedmineResponse:
        """Get a list of projects.

        Args:
            limit: Maximum number of results
            offset: Number of results to skip

        Returns:
            RedmineResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if limit is not None:
            query_params["limit"] = str(limit)
        if offset is not None:
            query_params["offset"] = str(offset)

        url = self.base_url + "/projects.json"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return RedmineResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message=(
                    "Successfully executed get_projects"
                    if response.status < HTTP_ERROR_THRESHOLD
                    else f"Failed with status {response.status}"
                ),
            )
        except Exception as e:
            return RedmineResponse(
                success=False,
                error=str(e),
                message="Failed to execute get_projects",
            )

    async def get_project(self, project_id: str) -> RedmineResponse:
        """Get a project by ID or identifier.

        Args:
            project_id: The project ID or string identifier

        Returns:
            RedmineResponse with operation result
        """
        url = self.base_url + "/projects/{project_id}.json".format(
            project_id=project_id
        )

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return RedmineResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message=(
                    "Successfully executed get_project"
                    if response.status < HTTP_ERROR_THRESHOLD
                    else f"Failed with status {response.status}"
                ),
            )
        except Exception as e:
            return RedmineResponse(
                success=False,
                error=str(e),
                message="Failed to execute get_project",
            )

    # -----------------------------------------------------------------------
    # Issues
    # -----------------------------------------------------------------------

    async def get_issues(
        self,
        *,
        project_id: str | None = None,
        tracker_id: str | None = None,
        status_id: str | None = None,
        assigned_to_id: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> RedmineResponse:
        """Get a list of issues.

        Args:
            project_id: Filter by project ID
            tracker_id: Filter by tracker ID
            status_id: Filter by status ID (use "open", "closed", "*", or numeric ID)
            assigned_to_id: Filter by assigned user ID
            limit: Maximum number of results
            offset: Number of results to skip

        Returns:
            RedmineResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if project_id is not None:
            query_params["project_id"] = project_id
        if tracker_id is not None:
            query_params["tracker_id"] = tracker_id
        if status_id is not None:
            query_params["status_id"] = status_id
        if assigned_to_id is not None:
            query_params["assigned_to_id"] = assigned_to_id
        if limit is not None:
            query_params["limit"] = str(limit)
        if offset is not None:
            query_params["offset"] = str(offset)

        url = self.base_url + "/issues.json"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return RedmineResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message=(
                    "Successfully executed get_issues"
                    if response.status < HTTP_ERROR_THRESHOLD
                    else f"Failed with status {response.status}"
                ),
            )
        except Exception as e:
            return RedmineResponse(
                success=False,
                error=str(e),
                message="Failed to execute get_issues",
            )

    async def get_issue(self, issue_id: str) -> RedmineResponse:
        """Get an issue by ID.

        Args:
            issue_id: The issue ID

        Returns:
            RedmineResponse with operation result
        """
        url = self.base_url + "/issues/{issue_id}.json".format(
            issue_id=issue_id
        )

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return RedmineResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message=(
                    "Successfully executed get_issue"
                    if response.status < HTTP_ERROR_THRESHOLD
                    else f"Failed with status {response.status}"
                ),
            )
        except Exception as e:
            return RedmineResponse(
                success=False,
                error=str(e),
                message="Failed to execute get_issue",
            )

    # -----------------------------------------------------------------------
    # Users
    # -----------------------------------------------------------------------

    async def get_users(
        self,
        *,
        limit: int | None = None,
        offset: int | None = None,
    ) -> RedmineResponse:
        """Get a list of users.

        Args:
            limit: Maximum number of results
            offset: Number of results to skip

        Returns:
            RedmineResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if limit is not None:
            query_params["limit"] = str(limit)
        if offset is not None:
            query_params["offset"] = str(offset)

        url = self.base_url + "/users.json"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return RedmineResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message=(
                    "Successfully executed get_users"
                    if response.status < HTTP_ERROR_THRESHOLD
                    else f"Failed with status {response.status}"
                ),
            )
        except Exception as e:
            return RedmineResponse(
                success=False,
                error=str(e),
                message="Failed to execute get_users",
            )

    async def get_user(self, user_id: str) -> RedmineResponse:
        """Get a user by ID.

        Args:
            user_id: The user ID

        Returns:
            RedmineResponse with operation result
        """
        url = self.base_url + "/users/{user_id}.json".format(
            user_id=user_id
        )

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return RedmineResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message=(
                    "Successfully executed get_user"
                    if response.status < HTTP_ERROR_THRESHOLD
                    else f"Failed with status {response.status}"
                ),
            )
        except Exception as e:
            return RedmineResponse(
                success=False,
                error=str(e),
                message="Failed to execute get_user",
            )

    # -----------------------------------------------------------------------
    # Time Entries
    # -----------------------------------------------------------------------

    async def get_time_entries(
        self,
        *,
        limit: int | None = None,
        offset: int | None = None,
    ) -> RedmineResponse:
        """Get a list of time entries.

        Args:
            limit: Maximum number of results
            offset: Number of results to skip

        Returns:
            RedmineResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if limit is not None:
            query_params["limit"] = str(limit)
        if offset is not None:
            query_params["offset"] = str(offset)

        url = self.base_url + "/time_entries.json"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return RedmineResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message=(
                    "Successfully executed get_time_entries"
                    if response.status < HTTP_ERROR_THRESHOLD
                    else f"Failed with status {response.status}"
                ),
            )
        except Exception as e:
            return RedmineResponse(
                success=False,
                error=str(e),
                message="Failed to execute get_time_entries",
            )

    async def get_time_entry(
        self, time_entry_id: str
    ) -> RedmineResponse:
        """Get a time entry by ID.

        Args:
            time_entry_id: The time entry ID

        Returns:
            RedmineResponse with operation result
        """
        url = self.base_url + "/time_entries/{time_entry_id}.json".format(
            time_entry_id=time_entry_id
        )

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return RedmineResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message=(
                    "Successfully executed get_time_entry"
                    if response.status < HTTP_ERROR_THRESHOLD
                    else f"Failed with status {response.status}"
                ),
            )
        except Exception as e:
            return RedmineResponse(
                success=False,
                error=str(e),
                message="Failed to execute get_time_entry",
            )

    # -----------------------------------------------------------------------
    # News
    # -----------------------------------------------------------------------

    async def get_news(
        self,
        *,
        limit: int | None = None,
        offset: int | None = None,
    ) -> RedmineResponse:
        """Get a list of news items.

        Args:
            limit: Maximum number of results
            offset: Number of results to skip

        Returns:
            RedmineResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if limit is not None:
            query_params["limit"] = str(limit)
        if offset is not None:
            query_params["offset"] = str(offset)

        url = self.base_url + "/news.json"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return RedmineResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message=(
                    "Successfully executed get_news"
                    if response.status < HTTP_ERROR_THRESHOLD
                    else f"Failed with status {response.status}"
                ),
            )
        except Exception as e:
            return RedmineResponse(
                success=False,
                error=str(e),
                message="Failed to execute get_news",
            )

    # -----------------------------------------------------------------------
    # Wiki
    # -----------------------------------------------------------------------

    async def get_wiki_index(
        self, project_id: str
    ) -> RedmineResponse:
        """Get wiki page index for a project.

        Args:
            project_id: The project ID or identifier

        Returns:
            RedmineResponse with operation result
        """
        url = self.base_url + "/projects/{project_id}/wiki/index.json".format(
            project_id=project_id
        )

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return RedmineResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message=(
                    "Successfully executed get_wiki_index"
                    if response.status < HTTP_ERROR_THRESHOLD
                    else f"Failed with status {response.status}"
                ),
            )
        except Exception as e:
            return RedmineResponse(
                success=False,
                error=str(e),
                message="Failed to execute get_wiki_index",
            )

    async def get_wiki_page(
        self,
        project_id: str,
        page_title: str,
    ) -> RedmineResponse:
        """Get a specific wiki page.

        Args:
            project_id: The project ID or identifier
            page_title: The wiki page title

        Returns:
            RedmineResponse with operation result
        """
        url = (
            self.base_url
            + "/projects/{project_id}/wiki/{page_title}.json".format(
                project_id=project_id, page_title=page_title
            )
        )

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return RedmineResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message=(
                    "Successfully executed get_wiki_page"
                    if response.status < HTTP_ERROR_THRESHOLD
                    else f"Failed with status {response.status}"
                ),
            )
        except Exception as e:
            return RedmineResponse(
                success=False,
                error=str(e),
                message="Failed to execute get_wiki_page",
            )

    # -----------------------------------------------------------------------
    # Issue Statuses
    # -----------------------------------------------------------------------

    async def get_issue_statuses(self) -> RedmineResponse:
        """Get all issue statuses.

        Returns:
            RedmineResponse with operation result
        """
        url = self.base_url + "/issue_statuses.json"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return RedmineResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message=(
                    "Successfully executed get_issue_statuses"
                    if response.status < HTTP_ERROR_THRESHOLD
                    else f"Failed with status {response.status}"
                ),
            )
        except Exception as e:
            return RedmineResponse(
                success=False,
                error=str(e),
                message="Failed to execute get_issue_statuses",
            )

    # -----------------------------------------------------------------------
    # Trackers
    # -----------------------------------------------------------------------

    async def get_trackers(self) -> RedmineResponse:
        """Get all trackers.

        Returns:
            RedmineResponse with operation result
        """
        url = self.base_url + "/trackers.json"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return RedmineResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message=(
                    "Successfully executed get_trackers"
                    if response.status < HTTP_ERROR_THRESHOLD
                    else f"Failed with status {response.status}"
                ),
            )
        except Exception as e:
            return RedmineResponse(
                success=False,
                error=str(e),
                message="Failed to execute get_trackers",
            )

    # -----------------------------------------------------------------------
    # Roles
    # -----------------------------------------------------------------------

    async def get_roles(self) -> RedmineResponse:
        """Get all roles.

        Returns:
            RedmineResponse with operation result
        """
        url = self.base_url + "/roles.json"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return RedmineResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message=(
                    "Successfully executed get_roles"
                    if response.status < HTTP_ERROR_THRESHOLD
                    else f"Failed with status {response.status}"
                ),
            )
        except Exception as e:
            return RedmineResponse(
                success=False,
                error=str(e),
                message="Failed to execute get_roles",
            )

    # -----------------------------------------------------------------------
    # Memberships
    # -----------------------------------------------------------------------

    async def get_memberships(
        self,
        *,
        limit: int | None = None,
        offset: int | None = None,
    ) -> RedmineResponse:
        """Get project memberships.

        Args:
            limit: Maximum number of results
            offset: Number of results to skip

        Returns:
            RedmineResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if limit is not None:
            query_params["limit"] = str(limit)
        if offset is not None:
            query_params["offset"] = str(offset)

        url = self.base_url + "/memberships.json"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return RedmineResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message=(
                    "Successfully executed get_memberships"
                    if response.status < HTTP_ERROR_THRESHOLD
                    else f"Failed with status {response.status}"
                ),
            )
        except Exception as e:
            return RedmineResponse(
                success=False,
                error=str(e),
                message="Failed to execute get_memberships",
            )
