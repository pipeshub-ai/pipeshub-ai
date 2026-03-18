# ruff: noqa
"""
Looker Studio (Google Data Studio) REST API DataSource - Auto-generated API wrapper

Generated from Looker Studio REST API v1 documentation.
Uses HTTP client for direct REST API interactions.
All methods have explicit parameter signatures.
"""

from __future__ import annotations

from typing import Any

from app.sources.client.lookerstudio.lookerstudio import LookerStudioClient, LookerStudioResponse
from app.sources.client.http.http_request import HTTPRequest

# HTTP status code constant
HTTP_ERROR_THRESHOLD = 400


class LookerStudioDataSource:
    """Looker Studio REST API DataSource

    Provides async wrapper methods for Looker Studio REST API operations:
    - Asset search and retrieval
    - Report management
    - Data source management
    - Permissions

    The base URL is https://datastudio.googleapis.com/v1

    All methods return LookerStudioResponse objects.
    """

    def __init__(self, client: LookerStudioClient) -> None:
        """Initialize with LookerStudioClient.

        Args:
            client: LookerStudioClient instance with configured authentication
        """
        self._client = client
        self.http = client.get_client()
        try:
            self.base_url = self.http.get_base_url().rstrip('/')
        except AttributeError as exc:
            raise ValueError('HTTP client does not have get_base_url method') from exc

    def get_data_source(self) -> 'LookerStudioDataSource':
        """Return the data source instance."""
        return self

    def get_client(self) -> LookerStudioClient:
        """Return the underlying LookerStudioClient."""
        return self._client

    async def search_assets(
        self,
        *,
        title: str | None = None,
        page_size: int | None = None,
        page_token: str | None = None,
        asset_types: str | None = None,
    ) -> LookerStudioResponse:
        """Search assets (GET /assets:search)

        Args:
            title: Filter by asset title
            page_size: Maximum number of results per page
            page_token: Token for pagination
            asset_types: Filter by asset types (e.g. REPORT, DATA_SOURCE)

        Returns:
            LookerStudioResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if title is not None:
            query_params['title'] = title
        if page_size is not None:
            query_params['pageSize'] = str(page_size)
        if page_token is not None:
            query_params['pageToken'] = page_token
        if asset_types is not None:
            query_params['assetTypes'] = asset_types

        url = self.base_url + "/assets:search"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return LookerStudioResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed search_assets" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return LookerStudioResponse(success=False, error=str(e), message="Failed to execute search_assets")

    async def get_asset(
        self,
        asset_id: str,
    ) -> LookerStudioResponse:
        """Get a specific asset (GET /assets/{assetId})

        Args:
            asset_id: The asset ID

        Returns:
            LookerStudioResponse with operation result
        """
        url = self.base_url + "/assets/{asset_id}".format(asset_id=asset_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return LookerStudioResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_asset" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return LookerStudioResponse(success=False, error=str(e), message="Failed to execute get_asset")

    async def get_asset_permissions(
        self,
        asset_id: str,
    ) -> LookerStudioResponse:
        """Get permissions for an asset (GET /assets/{assetId}/permissions)

        Args:
            asset_id: The asset ID

        Returns:
            LookerStudioResponse with operation result
        """
        url = self.base_url + "/assets/{asset_id}/permissions".format(asset_id=asset_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return LookerStudioResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_asset_permissions" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return LookerStudioResponse(success=False, error=str(e), message="Failed to execute get_asset_permissions")

    async def list_reports(
        self,
    ) -> LookerStudioResponse:
        """List all reports (GET /reports)

        Returns:
            LookerStudioResponse with operation result
        """
        url = self.base_url + "/reports"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return LookerStudioResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_reports" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return LookerStudioResponse(success=False, error=str(e), message="Failed to execute list_reports")

    async def get_report(
        self,
        report_id: str,
    ) -> LookerStudioResponse:
        """Get a specific report (GET /reports/{reportId})

        Args:
            report_id: The report ID

        Returns:
            LookerStudioResponse with operation result
        """
        url = self.base_url + "/reports/{report_id}".format(report_id=report_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return LookerStudioResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_report" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return LookerStudioResponse(success=False, error=str(e), message="Failed to execute get_report")

    async def list_data_sources(
        self,
    ) -> LookerStudioResponse:
        """List all data sources (GET /dataSources)

        Returns:
            LookerStudioResponse with operation result
        """
        url = self.base_url + "/dataSources"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return LookerStudioResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_data_sources" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return LookerStudioResponse(success=False, error=str(e), message="Failed to execute list_data_sources")

    async def get_data_source_detail(
        self,
        data_source_id: str,
    ) -> LookerStudioResponse:
        """Get a specific data source (GET /dataSources/{dataSourceId})

        Args:
            data_source_id: The data source ID

        Returns:
            LookerStudioResponse with operation result
        """
        url = self.base_url + "/dataSources/{data_source_id}".format(data_source_id=data_source_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return LookerStudioResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_data_source_detail" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return LookerStudioResponse(success=False, error=str(e), message="Failed to execute get_data_source_detail")
