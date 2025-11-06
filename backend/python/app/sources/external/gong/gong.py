"""Gong Data Source

This module provides the GongDataSource class for interacting with Gong API endpoints.
"""

from typing import Any

from app.sources.client.gong.gong import GongClient
from app.sources.client.http.http_request import HTTPRequest
from app.sources.client.http.http_response import HTTPResponse


def _safe_format_url(url: str, path_params: dict[str, Any]) -> str:
    """Safely format URL with path parameters"""
    try:
        return url.format(**path_params)
    except KeyError as e:
        raise ValueError(f"Missing path parameter: {e}")


def _as_str_dict(data: dict[str, Any]) -> dict[str, str]:
    """Convert dictionary values to strings"""
    return {k: str(v) for k, v in data.items()}


class GongDataSource:
    def __init__(self, client: GongClient) -> None:
        """Default init for the connector-specific data source."""
        self._client = client.get_client()
        if self._client is None:
            raise ValueError("HTTP client is not initialized")
        try:
            self.base_url = self._client.get_base_url().rstrip("/")  # type: ignore [valid method]
        except AttributeError as exc:
            raise ValueError("HTTP client does not have get_base_url method") from exc

    def get_data_source(self) -> "GongDataSource":
        return self

    async def get_users(
        self,
        cursor: str | None = None,
        limit: int | None = None,
        headers: dict[str, Any] | None = None,
    ) -> HTTPResponse:
        """Get list of users from Gong API

        HTTP GET /v2/users
        Query params:
          - cursor (str, optional): Pagination cursor
          - limit (int, optional): Number of results per page (max 100)
        """
        if self._client is None:
            raise ValueError("HTTP client is not initialized")
        _headers: dict[str, Any] = dict(headers or {})
        _path: dict[str, Any] = {}
        _query: dict[str, Any] = {}
        if cursor is not None:
            _query["cursor"] = cursor
        if limit is not None:
            _query["limit"] = min(limit, 100)
        _body = None
        rel_path = "/users"
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method="GET",
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_calls(
        self,
        from_date_time: str | None = None,
        to_date_time: str | None = None,
        cursor: str | None = None,
        limit: int | None = None,
        workspace_id: str | None = None,
        headers: dict[str, Any] | None = None,
    ) -> HTTPResponse:
        """Get list of calls from Gong API

        HTTP GET /v2/calls
        Query params:
          - fromDateTime (str, optional): Start date (ISO format: YYYY-MM-DDTHH:MM:SS.sssZ)
          - toDateTime (str, optional): End date (ISO format: YYYY-MM-DDTHH:MM:SS.sssZ)
          - cursor (str, optional): Pagination cursor
          - limit (int, optional): Number of results per page (max 100)
          - workspaceId (str, optional): Specific workspace ID
        """
        if self._client is None:
            raise ValueError("HTTP client is not initialized")
        _headers: dict[str, Any] = dict(headers or {})
        _path: dict[str, Any] = {}
        _query: dict[str, Any] = {}
        if from_date_time is not None:
            _query["fromDateTime"] = from_date_time
        if to_date_time is not None:
            _query["toDateTime"] = to_date_time
        if cursor is not None:
            _query["cursor"] = cursor
        if limit is not None:
            _query["limit"] = min(limit, 100)
        if workspace_id is not None:
            _query["workspaceId"] = workspace_id
        _body = None
        rel_path = "/calls"
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method="GET",
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_call_details(
        self,
        call_id: str,
        headers: dict[str, Any] | None = None,
    ) -> HTTPResponse:
        """Get detailed information about a specific call

        HTTP GET /v2/calls/{call_id}
        Path params:
          - call_id (str): Unique call identifier
        """
        if self._client is None:
            raise ValueError("HTTP client is not initialized")
        _headers: dict[str, Any] = dict(headers or {})
        _path: dict[str, Any] = {
            "call_id": call_id,
        }
        _query: dict[str, Any] = {}
        _body = None
        rel_path = "/calls/{call_id}"
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method="GET",
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_call_transcript(
        self,
        call_id: str,
        headers: dict[str, Any] | None = None,
    ) -> HTTPResponse:
        """Get transcript for a specific call

        HTTP GET /v2/calls/{call_id}/transcript
        Path params:
          - call_id (str): Unique call identifier
        """
        if self._client is None:
            raise ValueError("HTTP client is not initialized")
        _headers: dict[str, Any] = dict(headers or {})
        _path: dict[str, Any] = {
            "call_id": call_id,
        }
        _query: dict[str, Any] = {}
        _body = None
        rel_path = "/calls/{call_id}/transcript"
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method="GET",
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_workspaces(
        self,
        headers: dict[str, Any] | None = None,
    ) -> HTTPResponse:
        """Get list of workspaces

        HTTP GET /v2/workspaces
        """
        if self._client is None:
            raise ValueError("HTTP client is not initialized")
        _headers: dict[str, Any] = dict(headers or {})
        _path: dict[str, Any] = {}
        _query: dict[str, Any] = {}
        _body = None
        rel_path = "/workspaces"
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method="GET",
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_deals(
        self,
        cursor: str | None = None,
        limit: int | None = None,
        workspace_id: str | None = None,
        headers: dict[str, Any] | None = None,
    ) -> HTTPResponse:
        """Get list of deals

        HTTP GET /v2/deals
        Query params:
          - cursor (str, optional): Pagination cursor
          - limit (int, optional): Number of results per page (max 100)
          - workspaceId (str, optional): Specific workspace ID
        """
        if self._client is None:
            raise ValueError("HTTP client is not initialized")
        _headers: dict[str, Any] = dict(headers or {})
        _path: dict[str, Any] = {}
        _query: dict[str, Any] = {}
        if cursor is not None:
            _query["cursor"] = cursor
        if limit is not None:
            _query["limit"] = min(limit, 100)
        if workspace_id is not None:
            _query["workspaceId"] = workspace_id
        _body = None
        rel_path = "/deals"
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method="GET",
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_meetings(
        self,
        from_date_time: str | None = None,
        to_date_time: str | None = None,
        cursor: str | None = None,
        limit: int | None = None,
        workspace_id: str | None = None,
        headers: dict[str, Any] | None = None,
    ) -> HTTPResponse:
        """Get list of meetings

        HTTP GET /v2/meetings
        Query params:
          - fromDateTime (str, optional): Start date (ISO format: YYYY-MM-DDTHH:MM:SS.sssZ)
          - toDateTime (str, optional): End date (ISO format: YYYY-MM-DDTHH:MM:SS.sssZ)
          - cursor (str, optional): Pagination cursor
          - limit (int, optional): Number of results per page (max 100)
          - workspaceId (str, optional): Specific workspace ID
        """
        if self._client is None:
            raise ValueError("HTTP client is not initialized")
        _headers: dict[str, Any] = dict(headers or {})
        _path: dict[str, Any] = {}
        _query: dict[str, Any] = {}
        if from_date_time is not None:
            _query["fromDateTime"] = from_date_time
        if to_date_time is not None:
            _query["toDateTime"] = to_date_time
        if cursor is not None:
            _query["cursor"] = cursor
        if limit is not None:
            _query["limit"] = min(limit, 100)
        if workspace_id is not None:
            _query["workspaceId"] = workspace_id
        _body = None
        rel_path = "/meetings"
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method="GET",
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_crm_objects(
        self,
        cursor: str | None = None,
        limit: int | None = None,
        headers: dict[str, Any] | None = None,
    ) -> HTTPResponse:
        """Get list of CRM objects

        HTTP GET /v2/crm/objects
        Query params:
          - cursor (str, optional): Pagination cursor
          - limit (int, optional): Number of results per page (max 100)
        """
        if self._client is None:
            raise ValueError("HTTP client is not initialized")
        _headers: dict[str, Any] = dict(headers or {})
        _path: dict[str, Any] = {}
        _query: dict[str, Any] = {}
        if cursor is not None:
            _query["cursor"] = cursor
        if limit is not None:
            _query["limit"] = min(limit, 100)
        _body = None
        rel_path = "/crm/objects"
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method="GET",
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_stats_activity(
        self,
        from_date_time: str | None = None,
        to_date_time: str | None = None,
        cursor: str | None = None,
        limit: int | None = None,
        headers: dict[str, Any] | None = None,
    ) -> HTTPResponse:
        """Get activity statistics

        HTTP GET /v2/stats/activity
        Query params:
          - fromDateTime (str, optional): Start date (ISO format: YYYY-MM-DDTHH:MM:SS.sssZ)
          - toDateTime (str, optional): End date (ISO format: YYYY-MM-DDTHH:MM:SS.sssZ)
          - cursor (str, optional): Pagination cursor
          - limit (int, optional): Number of results per page (max 100)
        """
        if self._client is None:
            raise ValueError("HTTP client is not initialized")
        _headers: dict[str, Any] = dict(headers or {})
        _path: dict[str, Any] = {}
        _query: dict[str, Any] = {}
        if from_date_time is not None:
            _query["fromDateTime"] = from_date_time
        if to_date_time is not None:
            _query["toDateTime"] = to_date_time
        if cursor is not None:
            _query["cursor"] = cursor
        if limit is not None:
            _query["limit"] = min(limit, 100)
        _body = None
        rel_path = "/stats/activity"
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method="GET",
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp

    async def get_library_calls(
        self,
        cursor: str | None = None,
        limit: int | None = None,
        workspace_id: str | None = None,
        headers: dict[str, Any] | None = None,
    ) -> HTTPResponse:
        """Get library calls

        HTTP GET /v2/library/calls
        Query params:
          - cursor (str, optional): Pagination cursor
          - limit (int, optional): Number of results per page (max 100)
          - workspaceId (str, optional): Specific workspace ID
        """
        if self._client is None:
            raise ValueError("HTTP client is not initialized")
        _headers: dict[str, Any] = dict(headers or {})
        _path: dict[str, Any] = {}
        _query: dict[str, Any] = {}
        if cursor is not None:
            _query["cursor"] = cursor
        if limit is not None:
            _query["limit"] = min(limit, 100)
        if workspace_id is not None:
            _query["workspaceId"] = workspace_id
        _body = None
        rel_path = "/library/calls"
        url = self.base_url + _safe_format_url(rel_path, _path)
        req = HTTPRequest(
            method="GET",
            url=url,
            headers=_as_str_dict(_headers),
            path_params=_as_str_dict(_path),
            query_params=_as_str_dict(_query),
            body=_body,
        )
        resp = await self._client.execute(req)
        return resp
