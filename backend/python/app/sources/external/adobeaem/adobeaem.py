# ruff: noqa
"""
Adobe Experience Manager (AEM as Cloud Service) REST API DataSource - Auto-generated API wrapper

Generated from AEM Assets HTTP API and QueryBuilder API documentation.
Uses HTTP client for direct REST API interactions.
All methods have explicit parameter signatures.
"""

from __future__ import annotations

from typing import Any

from app.sources.client.adobeaem.adobeaem import AdobeAEMClient, AdobeAEMResponse
from app.sources.client.http.http_request import HTTPRequest

# HTTP status code constant
HTTP_ERROR_THRESHOLD = 400


class AdobeAEMDataSource:
    """Adobe AEM REST API DataSource

    Provides async wrapper methods for AEM REST API operations:
    - Assets management (list, get)
    - DAM content browsing
    - User/authorizable search
    - QueryBuilder queries
    - Package management

    The base URL is https://{instance}.adobeaemcloud.com.

    All methods return AdobeAEMResponse objects.
    """

    def __init__(self, client: AdobeAEMClient) -> None:
        """Initialize with AdobeAEMClient.

        Args:
            client: AdobeAEMClient instance with configured authentication
        """
        self._client = client
        self.http = client.get_client()
        try:
            self.base_url = self.http.get_base_url().rstrip('/')
        except AttributeError as exc:
            raise ValueError('HTTP client does not have get_base_url method') from exc

    def get_data_source(self) -> 'AdobeAEMDataSource':
        """Return the data source instance."""
        return self

    def get_client(self) -> AdobeAEMClient:
        """Return the underlying AdobeAEMClient."""
        return self._client

    # -----------------------------------------------------------------------
    # Assets API
    # -----------------------------------------------------------------------

    async def list_assets(
        self,
        *,
        limit: int | None = None,
        start: int | None = None,
        orderby: str | None = None,
    ) -> AdobeAEMResponse:
        """List assets via the Assets HTTP API.

        Args:
            limit: Maximum number of assets to return
            start: Offset for pagination
            orderby: Field to order results by

        Returns:
            AdobeAEMResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if limit is not None:
            query_params['limit'] = str(limit)
        if start is not None:
            query_params['start'] = str(start)
        if orderby is not None:
            query_params['orderby'] = orderby

        url = self.base_url + "/api/assets.json"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return AdobeAEMResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_assets" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return AdobeAEMResponse(success=False, error=str(e), message="Failed to execute list_assets")

    async def get_asset(
        self,
        path: str,
    ) -> AdobeAEMResponse:
        """Get a specific asset by path.

        Args:
            path: The asset path (e.g., "my-folder/my-asset.png")

        Returns:
            AdobeAEMResponse with operation result
        """
        # Ensure path doesn't start with /
        clean_path = path.lstrip('/')
        url = self.base_url + "/api/assets/{path}.json".format(path=clean_path)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return AdobeAEMResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_asset" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return AdobeAEMResponse(success=False, error=str(e), message="Failed to execute get_asset")

    # -----------------------------------------------------------------------
    # DAM Content
    # -----------------------------------------------------------------------

    async def get_dam_content(
        self,
        *,
        limit: int | None = None,
        start: int | None = None,
    ) -> AdobeAEMResponse:
        """Browse DAM content.

        Args:
            limit: Maximum number of results to return
            start: Offset for pagination

        Returns:
            AdobeAEMResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if limit is not None:
            query_params['limit'] = str(limit)
        if start is not None:
            query_params['start'] = str(start)

        url = self.base_url + "/content/dam.json"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return AdobeAEMResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_dam_content" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return AdobeAEMResponse(success=False, error=str(e), message="Failed to execute get_dam_content")

    # -----------------------------------------------------------------------
    # Authorizables (Users/Groups)
    # -----------------------------------------------------------------------

    async def search_authorizables(
        self,
        query: str,
    ) -> AdobeAEMResponse:
        """Search for authorizables (users/groups).

        Args:
            query: Search query string

        Returns:
            AdobeAEMResponse with operation result
        """
        query_params: dict[str, Any] = {'query': query}

        url = self.base_url + "/libs/granite/security/search/authorizables.json"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return AdobeAEMResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed search_authorizables" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return AdobeAEMResponse(success=False, error=str(e), message="Failed to execute search_authorizables")

    # -----------------------------------------------------------------------
    # QueryBuilder
    # -----------------------------------------------------------------------

    async def query_builder(
        self,
        *,
        path: str | None = None,
        type: str | None = None,
        p_limit: int | None = None,
        orderby: str | None = None,
        fulltext: str | None = None,
    ) -> AdobeAEMResponse:
        """Execute a QueryBuilder query.

        Args:
            path: Content path to search under
            type: Node type to filter (e.g., "dam:Asset", "cq:Page")
            p_limit: Maximum number of results
            orderby: Sort field (e.g., "@jcr:content/jcr:lastModified")
            fulltext: Full-text search query

        Returns:
            AdobeAEMResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if path is not None:
            query_params['path'] = path
        if type is not None:
            query_params['type'] = type
        if p_limit is not None:
            query_params['p.limit'] = str(p_limit)
        if orderby is not None:
            query_params['orderby'] = orderby
        if fulltext is not None:
            query_params['fulltext'] = fulltext

        url = self.base_url + "/bin/querybuilder.json"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return AdobeAEMResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed query_builder" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return AdobeAEMResponse(success=False, error=str(e), message="Failed to execute query_builder")

    # -----------------------------------------------------------------------
    # Package Manager
    # -----------------------------------------------------------------------

    async def get_package_list(
        self,
    ) -> AdobeAEMResponse:
        """List packages via CRX Package Manager.

        Returns:
            AdobeAEMResponse with operation result
        """
        query_params: dict[str, Any] = {'cmd': 'ls'}

        url = self.base_url + "/crx/packmgr/service.jsp"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return AdobeAEMResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_package_list" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return AdobeAEMResponse(success=False, error=str(e), message="Failed to execute get_package_list")
