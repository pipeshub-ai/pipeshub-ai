"""
Mattermost REST API DataSource - Auto-generated API wrapper

Generated from Mattermost REST API v4 documentation.
Uses HTTP client for direct REST API interactions.
All methods have explicit parameter signatures.
"""

from __future__ import annotations

from typing import Any

from app.sources.client.http.http_request import HTTPRequest
from app.sources.client.mattermost.mattermost import (
    MattermostClient,
    MattermostResponse,
)

# HTTP status code constant
HTTP_ERROR_THRESHOLD = 400


class MattermostDataSource:
    """Mattermost REST API DataSource

    Provides async wrapper methods for Mattermost REST API v4 operations:
    - User management
    - Team management
    - Channel management
    - Post/message management
    - File info
    - System health
    - Emoji

    The base URL is determined by the server domain configured in the client.
    All methods return MattermostResponse objects.
    """

    def __init__(self, client: MattermostClient) -> None:
        """Initialize with MattermostClient.

        Args:
            client: MattermostClient instance with configured authentication
        """
        self._client = client
        self.http = client.get_client()
        try:
            self.base_url = self.http.get_base_url().rstrip("/")
        except AttributeError as exc:
            raise ValueError(
                "HTTP client does not have get_base_url method"
            ) from exc

    def get_data_source(self) -> "MattermostDataSource":
        """Return the data source instance."""
        return self

    def get_client(self) -> MattermostClient:
        """Return the underlying MattermostClient."""
        return self._client

    # -----------------------------------------------------------------------
    # Users
    # -----------------------------------------------------------------------

    async def get_users(
        self,
        *,
        page: int | None = None,
        per_page: int | None = None,
        in_team: str | None = None,
        in_channel: str | None = None,
        sort: str | None = None,
    ) -> MattermostResponse:
        """Get a list of users.

        Args:
            page: Page number (0-based)
            per_page: Number of results per page (max 200)
            in_team: Filter by team ID
            in_channel: Filter by channel ID
            sort: Sort field (e.g. "last_activity_at", "create_at")

        Returns:
            MattermostResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if page is not None:
            query_params["page"] = str(page)
        if per_page is not None:
            query_params["per_page"] = str(per_page)
        if in_team is not None:
            query_params["in_team"] = in_team
        if in_channel is not None:
            query_params["in_channel"] = in_channel
        if sort is not None:
            query_params["sort"] = sort

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
            return MattermostResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message=(
                    "Successfully executed get_users"
                    if response.status < HTTP_ERROR_THRESHOLD
                    else f"Failed with status {response.status}"
                ),
            )
        except Exception as e:
            return MattermostResponse(
                success=False,
                error=str(e),
                message="Failed to execute get_users",
            )

    async def get_user(self, user_id: str) -> MattermostResponse:
        """Get a user by ID.

        Args:
            user_id: The user ID

        Returns:
            MattermostResponse with operation result
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
            return MattermostResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message=(
                    "Successfully executed get_user"
                    if response.status < HTTP_ERROR_THRESHOLD
                    else f"Failed with status {response.status}"
                ),
            )
        except Exception as e:
            return MattermostResponse(
                success=False,
                error=str(e),
                message="Failed to execute get_user",
            )

    async def get_me(self) -> MattermostResponse:
        """Get the authenticated user's details.

        Returns:
            MattermostResponse with operation result
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
            return MattermostResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message=(
                    "Successfully executed get_me"
                    if response.status < HTTP_ERROR_THRESHOLD
                    else f"Failed with status {response.status}"
                ),
            )
        except Exception as e:
            return MattermostResponse(
                success=False,
                error=str(e),
                message="Failed to execute get_me",
            )

    # -----------------------------------------------------------------------
    # Teams
    # -----------------------------------------------------------------------

    async def get_teams(
        self,
        *,
        page: int | None = None,
        per_page: int | None = None,
    ) -> MattermostResponse:
        """Get a list of teams.

        Args:
            page: Page number (0-based)
            per_page: Number of results per page

        Returns:
            MattermostResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if page is not None:
            query_params["page"] = str(page)
        if per_page is not None:
            query_params["per_page"] = str(per_page)

        url = self.base_url + "/teams"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return MattermostResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message=(
                    "Successfully executed get_teams"
                    if response.status < HTTP_ERROR_THRESHOLD
                    else f"Failed with status {response.status}"
                ),
            )
        except Exception as e:
            return MattermostResponse(
                success=False,
                error=str(e),
                message="Failed to execute get_teams",
            )

    async def get_team(self, team_id: str) -> MattermostResponse:
        """Get a team by ID.

        Args:
            team_id: The team ID

        Returns:
            MattermostResponse with operation result
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
            return MattermostResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message=(
                    "Successfully executed get_team"
                    if response.status < HTTP_ERROR_THRESHOLD
                    else f"Failed with status {response.status}"
                ),
            )
        except Exception as e:
            return MattermostResponse(
                success=False,
                error=str(e),
                message="Failed to execute get_team",
            )

    async def get_team_channels(
        self,
        team_id: str,
        *,
        page: int | None = None,
        per_page: int | None = None,
    ) -> MattermostResponse:
        """Get channels for a team.

        Args:
            team_id: The team ID
            page: Page number (0-based)
            per_page: Number of results per page

        Returns:
            MattermostResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if page is not None:
            query_params["page"] = str(page)
        if per_page is not None:
            query_params["per_page"] = str(per_page)

        url = self.base_url + "/teams/{team_id}/channels".format(
            team_id=team_id
        )

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return MattermostResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message=(
                    "Successfully executed get_team_channels"
                    if response.status < HTTP_ERROR_THRESHOLD
                    else f"Failed with status {response.status}"
                ),
            )
        except Exception as e:
            return MattermostResponse(
                success=False,
                error=str(e),
                message="Failed to execute get_team_channels",
            )

    async def get_team_members(
        self,
        team_id: str,
        *,
        page: int | None = None,
        per_page: int | None = None,
    ) -> MattermostResponse:
        """Get members of a team.

        Args:
            team_id: The team ID
            page: Page number (0-based)
            per_page: Number of results per page

        Returns:
            MattermostResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if page is not None:
            query_params["page"] = str(page)
        if per_page is not None:
            query_params["per_page"] = str(per_page)

        url = self.base_url + "/teams/{team_id}/members".format(
            team_id=team_id
        )

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return MattermostResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message=(
                    "Successfully executed get_team_members"
                    if response.status < HTTP_ERROR_THRESHOLD
                    else f"Failed with status {response.status}"
                ),
            )
        except Exception as e:
            return MattermostResponse(
                success=False,
                error=str(e),
                message="Failed to execute get_team_members",
            )

    async def get_user_teams(self, user_id: str) -> MattermostResponse:
        """Get teams for a user.

        Args:
            user_id: The user ID

        Returns:
            MattermostResponse with operation result
        """
        url = self.base_url + "/users/{user_id}/teams".format(
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
            return MattermostResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message=(
                    "Successfully executed get_user_teams"
                    if response.status < HTTP_ERROR_THRESHOLD
                    else f"Failed with status {response.status}"
                ),
            )
        except Exception as e:
            return MattermostResponse(
                success=False,
                error=str(e),
                message="Failed to execute get_user_teams",
            )

    # -----------------------------------------------------------------------
    # Channels
    # -----------------------------------------------------------------------

    async def get_channels(
        self,
        *,
        page: int | None = None,
        per_page: int | None = None,
    ) -> MattermostResponse:
        """Get a list of all channels.

        Args:
            page: Page number (0-based)
            per_page: Number of results per page

        Returns:
            MattermostResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if page is not None:
            query_params["page"] = str(page)
        if per_page is not None:
            query_params["per_page"] = str(per_page)

        url = self.base_url + "/channels"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return MattermostResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message=(
                    "Successfully executed get_channels"
                    if response.status < HTTP_ERROR_THRESHOLD
                    else f"Failed with status {response.status}"
                ),
            )
        except Exception as e:
            return MattermostResponse(
                success=False,
                error=str(e),
                message="Failed to execute get_channels",
            )

    async def get_channel(self, channel_id: str) -> MattermostResponse:
        """Get a channel by ID.

        Args:
            channel_id: The channel ID

        Returns:
            MattermostResponse with operation result
        """
        url = self.base_url + "/channels/{channel_id}".format(
            channel_id=channel_id
        )

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return MattermostResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message=(
                    "Successfully executed get_channel"
                    if response.status < HTTP_ERROR_THRESHOLD
                    else f"Failed with status {response.status}"
                ),
            )
        except Exception as e:
            return MattermostResponse(
                success=False,
                error=str(e),
                message="Failed to execute get_channel",
            )

    async def get_channel_posts(
        self,
        channel_id: str,
        *,
        page: int | None = None,
        per_page: int | None = None,
        since: int | None = None,
        before: str | None = None,
        after: str | None = None,
    ) -> MattermostResponse:
        """Get posts for a channel.

        Args:
            channel_id: The channel ID
            page: Page number (0-based)
            per_page: Number of results per page
            since: Unix timestamp in milliseconds to filter posts created after
            before: Post ID to get posts before
            after: Post ID to get posts after

        Returns:
            MattermostResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if page is not None:
            query_params["page"] = str(page)
        if per_page is not None:
            query_params["per_page"] = str(per_page)
        if since is not None:
            query_params["since"] = str(since)
        if before is not None:
            query_params["before"] = before
        if after is not None:
            query_params["after"] = after

        url = self.base_url + "/channels/{channel_id}/posts".format(
            channel_id=channel_id
        )

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return MattermostResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message=(
                    "Successfully executed get_channel_posts"
                    if response.status < HTTP_ERROR_THRESHOLD
                    else f"Failed with status {response.status}"
                ),
            )
        except Exception as e:
            return MattermostResponse(
                success=False,
                error=str(e),
                message="Failed to execute get_channel_posts",
            )

    # -----------------------------------------------------------------------
    # Posts
    # -----------------------------------------------------------------------

    async def get_post(self, post_id: str) -> MattermostResponse:
        """Get a post by ID.

        Args:
            post_id: The post ID

        Returns:
            MattermostResponse with operation result
        """
        url = self.base_url + "/posts/{post_id}".format(post_id=post_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return MattermostResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message=(
                    "Successfully executed get_post"
                    if response.status < HTTP_ERROR_THRESHOLD
                    else f"Failed with status {response.status}"
                ),
            )
        except Exception as e:
            return MattermostResponse(
                success=False,
                error=str(e),
                message="Failed to execute get_post",
            )

    # -----------------------------------------------------------------------
    # Files
    # -----------------------------------------------------------------------

    async def get_file_info(self, file_id: str) -> MattermostResponse:
        """Get file info by ID.

        Args:
            file_id: The file ID

        Returns:
            MattermostResponse with operation result
        """
        url = self.base_url + "/files/{file_id}/info".format(
            file_id=file_id
        )

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return MattermostResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message=(
                    "Successfully executed get_file_info"
                    if response.status < HTTP_ERROR_THRESHOLD
                    else f"Failed with status {response.status}"
                ),
            )
        except Exception as e:
            return MattermostResponse(
                success=False,
                error=str(e),
                message="Failed to execute get_file_info",
            )

    # -----------------------------------------------------------------------
    # System
    # -----------------------------------------------------------------------

    async def ping(self) -> MattermostResponse:
        """Check system health (ping).

        Returns:
            MattermostResponse with operation result
        """
        url = self.base_url + "/system/ping"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return MattermostResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message=(
                    "Successfully executed ping"
                    if response.status < HTTP_ERROR_THRESHOLD
                    else f"Failed with status {response.status}"
                ),
            )
        except Exception as e:
            return MattermostResponse(
                success=False,
                error=str(e),
                message="Failed to execute ping",
            )

    # -----------------------------------------------------------------------
    # Emoji
    # -----------------------------------------------------------------------

    async def get_emoji_list(
        self,
        *,
        page: int | None = None,
        per_page: int | None = None,
        sort: str | None = None,
    ) -> MattermostResponse:
        """Get a list of custom emoji.

        Args:
            page: Page number (0-based)
            per_page: Number of results per page
            sort: Sort order ("name" for alphabetical)

        Returns:
            MattermostResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if page is not None:
            query_params["page"] = str(page)
        if per_page is not None:
            query_params["per_page"] = str(per_page)
        if sort is not None:
            query_params["sort"] = sort

        url = self.base_url + "/emoji"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return MattermostResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message=(
                    "Successfully executed get_emoji_list"
                    if response.status < HTTP_ERROR_THRESHOLD
                    else f"Failed with status {response.status}"
                ),
            )
        except Exception as e:
            return MattermostResponse(
                success=False,
                error=str(e),
                message="Failed to execute get_emoji_list",
            )
