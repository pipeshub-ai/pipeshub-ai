"""
Figma REST API DataSource - Auto-generated API wrapper

Generated from Figma REST API v1 documentation.
Uses HTTP client for direct REST API interactions.
All methods have explicit parameter signatures.
"""

from __future__ import annotations

from typing import Any

from app.sources.client.figma.figma import FigmaClient, FigmaResponse
from app.sources.client.http.http_request import HTTPRequest

# HTTP status code constant
HTTP_ERROR_THRESHOLD = 400


class FigmaDataSource:
    """Figma REST API DataSource

    Provides async wrapper methods for Figma REST API operations:
    - User / Authentication
    - Files and File Nodes
    - Images
    - Comments
    - File Versions
    - Team Projects and Project Files
    - Components and Component Sets
    - Styles
    - Variables (Local and Published)
    - Webhooks
    - Activity Logs

    The base URL is https://api.figma.com/v1.

    All methods return FigmaResponse objects.
    """

    def __init__(self, client: FigmaClient) -> None:
        """Initialize with FigmaClient.

        Args:
            client: FigmaClient instance with configured authentication
        """
        self._client = client
        self.http = client.get_client()
        try:
            self.base_url = self.http.get_base_url().rstrip('/')
        except AttributeError as exc:
            raise ValueError('HTTP client does not have get_base_url method') from exc

    def get_data_source(self) -> 'FigmaDataSource':
        """Return the data source instance."""
        return self

    def get_client(self) -> FigmaClient:
        """Return the underlying FigmaClient."""
        return self._client

    async def get_current_user(
        self
    ) -> FigmaResponse:
        """Get the current authenticated user

        Returns:
            FigmaResponse with operation result
        """
        url = self.base_url + "/me"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return FigmaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_current_user" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return FigmaResponse(success=False, error=str(e), message="Failed to execute get_current_user")

    async def get_file(
        self,
        file_key: str,
        *,
        version: str | None = None,
        ids: str | None = None,
        depth: int | None = None,
        geometry: str | None = None,
        plugin_data: str | None = None,
        branch_data: bool | None = None
    ) -> FigmaResponse:
        """Get a Figma file by key

        Args:
            file_key: The file key (from the Figma file URL)
            version: A specific version ID to get
            ids: Comma-separated list of node IDs to retrieve
            depth: Positive integer representing how deep into the document tree to traverse
            geometry: Set to 'paths' to export vector data
            plugin_data: Comma-separated list of plugin IDs or 'shared' for shared plugin data
            branch_data: Returns branch metadata for the requested file

        Returns:
            FigmaResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if version is not None:
            query_params['version'] = version
        if ids is not None:
            query_params['ids'] = ids
        if depth is not None:
            query_params['depth'] = str(depth)
        if geometry is not None:
            query_params['geometry'] = geometry
        if plugin_data is not None:
            query_params['plugin_data'] = plugin_data
        if branch_data is not None:
            query_params['branch_data'] = str(branch_data).lower()

        url = self.base_url + "/files/{file_key}".format(file_key=file_key)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return FigmaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_file" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return FigmaResponse(success=False, error=str(e), message="Failed to execute get_file")

    async def get_file_nodes(
        self,
        file_key: str,
        ids: str,
        version: str | None = None,
        depth: int | None = None,
        geometry: str | None = None,
        plugin_data: str | None = None
    ) -> FigmaResponse:
        """Get specific nodes from a Figma file

        Args:
            file_key: The file key
            ids: Comma-separated list of node IDs to retrieve
            version: A specific version ID to get
            depth: Positive integer for document tree depth
            geometry: Set to 'paths' to export vector data
            plugin_data: Comma-separated list of plugin IDs or 'shared'

        Returns:
            FigmaResponse with operation result
        """
        query_params: dict[str, Any] = {}
        query_params['ids'] = ids
        if version is not None:
            query_params['version'] = version
        if depth is not None:
            query_params['depth'] = str(depth)
        if geometry is not None:
            query_params['geometry'] = geometry
        if plugin_data is not None:
            query_params['plugin_data'] = plugin_data

        url = self.base_url + "/files/{file_key}/nodes".format(file_key=file_key)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return FigmaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_file_nodes" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return FigmaResponse(success=False, error=str(e), message="Failed to execute get_file_nodes")

    async def get_file_images(
        self,
        file_key: str,
        ids: str,
        *,
        scale: float | None = None,
        image_format: str | None = None,
        svg_include_id: bool | None = None,
        svg_simplify_stroke: bool | None = None,
        use_absolute_bounds: bool | None = None,
        version: str | None = None
    ) -> FigmaResponse:
        """Render images from a Figma file

        Args:
            file_key: The file key
            ids: Comma-separated list of node IDs to render
            scale: Image scale factor (0.01 to 4)
            image_format: Image format: jpg, png, svg, or pdf
            svg_include_id: Include id attribute for all SVG elements
            svg_simplify_stroke: Simplify inside/outside strokes and use stroke attribute
            use_absolute_bounds: Use full dimensions of the node regardless of cropping
            version: A specific version ID to get

        Returns:
            FigmaResponse with operation result
        """
        query_params: dict[str, Any] = {}
        query_params['ids'] = ids
        if scale is not None:
            query_params['scale'] = str(scale)
        if image_format is not None:
            query_params['format'] = image_format
        if svg_include_id is not None:
            query_params['svg_include_id'] = str(svg_include_id).lower()
        if svg_simplify_stroke is not None:
            query_params['svg_simplify_stroke'] = str(svg_simplify_stroke).lower()
        if use_absolute_bounds is not None:
            query_params['use_absolute_bounds'] = str(use_absolute_bounds).lower()
        if version is not None:
            query_params['version'] = version

        url = self.base_url + "/images/{file_key}".format(file_key=file_key)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return FigmaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_file_images" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return FigmaResponse(success=False, error=str(e), message="Failed to execute get_file_images")

    async def list_comments(
        self,
        file_key: str
    ) -> FigmaResponse:
        """List comments on a file

        Args:
            file_key: The file key

        Returns:
            FigmaResponse with operation result
        """
        url = self.base_url + "/files/{file_key}/comments".format(file_key=file_key)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return FigmaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_comments" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return FigmaResponse(success=False, error=str(e), message="Failed to execute list_comments")

    async def post_comment(
        self,
        file_key: str,
        message: str,
        comment_id: str | None = None,
        client_meta: dict[str, Any] | None = None
    ) -> FigmaResponse:
        """Post a comment on a file

        Args:
            file_key: The file key
            message: The comment text
            comment_id: The ID of the comment to reply to
            client_meta: Position of the comment (x, y, node_id, node_offset)

        Returns:
            FigmaResponse with operation result
        """
        url = self.base_url + "/files/{file_key}/comments".format(file_key=file_key)

        body: dict[str, Any] = {}
        body['message'] = message
        if comment_id is not None:
            body['comment_id'] = comment_id
        if client_meta is not None:
            body['client_meta'] = client_meta

        try:
            request = HTTPRequest(
                method="POST",
                url=url,
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return FigmaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed post_comment" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return FigmaResponse(success=False, error=str(e), message="Failed to execute post_comment")

    async def list_file_versions(
        self,
        file_key: str
    ) -> FigmaResponse:
        """List version history of a file

        Args:
            file_key: The file key

        Returns:
            FigmaResponse with operation result
        """
        url = self.base_url + "/files/{file_key}/versions".format(file_key=file_key)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return FigmaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_file_versions" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return FigmaResponse(success=False, error=str(e), message="Failed to execute list_file_versions")

    async def list_team_projects(
        self,
        team_id: str
    ) -> FigmaResponse:
        """List projects in a team

        Args:
            team_id: The team ID

        Returns:
            FigmaResponse with operation result
        """
        url = self.base_url + "/teams/{team_id}/projects".format(team_id=team_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return FigmaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_team_projects" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return FigmaResponse(success=False, error=str(e), message="Failed to execute list_team_projects")

    async def list_project_files(
        self,
        project_id: str,
        *,
        branch_data: bool | None = None
    ) -> FigmaResponse:
        """List files in a project

        Args:
            project_id: The project ID
            branch_data: Returns branch metadata for the requested files

        Returns:
            FigmaResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if branch_data is not None:
            query_params['branch_data'] = str(branch_data).lower()

        url = self.base_url + "/projects/{project_id}/files".format(project_id=project_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return FigmaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_project_files" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return FigmaResponse(success=False, error=str(e), message="Failed to execute list_project_files")

    async def list_team_components(
        self,
        team_id: str,
        page_size: int | None = None,
        after: str | None = None,
        before: str | None = None
    ) -> FigmaResponse:
        """List components published in a team library

        Args:
            team_id: The team ID
            page_size: Number of items per page (max 30)
            after: Cursor for pagination (next page)
            before: Cursor for pagination (previous page)

        Returns:
            FigmaResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if page_size is not None:
            query_params['page_size'] = str(page_size)
        if after is not None:
            query_params['after'] = after
        if before is not None:
            query_params['before'] = before

        url = self.base_url + "/teams/{team_id}/components".format(team_id=team_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return FigmaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_team_components" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return FigmaResponse(success=False, error=str(e), message="Failed to execute list_team_components")

    async def list_file_components(
        self,
        file_key: str
    ) -> FigmaResponse:
        """List components in a file

        Args:
            file_key: The file key

        Returns:
            FigmaResponse with operation result
        """
        url = self.base_url + "/files/{file_key}/components".format(file_key=file_key)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return FigmaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_file_components" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return FigmaResponse(success=False, error=str(e), message="Failed to execute list_file_components")

    async def list_team_component_sets(
        self,
        team_id: str,
        page_size: int | None = None,
        after: str | None = None,
        before: str | None = None
    ) -> FigmaResponse:
        """List component sets published in a team library

        Args:
            team_id: The team ID
            page_size: Number of items per page (max 30)
            after: Cursor for pagination (next page)
            before: Cursor for pagination (previous page)

        Returns:
            FigmaResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if page_size is not None:
            query_params['page_size'] = str(page_size)
        if after is not None:
            query_params['after'] = after
        if before is not None:
            query_params['before'] = before

        url = self.base_url + "/teams/{team_id}/component_sets".format(team_id=team_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return FigmaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_team_component_sets" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return FigmaResponse(success=False, error=str(e), message="Failed to execute list_team_component_sets")

    async def list_team_styles(
        self,
        team_id: str,
        page_size: int | None = None,
        after: str | None = None,
        before: str | None = None
    ) -> FigmaResponse:
        """List styles published in a team library

        Args:
            team_id: The team ID
            page_size: Number of items per page (max 30)
            after: Cursor for pagination (next page)
            before: Cursor for pagination (previous page)

        Returns:
            FigmaResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if page_size is not None:
            query_params['page_size'] = str(page_size)
        if after is not None:
            query_params['after'] = after
        if before is not None:
            query_params['before'] = before

        url = self.base_url + "/teams/{team_id}/styles".format(team_id=team_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return FigmaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_team_styles" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return FigmaResponse(success=False, error=str(e), message="Failed to execute list_team_styles")

    async def list_file_styles(
        self,
        file_key: str
    ) -> FigmaResponse:
        """List styles in a file

        Args:
            file_key: The file key

        Returns:
            FigmaResponse with operation result
        """
        url = self.base_url + "/files/{file_key}/styles".format(file_key=file_key)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return FigmaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_file_styles" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return FigmaResponse(success=False, error=str(e), message="Failed to execute list_file_styles")

    async def get_local_variables(
        self,
        file_key: str
    ) -> FigmaResponse:
        """Get local variables in a file

        Args:
            file_key: The file key

        Returns:
            FigmaResponse with operation result
        """
        url = self.base_url + "/files/{file_key}/variables/local".format(file_key=file_key)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return FigmaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_local_variables" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return FigmaResponse(success=False, error=str(e), message="Failed to execute get_local_variables")

    async def get_published_variables(
        self,
        file_key: str
    ) -> FigmaResponse:
        """Get published variables in a file

        Args:
            file_key: The file key

        Returns:
            FigmaResponse with operation result
        """
        url = self.base_url + "/files/{file_key}/variables/published".format(file_key=file_key)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return FigmaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_published_variables" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return FigmaResponse(success=False, error=str(e), message="Failed to execute get_published_variables")

    async def get_webhook(
        self,
        webhook_id: str
    ) -> FigmaResponse:
        """Get a webhook by ID

        Args:
            webhook_id: The webhook ID

        Returns:
            FigmaResponse with operation result
        """
        url = self.base_url + "/webhooks/{webhook_id}".format(webhook_id=webhook_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return FigmaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed get_webhook" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return FigmaResponse(success=False, error=str(e), message="Failed to execute get_webhook")

    async def create_webhook(
        self,
        event_type: str,
        team_id: str,
        endpoint: str,
        passcode: str | None = None,
        description: str | None = None
    ) -> FigmaResponse:
        """Create a new webhook

        Args:
            event_type: The event type to subscribe to
            team_id: The team ID to receive events from
            endpoint: The endpoint URL to receive webhook events
            passcode: A passcode for webhook verification
            description: A description for the webhook

        Returns:
            FigmaResponse with operation result
        """
        url = self.base_url + "/webhooks"

        body: dict[str, Any] = {}
        body['event_type'] = event_type
        body['team_id'] = team_id
        body['endpoint'] = endpoint
        if passcode is not None:
            body['passcode'] = passcode
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
            return FigmaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed create_webhook" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return FigmaResponse(success=False, error=str(e), message="Failed to execute create_webhook")

    async def update_webhook(
        self,
        webhook_id: str,
        event_type: str | None = None,
        endpoint: str | None = None,
        passcode: str | None = None,
        description: str | None = None
    ) -> FigmaResponse:
        """Update an existing webhook

        Args:
            webhook_id: The webhook ID
            event_type: The event type to subscribe to
            endpoint: The endpoint URL to receive webhook events
            passcode: A passcode for webhook verification
            description: A description for the webhook

        Returns:
            FigmaResponse with operation result
        """
        url = self.base_url + "/webhooks/{webhook_id}".format(webhook_id=webhook_id)

        body: dict[str, Any] = {}
        if event_type is not None:
            body['event_type'] = event_type
        if endpoint is not None:
            body['endpoint'] = endpoint
        if passcode is not None:
            body['passcode'] = passcode
        if description is not None:
            body['description'] = description

        try:
            request = HTTPRequest(
                method="PUT",
                url=url,
                headers={"Content-Type": "application/json"},
                body=body,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return FigmaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed update_webhook" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return FigmaResponse(success=False, error=str(e), message="Failed to execute update_webhook")

    async def delete_webhook(
        self,
        webhook_id: str
    ) -> FigmaResponse:
        """Delete a webhook

        Args:
            webhook_id: The webhook ID

        Returns:
            FigmaResponse with operation result
        """
        url = self.base_url + "/webhooks/{webhook_id}".format(webhook_id=webhook_id)

        try:
            request = HTTPRequest(
                method="DELETE",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return FigmaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed delete_webhook" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return FigmaResponse(success=False, error=str(e), message="Failed to execute delete_webhook")

    async def list_team_webhooks(
        self,
        team_id: str
    ) -> FigmaResponse:
        """List webhooks for a team

        Args:
            team_id: The team ID

        Returns:
            FigmaResponse with operation result
        """
        url = self.base_url + "/teams/{team_id}/webhooks".format(team_id=team_id)

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return FigmaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_team_webhooks" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return FigmaResponse(success=False, error=str(e), message="Failed to execute list_team_webhooks")

    async def list_activity_logs(
        self,
        events: str | None = None,
        limit: int | None = None,
        order: str | None = None
    ) -> FigmaResponse:
        """List activity log events

        Args:
            events: Comma-separated list of event types to filter
            limit: Maximum number of events to return
            order: Sort order: 'asc' or 'desc'

        Returns:
            FigmaResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if events is not None:
            query_params['events'] = events
        if limit is not None:
            query_params['limit'] = str(limit)
        if order is not None:
            query_params['order'] = order

        url = self.base_url + "/activity_logs"

        try:
            request = HTTPRequest(
                method="GET",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return FigmaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed list_activity_logs" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return FigmaResponse(success=False, error=str(e), message="Failed to execute list_activity_logs")
