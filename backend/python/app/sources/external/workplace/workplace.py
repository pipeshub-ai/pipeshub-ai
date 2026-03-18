# ruff: noqa
"""
Facebook Workplace (Meta Workplace) REST API DataSource - Auto-generated API wrapper

Generated from Facebook Graph API v18.0 documentation for Workplace.
Uses HTTP client for direct REST API interactions.
All methods have explicit parameter signatures.
"""

from __future__ import annotations

from typing import Any

from app.sources.client.workplace.workplace import WorkplaceClient, WorkplaceResponse
from app.sources.client.http.http_request import HTTPRequest

# HTTP status code constant
HTTP_ERROR_THRESHOLD = 400


class WorkplaceDataSource:
    """Facebook Workplace REST API DataSource

    Provides async wrapper methods for Facebook Workplace API operations:
    - Current user (me)
    - Community members
    - User profiles and feeds
    - Community groups
    - Group feeds and members
    - Posts and comments
    - Community feeds

    The base URL is https://graph.facebook.com/v18.0 by default.

    All methods return WorkplaceResponse objects.
    """

    def __init__(self, client: WorkplaceClient) -> None:
        """Initialize with WorkplaceClient.

        Args:
            client: WorkplaceClient instance with configured authentication
        """
        self._client = client
        self.http = client.get_client()
        try:
            self.base_url = self.http.get_base_url().rstrip('/')
        except AttributeError as exc:
            raise ValueError('HTTP client does not have get_base_url method') from exc

    def get_data_source(self) -> 'WorkplaceDataSource':
        """Return the data source instance."""
        return self

    def get_client(self) -> WorkplaceClient:
        """Return the underlying WorkplaceClient."""
        return self._client

    # -----------------------------------------------------------------------
    # Current User
    # -----------------------------------------------------------------------

    async def get_me(
        self,
        *,
        fields: str | None = None,
    ) -> WorkplaceResponse:
        """Get the current authenticated user

        Args:
            fields: Comma-separated list of fields to include

        Returns:
            WorkplaceResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if fields is not None:
            query_params['fields'] = fields

        url = self.base_url + "/me"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return WorkplaceResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_me" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return WorkplaceResponse(success=False, error=str(e), message="Failed to execute get_me")

    # -----------------------------------------------------------------------
    # Community Members
    # -----------------------------------------------------------------------

    async def get_community_members(
        self,
        *,
        limit: int | None = None,
        after: str | None = None,
        fields: str | None = None,
    ) -> WorkplaceResponse:
        """Get community members

        Args:
            limit: Maximum number of results per page
            after: Cursor for pagination (next page)
            fields: Comma-separated list of fields to include

        Returns:
            WorkplaceResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if limit is not None:
            query_params['limit'] = str(limit)
        if after is not None:
            query_params['after'] = after
        if fields is not None:
            query_params['fields'] = fields

        url = self.base_url + "/community/members"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return WorkplaceResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_community_members" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return WorkplaceResponse(success=False, error=str(e), message="Failed to execute get_community_members")

    # -----------------------------------------------------------------------
    # Users
    # -----------------------------------------------------------------------

    async def get_user(
        self,
        user_id: str,
        *,
        fields: str | None = None,
    ) -> WorkplaceResponse:
        """Get a specific user by ID

        Args:
            user_id: The user ID
            fields: Comma-separated list of fields to include

        Returns:
            WorkplaceResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if fields is not None:
            query_params['fields'] = fields

        url = self.base_url + "/{user_id}".format(user_id=user_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return WorkplaceResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_user" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return WorkplaceResponse(success=False, error=str(e), message="Failed to execute get_user")

    async def get_user_feed(
        self,
        user_id: str,
        *,
        limit: int | None = None,
        after: str | None = None,
        fields: str | None = None,
    ) -> WorkplaceResponse:
        """Get a user's feed

        Args:
            user_id: The user ID
            limit: Maximum number of results per page
            after: Cursor for pagination (next page)
            fields: Comma-separated list of fields to include

        Returns:
            WorkplaceResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if limit is not None:
            query_params['limit'] = str(limit)
        if after is not None:
            query_params['after'] = after
        if fields is not None:
            query_params['fields'] = fields

        url = self.base_url + "/{user_id}/feed".format(user_id=user_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return WorkplaceResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_user_feed" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return WorkplaceResponse(success=False, error=str(e), message="Failed to execute get_user_feed")

    # -----------------------------------------------------------------------
    # Community Groups
    # -----------------------------------------------------------------------

    async def get_community_groups(
        self,
        *,
        limit: int | None = None,
        after: str | None = None,
        fields: str | None = None,
    ) -> WorkplaceResponse:
        """Get community groups

        Args:
            limit: Maximum number of results per page
            after: Cursor for pagination (next page)
            fields: Comma-separated list of fields to include

        Returns:
            WorkplaceResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if limit is not None:
            query_params['limit'] = str(limit)
        if after is not None:
            query_params['after'] = after
        if fields is not None:
            query_params['fields'] = fields

        url = self.base_url + "/community/groups"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return WorkplaceResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_community_groups" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return WorkplaceResponse(success=False, error=str(e), message="Failed to execute get_community_groups")

    # -----------------------------------------------------------------------
    # Groups
    # -----------------------------------------------------------------------

    async def get_group(
        self,
        group_id: str,
        *,
        fields: str | None = None,
    ) -> WorkplaceResponse:
        """Get a specific group by ID

        Args:
            group_id: The group ID
            fields: Comma-separated list of fields to include

        Returns:
            WorkplaceResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if fields is not None:
            query_params['fields'] = fields

        url = self.base_url + "/{group_id}".format(group_id=group_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return WorkplaceResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_group" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return WorkplaceResponse(success=False, error=str(e), message="Failed to execute get_group")

    async def get_group_feed(
        self,
        group_id: str,
        *,
        limit: int | None = None,
        after: str | None = None,
        fields: str | None = None,
    ) -> WorkplaceResponse:
        """Get a group's feed

        Args:
            group_id: The group ID
            limit: Maximum number of results per page
            after: Cursor for pagination (next page)
            fields: Comma-separated list of fields to include

        Returns:
            WorkplaceResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if limit is not None:
            query_params['limit'] = str(limit)
        if after is not None:
            query_params['after'] = after
        if fields is not None:
            query_params['fields'] = fields

        url = self.base_url + "/{group_id}/feed".format(group_id=group_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return WorkplaceResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_group_feed" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return WorkplaceResponse(success=False, error=str(e), message="Failed to execute get_group_feed")

    async def get_group_members(
        self,
        group_id: str,
        *,
        limit: int | None = None,
        after: str | None = None,
        fields: str | None = None,
    ) -> WorkplaceResponse:
        """Get members of a group

        Args:
            group_id: The group ID
            limit: Maximum number of results per page
            after: Cursor for pagination (next page)
            fields: Comma-separated list of fields to include

        Returns:
            WorkplaceResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if limit is not None:
            query_params['limit'] = str(limit)
        if after is not None:
            query_params['after'] = after
        if fields is not None:
            query_params['fields'] = fields

        url = self.base_url + "/{group_id}/members".format(group_id=group_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return WorkplaceResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_group_members" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return WorkplaceResponse(success=False, error=str(e), message="Failed to execute get_group_members")

    # -----------------------------------------------------------------------
    # Posts
    # -----------------------------------------------------------------------

    async def get_post(
        self,
        post_id: str,
        *,
        fields: str | None = None,
    ) -> WorkplaceResponse:
        """Get a specific post by ID

        Args:
            post_id: The post ID
            fields: Comma-separated list of fields to include

        Returns:
            WorkplaceResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if fields is not None:
            query_params['fields'] = fields

        url = self.base_url + "/{post_id}".format(post_id=post_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return WorkplaceResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_post" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return WorkplaceResponse(success=False, error=str(e), message="Failed to execute get_post")

    async def get_post_comments(
        self,
        post_id: str,
        *,
        limit: int | None = None,
        after: str | None = None,
        fields: str | None = None,
    ) -> WorkplaceResponse:
        """Get comments on a specific post

        Args:
            post_id: The post ID
            limit: Maximum number of results per page
            after: Cursor for pagination (next page)
            fields: Comma-separated list of fields to include

        Returns:
            WorkplaceResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if limit is not None:
            query_params['limit'] = str(limit)
        if after is not None:
            query_params['after'] = after
        if fields is not None:
            query_params['fields'] = fields

        url = self.base_url + "/{post_id}/comments".format(post_id=post_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return WorkplaceResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_post_comments" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return WorkplaceResponse(success=False, error=str(e), message="Failed to execute get_post_comments")

    # -----------------------------------------------------------------------
    # Community Feeds
    # -----------------------------------------------------------------------

    async def get_community_feeds(
        self,
        *,
        limit: int | None = None,
        after: str | None = None,
        fields: str | None = None,
    ) -> WorkplaceResponse:
        """Get community feeds

        Args:
            limit: Maximum number of results per page
            after: Cursor for pagination (next page)
            fields: Comma-separated list of fields to include

        Returns:
            WorkplaceResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if limit is not None:
            query_params['limit'] = str(limit)
        if after is not None:
            query_params['after'] = after
        if fields is not None:
            query_params['fields'] = fields

        url = self.base_url + "/community/feeds"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return WorkplaceResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_community_feeds" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return WorkplaceResponse(success=False, error=str(e), message="Failed to execute get_community_feeds")
