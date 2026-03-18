# ruff: noqa
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownParameterType=false
"""
Bynder SDK DataSource - Auto-generated SDK wrapper

Generated from Bynder SDK method specifications.
Wraps the official bynder-sdk Python package.
All methods have explicit parameter signatures.
"""

from __future__ import annotations

from typing import Union, cast

from bynder_sdk import BynderClient as BynderSDKClient

from app.sources.client.bynder.bynder import BynderClient, BynderResponse


class BynderDataSource:
    """Bynder SDK DataSource

    Provides typed wrapper methods for Bynder SDK operations:
    - Media asset management
    - Collections management
    - Tags management
    - Metaproperties management
    - Brands management
    - Account users management
    - Smartfilters

    All methods return BynderResponse objects.
    """

    def __init__(self, client_or_sdk: Union[BynderClient, BynderSDKClient, object]) -> None:
        """Initialize with BynderClient, raw SDK, or any wrapper with ``get_sdk()``.

        Args:
            client_or_sdk: BynderClient, BynderSDKClient instance, or wrapper
        """
        if isinstance(client_or_sdk, BynderSDKClient):
            self._sdk: BynderSDKClient = client_or_sdk
        elif hasattr(client_or_sdk, "get_sdk"):
            sdk_obj = getattr(client_or_sdk, "get_sdk")()
            self._sdk = cast(BynderSDKClient, sdk_obj)
        else:
            self._sdk = cast(BynderSDKClient, client_or_sdk)

        self._asset_bank = self._sdk.asset_bank_client
        self._collection_client = self._sdk.collection_client

    # -----------------------------------------------------------------------
    # Media
    # -----------------------------------------------------------------------

    def get_media_list(
        self,
        *,
        limit: int | None = None,
        page: int | None = None,
        keyword: str | None = None,
        type: str | None = None,
    ) -> BynderResponse:
        """List media assets.

        Args:
            limit: Maximum number of results
            page: Page number for pagination
            keyword: Filter by keyword
            type: Filter by media type

        Returns:
            BynderResponse with operation result
        """
        try:
            query: dict[str, object] = {}
            if limit is not None:
                query['limit'] = limit
            if page is not None:
                query['page'] = page
            if keyword is not None:
                query['keyword'] = keyword
            if type is not None:
                query['type'] = type
            result = self._asset_bank.media_list(query)
            return BynderResponse(success=True, data=result)
        except Exception as e:
            return BynderResponse(
                success=False, error=str(e), message="Failed to execute get_media_list"
            )


    def get_media(
        self,
        media_id: str,
    ) -> BynderResponse:
        """Get a specific media asset by ID.

        Args:
            media_id: The media asset ID

        Returns:
            BynderResponse with operation result
        """
        try:
            result = self._asset_bank.media_info(media_id)
            return BynderResponse(success=True, data=result)
        except Exception as e:
            return BynderResponse(
                success=False, error=str(e), message="Failed to execute get_media"
            )


    def get_media_download_url(
        self,
        media_id: str,
    ) -> BynderResponse:
        """Get the download URL for a media asset.

        Args:
            media_id: The media asset ID

        Returns:
            BynderResponse with operation result
        """
        try:
            result = self._asset_bank.media_download_url(media_id)
            return BynderResponse(success=True, data=result)
        except Exception as e:
            return BynderResponse(
                success=False, error=str(e), message="Failed to execute get_media_download_url"
            )


    # -----------------------------------------------------------------------
    # Collections
    # -----------------------------------------------------------------------

    def get_collections(
        self,
        *,
        limit: int | None = None,
        page: int | None = None,
    ) -> BynderResponse:
        """List all collections.

        Args:
            limit: Maximum number of results
            page: Page number for pagination

        Returns:
            BynderResponse with operation result
        """
        try:
            query: dict[str, object] = {}
            if limit is not None:
                query['limit'] = limit
            if page is not None:
                query['page'] = page
            result = self._collection_client.collections(query)
            return BynderResponse(success=True, data=result)
        except Exception as e:
            return BynderResponse(
                success=False, error=str(e), message="Failed to execute get_collections"
            )


    def get_collection(
        self,
        collection_id: str,
    ) -> BynderResponse:
        """Get a specific collection by ID.

        Args:
            collection_id: The collection ID

        Returns:
            BynderResponse with operation result
        """
        try:
            result = self._collection_client.collection_info(collection_id)
            return BynderResponse(success=True, data=result)
        except Exception as e:
            return BynderResponse(
                success=False, error=str(e), message="Failed to execute get_collection"
            )


    # -----------------------------------------------------------------------
    # Tags
    # -----------------------------------------------------------------------

    def get_tags(
        self,
    ) -> BynderResponse:
        """List all tags.
        Returns:
            BynderResponse with operation result
        """
        try:
            result = self._asset_bank.tags()
            return BynderResponse(success=True, data=result)
        except Exception as e:
            return BynderResponse(
                success=False, error=str(e), message="Failed to execute get_tags"
            )


    # -----------------------------------------------------------------------
    # Metaproperties
    # -----------------------------------------------------------------------

    def get_metaproperties(
        self,
    ) -> BynderResponse:
        """List all metaproperties.
        Returns:
            BynderResponse with operation result
        """
        try:
            result = self._asset_bank.meta_properties()
            return BynderResponse(success=True, data=result)
        except Exception as e:
            return BynderResponse(
                success=False, error=str(e), message="Failed to execute get_metaproperties"
            )


    def get_metaproperty(
        self,
        metaproperty_id: str,
    ) -> BynderResponse:
        """Get a specific metaproperty by ID.

        Args:
            metaproperty_id: The metaproperty ID

        Returns:
            BynderResponse with operation result
        """
        try:
            result = self._asset_bank.meta_property_info(metaproperty_id)
            return BynderResponse(success=True, data=result)
        except Exception as e:
            return BynderResponse(
                success=False, error=str(e), message="Failed to execute get_metaproperty"
            )


    # -----------------------------------------------------------------------
    # Brands
    # -----------------------------------------------------------------------

    def get_brands(
        self,
    ) -> BynderResponse:
        """List all brands.
        Returns:
            BynderResponse with operation result
        """
        try:
            result = self._asset_bank.brands()
            return BynderResponse(success=True, data=result)
        except Exception as e:
            return BynderResponse(
                success=False, error=str(e), message="Failed to execute get_brands"
            )


    # -----------------------------------------------------------------------
    # Account Users
    # -----------------------------------------------------------------------

    def get_account_users(
        self,
    ) -> BynderResponse:
        """List all account users.
        Returns:
            BynderResponse with operation result
        """
        try:
            result = self._asset_bank.users()
            return BynderResponse(success=True, data=result)
        except Exception as e:
            return BynderResponse(
                success=False, error=str(e), message="Failed to execute get_account_users"
            )


    # -----------------------------------------------------------------------
    # Smartfilters
    # -----------------------------------------------------------------------

    def get_smartfilters(
        self,
    ) -> BynderResponse:
        """List all smartfilters.
        Returns:
            BynderResponse with operation result
        """
        try:
            result = self._asset_bank.smartfilters()
            return BynderResponse(success=True, data=result)
        except Exception as e:
            return BynderResponse(
                success=False, error=str(e), message="Failed to execute get_smartfilters"
            )

