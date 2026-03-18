# ruff: noqa
"""
Figma REST API DataSource - Auto-generated API wrapper

Generated from Figma REST API documentation.
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
    - Files and File Nodes
    - File Versions
    - Images and Image Fills
    - Comments and Comment Reactions
    - Users
    - Projects and Project Files
    - Components and Component Sets
    - Styles
    - Webhooks (v2)
    - Activity Logs
    - Payments
    - Variables
    - Dev Resources
    - Library Analytics

    The base URL is determined by the FigmaClient's configured base URL
    (default: https://api.figma.com).

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
        """Get file JSON

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

        url = self.base_url + "/v1/files/{file_key}".format(file_key=file_key)

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
        """Get specific nodes from a file

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

        url = self.base_url + "/v1/files/{file_key}/nodes".format(file_key=file_key)

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

    async def get_images(
        self,
        file_key: str,
        ids: str,
        *,
        version: str | None = None,
        scale: float | None = None,
        image_format: str | None = None,
        svg_outline_text: bool | None = None,
        svg_include_id: bool | None = None,
        svg_include_node_id: bool | None = None,
        svg_simplify_stroke: bool | None = None,
        contents_only: bool | None = None,
        use_absolute_bounds: bool | None = None
    ) -> FigmaResponse:
        """Render images of file nodes

        Args:
            file_key: The file key
            ids: Comma-separated list of node IDs to render
            version: A specific version ID to use
            scale: Image scale factor (0.01 to 4)
            image_format: Image format: jpg, png, svg, or pdf
            svg_outline_text: Whether text elements are rendered as outlines in SVGs
            svg_include_id: Include id attribute for all SVG elements
            svg_include_node_id: Include node-id attribute for all SVG elements
            svg_simplify_stroke: Simplify inside/outside strokes and use stroke attribute
            contents_only: Whether content that overlaps the node should be excluded
            use_absolute_bounds: Use full dimensions of the node regardless of cropping

        Returns:
            FigmaResponse with operation result
        """
        query_params: dict[str, Any] = {}
        query_params['ids'] = ids
        if version is not None:
            query_params['version'] = version
        if scale is not None:
            query_params['scale'] = str(scale)
        if image_format is not None:
            query_params['format'] = image_format
        if svg_outline_text is not None:
            query_params['svg_outline_text'] = str(svg_outline_text).lower()
        if svg_include_id is not None:
            query_params['svg_include_id'] = str(svg_include_id).lower()
        if svg_include_node_id is not None:
            query_params['svg_include_node_id'] = str(svg_include_node_id).lower()
        if svg_simplify_stroke is not None:
            query_params['svg_simplify_stroke'] = str(svg_simplify_stroke).lower()
        if contents_only is not None:
            query_params['contents_only'] = str(contents_only).lower()
        if use_absolute_bounds is not None:
            query_params['use_absolute_bounds'] = str(use_absolute_bounds).lower()

        url = self.base_url + "/v1/images/{file_key}".format(file_key=file_key)

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
                message="Successfully executed get_images" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return FigmaResponse(success=False, error=str(e), message="Failed to execute get_images")

    async def get_image_fills(
        self,
        file_key: str
    ) -> FigmaResponse:
        """Get image fills

        Args:
            file_key: The file key

        Returns:
            FigmaResponse with operation result
        """
        url = self.base_url + "/v1/files/{file_key}/images".format(file_key=file_key)

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
                message="Successfully executed get_image_fills" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return FigmaResponse(success=False, error=str(e), message="Failed to execute get_image_fills")

    async def get_file_meta(
        self,
        file_key: str
    ) -> FigmaResponse:
        """Get file metadata

        Args:
            file_key: The file key

        Returns:
            FigmaResponse with operation result
        """
        url = self.base_url + "/v1/files/{file_key}/meta".format(file_key=file_key)

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
                message="Successfully executed get_file_meta" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return FigmaResponse(success=False, error=str(e), message="Failed to execute get_file_meta")

    async def get_file_versions(
        self,
        file_key: str,
        page_size: int | None = None,
        before: int | None = None,
        after: int | None = None
    ) -> FigmaResponse:
        """Get version history

        Args:
            file_key: The file key
            page_size: Number of items per page
            before: Version ID to get versions before
            after: Version ID to get versions after

        Returns:
            FigmaResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if page_size is not None:
            query_params['page_size'] = str(page_size)
        if before is not None:
            query_params['before'] = str(before)
        if after is not None:
            query_params['after'] = str(after)

        url = self.base_url + "/v1/files/{file_key}/versions".format(file_key=file_key)

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
                message="Successfully executed get_file_versions" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return FigmaResponse(success=False, error=str(e), message="Failed to execute get_file_versions")

    async def get_comments(
        self,
        file_key: str,
        *,
        as_md: bool | None = None
    ) -> FigmaResponse:
        """Get comments in a file

        Args:
            file_key: The file key
            as_md: Return comments as markdown

        Returns:
            FigmaResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if as_md is not None:
            query_params['as_md'] = str(as_md).lower()

        url = self.base_url + "/v1/files/{file_key}/comments".format(file_key=file_key)

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
                message="Successfully executed get_comments" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return FigmaResponse(success=False, error=str(e), message="Failed to execute get_comments")

    async def post_comment(
        self,
        file_key: str,
        message: str,
        comment_id: str | None = None,
        client_meta: dict[str, Any] | None = None
    ) -> FigmaResponse:
        """Add a comment to a file

        Args:
            file_key: The file key
            message: The comment text
            comment_id: The ID of the comment to reply to
            client_meta: Position of the comment (x, y, node_id, node_offset)

        Returns:
            FigmaResponse with operation result
        """
        url = self.base_url + "/v1/files/{file_key}/comments".format(file_key=file_key)

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

    async def delete_comment(
        self,
        file_key: str,
        comment_id: str
    ) -> FigmaResponse:
        """Delete a comment

        Args:
            file_key: The file key
            comment_id: The comment ID

        Returns:
            FigmaResponse with operation result
        """
        url = self.base_url + "/v1/files/{file_key}/comments/{comment_id}".format(file_key=file_key, comment_id=comment_id)

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
                message="Successfully executed delete_comment" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return FigmaResponse(success=False, error=str(e), message="Failed to execute delete_comment")

    async def get_comment_reactions(
        self,
        file_key: str,
        comment_id: str,
        cursor: str | None = None
    ) -> FigmaResponse:
        """Get reactions for a comment

        Args:
            file_key: The file key
            comment_id: The comment ID
            cursor: Cursor for pagination

        Returns:
            FigmaResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if cursor is not None:
            query_params['cursor'] = cursor

        url = self.base_url + "/v1/files/{file_key}/comments/{comment_id}/reactions".format(file_key=file_key, comment_id=comment_id)

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
                message="Successfully executed get_comment_reactions" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return FigmaResponse(success=False, error=str(e), message="Failed to execute get_comment_reactions")

    async def post_comment_reaction(
        self,
        file_key: str,
        comment_id: str,
        emoji: str
    ) -> FigmaResponse:
        """Add a reaction to a comment

        Args:
            file_key: The file key
            comment_id: The comment ID
            emoji: The emoji to react with

        Returns:
            FigmaResponse with operation result
        """
        url = self.base_url + "/v1/files/{file_key}/comments/{comment_id}/reactions".format(file_key=file_key, comment_id=comment_id)

        body: dict[str, Any] = {}
        body['emoji'] = emoji

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
                message="Successfully executed post_comment_reaction" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return FigmaResponse(success=False, error=str(e), message="Failed to execute post_comment_reaction")

    async def delete_comment_reaction(
        self,
        file_key: str,
        comment_id: str,
        emoji: str
    ) -> FigmaResponse:
        """Delete a reaction

        Args:
            file_key: The file key
            comment_id: The comment ID
            emoji: The emoji to remove

        Returns:
            FigmaResponse with operation result
        """
        query_params: dict[str, Any] = {}
        query_params['emoji'] = emoji

        url = self.base_url + "/v1/files/{file_key}/comments/{comment_id}/reactions".format(file_key=file_key, comment_id=comment_id)

        try:
            request = HTTPRequest(
                method="DELETE",
                url=url,
                headers={"Content-Type": "application/json"},
                query=query_params,
            )
            response = await self.http.execute(request)  # type: ignore[reportUnknownMemberType]
            response_data = response.json() if response.text() else None
            return FigmaResponse(
                success=response.status < HTTP_ERROR_THRESHOLD,
                data=response_data,
                message="Successfully executed delete_comment_reaction" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return FigmaResponse(success=False, error=str(e), message="Failed to execute delete_comment_reaction")

    async def get_me(
        self
    ) -> FigmaResponse:
        """Get current user

        Returns:
            FigmaResponse with operation result
        """
        url = self.base_url + "/v1/me"

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
                message="Successfully executed get_me" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return FigmaResponse(success=False, error=str(e), message="Failed to execute get_me")

    async def get_team_projects(
        self,
        team_id: str
    ) -> FigmaResponse:
        """Get projects in a team

        Args:
            team_id: The team ID

        Returns:
            FigmaResponse with operation result
        """
        url = self.base_url + "/v1/teams/{team_id}/projects".format(team_id=team_id)

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
                message="Successfully executed get_team_projects" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return FigmaResponse(success=False, error=str(e), message="Failed to execute get_team_projects")

    async def get_project_files(
        self,
        project_id: str,
        *,
        branch_data: bool | None = None
    ) -> FigmaResponse:
        """Get files in a project

        Args:
            project_id: The project ID
            branch_data: Returns branch metadata for the requested files

        Returns:
            FigmaResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if branch_data is not None:
            query_params['branch_data'] = str(branch_data).lower()

        url = self.base_url + "/v1/projects/{project_id}/files".format(project_id=project_id)

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
                message="Successfully executed get_project_files" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return FigmaResponse(success=False, error=str(e), message="Failed to execute get_project_files")

    async def get_team_components(
        self,
        team_id: str,
        page_size: int | None = None,
        after: int | None = None,
        before: int | None = None
    ) -> FigmaResponse:
        """Get team components

        Args:
            team_id: The team ID
            page_size: Number of items per page
            after: Cursor for pagination (next page)
            before: Cursor for pagination (previous page)

        Returns:
            FigmaResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if page_size is not None:
            query_params['page_size'] = str(page_size)
        if after is not None:
            query_params['after'] = str(after)
        if before is not None:
            query_params['before'] = str(before)

        url = self.base_url + "/v1/teams/{team_id}/components".format(team_id=team_id)

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
                message="Successfully executed get_team_components" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return FigmaResponse(success=False, error=str(e), message="Failed to execute get_team_components")

    async def get_file_components(
        self,
        file_key: str
    ) -> FigmaResponse:
        """Get file components

        Args:
            file_key: The file key

        Returns:
            FigmaResponse with operation result
        """
        url = self.base_url + "/v1/files/{file_key}/components".format(file_key=file_key)

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
                message="Successfully executed get_file_components" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return FigmaResponse(success=False, error=str(e), message="Failed to execute get_file_components")

    async def get_component(
        self,
        key: str
    ) -> FigmaResponse:
        """Get component by key

        Args:
            key: The component key

        Returns:
            FigmaResponse with operation result
        """
        url = self.base_url + "/v1/components/{key}".format(key=key)

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
                message="Successfully executed get_component" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return FigmaResponse(success=False, error=str(e), message="Failed to execute get_component")

    async def get_team_component_sets(
        self,
        team_id: str,
        page_size: int | None = None,
        after: int | None = None,
        before: int | None = None
    ) -> FigmaResponse:
        """Get team component sets

        Args:
            team_id: The team ID
            page_size: Number of items per page
            after: Cursor for pagination (next page)
            before: Cursor for pagination (previous page)

        Returns:
            FigmaResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if page_size is not None:
            query_params['page_size'] = str(page_size)
        if after is not None:
            query_params['after'] = str(after)
        if before is not None:
            query_params['before'] = str(before)

        url = self.base_url + "/v1/teams/{team_id}/component_sets".format(team_id=team_id)

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
                message="Successfully executed get_team_component_sets" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return FigmaResponse(success=False, error=str(e), message="Failed to execute get_team_component_sets")

    async def get_file_component_sets(
        self,
        file_key: str
    ) -> FigmaResponse:
        """Get file component sets

        Args:
            file_key: The file key

        Returns:
            FigmaResponse with operation result
        """
        url = self.base_url + "/v1/files/{file_key}/component_sets".format(file_key=file_key)

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
                message="Successfully executed get_file_component_sets" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return FigmaResponse(success=False, error=str(e), message="Failed to execute get_file_component_sets")

    async def get_component_set(
        self,
        key: str
    ) -> FigmaResponse:
        """Get component set by key

        Args:
            key: The component set key

        Returns:
            FigmaResponse with operation result
        """
        url = self.base_url + "/v1/component_sets/{key}".format(key=key)

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
                message="Successfully executed get_component_set" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return FigmaResponse(success=False, error=str(e), message="Failed to execute get_component_set")

    async def get_team_styles(
        self,
        team_id: str,
        page_size: int | None = None,
        after: int | None = None,
        before: int | None = None
    ) -> FigmaResponse:
        """Get team styles

        Args:
            team_id: The team ID
            page_size: Number of items per page
            after: Cursor for pagination (next page)
            before: Cursor for pagination (previous page)

        Returns:
            FigmaResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if page_size is not None:
            query_params['page_size'] = str(page_size)
        if after is not None:
            query_params['after'] = str(after)
        if before is not None:
            query_params['before'] = str(before)

        url = self.base_url + "/v1/teams/{team_id}/styles".format(team_id=team_id)

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
                message="Successfully executed get_team_styles" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return FigmaResponse(success=False, error=str(e), message="Failed to execute get_team_styles")

    async def get_file_styles(
        self,
        file_key: str
    ) -> FigmaResponse:
        """Get file styles

        Args:
            file_key: The file key

        Returns:
            FigmaResponse with operation result
        """
        url = self.base_url + "/v1/files/{file_key}/styles".format(file_key=file_key)

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
                message="Successfully executed get_file_styles" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return FigmaResponse(success=False, error=str(e), message="Failed to execute get_file_styles")

    async def get_style(
        self,
        key: str
    ) -> FigmaResponse:
        """Get style by key

        Args:
            key: The style key

        Returns:
            FigmaResponse with operation result
        """
        url = self.base_url + "/v1/styles/{key}".format(key=key)

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
                message="Successfully executed get_style" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return FigmaResponse(success=False, error=str(e), message="Failed to execute get_style")

    async def get_webhooks(
        self,
        context: str | None = None,
        context_id: str | None = None,
        plan_api_id: str | None = None,
        cursor: str | None = None
    ) -> FigmaResponse:
        """Get webhooks by context or plan

        Args:
            context: The context type to filter by
            context_id: The context ID to filter by
            plan_api_id: The plan API ID to filter by
            cursor: Cursor for pagination

        Returns:
            FigmaResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if context is not None:
            query_params['context'] = context
        if context_id is not None:
            query_params['context_id'] = context_id
        if plan_api_id is not None:
            query_params['plan_api_id'] = plan_api_id
        if cursor is not None:
            query_params['cursor'] = cursor

        url = self.base_url + "/v2/webhooks"

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
                message="Successfully executed get_webhooks" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return FigmaResponse(success=False, error=str(e), message="Failed to execute get_webhooks")

    async def post_webhook(
        self,
        event_type: str,
        context: str,
        context_id: str,
        endpoint: str,
        passcode: str,
        team_id: str | None = None,
        status: str | None = None,
        description: str | None = None
    ) -> FigmaResponse:
        """Create a webhook

        Args:
            event_type: The event type to subscribe to
            context: The context type for the webhook
            context_id: The context ID for the webhook
            endpoint: The endpoint URL to receive webhook events
            passcode: A passcode for webhook verification
            team_id: The team ID
            status: The webhook status
            description: A description for the webhook

        Returns:
            FigmaResponse with operation result
        """
        url = self.base_url + "/v2/webhooks"

        body: dict[str, Any] = {}
        body['event_type'] = event_type
        body['context'] = context
        body['context_id'] = context_id
        body['endpoint'] = endpoint
        body['passcode'] = passcode
        if team_id is not None:
            body['team_id'] = team_id
        if status is not None:
            body['status'] = status
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
                message="Successfully executed post_webhook" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return FigmaResponse(success=False, error=str(e), message="Failed to execute post_webhook")

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
        url = self.base_url + "/v2/webhooks/{webhook_id}".format(webhook_id=webhook_id)

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

    async def put_webhook(
        self,
        webhook_id: str,
        event_type: str | None = None,
        endpoint: str | None = None,
        passcode: str | None = None,
        status: str | None = None,
        description: str | None = None
    ) -> FigmaResponse:
        """Update a webhook

        Args:
            webhook_id: The webhook ID
            event_type: The event type to subscribe to
            endpoint: The endpoint URL to receive webhook events
            passcode: A passcode for webhook verification
            status: The webhook status
            description: A description for the webhook

        Returns:
            FigmaResponse with operation result
        """
        url = self.base_url + "/v2/webhooks/{webhook_id}".format(webhook_id=webhook_id)

        body: dict[str, Any] = {}
        if event_type is not None:
            body['event_type'] = event_type
        if endpoint is not None:
            body['endpoint'] = endpoint
        if passcode is not None:
            body['passcode'] = passcode
        if status is not None:
            body['status'] = status
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
                message="Successfully executed put_webhook" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return FigmaResponse(success=False, error=str(e), message="Failed to execute put_webhook")

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
        url = self.base_url + "/v2/webhooks/{webhook_id}".format(webhook_id=webhook_id)

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

    async def get_team_webhooks(
        self,
        team_id: str
    ) -> FigmaResponse:
        """Get team webhooks (deprecated)

        Args:
            team_id: The team ID

        Returns:
            FigmaResponse with operation result
        """
        url = self.base_url + "/v2/teams/{team_id}/webhooks".format(team_id=team_id)

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
                message="Successfully executed get_team_webhooks" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return FigmaResponse(success=False, error=str(e), message="Failed to execute get_team_webhooks")

    async def get_webhook_requests(
        self,
        webhook_id: str
    ) -> FigmaResponse:
        """Get webhook requests

        Args:
            webhook_id: The webhook ID

        Returns:
            FigmaResponse with operation result
        """
        url = self.base_url + "/v2/webhooks/{webhook_id}/requests".format(webhook_id=webhook_id)

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
                message="Successfully executed get_webhook_requests" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return FigmaResponse(success=False, error=str(e), message="Failed to execute get_webhook_requests")

    async def get_activity_logs(
        self,
        events: str | None = None,
        start_time: int | None = None,
        end_time: int | None = None,
        limit: int | None = None,
        order: str | None = None
    ) -> FigmaResponse:
        """Get activity logs

        Args:
            events: Comma-separated list of event types to filter
            start_time: Start time as Unix timestamp
            end_time: End time as Unix timestamp
            limit: Maximum number of events to return
            order: Sort order: 'asc' or 'desc'

        Returns:
            FigmaResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if events is not None:
            query_params['events'] = events
        if start_time is not None:
            query_params['start_time'] = str(start_time)
        if end_time is not None:
            query_params['end_time'] = str(end_time)
        if limit is not None:
            query_params['limit'] = str(limit)
        if order is not None:
            query_params['order'] = order

        url = self.base_url + "/v1/activity_logs"

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
                message="Successfully executed get_activity_logs" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return FigmaResponse(success=False, error=str(e), message="Failed to execute get_activity_logs")

    async def get_payments(
        self,
        plugin_payment_token: str | None = None,
        user_id: str | None = None,
        community_file_id: str | None = None,
        plugin_id: str | None = None,
        widget_id: str | None = None
    ) -> FigmaResponse:
        """Get payments

        Args:
            plugin_payment_token: Plugin payment token
            user_id: User ID to filter by
            community_file_id: Community file ID to filter by
            plugin_id: Plugin ID to filter by
            widget_id: Widget ID to filter by

        Returns:
            FigmaResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if plugin_payment_token is not None:
            query_params['plugin_payment_token'] = plugin_payment_token
        if user_id is not None:
            query_params['user_id'] = user_id
        if community_file_id is not None:
            query_params['community_file_id'] = community_file_id
        if plugin_id is not None:
            query_params['plugin_id'] = plugin_id
        if widget_id is not None:
            query_params['widget_id'] = widget_id

        url = self.base_url + "/v1/payments"

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
                message="Successfully executed get_payments" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return FigmaResponse(success=False, error=str(e), message="Failed to execute get_payments")

    async def get_local_variables(
        self,
        file_key: str
    ) -> FigmaResponse:
        """Get local variables

        Args:
            file_key: The file key

        Returns:
            FigmaResponse with operation result
        """
        url = self.base_url + "/v1/files/{file_key}/variables/local".format(file_key=file_key)

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
        """Get published variables

        Args:
            file_key: The file key

        Returns:
            FigmaResponse with operation result
        """
        url = self.base_url + "/v1/files/{file_key}/variables/published".format(file_key=file_key)

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

    async def post_variables(
        self,
        file_key: str,
        variable_collections: list[Any] | None = None,
        variable_modes: list[Any] | None = None,
        variables: list[Any] | None = None,
        variable_mode_values: list[Any] | None = None
    ) -> FigmaResponse:
        """Create/modify/delete variables

        Args:
            file_key: The file key
            variable_collections: List of variable collections to create/modify/delete
            variable_modes: List of variable modes to create/modify/delete
            variables: List of variables to create/modify/delete
            variable_mode_values: List of variable mode values to set

        Returns:
            FigmaResponse with operation result
        """
        url = self.base_url + "/v1/files/{file_key}/variables".format(file_key=file_key)

        body: dict[str, Any] = {}
        if variable_collections is not None:
            body['variableCollections'] = variable_collections
        if variable_modes is not None:
            body['variableModes'] = variable_modes
        if variables is not None:
            body['variables'] = variables
        if variable_mode_values is not None:
            body['variableModeValues'] = variable_mode_values

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
                message="Successfully executed post_variables" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return FigmaResponse(success=False, error=str(e), message="Failed to execute post_variables")

    async def get_dev_resources(
        self,
        file_key: str,
        node_ids: str | None = None
    ) -> FigmaResponse:
        """Get dev resources

        Args:
            file_key: The file key
            node_ids: Comma-separated list of node IDs to filter by

        Returns:
            FigmaResponse with operation result
        """
        query_params: dict[str, Any] = {}
        if node_ids is not None:
            query_params['node_ids'] = node_ids

        url = self.base_url + "/v1/files/{file_key}/dev_resources".format(file_key=file_key)

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
                message="Successfully executed get_dev_resources" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return FigmaResponse(success=False, error=str(e), message="Failed to execute get_dev_resources")

    async def post_dev_resources(
        self,
        dev_resources: list[Any]
    ) -> FigmaResponse:
        """Create dev resources

        Args:
            dev_resources: List of dev resources to create (each with name, url, file_key, node_id)

        Returns:
            FigmaResponse with operation result
        """
        url = self.base_url + "/v1/dev_resources"

        body: dict[str, Any] = {}
        body['dev_resources'] = dev_resources

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
                message="Successfully executed post_dev_resources" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return FigmaResponse(success=False, error=str(e), message="Failed to execute post_dev_resources")

    async def put_dev_resources(
        self,
        dev_resources: list[Any]
    ) -> FigmaResponse:
        """Update dev resources

        Args:
            dev_resources: List of dev resources to update (each with id, name, url)

        Returns:
            FigmaResponse with operation result
        """
        url = self.base_url + "/v1/dev_resources"

        body: dict[str, Any] = {}
        body['dev_resources'] = dev_resources

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
                message="Successfully executed put_dev_resources" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return FigmaResponse(success=False, error=str(e), message="Failed to execute put_dev_resources")

    async def delete_dev_resource(
        self,
        file_key: str,
        dev_resource_id: str
    ) -> FigmaResponse:
        """Delete dev resource

        Args:
            file_key: The file key
            dev_resource_id: The dev resource ID

        Returns:
            FigmaResponse with operation result
        """
        url = self.base_url + "/v1/files/{file_key}/dev_resources/{dev_resource_id}".format(file_key=file_key, dev_resource_id=dev_resource_id)

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
                message="Successfully executed delete_dev_resource" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return FigmaResponse(success=False, error=str(e), message="Failed to execute delete_dev_resource")

    async def get_library_analytics_component_actions(
        self,
        file_key: str,
        group_by: str,
        cursor: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None
    ) -> FigmaResponse:
        """Get library analytics for component actions

        Args:
            file_key: The file key
            group_by: How to group the results
            cursor: Cursor for pagination
            start_date: Start date (ISO 8601)
            end_date: End date (ISO 8601)

        Returns:
            FigmaResponse with operation result
        """
        query_params: dict[str, Any] = {}
        query_params['group_by'] = group_by
        if cursor is not None:
            query_params['cursor'] = cursor
        if start_date is not None:
            query_params['start_date'] = start_date
        if end_date is not None:
            query_params['end_date'] = end_date

        url = self.base_url + "/v1/analytics/libraries/{file_key}/component/actions".format(file_key=file_key)

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
                message="Successfully executed get_library_analytics_component_actions" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return FigmaResponse(success=False, error=str(e), message="Failed to execute get_library_analytics_component_actions")

    async def get_library_analytics_component_usages(
        self,
        file_key: str,
        group_by: str,
        cursor: str | None = None
    ) -> FigmaResponse:
        """Get library analytics for component usages

        Args:
            file_key: The file key
            group_by: How to group the results
            cursor: Cursor for pagination

        Returns:
            FigmaResponse with operation result
        """
        query_params: dict[str, Any] = {}
        query_params['group_by'] = group_by
        if cursor is not None:
            query_params['cursor'] = cursor

        url = self.base_url + "/v1/analytics/libraries/{file_key}/component/usages".format(file_key=file_key)

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
                message="Successfully executed get_library_analytics_component_usages" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return FigmaResponse(success=False, error=str(e), message="Failed to execute get_library_analytics_component_usages")

    async def get_library_analytics_style_actions(
        self,
        file_key: str,
        group_by: str,
        cursor: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None
    ) -> FigmaResponse:
        """Get library analytics for style actions

        Args:
            file_key: The file key
            group_by: How to group the results
            cursor: Cursor for pagination
            start_date: Start date (ISO 8601)
            end_date: End date (ISO 8601)

        Returns:
            FigmaResponse with operation result
        """
        query_params: dict[str, Any] = {}
        query_params['group_by'] = group_by
        if cursor is not None:
            query_params['cursor'] = cursor
        if start_date is not None:
            query_params['start_date'] = start_date
        if end_date is not None:
            query_params['end_date'] = end_date

        url = self.base_url + "/v1/analytics/libraries/{file_key}/style/actions".format(file_key=file_key)

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
                message="Successfully executed get_library_analytics_style_actions" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return FigmaResponse(success=False, error=str(e), message="Failed to execute get_library_analytics_style_actions")

    async def get_library_analytics_style_usages(
        self,
        file_key: str,
        group_by: str,
        cursor: str | None = None
    ) -> FigmaResponse:
        """Get library analytics for style usages

        Args:
            file_key: The file key
            group_by: How to group the results
            cursor: Cursor for pagination

        Returns:
            FigmaResponse with operation result
        """
        query_params: dict[str, Any] = {}
        query_params['group_by'] = group_by
        if cursor is not None:
            query_params['cursor'] = cursor

        url = self.base_url + "/v1/analytics/libraries/{file_key}/style/usages".format(file_key=file_key)

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
                message="Successfully executed get_library_analytics_style_usages" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return FigmaResponse(success=False, error=str(e), message="Failed to execute get_library_analytics_style_usages")

    async def get_library_analytics_variable_actions(
        self,
        file_key: str,
        group_by: str,
        cursor: str | None = None,
        start_date: str | None = None,
        end_date: str | None = None
    ) -> FigmaResponse:
        """Get library analytics for variable actions

        Args:
            file_key: The file key
            group_by: How to group the results
            cursor: Cursor for pagination
            start_date: Start date (ISO 8601)
            end_date: End date (ISO 8601)

        Returns:
            FigmaResponse with operation result
        """
        query_params: dict[str, Any] = {}
        query_params['group_by'] = group_by
        if cursor is not None:
            query_params['cursor'] = cursor
        if start_date is not None:
            query_params['start_date'] = start_date
        if end_date is not None:
            query_params['end_date'] = end_date

        url = self.base_url + "/v1/analytics/libraries/{file_key}/variable/actions".format(file_key=file_key)

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
                message="Successfully executed get_library_analytics_variable_actions" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return FigmaResponse(success=False, error=str(e), message="Failed to execute get_library_analytics_variable_actions")

    async def get_library_analytics_variable_usages(
        self,
        file_key: str,
        group_by: str,
        cursor: str | None = None
    ) -> FigmaResponse:
        """Get library analytics for variable usages

        Args:
            file_key: The file key
            group_by: How to group the results
            cursor: Cursor for pagination

        Returns:
            FigmaResponse with operation result
        """
        query_params: dict[str, Any] = {}
        query_params['group_by'] = group_by
        if cursor is not None:
            query_params['cursor'] = cursor

        url = self.base_url + "/v1/analytics/libraries/{file_key}/variable/usages".format(file_key=file_key)

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
                message="Successfully executed get_library_analytics_variable_usages" if response.status < HTTP_ERROR_THRESHOLD else f"Failed with status {response.status}"
            )
        except Exception as e:
            return FigmaResponse(success=False, error=str(e), message="Failed to execute get_library_analytics_variable_usages")
