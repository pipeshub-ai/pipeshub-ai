"""
Figma Data Source Implementation

This module provides data source implementations for interacting with the Figma API.
"""

from __future__ import annotations

import logging
from http import HTTPStatus
from typing import Any, Dict, List, Optional, TypedDict, TypeVar, Union, Unpack

from app.sources.client.figma.figma import FigmaClient, FigmaResponse
from app.sources.client.http.http_request import HTTPRequest
from app.sources.client.http.http_response import HTTPResponse

T = TypeVar("T", bound=Any)


def safe_format_url(template: str, params: Dict[str, object]) -> str:
    try:
        return template.format(**params)
    except KeyError as e:
        raise ValueError(f"Missing required parameter: {e}") from e


def to_bool_str(value: Union[bool, str, int, float]) -> str:
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, str) and value.lower() in ("true", "false"):
        return value.lower()
    return str(bool(value)).lower()


def serialize_value(value: Union[bool, str, int, float, list, tuple, set, None]) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return to_bool_str(value)
    if isinstance(value, (list, tuple, set)):
        return ",".join(str(serialize_value(v)) for v in value)
    return str(value)


def as_str_dict(data: Dict[str, Any]) -> Dict[str, str]:
    return {k: serialize_value(v) for k, v in data.items()}


class FigmaBaseSource:
    """Base class for Figma data sources.

    This class provides common functionality and error handling for all Figma data sources.
    """

    def __init__(self, client: FigmaClient) -> None:
        """Initialize Figma data source with a configured client.

        Args:
            client: An instance of FigmaClient

        Raises:
            ValueError: If the client is not properly initialized
        """
        self.logger = logging.getLogger(__name__)
        self._client = client.get_client()
        if self._client is None:
            raise ValueError("HTTP client is not initialized")

        try:
            self.base_url = self._client.get_base_url().rstrip("/")
        except AttributeError as exc:
            raise ValueError("HTTP client does not have get_base_url method") from exc



class FigmaDataSource(FigmaBaseSource):
    """Main Figma data source class that composes functionality from other source classes."""

    def __init__(self, client: FigmaClient) -> None:
        """Initialize Figma data source with a configured client.

        Args:
            client: An instance of FigmaClient
        """
        super().__init__(client)

    def _handle_response(
        self, response: HTTPResponse, method_name: str
    ) -> FigmaResponse[Any]:
        """Handle API response and convert to FigmaResponse.

        Args:
            response: Raw HTTP response from the client
            method_name: Name of the calling method for logging

        Returns:
            Standardized FigmaResponse

        Note:
            This method converts the raw HTTP response into a standardized FigmaResponse
            object that's consistent across the entire application.
        """
        try:
            status_code = getattr(response, "status_code", None)

            try:
                data = response.json() if hasattr(response, "json") else response
            except Exception as json_err:
                self.logger.debug(f"Could not parse JSON response: {json_err}")
                data = response

            if HTTPStatus.OK <= status_code < HTTPStatus.MULTIPLE_CHOICES:
                return FigmaResponse[Any](
                    success=True,
                    data=data,
                    status_code=status_code,
                    message=f"{method_name} completed successfully",
                )
            else:
                error_msg = getattr(
                    data, "error", f"API request failed with status {status_code}"
                )
                if isinstance(error_msg, dict):
                    error_msg = error_msg.get("message", str(error_msg))

                self.logger.error(f"{method_name} error: {error_msg}")
                return FigmaResponse[Any](
                    success=False,
                    error=str(error_msg),
                    status_code=status_code,
                    message=f"{method_name} failed with status {status_code}",
                )

        except Exception as e:
            self.logger.error(
                f"Error processing {method_name} response: {str(e)}", exc_info=True
            )
            return FigmaResponse[Any](
                success=False,
                error=f"Error processing response: {str(e)}",
                status_code=getattr(response, "status_code", 500),
                message=f"An error occurred while processing the {method_name} response",
            )

    def get_data_source(self) -> "FigmaDataSource":
        """Return the data source instance.

        Returns:
            The current FigmaDataSource instance
        """
        return self

    async def get_file_data(
        self,
        file_key: str,
        version: Optional[str] = None,
        depth: Optional[int] = None,
        geometry: Optional[str] = None,
        plugin_data: Optional[str] = None,
        branch_data: Optional[bool] = None,
        headers: Optional[Dict[str, Any]] = None,
    ) -> FigmaResponse[Dict[str, Any]]:
        """Get raw Figma file data including document structure.

        This is an alias for get_file() for backward compatibility.

        Args:
            file_key: The key of the Figma file
            version: A specific version ID to get
            depth: How deep into the document tree to traverse
            geometry: Set to "paths" to export vector data
            plugin_data: Comma-separated list of plugin IDs or "shared"
            branch_data: Whether to include branch metadata
            headers: Additional request headers

        Returns:
            FigmaResponse containing the file data or error details
        """
        return await self.get_file(
            file_key=file_key,
            version=version,
            depth=depth,
            geometry=geometry,
            plugin_data=plugin_data,
            branch_data=branch_data,
            headers=headers
        )

    async def get_file(
        self,
        file_key: str,
        version: Optional[str] = None,
        depth: Optional[int] = None,
        geometry: Optional[str] = None,
        plugin_data: Optional[str] = None,
        branch_data: Optional[bool] = None,
        headers: Optional[Dict[str, Any]] = None,
    ) -> FigmaResponse[Dict[str, Any]]:
        """Get a Figma file.

        Args:
            file_key: The key of the Figma file
            version: A specific version ID to get
            depth: How deep into the document tree to traverse
            geometry: Set to "paths" to export vector data
            plugin_data: Comma-separated list of plugin IDs or "shared"
            branch_data: Whether to include branch metadata
            headers: Additional request headers

        Returns:
            FigmaResponse containing the file data or error details
        """
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {"file_key": file_key}
        _query: Dict[str, Any] = {}

        if version is not None:
            _query["version"] = version
        if depth is not None:
            _query["depth"] = depth
        if geometry is not None:
            _query["geometry"] = geometry
        if plugin_data is not None:
            _query["plugin_data"] = plugin_data
        if branch_data is not None:
            _query["branch_data"] = str(branch_data).lower()

        rel_path = "/v1/files/{file_key}".format(file_key=file_key)
        url = self.base_url + rel_path

        req = HTTPRequest(
            method="GET",
            url=url,
            headers=as_str_dict(_headers),
            path_params=as_str_dict(_path),
            query_params=as_str_dict(_query),
            body=None,
        )

        resp = await self._client.execute(req)
        return self._handle_response(resp, "get_file")

    async def get_team_projects(
        self,
        team_id: str,
        cursor: Optional[str] = None,
        page_size: int = 100,
        headers: Optional[Dict[str, Any]] = None,
    ) -> FigmaResponse[Dict[str, Any]]:
        """Get all projects for a team.

        Args:
            team_id: The ID of the team
            cursor: Pagination cursor
            page_size: Number of items per page (1-100)
            headers: Additional request headers

        Returns:
            FigmaResponse containing projects and pagination info or error details
        """
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {"team_id": team_id}
        _query: Dict[str, Any] = {"page_size": min(100, max(1, page_size))}

        if cursor is not None:
            _query["cursor"] = cursor

        rel_path = "/v1/teams/{team_id}/projects".format(team_id=team_id)
        url = self.base_url + rel_path

        req = HTTPRequest(
            method="GET",
            url=url,
            headers=as_str_dict(_headers),
            path_params=as_str_dict(_path),
            query_params=as_str_dict(_query),
            body=None,
        )

        resp = await self._client.execute(req)
        return self._handle_response(resp, "get_team_projects")

    async def get_file_nodes(
        self,
        file_key: str,
        ids: List[str],
        depth: Optional[int] = None,
        geometry: Optional[str] = None,
        plugin_data: Optional[str] = None,
        branch_data: Optional[bool] = None,
        headers: Optional[Dict[str, Any]] = None,
    ) -> FigmaResponse[Dict[str, Any]]:
        """Get specific nodes from a Figma file.

        Args:
            file_key: The key of the Figma file
            ids: List of node IDs to retrieve
            depth: How deep into the document tree to traverse
            geometry: Set to "paths" to export vector data
            plugin_data: Comma-separated list of plugin IDs or "shared"
            branch_data: Whether to include branch metadata
            headers: Additional request headers

        Returns:
            FigmaResponse containing the requested nodes or error details
        """
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {"file_key": file_key}
        _query: Dict[str, Any] = {"ids": ",".join(ids)}

        if depth is not None:
            _query["depth"] = depth
        if geometry is not None:
            _query["geometry"] = geometry
        if plugin_data is not None:
            _query["plugin_data"] = plugin_data
        if branch_data is not None:
            _query["branch_data"] = str(branch_data).lower()

        rel_path = "/v1/files/{file_key}/nodes".format(file_key=file_key)
        url = self.base_url + rel_path

        req = HTTPRequest(
            method="GET",
            url=url,
            headers=as_str_dict(_headers),
            path_params=as_str_dict(_path),
            query_params=as_str_dict(_query),
            body=None,
        )

        resp = await self._client.execute(req)
        return self._handle_response(resp, "get_file_nodes")

    async def get_file_versions(
        self,
        file_key: str,
        page_size: Optional[int] = 30,
        cursor: Optional[str] = None,
        headers: Optional[Dict[str, Any]] = None,
    ) -> FigmaResponse[Dict[str, Any]]:
        """Get version history of a Figma file.

        Args:
            file_key: The key of the Figma file
            page_size: Number of items per page (1-100)
            cursor: Pagination cursor
            headers: Additional request headers

        Returns:
            FigmaResponse containing file versions and pagination info or error details
        """
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {"file_key": file_key}
        _query: Dict[str, Any] = {"page_size": min(100, max(1, page_size))}

        if cursor is not None:
            _query["cursor"] = cursor

        rel_path = "/v1/files/{file_key}/versions".format(file_key=file_key)
        url = self.base_url + rel_path

        req = HTTPRequest(
            method="GET",
            url=url,
            headers=as_str_dict(_headers),
            path_params=as_str_dict(_path),
            query_params=as_str_dict(_query),
            body=None,
        )

        resp = await self._client.execute(req)
        return self._handle_response(resp, "get_file_versions")

    async def get_file_comments(
        self, file_key: str, headers: Optional[Dict[str, Any]] = None
    ) -> FigmaResponse[Dict[str, Any]]:
        """Get comments from a Figma file.

        Args:
            file_key: The key of the Figma file
            headers: Additional request headers

        Returns:
            FigmaResponse containing comments data or error details
        """
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {"file_key": file_key}

        rel_path = "/v1/files/{file_key}/comments".format(file_key=file_key)
        url = self.base_url + rel_path

        req = HTTPRequest(
            method="GET",
            url=url,
            headers=as_str_dict(_headers),
            path_params=as_str_dict(_path),
            query_params={},
            body=None,
        )

        resp = await self._client.execute(req)
        return self._handle_response(resp, "get_file_comments")

    async def get_me(self, headers: Optional[Dict[str, Any]] = None) -> FigmaResponse[Dict[str, Any]]:
        """Get the current user's information.

        Args:
            headers: Additional request headers

        Returns:
            FigmaResponse containing user information or error details
        """
        _headers = dict(headers or {})
        _headers.setdefault("X-Figma-Token", self._client.token)

        req = HTTPRequest(
            method="GET",
            url=f"{self.base_url}/v1/me",
            headers=_headers,
        )

        resp = await self._client.execute(req)
        return self._handle_response(resp, "get_me")

    async def get_comment_reactions(
        self, comment_id: str, headers: Optional[Dict[str, Any]] = None
    ) -> FigmaResponse[Dict[str, Any]]:
        """Get reactions for a specific comment.

        Args:
            comment_id: The ID of the comment
            headers: Additional request headers

        Returns:
            FigmaResponse containing reactions data or error details
        """
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {"comment_id": comment_id}

        rel_path = "/v1/comments/{comment_id}/reactions".format(comment_id=comment_id)
        url = self.base_url + rel_path

        req = HTTPRequest(
            method="GET",
            url=url,
            headers=as_str_dict(_headers),
            path_params=as_str_dict(_path),
            query_params={},
            body=None,
        )

        resp = await self._client.execute(req)
        return self._handle_response(resp, "get_comment_reactions")

    async def get_comment_pins(
        self, file_key: str, headers: Optional[Dict[str, Any]] = None
    ) -> FigmaResponse[Dict[str, Any]]:
        """Get all pinned comments in a file.

        Args:
            file_key: The key of the Figma file
            headers: Additional request headers

        Returns:
            FigmaResponse containing pinned comments data or error details
        """
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {"file_key": file_key}

        rel_path = "/v1/files/{file_key}/comments/pinned".format(file_key=file_key)
        url = self.base_url + rel_path

        req = HTTPRequest(
            method="GET",
            url=url,
            headers=as_str_dict(_headers),
            path_params=as_str_dict(_path),
            query_params={},
            body=None,
        )

        resp = await self._client.execute(req)
        return self._handle_response(resp, "get_comment_pins")

    async def get_webhooks(
        self, team_id: str, headers: Optional[Dict[str, Any]] = None
    ) -> FigmaResponse[Dict[str, Any]]:
        """Get all webhooks for a team.

        Args:
            team_id: The ID of the team
            headers: Additional request headers

        Returns:
            FigmaResponse containing webhooks data or error details
        """
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {"team_id": team_id}

        rel_path = "/v1/teams/{team_id}/webhooks".format(team_id=team_id)
        url = self.base_url + rel_path

        req = HTTPRequest(
            method="GET",
            url=url,
            headers=as_str_dict(_headers),
            path_params=as_str_dict(_path),
            query_params={},
            body=None,
        )

        resp = await self._client.execute(req)
        return self._handle_response(resp, "get_webhooks")

    async def get_webhook_by_id(
        self, webhook_id: str, headers: Optional[Dict[str, Any]] = None
    ) -> FigmaResponse[Dict[str, Any]]:
        """Get a specific webhook by ID.

        Args:
            webhook_id: The ID of the webhook
            headers: Additional request headers

        Returns:
            FigmaResponse containing webhook data or error details
        """
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {"webhook_id": webhook_id}

        rel_path = "/v1/webhooks/{webhook_id}".format(webhook_id=webhook_id)
        url = self.base_url + rel_path

        req = HTTPRequest(
            method="GET",
            url=url,
            headers=as_str_dict(_headers),
            path_params=as_str_dict(_path),
            query_params={},
            body=None,
        )

        resp = await self._client.execute(req)
        return self._handle_response(resp, "get_webhook_by_id")

    async def get_webhook_deliveries(
        self,
        webhook_id: str,
        cursor: Optional[str] = None,
        count: Optional[int] = 10,
        headers: Optional[Dict[str, Any]] = None,
    ) -> FigmaResponse[Dict[str, Any]]:
        """Get recent deliveries for a webhook.

        Args:
            webhook_id: The ID of the webhook
            cursor: Pagination cursor
            count: Number of deliveries to return (1-100)
            headers: Additional request headers

        Returns:
            FigmaResponse containing webhook deliveries and pagination info or error details
        """
        _headers: Dict[str, Any] = dict(headers or {})
        _path: Dict[str, Any] = {"webhook_id": webhook_id}
        _query: Dict[str, Any] = {"count": min(100, max(1, count))}

        if cursor is not None:
            _query["cursor"] = cursor

        rel_path = "/v1/webhooks/{webhook_id}/deliveries".format(webhook_id=webhook_id)
        url = self.base_url + rel_path

        req = HTTPRequest(
            method="GET",
            url=url,
            headers=as_str_dict(_headers),
            path_params=as_str_dict(_path),
            query_params=as_str_dict(_query),
            body=None,
        )

        resp = await self._client.execute(req)
        return self._handle_response(resp, "get_webhook_deliveries")

    # ==================== Logs API ====================

    class ListLogsKwargs(TypedDict, total=False):
        """Type definition for additional list_logs request parameters."""
        headers: Optional[Dict[str, str]]
        timeout: Optional[float]
        params: Optional[Dict[str, Any]]

    async def list_logs(
        self, body: Dict[str, Any], **kwargs: Unpack[ListLogsKwargs]
    ) -> FigmaResponse[Dict[str, Any]]:
        """List logs with search query.

        Args:
            body: Log search request body containing query parameters
            **kwargs: Additional arguments to pass to the request

        Returns:
            FigmaResponse containing the logs data or error information
        """
        self.logger.info("Calling Figma API: list_logs")

        try:
            _headers = kwargs.pop("headers", {})
            _query: Dict[str, Any] = {}

            if "filter" in body:
                _query["filter"] = body["filter"]
            if "page" in body:
                _query["page"] = body["page"]
            if "per_page" in body:
                _query["per_page"] = min(max(1, body["per_page"]), 100)

            url = f"{self.base_url}/v1/logs"

            req = HTTPRequest(
                method="GET",
                url=url,
                headers=as_str_dict(_headers),
                query_params=as_str_dict(_query),
                body=None,
            )

            response = await self._client.execute(req)
            return self._handle_response(response, "list_logs")

        except Exception as e:
            self.logger.error(f"Figma API error in list_logs: {e}", exc_info=True)
            return FigmaResponse(success=False, error=str(e))

    class RequestKwargs(TypedDict, total=False):
        """Type definition for additional request parameters."""
        headers: Optional[Dict[str, str]]
        timeout: Optional[float]
        params: Optional[Dict[str, Any]]

    async def aggregate_logs(
        self, body: Dict[str, Any], **kwargs: Unpack[RequestKwargs]
    ) -> FigmaResponse[Dict[str, Any]]:
        """Aggregate logs based on the provided query.

        Args:
            body: Aggregation request body containing query parameters
            **kwargs: Additional arguments to pass to the request

        Returns:
            FigmaResponse containing the aggregated logs data or error information
        """
        self.logger.info("Calling Figma API: aggregate_logs")

        try:
            _headers = kwargs.pop("headers", {})
            _headers["Content-Type"] = "application/json"

            url = f"{self.base_url}/v1/logs/aggregate"

            req = HTTPRequest(
                method="POST",
                url=url,
                headers=as_str_dict(_headers),
                body=body,
            )

            response = await self._client.execute(req)
            return self._handle_response(response, "aggregate_logs")

        except Exception as e:
            self.logger.error(f"Figma API error in aggregate_logs: {e}", exc_info=True)
            return FigmaResponse(success=False, error=str(e))
