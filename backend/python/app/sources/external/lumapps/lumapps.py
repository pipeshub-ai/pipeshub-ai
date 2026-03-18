# ruff: noqa
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownParameterType=false
"""
LumApps SDK DataSource - Auto-generated SDK wrapper

Generated from LumApps SDK method specifications.
Wraps the official lumapps-sdk Python package.
All methods have explicit parameter signatures.
"""

from __future__ import annotations

from typing import Union, cast

from lumapps.api import BaseClient

from app.sources.client.lumapps.lumapps import LumAppsClient, LumAppsResponse


class LumAppsDataSource:
    """LumApps SDK DataSource

    Provides typed wrapper methods for LumApps SDK operations:
    - Users management
    - Communities management
    - Content management
    - Feeds management
    - Search
    - Directories management
    - Spaces management

    All methods return LumAppsResponse objects.
    """

    def __init__(self, client_or_sdk: Union[LumAppsClient, BaseClient, object]) -> None:
        """Initialize with LumAppsClient, raw SDK, or any wrapper with ``get_sdk()``.

        Args:
            client_or_sdk: LumAppsClient, BaseClient instance, or wrapper
        """
        if isinstance(client_or_sdk, BaseClient):
            self._sdk: BaseClient = client_or_sdk
        elif hasattr(client_or_sdk, "get_sdk"):
            sdk_obj = getattr(client_or_sdk, "get_sdk")()
            self._sdk = cast(BaseClient, sdk_obj)
        else:
            self._sdk = cast(BaseClient, client_or_sdk)

    # -----------------------------------------------------------------------
    # Users
    # -----------------------------------------------------------------------

    def list_users(
        self,
    ) -> LumAppsResponse:
        """List all users.
        Returns:
            LumAppsResponse with operation result
        """
        try:
            result = self._sdk.get_call("user/list")
            return LumAppsResponse(success=True, data=result)
        except Exception as e:
            return LumAppsResponse(
                success=False, error=str(e), message="Failed to execute list_users"
            )


    def get_user(
        self,
        email: str,
    ) -> LumAppsResponse:
        """Get a specific user by email.

        Args:
            email: The user email address

        Returns:
            LumAppsResponse with operation result
        """
        try:
            result = self._sdk.get_call("user/get", email=email)
            return LumAppsResponse(success=True, data=result)
        except Exception as e:
            return LumAppsResponse(
                success=False, error=str(e), message="Failed to execute get_user"
            )


    def get_user_by_id(
        self,
        user_id: str,
    ) -> LumAppsResponse:
        """Get a specific user by ID.

        Args:
            user_id: The user ID

        Returns:
            LumAppsResponse with operation result
        """
        try:
            result = self._sdk.get_call("user/get", uid=user_id)
            return LumAppsResponse(success=True, data=result)
        except Exception as e:
            return LumAppsResponse(
                success=False, error=str(e), message="Failed to execute get_user_by_id"
            )


    # -----------------------------------------------------------------------
    # Communities
    # -----------------------------------------------------------------------

    def list_communities(
        self,
    ) -> LumAppsResponse:
        """List all communities.
        Returns:
            LumAppsResponse with operation result
        """
        try:
            result = self._sdk.get_call("community/list")
            return LumAppsResponse(success=True, data=result)
        except Exception as e:
            return LumAppsResponse(
                success=False, error=str(e), message="Failed to execute list_communities"
            )


    def get_community(
        self,
        community_id: str,
    ) -> LumAppsResponse:
        """Get a specific community by ID.

        Args:
            community_id: The community ID

        Returns:
            LumAppsResponse with operation result
        """
        try:
            result = self._sdk.get_call("community/get", uid=community_id)
            return LumAppsResponse(success=True, data=result)
        except Exception as e:
            return LumAppsResponse(
                success=False, error=str(e), message="Failed to execute get_community"
            )


    # -----------------------------------------------------------------------
    # Content
    # -----------------------------------------------------------------------

    def list_content(
        self,
    ) -> LumAppsResponse:
        """List all content items.
        Returns:
            LumAppsResponse with operation result
        """
        try:
            result = self._sdk.get_call("content/list")
            return LumAppsResponse(success=True, data=result)
        except Exception as e:
            return LumAppsResponse(
                success=False, error=str(e), message="Failed to execute list_content"
            )


    def get_content(
        self,
        content_id: str,
    ) -> LumAppsResponse:
        """Get a specific content item by ID.

        Args:
            content_id: The content ID

        Returns:
            LumAppsResponse with operation result
        """
        try:
            result = self._sdk.get_call("content/get", uid=content_id)
            return LumAppsResponse(success=True, data=result)
        except Exception as e:
            return LumAppsResponse(
                success=False, error=str(e), message="Failed to execute get_content"
            )


    # -----------------------------------------------------------------------
    # Feeds
    # -----------------------------------------------------------------------

    def list_feeds(
        self,
    ) -> LumAppsResponse:
        """List all feeds.
        Returns:
            LumAppsResponse with operation result
        """
        try:
            result = self._sdk.get_call("feed/list")
            return LumAppsResponse(success=True, data=result)
        except Exception as e:
            return LumAppsResponse(
                success=False, error=str(e), message="Failed to execute list_feeds"
            )


    def get_feed(
        self,
        feed_id: str,
    ) -> LumAppsResponse:
        """Get a specific feed by ID.

        Args:
            feed_id: The feed ID

        Returns:
            LumAppsResponse with operation result
        """
        try:
            result = self._sdk.get_call("feed/get", uid=feed_id)
            return LumAppsResponse(success=True, data=result)
        except Exception as e:
            return LumAppsResponse(
                success=False, error=str(e), message="Failed to execute get_feed"
            )


    # -----------------------------------------------------------------------
    # Search
    # -----------------------------------------------------------------------

    def search(
        self,
        query: str,
        *,
        content_types: list[str] | None = None,
        limit: int | None = None,
    ) -> LumAppsResponse:
        """Search across LumApps content.

        Args:
            query: Search query string
            content_types: Content type filters
            limit: Maximum number of results

        Returns:
            LumAppsResponse with operation result
        """
        try:
            body: dict[str, object] = {"query": query}
            if content_types is not None:
                body["contentTypes"] = content_types
            if limit is not None:
                body["limit"] = limit
            result = self._sdk.get_call("search", body=body)
            return LumAppsResponse(success=True, data=result)
        except Exception as e:
            return LumAppsResponse(
                success=False, error=str(e), message="Failed to execute search"
            )


    # -----------------------------------------------------------------------
    # Directories
    # -----------------------------------------------------------------------

    def list_directories(
        self,
    ) -> LumAppsResponse:
        """List all directories.
        Returns:
            LumAppsResponse with operation result
        """
        try:
            result = self._sdk.get_call("directory/list")
            return LumAppsResponse(success=True, data=result)
        except Exception as e:
            return LumAppsResponse(
                success=False, error=str(e), message="Failed to execute list_directories"
            )


    def get_directory(
        self,
        directory_id: str,
    ) -> LumAppsResponse:
        """Get a specific directory by ID.

        Args:
            directory_id: The directory ID

        Returns:
            LumAppsResponse with operation result
        """
        try:
            result = self._sdk.get_call("directory/get", uid=directory_id)
            return LumAppsResponse(success=True, data=result)
        except Exception as e:
            return LumAppsResponse(
                success=False, error=str(e), message="Failed to execute get_directory"
            )


    # -----------------------------------------------------------------------
    # Spaces
    # -----------------------------------------------------------------------

    def list_spaces(
        self,
    ) -> LumAppsResponse:
        """List all spaces.
        Returns:
            LumAppsResponse with operation result
        """
        try:
            result = self._sdk.get_call("space/list")
            return LumAppsResponse(success=True, data=result)
        except Exception as e:
            return LumAppsResponse(
                success=False, error=str(e), message="Failed to execute list_spaces"
            )

