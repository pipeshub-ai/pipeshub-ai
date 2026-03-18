"""
Canva Connect REST API DataSource - Auto-generated API wrapper

Generated from Canva Connect REST API v1 documentation.
Uses HTTP client for direct REST API interactions.
All methods have explicit parameter signatures.
"""

from __future__ import annotations

from typing import Any

from app.sources.client.canva.canva import CanvaClient, CanvaResponse
from app.sources.client.http.http_request import HTTPRequest

# HTTP status code constant
HTTP_ERROR_THRESHOLD = 400


class CanvaDataSource:
    """Canva Connect REST API DataSource

    Provides async wrapper methods for Canva Connect REST API operations:
    - User profile
    - Designs (list, get, create)
    - Folders (list, get, create, items)
    - Brand templates (list, get)
    - Assets (list, upload)
    - Comments (list, create)
    - Exports (create, get status)

    The base URL is determined by the CanvaClient's configured base URL.
    All methods return CanvaResponse objects.
    """

    def __init__(self, client: CanvaClient) -> None:
        """Initialize with CanvaClient.

        Args:
            client: CanvaClient instance with configured authentication
        """
        self._client = client
        self.http = client.get_client()
        try:
            self.base_url = self.http.get_base_url().rstrip('/')
        except AttributeError as exc:
            raise ValueError('HTTP client does not have get_base_url method') from exc

    def get_data_source(self) -> 'CanvaDataSource':
        """Return the data source instance."""
        return self

    def get_client(self) -> CanvaClient:
        """Return the underlying CanvaClient."""
        return self._client

    async def get_current_user(
        self
    ) -> CanvaResponse:
        """Get the profile of the currently authenticated user

        Returns:
            CanvaResponse with operation result
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
            return CanvaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_current_user" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return CanvaResponse(success=False, error=str(e), message="Failed to execute get_current_user")

    async def list_designs(
        self,
        ownership: str | None = None,
        sort_by: str | None = None,
        limit: int | None = None,
        continuation: str | None = None
    ) -> CanvaResponse:
        """List designs accessible by the authenticated user

        Args:
            ownership: Filter by ownership (owned, shared, any)
            sort_by: Sort field (relevance, modified_descending, modified_ascending, title_descending, title_ascending)
            limit: Maximum number of results to return
            continuation: Continuation token for pagination

        Returns:
            CanvaResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if ownership is not None:
            query_params['ownership'] = ownership
        if sort_by is not None:
            query_params['sort_by'] = sort_by
        if limit is not None:
            query_params['limit'] = str(limit)
        if continuation is not None:
            query_params['continuation'] = continuation

        url = self.base_url + "/designs"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return CanvaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_designs" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return CanvaResponse(success=False, error=str(e), message="Failed to execute list_designs")

    async def get_design(
        self,
        design_id: str
    ) -> CanvaResponse:
        """Get metadata for a specific design

        Args:
            design_id: The design ID

        Returns:
            CanvaResponse with operation result
        """
        url = self.base_url + "/designs/{design_id}".format(design_id=design_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return CanvaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_design" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return CanvaResponse(success=False, error=str(e), message="Failed to execute get_design")

    async def create_design(
        self,
        design_type: str | None = None,
        title: str | None = None,
        width: int | None = None,
        height: int | None = None,
        asset_id: str | None = None
    ) -> CanvaResponse:
        """Create a new Canva design

        Args:
            design_type: Type of design to create
            title: Title for the new design
            width: Width of the design in pixels
            height: Height of the design in pixels
            asset_id: Asset ID to use as design content

        Returns:
            CanvaResponse with operation result
        """
        url = self.base_url + "/designs"

        body: dict[str, Any] = {}
        if design_type is not None:
            body['design_type'] = design_type
        if title is not None:
            body['title'] = title
        if width is not None:
            body['width'] = width
        if height is not None:
            body['height'] = height
        if asset_id is not None:
            body['asset_id'] = asset_id

        try:
            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return CanvaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed create_design" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return CanvaResponse(success=False, error=str(e), message="Failed to execute create_design")

    async def list_folders(
        self,
        sort_by: str | None = None,
        limit: int | None = None,
        continuation: str | None = None
    ) -> CanvaResponse:
        """List folders accessible by the authenticated user

        Args:
            sort_by: Sort field (relevance, modified_descending, modified_ascending, title_descending, title_ascending)
            limit: Maximum number of results to return
            continuation: Continuation token for pagination

        Returns:
            CanvaResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if sort_by is not None:
            query_params['sort_by'] = sort_by
        if limit is not None:
            query_params['limit'] = str(limit)
        if continuation is not None:
            query_params['continuation'] = continuation

        url = self.base_url + "/folders"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return CanvaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_folders" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return CanvaResponse(success=False, error=str(e), message="Failed to execute list_folders")

    async def get_folder(
        self,
        folder_id: str
    ) -> CanvaResponse:
        """Get metadata for a specific folder

        Args:
            folder_id: The folder ID

        Returns:
            CanvaResponse with operation result
        """
        url = self.base_url + "/folders/{folder_id}".format(folder_id=folder_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return CanvaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_folder" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return CanvaResponse(success=False, error=str(e), message="Failed to execute get_folder")

    async def list_folder_items(
        self,
        folder_id: str,
        item_types: str | None = None,
        sort_by: str | None = None,
        limit: int | None = None,
        continuation: str | None = None
    ) -> CanvaResponse:
        """List items within a specific folder

        Args:
            folder_id: The folder ID
            item_types: Filter by item type (design, folder, image)
            sort_by: Sort field (relevance, modified_descending, modified_ascending, title_descending, title_ascending)
            limit: Maximum number of results to return
            continuation: Continuation token for pagination

        Returns:
            CanvaResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if item_types is not None:
            query_params['item_types'] = item_types
        if sort_by is not None:
            query_params['sort_by'] = sort_by
        if limit is not None:
            query_params['limit'] = str(limit)
        if continuation is not None:
            query_params['continuation'] = continuation

        url = self.base_url + "/folders/{folder_id}/items".format(folder_id=folder_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return CanvaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_folder_items" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return CanvaResponse(success=False, error=str(e), message="Failed to execute list_folder_items")

    async def create_folder(
        self,
        name: str,
        parent_folder_id: str | None = None
    ) -> CanvaResponse:
        """Create a new folder

        Args:
            name: Name of the folder
            parent_folder_id: ID of the parent folder

        Returns:
            CanvaResponse with operation result
        """
        url = self.base_url + "/folders"

        body: dict[str, Any] = {}
        body['name'] = name
        if parent_folder_id is not None:
            body['parent_folder_id'] = parent_folder_id

        try:
            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return CanvaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed create_folder" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return CanvaResponse(success=False, error=str(e), message="Failed to execute create_folder")

    async def list_brand_templates(
        self,
        dataset: str | None = None,
        ownership: str | None = None,
        sort_by: str | None = None,
        limit: int | None = None,
        continuation: str | None = None
    ) -> CanvaResponse:
        """List brand templates accessible by the authenticated user

        Args:
            dataset: Filter by dataset
            ownership: Filter by ownership (owned, shared, any)
            sort_by: Sort field
            limit: Maximum number of results to return
            continuation: Continuation token for pagination

        Returns:
            CanvaResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if dataset is not None:
            query_params['dataset'] = dataset
        if ownership is not None:
            query_params['ownership'] = ownership
        if sort_by is not None:
            query_params['sort_by'] = sort_by
        if limit is not None:
            query_params['limit'] = str(limit)
        if continuation is not None:
            query_params['continuation'] = continuation

        url = self.base_url + "/brand-templates"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return CanvaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_brand_templates" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return CanvaResponse(success=False, error=str(e), message="Failed to execute list_brand_templates")

    async def get_brand_template(
        self,
        brand_template_id: str
    ) -> CanvaResponse:
        """Get metadata for a specific brand template

        Args:
            brand_template_id: The brand template ID

        Returns:
            CanvaResponse with operation result
        """
        url = self.base_url + "/brand-templates/{brand_template_id}".format(brand_template_id=brand_template_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return CanvaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_brand_template" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return CanvaResponse(success=False, error=str(e), message="Failed to execute get_brand_template")

    async def list_assets(
        self,
        sort_by: str | None = None,
        limit: int | None = None,
        continuation: str | None = None
    ) -> CanvaResponse:
        """List assets accessible by the authenticated user

        Args:
            sort_by: Sort field
            limit: Maximum number of results to return
            continuation: Continuation token for pagination

        Returns:
            CanvaResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if sort_by is not None:
            query_params['sort_by'] = sort_by
        if limit is not None:
            query_params['limit'] = str(limit)
        if continuation is not None:
            query_params['continuation'] = continuation

        url = self.base_url + "/assets"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return CanvaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_assets" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return CanvaResponse(success=False, error=str(e), message="Failed to execute list_assets")

    async def upload_asset(
        self,
        name: str,
        folder_id: str | None = None
    ) -> CanvaResponse:
        """Upload an asset to Canva (multipart upload)

        Args:
            name: Name of the asset
            folder_id: Target folder ID for the asset

        Returns:
            CanvaResponse with operation result
        """
        url = self.base_url + "/assets/upload"

        body: dict[str, Any] = {}
        body['name'] = name
        if folder_id is not None:
            body['folder_id'] = folder_id

        try:
            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return CanvaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed upload_asset" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return CanvaResponse(success=False, error=str(e), message="Failed to execute upload_asset")

    async def list_design_comments(
        self,
        design_id: str,
        limit: int | None = None,
        continuation: str | None = None
    ) -> CanvaResponse:
        """List comments on a specific design

        Args:
            design_id: The design ID
            limit: Maximum number of results to return
            continuation: Continuation token for pagination

        Returns:
            CanvaResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if limit is not None:
            query_params['limit'] = str(limit)
        if continuation is not None:
            query_params['continuation'] = continuation

        url = self.base_url + "/comments/{design_id}".format(design_id=design_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return CanvaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_design_comments" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return CanvaResponse(success=False, error=str(e), message="Failed to execute list_design_comments")

    async def create_design_comment(
        self,
        design_id: str,
        message: str
    ) -> CanvaResponse:
        """Create a comment on a specific design

        Args:
            design_id: The design ID
            message: The comment message text

        Returns:
            CanvaResponse with operation result
        """
        url = self.base_url + "/comments/{design_id}".format(design_id=design_id)

        body: dict[str, Any] = {}
        body['message'] = message

        try:
            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return CanvaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed create_design_comment" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return CanvaResponse(success=False, error=str(e), message="Failed to execute create_design_comment")

    async def create_export(
        self,
        design_id: str,
        export_format: str | None = None,
        quality: str | None = None,
        pages: list[int] | None = None,
        width: int | None = None,
        height: int | None = None
    ) -> CanvaResponse:
        """Create an export job to export a design

        Args:
            design_id: The design ID to export
            export_format: Export format (pdf, jpg, png, gif, pptx, mp4)
            quality: Export quality (regular, pro)
            pages: List of page indices to export
            width: Target width in pixels
            height: Target height in pixels

        Returns:
            CanvaResponse with operation result
        """
        url = self.base_url + "/exports"

        body: dict[str, Any] = {}
        body['design_id'] = design_id
        if export_format is not None:
            body['format'] = export_format
        if quality is not None:
            body['quality'] = quality
        if pages is not None:
            body['pages'] = pages
        if width is not None:
            body['width'] = width
        if height is not None:
            body['height'] = height

        try:
            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return CanvaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed create_export" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return CanvaResponse(success=False, error=str(e), message="Failed to execute create_export")

    async def get_export(
        self,
        export_id: str
    ) -> CanvaResponse:
        """Get the status and result of an export job

        Args:
            export_id: The export job ID

        Returns:
            CanvaResponse with operation result
        """
        url = self.base_url + "/exports/{export_id}".format(export_id=export_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return CanvaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_export" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return CanvaResponse(success=False, error=str(e), message="Failed to execute get_export")
